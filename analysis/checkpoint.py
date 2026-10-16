"""
analysis/checkpoint.py
----------------------
Meccanismo sicuro di Checkpoint e Ripresa per analisi tematiche estese (Topic Detection e Topic Discovery).

Principi architetturali e forensi:
1. IDENTIFICAZIONE CRITTOGRAFICA UNILATERALE (Document Fingerprint):
   Ciascun checkpoint è associato in modo univoco al fingerprint SHA-256 del documento analizzato
   (hash dei testi originali delle evidenze, message_id ed evidence_id). Nessuna cache o checkpoint
   può mai essere condiviso o scambiato tra documenti differenti.
2. CONFIG PROVENANCE RIGOROSA (Run Config Fingerprint):
   Memorizza modello (model_id), versione del prompt (prompt_version), schema e parametri di chunking
   (context_length, caratteri, token, temperature, seed). Se la configurazione cambia, il checkpoint precedente
   non viene considerato valido e viene avviata una nuova elaborazione coerente.
3. SALVATAGGIO POST-VALIDAZIONE ED ELABORAZIONE INCREMENTALE:
   I risultati dei chunk intermedi vengono salvati nel checkpoint ESCLUSIVAMENTE dopo la validazione formale
   del payload JSON e delle evidenze citate. In caso di ripresa, i chunk già validati non vengono rieseguiti.
4. PROTEZIONE LOCALE DEI DATI INVESTIGATIVI:
   I checkpoint risiedono esclusivamente nel filesystem protetto locale (nessuna trasmissione esterna).
   La scrittura avviene in modo atomico (file temporaneo + replace) per scongiurare corruzioni di stato
   in caso di terminazione improvvisa del processo.
5. ANNULLAMENTO E RIPRESA CONTROLLATI:
   In caso di interruzione (manuale o crash), lo stato salvato garantisce la ripresa trasparente dall'ultimo
   chunk completato, riducendo a zero il tempo di calcolo sprecato per conversazioni da centinaia di megabyte.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.models import OperationalAnalysisConfig, ChunkDiagnosticRecord


def compute_document_fingerprint(document: ConversationEvidenceDocument) -> str:
    """
    Calcola un digest crittografico SHA-256 del documento e delle sue evidenze forensi.
    Include esplicitamente timestamp e ordine dei messaggi, sender, identificativi e testi delle evidenze.
    Garantisce che qualsiasi alterazione di testo, timestamp, ordine o metadati modifichi il fingerprint.
    """
    hasher = hashlib.sha256()
    hasher.update(document.document_id.encode("utf-8"))
    hasher.update(str(document.chat_id or "").encode("utf-8"))
    hasher.update(str(document.source_name or "").encode("utf-8"))

    for idx, bundle in enumerate(document.bundles):
        hasher.update(f"idx:{idx}".encode("utf-8"))
        hasher.update(bundle.message.message_id.encode("utf-8"))
        hasher.update(str(bundle.message.timestamp or "").encode("utf-8"))
        hasher.update(str(bundle.message.sender or "").encode("utf-8"))
        for s_idx, sec in enumerate(bundle.text_evidence_sections):
            hasher.update(f"sec_idx:{s_idx}".encode("utf-8"))
            hasher.update(sec.evidence_id.encode("utf-8"))
            hasher.update(str(sec.source_record_id or "").encode("utf-8"))
            hasher.update(str(sec.language or "").encode("utf-8"))
            hasher.update(sec.text.encode("utf-8"))

    return hasher.hexdigest()


def compute_run_config_hash(
    model_id: str,
    prompt_version: str,
    config: OperationalAnalysisConfig | None = None,
    topic_query: TopicQuery | None = None,
    extra_params: Mapping[str, Any] | None = None,
    chunks: Sequence[ConversationEvidenceDocument] | None = None,
) -> str:
    """
    Calcola l'hash univoco e rigoroso dei parametri operativi e del modello per la run.
    Dipende dai parametri realmente usati: modello, quantizzazione, context length,
    temperature, seed, max output tokens, max topics per discovery, strategia,
    prompt e versione, schema, limiti di chunking, profilo e identità dei chunk.
    Separa l'identificativo volatile della sessione LM Studio (loaded_instance_id)
    dall'identità stabile del modello, consentendo la ripresa legittima dopo il riavvio del server.
    """
    cfg = config or OperationalAnalysisConfig()
    prof_val = cfg.profile.value if hasattr(cfg.profile, "value") else str(cfg.profile)
    payload: dict[str, Any] = {
        "model_id": model_id,
        "prompt_version": prompt_version,
        "context_length": cfg.context_length,
        "chunk_character_budget": cfg.chunk_character_budget,
        "max_chunk_bundles": cfg.max_chunk_bundles,
        "max_chunk_tokens": cfg.max_chunk_tokens,
        "temperature": cfg.temperature,
        "seed": cfg.seed,
        "profile": prof_val,
        "max_tokens": cfg.max_tokens,
        "max_topics": cfg.max_topics,
    }
    if topic_query is not None:
        payload["topic_id"] = topic_query.topic_id
        payload["topic_label"] = topic_query.label
        payload["topic_description"] = topic_query.description
        payload["is_label_only"] = getattr(topic_query, "is_label_only", False)
    if chunks is not None:
        payload["chunk_count"] = len(chunks)
        payload["chunk_ids"] = [c.document_id for c in chunks]
    if extra_params:
        # Separa l'identificativo volatile della sessione LM Studio dall'identità stabile del modello
        clean_extra = {k: v for k, v in extra_params.items() if k != "loaded_instance_id"}
        payload["extra"] = clean_extra

    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_checkpoint_evidence_references(
    checkpoint: AnalysisCheckpoint,
    document: ConversationEvidenceDocument,
    chunks: Sequence[ConversationEvidenceDocument] | None = None,
    expected_topic_query: TopicQuery | None = None,
    expected_analysis_type: str | None = None,
) -> bool:
    """
    Rivalida rigorosamente dopo il caricamento la struttura, la provenienza
    e la presenza di tutte le evidenze citate nel documento corrente.
    
    Verifiche tassative:
    - Corrispondenza del document_id.
    - Corrispondenza crittografica del document_fingerprint.
    - Provenance documentale di ciascun risultato (provenance_document_id == document.document_id).
    - Tipo dell'analisi coerente con l'attesa (detection | discovery).
    - Indici dei chunk validi nell'intervallo [0, total_planned_chunks).
    - Numero totale dei chunk coerente con la partizione effettiva (se chunks fornito).
    - Coerenza degli evidence ID per singolo chunk (nessuna contaminazione cross-chunk).
    - Compatibilità del risultato con la query (topic_id e label).
    - Completezza strutturale del payload serializzato (assenza di campi obbligatori mancanti).
    
    Se un checkpoint cita evidence_id inesistenti, appartiene a un altro documento,
    oppure omette metadati obbligatori di provenienza, la validazione fallisce restituendo False.
    """
    if checkpoint.document_id != document.document_id:
        return False

    computed_doc_fp = compute_document_fingerprint(document)
    if checkpoint.document_fingerprint != computed_doc_fp:
        return False

    if expected_analysis_type is not None:
        if checkpoint.analysis_type != expected_analysis_type:
            return False
    elif checkpoint.analysis_type not in ("detection", "discovery"):
        return False

    if not isinstance(checkpoint.total_planned_chunks, int) or checkpoint.total_planned_chunks <= 0:
        return False

    if chunks is not None and checkpoint.total_planned_chunks != len(chunks):
        return False

    if not isinstance(checkpoint.serialized_results, (dict, Mapping)):
        return False

    valid_doc_eids = {sec.evidence_id for sec in document.all_evidence_sections}

    for s_idx, s_data in checkpoint.serialized_results.items():
        try:
            chunk_num = int(s_idx)
        except (ValueError, TypeError):
            return False

        if chunk_num < 0 or chunk_num >= checkpoint.total_planned_chunks:
            return False

        if chunks is not None:
            if chunk_num >= len(chunks):
                return False
            allowed_eids = {sec.evidence_id for sec in chunks[chunk_num].all_evidence_sections}
        else:
            allowed_eids = valid_doc_eids

        if not isinstance(s_data, dict):
            return False

        # Verifica tassativa della provenienza documentale del risultato per singolo chunk:
        # Il risultato deve provenire dal documento padre o dal corrispondente blocco pianificato.
        # Un risultato che dichiara un documento differente (es. 'FAKE_OTHER_DOCUMENT') DEVE essere rifiutato.
        provenance_doc_id = s_data.get("provenance_document_id")
        if not provenance_doc_id:
            return False

        expected_chunk_doc_id = (
            chunks[chunk_num].document_id
            if chunks is not None
            else f"{document.document_id}::chunk::{chunk_num}"
        )
        is_valid_provenance = (
            provenance_doc_id == document.document_id
            or provenance_doc_id == expected_chunk_doc_id
        )
        if not is_valid_provenance:
            return False

        if checkpoint.analysis_type == "detection":
            if "topic" not in s_data or not isinstance(s_data["topic"], dict):
                return False
            t_data = s_data["topic"]
            if not t_data.get("topic_id") or not t_data.get("label"):
                return False
            if expected_topic_query is not None:
                if (
                    t_data.get("topic_id") != expected_topic_query.topic_id
                    or t_data.get("label") != expected_topic_query.label
                ):
                    return False

            decision = s_data.get("decision")
            if decision not in ("PRESENT", "ABSENT", "UNCERTAIN"):
                return False

            if "evidence_ids" not in s_data or not isinstance(s_data["evidence_ids"], (list, tuple)):
                return False
            eids = s_data["evidence_ids"]
            for eid in eids:
                if eid not in allowed_eids:
                    return False

        elif checkpoint.analysis_type == "discovery":
            if "topics" not in s_data or not isinstance(s_data["topics"], (list, tuple)):
                return False
            topics = s_data["topics"]
            for t in topics:
                if not isinstance(t, dict):
                    return False
                if not t.get("label"):
                    return False
                if "evidence_ids" not in t or not isinstance(t["evidence_ids"], (list, tuple)):
                    return False
                for eid in t["evidence_ids"]:
                    if eid not in allowed_eids:
                        return False

    # Rivalidazione rigorosa dei sotto-blocchi persistiti
    for sub in checkpoint.subchunks:
        p_idx = sub.get("parent_chunk_index")
        if not isinstance(p_idx, int) or p_idx < 0 or p_idx >= checkpoint.total_planned_chunks:
            return False
        if chunks is not None:
            if p_idx >= len(chunks):
                return False
            allowed_sub_eids = {sec.evidence_id for sec in chunks[p_idx].all_evidence_sections}
        else:
            allowed_sub_eids = valid_doc_eids

        declared_sub_eids = set(sub.get("evidence_ids", []))
        if not declared_sub_eids.issubset(allowed_sub_eids):
            return False

        sub_res = sub.get("serialized_result", {})
        if not isinstance(sub_res, dict):
            return False
        sub_topics = sub_res.get("topics", [])
        if not isinstance(sub_topics, (list, tuple)):
            return False
        for t in sub_topics:
            if not isinstance(t, dict) or not t.get("label"):
                return False
            t_eids = t.get("evidence_ids", [])
            if not isinstance(t_eids, (list, tuple)):
                return False
            for eid in t_eids:
                if eid not in declared_sub_eids:
                    return False

    return True



@dataclass(frozen=True)
class AnalysisCheckpoint:
    """
    Rappresentazione immutabile dello stato di checkpoint salvato per una run.
    """
    document_id: str
    document_fingerprint: str
    config_fingerprint: str
    analysis_type: str  # "detection" | "discovery"
    model_id: str
    prompt_version: str
    total_planned_chunks: int
    completed_chunk_indices: tuple[int, ...]
    serialized_results: Mapping[str, Any]
    created_at: str
    updated_at: str
    subchunks: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_fingerprint": self.document_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "analysis_type": self.analysis_type,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "total_planned_chunks": self.total_planned_chunks,
            "completed_chunk_indices": list(self.completed_chunk_indices),
            "serialized_results": dict(self.serialized_results),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "subchunks": list(self.subchunks),
        }


def serialize_detection_result_for_checkpoint(result: TopicDetectionResult) -> dict[str, Any]:
    """Serializza deterministicamente un TopicDetectionResult validato."""
    return {
        "topic": {
            "topic_id": result.topic.topic_id,
            "label": result.topic.label,
            "description": result.topic.description,
            "is_label_only": getattr(result.topic, "is_label_only", False),
        },
        "decision": result.decision.value,
        "evidence_ids": list(result.evidence_ids),
        "rationale": result.rationale,
        "provenance_document_id": result.provenance_document_id,
        "metadata": dict(result.metadata),
    }


def deserialize_detection_result_from_checkpoint(data: dict[str, Any]) -> TopicDetectionResult:
    """Ricostruisce un TopicDetectionResult da checkpoint validando rigorosamente i campi obbligatori."""
    if not isinstance(data, dict):
        raise ValueError("Payload checkpoint non valido: atteso dizionario")
    if "topic" not in data or not isinstance(data["topic"], dict):
        raise ValueError("Campo obbligatorio 'topic' mancante o invalido nel checkpoint")
    if "decision" not in data or not data["decision"]:
        raise ValueError("Campo obbligatorio 'decision' mancante nel checkpoint")
    if "provenance_document_id" not in data or not data["provenance_document_id"]:
        raise ValueError("Campo obbligatorio 'provenance_document_id' mancante nel checkpoint")
    if "evidence_ids" not in data or not isinstance(data["evidence_ids"], (list, tuple)):
        raise ValueError("Campo obbligatorio 'evidence_ids' mancante o invalido nel checkpoint")

    t_data = data["topic"]
    if "topic_id" not in t_data or "label" not in t_data:
        raise ValueError("Campi 'topic_id' o 'label' mancanti nel topic deserializzato")

    topic = TopicQuery(
        topic_id=t_data["topic_id"],
        label=t_data["label"],
        description=t_data.get("description", ""),
        is_label_only=t_data.get("is_label_only", False),
    )
    return TopicDetectionResult(
        topic=topic,
        decision=TopicDecision(data["decision"]),
        evidence_ids=tuple(data["evidence_ids"]),
        rationale=data.get("rationale", ""),
        provenance_document_id=data["provenance_document_id"],
        metadata=dict(data.get("metadata", {})),
    )


def serialize_discovery_result_for_checkpoint(result: TopicDiscoveryResult) -> dict[str, Any]:
    """Serializza deterministicamente un TopicDiscoveryResult validato."""
    return {
        "topics": [
            {
                "label": t.label,
                "short_description": t.short_description,
                "evidence_ids": list(t.evidence_ids),
            }
            for t in result.topics
        ],
        "provenance_document_id": result.provenance_document_id,
        "metadata": dict(result.metadata),
    }


def deserialize_discovery_result_from_checkpoint(data: dict[str, Any]) -> TopicDiscoveryResult:
    """Ricostruisce un TopicDiscoveryResult da checkpoint validando rigorosamente i campi obbligatori."""
    if not isinstance(data, dict):
        raise ValueError("Payload checkpoint non valido: atteso dizionario")
    if "topics" not in data or not isinstance(data["topics"], (list, tuple)):
        raise ValueError("Campo obbligatorio 'topics' mancante o invalido nel checkpoint")
    if "provenance_document_id" not in data or not data["provenance_document_id"]:
        raise ValueError("Campo obbligatorio 'provenance_document_id' mancante nel checkpoint")

    topics = [
        DiscoveredTopic(
            label=t["label"],
            short_description=t.get("short_description", ""),
            evidence_ids=tuple(t.get("evidence_ids", ())),
        )
        for t in data["topics"]
    ]
    return TopicDiscoveryResult(
        topics=tuple(topics),
        provenance_document_id=data["provenance_document_id"],
        metadata=dict(data.get("metadata", {})),
    )


class CheckpointSaveError(Exception):
    """Sollevata quando il salvataggio atomico del checkpoint su disco fallisce."""
    pass


class AnalysisCheckpointStore:
    """
    Gestore transazionale, incrementale e thread-safe dei checkpoint forensi su SQLite locale.
    Salva atomicamente soltanto il nuovo blocco validato (O(1)), evitando riscritture O(N^2).
    Include permessi restrittivi sulla directory (0o700) e sul file database (0o600).
    Supporta migrazione trasparente dai legacy checkpoint JSON e cancellazione controllata per documento.
    """

    def __init__(
        self,
        checkpoint_dir: str | Path | None = None,
        db_name: str = "checkpoints.db",
    ) -> None:
        if checkpoint_dir is None:
            self.checkpoint_dir = Path(".checkpoints")
        else:
            self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.checkpoint_dir, 0o700)
        except Exception:
            pass

        self.db_path = self.checkpoint_dir / db_name
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_legacy_json_if_needed()

    def _init_db(self) -> None:
        """Inizializza le tabelle SQLite con modalità WAL e permessi restrittivi."""
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                conn.execute("PRAGMA busy_timeout=5000;")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        document_fingerprint TEXT NOT NULL,
                        config_fingerprint TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        analysis_type TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        prompt_version TEXT NOT NULL,
                        total_planned_chunks INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (document_fingerprint, config_fingerprint)
                    );
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoint_chunks (
                        document_fingerprint TEXT NOT NULL,
                        config_fingerprint TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        serialized_result TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index),
                        FOREIGN KEY (document_fingerprint, config_fingerprint)
                            REFERENCES checkpoints (document_fingerprint, config_fingerprint)
                            ON DELETE CASCADE
                    );
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_doc
                    ON checkpoint_chunks (document_fingerprint);
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_checkpoints_doc_id
                    ON checkpoints (document_id);
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoint_chunk_diagnostics (
                        document_fingerprint TEXT NOT NULL,
                        config_fingerprint TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        chunk_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 1,
                        duration_seconds REAL NOT NULL DEFAULT 0.0,
                        model_id TEXT NOT NULL DEFAULT '',
                        http_status INTEGER,
                        finish_reason TEXT,
                        prompt_tokens INTEGER,
                        completion_tokens INTEGER,
                        error_type TEXT,
                        diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                        failure_phase TEXT,
                        error_detail TEXT,
                        json_valid INTEGER NOT NULL DEFAULT 0,
                        schema_valid INTEGER NOT NULL DEFAULT 0,
                        evidence_valid INTEGER NOT NULL DEFAULT 0,
                        checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
                    );
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_diagnostics_doc_cfg
                    ON checkpoint_chunk_diagnostics (document_fingerprint, config_fingerprint);
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoint_subchunks (
                        document_fingerprint TEXT NOT NULL,
                        config_fingerprint TEXT NOT NULL,
                        parent_chunk_index INTEGER NOT NULL,
                        subchunk_index INTEGER NOT NULL,
                        subchunk_id TEXT NOT NULL,
                        total_subchunks INTEGER NOT NULL,
                        serialized_result TEXT NOT NULL,
                        evidence_ids TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (document_fingerprint, config_fingerprint, parent_chunk_index, subchunk_index),
                        FOREIGN KEY (document_fingerprint, config_fingerprint)
                            REFERENCES checkpoints (document_fingerprint, config_fingerprint)
                            ON DELETE CASCADE
                    );
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_subchunks_parent
                    ON checkpoint_subchunks (document_fingerprint, config_fingerprint, parent_chunk_index);
                """)

            if self.db_path.exists():
                try:
                    os.chmod(self.db_path, 0o600)
                except Exception:
                    pass
        except Exception:
            pass

    @contextmanager
    def _connect(self, timeout: float = 5.0):
        """Restituisce una nuova connessione SQLite con gestione deterministica di commit, rollback e chiusura."""
        conn = sqlite3.connect(str(self.db_path), timeout=timeout, check_same_thread=False)
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate_legacy_json_if_needed(self) -> None:
        """
        Migra trasparentemente eventuali file checkpoint JSON preesistenti nel database SQLite.
        
        Protocollo di sicurezza e riconciliazione rinforzato (Hardening):
        1. Lettura protetta del file checkpoint legacy;
        2. Validazione rigorosa della struttura e dei campi obbligatori;
        3. Ispezione comparativa completa con lo stato consolidato in SQLite:
           - Incompatibilità metadati: se SQLite possiede già una testata con document_id,
             analysis_type, model_id, prompt_version o total_planned_chunks differenti,
             la testata SQLite NON viene modificata, total_planned_chunks NON viene alterato
             con MAX, nessun blocco viene importato e il file legacy viene PRESERVATO;
           - Validazione indici e provenienza: gli indici dei blocchi devono essere interi
             compresi nell'intervallo [0, total_planned_chunks) e dichiarare provenienza coerente;
           - Tolleranza a SQLite corrotto: se un blocco in SQLite è corrotto (non parsabile come JSON),
             viene sollevato un conflitto esplicito, SQLite non viene sovrascritto arbitrariamente
             e il file legacy valido viene tassativamente PRESERVATO su disco;
           - Precedenza SQLite consolidato: in caso di esiti discordanti (es. PRESENT vs ABSENT),
             il dato SQLite consolidato prevale e il file legacy viene preservato;
           - Integrazione sicura: se SQLite è privo di conflitti ed incompleto, i soli blocchi
             mancanti vengono integrati.
        4. Transazione isolata SQLite protetta (ON CONFLICT DO NOTHING);
        5. Verifica effettiva basata sulla parsabilità e presenza di ciascun blocco (non solo COUNT);
        6. Rimozione sicura del file legacy SOLO ed ESCLUSIVAMENTE se la migrazione è verificata
           al 100% e priva di qualunque conflitto.
        """
        try:
            json_files = list(self.checkpoint_dir.glob("chk_*.json"))
        except OSError as e:
            logger.warning("Impossibile esplorare directory checkpoint per migrazione legacy: %s", type(e).__name__)
            return

        if not json_files:
            return

        for jf in json_files:
            try:
                # 1. Lettura del checkpoint legacy
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as read_err:
                    logger.warning(
                        "Impossibile leggere file checkpoint legacy %s: %s (file preservato per ispezione)",
                        jf.name,
                        type(read_err).__name__,
                    )
                    continue

                if not isinstance(data, dict):
                    logger.warning(
                        "File checkpoint legacy %s ignorato: struttura radice non valida (atteso dizionario)",
                        jf.name,
                    )
                    continue

                # 2. VALIDAZIONE PRELIMINARE COMPLETA (Fase 1: Campi obbligatori)
                doc_fp = data.get("document_fingerprint")
                cfg_fp = data.get("config_fingerprint")
                doc_id = data.get("document_id")
                a_type = data.get("analysis_type")
                m_id = str(data.get("model_id", ""))
                p_ver = str(data.get("prompt_version", ""))
                t_chunks = data.get("total_planned_chunks")
                s_results = data.get("serialized_results")

                if (
                    not doc_fp
                    or not cfg_fp
                    or not doc_id
                    or not a_type
                    or not isinstance(t_chunks, int)
                    or t_chunks <= 0
                    or not isinstance(s_results, dict)
                ):
                    logger.warning(
                        "File checkpoint legacy %s rifiutato: campi obbligatori mancanti o non conformi. "
                        "Nessuna operazione SQLite eseguita; file preservato su disco.",
                        jf.name,
                    )
                    continue

                # 3. VALIDAZIONE PRELIMINARE COMPLETA (Fase 2: Struttura, indici e provenienza di TUTTI i chunk)
                file_structural_anomaly = False
                parsed_legacy_chunks: dict[int, dict[str, Any]] = {}

                for s_idx, s_val in s_results.items():
                    # Verifica indice numerico intero
                    try:
                        c_idx = int(s_idx)
                    except (ValueError, TypeError):
                        logger.warning(
                            "File legacy %s rifiutato: indice chunk non numerico '%s'. "
                            "Nessuna operazione o testata SQLite parziale creata; file preservato.",
                            jf.name,
                            str(s_idx),
                        )
                        file_structural_anomaly = True
                        break

                    # Verifica intervallo indici [0, total_planned_chunks)
                    if c_idx < 0 or c_idx >= int(t_chunks):
                        logger.warning(
                            "File legacy %s rifiutato: indice chunk %d fuori intervallo [0, %d). "
                            "Nessuna operazione o testata SQLite parziale creata; file preservato.",
                            jf.name,
                            c_idx,
                            int(t_chunks),
                        )
                        file_structural_anomaly = True
                        break

                    # Verifica struttura del payload (dizionario conforme)
                    if not isinstance(s_val, dict):
                        logger.warning(
                            "File legacy %s rifiutato: payload chunk %d non conforme (atteso dizionario). "
                            "Nessuna operazione o testata SQLite parziale creata; file preservato.",
                            jf.name,
                            c_idx,
                        )
                        file_structural_anomaly = True
                        break

                    # Verifica provenienza documentale dichiarata
                    p_doc_id = s_val.get("provenance_document_id")
                    expected_chunk_id = f"{doc_id}::chunk::{c_idx}"
                    if not p_doc_id or (p_doc_id != doc_id and p_doc_id != expected_chunk_id):
                        logger.warning(
                            "File legacy %s rifiutato: provenienza incongruente o aliena ('%s') per chunk %d (atteso '%s'). "
                            "Nessuna operazione o testata SQLite parziale creata; file preservato.",
                            jf.name,
                            str(p_doc_id),
                            c_idx,
                            doc_id,
                        )
                        file_structural_anomaly = True
                        break

                    if c_idx in parsed_legacy_chunks:
                        logger.warning(
                            "File legacy %s rifiutato: indice chunk %d duplicato nel file. File preservato.",
                            jf.name,
                            c_idx,
                        )
                        file_structural_anomaly = True
                        break

                    parsed_legacy_chunks[c_idx] = s_val

                # Se il file contiene QUALSIASI anomalia strutturale, non procedere con alcuna scrittura
                if file_structural_anomaly:
                    continue

                c_at = str(data.get("created_at", datetime.now(timezone.utc).isoformat()))
                u_at = str(data.get("updated_at", c_at))

                # 4. VALIDAZIONE PRELIMINARE COMPLETA (Fase 3: Ispezione coerenza con SQLite e stato consolidato)
                existing_valid_chunks: dict[int, Any] = {}
                sqlite_corrupted_indices: set[int] = set()
                header_exists = False
                header_compatible = True

                try:
                    with self._connect(timeout=1.0) as chk_conn:
                        h_cur = chk_conn.execute("""
                            SELECT document_id, analysis_type, model_id, prompt_version, total_planned_chunks
                            FROM checkpoints
                            WHERE document_fingerprint = ? AND config_fingerprint = ?;
                        """, (str(doc_fp), str(cfg_fp)))
                        h_row = h_cur.fetchone()
                        if h_row is not None:
                            header_exists = True
                            sql_doc_id, sql_a_type, sql_m_id, sql_p_ver, sql_t_chunks = h_row
                            # Verifica rigorosa compatibilità metadati (CORREZIONE 3)
                            if (
                                str(sql_doc_id) != str(doc_id)
                                or str(sql_a_type) != str(a_type)
                                or str(sql_m_id) != str(m_id)
                                or str(sql_p_ver) != str(p_ver)
                                or int(sql_t_chunks) != int(t_chunks)
                            ):
                                header_compatible = False
                                logger.warning(
                                    "Incompatibilità metadati per checkpoint (%s, %s) nel file %s: "
                                    "SQLite (doc_id=%s, type=%s, model=%s, prompt=%s, total=%d) "
                                    "!= legacy (doc_id=%s, type=%s, model=%s, prompt=%s, total=%d). "
                                    "Nessuna fusione né alterazione eseguita; file legacy preservato.",
                                    str(doc_fp)[:8],
                                    str(cfg_fp)[:8],
                                    jf.name,
                                    sql_doc_id,
                                    sql_a_type,
                                    sql_m_id,
                                    sql_p_ver,
                                    sql_t_chunks,
                                    doc_id,
                                    a_type,
                                    m_id,
                                    p_ver,
                                    t_chunks,
                                )

                        cur = chk_conn.execute("""
                            SELECT chunk_index, serialized_result FROM checkpoint_chunks
                            WHERE document_fingerprint = ? AND config_fingerprint = ?;
                        """, (str(doc_fp), str(cfg_fp)))
                        for row in cur.fetchall():
                            try:
                                c_idx_val = int(row[0])
                            except (ValueError, TypeError):
                                sqlite_corrupted_indices.add(-1)
                                continue

                            try:
                                parsed_chunk = json.loads(row[1])
                                if isinstance(parsed_chunk, dict):
                                    existing_valid_chunks[c_idx_val] = parsed_chunk
                                else:
                                    sqlite_corrupted_indices.add(c_idx_val)
                            except Exception:
                                # Chunk in SQLite corrotto/non valido (CORREZIONE 1)
                                sqlite_corrupted_indices.add(c_idx_val)

                except sqlite3.OperationalError:
                    raise
                except Exception as insp_err:
                    logger.warning("Errore durante ispezione SQLite per %s: %s", jf.name, type(insp_err).__name__)
                    continue

                # Se i metadati della testata sono incompatibili, non eseguire fusioni arbitrarie
                if header_exists and not header_compatible:
                    # File legacy preservato su disco, testata SQLite inalterata
                    continue

                # 5. INDIVIDUAZIONE DUPLICATI O CONFLITTI
                has_conflict = False
                chunks_to_insert: dict[int, str] = {}
                valid_legacy_indices: list[int] = list(parsed_legacy_chunks.keys())

                if sqlite_corrupted_indices:
                    # CORREZIONE 1: Chunk SQLite corrotto produce un conflitto esplicito e preserva il legacy
                    has_conflict = True
                    logger.warning(
                        "Rilevati chunk SQLite non decodificabili/corrotti agli indici %s per (%s, %s). "
                        "File legacy valido %s preservato su disco; nessun overwrite arbitrario.",
                        sqlite_corrupted_indices,
                        str(doc_fp)[:8],
                        str(cfg_fp)[:8],
                        jf.name,
                    )

                for c_idx, s_val in parsed_legacy_chunks.items():
                    if c_idx in sqlite_corrupted_indices:
                        # Chunk SQLite corrotto: conflitto esplicito, preserva legacy
                        has_conflict = True
                    elif c_idx in existing_valid_chunks:
                        existing_val = existing_valid_chunks[c_idx]
                        if existing_val == s_val:
                            # Contenuto identico: nessun conflitto, SQLite già consolidato
                            pass
                        else:
                            # Contenuto divergente: precedenza assoluta a SQLite consolidato
                            has_conflict = True
                            logger.warning(
                                "Conflitto rilevato per checkpoint (%s, %s) blocco %d nel file %s: "
                                "il dato consolidato in SQLite prevale. Il file legacy viene preservato intatto.",
                                str(doc_fp)[:8],
                                str(cfg_fp)[:8],
                                c_idx,
                                jf.name,
                            )
                    else:
                        # Blocco mancante in SQLite: inserimento sicuro
                        chunks_to_insert[c_idx] = json.dumps(s_val, ensure_ascii=False)

                # 6. TRANSAZIONE ATOMICA SQLITE PER TESTATA E BLOCCHI IDONEI
                if chunks_to_insert or not header_exists:
                    with self._connect(timeout=1.0) as conn:
                        if not header_exists:
                            conn.execute("""
                                INSERT INTO checkpoints (
                                    document_fingerprint, config_fingerprint, document_id,
                                    analysis_type, model_id, prompt_version, total_planned_chunks,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(document_fingerprint, config_fingerprint) DO NOTHING;
                            """, (str(doc_fp), str(cfg_fp), str(doc_id), str(a_type), m_id, p_ver, int(t_chunks), c_at, u_at))

                        for c_idx, json_val in chunks_to_insert.items():
                            conn.execute("""
                                INSERT INTO checkpoint_chunks (
                                    document_fingerprint, config_fingerprint, chunk_index,
                                    serialized_result, created_at
                                ) VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(document_fingerprint, config_fingerprint, chunk_index) DO NOTHING;
                            """, (str(doc_fp), str(cfg_fp), c_idx, json_val, u_at))

                # 7. Verifica analitica della persistenza e parsabilità dei risultati migrati (CORREZIONE 1)
                migration_verified = False
                if not has_conflict:
                    with self._connect(timeout=1.0) as verify_conn:
                        v_cur = verify_conn.execute("""
                            SELECT chunk_index, serialized_result
                            FROM checkpoint_chunks
                            WHERE document_fingerprint = ? AND config_fingerprint = ?;
                        """, (str(doc_fp), str(cfg_fp)))
                        v_rows = v_cur.fetchall()
                        persisted_chunks: dict[int, Any] = {}
                        all_sqlite_parseable = True
                        for r_idx, r_val in v_rows:
                            try:
                                p_idx = int(r_idx)
                                p_val = json.loads(r_val)
                                if isinstance(p_val, dict):
                                    persisted_chunks[p_idx] = p_val
                                else:
                                    all_sqlite_parseable = False
                            except Exception:
                                all_sqlite_parseable = False

                        expected_set = set(valid_legacy_indices)
                        actual_set = set(persisted_chunks.keys())
                        if all_sqlite_parseable and expected_set and expected_set.issubset(actual_set):
                            migration_verified = True
                        else:
                            logger.warning(
                                "Verifica analitica fallita per %s: attesi indici %s, trovati validi %s (parseable=%s). File legacy preservato.",
                                jf.name,
                                expected_set,
                                actual_set,
                                all_sqlite_parseable,
                            )

                # 8. Rimozione sicura del file legacy SOLO se privo di conflitti e verificato al 100%
                if has_conflict or not migration_verified:
                    logger.info(
                        "File legacy %s preservato su disco (has_conflict=%s, verified=%s).",
                        jf.name,
                        has_conflict,
                        migration_verified,
                    )
                else:
                    try:
                        jf.unlink()
                        logger.info("Migrazione checkpoint legacy completata con successo: %s", jf.name)
                    except OSError as unl_err:
                        logger.warning("Impossibile rimuovere file legacy migrato %s: %s", jf.name, type(unl_err).__name__)

            except sqlite3.OperationalError as db_lock_err:
                logger.warning(
                    "Migrazione differita per %s: database SQLite non disponibile o bloccato (%s)",
                    jf.name,
                    type(db_lock_err).__name__,
                )
            except Exception as mig_err:
                logger.warning(
                    "Migrazione fallita per file legacy %s: %s (file originale preservato)",
                    jf.name,
                    type(mig_err).__name__,
                )

    def _get_checkpoint_path(self, document_fingerprint: str, config_fingerprint: str) -> Path:
        """Percorso di compatibilità per test legacy."""
        safe_name = f"chk_{document_fingerprint[:16]}_{config_fingerprint[:16]}.json"
        return self.checkpoint_dir / safe_name

    def has_checkpoint(self, document_fingerprint: str, config_fingerprint: str) -> bool:
        """Verifica la presenza di un checkpoint con almeno un blocco o sotto-blocco salvato."""
        try:
            with self._connect() as conn:
                cur = conn.execute("""
                    SELECT 1 FROM checkpoint_chunks
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    UNION ALL
                    SELECT 1 FROM checkpoint_subchunks
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    LIMIT 1;
                """, (document_fingerprint, config_fingerprint, document_fingerprint, config_fingerprint))
                if cur.fetchone() is not None:
                    return True
        except Exception:
            pass
        # Fallback su file legacy se presente e valido
        p = self._get_checkpoint_path(document_fingerprint, config_fingerprint)
        if p.is_file() and p.stat().st_size > 0:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return bool(data.get("serialized_results"))
            except Exception:
                return False
        return False

    def _load_checkpoint_from_sqlite(
        self,
        document_fingerprint: str,
        config_fingerprint: str,
    ) -> AnalysisCheckpoint | None:
        """Carica internamente un checkpoint da SQLite se presente e valido."""
        try:
            with self._connect() as conn:
                cur = conn.execute("""
                    SELECT document_id, analysis_type, model_id, prompt_version,
                           total_planned_chunks, created_at, updated_at
                    FROM checkpoints
                    WHERE document_fingerprint = ? AND config_fingerprint = ?;
                """, (document_fingerprint, config_fingerprint))
                main_row = cur.fetchone()
                if not main_row:
                    return None

                doc_id, a_type, m_id, p_ver, t_chunks, c_at, u_at = main_row

                chunk_cur = conn.execute("""
                    SELECT chunk_index, serialized_result
                    FROM checkpoint_chunks
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    ORDER BY chunk_index ASC;
                """, (document_fingerprint, config_fingerprint))
                chunk_rows = chunk_cur.fetchall()

                sub_cur = conn.execute("""
                    SELECT parent_chunk_index, subchunk_index, subchunk_id, total_subchunks,
                           serialized_result, evidence_ids, created_at
                    FROM checkpoint_subchunks
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    ORDER BY parent_chunk_index ASC, subchunk_index ASC;
                """, (document_fingerprint, config_fingerprint))
                sub_rows = sub_cur.fetchall()

                if not chunk_rows and not sub_rows:
                    return None

                completed_indices = []
                serialized_results = {}
                for c_idx, s_raw in chunk_rows:
                    completed_indices.append(int(c_idx))
                    serialized_results[str(c_idx)] = json.loads(s_raw)

                parsed_subs = []
                for s_row in sub_rows:
                    try:
                        p_res = json.loads(s_row[4])
                        p_eids = json.loads(s_row[5]) if s_row[5] else []
                    except Exception:
                        continue
                    parsed_subs.append({
                        "parent_chunk_index": int(s_row[0]),
                        "subchunk_index": int(s_row[1]),
                        "subchunk_id": str(s_row[2]),
                        "total_subchunks": int(s_row[3]),
                        "serialized_result": p_res,
                        "evidence_ids": list(p_eids),
                        "created_at": str(s_row[6]),
                    })

                return AnalysisCheckpoint(
                    document_id=str(doc_id),
                    document_fingerprint=str(document_fingerprint),
                    config_fingerprint=str(config_fingerprint),
                    analysis_type=str(a_type),
                    model_id=str(m_id),
                    prompt_version=str(p_ver),
                    total_planned_chunks=int(t_chunks),
                    completed_chunk_indices=tuple(sorted(completed_indices)),
                    serialized_results=serialized_results,
                    created_at=str(c_at),
                    updated_at=str(u_at),
                    subchunks=tuple(parsed_subs),
                )
        except Exception:
            return None

    def load_checkpoint(
        self,
        document_fingerprint: str,
        config_fingerprint: str,
    ) -> AnalysisCheckpoint | None:
        """
        Carica lo stato del checkpoint salvato per (document_fingerprint, config_fingerprint).
        
        Priorità esplicita a SQLite:
        1. Se in SQLite è presente un checkpoint valido, viene data priorità a SQLite.
           Un eventuale file legacy JSON presente viene esaminato:
           - se corrotto o illeggibile, viene segnalato e preservato su disco senza impedire
             il caricamento del record SQLite consolidato;
           - se valido, viene avviata la riconciliazione ordinata (precedenza assoluta
             ai dati consolidati SQLite, importazione di blocchi mancanti, preservazione
             del file legacy in caso di conflitti).
        2. Se in SQLite il checkpoint è assente:
           - se è presente un file legacy valido, viene migrato in SQLite e restituito;
           - se il file legacy è corrotto o non compatibile, viene restituito None
             e il file viene preservato su disco per ispezione.
        """
        # 1. Caricamento prioritario da SQLite
        sqlite_chk = self._load_checkpoint_from_sqlite(document_fingerprint, config_fingerprint)
        p = self._get_checkpoint_path(document_fingerprint, config_fingerprint)

        if sqlite_chk is not None:
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        logger.warning(
                            "File legacy %s ha struttura non valida; utilizzato record SQLite consolidato.",
                            p.name,
                        )
                    else:
                        # Riconciliazione: integra eventuali blocchi mancanti senza sovrascritture
                        self._migrate_legacy_json_if_needed()
                        reconciled = self._load_checkpoint_from_sqlite(document_fingerprint, config_fingerprint)
                        if reconciled is not None:
                            return reconciled
                except Exception as legacy_err:
                    # JSON corrotto: NON deve mascherare il record SQLite valido
                    logger.warning(
                        "File legacy JSON corrotto o illeggibile rilevato per (%s, %s): %s. "
                        "Utilizzato checkpoint SQLite valido consolidato; file legacy preservato.",
                        document_fingerprint[:8],
                        config_fingerprint[:8],
                        type(legacy_err).__name__,
                    )
            return sqlite_chk

        # 2. SQLite assente: verifica file legacy
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning("File legacy %s non valido (atteso dizionario).", p.name)
                    return None
                if (
                    data.get("document_fingerprint") != document_fingerprint
                    or data.get("config_fingerprint") != config_fingerprint
                ):
                    return None

                self._migrate_legacy_json_if_needed()
                return self._load_checkpoint_from_sqlite(document_fingerprint, config_fingerprint)
            except Exception as read_err:
                logger.warning(
                    "Impossibile caricare file legacy %s: %s (file preservato per ispezione)",
                    p.name,
                    type(read_err).__name__,
                )
                return None

        return None

    def load_and_validate_checkpoint(
        self,
        document: ConversationEvidenceDocument,
        config_fingerprint: str,
        chunks: Sequence[ConversationEvidenceDocument] | None = None,
        expected_topic_query: TopicQuery | None = None,
        expected_analysis_type: str | None = None,
    ) -> AnalysisCheckpoint | None:
        """
        Carica e rivalida integralmente il checkpoint:
        1. Calcola il fingerprint crittografico del documento attuale;
        2. Carica i blocchi validati da SQLite;
        3. Rivalida che tutte le evidenze citate esistano e, se chunks è fornito,
           appartengano tassativamente allo specifico blocco di provenienza;
        4. Rivalida la provenienza documentale di ciascun chunk e la compatibilità con la query.
        """
        doc_fp = compute_document_fingerprint(document)
        chk = self.load_checkpoint(doc_fp, config_fingerprint)
        if chk is None:
            return None
        if not validate_checkpoint_evidence_references(
            checkpoint=chk,
            document=document,
            chunks=chunks,
            expected_topic_query=expected_topic_query,
            expected_analysis_type=expected_analysis_type,
        ):
            return None
        return chk

    def save_chunk(
        self,
        document_id: str,
        document_fingerprint: str,
        config_fingerprint: str,
        chunk_idx: int,
        result: TopicDetectionResult | TopicDiscoveryResult,
        total_planned_chunks: int,
        analysis_type: str,
        model_id: str,
        prompt_version: str,
    ) -> None:
        """
        Salva atomicamente il singolo blocco validato nel database SQLite (scrittura incrementale O(1)).
        Non rilegge né riscrive i blocchi precedenti, garantendo scalabilità su migliaia di chunk.
        """
        if isinstance(result, TopicDetectionResult):
            serialized_chunk = serialize_detection_result_for_checkpoint(result)
        elif isinstance(result, TopicDiscoveryResult):
            serialized_chunk = serialize_discovery_result_for_checkpoint(result)
        else:
            raise TypeError(f"Tipo risultato non supportato per checkpoint: {type(result)}")

        serialized_json = json.dumps(serialized_chunk, ensure_ascii=False)
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            with self._lock:
                with self._connect() as conn:
                    # Inserisce o aggiorna la testata checkpoint
                    conn.execute("""
                        INSERT INTO checkpoints (
                            document_fingerprint, config_fingerprint, document_id,
                            analysis_type, model_id, prompt_version, total_planned_chunks,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint) DO UPDATE SET
                            updated_at = excluded.updated_at,
                            total_planned_chunks = excluded.total_planned_chunks;
                    """, (
                        document_fingerprint, config_fingerprint, document_id,
                        analysis_type, model_id, prompt_version, total_planned_chunks,
                        now_iso, now_iso,
                    ))

                    # Inserisce o aggiorna il singolo chunk
                    conn.execute("""
                        INSERT INTO checkpoint_chunks (
                            document_fingerprint, config_fingerprint, chunk_index,
                            serialized_result, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint, chunk_index) DO UPDATE SET
                            serialized_result = excluded.serialized_result;
                    """, (
                        document_fingerprint, config_fingerprint, chunk_idx,
                        serialized_json, now_iso,
                    ))
        except Exception as exc:
            raise CheckpointSaveError(f"Impossibile salvare il chunk {chunk_idx} in SQLite: {exc}") from exc

    def save_subchunk(
        self,
        document_id: str,
        document_fingerprint: str,
        config_fingerprint: str,
        parent_chunk_index: int,
        subchunk_index: int,
        total_subchunks: int,
        result: TopicDiscoveryResult,
        subchunk_id: str,
        evidence_ids: Sequence[str],
        total_planned_chunks: int = 1,
        analysis_type: str = "discovery",
        model_id: str = "",
        prompt_version: str = "",
    ) -> None:
        """
        Salva incrementalmente un sotto-blocco validato in SQLite preservando la relazione parent/child.
        """
        serialized_chunk = serialize_discovery_result_for_checkpoint(result)
        serialized_json = json.dumps(serialized_chunk, ensure_ascii=False)
        eids_json = json.dumps(list(evidence_ids), ensure_ascii=False)
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO checkpoints (
                            document_fingerprint, config_fingerprint, document_id,
                            analysis_type, model_id, prompt_version, total_planned_chunks,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint) DO UPDATE SET
                            updated_at = excluded.updated_at;
                    """, (
                        document_fingerprint, config_fingerprint, document_id,
                        analysis_type, model_id, prompt_version, total_planned_chunks,
                        now_iso, now_iso,
                    ))

                    conn.execute("""
                        INSERT INTO checkpoint_subchunks (
                            document_fingerprint, config_fingerprint, parent_chunk_index,
                            subchunk_index, subchunk_id, total_subchunks,
                            serialized_result, evidence_ids, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint, parent_chunk_index, subchunk_index) DO UPDATE SET
                            subchunk_id = excluded.subchunk_id,
                            total_subchunks = excluded.total_subchunks,
                            serialized_result = excluded.serialized_result,
                            evidence_ids = excluded.evidence_ids;
                    """, (
                        document_fingerprint, config_fingerprint, parent_chunk_index,
                        subchunk_index, subchunk_id, total_subchunks,
                        serialized_json, eids_json, now_iso,
                    ))
        except Exception as exc:
            raise CheckpointSaveError(
                f"Impossibile salvare il sotto-blocco ({parent_chunk_index}, {subchunk_index}) in SQLite: {exc}"
            ) from exc

    def get_subchunks(
        self,
        document_fingerprint: str,
        config_fingerprint: str,
        parent_chunk_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Recupera i sotto-blocchi validati salvati per (document_fingerprint, config_fingerprint),
        opzionalmente filtrati per parent_chunk_index.
        """
        subchunks: list[dict[str, Any]] = []
        try:
            with self._connect() as conn:
                if parent_chunk_index is not None:
                    cur = conn.execute("""
                        SELECT parent_chunk_index, subchunk_index, subchunk_id, total_subchunks,
                               serialized_result, evidence_ids, created_at
                        FROM checkpoint_subchunks
                        WHERE document_fingerprint = ? AND config_fingerprint = ? AND parent_chunk_index = ?
                        ORDER BY subchunk_index ASC;
                    """, (document_fingerprint, config_fingerprint, parent_chunk_index))
                else:
                    cur = conn.execute("""
                        SELECT parent_chunk_index, subchunk_index, subchunk_id, total_subchunks,
                               serialized_result, evidence_ids, created_at
                        FROM checkpoint_subchunks
                        WHERE document_fingerprint = ? AND config_fingerprint = ?
                        ORDER BY parent_chunk_index ASC, subchunk_index ASC;
                    """, (document_fingerprint, config_fingerprint))

                for row in cur.fetchall():
                    try:
                        parsed_res = json.loads(row[4])
                        parsed_eids = json.loads(row[5]) if row[5] else []
                    except Exception:
                        continue
                    subchunks.append({
                        "parent_chunk_index": int(row[0]),
                        "subchunk_index": int(row[1]),
                        "subchunk_id": str(row[2]),
                        "total_subchunks": int(row[3]),
                        "serialized_result": parsed_res,
                        "evidence_ids": list(parsed_eids),
                        "created_at": str(row[6]),
                    })
        except Exception as e:
            logger.warning("Impossibile recuperare sotto-blocchi da SQLite: %s", type(e).__name__)
        return subchunks

    def get_subchunk(
        self,
        document_fingerprint: str,
        config_fingerprint: str,
        parent_chunk_index: int,
        subchunk_index: int,
    ) -> dict[str, Any] | None:
        """Recupera un singolo sotto-blocco specificato da indici genitore e figlio."""
        subs = self.get_subchunks(document_fingerprint, config_fingerprint, parent_chunk_index=parent_chunk_index)
        for s in subs:
            if s["subchunk_index"] == subchunk_index:
                return s
        return None

    def save_chunk_diagnostic(
        self,
        document_id: str,
        document_fingerprint: str,
        config_fingerprint: str,
        diagnostic: ChunkDiagnosticRecord,
        total_planned_chunks: int = 1,
        analysis_type: str = "discovery",
        model_id: str = "",
        prompt_version: str = "",
    ) -> None:
        """
        Salva la diagnostica dettagliata del singolo chunk nel database SQLite.
        Persiste anche in caso di fallimento o rifiuto della validazione.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                with self._connect() as conn:
                    # Inserisce o aggiorna la testata checkpoint se non presente
                    conn.execute("""
                        INSERT INTO checkpoints (
                            document_fingerprint, config_fingerprint, document_id,
                            analysis_type, model_id, prompt_version, total_planned_chunks,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint) DO UPDATE SET
                            updated_at = excluded.updated_at;
                    """, (
                        document_fingerprint, config_fingerprint, document_id,
                        analysis_type, model_id, prompt_version, total_planned_chunks,
                        now_iso, now_iso,
                    ))

                    conn.execute("""
                        INSERT INTO checkpoint_chunk_diagnostics (
                            document_fingerprint, config_fingerprint, chunk_index,
                            chunk_id, status, attempts, duration_seconds, model_id,
                            http_status, finish_reason, prompt_tokens, completion_tokens,
                            error_type, diagnostic_category, failure_phase, error_detail,
                            json_valid, schema_valid, evidence_valid, checkpoint_saved, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_fingerprint, config_fingerprint, chunk_index) DO UPDATE SET
                            status = excluded.status,
                            attempts = excluded.attempts,
                            duration_seconds = excluded.duration_seconds,
                            model_id = excluded.model_id,
                            http_status = excluded.http_status,
                            finish_reason = excluded.finish_reason,
                            prompt_tokens = excluded.prompt_tokens,
                            completion_tokens = excluded.completion_tokens,
                            error_type = excluded.error_type,
                            diagnostic_category = excluded.diagnostic_category,
                            failure_phase = excluded.failure_phase,
                            error_detail = excluded.error_detail,
                            json_valid = excluded.json_valid,
                            schema_valid = excluded.schema_valid,
                            evidence_valid = excluded.evidence_valid,
                            checkpoint_saved = excluded.checkpoint_saved,
                            updated_at = excluded.updated_at;
                    """, (
                        document_fingerprint,
                        config_fingerprint,
                        diagnostic.chunk_index,
                        diagnostic.chunk_id,
                        diagnostic.status,
                        diagnostic.attempts,
                        diagnostic.duration_seconds,
                        diagnostic.model_id,
                        diagnostic.http_status,
                        diagnostic.finish_reason,
                        diagnostic.prompt_tokens,
                        diagnostic.completion_tokens,
                        diagnostic.error_type,
                        diagnostic.diagnostic_category,
                        diagnostic.failure_phase,
                        diagnostic.error_detail,
                        1 if diagnostic.json_valid else 0,
                        1 if diagnostic.schema_valid else 0,
                        1 if diagnostic.evidence_valid else 0,
                        1 if diagnostic.checkpoint_saved else 0,
                        now_iso,
                    ))
        except Exception as exc:
            logger.warning("Impossibile salvare diagnostica chunk %s in SQLite: %s", diagnostic.chunk_id, exc)
            raise CheckpointSaveError(f"Impossibile salvare diagnostica chunk {diagnostic.chunk_id}: {exc}") from exc

    def get_chunk_diagnostics(
        self,
        document_fingerprint: str,
        config_fingerprint: str,
    ) -> list[ChunkDiagnosticRecord]:
        """Recupera la cronologia diagnostica di tutti i chunk salvati per la run."""
        diagnostics: list[ChunkDiagnosticRecord] = []
        try:
            with self._connect() as conn:
                cur = conn.execute("""
                    SELECT chunk_index, chunk_id, status, attempts, duration_seconds,
                           model_id, http_status, finish_reason, prompt_tokens,
                           completion_tokens, error_type, diagnostic_category,
                           failure_phase, error_detail, json_valid, schema_valid,
                           evidence_valid, checkpoint_saved
                    FROM checkpoint_chunk_diagnostics
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    ORDER BY chunk_index ASC;
                """, (document_fingerprint, config_fingerprint))
                for row in cur.fetchall():
                    diagnostics.append(
                        ChunkDiagnosticRecord(
                            chunk_index=int(row[0]),
                            chunk_id=str(row[1]),
                            status=str(row[2]),
                            attempts=int(row[3]),
                            duration_seconds=float(row[4]),
                            model_id=str(row[5]),
                            http_status=row[6],
                            finish_reason=row[7],
                            prompt_tokens=row[8],
                            completion_tokens=row[9],
                            error_type=row[10],
                            diagnostic_category=str(row[11]),
                            failure_phase=row[12],
                            error_detail=row[13],
                            json_valid=bool(row[14]),
                            schema_valid=bool(row[15]),
                            evidence_valid=bool(row[16]),
                            checkpoint_saved=bool(row[17]),
                        )
                    )
        except Exception as e:
            logger.warning("Impossibile recuperare diagnostica chunk da SQLite: %s", type(e).__name__)
        return diagnostics

    def get_latest_checkpoint_metadata_for_document(
        self,
        document_id: str,
        document_fingerprint: str | None = None,
        analysis_type: str = "discovery",
        model_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Recupera i metadati dell'ultimo checkpoint SQLite registrato per il documento e tipo di analisi.
        Consente alla UI e al servizio di ripristinare stato e diagnostica anche dopo un riavvio completo di Streamlit.
        Se model_id è specificato, filtra rigorosamente per quel modello.
        """
        try:
            with self._connect() as conn:
                clauses = ["document_id = ?", "analysis_type = ?"]
                params: list[Any] = [document_id, analysis_type]
                if document_fingerprint:
                    clauses.append("document_fingerprint = ?")
                    params.append(document_fingerprint)
                if model_id:
                    clauses.append("model_id = ?")
                    params.append(model_id)

                where_sql = " AND ".join(clauses)
                sql = f"""
                    SELECT document_fingerprint, config_fingerprint, document_id,
                           analysis_type, model_id, prompt_version, total_planned_chunks,
                           created_at, updated_at
                    FROM checkpoints
                    WHERE {where_sql}
                    ORDER BY updated_at DESC
                    LIMIT 1;
                """
                cur = conn.execute(sql, tuple(params))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "document_fingerprint": str(row[0]),
                    "config_fingerprint": str(row[1]),
                    "document_id": str(row[2]),
                    "analysis_type": str(row[3]),
                    "model_id": str(row[4]),
                    "prompt_version": str(row[5]),
                    "total_planned_chunks": int(row[6]),
                    "created_at": str(row[7]),
                    "updated_at": str(row[8]),
                }
        except Exception as e:
            logger.warning("Impossibile recuperare metadati checkpoint per document_id '%s': %s", document_id, e)
            return None

    def clear_checkpoint(self, document_fingerprint: str, config_fingerprint: str) -> bool:
        """Elimina il checkpoint una volta conclusa con successo l'analisi complessiva."""
        deleted = False
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute("""
                        DELETE FROM checkpoint_subchunks
                        WHERE document_fingerprint = ? AND config_fingerprint = ?;
                    """, (document_fingerprint, config_fingerprint))
                    cur = conn.execute("""
                        DELETE FROM checkpoints
                        WHERE document_fingerprint = ? AND config_fingerprint = ?;
                    """, (document_fingerprint, config_fingerprint))
                    if cur.rowcount > 0:
                        deleted = True
        except Exception:
            pass

        # Pulisce anche eventuale file legacy
        p = self._get_checkpoint_path(document_fingerprint, config_fingerprint)
        if p.is_file():
            try:
                p.unlink()
                deleted = True
            except OSError:
                pass
        return deleted

    def clear_for_document(
        self,
        document_or_fingerprint: ConversationEvidenceDocument | str,
    ) -> int:
        """
        Cancellazione controllata e sicura di tutti i checkpoint appartenenti ESCLUSIVAMENTE
        alla specifica versione/contenuto del documento indicato, identificata dal suo fingerprint crittografico.
        
        Accetta:
        - un oggetto ConversationEvidenceDocument (da cui calcola il fingerprint crittografico univoco)
        - un fingerprint SHA-256 esadecimale valido a 64 caratteri.
        
        NON utilizza MAI 'OR document_id = ?': versioni differenti dello stesso documento
        (es. con messaggi aggiunti o rimossi) che condividono lo stesso document_id conservano
        integri i rispettivi checkpoint senza alcuna cancellazione collaterale.
        
        Per una cancellazione trasversale di tutte le versioni con lo stesso identificativo leggibile,
        utilizzare l'API separata ed esplicita clear_all_for_document_id().
        """
        if isinstance(document_or_fingerprint, ConversationEvidenceDocument):
            doc_fp = compute_document_fingerprint(document_or_fingerprint)
        elif isinstance(document_or_fingerprint, str):
            val = document_or_fingerprint.strip()
            if len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val):
                doc_fp = val.lower()
            else:
                logger.warning(
                    "clear_for_document invocato con valore non valido come fingerprint crittografico (lunghezza: %d). "
                    "Operazione ignorata per prevenire cancellazioni accidentali. "
                    "Utilizzare clear_all_for_document_id se si intende cancellare per identificativo leggibile.",
                    len(val),
                )
                return 0
        else:
            return 0

        cleared_count = 0
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute("""
                        DELETE FROM checkpoint_subchunks
                        WHERE document_fingerprint = ?;
                    """, (doc_fp,))
                    cur = conn.execute("""
                        DELETE FROM checkpoints
                        WHERE document_fingerprint = ?;
                    """, (doc_fp,))
                    cleared_count = cur.rowcount if cur else 0

                # Pulisce esclusivamente gli eventuali file JSON legacy appartenenti e verificati per questo fingerprint
                for p in self.checkpoint_dir.glob(f"chk_{doc_fp[:16]}_*.json"):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if not isinstance(data, dict):
                            logger.warning("File legacy %s non decodificabile in dizionario in clear_for_document; preservato.", p.name)
                            continue
                        f_doc_fp = data.get("document_fingerprint")
                        if f_doc_fp and f_doc_fp != doc_fp:
                            logger.warning("File legacy %s con fingerprint divergente (%s != %s); preservato.", p.name, f_doc_fp[:8], doc_fp[:8])
                            continue
                        p.unlink()
                        cleared_count += 1
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as read_err:
                        logger.warning(
                            "File legacy %s non decodificabile durante clear_for_document (%s); preservato per sicurezza.",
                            p.name,
                            type(read_err).__name__,
                        )
        except Exception as err:
            logger.warning("Errore durante clear_for_document per fingerprint: %s", type(err).__name__)
        return cleared_count

    def clear_all_for_document_id(self, document_id: str) -> int:
        """
        Cancellazione intenzionale e completa di TUTTI i checkpoint che condividono
        lo stesso identificativo leggibile (document_id), indipendentemente dal fingerprint
        e dalla specifica versione/lunghezza del documento.
        
        Protocollo rigoroso di riconciliazione (CORREZIONE 2):
        1. Recupera da SQLite tutti i document_fingerprint associati a questo document_id
           prima di qualunque eliminazione;
        2. Esamina i file legacy JSON nella directory con un'UNICA procedura di attribuzione verificata:
           - I file non decodificabili o strutturalmente non validi vengono rigorosamente preservati
             su disco (nessun fallthrough a unlink via except Exception: pass);
           - I file appartenenti ad altri documenti (document_id diverso) vengono tassativamente preservati;
           - I soli file validi esplicitamente attribuiti a questo document_id vengono candidati alla rimozione;
        3. Elimina da SQLite i record per document_id e per i fingerprint associati;
        4. Rimuove i soli file legacy JSON verificati ed attribuiti a questo document_id;
        5. Segnala le eventuali cancellazioni parziali o file preservati;
        6. Garantisce che i checkpoint cancellati non possano risorgere tramite residui legacy.
        
        Restituisce il conteggio totale di record SQLite e file legacy rimossi.
        """
        doc_id = str(document_id).strip()
        if not doc_id:
            return 0

        cleared_count = 0
        try:
            with self._lock:
                verified_doc_fps: set[str] = set()
                other_doc_fps: set[str] = set()

                # 1. Recupera da SQLite i fingerprint associati a questo document_id e quelli di altri documenti
                try:
                    with self._connect() as conn:
                        cur = conn.execute("""
                            SELECT DISTINCT document_fingerprint
                            FROM checkpoints
                            WHERE document_id = ?;
                        """, (doc_id,))
                        for row in cur.fetchall():
                            if row[0]:
                                verified_doc_fps.add(str(row[0]))

                        cur_other = conn.execute("""
                            SELECT DISTINCT document_fingerprint
                            FROM checkpoints
                            WHERE document_id != ?;
                        """, (doc_id,))
                        for row in cur_other.fetchall():
                            if row[0]:
                                other_doc_fps.add(str(row[0]))
                except Exception as db_err:
                    logger.warning("Errore recupero fingerprint per document_id '%s': %s", doc_id, type(db_err).__name__)

                # 2. Unica procedura di attribuzione verificata per tutti i file legacy
                legacy_files_to_delete: list[Path] = []
                unattributable_files: list[Path] = []
                contradictory_files: list[Path] = []

                try:
                    for jf in self.checkpoint_dir.glob("chk_*.json"):
                        try:
                            with open(jf, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            if not isinstance(data, dict):
                                unattributable_files.append(jf)
                                continue

                            f_doc_id = data.get("document_id")
                            f_doc_fp = data.get("document_fingerprint")

                            # Rilevamento contraddizione: il file dichiara questo document_id ma un fingerprint
                            # che nel database appartiene a un altro documento distinto
                            if f_doc_fp and str(f_doc_fp) in other_doc_fps and f_doc_id == doc_id:
                                logger.warning(
                                    "File legacy %s presenta contraddizione: dichiara document_id '%s' "
                                    "ma il fingerprint %s appartiene ad altro documento nel database. File preservato.",
                                    jf.name,
                                    doc_id,
                                    str(f_doc_fp)[:8],
                                )
                                contradictory_files.append(jf)
                                continue

                            # File appartenente chiaramente ad un altro documento
                            if f_doc_id and f_doc_id != doc_id:
                                continue

                            # File attribuibile al documento richiesto
                            if f_doc_id == doc_id:
                                legacy_files_to_delete.append(jf)
                            elif f_doc_fp and str(f_doc_fp) in verified_doc_fps:
                                # Nessun document_id nel file, ma fingerprint accertato per questo doc_id
                                legacy_files_to_delete.append(jf)

                        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as read_err:
                            logger.warning(
                                "File legacy %s non decodificabile durante clear_all_for_document_id: %s. Preservato per sicurezza.",
                                jf.name,
                                type(read_err).__name__,
                            )
                            unattributable_files.append(jf)
                except OSError as glob_err:
                    logger.warning("Errore esplorazione directory durante clear_all_for_document_id: %s", type(glob_err).__name__)

                if unattributable_files or contradictory_files:
                    logger.warning(
                        "Cancellazione selettiva per document_id '%s': preservati %d file non decodificabili e %d file contraddittori.",
                        doc_id,
                        len(unattributable_files),
                        len(contradictory_files),
                    )

                # 3. Cancellazione atomica da SQLite: limitata tassativamente al document_id indicato
                # I file legacy non verificati non possono in alcun caso ampliare il perimetro SQLite
                with self._connect() as conn:
                    cur = conn.execute("""
                        DELETE FROM checkpoints
                        WHERE document_id = ?;
                    """, (doc_id,))
                    cleared_count += cur.rowcount if cur else 0

                # 4. Eliminazione dei SOLI file legacy verificati ed attribuiti a questo document_id
                failed_deletions: list[Path] = []
                for jf in legacy_files_to_delete:
                    try:
                        if jf.exists():
                            jf.unlink()
                            cleared_count += 1
                    except OSError as unl_err:
                        logger.warning("Impossibile eliminare file legacy %s: %s", jf.name, type(unl_err).__name__)
                        failed_deletions.append(jf)

                if failed_deletions:
                    logger.warning(
                        "Cancellazione parziale per document_id '%s': %d file legacy non sono stati eliminati per errori I/O.",
                        doc_id,
                        len(failed_deletions),
                    )

        except Exception as err:
            logger.warning("Errore durante clear_all_for_document_id: %s", type(err).__name__)
        return cleared_count

    def clear_all_for_document(self, document_or_fingerprint: ConversationEvidenceDocument | str) -> int:
        """Alias retrocompatibile per clear_for_document."""
        return self.clear_for_document(document_or_fingerprint)

