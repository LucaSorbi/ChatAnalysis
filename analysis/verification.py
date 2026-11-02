"""
analysis/verification.py
------------------------
Funzioni di DOMINIO PURE (nessuna dipendenza da Streamlit, rete o modello) per la verifica di consistenza semantica
indipendente tra una Topic Discovery e una Topic Detection:

- identità STABILE del topic scoperto (non la label testuale);
- controllo dell'identità dello SNAPSHOT di documento realmente analizzato (fingerprint di contenuto);
- origine di esecuzione della Detection (calcolata ora vs ripristinata da checkpoint).

Il CONFRONTO semantico resta esclusivamente in `analysis.consistency.ConsistencyService`: qui non si confronta nulla.
Questo modulo è l'unica fonte di verità per i controlli usati sia dal service (fail-closed prima di ogni chiamata al
modello) sia dalla UI (pulsante disabilitato con motivo), così le due viste non possono divergere.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Mapping

from ai.models import ConversationEvidenceDocument, DiscoveredTopic
from analysis.checkpoint import compute_document_fingerprint
from analysis.models import TopicProvenanceError

# Versione del contratto dell'export di verifica (artefatto autonomo). Incrementare ad ogni cambio di semantica.
VERIFICATION_EXPORT_CONTRACT_VERSION = "semantic_verification_export_v1"

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class SnapshotStatus(str, Enum):
    """Esito del confronto fra lo snapshot analizzato dalla Discovery e quello attivo per la verifica."""

    MATCH = "MATCH"                  # stessa fingerprint di contenuto: stesso testo, evidence ID, sezioni STT/OCR
    MISMATCH = "MISMATCH"            # contenuto diverso a parità di document_id: verifica NON eseguibile
    UNVERIFIABLE = "UNVERIFIABLE"    # la run sorgente non registra la fingerprint: equivalenza non dimostrabile


class ExecutionOrigin(str, Enum):
    """Origine dell'esecuzione della Detection: distingue l'indipendenza dalla Discovery dalla novità dell'inferenza."""

    COMPUTED_THIS_RUN = "COMPUTED_THIS_RUN"
    RESTORED_FROM_CHECKPOINT = "RESTORED_FROM_CHECKPOINT"
    MIXED = "MIXED"
    UNDETERMINED = "UNDETERMINED"


class DocumentSnapshotMismatchError(TopicProvenanceError):
    """Il documento attivo non ha lo stesso contenuto analizzato dalla Discovery: rieseguire la Discovery."""


@dataclass(frozen=True)
class SnapshotCheck:
    status: SnapshotStatus
    discovery_fingerprint: str | None
    active_fingerprint: str


def _valid_fp(value: Any) -> str | None:
    if isinstance(value, str) and _SHA256_HEX.match(value.strip()):
        return value.strip().lower()
    return None


def compute_discovered_topic_uid(discovery_run_id: str, topic: DiscoveredTopic) -> str:
    """
    Identità stabile di un topic scoperto: dipende dalla run e dal CONTENUTO validato (label, descrizione, evidence_id),
    mai dalla sola label né dalla posizione della card. Due topic omonimi con descrizione/evidenze diverse hanno UID
    diversi; due run diverse con lo stesso topic hanno UID diversi.
    """
    parts = [str(discovery_run_id).strip(), topic.label.strip(), topic.short_description.strip(), *map(str, topic.evidence_ids)]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"dtp_{digest}"


def check_document_snapshot(
    discovery_metadata: Mapping[str, Any] | None,
    document: ConversationEvidenceDocument,
) -> SnapshotCheck:
    """
    Confronta la fingerprint registrata dalla run di Discovery con quella del documento attivo.
    La fingerprint include document_id, ordine/timestamp/mittente dei messaggi, evidence_id e TESTO di tutte le sezioni
    (originali e derivate STT/OCR/Vision): un reimport o un arricchimento successivo cambia l'esito.
    """
    active = compute_document_fingerprint(document)
    recorded = _valid_fp((discovery_metadata or {}).get("document_fingerprint"))
    if recorded is None:
        return SnapshotCheck(SnapshotStatus.UNVERIFIABLE, None, active)
    if recorded != active:
        return SnapshotCheck(SnapshotStatus.MISMATCH, recorded, active)
    return SnapshotCheck(SnapshotStatus.MATCH, recorded, active)


def derive_execution_origin(detection_metadata: Mapping[str, Any] | None) -> ExecutionOrigin:
    """
    Deriva da contatori REGISTRATI (non da ipotesi) se la Detection è stata calcolata in questa esecuzione o ripristinata
    da checkpoint. Non usa `http_requests_sent == 0` come prova di ripristino: i client non LM Studio non notificano le
    richieste.
    """
    meta = detection_metadata or {}
    diag = meta.get("detection_diagnostics")
    diag = diag if isinstance(diag, Mapping) else {}

    def _int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    parents_total = _int(meta.get("chunk_count"))
    if parents_total is None or parents_total <= 0:
        return ExecutionOrigin.UNDETERMINED
    parents_recovered = _int(meta.get("recovered_chunk_count")) or 0
    micro_recovered = _int(diag.get("microchunks_recovered_from_checkpoint")) or 0
    micro_computed = _int(diag.get("microchunks_computed_this_run"))

    if parents_recovered >= parents_total:
        return ExecutionOrigin.RESTORED_FROM_CHECKPOINT
    if parents_recovered > 0:
        return ExecutionOrigin.MIXED
    if micro_recovered > 0:
        if micro_computed is not None and micro_computed > 0:
            return ExecutionOrigin.MIXED
        return ExecutionOrigin.RESTORED_FROM_CHECKPOINT
    return ExecutionOrigin.COMPUTED_THIS_RUN
