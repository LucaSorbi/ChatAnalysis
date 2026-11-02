"""
ui/verification.py
------------------
Logica APPLICATIVA PURA (nessuna dipendenza da Streamlit) della card «Verifica con Topic Detection» della scheda Discovery.

La vista (`ui/presentation.py`) si limita a mostrare ciò che questo modulo decide:
- precondizioni (pulsante abilitato/disabilitato con motivo) calcolate con le STESSE funzioni usate dal service;
- selezione del risultato di verifica per identità (run Discovery + UID del topic), mai per label;
- testi d'esito non ambigui;
- esportazione del rapporto SAFE tramite `analysis.export` (nessun dizionario assemblato nella vista).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping, Sequence

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from analysis.export import (
    SemanticVerificationExportError,
    build_semantic_verification_export_report,
    generate_export_filename,
    serialize_report_to_json,
)
from analysis.models import ConsistencyResult, ConsistencyStatus
from analysis.verification import (
    ExecutionOrigin,
    SnapshotStatus,
    check_document_snapshot,
    compute_discovered_topic_uid,
)

# Codici di blocco (chiusi) -> messaggio comprensibile per l'utente.
BLOCK_MESSAGES: dict[str, str] = {
    "NO_DOCUMENT": "Nessun documento di conversazione disponibile.",
    "NO_EVIDENCE": "Il documento non contiene evidenze analizzabili: impossibile avviare la Topic Detection.",
    "DOCUMENT_MISMATCH": "Il topic appartiene a un altro documento: verifica bloccata per discrepanza di provenienza.",
    "DISCOVERY_RUN_UNAVAILABLE": (
        "La run di Discovery di questo topic non è più disponibile in sessione: rieseguire la Topic Discovery."
    ),
    "SNAPSHOT_MISMATCH": (
        "Il documento attuale ha un contenuto diverso da quello analizzato da questa Discovery "
        "(reimport o arricchimento STT/OCR successivo): rieseguire la Topic Discovery prima di verificare."
    ),
}

EXPORT_ERROR_MESSAGES: dict[str, str] = {
    "DETECTION_RESULT_MISSING": "Risultato della Topic Detection non più disponibile in sessione: ripetere la verifica.",
    "DISCOVERY_RESULT_MISSING": "Risultato della Topic Discovery non più disponibile in sessione: ripetere la Discovery.",
    "RUN_ID_MISSING": "Identificativi di run mancanti: il rapporto non è dimostrabile.",
    "DOCUMENT_ID_MISMATCH": "I risultati appartengono a documenti diversi: esportazione bloccata.",
    "RUN_LINK_MISMATCH": "Il collegamento fra Discovery e Detection non è verificabile: esportazione bloccata.",
    "NOT_A_VERIFICATION_RUN": "La Detection associata non è una verifica da Discovery: esportazione bloccata.",
    "TOPIC_NOT_IN_DISCOVERY_RUN": "Il topic non risulta nella run di Discovery indicata: esportazione bloccata.",
    "QUERY_NOT_FROM_DISCOVERY_TOPIC": "La query della Detection non coincide con il topic di Discovery: esportazione bloccata.",
    "QUERY_IS_LABEL_ONLY": "La Detection è stata eseguita a sola label: non è una verifica associata.",
    "COMPARATOR_RECHECK_FAILED": "Il confronto non è ricalcolabile: esportazione bloccata.",
    "CONSISTENCY_RESULT_STALE": "L'esito registrato non coincide con il ricalcolo: esportazione bloccata.",
    "PRIVACY_CHECK_FAILED": "Controllo privacy fallito: esportazione SAFE bloccata.",
}


def describe_export_error(code: str) -> str:
    key = code.split(":", 1)[0]
    return EXPORT_ERROR_MESSAGES.get(key, f"Rapporto non esportabile ({code}).")


@dataclass(frozen=True)
class VerificationPreconditions:
    can_verify: bool
    block_code: str | None
    block_message: str | None
    warnings: tuple[str, ...]
    snapshot_status: str | None
    discovery_status: str | None


def find_discovery_run(
    run_id: str | None,
    document_id: str,
    candidate_runs: Sequence[TopicDiscoveryResult],
) -> TopicDiscoveryResult | None:
    """Run di Discovery per IDENTIFICATIVO (e documento): mai per posizione né per label."""
    if not run_id:
        return None
    for r in candidate_runs:
        meta = r.metadata or {}
        if r.provenance_document_id == document_id and str(meta.get("run_id", "")).strip() == str(run_id).strip():
            return r
    return None


def evaluate_verification_preconditions(
    document: ConversationEvidenceDocument | None,
    discovery_result: TopicDiscoveryResult | None,
    topic_provenance_document_id: str | None = None,
) -> VerificationPreconditions:
    """Precondizioni della verifica: stesse regole del service (snapshot, provenienza), valutate PRIMA del click."""

    def _blocked(code: str, snap: str | None = None, disc: str | None = None) -> VerificationPreconditions:
        return VerificationPreconditions(False, code, BLOCK_MESSAGES[code], (), snap, disc)

    if document is None:
        return _blocked("NO_DOCUMENT")
    if not document.all_evidence_sections:
        return _blocked("NO_EVIDENCE")
    if topic_provenance_document_id is not None and topic_provenance_document_id != document.document_id:
        return _blocked("DOCUMENT_MISMATCH")
    if discovery_result is None:
        return _blocked("DISCOVERY_RUN_UNAVAILABLE")

    meta = discovery_result.metadata
    disc_status = str(meta.get("status") or "NOT_RECORDED")
    snap = check_document_snapshot(meta, document)
    if snap.status == SnapshotStatus.MISMATCH:
        return _blocked("SNAPSHOT_MISMATCH", snap.status.value, disc_status)

    warnings: list[str] = []
    if disc_status != "COMPLETE":
        total = meta.get("total_evidence_count")
        analyzed = meta.get("analyzed_evidence_count")
        cov = f" (evidenze analizzate: {analyzed}/{total})" if analyzed is not None and total is not None else ""
        warnings.append(
            f"Discovery sorgente {disc_status}{cov}: il topic proviene da una copertura parziale; la verifica non la estende."
        )
    if snap.status == SnapshotStatus.UNVERIFIABLE:
        warnings.append(
            "L'equivalenza del documento con quello analizzato dalla Discovery non è dimostrabile "
            "(fingerprint non registrata dalla run sorgente)."
        )
    return VerificationPreconditions(True, None, None, tuple(warnings), snap.status.value, disc_status)


def select_verification_results(
    consistency_results: Sequence[ConsistencyResult],
    document_id: str,
    discovery_run_id: str | None,
    topic: DiscoveredTopic,
) -> tuple[ConsistencyResult, ...]:
    """
    Risultati di verifica di QUESTO topic in QUESTA run Discovery, in ordine cronologico.
    L'associazione usa document_id + discovery_run_id + UID del topic (contenuto validato): nessuna corrispondenza per label.
    """
    if not discovery_run_id:
        return ()
    uid = compute_discovered_topic_uid(discovery_run_id, topic)
    return tuple(
        c for c in consistency_results
        if c.document_id == document_id
        and c.discovery_run_id == discovery_run_id
        and c.metadata.get("discovery_topic_uid") == uid
    )


@dataclass(frozen=True)
class VerificationOutcomeView:
    badge: str
    headline: str
    notes: tuple[str, ...]
    execution_origin_label: str


_BADGES = {
    ConsistencyStatus.CONSISTENT: "✅ COERENTE",
    ConsistencyStatus.PARTIALLY_CONSISTENT: "🔵 PARZIALMENTE COERENTE",
    ConsistencyStatus.DISCREPANCY_ABSENT: "⚠️ DISCREPANZA (ABSENT)",
    ConsistencyStatus.DISCREPANCY_UNCERTAIN: "🟡 INCERTO (UNCERTAIN)",
}

_EXECUTION_LABELS = {
    ExecutionOrigin.COMPUTED_THIS_RUN.value: "Detection calcolata in questa esecuzione.",
    ExecutionOrigin.RESTORED_FROM_CHECKPOINT.value: (
        "Risultato Detection RECUPERATO da checkpoint (nessuna nuova richiesta al modello)."
    ),
    ExecutionOrigin.MIXED.value: "Detection ottenuta in parte da checkpoint e in parte calcolata ora.",
    ExecutionOrigin.UNDETERMINED.value: "Origine dell'esecuzione della Detection non determinabile.",
}


def describe_verification_outcome(result: ConsistencyResult) -> VerificationOutcomeView:
    """Testi d'esito. ABSENT è una DISCREPANZA fra due inferenze indipendenti, non un errore né una prova di assenza."""
    meta = result.metadata
    notes: list[str] = []
    if result.status == ConsistencyStatus.DISCREPANCY_ABSENT:
        headline = (
            "La Discovery ha identificato questo topic; la Detection indipendente (eseguita con etichetta e descrizione, "
            "senza le evidenze della Discovery) non lo ha rilevato. È una discrepanza fra due inferenze, non un errore "
            "di esecuzione né una prova che l'argomento sia assente."
        )
    elif result.status == ConsistencyStatus.DISCREPANCY_UNCERTAIN:
        headline = (
            "La Discovery ha identificato questo topic; la Detection indipendente è rimasta INCERTA. Giudizio limitato."
        )
    elif result.status == ConsistencyStatus.PARTIALLY_CONSISTENT:
        headline = (
            "Entrambe le inferenze indicano il topic, ma citano evidenze diverse (nessuna evidenza in comune)."
        )
    else:
        headline = "Entrambe le inferenze indipendenti indicano il topic con almeno un'evidenza in comune."
    if meta.get("is_partial_detection"):
        notes.append("Detection PARZIALE: l'esito vale solo per le porzioni analizzate.")
    if str(meta.get("discovery_status", "COMPLETE")) != "COMPLETE":
        notes.append(f"Discovery sorgente {meta.get('discovery_status')}: copertura non totale.")
    if meta.get("snapshot_status") == SnapshotStatus.UNVERIFIABLE.value:
        notes.append("Equivalenza del documento con quello della Discovery non dimostrabile.")
    notes.append("La coerenza fra inferenze dello stesso modello non è prova di verità.")
    origin = str(meta.get("detection_execution_origin", ExecutionOrigin.UNDETERMINED.value))
    return VerificationOutcomeView(
        badge=_BADGES[result.status],
        headline=headline,
        notes=tuple(notes),
        execution_origin_label=_EXECUTION_LABELS.get(origin, _EXECUTION_LABELS[ExecutionOrigin.UNDETERMINED.value]),
    )


def _find_detection(runs: Sequence[TopicDetectionResult], run_id: str | None) -> TopicDetectionResult | None:
    for r in runs:
        if run_id and str(r.metadata.get("run_id", "")).strip() == str(run_id).strip():
            return r
    return None


def export_semantic_verification_json(
    consistency_result: ConsistencyResult,
    document: ConversationEvidenceDocument,
    session_state: MutableMapping[str, Any] | None = None,
    redact_sensitive_topics: bool = True,
) -> tuple[str, str]:
    """
    Serializza il rapporto di verifica (SAFE_REDACTED di default). Risolve Detection e Discovery per run_id dallo
    stato di sessione e delega la costruzione a `analysis.export`. Non invoca mai il modello.
    Solleva SemanticVerificationExportError (codice chiuso) se il rapporto non è dimostrabile.
    """
    from ui import state

    doc_analysis = state.get_document_analysis_state(document.document_id, state=session_state)
    det = _find_detection(doc_analysis.detection_runs, consistency_result.detection_run_id)
    if det is None:
        raise SemanticVerificationExportError("DETECTION_RESULT_MISSING")
    disc = find_discovery_run(
        consistency_result.discovery_run_id, document.document_id,
        list(doc_analysis.discovery_runs) + list(state.get_discovery_results(state=session_state)),
    )
    if disc is None:
        raise SemanticVerificationExportError("DISCOVERY_RESULT_MISSING")
    try:
        dataset_mode = state.get_dataset_mode(state=session_state)
    except Exception:
        dataset_mode = None
    auth_ids = set(state.get_authoritative_live_run_ids(session_state))
    report = build_semantic_verification_export_report(
        consistency_result=consistency_result,
        detection_result=det,
        discovery_result=disc,
        document=document,
        dataset_mode=dataset_mode,
        authoritative_live_run_ids=auth_ids,
        redact_sensitive_topics=redact_sensitive_topics,
    )
    json_str = serialize_report_to_json(report)
    filename = generate_export_filename(
        analysis_type="SEMANTIC_VERIFICATION",
        dataset_id=document.chat_id or document.document_id,
        model_id=report["detection"]["model"],
        run_id=det.metadata.get("run_id"),
        timestamp=report["experiment"]["generated_at"],
        export_id=report["experiment"]["export_id"],
    )
    return json_str, filename
