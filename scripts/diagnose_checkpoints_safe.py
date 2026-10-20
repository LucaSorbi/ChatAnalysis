"""
scripts/diagnose_checkpoints_safe.py
------------------------------------
Strumento diagnostico locale di SOLA LETTURA per la verifica forense e sicura
delle sessioni di checkpoint su ambiente Ubuntu.

GARANZIE DI SICUREZZA, PRIVACY E RIGORE (VERSIONE CORRETTA CONCLUSIVA):
1. ACCESSO ESCLUSIVO IN SOLA LETTURA:
   - Apertura SQLite TASSATIVAMENTE con URI `mode=ro` (read-only) e PRAGMA query_only = ON.
   - Se l'apertura in sola lettura fallisce, l'audit per quel database si arresta con codice standard.
   - NESSUN fallback a connessioni ordinarie read-write.
2. PRIVACY RIGOROSA E ALLOWLIST CHIUSA DEI METADATI:
   - Allowlist chiuse e verificate per diagnostic_category, failure_phase, finish_reason, model_id, prompt_version, analysis_type.
   - Sostituzione di qualsiasi valore non riconosciuto o arbitrario con codici opachi predefiniti.
   - Validazione rigorosa dei timestamp (formato ISO 8601 standard; in caso contrario TIMESTAMP_OPAQUE).
   - Nessun identificativo documentale riconoscibile (token deterministico opaco basato su hash).
   - Nessun percorso assoluto o personale esposto (etichette opache e ambiti simbolici per i database).
   - Nessun messaggio di eccezione SQLite grezzo emesso nell'output JSON o testuale.
   - Nessun digest crittografico SHA-256 completo a 64 caratteri nell'output JSON o testuale.
   - CONFRONTO DEI METADATI ORIGINALI per la compatibilità prima dell'applicazione della sanificazione.
3. ROBUSTEZZA DELLO SCHEMA E QUERY STATICHE:
   - Verifica preliminare statica della presenza di TUTTE le colonne necessarie prima delle interrogazioni.
   - Nomi di tabella esclusivamente statici e noti: checkpoints, checkpoint_chunks, checkpoint_chunk_diagnostics.
   - Gestione di tabelle incomplete o prive di colonne richieste restituendo SCHEMA_MISMATCH senza crash.
   - L'errore di schema su un database non interrompe l'analisi degli altri database candidati.
4. RIGORE FORENSE E ALLINEAMENTO AL MODELLO DI DOMINIO:
   - Allineato a TopicDiscoveryResult: lista 'topics' vuota è ammissibile (0 topic scoperti).
   - Vincolo su short_description: deve essere stringa non vuota per ciascun DiscoveredTopic.
   - Gestione totale di payload corrotti: nessun crash con KeyError('evidence_validation_notice').
   - Dichiarazione esplicita: EVIDENCE_VALIDATION_NOT_PERFORMED (validazione probatoria non eseguita).
   - Rilevazione di METADATA_DISCORDANCE_ANOMALY in caso di disallineamento descrittivo a parità di fingerprint.
   - Nessun merge automatico o riconciliazione a freddo tra config_fingerprint differenti.
   - Descrizione neutrale dello stato osservato dei checkpoint (nessuna etichetta automatica 'Run 1'/'Run 2').
5. ANALISI MULTI-DATABASE:
   - Identifica e analizza separatamente TUTTI i database SQLite candidati trovati.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

# Avviso standard di validazione probatoria
DEFAULT_EVIDENCE_VALIDATION_NOTICE = (
    "Evidence citations cannot be verified against forensic source text without "
    "loading the active ConversationEvidenceDocument in memory."
)

# Colonne minime richieste per ciascuna tabella dello schema ChatAnalysis
REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "checkpoints": {
        "document_fingerprint",
        "config_fingerprint",
        "document_id",
        "analysis_type",
        "model_id",
        "prompt_version",
        "total_planned_chunks",
        "created_at",
        "updated_at",
    },
    "checkpoint_chunks": {
        "document_fingerprint",
        "config_fingerprint",
        "chunk_index",
        "serialized_result",
        "created_at",
    },
    "checkpoint_chunk_diagnostics": {
        "config_fingerprint",
        "chunk_index",
        "status",
        "attempts",
        "http_status",
        "finish_reason",
        "diagnostic_category",
        "failure_phase",
        "error_detail",
        "json_valid",
        "schema_valid",
        "evidence_valid",
        "checkpoint_saved",
        "updated_at",
    },
}

# Allowlist chiusa dei modelli ammessi nello schema ChatAnalysis
ALLOWED_MODEL_IDS: set[str] = {
    "qwen2.5-7b-instruct",
    "qwen2.5-14b-instruct",
    "qwen2.5-32b-instruct",
    "qwen2.5-72b-instruct",
    "llama-3.1-8b-instruct",
    "llama-3.2-3b-instruct",
    "llama-3.2-1b-instruct",
    "deepseek-r1-distill-qwen-7b",
    "deepseek-r1-distill-llama-8b",
    "deepseek-r1-distill-qwen-14b",
    "deepseek-r1-distill-qwen-32b",
    "mistral-7b-instruct",
    "phi-3-mini",
    "gemma-2-9b",
}

# Allowlist chiusa delle versioni prompt ammesse
ALLOWED_PROMPT_VERSIONS: set[str] = {
    "discovery_operational",
    "detection_operational",
    "translation_operational",
    "discovery_default",
    "detection_default",
    "operational",
    "v1",
    "v2",
}

# Allowlist chiusa dei tipi di analisi
ALLOWED_ANALYSIS_TYPES: set[str] = {"discovery", "detection", "translation"}

# Allowlist chiusa delle categorie diagnostiche
ALLOWED_DIAGNOSTIC_CATEGORIES: set[str] = {
    "HTTP_CONTEXT_OVERFLOW",
    "HTTP_INVALID_PARAMETERS",
    "HTTP_SCHEMA_REJECTED",
    "TIMEOUT",
    "JSON_DECODE_ERROR",
    "OUTPUT_TRUNCATED",
    "SCHEMA_VALIDATION_ERROR",
    "INVALID_EVIDENCE_ID",
    "DUPLICATE_EVIDENCE_ID",
    "EMPTY_EVIDENCE",
    "EXCESS_TOPICS",
    "PROVENANCE_MISMATCH",
    "CHECKPOINT_SAVE_ERROR",
    "MODEL_MISMATCH",
    "INTERNAL_VALIDATION_ERROR",
    "UNKNOWN_ERROR",
    "NONE",
}

# Allowlist chiusa delle fasi di fallimento
ALLOWED_FAILURE_PHASES: set[str] = {
    "inference",
    "json_parsing",
    "schema_validation",
    "evidence_validation",
    "checkpoint_save",
    "completed",
    "recovered",
    "none",
}

# Allowlist chiusa dei finish reason
ALLOWED_FINISH_REASONS: set[str] = {
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "null",
    "none",
}


def _sanitize_path(p: Path | str | None, default_tag: str = "PATH_REDACTED") -> str:
    """
    Restituisce esclusivamente etichette opache e sicure senza percorsi personali o esterni.
    Non esporta nomi di cartelle o file (Audit Fix B1).
    Supporta percorsi Windows anche su ambienti Linux.
    Restituisce:
    - DATABASE_REDACTED
    - CHAT_FILE_REDACTED
    - PATH_REDACTED
    """
    if not p:
        return default_tag
    s = str(p).replace("\\", "/").strip()
    if not s:
        return default_tag
    lower_s = s.lower()
    if lower_s.endswith((".db", ".sqlite", ".sqlite3")) or "checkpoint" in lower_s:
        return "DATABASE_REDACTED"
    if lower_s.endswith((".txt", ".csv", ".json", ".xml")) or "chat" in lower_s:
        return "CHAT_FILE_REDACTED"
    return default_tag


def _sanitize_doc_id(doc_id: str | None) -> str:
    """
    Restituisce un'etichetta opaca sicura per il document ID, priva di prefissi,
    suffissi o parole della stringa originale (zero leak investigativo).
    """
    if not doc_id:
        return "DOC_ID_ABSENT"
    clean = str(doc_id).strip()
    token = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
    return f"doc_***[token={token},len={len(clean)}]"


def _abbreviate_fp(fp: Any, length: int = 12) -> str:
    """
    Abbrevia rigorosamente un digest crittografico SHA-256 a un prefisso sicuro con ellissi.
    Accetta come fingerprint mostrabile soltanto una stringa esadecimale SHA-256 valida di 64 caratteri.
    Per ogni altro valore (non valido, non esadecimale o di lunghezza errata)
    restituisce 'FINGERPRINT_INVALID_OPAQUE'.
    """
    if not fp or not isinstance(fp, str):
        return "FINGERPRINT_INVALID_OPAQUE"
    clean = fp.strip()
    if not re.fullmatch(r"^[0-9a-fA-F]{64}$", clean):
        return "FINGERPRINT_INVALID_OPAQUE"
    if len(clean) <= length:
        return clean
    return f"{clean[:length]}..."


def _sanitize_model_id(m_id: Any) -> str:
    """Consente solo identificativi di modello inclusi in una allowlist chiusa verificata."""
    if not m_id:
        return "MODEL_ABSENT"
    val = str(m_id).strip().lower()
    if val in ALLOWED_MODEL_IDS:
        return val
    return "MODEL_OPAQUE"


def _sanitize_prompt_version(p_ver: Any) -> str:
    """Consente solo versioni di prompt incluse in una allowlist chiusa verificata."""
    if not p_ver:
        return "PROMPT_VERSION_ABSENT"
    val = str(p_ver).strip().lower()
    if val in ALLOWED_PROMPT_VERSIONS:
        return val
    return "PROMPT_VERSION_OPAQUE"


def _sanitize_analysis_type(a_type: Any) -> str:
    """Consente solo tipi di analisi inclusi in una allowlist chiusa verificata."""
    val = str(a_type).strip().lower() if a_type else ""
    if val in ALLOWED_ANALYSIS_TYPES:
        return val
    return "ANALYSIS_TYPE_OPAQUE"


def _sanitize_timestamp(ts: Any) -> str:
    """
    Valida rigorosamente che la stringa sia un timestamp ISO 8601 standard;
    in caso contrario restituisce TIMESTAMP_OPAQUE (nessuna informazione o marcatore arbitrario emesso).
    """
    if not ts:
        return "TIMESTAMP_ABSENT"
    val = str(ts).strip()
    iso_pattern = r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?$"
    if not re.fullmatch(iso_pattern, val):
        return "TIMESTAMP_OPAQUE"
    try:
        clean_val = val.replace(" ", "T")
        if clean_val.endswith("Z"):
            clean_val = clean_val[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean_val)
        return dt.isoformat()
    except Exception:
        return "TIMESTAMP_OPAQUE"


def _sanitize_diagnostic_category(cat: Any) -> str:
    """Valida la categoria diagnostica rispetto all'allowlist chiusa."""
    val = str(cat).strip().upper() if cat else "NONE"
    if val in ALLOWED_DIAGNOSTIC_CATEGORIES:
        return val
    return "DIAGNOSTIC_CATEGORY_OPAQUE"


def _sanitize_failure_phase(phase: Any) -> str | None:
    """Valida la fase di fallimento rispetto all'allowlist chiusa."""
    if not phase:
        return None
    val = str(phase).strip().lower()
    if val in ALLOWED_FAILURE_PHASES:
        return val
    return "failure_phase_opaque"


def _sanitize_finish_reason(reason: Any) -> str | None:
    """Valida il finish_reason rispetto all'allowlist chiusa."""
    if not reason:
        return None
    val = str(reason).strip().lower()
    if val in ALLOWED_FINISH_REASONS:
        return val
    return "finish_reason_opaque"


def _safe_int_range(
    val: Any,
    min_val: int = 0,
    max_val: int = 100_000,
    fallback: Any = "NUMERIC_VALUE_MALFORMED",
) -> Any:
    """
    Valida rigorosamente il tipo e l'intervallo di un valore numerico o contatore letto da SQLite.
    Se il valore è non numerico, negativo (quando min_val >= 0), fuori intervallo
    o irragionevolmente grande (> max_val), restituisce `fallback` (default: 'NUMERIC_VALUE_MALFORMED')
    senza esporre il valore originale grezzo.
    """
    if val is None or isinstance(val, bool):
        return fallback
    try:
        if isinstance(val, str):
            clean = val.strip()
            if not re.fullmatch(r"^-?\d+$", clean):
                return fallback
            int_val = int(clean)
        elif isinstance(val, (int, float)):
            if isinstance(val, float) and not val.is_integer():
                return fallback
            int_val = int(val)
        else:
            return fallback

        if min_val <= int_val <= max_val:
            return int_val
        return fallback
    except Exception:
        return fallback


def _safe_bool(val: Any) -> bool:
    """Valida un valore booleano letto da SQLite senza esporre testo grezzo."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "t", "yes")
    return False


def _verify_chunk_payload_structure(
    serialized_raw: str,
    expected_doc_id: str,
    chunk_index: int,
) -> dict[str, Any]:
    """
    Verifica approfondita dell'integrità strutturale e di provenance di un chunk serializzato.
    TUTTI i percorsi di ritorno restituiscono la medesima struttura completa di campi,
    incluso 'evidence_validation_notice'.
    Allineata al modello di dominio TopicDiscoveryResult:
    - Lista 'topics' vuota è ammessa (0 topic rilevati).
    - Per ciascun topic, 'label' e 'short_description' devono essere stringhe non vuote.
    - 'evidence_ids' deve essere lista non vuota di stringhe non vuote senza duplicati.
    """
    base_eval: dict[str, Any] = {
        "structural_integrity": "INVALID_EMPTY_PAYLOAD",
        "is_valid_json": False,
        "provenance_status": "PAYLOAD_EMPTY",
        "schema_status": "PAYLOAD_EMPTY",
        "topic_count": 0,
        "total_evidence_references": 0,
        "duplicate_evidence_ids_found": False,
        "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
        "evidence_validation_notice": DEFAULT_EVIDENCE_VALIDATION_NOTICE,
    }

    if not serialized_raw or not isinstance(serialized_raw, str) or not serialized_raw.strip():
        return base_eval

    try:
        data = json.loads(serialized_raw)
    except Exception:
        base_eval.update({
            "structural_integrity": "INVALID_JSON_SYNTAX",
            "is_valid_json": False,
            "provenance_status": "JSON_PARSE_ERROR",
            "schema_status": "JSON_PARSE_ERROR",
        })
        return base_eval

    if not isinstance(data, dict):
        base_eval.update({
            "structural_integrity": "INVALID_ROOT_NOT_DICT",
            "is_valid_json": True,
            "provenance_status": "SCHEMA_VIOLATION",
            "schema_status": "SCHEMA_VIOLATION",
        })
        return base_eval

    # 1. Verifica Invariante di Provenance
    prov_doc_id = data.get("provenance_document_id")
    expected_chunk_id = f"{expected_doc_id}::chunk::{chunk_index}"
    if not prov_doc_id:
        provenance_status = "MISSING_PROVENANCE_ID"
    elif prov_doc_id in (expected_doc_id, expected_chunk_id):
        provenance_status = "VALID_MATCHING_PARENT_OR_CHUNK"
    else:
        provenance_status = "PROVENANCE_MISMATCH_ALIEN_DOCUMENT"

    # 2. Verifica Schema 'topics' (allineato a TopicDiscoveryResult)
    topics = data.get("topics")
    if not isinstance(topics, list):
        base_eval.update({
            "structural_integrity": "COMPROMISED_INVALID_SCHEMA",
            "is_valid_json": True,
            "provenance_status": provenance_status,
            "schema_status": "INVALID_TOPICS_NOT_LIST",
        })
        return base_eval

    # G2: TopicDiscoveryResult ammette lista 'topics' vuota
    if len(topics) == 0:
        if provenance_status != "VALID_MATCHING_PARENT_OR_CHUNK":
            structural_integrity = "COMPROMISED_ALIEN_PROVENANCE"
        else:
            structural_integrity = "STRUCTURALLY_INTACT"

        base_eval.update({
            "structural_integrity": structural_integrity,
            "is_valid_json": True,
            "provenance_status": provenance_status,
            "schema_status": "CONFORMANT_EMPTY_TOPICS_LIST",
            "topic_count": 0,
            "total_evidence_references": 0,
            "duplicate_evidence_ids_found": False,
        })
        return base_eval

    schema_valid = True
    total_eids = 0
    has_duplicates = False

    for t in topics:
        if not isinstance(t, dict):
            schema_valid = False
            break
        label = t.get("label")
        short_desc = t.get("short_description")
        eids = t.get("evidence_ids")

        # label deve essere stringa non vuota
        if not isinstance(label, str) or not label.strip():
            schema_valid = False
            break
        # short_description deve essere stringa non vuota (modello reale DiscoveredTopic)
        if not isinstance(short_desc, str) or not short_desc.strip():
            schema_valid = False
            break
        # evidence_ids deve essere sequenza non vuota di stringhe non vuote
        if not isinstance(eids, list) or len(eids) == 0:
            schema_valid = False
            break
        if any(not isinstance(e, str) or not e.strip() for e in eids):
            schema_valid = False
            break
        if len(eids) != len(set(eids)):
            has_duplicates = True
        total_eids += len(eids)

    if not schema_valid:
        schema_status = "INVALID_TOPIC_FIELDS_OR_TYPES"
        structural_integrity = "COMPROMISED_INVALID_SCHEMA"
    elif has_duplicates:
        schema_status = "VALID_SCHEMA_WITH_DUPLICATE_EVIDENCE_IDS"
        structural_integrity = "COMPROMISED_DUPLICATE_CITATIONS"
    elif provenance_status != "VALID_MATCHING_PARENT_OR_CHUNK":
        schema_status = "STRUCTURALLY_CONFORMANT"
        structural_integrity = "COMPROMISED_ALIEN_PROVENANCE"
    else:
        schema_status = "STRUCTURALLY_CONFORMANT"
        structural_integrity = "STRUCTURALLY_INTACT"

    return {
        "structural_integrity": structural_integrity,
        "is_valid_json": True,
        "provenance_status": provenance_status,
        "schema_status": schema_status,
        "topic_count": len(topics),
        "total_evidence_references": total_eids,
        "duplicate_evidence_ids_found": has_duplicates,
        "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
        "evidence_validation_notice": DEFAULT_EVIDENCE_VALIDATION_NOTICE,
    }


def _strip_and_sanitize_recursively(obj: Any) -> Any:
    """
    Rimuove ricorsivamente chiavi non consentite, maschera percorsi personali
    e abbrevia hash completi a 64 caratteri.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if "_full" in k:
                continue
            cleaned[k] = _strip_and_sanitize_recursively(v)
        return cleaned
    elif isinstance(obj, list):
        return [_strip_and_sanitize_recursively(item) for item in obj]
    elif isinstance(obj, str):
        # Troncamento digest SHA-256 completi
        if re.fullmatch(r"[0-9a-fA-F]{64}", obj):
            return obj[:12] + "..."
        # Mascheramento percorsi assoluti Windows o Linux
        if re.search(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|var|tmp|opt|etc|root)[\\/])", obj):
            return re.sub(
                r"(?:[A-Za-z]:[\\/]|/(?:home|Users|var|tmp|opt|etc|root)[\\/])[^\s\"',]+",
                "<PATH_REDACTED>",
                obj,
            )
        return obj
    return obj


def _get_table_columns_statically(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Interroga staticamente le colonne di una specifica tabella nota dello schema."""
    if table_name == "checkpoints":
        rows = conn.execute("PRAGMA table_info(checkpoints)").fetchall()
    elif table_name == "checkpoint_chunks":
        rows = conn.execute("PRAGMA table_info(checkpoint_chunks)").fetchall()
    elif table_name == "checkpoint_chunk_diagnostics":
        rows = conn.execute("PRAGMA table_info(checkpoint_chunk_diagnostics)").fetchall()
    else:
        return set()
    return {str(r[1]).lower() for r in rows if len(r) > 1}


def _inspect_known_tables_statically(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Interroga staticamente SOLO le tabelle note dello schema ChatAnalysis
    verificando la conformità delle colonne richieste prima di ogni operazione.
    """
    tables_present: list[str] = []
    row_counts: dict[str, int] = {}
    schema_conformant: dict[str, bool] = {}
    tables_existing: list[str] = []

    for tbl in ("checkpoints", "checkpoint_chunks", "checkpoint_chunk_diagnostics"):
        try:
            cols = _get_table_columns_statically(conn, tbl)
            if not cols:
                schema_conformant[tbl] = False
                continue

            tables_existing.append(tbl)
            req = REQUIRED_TABLE_COLUMNS[tbl]
            if not req.issubset(cols):
                # La tabella esiste ma ha colonne mancanti (schema incompleto)
                schema_conformant[tbl] = False
                continue

            # Tabella presente e con colonne complete
            if tbl == "checkpoints":
                c = conn.execute("SELECT count(*) FROM checkpoints").fetchone()
            elif tbl == "checkpoint_chunks":
                c = conn.execute("SELECT count(*) FROM checkpoint_chunks").fetchone()
            elif tbl == "checkpoint_chunk_diagnostics":
                c = conn.execute("SELECT count(*) FROM checkpoint_chunk_diagnostics").fetchone()
            else:
                c = None

            if c is not None:
                tables_present.append(tbl)
                row_counts[tbl] = _safe_int_range(c[0], min_val=0, max_val=100_000_000, fallback=0)
                schema_conformant[tbl] = True
            else:
                schema_conformant[tbl] = False
        except sqlite3.OperationalError:
            schema_conformant[tbl] = False

    return {
        "tables_present": tables_present,
        "row_counts": row_counts,
        "schema_conformant": schema_conformant,
        "tables_existing": tables_existing,
    }


class SafeCheckpointAuditor:
    """Esegue l'audit diagnostico di sola lettura sulle sessioni di checkpoint."""

    def __init__(self, target_db_path: Path | str | None = None) -> None:
        self.explicit_path = Path(target_db_path) if target_db_path else None
        self.candidate_paths_with_scope = self._discover_candidate_locations()
        self.candidate_paths = [p for _, p in self.candidate_paths_with_scope]

    def _discover_candidate_locations(self) -> list[tuple[str, Path]]:
        """Individua tutti i potenziali percorsi del database associati a un ambito opaco."""
        candidates: list[tuple[str, Path]] = []
        if self.explicit_path:
            candidates.append(("EXPLICIT_PARAMETER", self.explicit_path.resolve()))

        cwd = Path.cwd().resolve()
        candidates.append(("WORKSPACE_LOCAL", (cwd / ".checkpoints" / "checkpoints.db").resolve()))
        candidates.append(("WORKSPACE_ROOT", (cwd / "checkpoints.db").resolve()))

        script_root = Path(__file__).resolve().parent.parent
        candidates.append(("REPOSITORY_ROOT", (script_root / ".checkpoints" / "checkpoints.db").resolve()))
        candidates.append(("USER_HOME", (Path.home().resolve() / ".checkpoints" / "checkpoints.db").resolve()))

        # Rimuove duplicati preservando l'ordine
        seen: set[str] = set()
        unique: list[tuple[str, Path]] = []
        for scope, c in candidates:
            resolved_str = str(c)
            if resolved_str not in seen:
                seen.add(resolved_str)
                unique.append((scope, c))
        return unique

    def audit_environment_paths(self) -> dict[str, Any]:
        """Ispezione opaca delle posizioni candidate filesystem."""
        path_report = []
        for idx, (scope, p) in enumerate(self.candidate_paths_with_scope, start=1):
            exists = p.is_file()
            size = p.stat().st_size if exists else 0
            path_report.append({
                "database_ref": f"db_candidate_{idx}",
                "location_scope": scope,
                "exists": exists,
                "size_bytes": size,
            })
        return {
            "candidate_locations_inspected": path_report,
            "paths_checked": path_report,  # retrocompatibilità
        }

    def audit_single_database(
        self,
        db_path: Path,
        db_ref: str = "database_1",
        location_scope: str = "UNKNOWN",
    ) -> dict[str, Any]:
        """Esegue l'audit atomico di sola lettura su uno specifico file database SQLite."""
        if not db_path.is_file():
            return {
                "database_ref": db_ref,
                "location_scope": location_scope,
                "status": "ERROR_FILE_NOT_FOUND",
                "error_code": "FILE_NOT_FOUND",
                "safety_notice": "Il database non esiste sul filesystem.",
            }

        # Apertura TASSATIVAMENTE con URI mode=ro (Read-Only)
        target_posix = db_path.resolve().as_posix()
        db_uri = f"file:{target_posix}?mode=ro"
        try:
            conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
            conn.execute("PRAGMA query_only = ON;")
        except sqlite3.OperationalError:
            return {
                "database_ref": db_ref,
                "location_scope": location_scope,
                "status": "ERROR_READ_ONLY_ACCESS_FAILED",
                "error_code": "SQLITE_READ_ONLY_MODE_FAILED",
                "safety_notice": "Audit interrotto per questo database: nessun fallback read-write consentito.",
            }
        except Exception:
            return {
                "database_ref": db_ref,
                "location_scope": location_scope,
                "status": "ERROR_READ_ONLY_ACCESS_FAILED",
                "error_code": "SQLITE_CONNECTION_FAILED",
                "safety_notice": "Audit interrotto per questo database: nessun fallback read-write consentito.",
            }

        try:
            cur = conn.cursor()

            # 1. Ispezione statica delle sole tabelle note e verifica preventiva colonne
            tables_info = _inspect_known_tables_statically(conn)
            tables = tables_info["tables_present"]
            row_counts = tables_info["row_counts"]
            schema_conformant = tables_info["schema_conformant"]
            tables_existing = tables_info["tables_existing"]

            # Controllo conformità schema tabella checkpoints
            if not schema_conformant.get("checkpoints", False):
                if "checkpoints" in tables_existing:
                    return {
                        "database_ref": db_ref,
                        "location_scope": location_scope,
                        "status": "ERROR_SCHEMA_MISMATCH",
                        "error_code": "SCHEMA_MISMATCH",
                        "safety_notice": "La tabella checkpoints non possiede le colonne richieste dallo schema ChatAnalysis.",
                    }
                else:
                    return {
                        "database_ref": db_ref,
                        "location_scope": location_scope,
                        "status": "ERROR_MISSING_CHECKPOINTS_TABLE",
                        "error_code": "MISSING_CHECKPOINTS_TABLE",
                        "safety_notice": "La tabella checkpoints non esiste nel database.",
                    }

            # 2. Censimento testate (checkpoints)
            try:
                headers_raw = cur.execute("""
                    SELECT document_fingerprint, config_fingerprint, document_id,
                           analysis_type, model_id, prompt_version, total_planned_chunks,
                           created_at, updated_at
                    FROM checkpoints
                    ORDER BY updated_at ASC
                """).fetchall()
            except sqlite3.OperationalError:
                return {
                    "database_ref": db_ref,
                    "location_scope": location_scope,
                    "status": "ERROR_SCHEMA_MISMATCH",
                    "error_code": "SCHEMA_MISMATCH",
                    "safety_notice": "Interrogazione della tabella checkpoints fallita per schema non conforme.",
                }

            sessions: list[dict[str, Any]] = []
            internal_sessions: list[dict[str, Any]] = []
            for h in headers_raw:
                doc_fp, cfg_fp, doc_id, a_type, m_id, p_ver, t_chunks, c_at, u_at = h
                
                safe_total_planned = _safe_int_range(
                    t_chunks, min_val=1, max_val=100_000, fallback="NUMERIC_VALUE_MALFORMED"
                )

                # REQUISITO CRITICO: Memorizza i metadati ORIGINALI per il confronto di compatibilità esatto
                internal_sessions.append({
                    "doc_fp": str(doc_fp),
                    "cfg_fp": str(cfg_fp),
                    "doc_id": str(doc_id),
                    "total_planned": safe_total_planned,
                    "raw_model_id": str(m_id),
                    "raw_prompt_version": str(p_ver),
                    "raw_analysis_type": str(a_type),
                })
                
                # Rappresentazione sanificata per l'output sicuro (zero leak)
                sessions.append({
                    "document_fingerprint": _abbreviate_fp(doc_fp),
                    "config_fingerprint": _abbreviate_fp(cfg_fp),
                    "document_id": _sanitize_doc_id(doc_id),
                    "analysis_type": _sanitize_analysis_type(a_type),
                    "model_id": _sanitize_model_id(m_id),
                    "prompt_version": _sanitize_prompt_version(p_ver),
                    "total_planned_chunks": safe_total_planned,
                    "created_at": _sanitize_timestamp(c_at),
                    "updated_at": _sanitize_timestamp(u_at),
                })

            # 3. Conteggio chunk validati per coppia (doc_fp, cfg_fp)
            chunks_summary = []
            has_chunks_table = schema_conformant.get("checkpoint_chunks", False)
            for s_idx, s in enumerate(internal_sessions):
                doc_fp_val = s["doc_fp"]
                cfg_fp_val = s["cfg_fp"]
                if has_chunks_table:
                    try:
                        c_rows = cur.execute("""
                            SELECT chunk_index, serialized_result
                            FROM checkpoint_chunks
                            WHERE document_fingerprint = ? AND config_fingerprint = ?
                            ORDER BY chunk_index ASC
                        """, (doc_fp_val, cfg_fp_val)).fetchall()
                    except sqlite3.OperationalError:
                        c_rows = []
                else:
                    c_rows = []

                completed_indices = []
                structurally_intact_count = 0
                compromised_chunks_count = 0

                for r in c_rows:
                    c_num = _safe_int_range(r[0], min_val=0, max_val=100_000, fallback="NUMERIC_VALUE_MALFORMED")
                    if isinstance(c_num, int):
                        completed_indices.append(c_num)
                        eval_c_num = c_num
                    else:
                        eval_c_num = 0

                    c_payload = str(r[1])
                    try:
                        chk_eval = _verify_chunk_payload_structure(c_payload, s["doc_id"], eval_c_num)
                    except Exception:
                        chk_eval = {
                            "structural_integrity": "CORRUPTED_PAYLOAD_EXCEPTION",
                            "is_valid_json": False,
                            "provenance_status": "EVALUATION_ERROR",
                            "schema_status": "EVALUATION_ERROR",
                            "topic_count": 0,
                            "total_evidence_references": 0,
                            "duplicate_evidence_ids_found": False,
                            "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
                            "evidence_validation_notice": DEFAULT_EVIDENCE_VALIDATION_NOTICE,
                        }

                    if not isinstance(c_num, int):
                        compromised_chunks_count += 1
                    elif chk_eval["structural_integrity"] == "STRUCTURALLY_INTACT":
                        structurally_intact_count += 1
                    else:
                        compromised_chunks_count += 1

                total_planned = s["total_planned"]
                if isinstance(total_planned, int):
                    missing_indices = [i for i in range(total_planned) if i not in completed_indices]
                    if len(completed_indices) == 20 and total_planned == 26:
                        observed_desc = "OBSERVED_20_OF_26_CHUNKS"
                    elif len(completed_indices) == 24 and total_planned == 26:
                        observed_desc = "OBSERVED_24_OF_26_CHUNKS"
                    else:
                        observed_desc = f"OBSERVED_{len(completed_indices)}_OF_{total_planned}_CHUNKS"
                    is_20 = (len(completed_indices) == 20 and total_planned == 26)
                    is_24 = (len(completed_indices) == 24 and total_planned == 26)
                else:
                    missing_indices = []
                    observed_desc = f"OBSERVED_{len(completed_indices)}_OF_MALFORMED_PLANNED_CHUNKS"
                    is_20 = False
                    is_24 = False

                chunks_summary.append({
                    "config_fingerprint": _abbreviate_fp(cfg_fp_val),
                    "physical_chunks_count": len(completed_indices),
                    "completed_chunks_count": len(completed_indices),
                    "structurally_intact_chunks_count": structurally_intact_count,
                    "compromised_chunks_count": compromised_chunks_count,
                    "total_planned_chunks": total_planned,
                    "completed_indices": completed_indices,
                    "missing_indices": missing_indices,
                    "observed_state": observed_desc,
                    "is_session_20_of_26": is_20,
                    "is_session_24_of_26": is_24,
                    "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
                })

            # 4. Verifica approfondita del Chunk 10
            if has_chunks_table:
                try:
                    c10_rows = cur.execute("""
                        SELECT document_fingerprint, config_fingerprint, chunk_index,
                               serialized_result, created_at
                        FROM checkpoint_chunks
                        WHERE chunk_index = 10
                    """).fetchall()
                except sqlite3.OperationalError:
                    c10_rows = []
            else:
                c10_rows = []

            chunk_10_records = []
            for r in c10_rows:
                doc_fp_10, cfg_fp_10, c_idx, s_res, c_at = r
                matching_header = next(
                    (s for s in internal_sessions if s["doc_fp"] == doc_fp_10 and s["cfg_fp"] == cfg_fp_10),
                    None,
                )
                expected_doc = matching_header["doc_id"] if matching_header else "unknown_doc"

                try:
                    c10_eval = _verify_chunk_payload_structure(str(s_res), expected_doc, 10)
                except Exception:
                    c10_eval = {
                        "structural_integrity": "CORRUPTED_PAYLOAD_EXCEPTION",
                        "is_valid_json": False,
                        "provenance_status": "EVALUATION_ERROR",
                        "schema_status": "EVALUATION_ERROR",
                        "topic_count": 0,
                        "total_evidence_references": 0,
                        "duplicate_evidence_ids_found": False,
                        "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
                        "evidence_validation_notice": DEFAULT_EVIDENCE_VALIDATION_NOTICE,
                    }

                chunk_10_records.append({
                    "document_fingerprint": _abbreviate_fp(doc_fp_10),
                    "config_fingerprint": _abbreviate_fp(cfg_fp_10),
                    "chunk_index": 10,
                    "physical_presence": True,
                    "structural_integrity": c10_eval["structural_integrity"],
                    "is_valid_json": c10_eval["is_valid_json"],
                    "is_payload_valid_json": c10_eval["is_valid_json"],
                    "provenance_status": c10_eval["provenance_status"],
                    "schema_status": c10_eval["schema_status"],
                    "topic_count": c10_eval["topic_count"],
                    "discovered_topics_count": c10_eval["topic_count"],
                    "total_evidence_references": c10_eval["total_evidence_references"],
                    "duplicate_evidence_ids_found": c10_eval["duplicate_evidence_ids_found"],
                    "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
                    "evidence_validation_notice": c10_eval.get(
                        "evidence_validation_notice",
                        DEFAULT_EVIDENCE_VALIDATION_NOTICE,
                    ),
                    "created_at": _sanitize_timestamp(c_at),
                })

            # 5. Analisi di compatibilità formale (con confronto su metadati ORIGINALI)
            compatibility = self._evaluate_compatibility_rigorous(internal_sessions)

            # 6. Diagnostica dei blocchi falliti
            diagnostics_summary = []
            has_diag_table = schema_conformant.get("checkpoint_chunk_diagnostics", False)
            if has_diag_table:
                try:
                    diag_rows = cur.execute("""
                        SELECT config_fingerprint, chunk_index, status, attempts,
                               http_status, finish_reason, diagnostic_category,
                               failure_phase, error_detail, json_valid, schema_valid,
                               evidence_valid, checkpoint_saved, updated_at
                        FROM checkpoint_chunk_diagnostics
                        WHERE status = 'FAILED' OR chunk_index IN (10, 19)
                        ORDER BY chunk_index ASC, updated_at DESC
                    """).fetchall()

                    for d in diag_rows:
                        (
                            cfg_fp_d, c_idx, st_val, att, http_st, f_reason, diag_cat,
                            f_phase, err_detail, j_val, s_val, e_val, chk_sav, u_at
                        ) = d
                        
                        safe_c_idx = _safe_int_range(
                            c_idx, min_val=0, max_val=100_000, fallback="NUMERIC_VALUE_MALFORMED"
                        )
                        safe_attempts = _safe_int_range(
                            att, min_val=0, max_val=10_000, fallback="NUMERIC_VALUE_MALFORMED"
                        )
                        safe_http_status = (
                            _safe_int_range(http_st, min_val=100, max_val=599, fallback=None)
                            if http_st is not None
                            else None
                        )

                        # Applicazione allowlist chiuse e sanificazione tassativa dei metadati diagnostici
                        diagnostics_summary.append({
                            "config_fingerprint": _abbreviate_fp(cfg_fp_d),
                            "chunk_index": safe_c_idx,
                            "status": (
                                str(st_val)
                                if str(st_val) in ("PENDING", "RUNNING", "SUCCESS", "FAILED", "RECOVERED_FROM_CHECKPOINT")
                                else "STATUS_OPAQUE"
                            ),
                            "attempts": safe_attempts,
                            "http_status": safe_http_status,
                            "finish_reason": _sanitize_finish_reason(f_reason),
                            "diagnostic_category": _sanitize_diagnostic_category(diag_cat),
                            "failure_phase": _sanitize_failure_phase(f_phase),
                            "safe_error_code": _sanitize_diagnostic_category(diag_cat),
                            "json_valid": _safe_bool(j_val),
                            "schema_valid": _safe_bool(s_val),
                            "evidence_valid": _safe_bool(e_val),
                            "checkpoint_saved": _safe_bool(chk_sav),
                            "updated_at": _sanitize_timestamp(u_at),
                        })
                except sqlite3.OperationalError:
                    pass

            # 7. Verdetto motivato e non sovra-dichiarato di recuperabilità
            recovery_verdict = self._evaluate_recovery_rigorous(
                internal_sessions=internal_sessions,
                chunks_summary=chunks_summary,
                chunk_10_records=chunk_10_records,
                compatibility=compatibility,
            )

            return {
                "database_ref": db_ref,
                "location_scope": location_scope,
                "status": "SUCCESS",
                "tables_present": tables,
                "row_counts": row_counts,
                "sessions_found_count": len(sessions),
                "sessions": sessions,
                "chunks_per_session": chunks_summary,
                "chunk_10_status": chunk_10_records,
                "compatibility_analysis": compatibility,
                "diagnostics_summary": diagnostics_summary,
                "recovery_verdict": recovery_verdict,
            }

        finally:
            conn.close()

    def _evaluate_compatibility_rigorous(
        self,
        internal_sessions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Valutazione rigorosa di compatibilità tra sessioni:
        - Confronta i metadati ORIGINALI (raw_model_id, raw_prompt_version, etc.) prima della sanificazione.
        - Rifiuta di dichiarare compatibili sessioni con documenti differenti.
        - Rifiuta ripresa automatica con config_fingerprint differenti.
        - Segnala METADATA_DISCORDANCE_ANOMALY se a parità di hash differiscono metadati descrittivi.
        """
        if len(internal_sessions) < 2:
            return {
                "sessions_comparable": False,
                "reason_code": "INSUFFICIENT_SESSIONS_COUNT",
                "runtime_interchangeable": False,
            }

        s1 = internal_sessions[0]
        s2 = internal_sessions[1]

        same_doc_fp = (s1["doc_fp"] == s2["doc_fp"])
        same_cfg_fp = (s1["cfg_fp"] == s2["cfg_fp"])
        
        # REQUISITO CRITICO: Confronto su metadati ORIGINALI prima di applicare la sanificazione
        same_model = (s1["raw_model_id"] == s2["raw_model_id"])
        same_prompt = (s1["raw_prompt_version"] == s2["raw_prompt_version"])
        same_analysis = (s1["raw_analysis_type"] == s2["raw_analysis_type"])
        same_total_planned = (
            (s1["total_planned"] == s2["total_planned"])
            and isinstance(s1["total_planned"], int)
        )

        if not same_doc_fp:
            return {
                "sessions_comparable": True,
                "same_document_fingerprint": False,
                "different_documents_detected": True,
                "same_config_fingerprint": same_cfg_fp,
                "config_fingerprint_mismatch_detected": not same_cfg_fp,
                "same_model_id": same_model,
                "same_prompt_version": same_prompt,
                "same_analysis_type": same_analysis,
                "same_total_planned_chunks": same_total_planned,
                "logically_compatible_configuration": False,
                "runtime_interchangeable": False,
                "status": "INCOMPATIBLE_DIFFERENT_DOCUMENTS",
                "reason_code": "DIFFERENT_DOCUMENT_FINGERPRINTS",
            }

        if not same_cfg_fp:
            return {
                "sessions_comparable": True,
                "same_document_fingerprint": True,
                "different_documents_detected": False,
                "same_config_fingerprint": False,
                "config_fingerprint_mismatch_detected": True,
                "same_model_id": same_model,
                "same_prompt_version": same_prompt,
                "same_analysis_type": same_analysis,
                "same_total_planned_chunks": same_total_planned,
                "logically_compatible_configuration": (
                    same_model and same_prompt and same_analysis and same_total_planned
                ),
                "runtime_interchangeable": False,
                "status": "CONFIG_FINGERPRINT_MISMATCH",
                "reason_code": "CONFIG_FINGERPRINT_DISCORDANT",
            }

        # REQUISITO: A parità di fingerprint, se i metadati originali differiscono è un'anomalia
        if not (same_model and same_prompt and same_analysis and same_total_planned):
            return {
                "sessions_comparable": True,
                "same_document_fingerprint": True,
                "different_documents_detected": False,
                "same_config_fingerprint": True,
                "config_fingerprint_mismatch_detected": False,
                "same_model_id": same_model,
                "same_prompt_version": same_prompt,
                "same_analysis_type": same_analysis,
                "same_total_planned_chunks": same_total_planned,
                "logically_compatible_configuration": False,
                "runtime_interchangeable": False,
                "status": "METADATA_DISCORDANCE_ANOMALY",
                "reason_code": "METADATA_DISCORDANT_DESPITE_SAME_CONFIG_FP",
            }

        return {
            "sessions_comparable": True,
            "same_document_fingerprint": True,
            "different_documents_detected": False,
            "same_config_fingerprint": True,
            "config_fingerprint_mismatch_detected": False,
            "same_model_id": True,
            "same_prompt_version": True,
            "same_analysis_type": True,
            "same_total_planned_chunks": True,
            "logically_compatible_configuration": True,
            "runtime_interchangeable": True,
            "status": "FULLY_COMPATIBLE_IDENTICAL_CONFIGURATION",
            "reason_code": "ALL_METADATA_AND_FINGERPRINTS_MATCH",
        }

    def _evaluate_recovery_rigorous(
        self,
        internal_sessions: list[dict[str, Any]],
        chunks_summary: list[dict[str, Any]],
        chunk_10_records: list[dict[str, Any]],
        compatibility: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Valutazione rigorosa di recuperabilità:
        - Non dichiara mai verificato il recupero delle evidenze senza la validazione del runtime applicativo.
        - Non propone merge automatici tra config differenti.
        - Descrive neutrale lo stato osservato.
        """
        s24 = next((c for c in chunks_summary if c["is_session_24_of_26"]), None)
        s20 = next((c for c in chunks_summary if c["is_session_20_of_26"]), None)

        verdict: dict[str, Any] = {
            "safe_recovery_verified": False,
            "safe_recovery_possible": False,
            "session_24_of_26_present": s24 is not None,
            "session_20_of_26_present": s20 is not None,
            "status": "SAFE_RECOVERY_UNVERIFIED_PENDING_EVIDENCE_CHECK",
            "runtime_resumption_feasible_for_matching_config": False,
            "missing_chunks_to_process": [],
            "chunks_to_process": [],
            "recommended_strategy": (
                "RETRY_ONLY_MISSING_10_AND_19"
                if (s24 is not None and set(s24["missing_indices"]) == {10, 19})
                else "INVESTIGATE_MANUALLY"
            ),
            "chunk_10_reconcilable_from_prior_session": False,
            "evidence_validation": "EVIDENCE_VALIDATION_NOT_PERFORMED",
            "reasons": [],
        }

        if s24 is not None:
            missing = s24["missing_indices"]
            verdict["missing_chunks_to_process"] = missing
            verdict["chunks_to_process"] = missing
            if set(missing) == {10, 19}:
                if s24["structurally_intact_chunks_count"] == 24:
                    verdict["runtime_resumption_feasible_for_matching_config"] = True
                    verdict["status"] = "STRUCTURAL_RESUMPTION_FEASIBLE_MISSING_10_AND_19"
                    verdict["reasons"].append(
                        "Rilevati 24 chunk fisicamente presenti e strutturalmente conformi; "
                        "indici non completati: [10, 19]."
                    )
                    verdict["reasons"].append(
                        "Dichiarazione rigorosa: SAFE_RECOVERY_VERIFIED non può essere certificato in questa sede "
                        "in quanto la validazione probatoria delle citazioni richiede il runtime applicativo attivo."
                    )
                else:
                    verdict["status"] = "CORRUPTED_CHUNKS_DETECTED_IN_CHECKPOINTS"
                    verdict["reasons"].append(
                        f"Rilevati {s24['compromised_chunks_count']} chunk strutturalmente non conformi."
                    )
            else:
                verdict["status"] = "UNEXPECTED_MISSING_CHUNKS"
                verdict["reasons"].append(f"Indici mancanti imprevisti: {missing}.")

        elif s20 is not None:
            missing = s20["missing_indices"]
            verdict["missing_chunks_to_process"] = missing
            verdict["chunks_to_process"] = missing
            verdict["runtime_resumption_feasible_for_matching_config"] = (
                s20["structurally_intact_chunks_count"] == 20
            )
            verdict["status"] = "ONLY_PARTIAL_20_CHUNKS_AVAILABLE"
            verdict["reasons"].append("Rilevata testata con 20 chunk validati su 26.")

        else:
            verdict["status"] = "NO_STANDARD_SESSION_FOUND"
            verdict["reasons"].append("Nessuna testata corrispondente a 20 o 24 chunk rilevata.")

        # Valutazione Chunk 10 da sessione differente
        if len(chunk_10_records) > 0:
            c10_item = chunk_10_records[0]
            if c10_item["structural_integrity"] == "STRUCTURALLY_INTACT":
                verdict["reasons"].append(
                    "Il Chunk 10 esiste fisicamente ed è strutturalmente conforme nel database, "
                    "ma appartiene a un config_fingerprint differente: nessun merge o riuso automatico "
                    "è consentito senza previa rigenerazione o conciliazione formale."
                )

        return verdict

    def run_full_audit(self, specific_db: Path | str | None = None) -> dict[str, Any]:
        """Esegue l'audit completo sanificato su uno specifico database (retrocompatibilità)."""
        target = (
            Path(specific_db)
            if specific_db
            else (self.explicit_path or (self.candidate_paths[0] if self.candidate_paths else None))
        )
        if not target or not target.is_file():
            res = {
                "database_ref": "target_database",
                "location_scope": "EXPLICIT_SPECIFIED" if specific_db else "DEFAULT_SEARCH",
                "status": "ERROR_NO_DATABASE_FOUND",
                "error_code": "DATABASE_NOT_FOUND",
                "environment_paths": self.audit_environment_paths(),
                "recovery_verdict": {
                    "safe_recovery_verified": False,
                    "safe_recovery_possible": False,
                    "status": "NO_DATABASE_FOUND",
                    "reasons": ["Nessun database SQLite trovato."],
                },
            }
            return _strip_and_sanitize_recursively(res)
        single = self.audit_single_database(
            db_path=target,
            db_ref="target_database",
            location_scope="EXPLICIT_SPECIFIED",
        )
        single["environment_paths"] = self.audit_environment_paths()
        return _strip_and_sanitize_recursively(single)

    def run_multi_database_audit(self) -> dict[str, Any]:
        """Identifica e analizza separatamente TUTTI i database candidati trovati."""
        env_paths = self.audit_environment_paths()
        existing_dbs = [(scope, p) for scope, p in self.candidate_paths_with_scope if p.is_file()]

        databases_results: dict[str, Any] = {}
        database_refs: list[str] = []

        for idx, (scope, db) in enumerate(existing_dbs, start=1):
            ref_label = f"database_{idx}"
            database_refs.append(ref_label)
            databases_results[ref_label] = self.audit_single_database(
                db_path=db,
                db_ref=ref_label,
                location_scope=scope,
            )

        total_found = len(existing_dbs)
        multi_summary = {
            "total_databases_found": total_found,
            "database_references": database_refs,
            "different_paths_detected": total_found > 1,
        }

        if total_found == 0:
            primary_status = "ERROR_NO_DATABASE_FOUND"
            primary_verdict = {
                "safe_recovery_verified": False,
                "safe_recovery_possible": False,
                "status": "NO_DATABASE_FOUND",
                "reasons": ["Nessun database SQLite 'checkpoints.db' rilevato nei percorsi esplorati."],
            }
        elif total_found == 1:
            first_db_res = next(iter(databases_results.values()))
            primary_status = first_db_res.get("status", "SUCCESS")
            primary_verdict = first_db_res.get("recovery_verdict", {})
        else:
            primary_status = "MULTI_DATABASE_DETECTED"
            primary_verdict = {
                "safe_recovery_verified": False,
                "safe_recovery_possible": False,
                "status": "MULTI_DATABASE_SEPARATION_OBSERVED",
                "reasons": [
                    f"Rilevati {total_found} database SQLite distinti sul filesystem.",
                    "L'avvio dell'applicazione da directory di lavoro differenti ha generato database isolati.",
                ],
            }

        full_report = {
            "audit_timestamp": datetime.now().isoformat(),
            "overall_status": primary_status,
            "multi_database_summary": multi_summary,
            "environment_paths": env_paths,
            "databases": databases_results,
            "overall_recovery_verdict": primary_verdict,
        }

        return _strip_and_sanitize_recursively(full_report)


def print_formatted_report(audit_result: dict[str, Any]) -> None:
    """Formatta e stampa a console il report diagnostico garantendo privacy e rigore logico."""
    print("=" * 80)
    print("CHATANALYSIS — VERIFICA SICURA DEI CHECKPOINT (AUDIT HARDENED CONCLUSIVO)")
    print("Modalità: SOLA LETTURA (mode=ro) — PRIVACY RIGOROSA — RIGORE FORENSE")
    print("=" * 80)

    print(f"\n[STATO COMPLESSIVO]: {audit_result.get('overall_status')}")

    multi = audit_result.get("multi_database_summary", {})
    print(f"\nDatabase SQLite rilevati: {multi.get('total_databases_found')}")
    for ref in multi.get("database_references", []):
        print(f"  • Riferimento opaco: {ref}")

    databases = audit_result.get("databases", {})
    if not databases:
        print("\n[ATTENZIONE] Nessun database trovato da analizzare.")

    for db_ref, db_data in databases.items():
        print("\n" + "=" * 80)
        print(f"ANALISI DATABASE: {db_ref} (Ambito: {db_data.get('location_scope')})")
        print("=" * 80)

        st = db_data.get("status")
        if st != "SUCCESS":
            print(f"  [ERRORE / STATO]: {db_data.get('error_code', st)}")
            print(f"  [AVVISO]: {db_data.get('safety_notice', '')}")
            continue

        # Tabelle e record (statiche)
        print("\n--- 1. CONSISTENZA TABELLE SCHEMA CHATANALYSIS ---")
        for tbl, cnt in db_data.get("row_counts", {}).items():
            print(f"  • Tabella nota '{tbl}': {cnt} record")

        # Sessioni censite
        sessions = db_data.get("sessions", [])
        print(f"\n--- 2. TESTATE DI CHECKPOINT CENSITE ({len(sessions)}) ---")
        for s_idx, s in enumerate(sessions, start=1):
            print(f"  [Testata #{s_idx}]")
            print(f"    Document ID (opaco)      : {s['document_id']}")
            print(f"    Document Fingerprint     : {s['document_fingerprint']}")
            print(f"    Config Fingerprint       : {s['config_fingerprint']}")
            print(f"    Modello                  : {s['model_id']}")
            print(f"    Prompt Version           : {s['prompt_version']}")
            print(f"    Blocchi pianificati      : {s['total_planned_chunks']}")
            print(f"    Creata / Aggiornata      : {s['created_at']} / {s['updated_at']}")

        # Chunk per sessione
        print("\n--- 3. CHUNK VALIDATI E SALVATI PER CONFIGURAZIONE ---")
        for cs in db_data.get("chunks_per_session", []):
            print(f"  • Config {cs['config_fingerprint']}: {cs['physical_chunks_count']}/{cs['total_planned_chunks']} chunk completati")
            print(f"    Integrità strutturale    : {cs['structurally_intact_chunks_count']} conformi, {cs['compromised_chunks_count']} compromessi")
            print(f"    Indici completati        : {cs['completed_indices']}")
            print(f"    Indici non completati    : {cs['missing_indices']}")
            print(f"    Validazione evidenze     : {cs['evidence_validation']}")
            print(f"    Stato osservato          : {cs.get('observed_state')}")

        # Chunk 10
        print("\n--- 4. STATO VERIFICATO DEL CHUNK 10 ---")
        c10_list = db_data.get("chunk_10_status", [])
        if not c10_list:
            print("  [!] NESSUN record di Chunk 10 presente in checkpoint_chunks in questa configurazione.")
        else:
            for r in c10_list:
                print(f"  • Chunk 10 sotto Config {r['config_fingerprint']}:")
                print(f"    Presenza fisica          : {r['physical_presence']}")
                print(f"    Integrità strutturale    : {r['structural_integrity']}")
                print(f"    Stato Provenance         : {r['provenance_status']}")
                print(f"    Stato Schema             : {r['schema_status']}")
                print(f"    Argomenti scoperti       : {r['topic_count']}")
                print(f"    Citazioni evidenze tot   : {r['total_evidence_references']}")
                print(f"    Citazioni duplicate      : {r['duplicate_evidence_ids_found']}")
                print(f"    Validazione evidenze     : {r['evidence_validation']}")
                print(f"    Data salvataggio         : {r['created_at']}")

        # Compatibilità
        print("\n--- 5. ANALISI DI COMPATIBILITÀ DELLE CONFIGURAZIONI ---")
        comp = db_data.get("compatibility_analysis", {})
        if not comp.get("sessions_comparable"):
            print(f"  Codice esito: {comp.get('reason_code')}")
        else:
            print(f"  • Document Fingerprint identico : {comp.get('same_document_fingerprint')}")
            print(f"  • Modello identico              : {comp.get('same_model_id')}")
            print(f"  • Prompt Version identica       : {comp.get('same_prompt_version')}")
            print(f"  • Config Fingerprint identico   : {comp.get('same_config_fingerprint')}")
            print(f"  • Intercambiabili a runtime     : {comp.get('runtime_interchangeable')}")
            print(f"  • Stato compatibilità           : {comp.get('status')}")
            print(f"  • Codice motivazione            : {comp.get('reason_code')}")

        # Diagnostica fallimenti
        print("\n--- 6. DIAGNOSTICA SANIFICATA DEI BLOCCHI FALLITI ---")
        diags = db_data.get("diagnostics_summary", [])
        if not diags:
            print("  Nessun fallimento registrato nella tabella diagnostica.")
        else:
            for d in diags:
                print(f"  • Blocco {d['chunk_index']} (Config {d['config_fingerprint']}):")
                print(f"    Stato: {d['status']} | Tentativi: {d['attempts']} | HTTP: {d['http_status']} | Finish: {d['finish_reason']}")
                print(f"    Categoria diagnostica : {d['diagnostic_category']}")
                print(f"    Fase del fallimento   : {d['failure_phase']}")
                print(f"    Codice errore sicuro  : {d['safe_error_code']}")
                print(f"    Convalida formale     : JSON={d['json_valid']}, Schema={d['schema_valid']}, Evidenze={d['evidence_valid']}")

        # Verdetto per questo database
        print("\n--- 7. VERDETTO RECUPERO PER QUESTO DATABASE ---")
        rv = db_data.get("recovery_verdict", {})
        print(f"  Safe Recovery Verified : {rv.get('safe_recovery_verified')}")
        print(f"  Stato Verdetto         : {rv.get('status')}")
        print(f"  Ripresa fattibile      : {rv.get('runtime_resumption_feasible_for_matching_config')}")
        print(f"  Blocchi da elaborare   : {rv.get('missing_chunks_to_process')}")
        print(f"  Validazione evidenze   : {rv.get('evidence_validation')}")
        for rz in rv.get("reasons", []):
            print(f"  - {rz}")

    print("\n" + "=" * 80)
    print("VERDETTO COMPLESSIVO E LINEA GUIDA OPERATIVA:")
    ov = audit_result.get("overall_recovery_verdict", {})
    print(f"  Safe Recovery Verified : {ov.get('safe_recovery_verified')}")
    print(f"  Stato Operativo        : {ov.get('status')}")
    for rz in ov.get("reasons", []):
        print(f"  - {rz}")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica sicura dei checkpoint ChatAnalysis (Read-Only Hardened Conclusivo).")
    parser.add_argument("--db", type=str, default=None, help="Percorso opzionale a uno specifico database checkpoints.db.")
    parser.add_argument("--json", action="store_true", help="Emette il risultato integrale in formato JSON sanificato (Zero-PII).")
    args = parser.parse_args()

    auditor = SafeCheckpointAuditor(target_db_path=args.db)
    result = auditor.run_multi_database_audit()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_formatted_report(result)


if __name__ == "__main__":
    main()
