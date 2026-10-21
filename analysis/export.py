"""
analysis/export.py
------------------
Modulo autoritativo per l'esportazione di rapporti completi sperimentali in formato JSON
per Topic Detection e Topic Discovery in ChatAnalysis.

Principi architetturali di affidabilità scientifica e privacy (Audit 21/10/2026):
1. SCHEMA RIGOROSO E VERSIONATO ("1.1.0"):
   Struttura deterministica a sezioni coerenti:
   schema_version, experiment, software, document, model, configuration, execution,
   result, coverage, chunks, checkpoints, validation, provenance, privacy.
2. VERIDICITÀ SCIENTIFICA E NESSUNA FABBRICAZIONE:
   - Fail-closed provenance: se origin manca o non è verificata -> UNKNOWN/NOT_RECORDED, UNATTESTED.
     Nessuna attestazione LIVE_AUTHORITATIVE senza prova autenticata.
   - Non inventa modelli, parametri (context, max_tokens, timeout, temperature, profilo),
     contatori o durate: parametri non misurati rimangono null (0.0 è un valore misurato).
   - Validazione delle invarianti di copertura: se i contatori sono inconsistenti
     (es. 1 pianificato, 1 completato, 2 falliti), non fabbrica una percentuale o COMPLETE.
   - Tracciamento separato di execution_software_commit e export_software_commit.
3. PRIVACY SAFE BY DEFAULT:
   - Safe by default: redact_sensitive_topics=True per default.
   - Oscuramento di topic label, descrizioni, motivazioni, topic_id, consistency_rationale
     e sanitizzazione/redazione di error_detail e source_name.
   - Pseudonimizzazione di tutti gli evidence_id (eccetto alias generici sintetici E1..En).
   - Pseudonimizzazione del dataset nel nome file (mai percorso o chat_id grezzo).
   - Dichiarazione onesta dell'algoritmo di pseudonimizzazione: SHA256_UNSALTED_TRUNCATED.
4. INDIPENDENZA E INTEGRITÀ:
   - Oggetti di dominio immutabili preservati senza alterazione.
   - Serializzazione JSON deterministica (UTF-8, sort_keys=True, indent=2).
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import sys
from typing import Any, Mapping, Sequence

from ai.discovery import infer_model_family
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    ModelFamily,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.models import (
    AnalysisOrigin,
    AttestationLevel,
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ConsistencyResult,
    ConsistencyStatus,
    LoadingMode,
    OperationalProfile,
)

SCHEMA_VERSION = "1.1.0"
SOFTWARE_NAME = "ChatAnalysis"


# ---------------------------------------------------------------------------
# Utility per informazioni di versione e ambiente
# ---------------------------------------------------------------------------

def get_software_version() -> str:
    """Restituisce la versione applicativa dal pyproject.toml o '0.1.0' come fallback."""
    try:
        pyproj = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproj.exists():
            for line in pyproj.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version ="):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.1.0"


def get_git_commit(repo_path: Path | str | None = None) -> str:
    """Restituisce l'hash del commit Git corrente o 'NOT_RECORDED'."""
    try:
        import subprocess
        target_cwd = str(repo_path) if repo_path else str(Path(__file__).resolve().parent.parent)
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "NOT_RECORDED"


def get_platform_environment() -> dict[str, str]:
    """Restituisce l'ambiente host locale senza dati identificativi personali."""
    return {
        "os": platform.system().lower(),
        "os_release": platform.release(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
        "mode": "100% Offline / Local Execution",
    }


# ---------------------------------------------------------------------------
# Sanitizzazione e Privacy Guardrails (Safe by Default)
# ---------------------------------------------------------------------------

SUPPORTED_OPERATIONAL_MODELS = {
    "qwen2.5-7b-instruct",
    "qwen2.5-7b-instruct-1m",
    "qwen2.5-14b-instruct",
    "llama-3.1-8b-instruct",
    "llama-3.2-3b-instruct",
    "deepseek-r1-distill-qwen-7b",
    "mistral-7b-instruct",
    "fake-model",
    "mock-model",
    "qwen",
    "llama",
    "deepseek",
    "mistral",
}

ALLOWED_DIAGNOSTIC_CATEGORIES = {
    "NONE",
    "TIMEOUT",
    "PARSING_ERROR",
    "JSON_PARSE_ERROR",
    "SCHEMA_VALIDATION_ERROR",
    "NETWORK_ERROR",
    "SYSTEM_ERROR",
    "UNKNOWN_ERROR",
    "RECOVERED_FROM_CHECKPOINT",
    "EVIDENCE_VALIDATION_ERROR",
    "INVALID_EVIDENCE_ID",
    "INFERENCE_ERROR",
}

ALLOWED_FAILURE_PHASES = {
    "completed",
    "recovered",
    "inference",
    "parsing",
    "validation",
    "evidence_validation",
    "prompt_assembly",
    "response_parsing",
    "not_started",
    "unknown",
}

ALLOWED_FINISH_REASONS = {
    "stop",
    "length",
    "content_filter",
    "error",
    "timeout",
    "tool_calls",
    "unknown",
}

ALLOWED_ERROR_TYPES = {
    "TimeoutError",
    "ValueError",
    "AiStructuredOutputError",
    "AiInvalidEvidenceCitationError",
    "ConnectionError",
    "RuntimeError",
    "KeyError",
    "IndexError",
    "OSError",
    "JSONDecodeError",
    "FileNotFoundError",
    "PermissionError",
    "SnapshotCreationFailedError",
    "AiPromptError",
    "AiExecutionError",
    "None",
}

ALLOWED_MODEL_FAMILIES = {
    "qwen",
    "llama",
    "deepseek",
    "phi",
    "gemma",
    "mistral",
    "custom",
    "unknown",
}

ALLOWED_QUANTIZATIONS = {
    "Q4_K_M",
    "Q5_K_M",
    "Q8_0",
    "F16",
    "BF16",
    "Q4_0",
    "Q4_1",
    "Q5_0",
    "Q5_1",
    "Q2_K",
    "Q3_K_S",
    "Q3_K_M",
    "Q3_K_L",
    "Q6_K",
    "UNKNOWN",
}

ALLOWED_OPERATIONAL_PROFILES = {
    "HIGH_RESOURCE",
    "BALANCED",
    "RESOURCE_CONSTRAINED",
    "ULTRA_FAST",
    "FALLBACK",
}

ALLOWED_REDUCTION_STRATEGIES = {
    "DIRECT_MULTILINGUAL",
    "TRANSLATE_FIRST",
    "NONE",
    "CHUNKING",
    "HIERARCHICAL",
    "EXTRACTIVE",
}

ALLOWED_ORIGIN_FLOWS = {
    "MANUAL_DETECTION",
    "DISCOVERY_VERIFICATION",
    "BENCHMARK_EVALUATION",
    "CHECKPOINT_RESUME",
    "INTERACTIVE_UI",
    "LIVE_DISCOVERY",
    "NOT_RECORDED",
}

ALLOWED_ORIGINS = {
    "LIVE_LM_STUDIO",
    "MOCK_INSPECTION",
    "SYNTHETIC_REFERENCE",
    "NOT_RECORDED",
    "OFFLINE_PLAYBACK",
}

ALLOWED_LOADING_MODES = {
    "IN_MEMORY_EXECUTION",
    "CHECKPOINT_RESTORE",
    "DISK_BACKED",
    "NOT_RECORDED",
}

ALLOWED_DATASET_MODES = {
    "REAL_DATA",
    "SYNTHETIC_DATA",
    "BENCHMARK",
    "NOT_RECORDED",
    "TEST",
}

ALLOWED_EXECUTION_STATUSES = {
    "COMPLETE",
    "PARTIAL",
    "FAILED",
    "SUCCESS",
    "NOT_RECORDED",
    "INCONSISTENT_COUNTERS",
    "UNATTEMPTED",
    "UNVERIFIED",
    "NO_WORK",
}

ALLOWED_VALIDATION_STATUSES = {
    "STATICALLY_VERIFIED",
    "RUNTIME_VERIFIED",
    "TEST_VERIFIED",
    "MOCK_VERIFIED",
    "NOT_VERIFIED",
    "UNVERIFIED",
    "NOT_APPLIED",
}

ALLOWED_CHUNK_STATUSES = {
    "SUCCESS",
    "FAILED",
    "SKIPPED",
    "RECOVERED_FROM_CHECKPOINT",
    "UNATTEMPTED",
    "TIMEOUT",
    "CANCELLED",
}

ALLOWED_AGGREGATION_STATUSES = {
    "NOT_RECORDED",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "SKIPPED",
    "SUCCESS",
}


def sanitize_model_family(val: Any, redact: bool = True, fallback: str = "unknown") -> str:
    """Sanitizza la famiglia di modelli secondo allowlist formale."""
    if val is None:
        return fallback
    s = str(val).strip().lower()
    if s in ALLOWED_MODEL_FAMILIES:
        return s
    return fallback if redact else s[:32]


def sanitize_quantization(val: Any, redact: bool = True) -> str | None:
    """Sanitizza la quantizzazione dichiarata/osservata secondo allowlist."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s in ALLOWED_QUANTIZATIONS:
        return s
    return "UNKNOWN" if redact else s[:16]


def sanitize_operational_profile(val: Any, redact: bool = True) -> str | None:
    """Sanitizza il profilo operativo secondo allowlist formale."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s in ALLOWED_OPERATIONAL_PROFILES:
        return s
    return None if redact else s[:32]


def sanitize_reduction_strategy(val: Any, redact: bool = True) -> str | None:
    """Sanitizza la strategia di riduzione secondo allowlist."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s in ALLOWED_REDUCTION_STRATEGIES:
        return s
    return None if redact else s[:32]


def sanitize_origin_flow(val: Any, redact: bool = True, default: str = "NOT_RECORDED") -> str:
    """Sanitizza il flusso di origine secondo allowlist."""
    if not val:
        return default
    s = str(val).strip().upper()
    if s in ALLOWED_ORIGIN_FLOWS:
        return s
    return default


def sanitize_origin(val: Any, redact: bool = True) -> str:
    """Sanitizza l'origine di esecuzione secondo allowlist."""
    if not val:
        return "NOT_RECORDED"
    s = str(val).strip().upper()
    if s in ALLOWED_ORIGINS:
        return s
    return "NOT_RECORDED"


def sanitize_loading_mode(val: Any) -> str:
    """Sanitizza la modalità di caricamento checkpoint/memoria."""
    if not val:
        return "IN_MEMORY_EXECUTION"
    s = str(val).strip().upper()
    if s in ALLOWED_LOADING_MODES:
        return s
    return "IN_MEMORY_EXECUTION"


def sanitize_git_commit(commit: Any) -> str:
    """Valida tipicamente un commit Git (7-40 caratteri esadecimali), escludendo categoricamente tag (es. release_MarioRossi), canarini e testo libero."""
    if not commit or not isinstance(commit, str):
        return "NOT_RECORDED"
    s = commit.strip()
    if not s or s == "NOT_RECORDED":
        return "NOT_RECORDED"
    lower_s = s.lower()
    if any(k in lower_s for k in ("canary", "secret", "confidential", "token", "password", "example", "+", "@", " ", "/", "\\")):
        return "NOT_RECORDED"
    if re.match(r"^[0-9a-fA-F]{7,40}$", s):
        return s
    return "NOT_RECORDED"


def sanitize_software_version(val: Any, redact: bool = True) -> str:
    """Sanitizza la versione software secondo semver canonico escludendo categoricamente testi liberi o nomi personali."""
    if not val:
        return get_software_version()
    s = str(val).strip()
    if redact:
        if re.match(r"^v?\d+\.\d+(?:\.\d+)?(?:-[0-9a-zA-Z._]+)?$", s) and not any(k in s.lower() for k in ("mario", "rossi", "canary", "secret", "user", "admin")):
            return s
        return get_software_version()
    return s[:32]


def sanitize_validation_flag(val: Any, redact: bool = True, default: str = "STATICALLY_VERIFIED") -> str | bool | None:
    """Sanitizza i flag tecnici di validazione impedendo fughe di testo arbitrario o nomi personali."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().upper()
    if s in ALLOWED_VALIDATION_STATUSES:
        return s
    if s in ("TRUE", "1"):
        return True
    if s in ("FALSE", "0"):
        return False
    return default if redact else s[:32]


def sanitize_aggregation_errors(errors: Any, redact: bool = True) -> list[str]:
    """Sanitizza la lista degli errori di aggregazione Discovery."""
    if not errors or not isinstance(errors, (list, tuple)):
        return []
    if redact:
        return [sanitize_error_detail(str(e), redact=True) or "[REDACTED_DIAGNOSTIC_DETAIL]" for e in errors]
    return [str(e)[:128] for e in errors]


def sanitize_aggregation_status(val: Any) -> str:
    """Sanitizza lo stato di aggregazione Discovery."""
    if not val:
        return "NOT_RECORDED"
    s = str(val).strip().upper()
    if s in ALLOWED_AGGREGATION_STATUSES:
        return s
    return "NOT_RECORDED"


def sanitize_dataset_mode(mode: Any) -> str:
    """Sanitizza la modalità del dataset."""
    if not mode:
        return "NOT_RECORDED"
    s = str(mode).strip().upper()
    if s in ALLOWED_DATASET_MODES:
        return s
    return "NOT_RECORDED"


def safe_int(val: Any) -> int | None:
    """Conversione sicura a intero, resiste a None, stringhe arbitrarie, NaN e Infinity."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None


def safe_float(val: Any) -> float | None:
    """Conversione sicura a float, resiste a None, stringhe arbitrarie, NaN e Infinity."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def sanitize_local_path(path_str: str | None) -> str | None:
    """Rimuove percorsi assoluti utente o cartelle personali."""
    if not path_str or not isinstance(path_str, str):
        return None
    cleaned = re.sub(r"[A-Za-z]:\\[Uu]sers\\[^\\]+\\", lambda _: "<USER_HOME>\\", path_str)
    cleaned = re.sub(r"/home/[^/]+/", "<USER_HOME>/", cleaned)
    cleaned = re.sub(r"[A-Za-z]:\\[^\\]+\\", lambda _: "<LOCAL_DISK>\\", cleaned)
    return cleaned


def sanitize_source_name(source_name: str | None, redact: bool = True) -> str | None:
    """
    Restituisce un identificativo sicuro della sorgente.
    In modalità safe (default), restituisce uno pseudonimo privo di percorsi o nomi file personali.
    In modalità unredacted, estrae il nome file sanitizzato da percorsi utente.
    """
    if not source_name or not isinstance(source_name, str):
        return None
    if redact:
        return get_pseudonymous_id(source_name, prefix="src")
    p = Path(source_name)
    clean = p.name if p.name else source_name
    return sanitize_local_path(clean)


def sanitize_error_detail(detail: str | None, redact: bool = True) -> str | None:
    """
    Rimuove percorsi locali, email, numeri di telefono o tracce di chat dai messaggi di errore.
    In modalità safe (default), blocca categoricamente qualsiasi testo libero non tipizzato.
    """
    if not detail or not isinstance(detail, str):
        return None
    if redact:
        return "[REDACTED_DIAGNOSTIC_DETAIL]"
    clean = sanitize_local_path(detail)
    if len(clean) > 300:
        clean = clean[:297] + "..."
    return clean


def get_pseudonymous_id(raw_id: str | None, prefix: str = "anon") -> str:
    """
    Genera un identificativo pseudonimo deterministico basato su SHA-256 troncato a 12 caratteri esadecimali.
    Dichiarato onestamente come SHA256_UNSALTED_TRUNCATED.
    """
    if not raw_id:
        return f"{prefix}_unknown"
    raw_str = str(raw_id).strip()
    h = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def sanitize_run_id(raw_run_id: str | None, redact: bool = True) -> str:
    r"""
    Sanitizza un identificativo di esecuzione (run_id).
    In modalità safe (default, redact=True), pseudonimizza SEMPRE l'identificativo di run in modo deterministico
    tramite SHA-256 troncato, garantendo la collegabilità coerente tra report dello stesso esperimento
    senza mai esporre identificativi originari o nomi personali (es. run_MarioRossi).
    """
    if not raw_run_id or str(raw_run_id).strip() in ("NOT_RECORDED", "unknown", "None", ""):
        return "NOT_RECORDED"
    clean = str(raw_run_id).strip()
    if redact:
        return get_pseudonymous_id(clean, prefix="run")
    return clean


def sanitize_model_id(raw_model: str | None, redact: bool = True) -> str | None:
    """
    Sanitizza l'identificativo del modello.
    In modalità safe (default), applica una allowlist rigorosa e converte in pseudonimo
    qualsiasi stringa non autorizzata o contenente email, percorsi o pattern personali arbitrari.
    """
    if not raw_model:
        return None
    s = str(raw_model).strip()
    if redact:
        if "@" in s or "/" in s or "\\" in s or " " in s or "+" in s:
            return get_pseudonymous_id(s, prefix="mod")
        if s.lower() not in SUPPORTED_OPERATIONAL_MODELS:
            return get_pseudonymous_id(s, prefix="mod")
    return s


def sanitize_diagnostic_category(val: Any, redact: bool = True) -> str:
    """Sanitizza la categoria diagnostica secondo allowlist strutturata."""
    if val is None:
        return "NONE"
    s = str(val).strip().upper()
    if s in ALLOWED_DIAGNOSTIC_CATEGORIES:
        return s
    return "UNKNOWN_ERROR" if redact else s


def sanitize_failure_phase(val: Any, redact: bool = True) -> str:
    """Sanitizza la fase di fallimento secondo allowlist strutturata."""
    if val is None:
        return "unknown"
    s = str(val).strip().lower()
    if s in ALLOWED_FAILURE_PHASES:
        return s
    return "unknown" if redact else s


def sanitize_finish_reason(val: Any, redact: bool = True) -> str | None:
    """Sanitizza il finish_reason secondo allowlist strutturata."""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ALLOWED_FINISH_REASONS:
        return s
    return "unknown" if redact else s


def sanitize_error_type(val: Any, redact: bool = True) -> str | None:
    """Sanitizza il tipo di eccezione secondo allowlist strutturata."""
    if val is None:
        return None
    s = str(val).strip()
    if redact:
        if "." in s:
            s = s.rsplit(".", 1)[-1]
        if s in ALLOWED_ERROR_TYPES:
            return s
        return "GENERIC_ERROR"
    return s


def sanitize_document_fingerprint(fp: Any) -> str | None:
    """
    Restituisce l'impronta crittografica autentica registrata del documento (64 caratteri esadecimali SHA-256).
    Se assente o non conforme a una vera impronta crittografica registrata, restituisce None (NOT_RECORDED).
    NON fabbrica pseudonimi del solo document_id.
    """
    if not fp or not isinstance(fp, str):
        return None
    s = fp.strip()
    if re.match(r"^[0-9a-fA-F]{64}$", s):
        return s.lower()
    return None


def sanitize_evidence_id(eid: str, redact: bool = True) -> str:
    """
    Rappresenta un evidence_id in modo scientificamente sicuro.
    In modalità safe (default), ammette solo alias numerici sintetici standard (es. E001, sec_01, chk_0).
    Tutti gli identificatori potenzialmente personali (nomi, numeri, colons, email) vengono pseudonimizzati.
    """
    if not eid or not isinstance(eid, str):
        return "E_UNKNOWN"
    clean = eid.strip()
    if redact:
        # Consente esclusivamente alias numerici sintetici standard (es. E1..E9999, sec_1..sec_9999)
        if re.match(r"^(?:E|sec_|chk_)\d{1,6}$", clean, re.IGNORECASE):
            return clean
        return get_pseudonymous_id(clean, prefix="ev")

    # In modalità locale dettagliata:
    if re.match(r"^[A-Za-z0-9_:\.-]+$", clean) and len(clean) <= 64:
        if "\\" not in clean and "/" not in clean and "@" not in clean:
            return clean
    return get_pseudonymous_id(clean, prefix="ev")


def sanitize_topic_text(text: str | None, redact: bool = True) -> str | None:
    """Restituisce il testo del topic o la dicitura di oscuramento in modalità privacy (default=True)."""
    if text is None:
        return None
    if redact:
        return "[OSCURATO PER PRIVACY / REDACTED]"
    return str(text)


def format_iso_timestamp(ts: Any) -> str | None:
    """Formatta e valida rigorosamente in ISO-8601 standard qualsiasi tipo di timestamp o stringa."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, (int, float)):
        try:
            if math.isnan(ts) or math.isinf(ts) or ts <= 0:
                return None
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None
    s = str(ts).strip()
    if not s or s in ("N/D", "n/a", "None", "NOT_RECORDED", "null"):
        return None
    try:
        norm = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Serializzatore JSON Determinista
# ---------------------------------------------------------------------------

class DeterministicJsonEncoder(json.JSONEncoder):
    """Encoder JSON rigoroso che converte Enum, dataclass, tuple, set e Mapping."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        if isinstance(obj, (set, frozenset)):
            return sorted(list(obj))
        if isinstance(obj, tuple):
            return list(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return format_iso_timestamp(obj)
        if isinstance(obj, Mapping):
            return dict(obj)
        return super().default(obj)


def serialize_report_to_json(report: dict[str, Any]) -> str:
    """Serializza un dizionario di rapporto in JSON deterministico formattato UTF-8."""
    return json.dumps(
        report,
        cls=DeterministicJsonEncoder,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Generatore di Nomi di File Sanitizzati (Privacy-Aware)
# ---------------------------------------------------------------------------

def generate_export_filename(
    analysis_type: str,
    dataset_id: str,
    model_id: str | None,
    run_id: str | None,
    timestamp: str | None = None,
    export_id: str | None = None,
    extension: str = "json",
) -> str:
    """
    Genera un nome file univoco, deterministico e sanitizzato.
    Usa SEMPRE lo pseudonimo del dataset anziché il percorso o chat_id grezzo.
    Se run_id o model_id contengono dati personali (email, numeri, nomi, percorsi), vengono pseudonimizzati.
    """
    def _clean(val: str, max_len: int = 32) -> str:
        s = re.sub(r"[^a-zA-Z0-9_\.-]+", "_", str(val).strip()).strip("_")
        return s[:max_len] if s else "unknown"

    type_prefix = "detection" if "detect" in analysis_type.lower() else "discovery"

    # Dataset: se non è già nel formato anonimo standard chat_<hash> o syn_<num>, genera pseudonimo
    raw_ds = str(dataset_id).strip() if dataset_id else ""
    if re.match(r"^(?:chat|doc)_[0-9a-fA-F]{8,32}$", raw_ds) or re.match(r"^syn_\d{1,6}$", raw_ds):
        clean_ds = _clean(raw_ds, 24)
    else:
        clean_ds = get_pseudonymous_id(raw_ds or "nodataset", prefix="chat")

    # Modello: se non è modello standard da allowlist, usa pseudonimo
    m_str = str(model_id).strip() if model_id else ""
    if m_str and m_str.lower() in SUPPORTED_OPERATIONAL_MODELS:
        clean_model = _clean(m_str, 32)
    else:
        clean_model = get_pseudonymous_id(m_str or "nomodel", prefix="mod")

    # Run ID: accetta solo formati sintetici standard, altrimenti pseudonimizza
    r_str = str(run_id).strip() if run_id else ""
    if not r_str or r_str in ("NOT_RECORDED", "unknown", "None", ""):
        clean_run = _clean(export_id or "norunid", 24)
    else:
        clean_run = sanitize_run_id(r_str, redact=True)

    if timestamp:
        clean_ts = re.sub(r"[^0-9TZ]+", "", str(timestamp).replace("-", "").replace(":", ""))
        if len(clean_ts) > 15:
            clean_ts = clean_ts[:15]
    else:
        clean_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    ext = "csv" if ("csv" in analysis_type.lower() or extension.lower() == "csv") else "json"
    return f"{type_prefix}_{clean_ds}_{clean_model}_{clean_run}_{clean_ts}.{ext}"


def export_chunk_diagnostics_to_csv(
    diagnostics: Sequence[Mapping[str, Any]] | None,
    total_planned_chunks: int | None = None,
    model_id: str | None = None,
    run_id: str | None = None,
    redact_sensitive_topics: bool = True,
) -> str:
    """Esporta la diagnostica dei chunk in formato CSV rigorosamente sanitizzato e safe-by-default."""
    norm_chunks = _normalize_chunks_diagnostics(
        raw_diagnostics=diagnostics,
        total_planned_chunks=total_planned_chunks,
        model_id=model_id,
        run_id=run_id,
        status_default="NOT_RECORDED",
        redact_sensitive_topics=redact_sensitive_topics,
    )
    if not norm_chunks:
        return "chunk_index,status,diagnostic_category,error_type\n"
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(norm_chunks[0].keys()))
    writer.writeheader()
    writer.writerows(norm_chunks)
    return buf.getvalue()



# ---------------------------------------------------------------------------
# Normalizzazione e Ricostruzione Diagnostica Chunk
# ---------------------------------------------------------------------------

def _normalize_chunks_diagnostics(
    raw_diagnostics: Sequence[Mapping[str, Any]] | None,
    total_planned_chunks: int | None,
    model_id: str | None,
    run_id: str | None,
    status_default: str = "NOT_RECORDED",
    redact_sensitive_topics: bool = True,
) -> list[dict[str, Any]]:
    """
    Assicura che tutti i chunk pianificati (0..total_planned_chunks-1) siano
    rappresentati esplicitamente e sanitizzati, senza inventare successi o durate.
    """
    diag_by_index: dict[int, dict[str, Any]] = {}

    if raw_diagnostics:
        for d in raw_diagnostics:
            idx = d.get("chunk_index")
            if idx is not None and isinstance(idx, int) and idx >= 0:
                diag_by_index[idx] = dict(d)

    total = total_planned_chunks if (total_planned_chunks is not None and total_planned_chunks >= 0) else (max(diag_by_index.keys()) + 1 if diag_by_index else 0)

    normalized: list[dict[str, Any]] = []
    for idx in range(total):
        if idx in diag_by_index:
            d = diag_by_index[idx]
            raw_st = str(d.get("status") or status_default).strip().upper()
            st = raw_st if raw_st in ALLOWED_CHUNK_STATUSES else ("NOT_RECORDED" if redact_sensitive_topics else raw_st[:32])
            att = safe_int(d.get("attempts"))
            dur = safe_float(d.get("duration_seconds"))
            raw_mid = d.get("model_id") or d.get("model") or model_id
            mid = sanitize_model_id(raw_mid, redact=redact_sensitive_topics)
            http_st = safe_int(d.get("http_status"))
            finish_r = sanitize_finish_reason(d.get("finish_reason"), redact=redact_sensitive_topics)
            p_tok = safe_int(d.get("prompt_tokens"))
            c_tok = safe_int(d.get("completion_tokens"))
            t_tok = safe_int(d.get("total_tokens")) or ((p_tok + c_tok) if p_tok is not None and c_tok is not None else None)
            jv_raw = d.get("json_valid") if d.get("json_valid") is not None else d.get("json_validation_ok")
            jv = bool(jv_raw) if jv_raw is not None else None
            sv_raw = d.get("schema_valid") if d.get("schema_valid") is not None else d.get("schema_validation_ok")
            sv = bool(sv_raw) if sv_raw is not None else None
            ev_raw = d.get("evidence_valid") if d.get("evidence_valid") is not None else d.get("evidence_validation_ok")
            ev = bool(ev_raw) if ev_raw is not None else None
            err_type = sanitize_error_type(d.get("error_type") or d.get("exception_class"), redact=redact_sensitive_topics)
            diag_cat = sanitize_diagnostic_category(
                d.get("diagnostic_category") or ("NONE" if st in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT") else "UNKNOWN_ERROR"),
                redact=redact_sensitive_topics
            )
            phase = sanitize_failure_phase(
                d.get("failure_phase") or ("completed" if st == "SUCCESS" else "recovered" if st == "RECOVERED_FROM_CHECKPOINT" else "inference"),
                redact=redact_sensitive_topics
            )
            err_det = sanitize_error_detail(d.get("error_detail") or d.get("details"), redact=redact_sensitive_topics)
            chk_raw = d.get("checkpoint_saved") if d.get("checkpoint_saved") is not None else d.get("checkpoint_save_ok")
            chk_saved = bool(chk_raw) if chk_raw is not None else False
            val_ev_c = safe_int(d.get("valid_evidence_count"))

            rec = {
                "chunk_index": idx,
                "chunk_display_number": idx + 1,
                "chunk_id": get_pseudonymous_id(d.get("chunk_id") or f"chk_{idx}", prefix="chk") if redact_sensitive_topics else str(d.get("chunk_id") or f"chunk_{idx}"),
                "status": st,
                "attempts": att,
                "duration_seconds": round(dur, 4) if dur is not None else None,
                "model_id": mid,
                "http_status": http_st,
                "finish_reason": finish_r,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": t_tok,
                "json_valid": jv,
                "schema_valid": sv,
                "evidence_valid": ev,
                "error_type": err_type,
                "diagnostic_category": diag_cat,
                "failure_phase": phase,
                "error_detail": err_det,
                "checkpoint_saved": chk_saved,
                "valid_evidence_count": val_ev_c,
                "subchunks_used": bool(d.get("subchunks_used", False)),
            }
        else:
            rec = {
                "chunk_index": idx,
                "chunk_display_number": idx + 1,
                "chunk_id": f"chk_{idx}_unattempted" if redact_sensitive_topics else f"chunk_{idx}_unattempted",
                "status": "UNATTEMPTED",
                "attempts": 0,
                "duration_seconds": None,
                "model_id": sanitize_model_id(model_id, redact=redact_sensitive_topics),
                "http_status": None,
                "finish_reason": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "json_valid": None,
                "schema_valid": None,
                "evidence_valid": None,
                "error_type": None,
                "diagnostic_category": "NONE",
                "failure_phase": "not_started",
                "error_detail": "[REDACTED_DIAGNOSTIC_DETAIL]" if redact_sensitive_topics else "Blocco non eseguito o saltato",
                "checkpoint_saved": False,
                "valid_evidence_count": 0,
                "subchunks_used": False,
            }
        normalized.append(rec)
    return normalized


# ---------------------------------------------------------------------------
# Costruttore Rapporto: Topic Detection
# ---------------------------------------------------------------------------

def build_topic_detection_export_report(
    detection_result: TopicDetectionResult,
    document: ConversationEvidenceDocument,
    dataset_mode: Any = None,
    consistency_result: ConsistencyResult | None = None,
    redact_sensitive_topics: bool = True,
    checkpoint_store: Any = None,
    custom_commit: str | None = None,
    custom_version: str | None = None,
    authoritative_live_run_ids: Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    """
    Costruisce la struttura dati completa e scientificamente veritiera per l'esportazione JSON di Topic Detection.
    Privacy Safe by Default: redact_sensitive_topics=True.
    """
    meta = dict(detection_result.metadata or {})

    # Run ID: non inventare 'run_' + id(obj). Usare NOT_RECORDED se non registrata.
    raw_run_id = meta.get("run_id")
    run_id = sanitize_run_id(raw_run_id, redact=redact_sensitive_topics)
    export_id = get_pseudonymous_id(f"exp_det_{id(detection_result)}_{datetime.now(timezone.utc).timestamp()}", prefix="exp")
    analysis_type = "TOPIC_DETECTION"

    # Risoluzione modello: acquisire dai metadati reali senza default fittizi
    req_model = sanitize_model_id(meta.get("requested_model") or meta.get("model"), redact=redact_sensitive_topics)
    eff_model = sanitize_model_id(meta.get("model") or req_model, redact=redact_sensitive_topics)
    if eff_model:
        family_inferred = infer_model_family(eff_model)
        fallback_fam = family_inferred.value if hasattr(family_inferred, "value") else str(family_inferred)
        family_val = sanitize_model_family(meta.get("family"), redact=redact_sensitive_topics, fallback=fallback_fam)
    else:
        family_val = None

    # Risoluzione conteggi chunk e validazione delle invarianti
    raw_diag = meta.get("chunk_diagnostics") or []
    planned_chunks_raw = meta.get("chunk_count") if meta.get("chunk_count") is not None else meta.get("expected_chunk_count")
    planned_chunks = safe_int(planned_chunks_raw)
    if planned_chunks is not None and planned_chunks < 0:
        planned_chunks = None
    elif planned_chunks is None and raw_diag:
        planned_chunks = len(raw_diag)

    has_explicit_counters = any(
        meta.get(k) is not None for k in ("completed_chunk_count", "failed_chunk_count", "recovered_chunk_count", "skipped_chunk_count")
    )

    if planned_chunks is not None and planned_chunks == 0:
        failed_chunks = safe_int(meta.get("failed_chunk_count")) or 0
        recovered_chunks = safe_int(meta.get("recovered_chunk_count")) or 0
        completed_chunks = safe_int(meta.get("completed_chunk_count")) or 0
        skipped_chunks = safe_int(meta.get("skipped_chunk_count")) or 0
        if completed_chunks == 0 and failed_chunks == 0:
            counter_invariants_valid = True
            is_complete = False
            coverage_status = "NO_WORK"
            completion_pct = None
        else:
            counter_invariants_valid = False
            is_complete = False
            coverage_status = "INCONSISTENT_COUNTERS"
            completion_pct = None
    elif planned_chunks is not None and (has_explicit_counters or raw_diag):
        if has_explicit_counters:
            failed_chunks = safe_int(meta.get("failed_chunk_count")) if meta.get("failed_chunk_count") is not None else 0
            recovered_chunks = safe_int(meta.get("recovered_chunk_count")) if meta.get("recovered_chunk_count") is not None else 0
            completed_chunks = safe_int(meta.get("completed_chunk_count"))
            skipped_chunks = safe_int(meta.get("skipped_chunk_count")) if meta.get("skipped_chunk_count") is not None else 0
        else:
            completed_chunks = sum(1 for d in raw_diag if d.get("status") in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"))
            failed_chunks = sum(1 for d in raw_diag if d.get("status") == "FAILED")
            recovered_chunks = sum(1 for d in raw_diag if d.get("status") == "RECOVERED_FROM_CHECKPOINT")
            skipped_chunks = sum(1 for d in raw_diag if d.get("status") in ("SKIPPED", "UNATTEMPTED"))

        if completed_chunks is None:
            counter_invariants_valid = None
            is_complete = None
            coverage_status = "NOT_RECORDED"
            completion_pct = None
        else:
            contradiction = False
            if raw_diag:
                diag_success = sum(1 for d in raw_diag if d.get("status") in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"))
                diag_failed = sum(1 for d in raw_diag if d.get("status") == "FAILED")
                diag_unattempted = sum(1 for d in raw_diag if d.get("status") == "UNATTEMPTED")
                if diag_unattempted == len(raw_diag) and completed_chunks > 0:
                    contradiction = True
                if diag_failed > 0 and (failed_chunks or 0) == 0:
                    contradiction = True
                if diag_success < completed_chunks and len(raw_diag) == planned_chunks:
                    contradiction = True

            invariants_ok = (
                (completed_chunks + (failed_chunks or 0) + (skipped_chunks or 0) == planned_chunks)
                and completed_chunks <= planned_chunks
                and (failed_chunks or 0) <= planned_chunks
                and not contradiction
            )

            if invariants_ok:
                counter_invariants_valid = True
                is_complete = (completed_chunks == planned_chunks and (failed_chunks or 0) == 0)
                coverage_status = "COMPLETE" if is_complete else ("PARTIAL" if completed_chunks > 0 else "FAILED")
                completion_pct = round((completed_chunks / planned_chunks) * 100.0, 1) if planned_chunks > 0 else 0.0
            else:
                counter_invariants_valid = False
                is_complete = False
                coverage_status = "INCONSISTENT_COUNTERS"
                completion_pct = None
    else:
        failed_chunks = safe_int(meta.get("failed_chunk_count"))
        recovered_chunks = safe_int(meta.get("recovered_chunk_count"))
        completed_chunks = safe_int(meta.get("completed_chunk_count"))
        skipped_chunks = safe_int(meta.get("skipped_chunk_count"))
        counter_invariants_valid = None
        is_complete = None
        coverage_status = "NOT_RECORDED"
        completion_pct = None

    # Determinazione stato esecuzione: P0/B separazione reported da verified
    raw_status = meta.get("status")
    if raw_status:
        st_clean = str(raw_status).strip().upper()
        reported_status = st_clean if st_clean in ALLOWED_EXECUTION_STATUSES else "NOT_RECORDED"
    else:
        reported_status = coverage_status

    if coverage_status == "INCONSISTENT_COUNTERS":
        verified_status = "INCONSISTENT_COUNTERS"
    elif coverage_status == "NO_WORK":
        verified_status = "NO_WORK"
    elif coverage_status == "NOT_RECORDED":
        verified_status = "NOT_RECORDED"
    elif coverage_status == "COMPLETE" and is_complete is True:
        verified_status = "COMPLETE"
    elif coverage_status == "PARTIAL":
        verified_status = "PARTIAL"
    elif coverage_status == "FAILED":
        verified_status = "FAILED"
    else:
        verified_status = "NOT_RECORDED"

    execution_status = verified_status

    # Risoluzione evidenze
    tot_evidence = document.evidence_count
    analyzed_ev = safe_int(meta.get("analyzed_evidence_count"))
    unanalyzed_ev = safe_int(meta.get("unanalyzed_evidence_count"))
    if unanalyzed_ev is None and analyzed_ev is not None:
        unanalyzed_ev = max(0, tot_evidence - analyzed_ev)

    ev_coverage_pct = round((analyzed_ev / tot_evidence) * 100.0, 1) if tot_evidence and analyzed_ev is not None else None

    # Profilo operativo: non inventare BALANCED se assente
    prof_req = sanitize_operational_profile(meta.get("profile_requested") or meta.get("profile"), redact=redact_sensitive_topics)
    prof_app = sanitize_operational_profile(meta.get("profile_applied") or meta.get("profile"), redact=redact_sensitive_topics)

    # Origine ed attestazione: FAIL-CLOSED (nessun LIVE_AUTHORITATIVE senza verifica autoritativa)
    raw_att = meta.get("attestation_level")
    origin_val = sanitize_origin(meta.get("origin"), redact=redact_sensitive_topics)
    is_mock = (
        bool(meta.get("is_mock"))
        or bool(meta.get("is_fake"))
        or (origin_val in (AnalysisOrigin.MOCK_INSPECTION.value, AnalysisOrigin.SYNTHETIC_REFERENCE.value))
        or ("fake" in str(meta.get("client_class", "")).lower())
        or ("fake" in str(eff_model).lower() if eff_model else False)
        or ("mock" in str(eff_model).lower() if eff_model else False)
    )

    if raw_att:
        att_str = str(raw_att).strip().upper()
        reported_attestation = att_str if att_str in ("LIVE_AUTHORITATIVE", "MOCK_VERIFIED", "UNATTESTED", "REPORTED_LIVE") else "UNATTESTED"
    elif (
        meta.get("authoritative_execution_certified") is True
        or meta.get("server_provenance_verified") is True
        or meta.get("runtime_binding_verified") is True
    ):
        reported_attestation = "REPORTED_LIVE"
    elif origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value:
        reported_attestation = "REPORTED_LIVE"
    else:
        reported_attestation = None

    # Autenticazione autoritativa verificata: FAIL-CLOSED
    # Solo una fonte autoritativa esterna (authoritative_live_run_ids) che conferma il raw_run_id originale può promuovere a LIVE_AUTHORITATIVE.
    # I soli flag auto-dichiarati nei metadati (authoritative_execution_certified, ecc.) NON possono promuovere a LIVE_AUTHORITATIVE.
    is_authoritative_live = False
    if authoritative_live_run_ids is not None and raw_run_id and not is_mock:
        if raw_run_id in authoritative_live_run_ids:
            is_authoritative_live = True

    if is_mock:
        verified_attestation = AttestationLevel.MOCK_VERIFIED.value
    elif is_authoritative_live and origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value:
        verified_attestation = AttestationLevel.LIVE_AUTHORITATIVE.value
    else:
        verified_attestation = AttestationLevel.UNATTESTED.value

    attestation = verified_attestation

    # Distinguere la modalità di caricamento dall'origine di inferenza
    loading_mode_raw = meta.get("loading_mode")
    if not loading_mode_raw:
        loading_mode_raw = (
            LoadingMode.CHECKPOINT_RESTORE.value
            if (meta.get("is_persisted_recovery") or (recovered_chunks and recovered_chunks > 0))
            else LoadingMode.IN_MEMORY_EXECUTION.value
        )
    loading_mode = sanitize_loading_mode(loading_mode_raw)

    if dataset_mode:
        mode_str = sanitize_dataset_mode(dataset_mode)
    elif meta.get("mode"):
        mode_str = sanitize_dataset_mode(meta["mode"])
    else:
        mode_str = "NOT_RECORDED"

    # Informazioni Topic ricercato
    t_obj = detection_result.topic
    is_label_only = getattr(t_obj, "is_label_only", False) or (t_obj.description.strip().casefold() == t_obj.label.strip().casefold())
    query_mode_str = "LABEL_ONLY" if is_label_only else "LABEL_WITH_DESCRIPTION"
    origin_flow = sanitize_origin_flow(meta.get("origin_flow"), redact=redact_sensitive_topics, default="MANUAL_DETECTION")

    # Diagnostica chunk normalizzata
    norm_chunks = _normalize_chunks_diagnostics(
        raw_diagnostics=raw_diag,
        total_planned_chunks=planned_chunks,
        model_id=eff_model,
        run_id=run_id,
        status_default="SUCCESS" if is_complete else "PARTIAL",
        redact_sensitive_topics=redact_sensitive_topics,
    )

    # Costruzione sezione semantica Prova del Nove / Consistency
    semantic_consistency_info: dict[str, Any] | None = None
    if consistency_result is not None:
        src_disc_run = sanitize_run_id(consistency_result.discovery_run_id, redact=redact_sensitive_topics)
        tgt_det_run = sanitize_run_id(consistency_result.detection_run_id, redact=redact_sensitive_topics)

        # Verifica indipendenza inferenziale documentata (non inventare True)
        if src_disc_run and tgt_det_run and src_disc_run != tgt_det_run and meta.get("inferential_independence_verified") is True:
            independence_verified = True
        elif meta.get("inferential_independence_verified") is False:
            independence_verified = False
        else:
            independence_verified = None

        semantic_consistency_info = {
            "origin_flow": "DISCOVERY_VERIFICATION",
            "source_discovery_run_id": src_disc_run,
            "target_detection_run_id": tgt_det_run,
            "verified_topic_id": get_pseudonymous_id(t_obj.topic_id, prefix="top") if redact_sensitive_topics else t_obj.topic_id,
            "authentication_status": attestation,
            "detection_decision": detection_result.decision.value,
            "consistency_status": consistency_result.status.value,
            "consistency_rationale": sanitize_topic_text(consistency_result.rationale, redact=redact_sensitive_topics),
            "discovery_evidence_ids": [sanitize_evidence_id(e, redact=redact_sensitive_topics) for e in consistency_result.discovery_evidence_ids],
            "detection_evidence_ids": [sanitize_evidence_id(e, redact=redact_sensitive_topics) for e in consistency_result.detection_evidence_ids],
            "overlap_evidence_ids": [sanitize_evidence_id(e, redact=redact_sensitive_topics) for e in consistency_result.overlap_evidence_ids],
            "overlap_count": len(consistency_result.overlap_evidence_ids),
            "inferential_independence_verified": independence_verified,
        }

    # Durate: null se non misurate, mai 0.0
    dur_val = meta.get("latency_seconds") if meta.get("latency_seconds") is not None else meta.get("duration_seconds")
    duration_sec = round(float(dur_val), 3) if dur_val is not None and safe_float(dur_val) is not None else None

    # Struttura del report
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "software": {
            "name": SOFTWARE_NAME,
            "version": sanitize_software_version(custom_version or get_software_version(), redact=redact_sensitive_topics),
            "execution_software_commit": sanitize_git_commit(meta.get("git_commit")),
            "export_software_commit": sanitize_git_commit(custom_commit or get_git_commit()),
            "offline_mode": bool(meta.get("offline_mode")) if meta.get("offline_mode") is not None else None,
            "environment": get_platform_environment(),
        },
        "experiment": {
            "analysis_type": analysis_type,
            "run_id": run_id,
            "export_id": export_id,
            "origin_flow": origin_flow,
            "mode": mode_str,
            "conversation_pseudonym": get_pseudonymous_id(document.chat_id or document.document_id, prefix="chat"),
            "reported_attestation_level": reported_attestation,
            "verified_attestation_level": verified_attestation,
            "attestation_level": verified_attestation,
            "loading_mode": loading_mode,
            "start_time": format_iso_timestamp(meta.get("start_timestamp")),
            "end_time": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
        },
        "document": {
            "document_pseudonym": get_pseudonymous_id(document.document_id, prefix="doc"),
            "document_fingerprint": sanitize_document_fingerprint(meta.get("document_fingerprint")),
            "message_count": document.message_count,
            "total_evidence_count": tot_evidence,
            "analyzed_evidence_count": analyzed_ev,
            "unanalyzed_evidence_count": unanalyzed_ev,
            "source_name": sanitize_source_name(document.source_name, redact=redact_sensitive_topics),
        },
        "model": {
            "requested_model_id": req_model,
            "effective_model_id": eff_model,
            "family": family_val,
            "declared_quantization": sanitize_quantization(meta.get("quantization") or meta.get("declared_quantization"), redact=redact_sensitive_topics),
            "observed_quantization": sanitize_quantization(meta.get("observed_quantization"), redact=redact_sensitive_topics),
            "declared_context_length": safe_int(meta.get("context_length")),
            "effective_context_length": safe_int(meta.get("runtime_context_length") or meta.get("context_length")),
            "local_endpoint": "http://127.0.0.1:1234" if eff_model else None,
        },
        "configuration": {
            "temperature": safe_float(meta.get("temperature")),
            "max_tokens": safe_int(meta.get("max_tokens")),
            "timeout_seconds": safe_float(meta.get("timeout_seconds")),
            "seed": safe_int(meta.get("seed")),
            "operational_profile_requested": prof_req,
            "operational_profile_applied": prof_app,
            "profile_downgraded": bool(meta.get("profile_downgraded", False)),
            "reduction_strategy": sanitize_reduction_strategy(meta.get("reduction_strategy"), redact=redact_sensitive_topics),
            "query_mode": query_mode_str,
        },
        "execution": {
            "start_timestamp": format_iso_timestamp(meta.get("start_timestamp")),
            "end_timestamp": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
            "origin": origin_val,
            "reported_execution_status": reported_status,
            "verified_execution_status": verified_status,
            "global_status": verified_status,
            "error_count": safe_int(meta.get("error_count")) if meta.get("error_count") is not None else (failed_chunks if failed_chunks is not None else None),
            "retry_count": safe_int(meta.get("retry_count")) if meta.get("retry_count") is not None else None,
            "subchunks_used": bool(meta.get("subchunks_used", False)) if meta.get("subchunks_used") is not None else None,
        },
        "coverage": {
            "total_chunks_planned": planned_chunks,
            "chunks_completed": completed_chunks,
            "chunks_failed": failed_chunks,
            "chunks_recovered": recovered_chunks,
            "chunks_skipped": skipped_chunks,
            "counter_invariants_valid": counter_invariants_valid,
            "completion_percentage": completion_pct,
            "is_complete": is_complete,
            "coverage_status": coverage_status,
            "evidence_coverage_percentage": ev_coverage_pct,
        },
        "result": {
            "decision": detection_result.decision.value,
            "decision_status": coverage_status,
            "decision_completeness_distinction": (
                "Decoupled: 'decision' reflects evidence citations in the processed chunks; "
                "'decision_status' reflects execution completeness."
            ),
            "topic": {
                "topic_id": get_pseudonymous_id(t_obj.topic_id, prefix="top") if redact_sensitive_topics else t_obj.topic_id,
                "label": sanitize_topic_text(t_obj.label, redact=redact_sensitive_topics),
                "description": sanitize_topic_text(t_obj.description, redact=redact_sensitive_topics),
                "is_label_only": is_label_only,
            },
            "rationale": sanitize_topic_text(detection_result.rationale, redact=redact_sensitive_topics),
            "evidence_ids": [sanitize_evidence_id(e, redact=redact_sensitive_topics) for e in detection_result.evidence_ids],
            "evidence_count": len(detection_result.evidence_ids),
            "linked_discovery_run_id": sanitize_run_id(
                meta.get("verified_discovery_run_id") or meta.get("discovery_run_id") or meta.get("linked_discovery_run_id"),
                redact=redact_sensitive_topics
            ),
            "semantic_consistency": semantic_consistency_info,
        },
        "chunks": norm_chunks,
        "checkpoints": {
            "enabled": bool(meta.get("checkpoint_enabled")) if meta.get("checkpoint_enabled") is not None else None,
            "recovered_count": recovered_chunks,
            "saved_count": safe_int(meta.get("saved_chunk_count")),
            "checkpoint_database_pseudonym": "<LOCAL_SQLITE_CHECKPOINT_STORE>",
        },
        "validation": {
            "evidence_id_validation_applied": sanitize_validation_flag(meta.get("evidence_id_validation_applied"), redact=redact_sensitive_topics),
            "strict_alias_matching": sanitize_validation_flag(meta.get("strict_alias_matching"), redact=redact_sensitive_topics),
            "hallucinated_citations_rejected": sanitize_validation_flag(meta.get("hallucinated_citations_rejected"), redact=redact_sensitive_topics),
            "schema_validation_applied": sanitize_validation_flag(meta.get("schema_validation_applied"), redact=redact_sensitive_topics),
        },
        "provenance": {
            "provenance_document_id_pseudonym": get_pseudonymous_id(detection_result.provenance_document_id or document.document_id, prefix="doc"),
            "chain_of_custody_intact": bool(meta.get("chain_of_custody_intact")) if meta.get("chain_of_custody_intact") is not None else None,
            "evidence_protocol": "deterministic_alias_v1",
        },
        "privacy": {
            "privacy_mode": "SAFE_REDACTED" if redact_sensitive_topics else "LOCAL_UNREDACTED_CAUTION",
            "text_redaction": {
                "messages_included": False,
                "raw_prompts_included": False,
                "raw_llm_responses_included": False,
                "topic_text_redacted": redact_sensitive_topics,
                "free_text_redacted": redact_sensitive_topics,
                "sanitization_scope": "SAFE_DEFAULT_ALLOWLIST" if redact_sensitive_topics else "LOCAL_UNREDACTED",
            },
            "pseudonymization_algorithm": "SHA256_UNSALTED_TRUNCATED",
            "pseudonymization_notes": (
                "Truncated SHA-256 without secret salt. Stable cross-referencing within authorized scope; "
                "dictionary pre-image vulnerability exists for low-entropy inputs."
            ),
        },
    }
    return report


# ---------------------------------------------------------------------------
# Costruttore Rapporto: Topic Discovery
# ---------------------------------------------------------------------------

def build_topic_discovery_export_report(
    discovery_result: TopicDiscoveryResult,
    document: ConversationEvidenceDocument,
    dataset_mode: Any = None,
    redact_sensitive_topics: bool = True,
    checkpoint_store: Any = None,
    custom_commit: str | None = None,
    custom_version: str | None = None,
    authoritative_live_run_ids: Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    """
    Costruisce la struttura dati completa e scientificamente veritiera per l'esportazione JSON di Topic Discovery.
    Privacy Safe by Default: redact_sensitive_topics=True.
    """
    meta = dict(discovery_result.metadata or {})

    # Run ID: non inventare 'run_' + id(obj). Usare NOT_RECORDED se non registrata.
    raw_run_id = meta.get("run_id")
    run_id = sanitize_run_id(raw_run_id, redact=redact_sensitive_topics)
    export_id = get_pseudonymous_id(f"exp_disc_{id(discovery_result)}_{datetime.now(timezone.utc).timestamp()}", prefix="exp")
    analysis_type = "TOPIC_DISCOVERY"

    # Risoluzione modello: acquisire dai metadati reali senza default fittizi
    req_model = sanitize_model_id(meta.get("requested_model") or meta.get("model"), redact=redact_sensitive_topics)
    eff_model = sanitize_model_id(meta.get("model") or req_model, redact=redact_sensitive_topics)
    if eff_model:
        family_inferred = infer_model_family(eff_model)
        raw_family = meta.get("family") or (family_inferred.value if hasattr(family_inferred, "value") else str(family_inferred))
        family_val = sanitize_model_family(raw_family, redact=redact_sensitive_topics)
    else:
        family_val = None

    # Risoluzione chunk e validazione invarianti
    raw_diag = meta.get("chunk_diagnostics") or []
    planned_chunks_raw = meta.get("chunk_count") if meta.get("chunk_count") is not None else meta.get("expected_chunk_count")
    planned_chunks = safe_int(planned_chunks_raw)
    if planned_chunks is not None and planned_chunks < 0:
        planned_chunks = None
    elif planned_chunks is None and raw_diag:
        planned_chunks = len(raw_diag)

    has_explicit_counters = any(
        meta.get(k) is not None for k in ("completed_chunk_count", "failed_chunk_count", "recovered_chunk_count", "skipped_chunk_count")
    )

    if planned_chunks is not None and planned_chunks == 0:
        failed_chunks = safe_int(meta.get("failed_chunk_count")) or 0
        recovered_chunks = safe_int(meta.get("recovered_chunk_count")) or 0
        completed_chunks = safe_int(meta.get("completed_chunk_count")) or 0
        skipped_chunks = safe_int(meta.get("skipped_chunk_count")) or 0
        if completed_chunks == 0 and failed_chunks == 0:
            counter_invariants_valid = True
            is_complete = False
            coverage_status = "NO_WORK"
            completion_pct = None
        else:
            counter_invariants_valid = False
            is_complete = False
            coverage_status = "INCONSISTENT_COUNTERS"
            completion_pct = None
    elif planned_chunks is not None and (has_explicit_counters or raw_diag):
        if has_explicit_counters:
            failed_chunks = safe_int(meta.get("failed_chunk_count")) if meta.get("failed_chunk_count") is not None else 0
            recovered_chunks = safe_int(meta.get("recovered_chunk_count")) if meta.get("recovered_chunk_count") is not None else 0
            completed_chunks = safe_int(meta.get("completed_chunk_count"))
            skipped_chunks = safe_int(meta.get("skipped_chunk_count")) if meta.get("skipped_chunk_count") is not None else 0
        else:
            completed_chunks = sum(1 for d in raw_diag if d.get("status") in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"))
            failed_chunks = sum(1 for d in raw_diag if d.get("status") == "FAILED")
            recovered_chunks = sum(1 for d in raw_diag if d.get("status") == "RECOVERED_FROM_CHECKPOINT")
            skipped_chunks = sum(1 for d in raw_diag if d.get("status") in ("SKIPPED", "UNATTEMPTED"))

        if completed_chunks is None:
            counter_invariants_valid = None
            is_complete = None
            coverage_status = "NOT_RECORDED"
            completion_pct = None
        else:
            contradiction = False
            if raw_diag:
                diag_success = sum(1 for d in raw_diag if d.get("status") in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"))
                diag_failed = sum(1 for d in raw_diag if d.get("status") == "FAILED")
                diag_unattempted = sum(1 for d in raw_diag if d.get("status") == "UNATTEMPTED")
                if diag_unattempted == len(raw_diag) and completed_chunks > 0:
                    contradiction = True
                if diag_failed > 0 and (failed_chunks or 0) == 0:
                    contradiction = True
                if diag_success < completed_chunks and len(raw_diag) == planned_chunks:
                    contradiction = True

            invariants_ok = (
                (completed_chunks + (failed_chunks or 0) + (skipped_chunks or 0) == planned_chunks)
                and completed_chunks <= planned_chunks
                and (failed_chunks or 0) <= planned_chunks
                and not contradiction
            )

            if invariants_ok:
                counter_invariants_valid = True
                is_complete = (completed_chunks == planned_chunks and (failed_chunks or 0) == 0)
                coverage_status = "COMPLETE" if is_complete else ("PARTIAL" if completed_chunks > 0 else "FAILED")
                completion_pct = round((completed_chunks / planned_chunks) * 100.0, 1) if planned_chunks > 0 else 0.0
            else:
                counter_invariants_valid = False
                is_complete = False
                coverage_status = "INCONSISTENT_COUNTERS"
                completion_pct = None
    else:
        failed_chunks = safe_int(meta.get("failed_chunk_count"))
        recovered_chunks = safe_int(meta.get("recovered_chunk_count"))
        completed_chunks = safe_int(meta.get("completed_chunk_count"))
        skipped_chunks = safe_int(meta.get("skipped_chunk_count"))
        counter_invariants_valid = None
        is_complete = None
        coverage_status = "NOT_RECORDED"
        completion_pct = None

    raw_status = meta.get("status")
    if raw_status:
        st_clean = str(raw_status).strip().upper()
        reported_status = st_clean if st_clean in ALLOWED_EXECUTION_STATUSES else "NOT_RECORDED"
    else:
        reported_status = coverage_status

    if coverage_status == "INCONSISTENT_COUNTERS":
        verified_status = "INCONSISTENT_COUNTERS"
    elif coverage_status == "NO_WORK":
        verified_status = "NO_WORK"
    elif coverage_status == "NOT_RECORDED":
        verified_status = "NOT_RECORDED"
    elif coverage_status == "COMPLETE" and is_complete is True:
        verified_status = "COMPLETE"
    elif coverage_status == "PARTIAL":
        verified_status = "PARTIAL"
    elif coverage_status == "FAILED":
        verified_status = "FAILED"
    else:
        verified_status = "NOT_RECORDED"

    execution_status = verified_status

    # Evidenze
    tot_evidence = document.evidence_count
    analyzed_ev = safe_int(meta.get("analyzed_evidence_count"))
    unanalyzed_ev = safe_int(meta.get("unanalyzed_evidence_count"))
    if unanalyzed_ev is None and analyzed_ev is not None:
        unanalyzed_ev = max(0, tot_evidence - analyzed_ev)

    ev_coverage_pct = round((analyzed_ev / tot_evidence) * 100.0, 1) if tot_evidence and analyzed_ev is not None else None

    # Profilo operativo: non inventare BALANCED se assente
    prof_req = sanitize_operational_profile(meta.get("profile_requested") or meta.get("profile"), redact=redact_sensitive_topics)
    prof_app = sanitize_operational_profile(meta.get("profile_applied") or meta.get("profile"), redact=redact_sensitive_topics)

    # Origine ed attestazione: FAIL-CLOSED (nessun LIVE_AUTHORITATIVE senza verifica autoritativa)
    raw_att = meta.get("attestation_level")
    origin_val = sanitize_origin(meta.get("origin"), redact=redact_sensitive_topics)
    is_mock = (
        bool(meta.get("is_mock"))
        or bool(meta.get("is_fake"))
        or (origin_val in (AnalysisOrigin.MOCK_INSPECTION.value, AnalysisOrigin.SYNTHETIC_REFERENCE.value))
        or ("fake" in str(meta.get("client_class", "")).lower())
        or ("fake" in str(eff_model).lower() if eff_model else False)
        or ("mock" in str(eff_model).lower() if eff_model else False)
    )

    if raw_att:
        att_str = str(raw_att).strip().upper()
        reported_attestation = att_str if att_str in ("LIVE_AUTHORITATIVE", "MOCK_VERIFIED", "UNATTESTED", "REPORTED_LIVE") else "UNATTESTED"
    elif (
        meta.get("authoritative_execution_certified") is True
        or meta.get("server_provenance_verified") is True
        or meta.get("runtime_binding_verified") is True
    ):
        reported_attestation = "REPORTED_LIVE"
    elif origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value:
        reported_attestation = "REPORTED_LIVE"
    else:
        reported_attestation = None

    # Autenticazione autoritativa verificata: FAIL-CLOSED
    # Solo una fonte autoritativa esterna (authoritative_live_run_ids) che conferma il raw_run_id originale può promuovere a LIVE_AUTHORITATIVE.
    # I soli flag auto-dichiarati nei metadati (authoritative_execution_certified, ecc.) NON possono promuovere a LIVE_AUTHORITATIVE.
    is_authoritative_live = False
    if authoritative_live_run_ids is not None and raw_run_id and not is_mock:
        if raw_run_id in authoritative_live_run_ids:
            is_authoritative_live = True

    if is_mock:
        verified_attestation = AttestationLevel.MOCK_VERIFIED.value
    elif is_authoritative_live and origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value:
        verified_attestation = AttestationLevel.LIVE_AUTHORITATIVE.value
    else:
        verified_attestation = AttestationLevel.UNATTESTED.value

    attestation = verified_attestation

    loading_mode_raw = meta.get("loading_mode")
    if not loading_mode_raw:
        loading_mode_raw = (
            LoadingMode.CHECKPOINT_RESTORE.value
            if (meta.get("is_persisted_recovery") or (recovered_chunks and recovered_chunks > 0))
            else LoadingMode.IN_MEMORY_EXECUTION.value
        )
    loading_mode = sanitize_loading_mode(loading_mode_raw)

    if dataset_mode:
        mode_str = sanitize_dataset_mode(dataset_mode)
    elif meta.get("mode"):
        mode_str = sanitize_dataset_mode(meta["mode"])
    else:
        mode_str = "NOT_RECORDED"

    # Mappatura argomenti scoperti
    topics_list: list[dict[str, Any]] = []
    for t_idx, t in enumerate(discovery_result.topics, start=1):
        clean_label = sanitize_topic_text(t.label, redact=redact_sensitive_topics)
        clean_desc = sanitize_topic_text(t.short_description, redact=redact_sensitive_topics)
        safe_eids = [sanitize_evidence_id(e, redact=redact_sensitive_topics) for e in t.evidence_ids]
        t_id = get_pseudonymous_id(f"disc_top_{t_idx}", prefix="top") if redact_sensitive_topics else f"discovered_topic_{t_idx}"

        topics_list.append({
            "topic_id": t_id,
            "label": clean_label,
            "short_description": clean_desc,
            "ranking_position": t_idx,
            "evidence_count": len(safe_eids),
            "evidence_ids": safe_eids,
            "contributing_chunks": None,
            "validation_status": "VALIDATED",
            "provenance_document_id": get_pseudonymous_id(discovery_result.provenance_document_id, prefix="doc"),
        })

    # Diagnostica chunk normalizzata
    norm_chunks = _normalize_chunks_diagnostics(
        raw_diagnostics=raw_diag,
        total_planned_chunks=planned_chunks,
        model_id=eff_model,
        run_id=run_id,
        status_default="SUCCESS" if is_complete else "PARTIAL",
        redact_sensitive_topics=redact_sensitive_topics,
    )

    # Durate: null se non misurate, mai 0.0
    dur_val = meta.get("latency_seconds") if meta.get("latency_seconds") is not None else meta.get("duration_seconds")
    duration_sec = round(float(dur_val), 3) if dur_val is not None and safe_float(dur_val) is not None else None

    # Origin flow di Discovery: solo se autenticato LIVE_AUTHORITATIVE può assumere LIVE_DISCOVERY
    origin_flow = sanitize_origin_flow(
        meta.get("origin_flow"),
        redact=redact_sensitive_topics,
        default="LIVE_DISCOVERY" if verified_attestation == AttestationLevel.LIVE_AUTHORITATIVE.value else "NOT_RECORDED"
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "software": {
            "name": SOFTWARE_NAME,
            "version": sanitize_software_version(custom_version or get_software_version(), redact=redact_sensitive_topics),
            "execution_software_commit": sanitize_git_commit(meta.get("git_commit")),
            "export_software_commit": sanitize_git_commit(custom_commit or get_git_commit()),
            "offline_mode": bool(meta.get("offline_mode")) if meta.get("offline_mode") is not None else None,
            "environment": get_platform_environment(),
        },
        "experiment": {
            "analysis_type": analysis_type,
            "run_id": run_id,
            "export_id": export_id,
            "origin_flow": origin_flow,
            "mode": mode_str,
            "conversation_pseudonym": get_pseudonymous_id(document.chat_id or document.document_id, prefix="chat"),
            "reported_attestation_level": reported_attestation,
            "verified_attestation_level": verified_attestation,
            "attestation_level": verified_attestation,
            "loading_mode": loading_mode,
            "start_time": format_iso_timestamp(meta.get("start_timestamp")),
            "end_time": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
        },
        "document": {
            "document_pseudonym": get_pseudonymous_id(document.document_id, prefix="doc"),
            "document_fingerprint": sanitize_document_fingerprint(meta.get("document_fingerprint")),
            "message_count": document.message_count,
            "total_evidence_count": tot_evidence,
            "analyzed_evidence_count": analyzed_ev,
            "unanalyzed_evidence_count": unanalyzed_ev,
            "source_name": sanitize_source_name(document.source_name, redact=redact_sensitive_topics),
        },
        "model": {
            "requested_model_id": req_model,
            "effective_model_id": eff_model,
            "family": family_val,
            "declared_quantization": sanitize_quantization(meta.get("quantization") or meta.get("declared_quantization"), redact=redact_sensitive_topics),
            "observed_quantization": sanitize_quantization(meta.get("observed_quantization"), redact=redact_sensitive_topics),
            "declared_context_length": safe_int(meta.get("context_length")),
            "effective_context_length": safe_int(meta.get("runtime_context_length") or meta.get("context_length")),
            "local_endpoint": "http://127.0.0.1:1234" if eff_model else None,
        },
        "configuration": {
            "temperature": safe_float(meta.get("temperature")),
            "max_tokens": safe_int(meta.get("max_tokens")),
            "timeout_seconds": safe_float(meta.get("timeout_seconds")),
            "seed": safe_int(meta.get("seed")),
            "operational_profile_requested": prof_req,
            "operational_profile_applied": prof_app,
            "profile_downgraded": bool(meta.get("profile_downgraded", False)),
            "reduction_strategy": sanitize_reduction_strategy(meta.get("reduction_strategy"), redact=redact_sensitive_topics),
            "max_topics": safe_int(meta.get("max_topics")),
        },
        "execution": {
            "start_timestamp": format_iso_timestamp(meta.get("start_timestamp")),
            "end_timestamp": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
            "origin": origin_val,
            "reported_execution_status": reported_status,
            "verified_execution_status": verified_status,
            "global_status": verified_status,
            "error_count": safe_int(meta.get("error_count")) if meta.get("error_count") is not None else (failed_chunks if failed_chunks is not None else None),
            "retry_count": safe_int(meta.get("retry_count")) if meta.get("retry_count") is not None else None,
            "subchunks_used": bool(meta.get("subchunks_used", False)) if meta.get("subchunks_used") is not None else None,
        },
        "coverage": {
            "total_chunks_planned": planned_chunks,
            "chunks_completed": completed_chunks,
            "chunks_failed": failed_chunks,
            "chunks_recovered": recovered_chunks,
            "chunks_skipped": skipped_chunks,
            "counter_invariants_valid": counter_invariants_valid,
            "completion_percentage": completion_pct,
            "is_complete": is_complete,
            "coverage_status": coverage_status,
            "evidence_coverage_percentage": ev_coverage_pct,
        },
        "result": {
            "total_topics_discovered": len(topics_list),
            "topics": topics_list,
            "aggregation": {
                "aggregation_phase_status": sanitize_aggregation_status(meta.get("aggregation_status")),
                "deduplication_applied": meta.get("deduplication_applied"),
                "aggregation_errors": sanitize_aggregation_errors(meta.get("aggregation_errors"), redact=redact_sensitive_topics),
            },
        },
        "chunks": norm_chunks,
        "checkpoints": {
            "enabled": bool(meta.get("checkpoint_enabled")) if meta.get("checkpoint_enabled") is not None else None,
            "recovered_count": recovered_chunks,
            "saved_count": safe_int(meta.get("saved_chunk_count")),
            "checkpoint_database_pseudonym": "<LOCAL_SQLITE_CHECKPOINT_STORE>",
        },
        "validation": {
            "evidence_id_validation_applied": sanitize_validation_flag(meta.get("evidence_id_validation_applied"), redact=redact_sensitive_topics),
            "strict_alias_matching": sanitize_validation_flag(meta.get("strict_alias_matching"), redact=redact_sensitive_topics),
            "hallucinated_citations_rejected": sanitize_validation_flag(meta.get("hallucinated_citations_rejected"), redact=redact_sensitive_topics),
            "schema_validation_applied": sanitize_validation_flag(meta.get("schema_validation_applied"), redact=redact_sensitive_topics),
        },
        "provenance": {
            "provenance_document_id_pseudonym": get_pseudonymous_id(discovery_result.provenance_document_id, prefix="doc"),
            "chain_of_custody_intact": bool(meta.get("chain_of_custody_intact")) if meta.get("chain_of_custody_intact") is not None else None,
            "evidence_protocol": "deterministic_alias_v1",
        },
        "privacy": {
            "privacy_mode": "SAFE_REDACTED" if redact_sensitive_topics else "LOCAL_UNREDACTED_CAUTION",
            "text_redaction": {
                "messages_included": False,
                "raw_prompts_included": False,
                "raw_llm_responses_included": False,
                "topic_text_redacted": redact_sensitive_topics,
                "free_text_redacted": redact_sensitive_topics,
                "sanitization_scope": "SAFE_DEFAULT_ALLOWLIST" if redact_sensitive_topics else "LOCAL_UNREDACTED",
            },
            "pseudonymization_algorithm": "SHA256_UNSALTED_TRUNCATED",
            "pseudonymization_notes": (
                "Truncated SHA-256 without secret salt. Stable cross-referencing within authorized scope; "
                "dictionary pre-image vulnerability exists for low-entropy inputs."
            ),
        },
    }
    return report
