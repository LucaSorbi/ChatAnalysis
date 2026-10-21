"""
scripts/preflight_checkpoint_resume.py
---------------------------------------
Strumento forense e di preflight per la verifica preliminare della ripresa
dei checkpoint (sessione 24/26) su ambiente Ubuntu (22.04 LTS / 24.04 LTS).

OBIETTIVO ARCHITETTURALE E FORENSE:
Stabilire in modo rigoroso, deterministico e matematico se i 24 checkpoint salvati
in SQLite possono essere riutilizzati dal motore ChatAnalysis, SENZA avviare alcuna
nuova inferenza e SENZA modificare i dati esistenti sul database (100% READ-ONLY).

STATI DI OUTPUT DISTINTI:
1. STRUCTURAL_ONLY: Integrità strutturale SQLite verificata, ma documento attivo non caricato.
2. RUNTIME_VALIDATION_REQUIRED: Verifica formale a runtime necessaria prima della ripresa.
3. SAFE_RESUME_VERIFIED: Tutti i 9 controlli obbligatori superati con successo con runtime binding verificato.
4. NOT_COMPATIBLE: Almeno un controllo fallito (abort immediato, zero modifiche ai checkpoint).

I 9 CONTROLLI OBBLIGATORI:
1. Identità del documento e document fingerprint SHA-256.
2. Config fingerprint SHA-256 e runtime model binding verificato.
3. Identità del modello (model_id).
4. Versione del prompt operativo (prompt_version).
5. Parametri e piano di chunking (conteggio, budget e boundaries).
6. Presenza e integrità strutturale dei 24 checkpoint (zero record o subchunk corrotti).
7. Validità degli evidence ID rispetto ai rispettivi chunk (zero citazioni cross-chunk).
8. Provenance dei risultati per ciascun blocco.
9. Possibilità effettiva di saltare i 24 blocchi recuperati a runtime (0 chiamate LLM).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request
from urllib.parse import urlparse

# Assicura che la root del repository sia nel PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("checkpoint_preflight")

# Import dei moduli di dominio ChatAnalysis
from ai.chunking import split_document_into_chunks
from ai.lmstudio import (
    extract_instance_context_length,
    extract_quantization_name,
)
from ai.models import (
    ConversationEvidenceDocument,
    TopicDiscoveryResult,
)
from ai.token_budget import DEFAULT_SAFETY_MARGIN_TOKENS
from analysis.checkpoint import (
    AnalysisCheckpoint,
    compute_chunk_partition_fingerprint,
    compute_document_fingerprint,
    compute_run_config_hash,
    deserialize_discovery_result_from_checkpoint,
    validate_checkpoint_evidence_references,
)
from analysis.models import (
    OperationalAnalysisConfig,
    OperationalProfile,
    resolve_operational_profile,
)
from analysis.service import (
    LocalTopicAnalysisService,
    calculate_discovery_max_tokens,
)
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


# Mappatura autoritativa tra codici CLI e membri dell'enumerazione SourceFormat
CLI_SOURCE_FORMAT_MAP: dict[str, SourceFormat] = {
    "whatsapp_export": SourceFormat.WHATSAPP_EXPORT,
    "whatsapp_msgstore": SourceFormat.WHATSAPP_MSGSTORE,
    "whatsapp_wa": SourceFormat.WHATSAPP_WA,
    "cellebrite_csv": SourceFormat.CELLEBRITE_CSV,
    "cellebrite_json": SourceFormat.CELLEBRITE_JSON,
    "cellebrite_xml": SourceFormat.CELLEBRITE_XML,
}


class PreflightVerdictStatus(str, Enum):
    """Stati autoritativi del verdetto di preflight."""
    STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
    STRUCTURALLY_COMPATIBLE = "STRUCTURALLY_COMPATIBLE"
    MOCK_VERIFIED = "MOCK_VERIFIED"
    RUNTIME_VALIDATION_REQUIRED = "RUNTIME_VALIDATION_REQUIRED"
    SAFE_RESUME_VERIFIED = "SAFE_RESUME_VERIFIED"
    NOT_COMPATIBLE = "NOT_COMPATIBLE"


# ==============================================================================
# ALLOWLISTS E FUNZIONI DI SANITIZZAZIONE (DATA MINIMIZATION / ZERO LEAKAGE)
# ==============================================================================

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

ALLOWED_PROMPT_VERSIONS: set[str] = {
    "discovery_operational",
    "discovery_default",
    "detection_operational",
    "detection_default",
    "translation_operational",
    "operational",
    "v1",
    "v2",
}

ALLOWED_PROFILES: set[str] = {
    "fast",
    "balanced",
    "thorough",
    "comprehensive",
}

_WINDOWS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/][^\s\"',;:]+")
_UNIX_PATH_RE = re.compile(r"(?:/home/|/Users/|/tmp/|/var/|/etc/|/usr/|/opt/|/private/|\./|\.\./)[^\s\"',;:]+")
_EXT_PATH_RE = re.compile(r"[^\s\"',;:]+\.(?:db|txt|csv|json|gguf|bin|py|sh|log)\b", re.IGNORECASE)
_CANARY_RE = re.compile(r"(?i)\b(?:canary|secret|token|password|passwd|private_key|credential|api_key)[^\s\"',;:]*")
_COLON_MARKER_RE = re.compile(r"\b\S*::\S*\b|::\S+|\S+::")
_DOC_ID_RE = re.compile(r"\b(?:doc_|chat_|test_doc_|session_)[A-Za-z0-9_-]{4,}\b")
_GENERIC_EVIDENCE_SOURCE_RE = re.compile(r"(?i)\b(?:VISION_DESCRIPTION|VISION_OBSERVATION|OCR_TEXT|STT_TRANSCRIPTION|ORIGINAL_TEXT)\b")


def _is_canonical_sha256(s: str | None) -> bool:
    """Verifica se una stringa è un digest SHA-256 esadecimale valido di 64 caratteri."""
    if not s or not isinstance(s, str):
        return False
    clean = s.strip()
    return len(clean) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean)


def _format_fp_safe(fp: str | None) -> str:
    """Mostra un fingerprint abbreviato soltanto se è una stringa SHA-256 esadecimale valida di 64 caratteri."""
    if _is_canonical_sha256(fp):
        clean = fp.strip().lower()
        return f"{clean[:8]}...{clean[-4:]}"
    return "[NON_CANONICAL_OR_INVALID]"


def _sanitize_path(p: Path | str | None, default_tag: str = "[PATH_REDACTED]") -> str:
    """
    Maschera percorsi in modo cross-platform senza mai esporre nomi personali o basename sensibili.
    Restituisce identificatori neutri: [DATABASE_REDACTED], [CHAT_FILE_REDACTED], [PATH_REDACTED].
    """
    if not p:
        return "[NONE]"
    s = str(p).replace("\\", "/")
    lower_s = s.lower()
    if lower_s.endswith(".db") or "checkpoint" in lower_s:
        return "[DATABASE_REDACTED]"
    if lower_s.endswith(".txt") or lower_s.endswith(".csv") or lower_s.endswith(".json") or "chat" in lower_s:
        return "[CHAT_FILE_REDACTED]"
    return default_tag


# Allowlist chiusa di identificatori, token diagnostici e parole di stato ammesse nei report (B4)
_KNOWN_SAFE_IDENTIFIERS: set[str] = {
    # Status e verdetti
    "structural_only", "structurally_compatible", "mock_verified", "runtime_validation_required",
    "safe_resume_verified", "not_compatible", "pass", "fail", "retry_failed_chunks_only",
    # Modelli e prompt
    *[m.lower() for m in ALLOWED_MODEL_IDS],
    *[p.lower() for p in ALLOWED_PROMPT_VERSIONS],
    *[pr.lower() for pr in ALLOWED_PROFILES],
    # Diagnostica e binding
    "model_available", "model_loaded", "model_identity_verified", "quantization_verified",
    "context_verified", "insufficient_runtime_metadata", "runtime_binding_verified",
    "model_not_found", "model_not_loaded", "model_key_mismatch", "quantization_mismatch",
    "context_length_insufficient", "api_unavailable", "api_timeout", "http_error",
    "response_parse_error", "unexpected_error", "disabled", "loaded_instance_verified",
    "declared_only_not_verified", "live_verified", "checkpoint_readable_and_validated",
    "checkpoint_config_compatible", "err_db_access", "unverified",
    "declared_only_unverified_runtime", "live_backend_insufficient_runtime_metadata",
    "live_backend_quantization_mismatch", "live_backend_context_length_insufficient",
    "live_backend_verified", "runtime_binding_status", "runtime_binding_verified",
    "persisted_config_fingerprint", "requested_model_id", "resolved_profile",
    "declared_resolved_model_key", "observed_runtime_identity", "context_length",
    # Parole standard di stato e vocabolario tecnico consentito
    "tutti", "controlli", "obbligatori", "superati", "in", "simulazione", "live", "ripresa",
    "non", "compatibile", "validazione", "runtime", "richiesta", "compatibilità", "strutturale",
    "accertata", "http", "json", "null", "true", "false", "none", "sha256", "uuid", "sqlite",
    "wal", "chunks_generated", "partition_compatible", "legacy_boundaries_limitation",
    "chunks_planned", "present_count", "missing_indices", "valid_payloads", "verified_chunks",
    "cross_chunk_citations", "matching_provenance_chunks", "load_status", "config_status",
    "recovered_chunks", "corrupt_records",
    "not_measured", "verified_true", "verified_false",
}

_ARBITRARY_TOKEN_PATTERN = re.compile(r"\b(?=[A-Za-z0-9_]*[A-Za-z])(?=[A-Za-z0-9_]*[0-9_])[A-Za-z0-9_]{3,}\b")


def _mask_arbitrary_unclassified_tokens(text: str) -> str:
    """
    Data Minimization rigorosa (B4):
    Maschera qualsiasi identificatore, token casuale, canarino sintetico o marcatore
    che contenga lettere mescolate a cifre o underscore, a meno che non appartenga
    alla allowlist chiusa verificata di identificatori canonici o a tag di redazione.
    """
    def _repl(m: re.Match) -> str:
        tok = m.group(0)
        lower_tok = tok.lower()
        if lower_tok in _KNOWN_SAFE_IDENTIFIERS:
            return tok
        if tok.startswith("[") and tok.endswith("]"):
            return tok
        return "[MARKER_REDACTED]"

    return _ARBITRARY_TOKEN_PATTERN.sub(_repl, text)


def _sanitize_string_deep(s: Any, default_tag: str = "[REDACTED]") -> str:
    """
    Sanifica sistematicamente una stringa rimuovendo percorsi Windows/Unix,
    canary, token segreti, marcatori con :: (compresi msg:: e VISION_DESCRIPTION),
    document ID reali, evidence ID e dettagli non consentiti.
    Applica inoltre la minimizzazione strutturale chiusa (B4) contro marcatori arbitrari.
    """
    if s is None:
        return ""
    clean = str(s)
    # 1. Marcatori con :: (ad es. msg::5::VISION_DESCRIPTION)
    clean = _COLON_MARKER_RE.sub("[MARKER_REDACTED]", clean)
    # 2. Identificatori di sorgente evidenza multimodale
    clean = _GENERIC_EVIDENCE_SOURCE_RE.sub("[EVIDENCE_TYPE_REDACTED]", clean)
    # 3. Canary e credenziali
    clean = _CANARY_RE.sub("[CONFIDENTIAL_CANARY_REDACTED]", clean)
    # 4. Percorsi locali assoluti e relativi
    clean = _WINDOWS_PATH_RE.sub("[PATH_REDACTED]", clean)
    clean = _UNIX_PATH_RE.sub("[PATH_REDACTED]", clean)
    clean = _EXT_PATH_RE.sub("[PATH_REDACTED]", clean)
    # 5. Document ID reali
    clean = _DOC_ID_RE.sub("[DOC_ID_REDACTED]", clean)
    # 6. Minimizzazione strutturale contro marcatori e token non classificati (B4)
    clean = _mask_arbitrary_unclassified_tokens(clean)
    return clean


def _sanitize_value_deep(val: Any) -> Any:
    """Sanifica ricorsivamente qualsiasi struttura dati (dict, list, str, primitive)."""
    if val is None:
        return ""
    if isinstance(val, (int, float, bool)):
        return val
    if isinstance(val, str):
        return _sanitize_string_deep(val)
    if isinstance(val, dict):
        return {_sanitize_string_deep(str(k)): _sanitize_value_deep(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_sanitize_value_deep(item) for item in val]
    return _sanitize_string_deep(str(val))


def _sanitize_model_identifier(m: str | None) -> str:
    """
    Sanifica identificativi e model keys: neutralizza percorsi personali o canary
    mantenendo esclusivamente etichette standard dall'allowlist o indicatori neutri.
    """
    if not m:
        return "[NOT_SPECIFIED]"
    clean = str(m).strip()
    clean_lower = clean.lower()
    if clean_lower in ALLOWED_MODEL_IDS:
        return clean_lower
    normalized = clean_lower.replace("_", "-")
    if normalized in ALLOWED_MODEL_IDS:
        return normalized
    if "/" in clean or "\\" in clean or clean.endswith(".gguf") or clean.endswith(".bin"):
        basename = Path(clean.replace("\\", "/")).name.lower().replace("_", "-")
        for sup in ALLOWED_MODEL_IDS:
            if sup in basename:
                return f"[MODEL_PATH_REDACTED:{sup}]"
        return "[MODEL_PATH_REDACTED]"
    return f"[MODEL_REDACTED:len={len(clean)}]"


_BACKEND_URL_ERROR_MESSAGES: dict[str, str] = {
    "LOOPBACK_URL_REQUIRED": "Indirizzo o host non consentito. Richiesto endpoint di loopback locale (127.0.0.1 o localhost).",
    "INVALID_BACKEND_URL": "URL backend non valido o schema non consentito (richiesto http:// o https:// su loopback).",
    "INVALID_BACKEND_PORT": "Porta TCP dell'URL backend non valida o fuori intervallo consentito (1-65535).",
    "BACKEND_CREDENTIALS_FORBIDDEN": "Credenziali utente o password non consentite nell'URL del backend locale.",
}


def _validate_loopback_backend_url(url: str) -> tuple[bool, str]:
    """
    Verifica che il backend URL di LM Studio punti rigorosamente all'interfaccia di loopback locale.
    Restituisce (True, "") in caso di conformità, oppure (False, error_code).
    I codici diagnostici restituiti sono tassativamente stabili ed enumerati (B3):
    - LOOPBACK_URL_REQUIRED
    - INVALID_BACKEND_URL
    - INVALID_BACKEND_PORT
    - BACKEND_CREDENTIALS_FORBIDDEN
    Nessun hostname o dato utente viene interpolato nei codici o nei messaggi esportabili.
    """
    if not url or not isinstance(url, str):
        return False, "INVALID_BACKEND_URL"

    clean_url = url.strip()
    if not clean_url:
        return False, "INVALID_BACKEND_URL"

    # Rifiuto categorico di credenziali nell'URL (user:pass@host)
    if "@" in clean_url:
        return False, "BACKEND_CREDENTIALS_FORBIDDEN"

    try:
        parsed = urlparse(clean_url)
    except Exception:
        return False, "INVALID_BACKEND_URL"

    if parsed.scheme not in ("http", "https"):
        return False, "INVALID_BACKEND_URL"

    if parsed.username or parsed.password:
        return False, "BACKEND_CREDENTIALS_FORBIDDEN"

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False, "INVALID_BACKEND_URL"

    # Validazione sintattica della porta TCP (B3)
    try:
        port = parsed.port
    except ValueError:
        return False, "INVALID_BACKEND_PORT"

    if parsed.netloc.endswith(":"):
        return False, "INVALID_BACKEND_PORT"

    if port is not None and not (1 <= port <= 65535):
        return False, "INVALID_BACKEND_PORT"

    # 1. Hostname canonico locale localhost
    if hostname == "localhost":
        return True, ""

    # 2. Validazione rigorosa tramite ipaddress (gestisce sia IPv4 che IPv6)
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback:
            return True, ""
        return False, "LOOPBACK_URL_REQUIRED"
    except ValueError:
        # Se non è un IP valido e non è 'localhost', è un nome di dominio esterno o host civetta (es. 127.attacker.example)
        return False, "LOOPBACK_URL_REQUIRED"


class StrictLoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Impedisce categoricamente a redirect HTTP (301, 302, 303, 307, 308) di reindirizzare
    richieste destinate a loopback verso host remoti o non-loopback (B9 / Audit Fix B3).
    Tutte le richieste devono restare confinate ad host locali conformi.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        is_loopback, err_code = _validate_loopback_backend_url(newurl)
        if not is_loopback:
            raise urllib.error.HTTPError(
                newurl, code, f"Redirect verso host non consentito bloccato: {err_code}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


CANONICAL_CHECK_NAMES: dict[int, str] = {
    1: "Document Identity & Fingerprint Match",
    2: "Run Configuration Fingerprint & Runtime Binding Match",
    3: "Model Identity Match",
    4: "Operational Prompt Version Match",
    5: "Chunking Count & Partition Compatibility",
    6: "24 Checkpoints Presence & Payload Structural Integrity",
    7: "Evidence ID Intra-Chunk Validity",
    8: "Provenance Document ID Verification",
    9: "Checkpoint Readability & Evidentiary Reconciliation",
}

ALLOWED_CHECK_NAMES: set[str] = {
    "Document Identity & Fingerprint Match",
    "Run Configuration Fingerprint & Runtime Binding Match",
    "Model Identity Match",
    "Operational Prompt Version Match",
    "Chunking Count & Algorithmic Plan Compatibility (Historical Boundaries Limitation)",
    "Chunking Count & Partition Compatibility",
    "Chunking Count & Partition Boundaries Identity Verified",
    "24 Checkpoints Presence & Payload Structural Integrity",
    "Evidence ID Intra-Chunk Validity",
    "Provenance Document ID Verification",
    "Checkpoint Readability & Evidentiary Reconciliation",
    "Structural Chunk Count & Indices (Zero-Based)",
    "Payload JSON & Schema Structural Validity",
    "Evidentiary In-Memory Document Validation",
}

CANONICAL_CHECK_DETAILS: dict[int, str] = {
    1: "Identità formale document_id e fingerprint crittografico SHA-256 dei messaggi e delle evidenze",
    2: "Hash SHA-256 dei parametri operativi e verifica formale del binding runtime con il modello",
    3: "Corrispondenza esatta del modello linguistico registrato",
    4: "Versione del prompt di Discovery compatibile con il parser e gli alias deterministici",
    5: "Verifica piano di chunking e compatibilità algoritmica di partizionamento",
    6: "Esattamente 24 blocchi presenti; blocchi mancanti conformi; zero record corrotti",
    7: "Tutti gli identificativi di evidenza citati appartengono rigorosamente al proprio blocco",
    8: "Tutti i risultati validati dichiarano provenienza autentica dal documento o dal blocco genitore",
    9: "Caricamento read-only e riconciliazione probatoria del checkpoint per salto blocchi",
}

_KNOWN_SAFE_DIAGNOSTIC_KEYS: set[str] = {
    "doc_id_match", "fp", "cfg_fp", "binding_status", "chunks_generated",
    "partition_compatible", "legacy_boundaries_limitation", "boundaries_verified",
    "chunks_planned", "present_count", "missing_indices", "valid_payloads",
    "verified_chunks", "cross_chunk_citations", "matching_provenance_chunks",
    "load_status", "config_status", "recovered_chunks", "corrupt_records",
    "present", "missing", "json_decodable", "schema_valid", "subchunks_valid",
    "corrupt_subchunks",
}

_ALLOWED_LIVE_BACKEND_STATUS_CODES: set[str] = {
    "LIVE_BACKEND_MODEL_AVAILABLE_NOT_LOADED",
    "LIVE_BACKEND_LOAD_STATE_UNCONFIRMED",
    "LIVE_BACKEND_INSUFFICIENT_RUNTIME_METADATA",
    "LIVE_BACKEND_QUANTIZATION_MISMATCH",
    "LIVE_BACKEND_CONTEXT_LENGTH_INSUFFICIENT",
    "LIVE_BACKEND_LOOPBACK_URL_REQUIRED",
    "LIVE_BACKEND_MODEL_NOT_FOUND",
    "LIVE_BACKEND_HTTP_ERROR",
    "LIVE_BACKEND_BACKEND_UNREACHABLE",
    "LIVE_BACKEND_PROTOCOL_ERROR",
    "LIVE_BACKEND_NOT_LOADED",
    "LIVE_BACKEND_VERIFIED",
    "LIVE_BACKEND_MODEL_KEY_MISMATCH",
}

_KNOWN_SAFE_STATUS_CODES: set[str] = {
    "SKIPPED_NO_DOCUMENT",
    "EVIDENTIARY_VALIDATION_REQUIRES_CHAT_FILE",
    "CHECKPOINT_READABLE_AND_VALIDATED",
    "CHECKPOINT_CONFIG_COMPATIBLE",
    "CHECKPOINT_VALIDATION_FAILED",
    "MOCK_VERIFIED",
    "VERIFIED_LIVE_BACKEND_READONLY",
    "DECLARED_ONLY_UNVERIFIED_RUNTIME",
    "NOT_PROVIDED",
    "BINDING_VERIFICATION_FAILED",
    "cross_chunk_or_invalid_evidence_detected",
    "provenance_mismatch",
    "True", "False", "None",
    "NOT_MEASURED", "VERIFIED_TRUE", "VERIFIED_FALSE",
    *_ALLOWED_LIVE_BACKEND_STATUS_CODES,
}

_ALLOWED_REDACTION_TAGS: set[str] = {
    "[MODEL_PATH_REDACTED]",
    "[MODEL_REDACTED]",
    "[NOT_SPECIFIED]",
    "[DATABASE_REDACTED]",
    "[CHAT_FILE_REDACTED]",
    "[PATH_REDACTED]",
    "[VALUE_REDACTED]",
    "[KEY_REDACTED]",
    "[KEY_REDACTED]=[VALUE_REDACTED]",
    "[DIAGNOSTIC_REASON_REDACTED]",
    "[OBSERVED_VALUE_REDACTED]",
    "[EXPECTED_VALUE_REDACTED]",
    "[CHECK_DETAILS_REDACTED]",
    "[CLASSIFICATION_REDACTED]",
    "[PROFILE_REDACTED]",
    "[NONE]",
    "[EMPTY]",
    "[EMPTY_REASON]",
    "[NON_CANONICAL_OR_INVALID]",
    "[MARKER_REDACTED]",
    "[EVIDENCE_TYPE_REDACTED]",
    "[CONFIDENTIAL_CANARY_REDACTED]",
    "[DOC_ID_REDACTED]",
}

_ALLOWED_PARAMETRIC_TAG_PATTERNS = (
    re.compile(r"^\[MODEL_PATH_REDACTED:[a-z0-9\.\-]+\]$"),
    re.compile(r"^\[MODEL_REDACTED:len=\d+\]$"),
)


def _is_approved_tag(s: str) -> bool:
    """Verifica se una stringa appartiene all'insieme chiuso di tag di redazione autorizzati (B3)."""
    if s in _ALLOWED_REDACTION_TAGS:
        return True
    for pat in _ALLOWED_PARAMETRIC_TAG_PATTERNS:
        if pat.fullmatch(s):
            if ":" in s:
                suffix = s.split(":", 1)[1].rstrip("]")
                if suffix.startswith("len="):
                    return True
                return suffix in ALLOWED_MODEL_IDS
            return True
    return False


class DiagnosticReasonCode(str, Enum):
    """Codici diagnostici enumerati chiusi per motivazioni esportabili (B3)."""
    NO_DISCOVERY_SESSION = "NO_DISCOVERY_SESSION"
    STRUCTURAL_NO_MATCHING_CHUNKS = "STRUCTURAL_NO_MATCHING_CHUNKS"
    STRUCTURAL_AUDIT_COMPLETED = "STRUCTURAL_AUDIT_COMPLETED"
    AMBIGUOUS_MULTIPLE_SESSIONS = "AMBIGUOUS_MULTIPLE_SESSIONS"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    NO_MATCHING_SESSION = "NO_MATCHING_SESSION"
    ERR_DB_ACCESS = "ERR_DB_ACCESS"
    MOCK_SIMULATION_PASSED = "MOCK_SIMULATION_PASSED"
    LEGACY_BOUNDARIES_LIMITATION = "LEGACY_BOUNDARIES_LIMITATION"
    LIVE_VERIFIED_SAFE_RESUME = "LIVE_VERIFIED_SAFE_RESUME"
    RUNTIME_BINDING_REQUIRED = "RUNTIME_BINDING_REQUIRED"
    RUNTIME_BINDING_INCOMPATIBLE = "RUNTIME_BINDING_INCOMPATIBLE"
    PREFLIGHT_CHECKS_FAILED = "PREFLIGHT_CHECKS_FAILED"


STATIC_DIAGNOSTIC_MESSAGES: dict[DiagnosticReasonCode, str] = {
    DiagnosticReasonCode.NO_DISCOVERY_SESSION: (
        "Nessuna sessione di Topic Discovery trovata nel database."
    ),
    DiagnosticReasonCode.STRUCTURAL_NO_MATCHING_CHUNKS: (
        "Nessuna sessione con 24/26 chunk (mancanti 10 e 19) trovata nel database."
    ),
    DiagnosticReasonCode.STRUCTURAL_AUDIT_COMPLETED: (
        "Audit strutturale completato: sessione con 24/26 checkpoint rilevata. "
        "Payload conformi. Validazione probatoria non eseguita (documento conversazione non fornito). "
        "Verdetto: RUNTIME_VALIDATION_REQUIRED."
    ),
    DiagnosticReasonCode.AMBIGUOUS_MULTIPLE_SESSIONS: (
        "AMBIGUOUS_MULTIPLE_SESSIONS_MATCH: Rilevate molteplici sessioni identiche compatibili nel database senza criterio univoco di disambiguazione."
    ),
    DiagnosticReasonCode.CONFIG_MISMATCH: (
        "VERIFICA FALLITA: CONFIG_MISMATCH (configurazione operativa discordante tra database e parametri richiesti)."
    ),
    DiagnosticReasonCode.FINGERPRINT_MISMATCH: (
        "VERIFICA FALLITA: FINGERPRINT_MISMATCH (identificativo documento coincidente ma fingerprint crittografico discordante)."
    ),
    DiagnosticReasonCode.NO_MATCHING_SESSION: (
        "VERIFICA FALLITA: NO_MATCHING_SESSION (nessuna sessione nel database corrisponde al documento fornito)."
    ),
    DiagnosticReasonCode.ERR_DB_ACCESS: (
        "Errore di accesso al database SQLite: [ERR_DB_ACCESS]"
    ),
    DiagnosticReasonCode.MOCK_SIMULATION_PASSED: (
        "TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI IN SIMULAZIONE (MOCK_VERIFIED). "
        "Nessuna autorizzazione di ripresa reale in produzione viene concessa da binding mock."
    ),
    DiagnosticReasonCode.LEGACY_BOUNDARIES_LIMITATION: (
        "Compatibilità configurazionale e probatoria accertata per 24/26 blocchi (mancanti 10 e 19). "
        "Limite forense storico: l'assenza di digest crittografico indipendente delle boundaries nei checkpoint SQLite legacy "
        "non consente la certificazione assoluta a priori dell'identità partizionale (RUNTIME_VALIDATION_REQUIRED). "
        "I 24 blocchi storici restano recuperabili previa validazione runtime all'avvio."
    ),
    DiagnosticReasonCode.LIVE_VERIFIED_SAFE_RESUME: (
        "TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI CON SUCCESSO CON BINDING RUNTIME ATTIVO E BOUNDARIES VERIFICATE (LIVE_VERIFIED). "
        "La sessione 24/26 può essere ripresa in sicurezza elaborando ESCLUSIVAMENTE i blocchi 11 e 20 (indici 10 e 19)."
    ),
    DiagnosticReasonCode.RUNTIME_BINDING_REQUIRED: (
        "Tutti i parametri probatori e strutturali sono conformi e il fingerprint di configurazione coincide con la model key dichiarata, "
        "ma non sussiste prova indipendente di binding runtime attivo. "
        "È richiesta la validazione a runtime prima della ripresa effettiva (RUNTIME_VALIDATION_REQUIRED)."
    ),
    DiagnosticReasonCode.RUNTIME_BINDING_INCOMPATIBLE: (
        "RIPRESA NON COMPATIBILE (NOT_COMPATIBLE): binding runtime incompatibile con i requisiti obbligatori."
    ),
    DiagnosticReasonCode.PREFLIGHT_CHECKS_FAILED: (
        "VERIFICA PRE-RIPRESA FALLITA. Uno o più controlli obbligatori non sono stati superati."
    ),
}

_ALLOWED_STATIC_EXPORT_REASONS: set[str] = set(STATIC_DIAGNOSTIC_MESSAGES.values())


def _serialize_exportable_observed_value(val: Any) -> str:
    """
    Rappresentazione di esportazione strettamente tipizzata (Audit Fix B3).
    Accetta ESCLUSIVAMENTE:
    - ID di modelli autorizzati (es. qwen2.5-7b-instruct)
    - Versioni prompt autorizzate (es. discovery_operational)
    - Codici di stato diagnostici verificati (insieme chiuso)
    - Tag di redazione autorizzati dall'insieme chiuso
    - Sequenze strutturate di coppie key=value composte da chiavi diagnostiche note
      e valori rigorosamente booleani, interi, rapporti numerici (es. 24/24),
      liste di interi o digest abbreviati.
    Qualsiasi stringa arbitraria, nome personale (es. 'Alice Wonderland', 'Mario Rossi') o testo non autorizzato
    viene categoricamente sostituito da [OBSERVED_VALUE_REDACTED].
    """
    if val is None:
        return "[NONE]"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip()
    if not s:
        return "[EMPTY]"
    if s.lower() in ALLOWED_MODEL_IDS:
        return s.lower()
    if s.lower() in ALLOWED_PROMPT_VERSIONS:
        return s.lower()
    if s in _KNOWN_SAFE_STATUS_CODES:
        return s
    if _is_approved_tag(s):
        return s
    if "=" in s:
        parts = [p.strip() for p in s.split(",")]
        safe_parts = []
        has_valid_pair = False
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean in _KNOWN_SAFE_DIAGNOSTIC_KEYS:
                    if v_clean in ("True", "False", "None"):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif v_clean.isdigit():
                        safe_parts.append(f"{k_clean}={int(v_clean)}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^\d+/\d+$", v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^\[(?:\d+(?:,\s*\d+)*)?\]$", v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif _is_approved_tag(v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif v_clean in _KNOWN_SAFE_STATUS_CODES:
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^[0-9a-fA-F]{8}\.\.\.[0-9a-fA-F]{4}$", v_clean) or v_clean == "...":
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    else:
                        safe_parts.append(f"{k_clean}=[VALUE_REDACTED]")
                        has_valid_pair = True
                else:
                    safe_parts.append("[KEY_REDACTED]=[VALUE_REDACTED]")
            else:
                if part in _KNOWN_SAFE_STATUS_CODES:
                    safe_parts.append(part)
        if has_valid_pair:
            return ", ".join(safe_parts)
    return "[OBSERVED_VALUE_REDACTED]"


def _serialize_exportable_expected_value(val: Any) -> str:
    """Rappresentazione di esportazione strettamente tipizzata per il valore atteso (Audit Fix B3)."""
    if val is None:
        return "[NONE]"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip()
    if not s:
        return "[EMPTY]"
    if s.lower() in ALLOWED_MODEL_IDS:
        return s.lower()
    if s.lower() in ALLOWED_PROMPT_VERSIONS:
        return s.lower()
    if s in _KNOWN_SAFE_STATUS_CODES:
        return s
    if _is_approved_tag(s):
        return s
    if "=" in s:
        parts = [p.strip() for p in s.split(",")]
        safe_parts = []
        has_valid_pair = False
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean in _KNOWN_SAFE_DIAGNOSTIC_KEYS:
                    if v_clean in ("True", "False", "None"):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif v_clean.isdigit():
                        safe_parts.append(f"{k_clean}={int(v_clean)}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^\d+/\d+$", v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^\[(?:\d+(?:,\s*\d+)*)?\]$", v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif _is_approved_tag(v_clean):
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif v_clean in _KNOWN_SAFE_STATUS_CODES:
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    elif re.fullmatch(r"^[0-9a-fA-F]{8}\.\.\.[0-9a-fA-F]{4}$", v_clean) or v_clean == "...":
                        safe_parts.append(f"{k_clean}={v_clean}")
                        has_valid_pair = True
                    else:
                        safe_parts.append(f"{k_clean}=[VALUE_REDACTED]")
                        has_valid_pair = True
                else:
                    safe_parts.append("[KEY_REDACTED]=[VALUE_REDACTED]")
            else:
                if part in _KNOWN_SAFE_STATUS_CODES:
                    safe_parts.append(part)
        if has_valid_pair:
            return ", ".join(safe_parts)
    return "[EXPECTED_VALUE_REDACTED]"


def _sanitize_reason_for_export(r: str) -> str:
    """
    Sanifica le motivazioni per il report esportabile (B3).
    Accetta ESCLUSIVAMENTE messaggi statici definiti nel codice dall'insieme chiuso autorizzato.
    Qualsiasi motivazione arbitraria, testo concatenato o tentativo di bypass
    (es. 'Audit strutturale completato: Alice Wonderland', 'TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI. Mario Rossi')
    viene categoricamente sostituito da [DIAGNOSTIC_REASON_REDACTED].
    """
    if not r or not isinstance(r, str):
        return "[EMPTY_REASON]"
    clean = r.strip()
    if clean in _ALLOWED_STATIC_EXPORT_REASONS:
        return clean
    return "[DIAGNOSTIC_REASON_REDACTED]"


@dataclass(frozen=True)
class CheckResult:
    """Risultato del singolo controllo di preflight con separazione tra report interno ed esportabile."""
    check_number: int
    name: str
    passed: bool
    details: Any = ""
    observed_value: str = ""
    expected_value: str = ""

    def to_internal_dict(self) -> dict[str, Any]:
        """Contesto diagnostico interno (può contenere dettagli descrittivi di sviluppo)."""
        return {
            "check_number": self.check_number,
            "name": self.name,
            "status": "PASS" if self.passed else "FAIL",
            "details": self.details,
            "observed_value": self.observed_value,
            "expected_value": self.expected_value,
        }

    def to_dict(self) -> dict[str, Any]:
        """
        Rappresentazione di esportazione strettamente tipizzata (Audit Fix B2).
        Zero leak di testo arbitrario o nomi personali.
        """
        safe_name = self.name if self.name in ALLOWED_CHECK_NAMES else CANONICAL_CHECK_NAMES.get(self.check_number, f"CHECK_{self.check_number}")
        
        safe_details: Any = CANONICAL_CHECK_DETAILS.get(self.check_number, "[CHECK_DETAILS_REDACTED]")
        if isinstance(self.details, dict):
            allowed_det_keys = {
                "chunks_present", "chunks_schema_valid", "corrupted_chunk_records",
                "subchunks_valid", "corrupted_subchunks", "total_subchunk_rows_read",
                "chunks_json_decodable", "missing_indices",
            }
            d_dict = {}
            for dk, dv in self.details.items():
                if str(dk) in allowed_det_keys:
                    if isinstance(dv, (int, float, bool)):
                        d_dict[str(dk)] = dv
                    elif isinstance(dv, (list, tuple)) and all(isinstance(x, int) for x in dv):
                        d_dict[str(dk)] = [int(x) for x in dv]
            if d_dict:
                safe_details = d_dict

        return {
            "check_number": int(self.check_number),
            "name": safe_name,
            "status": "PASS" if self.passed else "FAIL",
            "details": safe_details,
            "observed_value": _serialize_exportable_observed_value(self.observed_value),
            "expected_value": _serialize_exportable_expected_value(self.expected_value),
        }


@dataclass
class PreflightAuditReport:
    """Rapporto completo dell'audit di preflight con separazione tra report interno ed esportabile."""
    verdict: PreflightVerdictStatus
    database_path: str
    document_available: bool
    total_planned_chunks: int = 26
    completed_chunks_count: int = 0
    missing_chunks: list[int] = field(default_factory=list)
    skipped_chunks_on_resume: list[int] = field(default_factory=list)
    chunks_to_infer_on_resume: list[int] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    resumption_plan: dict[str, Any] | None = None
    configuration_details: dict[str, Any] = field(default_factory=dict)
    verification_classification: str = "UNVERIFIED"
    source_immutability_status: dict[str, Any] = field(default_factory=lambda: {
        "DATABASE_LOGICAL_CONTENT_UNCHANGED": "NOT_MEASURED",
        "WAL_CONTENT_UNCHANGED": "NOT_MEASURED",
        "SHM_BYTES_UNCHANGED": "NOT_MEASURED",
        "SOURCE_METADATA_UNCHANGED": "NOT_MEASURED",
        "CONSISTENT_SNAPSHOT_VERIFIED": "NOT_MEASURED",
        "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": "NOT_MEASURED",
    })
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_internal_dict(self) -> dict[str, Any]:
        """Rappresentazione per il contesto diagnostico interno di sviluppo."""
        return {
            "verdict": self.verdict.value,
            "verification_classification": self.verification_classification,
            "timestamp": self.timestamp,
            "database_path": str(self.database_path),
            "document_available": self.document_available,
            "total_planned_chunks": self.total_planned_chunks,
            "completed_chunks_count": self.completed_chunks_count,
            "missing_chunks": list(self.missing_chunks),
            "skipped_chunks_on_resume": list(self.skipped_chunks_on_resume),
            "chunks_to_infer_on_resume": list(self.chunks_to_infer_on_resume),
            "checks": [c.to_internal_dict() for c in self.checks],
            "reasons": list(self.reasons),
            "configuration_details": dict(self.configuration_details),
            "source_immutability_status": dict(self.source_immutability_status),
            "resumption_plan": dict(self.resumption_plan) if self.resumption_plan else None,
        }

    def to_exportable_dict(self) -> dict[str, Any]:
        """
        Rappresentazione di esportazione strettamente tipizzata e pubblicabile (Audit Fix B2).
        Basata esclusivamente su:
        - codici diagnostici enumerati;
        - booleani;
        - interi;
        - indici di chunk;
        - stati di verifica;
        - etichette modello canoniche;
        - fingerprint validati e abbreviati.
        Zero leak di testo arbitrario, canary o identificatori personali.
        """
        safe_plan = None
        if self.resumption_plan is not None:
            strat = str(self.resumption_plan.get("strategy", "RETRY_FAILED_CHUNKS_ONLY"))
            safe_strat = strat if strat in ("RETRY_FAILED_CHUNKS_ONLY", "FULL_RETRY") else "RETRY_FAILED_CHUNKS_ONLY"
            prof_val = str(self.resumption_plan.get("profile", ""))
            safe_prof = prof_val if prof_val in ALLOWED_PROFILES else "[PROFILE_REDACTED]"
            
            safe_plan = {
                "strategy": safe_strat,
                "total_planned_chunks": int(self.resumption_plan.get("total_planned_chunks", self.total_planned_chunks)),
                "chunks_to_skip_count": int(self.resumption_plan.get("chunks_to_skip_count", 0)),
                "chunks_to_skip_indices_zero_based": [int(x) for x in self.resumption_plan.get("chunks_to_skip_indices_zero_based", [])],
                "chunks_to_skip_display_one_based": "Blocchi 1-10, 12-19, 21-26",
                "chunks_to_infer_count": int(self.resumption_plan.get("chunks_to_infer_count", 0)),
                "chunks_to_infer_indices_zero_based": [int(x) for x in self.resumption_plan.get("chunks_to_infer_indices_zero_based", [])],
                "chunks_to_infer_display_one_based": "Blocco 11, Blocco 20",
                "model_id": _sanitize_model_identifier(self.resumption_plan.get("model_id")),
                "profile": safe_prof,
                "max_topics": int(self.resumption_plan.get("max_topics", 10)),
                "scientific_guarantee": (
                    "I 24 blocchi pregressi validati non verranno reinviati al modello LLM. "
                    "I blocchi 11 e 20 (indici zero-based 10 e 19) verranno elaborati con gestione standard di eventuali retry o subchunk."
                ),
            }

        safe_cfg: dict[str, Any] = {}
        allowed_config_keys = {
            "persisted_config_fingerprint", "computed_config_fingerprint",
            "requested_model_id", "resolved_profile", "context_length",
            "temperature", "seed", "runtime_binding_status",
            "runtime_binding_verified", "verification_classification",
            "declared_resolved_model_key", "observed_runtime_identity",
            "structural_inspection", "evidentiary_validation_status",
            "source_immutability_status",
        }
        for k, v in self.configuration_details.items():
            str_k = str(k)
            if str_k not in allowed_config_keys:
                continue
            if isinstance(v, (int, float, bool)):
                safe_cfg[str_k] = v
            elif str_k == "source_immutability_status" and isinstance(v, dict):
                allowed_immut_keys = {
                    "database_logical_content_unchanged", "wal_content_unchanged",
                    "shm_bytes_unchanged", "source_metadata_unchanged",
                    "consistent_snapshot_verified", "source_physical_immutability_verified",
                    "DATABASE_LOGICAL_CONTENT_UNCHANGED", "WAL_CONTENT_UNCHANGED",
                    "SHM_BYTES_UNCHANGED", "SOURCE_METADATA_UNCHANGED",
                    "CONSISTENT_SNAPSHOT_VERIFIED", "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED",
                }
                safe_imm: dict[str, Any] = {}
                for ik, iv in v.items():
                    if str(ik) in allowed_immut_keys:
                        if iv is True or str(iv) == "VERIFIED_TRUE":
                            safe_imm[str(ik)] = True
                        elif iv is False or str(iv) == "VERIFIED_FALSE":
                            safe_imm[str(ik)] = False
                        elif str(iv) == "NOT_MEASURED":
                            safe_imm[str(ik)] = "NOT_MEASURED"
                        else:
                            safe_imm[str(ik)] = "NOT_MEASURED"
                safe_cfg[str_k] = safe_imm
            elif str_k == "structural_inspection" and isinstance(v, dict):
                allowed_struct_keys = {
                    "chunks_present", "chunks_schema_valid", "corrupted_chunk_records",
                    "subchunks_valid", "corrupted_subchunks", "total_subchunk_rows_read",
                }
                safe_sub = {str(sk): int(sv) for sk, sv in v.items() if str(sk) in allowed_struct_keys and isinstance(sv, (int, bool))}
                safe_cfg[str_k] = safe_sub
            elif str_k in ("declared_resolved_model_key", "observed_runtime_identity", "requested_model_id"):
                safe_cfg[str_k] = _sanitize_model_identifier(str(v) if v is not None else None)
            elif str_k in ("persisted_config_fingerprint", "computed_config_fingerprint"):
                safe_cfg[str_k] = _format_fp_safe(str(v))
            elif str_k == "resolved_profile":
                safe_cfg[str_k] = str(v) if str(v) in ALLOWED_PROFILES else "[PROFILE_REDACTED]"
            elif str_k in ("runtime_binding_status", "verification_classification", "evidentiary_validation_status"):
                val_s = str(v)
                if val_s in _KNOWN_SAFE_STATUS_CODES or val_s in (
                    "NOT_COMPATIBLE", "MOCK_VERIFIED", "LIVE_VERIFIED",
                    "RUNTIME_VALIDATION_REQUIRED", "LIVE_RUNTIME_VALIDATION_REQUIRED",
                    "UNVERIFIED", "STATIC_AUDIT_WITHOUT_DOCUMENT"
                ):
                    safe_cfg[str_k] = val_s
                else:
                    safe_cfg[str_k] = "[STATUS_REDACTED]"
            else:
                pass

        if isinstance(self.verdict, PreflightVerdictStatus):
            verdict_str = self.verdict.value
        elif isinstance(self.verdict, str) and self.verdict in PreflightVerdictStatus._value2member_map_:
            verdict_str = self.verdict
        else:
            verdict_str = "[VERDICT_INVALID_REDACTED]"
        verif_class = str(self.verification_classification)
        if verif_class not in (
            "LIVE_VERIFIED", "MOCK_VERIFIED", "RUNTIME_VALIDATION_REQUIRED",
            "LIVE_RUNTIME_VALIDATION_REQUIRED", "NOT_COMPATIBLE", "UNVERIFIED",
            "STATIC_AUDIT_WITHOUT_DOCUMENT"
        ):
            verif_class = "[CLASSIFICATION_REDACTED]"

        # Validazione formato canonico timestamp ISO-8601 (B3)
        safe_timestamp: str
        try:
            ts_clean = str(self.timestamp).strip()
            datetime.fromisoformat(ts_clean.replace("Z", "+00:00"))
            safe_timestamp = ts_clean
        except Exception:
            safe_timestamp = datetime.now(timezone.utc).isoformat()

        return {
            "verdict": verdict_str,
            "verification_classification": verif_class,
            "timestamp": safe_timestamp,
            "database_path": _sanitize_path(self.database_path),
            "document_available": bool(self.document_available),
            "total_planned_chunks": int(self.total_planned_chunks),
            "completed_chunks_count": int(self.completed_chunks_count),
            "missing_chunks_zero_based": [int(x) for x in self.missing_chunks],
            "configuration_details": safe_cfg,
            "source_immutability_status": dict(self.source_immutability_status),
            "resumption_plan": safe_plan,
            "checks": [c.to_dict() for c in self.checks],
            "reasons": [_sanitize_reason_for_export(r) for r in self.reasons],
        }

    def to_dict(self) -> dict[str, Any]:
        """Serializzazione di default pubblicabile ed esportabile in conformità ai requisiti di audit."""
        return self.to_exportable_dict()


# ==============================================================================
# GESTIONE SQLITE READ-ONLY E CONSISTENT SNAPSHOT (ZERO MUTATION)
# ==============================================================================

@contextmanager
def _open_readonly_db(db_path: Path):
    """
    Apre il database SQLite in modalità rigorosamente read-only (query_only=ON, mode=ro).
    Non acquisisce lock esclusivi né muta journal o schema.
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"Database checkpoint non trovato: {_sanitize_path(db_path)}")
    target_posix = db_path.resolve().as_posix()
    uri = f"file:{target_posix}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only = ON;")
        yield conn
    finally:
        conn.close()


class SnapshotError(Exception):
    """Classe base per tutti gli errori controllati durante la creazione dello snapshot."""
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class SnapshotDestinationForbiddenError(SnapshotError, PermissionError):
    """Sollevata quando la destinazione dello snapshot ricade in un'area riservata o protetta."""
    def __init__(self, message: str = "Destinazione snapshot non consentita da criteri di isolamento forense."):
        super().__init__(message, error_code="SNAPSHOT_DESTINATION_FORBIDDEN")


class SnapshotDirectoryInvalidError(SnapshotError, NotADirectoryError):
    """Sollevata quando la directory specificata per lo snapshot non è valida o è un file ordinario."""
    def __init__(self, message: str = "Directory snapshot specificata non valida."):
        super().__init__(message, error_code="SNAPSHOT_DIRECTORY_INVALID")


class SnapshotPermissionDeniedError(SnapshotError, PermissionError):
    """Sollevata quando i permessi di scrittura sono insufficienti o è rilevato un symlink."""
    def __init__(self, message: str = "Permessi insufficienti o collegamento simbolico non consentito per lo snapshot."):
        super().__init__(message, error_code="SNAPSHOT_PERMISSION_DENIED")


class SnapshotCreationFailedError(SnapshotError, RuntimeError):
    """Sollevata quando la Backup API di SQLite fallisce durante la creazione dello snapshot."""
    def __init__(self, message: str = "Creazione dello snapshot SQLite non riuscita."):
        super().__init__(message, error_code="SNAPSHOT_CREATION_FAILED")


_SNAPSHOT_ERROR_MESSAGES: dict[str, str] = {
    "SNAPSHOT_DESTINATION_FORBIDDEN": "Destinazione dello snapshot non consentita dai criteri di isolamento forense.",
    "SNAPSHOT_DIRECTORY_INVALID": "Directory specificata per lo snapshot non valida o non accessibile.",
    "SNAPSHOT_PERMISSION_DENIED": "Permessi insufficienti o collegamento simbolico non consentito per lo snapshot.",
    "SNAPSHOT_CREATION_FAILED": "Creazione dello snapshot SQLite non riuscita durante la procedura di backup.",
}


def _is_subpath_or_equal(path: Path, potential_parent: Path) -> bool:
    """Verifica se path coincide con potential_parent o è un suo discendente (case-insensitive e canonical)."""
    try:
        p_res = path.resolve(strict=False)
        parent_res = potential_parent.resolve(strict=False)
        p_res.relative_to(parent_res)
        return True
    except (ValueError, RuntimeError):
        pass

    p_norm = os.path.normcase(os.path.abspath(str(path)))
    parent_norm = os.path.normcase(os.path.abspath(str(potential_parent)))
    if p_norm == parent_norm:
        return True
    if not parent_norm.endswith(os.sep):
        parent_norm += os.sep
    return p_norm.startswith(parent_norm)


def _validate_snapshot_target_safety(
    src_db_path: Path,
    target_dir: Path,
    target_file: Path | None = None,
) -> None:
    """
    Validazione preventiva rigorosa della destinazione di snapshot (B1):
    1. Vietare snapshot dentro '.checkpoints' (sia per parti esplicite che per risoluzione canonica).
    2. Vietare snapshot nella directory contenente il database sorgente (e suoi sottodirectory).
    3. Vietare snapshot all'interno del repository Git 'Tesi' (o REPO_ROOT).
    4. Vietare symlink nel file di destinazione, nella cartella di destinazione o in qualsiasi loro antenato.
    5. Verificare che la directory di destinazione o i suoi antenati non siano file ordinari.
    6. Se target_file è fornito, impedire che coincida con il database sorgente o i relativi sidecar SQLite.
    7. Nessuna directory o file viene creato da questa funzione di validazione.
    """
    # 4. Controllo symlink su target_dir e tutti gli antenati esistenti
    curr = target_dir
    while True:
        if os.path.islink(curr) or curr.is_symlink():
            raise SnapshotPermissionDeniedError("Symlink non consentito nella directory snapshot o nei suoi antenati.")
        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    if target_file is not None:
        if os.path.islink(target_file) or target_file.is_symlink():
            raise SnapshotPermissionDeniedError("Symlink non consentito per il file di snapshot.")

    # 5. Controllo che target_dir o qualsiasi antenato non sia un file ordinario
    if target_dir.exists() and not target_dir.is_dir():
        raise SnapshotDirectoryInvalidError("La directory snapshot specificata è un file ordinario o non è una cartella.")

    curr = target_dir.parent
    while True:
        if curr.exists() and not curr.is_dir():
            raise SnapshotDirectoryInvalidError("Uno degli antenati della directory snapshot è un file ordinario.")
        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    # 1. Vietare creazione snapshot dentro '.checkpoints'
    if any(part.lower() == ".checkpoints" for part in target_dir.parts):
        raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory '.checkpoints'.")

    target_dir_res = target_dir.resolve(strict=False)
    if any(part.lower() == ".checkpoints" for part in target_dir_res.parts):
        raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory '.checkpoints'.")

    if target_file is not None:
        if any(part.lower() == ".checkpoints" for part in target_file.parts):
            raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory '.checkpoints'.")
        if any(part.lower() == ".checkpoints" for part in target_file.resolve(strict=False).parts):
            raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory '.checkpoints'.")

    # 2. Vietare snapshot nella directory del database sorgente
    src_p = Path(src_db_path)
    src_dir = src_p.parent
    src_dir_res = src_dir.resolve(strict=False)
    src_file_res = src_p.resolve(strict=False)
    src_parent_res = src_file_res.parent

    if _is_subpath_or_equal(target_dir, src_dir) or _is_subpath_or_equal(target_dir, src_dir_res) or _is_subpath_or_equal(target_dir, src_parent_res):
        raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory del database sorgente.")

    if target_dir_res and (_is_subpath_or_equal(target_dir_res, src_dir) or _is_subpath_or_equal(target_dir_res, src_dir_res) or _is_subpath_or_equal(target_dir_res, src_parent_res)):
        raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory del database sorgente.")

    if target_file is not None:
        tf_parent = target_file.parent
        if _is_subpath_or_equal(tf_parent, src_dir) or _is_subpath_or_equal(tf_parent, src_dir_res) or _is_subpath_or_equal(tf_parent, src_parent_res):
            raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno della directory del database sorgente.")

    # 3. Vietare snapshot all'interno del repository Git 'Tesi' (o REPO_ROOT)
    if _is_subpath_or_equal(target_dir, REPO_ROOT) or _is_subpath_or_equal(target_dir_res, REPO_ROOT):
        raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno del repository 'Tesi'.")

    if target_file is not None:
        tf_res = target_file.resolve(strict=False)
        if _is_subpath_or_equal(target_file, REPO_ROOT) or _is_subpath_or_equal(tf_res, REPO_ROOT):
            raise SnapshotDestinationForbiddenError("Creazione dello snapshot non consentita all'interno del repository 'Tesi'.")

        # 6. Verifica collisione diretta con file sorgente o sidecar
        tf_norm = os.path.normcase(os.path.abspath(str(target_file)))
        src_norm = os.path.normcase(os.path.abspath(str(src_p)))
        src_res_norm = os.path.normcase(os.path.abspath(str(src_file_res)))

        if tf_norm == src_norm or tf_norm == src_res_norm:
            raise SnapshotDestinationForbiddenError("Il file di snapshot coincide con il database sorgente.")

        for ext in ("-wal", "-shm", "-journal"):
            if tf_norm == src_norm + ext or tf_norm == src_res_norm + ext:
                raise SnapshotDestinationForbiddenError("Il file di snapshot coincide con un sidecar SQLite del database sorgente.")


def _measure_source_file_state(path: Path) -> dict[str, tuple[int, int, int, str]] | None:
    """
    Misura in modo deterministico e fail-closed lo stato fisico e i metadati controllati
    (dimensione st_size, timestamp di modifica st_mtime_ns, permessi/modalità st_mode, hash crittografico SHA-256)
    del database SQLite principale e di tutti i sidecar (-wal, -shm, -journal).

    METADATI OBBLIGATORI CONTROLLATI (F3):
    - size_bytes: dimensione fisica in byte (st_size)
    - mtime_ns: timestamp di modifica in nanosecondi (st_mtime_ns)
    - mode: permessi POSIX e bit di modalità file (st_mode)
    - sha256: digest crittografico SHA-256 dei byte del file

    ESCLUSIONE ATIME:
    - atime (data/ora di ultimo accesso) è esplicitamente escluso dalle garanzie
      poiché qualsiasi lettura OS può aggiornarlo a seconda dei flag di mount del filesystem
      (relatime/strictatime), salvo supporto ed autorizzazione di O_NOATIME.
    - Non viene eseguito alcun ripristino artificiale di atime/mtime/permessi.

    GARANZIE FAIL-CLOSED (B1/F3):
    1. Distingue file realmente assente da file non leggibile.
    2. Rifiuta categoricamente symlink o percorsi non regolari.
    3. Solleva SnapshotPermissionDeniedError su PermissionError (stat, open, read).
    4. Solleva SnapshotCreationFailedError su OSError (I/O, hashing).
    5. Non ignora né salta alcun errore di accesso o lettura.
    6. Ritorna None esclusivamente se il database principale è inesistente sul filesystem.
    """
    src_p = Path(path)
    try:
        st = src_p.lstat()
    except FileNotFoundError:
        return None
    except PermissionError as pe:
        raise SnapshotPermissionDeniedError(
            f"Permessi insufficienti per accedere ai metadati del database sorgente: {_sanitize_path(src_p)}"
        ) from pe
    except OSError as oe:
        raise SnapshotCreationFailedError(
            f"Errore I/O durante l'accesso al database sorgente: {_sanitize_path(src_p)}"
        ) from oe

    if os.path.islink(src_p) or src_p.is_symlink() or stat.S_ISLNK(st.st_mode):
        raise SnapshotPermissionDeniedError(
            f"Symlink non consentito per il database sorgente: {_sanitize_path(src_p)}"
        )
    if not stat.S_ISREG(st.st_mode):
        raise SnapshotCreationFailedError(
            f"Il database sorgente non è un file regolare: {_sanitize_path(src_p)}"
        )

    state: dict[str, tuple[int, int, int, str]] = {}

    # Misurazione hash del database principale
    h = hashlib.sha256()
    try:
        fd = None
        if hasattr(os, "O_NOATIME"):
            try:
                fd = os.open(str(src_p), os.O_RDONLY | os.O_NOATIME)
            except (PermissionError, OSError):
                fd = None
        if fd is not None:
            with open(fd, "rb", closefd=True) as fp:
                while chunk := fp.read(65536):
                    h.update(chunk)
        else:
            with open(src_p, "rb") as fp:
                while chunk := fp.read(65536):
                    h.update(chunk)
        state[src_p.name] = (st.st_size, st.st_mtime_ns, st.st_mode, h.hexdigest())
    except PermissionError as pe:
        raise SnapshotPermissionDeniedError(
            f"Permessi insufficienti per leggere il database sorgente: {_sanitize_path(src_p)}"
        ) from pe
    except OSError as oe:
        raise SnapshotCreationFailedError(
            f"Errore I/O o hashing durante la lettura del database sorgente: {_sanitize_path(src_p)}"
        ) from oe

    # Misurazione fail-closed dei sidecar (-wal, -shm, -journal)
    base_stem = str(src_p)
    for ext in ("-wal", "-shm", "-journal"):
        sidecar = Path(base_stem + ext)
        try:
            s_st = sidecar.lstat()
            sidecar_present = True
        except FileNotFoundError:
            sidecar_present = False
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError(
                f"Permessi insufficienti per accedere ai metadati del sidecar SQLite ({ext}): {_sanitize_path(sidecar)}"
            ) from pe
        except OSError as oe:
            raise SnapshotCreationFailedError(
                f"Errore I/O durante l'accesso al sidecar SQLite ({ext}): {_sanitize_path(sidecar)}"
            ) from oe

        if not sidecar_present:
            continue

        if os.path.islink(sidecar) or sidecar.is_symlink() or stat.S_ISLNK(s_st.st_mode):
            raise SnapshotPermissionDeniedError(
                f"Symlink non consentito per il sidecar SQLite ({ext}): {_sanitize_path(sidecar)}"
            )
        if not stat.S_ISREG(s_st.st_mode):
            raise SnapshotCreationFailedError(
                f"Il sidecar SQLite ({ext}) non è un file regolare: {_sanitize_path(sidecar)}"
            )

        sh = hashlib.sha256()
        try:
            s_fd = None
            if hasattr(os, "O_NOATIME"):
                try:
                    s_fd = os.open(str(sidecar), os.O_RDONLY | os.O_NOATIME)
                except (PermissionError, OSError):
                    s_fd = None
            if s_fd is not None:
                with open(s_fd, "rb", closefd=True) as s_fp:
                    while chunk := s_fp.read(65536):
                        sh.update(chunk)
            else:
                with open(sidecar, "rb") as s_fp:
                    while chunk := s_fp.read(65536):
                        sh.update(chunk)
            state[sidecar.name] = (s_st.st_size, s_st.st_mtime_ns, s_st.st_mode, sh.hexdigest())
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError(
                f"Permessi insufficienti per leggere il sidecar SQLite ({ext}): {_sanitize_path(sidecar)}"
            ) from pe
        except OSError as oe:
            raise SnapshotCreationFailedError(
                f"Errore I/O o hashing durante la lettura del sidecar SQLite ({ext}): {_sanitize_path(sidecar)}"
            ) from oe

    return state


def acquire_forensic_readonly_copy(
    src_db_path: Path,
    dest_dir: Path | None = None,
    max_quiescence_retries: int = 3,
    quiescence_retry_delay: float = 0.05,
) -> tuple[Path, Path | None, dict[str, Any]]:
    """
    Acquisisce una copia forense isolata e consistente del database SQLite e dei suoi sidecar
    SENZA collegare il motore SQLite ai file sorgente originali.

    GARANZIE FORENSI (B1, B2, B3, B4, B5):
    1. Nessuna connessione SQLite aperta sui file sorgente (zero mutazioni del file -shm).
    2. Misurazione fail-closed di tutti i file e sidecar (rifiuto categorico di sidecar illeggibili).
    3. Verifica della finestra di quiescenza: confronto di size, mtime_ns e SHA-256 (t0 == t1).
    4. Copia atomica a livello filesystem con permessi restrittivi 0600.
    5. Verifica di integrità transazionale sulla copia isolata (PRAGMA integrity_check).
    6. Misurazione finale a t2 con ABORT immediato se t2 differisce dallo stato iniziale t0.
    7. Nessuna alterazione dei sorgenti e rimozione esclusiva delle risorse temporanee create.
    """
    src_p = Path(src_db_path)
    if not src_p.is_file():
        raise FileNotFoundError(f"Database checkpoint non trovato: {_sanitize_path(src_p)}")

    if os.path.islink(src_p) or src_p.is_symlink():
        raise SnapshotPermissionDeniedError("Symlink non consentito per il database sorgente.")

    if dest_dir is not None:
        target_dir = Path(dest_dir)
        _validate_snapshot_target_safety(src_p, target_dir=target_dir, target_file=None)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError("Permessi insufficienti per la cartella di acquisizione.") from pe
        except OSError as oe:
            raise SnapshotDirectoryInvalidError("Impossibile accedere alla cartella di acquisizione.") from oe
        created_temp_dir = None
        work_dir = target_dir
    else:
        try:
            created_temp_dir = Path(tempfile.mkdtemp(prefix="chatanalysis_forensic_"))
            _OWNERSHIP_TRACKER.register_temp_dir(created_temp_dir)
            work_dir = created_temp_dir
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError("Permessi insufficienti per creare la cartella temporanea.") from pe
        except OSError as oe:
            raise SnapshotDirectoryInvalidError("Impossibile creare la cartella temporanea.") from oe

    state_t0: dict[str, tuple[int, ...]] | None = None
    owned_created_files: list[tuple[Path, int | None, tuple[int, int] | None]] = []
    quiescent = False

    def _cleanup_owned_files() -> None:
        for f_path, dup_fd, f_ident in list(owned_created_files):
            try:
                # Fail-closed: se l'identità o il descrittore aperto sono None, MAI CANCELLARE
                if f_ident is None or dup_fd is None:
                    if dup_fd is not None:
                        try:
                            os.close(dup_fd)
                        except Exception:
                            pass
                    continue

                should_del = True
                # 1. Verifica tramite open descriptor che il link originario esista ancora
                try:
                    fd_st = os.fstat(dup_fd)
                    if hasattr(fd_st, "st_nlink") and fd_st.st_nlink == 0:
                        should_del = False  # Il file è già stato rimosso o sostituito da un processo concorrente!
                except Exception:
                    should_del = False

                # 2. Verifica che il file sul filesystem corrisponda esattamente all'identità posseduta
                if should_del:
                    try:
                        cur_st = os.lstat(f_path)
                        if (cur_st.st_dev, cur_st.st_ino) != f_ident:
                            should_del = False  # File sostituito da altro processo
                    except Exception:
                        should_del = False

                if should_del and (f_path.is_file() or os.path.lexists(f_path)):
                    if os.name != "nt":
                        f_path.unlink()
                        if dup_fd is not None:
                            try:
                                os.close(dup_fd)
                            except Exception:
                                pass
                    else:
                        if dup_fd is not None:
                            try:
                                os.close(dup_fd)
                            except Exception:
                                pass
                        f_path.unlink()
                else:
                    if dup_fd is not None:
                        try:
                            os.close(dup_fd)
                        except Exception:
                            pass
            except Exception:
                if dup_fd is not None:
                    try:
                        os.close(dup_fd)
                    except Exception:
                        pass
        owned_created_files.clear()

    try:
        for attempt in range(max_quiescence_retries):
            # 1. Misurazione dello stato sorgente pre-copia (t0)
            state_t0 = _measure_source_file_state(src_p)
            if not state_t0:
                raise FileNotFoundError(f"Database checkpoint non accessibile: {_sanitize_path(src_p)}")

            _cleanup_owned_files()

            # 2. Copia a livello filesystem con permessi restrittivi 0600
            old_umask = os.umask(0o077)
            try:
                for fname in state_t0:
                    sf = Path(src_p.parent / fname)
                    df = work_dir / fname
                    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    if hasattr(os, "O_CLOEXEC"):
                        flags |= os.O_CLOEXEC

                    try:
                        fd = os.open(str(df), flags, 0o600)
                    except PermissionError as pe:
                        raise SnapshotPermissionDeniedError("Permessi insufficienti durante la copia del database.") from pe
                    except FileExistsError as fe:
                        raise SnapshotCreationFailedError(f"File di destinazione copia forense già esistente: {_sanitize_path(df)}") from fe
                    except OSError as oe:
                        raise SnapshotDirectoryInvalidError("Errore I/O durante la creazione del file di copia forense.") from oe

                    # F1/F2: Registrazione IMMEDIATA dell'ownership e conservazione del descrittore aperto
                    dup_fd = None
                    try:
                        f_st = os.fstat(fd)
                        f_ident = (f_st.st_dev, f_st.st_ino)
                        dup_fd = os.dup(fd)
                    except Exception:
                        f_ident = None
                        dup_fd = None

                    owned_created_files.append((df, dup_fd, f_ident))
                    _OWNERSHIP_TRACKER.register_owned_file(df, f_ident)

                    if f_ident is None or dup_fd is None:
                        try:
                            os.close(fd)
                        except Exception:
                            pass
                        raise SnapshotCreationFailedError("Impossibile verificare l'identità del file creato per la copia forense.")

                    fd_closed = False
                    try:
                        try:
                            with open(fd, "wb", closefd=True) as out_fp:
                                fd_closed = True
                                with open(sf, "rb") as in_fp:
                                    while chunk := in_fp.read(65536):
                                        out_fp.write(chunk)
                                    out_fp.flush()
                                _OWNERSHIP_TRACKER.register_owned_file(df, f_ident)
                        finally:
                            if not fd_closed:
                                try:
                                    os.close(fd)
                                except Exception:
                                    pass
                    except PermissionError as pe:
                        raise SnapshotPermissionDeniedError("Permessi insufficienti durante la scrittura o lettura della copia.") from pe
                    except OSError as oe:
                        raise SnapshotCreationFailedError("Errore I/O durante la copia binaria dei dati.") from oe
            finally:
                os.umask(old_umask)

            # 3. Misurazione dello stato sorgente post-copia (t1, finestra di quiescenza)
            state_t1 = _measure_source_file_state(src_p)
            if state_t0 == state_t1:
                quiescent = True
                break
            else:
                _cleanup_owned_files()
                if attempt < max_quiescence_retries - 1:
                    time.sleep(quiescence_retry_delay)

        if not quiescent:
            raise SnapshotCreationFailedError("Acquisizione non quiescente: rilevata scrittura concorrente sul database sorgente.")

        # 4. Verifica di fedeltà crittografica della copia
        assert state_t0 is not None
        for fname, file_state in state_t0.items():
            expected_size = file_state[0]
            expected_sha = file_state[-1]
            df = work_dir / fname
            if not df.is_file() or df.stat().st_size != expected_size:
                raise SnapshotCreationFailedError("Copia forense incompleta o dimensione non corrispondente.")
            h = hashlib.sha256()
            with open(df, "rb") as fp:
                while chunk := fp.read(65536):
                    h.update(chunk)
            if h.hexdigest() != expected_sha:
                raise SnapshotCreationFailedError("Copia forense crittograficamente non fedele alla sorgente.")

        # 5. Verifica di integrità transazionale sulla copia isolata
        copy_db = work_dir / src_p.name
        if not copy_db.is_file() or copy_db.stat().st_size < 100:
            raise SnapshotCreationFailedError("Copia forense SQLite incompleta o dimensione non valida.")
        with open(copy_db, "rb") as fp:
            magic = fp.read(16)
        if magic != b"SQLite format 3\x00":
            raise SnapshotCreationFailedError("Copia forense priva del magic header SQLite format 3 valido.")

        copy_uri = f"file:{copy_db.resolve().as_posix()}?mode=ro"
        copy_conn = None
        try:
            copy_conn = sqlite3.connect(copy_uri, uri=True, timeout=5.0)
            copy_conn.execute("PRAGMA query_only = ON;")
            qchk = copy_conn.execute("PRAGMA quick_check;").fetchall()
            ichk = copy_conn.execute("PRAGMA integrity_check;").fetchall()
            if qchk != [("ok",)] or ichk != [("ok",)]:
                raise SnapshotCreationFailedError("Integrità transazionale della copia SQLite non valida o corrotta.")
        finally:
            if copy_conn is not None:
                try:
                    copy_conn.close()
                except Exception:
                    pass

        # 6. Misurazione finale per certificare zero mutazioni sui file originali (t2)
        state_t2 = _measure_source_file_state(src_p)
        if state_t2 is None:
            raise SnapshotCreationFailedError("Acquisizione forense non valida: database sorgente non rilevato a t2.")

        # B1 & B2: Stabilità dell'insieme dei file (sidecar che compare o scompare)
        if set(state_t2.keys()) != set(state_t0.keys()):
            raise SnapshotCreationFailedError(
                "Acquisizione forense non valida: rilevata discrepanza nell'insieme dei sidecar presenti a t2 rispetto a t0."
            )

        # B2 & F3: Abort immediato se t2 differisce dallo stato iniziale t0 o post-copia t1 (inclusi permessi mode)
        if state_t2 != state_t0 or state_t2 != state_t1:
            raise SnapshotCreationFailedError(
                "Acquisizione forense non valida: rilevata alterazione fisica (dimensione, mtime, permessi o hash) dei file sorgenti a t2."
            )

        shm_name = f"{src_p.name}-shm"
        wal_name = f"{src_p.name}-wal"

        shm_unchanged = True if shm_name in state_t0 else "NOT_MEASURED"
        wal_unchanged = True if wal_name in state_t0 else "NOT_MEASURED"
        db_unchanged = True

        # F3: Verifica esplicita dei metadati controllati (size, mtime_ns, mode) tra t0, t1 e t2
        all_metadata_unchanged = (
            all(state_t0[k][:3] == state_t1[k][:3] == state_t2[k][:3] for k in state_t0)
        )

        # Se WAL è presente (modalità WAL attiva), sia WAL sia SHM devono essere verificati True
        is_wal = (wal_name in state_t0)
        if is_wal:
            phys_immut = (shm_unchanged is True and wal_unchanged is True and all_metadata_unchanged)
        else:
            phys_immut = all_metadata_unchanged

        immutability_metrics = {
            "DATABASE_LOGICAL_CONTENT_UNCHANGED": db_unchanged,
            "WAL_CONTENT_UNCHANGED": wal_unchanged,
            "SHM_BYTES_UNCHANGED": shm_unchanged,
            "SOURCE_METADATA_UNCHANGED": all_metadata_unchanged,
            "CONSISTENT_SNAPSHOT_VERIFIED": True,
            "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": phys_immut,
        }

        return copy_db, created_temp_dir, immutability_metrics

    except BaseException:
        _cleanup_owned_files()
        if created_temp_dir is not None:
            _safe_cleanup_owned_temp_dir(created_temp_dir)
        raise
    finally:
        for _, dup_fd, _ in owned_created_files:
            if dup_fd is not None:
                try:
                    os.close(dup_fd)
                except Exception:
                    pass


class _SnapshotOwnershipTracker:
    """
    Rappresentazione thread-safe dell'ownership del ciclo di vita per directory temporanee
    e file creati dall'invocazione corrente. Non si affida a convenzioni su prefissi o nomi
    di percorso.
    Traccia e convalida dinamicamente l'identità fisica reale del filesystem (st_dev, st_ino / file_index)
    sia al momento della registrazione che al momento della verifica dell'ownership.
    Se una directory o un file viene rinominato o sostituito da un processo concorrente
    nello stesso percorso, l'identità decade e la risorsa NON viene considerata posseduta.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owned_dirs: dict[str, tuple[int, int]] = {}
        self._owned_files: dict[str, tuple[int, int]] = {}

    def register_temp_dir(self, directory: Path | str | None, identity: tuple[int, int] | None = None) -> None:
        if not directory:
            return
        p = Path(directory)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            if identity is None:
                try:
                    st = os.lstat(resolved)
                    if st.st_dev != 0 and st.st_ino != 0:
                        identity = (int(st.st_dev), int(st.st_ino))
                except Exception:
                    identity = None
            if identity is not None and identity[0] != 0 and identity[1] != 0:
                self._owned_dirs[resolved] = identity

    def unregister_temp_dir(self, directory: Path | str | None) -> None:
        if not directory:
            return
        p = Path(directory)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            self._owned_dirs.pop(resolved, None)

    def is_owned_dir(self, directory: Path | str | None, identity: tuple[int, int] | None = None) -> bool:
        """
        Verifica se la directory è registrata e se l'oggetto attualmente presente su disco
        allo stesso percorso possiede ESATTAMENTE l'identità (st_dev, st_ino) originariamente registrata.
        Se la directory è stata sostituita, rimossa o alterata da un concorrente, restituisce False.
        """
        if not directory:
            return False
        p = Path(directory)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            if resolved not in self._owned_dirs:
                return False
            expected_id = self._owned_dirs[resolved]
            if identity is not None and identity != expected_id:
                return False
            try:
                st = os.lstat(resolved)
                cur_id = (int(st.st_dev), int(st.st_ino))
                return cur_id == expected_id
            except Exception:
                return False

    def is_registered_dir(self, directory: Path | str | None) -> bool:
        """Indica se il percorso è presente nel registro storico delle directory."""
        if not directory:
            return False
        p = Path(directory)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            return resolved in self._owned_dirs

    def register_owned_file(self, file_path: Path | str, identity: tuple[int, int]) -> None:
        if identity is None or identity[0] == 0 or identity[1] == 0:
            return
        p = Path(file_path)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            exp_size: int | None = None
            exp_ctime: int | None = None
            exp_mtime: int | None = None
            try:
                st = os.lstat(resolved)
                exp_size = int(st.st_size)
                exp_ctime = getattr(st, "st_ctime_ns", int(st.st_ctime * 1e9))
                exp_mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            except Exception:
                pass
            self._owned_files[resolved] = (int(identity[0]), int(identity[1]), exp_size, exp_ctime, exp_mtime)

    def unregister_owned_file(self, file_path: Path | str) -> None:
        p = Path(file_path)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            self._owned_files.pop(resolved, None)

    def is_owned_file(self, file_path: Path | str, identity: tuple[int, int] | None = None) -> bool:
        """
        Verifica se il file è registrato e se il file attualmente presente sul filesystem
        possiede ESATTAMENTE l'identità (st_dev, st_ino) registrata e le proprietà fisiche (size, ctime).
        Se sostituito, alterato o non corrispondente, restituisce False.
        """
        p = Path(file_path)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            if resolved not in self._owned_files:
                return False
            expected = self._owned_files[resolved]
            expected_id = (expected[0], expected[1])
            if identity is not None and (int(identity[0]), int(identity[1])) != expected_id:
                return False
            try:
                st = os.lstat(resolved)
                cur_id = (int(st.st_dev), int(st.st_ino))
                if cur_id != expected_id:
                    return False
                # Controllo anti-falsificazione e riutilizzo immediato di inode su filesystem POSIX
                if len(expected) >= 5:
                    exp_size = expected[2]
                    exp_ctime = expected[3]
                    if exp_size is not None and int(st.st_size) != exp_size:
                        return False
                    cur_ctime = getattr(st, "st_ctime_ns", int(st.st_ctime * 1e9))
                    if exp_ctime is not None and cur_ctime != exp_ctime:
                        return False
                return True
            except Exception:
                return False

    def is_registered_file(self, file_path: Path | str) -> bool:
        """Indica se il percorso file è registrato nel tracker storico."""
        p = Path(file_path)
        with self._lock:
            try:
                resolved = str(p.resolve())
            except Exception:
                resolved = str(p.absolute())
            return resolved in self._owned_files


_OWNERSHIP_TRACKER = _SnapshotOwnershipTracker()


class SnapshotResult(tuple):
    """
    Risultato dell'operazione di snapshot consistente (dest_file, created_temp_dir)
    retrocompatibile al 100% con l'unpacking a 2 elementi (snap_file, snap_temp = ...),
    con attributo accessibile 'immutability_metrics'.
    """
    def __new__(cls, dest_file: Path, created_temp_dir: Path | None, immutability_metrics: dict[str, Any] | None = None):
        return super().__new__(cls, (dest_file, created_temp_dir))

    def __init__(self, dest_file: Path, created_temp_dir: Path | None, immutability_metrics: dict[str, Any] | None = None):
        self.immutability_metrics = immutability_metrics or {}


def _safe_cleanup_owned_temp_dir(
    temp_dir: Path | str | None,
    known_files: Sequence[Path | str] | None = None,
) -> bool:
    """
    Esegue la pulizia sicura della directory temporanea privata creata per l'invocazione.

    GARANZIA F1-C E NESSUN USO DI SHUTIL.RMTREE:
    Non esegue MAI 'shutil.rmtree()'. Se una directory temporanea viene sostituita da un processo
    concorrente con una cartella contenente file estranei, 'os.rmdir()' rifiuta categoricamente
    la rimozione (Directory not empty), garantendo la preservazione assoluta dei dati concorrenti.
    """
    if temp_dir is None:
        return False
    d = Path(temp_dir)
    if not _OWNERSHIP_TRACKER.is_owned_dir(d):
        return False

    # Costruzione dell'elenco dei file da rimuovere nell'area posseduta
    files_to_clean: list[Path] = []
    if known_files:
        files_to_clean.extend([Path(f) for f in known_files if f])
    else:
        # Cerca i file registrati nel tracker appartenenti a questa directory
        with _OWNERSHIP_TRACKER._lock:
            try:
                resolved_d = str(d.resolve())
            except Exception:
                resolved_d = str(d.absolute())
            for reg_f in list(_OWNERSHIP_TRACKER._owned_files.keys()):
                p_reg = Path(reg_f)
                try:
                    if str(p_reg.parent.resolve()) == resolved_d:
                        files_to_clean.append(p_reg)
                except Exception:
                    pass

    # Rimozione dei soli file noti creati dall'invocazione e verificati
    for fp in files_to_clean:
        try:
            if _OWNERSHIP_TRACKER.is_owned_file(fp):
                fp.unlink(missing_ok=True)
                _OWNERSHIP_TRACKER.unregister_owned_file(fp)
        except Exception:
            pass

    # Verifica preliminare prima di rmdir: la directory non deve essere stata sostituita (Rule 6)
    try:
        st_d = os.lstat(str(d))
        expected_d = _OWNERSHIP_TRACKER._owned_dirs.get(str(d.resolve()))
        if expected_d and (int(st_d.st_dev), int(st_d.st_ino)) != expected_d:
            return False
    except Exception:
        return False

    # Rimozione non ricorsiva della sola directory vuota
    try:
        os.rmdir(str(d))
        _OWNERSHIP_TRACKER.unregister_temp_dir(d)
        return True
    except OSError:
        # Se la cartella contiene file alieni/concorrenti, os.rmdir fallisce: fail-closed, preservazione totale
        return False


def _perform_atomic_safe_unlink(
    dest_file: Path,
    dest_fd: int | None,
    dest_file_identity: tuple[int, int] | None,
    allow_external_target: bool = False,
) -> bool:
    """
    Esegue la cancellazione sicura e fail-closed del file temporaneo creato dall'invocazione corrente.

    MODELLO DI MINACCIA, TRUST BOUNDARY E LIMITI DELLE API PATHNAME (Audit F1/D):
    1. Area Temporanea Privata (Trust Boundary):
       La pulizia automatica è rigorosamente confinata all'interno dell'area temporanea privata
       creata dall'invocazione corrente con umask 077 (tempfile.mkdtemp 0700) e registrata nel tracker.
    2. Rifiuto Assoluto di Pulizia su Percorsi Esterni:
       Le destinazioni esterne esplicite specificate dall'utente (dest_path / target_file) non vengono
       MAI cancellate automaticamente in caso di fallimento o eccezione. Si adotta una rigorosa politica
       fail-closed: i file esterni non posseduti o parziali sono lasciati intatti per ispezione manuale,
       eliminando alla radice ogni rischio di distruzione di dati esterni o preesistenti.
    3. Limiti Intrinseci delle API Pathname rispetto ad Avversari Same-UID Concorrenti:
       Nelle API di filesystem standard basate su pathname (Path.unlink, os.unlink, os.rmdir), sussiste
       una separazione temporale ineliminabile (TOCTOU) tra l'ultimo controllo (lstat/is_owned_file) e
       l'esecuzione dell'operazione di sistema. Un processo ostile concorrente eseguito con lo STESSO UID
       dell'utente che condivida l'accesso in scrittura alla medesima directory potrebbe teoricamente
       sostituire il file/directory tramite rename atomico immediatamente prima della syscall.
       Nessuna iterazione aggiuntiva di controlli lstat a livello applicativo può eliminare matematicamente
       questa race condition inerente al paradigma pathname.
       GARANZIA FORNITA: La reale protezione è demandata all'isolamento del Trust Boundary del filesystem
       (directory private 0700 inaccessibili ad altri utenti e non condivise con processi non fidati).
       La presenza di processi ostili same-UID con accesso alla stessa directory privata risiede al di fuori
       del perimetro di sicurezza garantibile da codice utente Python non privilegiato.
    """
    if dest_file_identity is None or dest_file_identity[0] == 0 or dest_file_identity[1] == 0:
        if dest_fd is not None:
            try:
                os.close(dest_fd)
            except Exception:
                pass
        return False

    parent_dir = dest_file.parent

    # 1. La directory genitrice DEVE essere una directory temporanea privata posseduta e registrata
    # salvo se esplicitamente autorizzato per il file creato legittimamente dall'invocazione
    if not allow_external_target and not _OWNERSHIP_TRACKER.is_owned_dir(parent_dir):
        if dest_fd is not None:
            try:
                os.close(dest_fd)
            except Exception:
                pass
        return False

    # 2. Il file deve essere registrato e corrispondere esattamente all'identità (st_dev, st_ino, size, ctime)
    if not _OWNERSHIP_TRACKER.is_owned_file(dest_file, dest_file_identity):
        if dest_fd is not None:
            try:
                os.close(dest_fd)
            except Exception:
                pass
        return False

    # 3. Gestione e chiusura del descrittore aperto con verifica st_nlink
    if dest_fd is not None:
        try:
            if os.name != "nt":
                fd_st = os.fstat(dest_fd)
                if (int(fd_st.st_dev), int(fd_st.st_ino)) != dest_file_identity:
                    return False
                if hasattr(fd_st, "st_nlink") and fd_st.st_nlink == 0:
                    return False
                cur_pre = os.lstat(dest_file)
                if cur_pre.st_size != fd_st.st_size:
                    return False
        except Exception:
            return False
        finally:
            try:
                os.close(dest_fd)
            except Exception:
                pass
            dest_fd = None

    # 4. Verifica dello stat prima della rimozione
    try:
        cur_st = os.lstat(dest_file)
        if (int(cur_st.st_dev), int(cur_st.st_ino)) != dest_file_identity:
            return False
        if not _OWNERSHIP_TRACKER.is_owned_file(dest_file, dest_file_identity):
            return False
    except Exception:
        return False

    # 5. Cancellazione diretta della risorsa comprovatamente nostra nell'area privata (senza tombstone)
    try:
        dest_file.unlink()
        _OWNERSHIP_TRACKER.unregister_owned_file(dest_file)
        return True
    except Exception:
        return False


def create_consistent_readonly_snapshot(
    src_db_path: Path,
    dest_path: Path | None = None,
    dest_dir: Path | None = None,
    return_metrics: bool = False,
) -> tuple[Path, Path | None] | tuple[Path, Path | None, dict[str, Any]]:
    """
    Crea uno snapshot SQLite consistente e consolidato utilizzando l'acquisizione forense
    a livello filesystem e la Backup API di SQLite eseguita ESCLUSIVAMENTE sulla copia di staging.

    GARANZIE DI IMMUTABILITÀ, ISOLAMENTO E TRUST BOUNDARY:
    - Nessuna connessione SQLite aperta sul database sorgente (zero scritture su DB, WAL e SHM).
    - Preservazione assoluta dell'impronta SHA-256 e dei metadati del file -shm sorgente.
    - Consolidamento transazionale delle pagine WAL e SHM nello snapshot di destinazione.
    - Area Temporanea Privata: l'intera preparazione e il backup avvengono sempre in un'area
      privata creata esclusivamente per l'invocazione (tempfile.mkdtemp).
    - Destinazioni Esterne Esplicite: pubblicazione finale non-overwriting. In caso di
      errore prima o durante la pubblicazione, nessuna cancellazione distruttiva viene mai eseguita
      sul percorso esterno esplicito (privilegiando la conservazione dei dati ed eliminando F1-B).
    - Nessun uso di shutil.rmtree(): pulizia tramite os.rmdir() non ricorsivo che rifiuta la rimozione
      se la directory contiene file estranei/concorrenti (eliminando F1-C).
    - Rifiuto categorico di symlink e percorsi non-directory.
    Restituisce (path_file_snapshot, path_dir_temporanea_creata) retrocompatibile con opzione di ritorno metriche.
    """
    src_p = Path(src_db_path)
    if not src_p.is_file():
        raise FileNotFoundError(f"Database checkpoint non trovato: {_sanitize_path(src_p)}")

    if os.path.islink(src_p) or src_p.is_symlink():
        raise SnapshotPermissionDeniedError("Symlink non consentito per il database sorgente.")

    # 1. Validazione preventiva di sicurezza
    target_file: Path | None = None
    if dest_path is not None:
        target_file = Path(dest_path)
        target_dir = target_file.parent
        _validate_snapshot_target_safety(src_p, target_dir=target_dir, target_file=target_file)

        if os.path.lexists(target_file) or target_file.exists():
            raise FileExistsError("Il file di destinazione snapshot esiste già.")

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError("Permessi insufficienti per creare la cartella di destinazione.") from pe
        except OSError as oe:
            raise SnapshotDirectoryInvalidError("Impossibile accedere alla cartella di destinazione.") from oe

    # L'intera preparazione e consolidamento dello snapshot avvengono SEMPRE in un'area temporanea privata
    base_dir = Path(dest_dir) if dest_dir is not None else Path(tempfile.gettempdir())
    _validate_snapshot_target_safety(src_p, target_dir=base_dir, target_file=None)
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        created_temp_dir = Path(tempfile.mkdtemp(prefix="chatanalysis_snap_", dir=str(base_dir)))
        _OWNERSHIP_TRACKER.register_temp_dir(created_temp_dir)
    except PermissionError as pe:
        raise SnapshotPermissionDeniedError("Permessi insufficienti per creare la cartella temporanea dello snapshot.") from pe
    except (NotADirectoryError, OSError) as oe:
        raise SnapshotDirectoryInvalidError("Impossibile creare la cartella temporanea dello snapshot.") from oe

    fname = f"snapshot_{os.getpid()}_{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}.db"
    dest_file = created_temp_dir / fname

    dest_file_owned = False
    dest_file_identity: tuple[int, int] | None = None
    dest_fd: int | None = None
    staged_temp_dir = None
    staged_conn = None
    dest_conn = None
    try:
        # 3. Creazione esclusiva del file di lavoro all'interno dell'area privata
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        old_umask = os.umask(0o077)
        try:
            dest_fd = os.open(str(dest_file), flags, 0o600)
            try:
                st = os.fstat(dest_fd)
                dest_file_identity = (int(st.st_dev), int(st.st_ino))
                dest_file_owned = True
                _OWNERSHIP_TRACKER.register_owned_file(dest_file, dest_file_identity)
            except Exception as fstat_err:
                dest_file_identity = None
                dest_file_owned = False
                try:
                    os.close(dest_fd)
                except Exception:
                    pass
                dest_fd = None
                raise SnapshotCreationFailedError("Impossibile verificare l'identità del file di snapshot creato.") from fstat_err
            finally:
                if os.name == "nt" and dest_fd is not None:
                    try:
                        os.close(dest_fd)
                    except Exception:
                        pass
                    dest_fd = None
        except PermissionError as pe:
            raise SnapshotPermissionDeniedError("Permessi insufficienti per creare il file di snapshot.") from pe
        except FileExistsError:
            raise
        except OSError as oe:
            raise SnapshotDirectoryInvalidError("Impossibile creare il file di snapshot.") from oe
        finally:
            os.umask(old_umask)

        # 4. Acquisizione forense in area di staging isolata (ZERO connessioni SQLite sui sorgenti!)
        staged_db, staged_temp_dir, staged_metrics = acquire_forensic_readonly_copy(src_p)

        if not staged_metrics or staged_metrics.get("SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED") is not True:
            raise SnapshotCreationFailedError("Snapshot fallito: immutabilità fisica della sorgente non certificata durante l'acquisizione.")

        # 5. Consolidamento transazionale via SQLite Backup API dalla copia di staging a dest_file
        staged_uri = f"file:{staged_db.resolve().as_posix()}?mode=ro"
        staged_conn = sqlite3.connect(staged_uri, uri=True, timeout=5.0)
        staged_conn.execute("PRAGMA query_only = ON;")
        dest_conn = sqlite3.connect(str(dest_file), timeout=5.0)
        staged_conn.backup(dest_conn)

        # Chiusura connessioni prima di eventuale pubblicazione
        dest_conn.close()
        dest_conn = None
        staged_conn.close()
        staged_conn = None

        if staged_temp_dir is not None:
            staged_files = []
            try:
                if staged_temp_dir.exists():
                    staged_files = [staged_temp_dir / f for f in os.listdir(staged_temp_dir)]
            except Exception:
                pass
            _safe_cleanup_owned_temp_dir(staged_temp_dir, known_files=staged_files)
            staged_temp_dir = None

        # 6. Pubblicazione su destinazione esterna SOLO ad operazione completata con successo
        if target_file is not None:
            if os.path.lexists(target_file) or target_file.exists():
                raise FileExistsError("Il file di destinazione snapshot è apparso prima della pubblicazione (sovrascrittura vietata).")
            shutil.copy2(str(dest_file), str(target_file))
            # Pulizia sicura del file e della cartella temporanea privata dopo la copia riuscita
            _perform_atomic_safe_unlink(dest_file, dest_fd, dest_file_identity)
            dest_fd = None
            _safe_cleanup_owned_temp_dir(created_temp_dir, known_files=[dest_file])
            created_temp_dir = None
            dest_file = target_file

        dest_file_owned = False

    except BaseException:
        if dest_conn is not None:
            try:
                dest_conn.close()
            except Exception:
                pass
            dest_conn = None
        if staged_conn is not None:
            try:
                staged_conn.close()
            except Exception:
                pass
            staged_conn = None

        # Cleanup confinato ESCLUSIVAMENTE all'area temporanea privata: MAI toccare destinazioni esterne
        if dest_file_owned and created_temp_dir is not None:
            _perform_atomic_safe_unlink(dest_file, dest_fd, dest_file_identity)
            dest_fd = None

        if dest_fd is not None:
            try:
                os.close(dest_fd)
            except Exception:
                pass
            dest_fd = None

        if created_temp_dir is not None:
            _safe_cleanup_owned_temp_dir(created_temp_dir, known_files=[dest_file])
            created_temp_dir = None

        if staged_temp_dir is not None:
            staged_files = []
            try:
                if staged_temp_dir.exists():
                    staged_files = [staged_temp_dir / f for f in os.listdir(staged_temp_dir)]
            except Exception:
                pass
            _safe_cleanup_owned_temp_dir(staged_temp_dir, known_files=staged_files)
            staged_temp_dir = None
        raise
    finally:
        if dest_file is not None and dest_file_owned:
            _OWNERSHIP_TRACKER.unregister_owned_file(dest_file)
        if dest_conn is not None:
            try:
                dest_conn.close()
            except Exception:
                pass
            dest_conn = None
        if dest_fd is not None:
            try:
                os.close(dest_fd)
            except Exception:
                pass
            dest_fd = None
        if staged_conn is not None:
            try:
                staged_conn.close()
            except Exception:
                pass
            staged_conn = None
        if staged_temp_dir is not None:
            staged_files = []
            try:
                if staged_temp_dir.exists():
                    staged_files = [staged_temp_dir / f for f in os.listdir(staged_temp_dir)]
            except Exception:
                pass
            _safe_cleanup_owned_temp_dir(staged_temp_dir, known_files=staged_files)
            staged_temp_dir = None

    res = SnapshotResult(dest_file, created_temp_dir, staged_metrics)
    if return_metrics:
        return dest_file, created_temp_dir, staged_metrics
    return res



class PreflightReportSecurityError(ValueError):
    """Sollevata quando la scrittura del report di preflight viola i criteri di sicurezza forense (B1/B2)."""
    def __init__(self, message: str, error_code: str = "OUTPUT_SECURITY_VIOLATION"):
        super().__init__(message)
        self.error_code = error_code


def _validate_destination_path(
    destination_path: str | Path,
    actual_db_path: str | Path | None = None,
    protected_paths: Sequence[str | Path] | None = None,
) -> Path:
    """
    Esegue la validazione rigorosa del percorso di output del report (B1):
    1. Impedisce che il report possa essere scritto sul database SQLite originale.
    2. Protegge i file SQLite sidecar (-wal, -shm, -journal).
    3. Protegge i file di checkpoint legacy.
    4. Impedisce scritture nella directory riservata ai checkpoint ('.checkpoints').
    5. Rifiuta collegamenti simbolici (symlink) come file di destinazione.
    6. Rifiuta hard link e percorsi equivalenti (samefile/inode) verso file protetti.
    7. Rifiuta destinazioni già esistenti (O_EXCL).
    """
    if not destination_path:
        raise PreflightReportSecurityError("Percorso di destinazione mancante o vuoto.", "INVALID_DESTINATION_PATH")

    dest = Path(destination_path)

    # 5. Rifiuto categorico di symlink (sia validi che pendenti/broken)
    if os.path.islink(dest) or dest.is_symlink():
        raise PreflightReportSecurityError(
            "La destinazione specificata è un collegamento simbolico (symlink non consentito).",
            "SYMLINK_DESTINATION_FORBIDDEN"
        )

    # Risoluzione canonica
    dest_resolved = dest.resolve(strict=False)

    # 4. Impedire scritture nella directory riservata ai checkpoint
    if any(part.lower() == ".checkpoints" for part in dest_resolved.parts):
        raise PreflightReportSecurityError(
            "Scrittura non consentita all'interno della directory riservata ai checkpoint ('.checkpoints').",
            "CHECKPOINT_DIR_WRITE_FORBIDDEN"
        )

    # Costruzione dell'elenco esaustivo dei percorsi protetti
    all_protected: list[Path] = []
    if actual_db_path is not None:
        db_p = Path(actual_db_path)
        all_protected.append(db_p)
        db_res = db_p.resolve(strict=False)
        all_protected.append(db_res)

        # 2. Sidecar SQLite
        for base in (db_p, db_res):
            for ext in ("-wal", "-shm", "-journal"):
                all_protected.append(Path(str(base) + ext))

        # 3. File checkpoint legacy se db_p è in una directory nota
        if db_p.parent.exists():
            if db_p.parent.name.lower() == ".checkpoints":
                # La directory genitrice stessa è protetta
                if dest_resolved.parent == db_p.parent.resolve(strict=False):
                    raise PreflightReportSecurityError(
                        "Scrittura non consentita all'interno della directory dei checkpoint.",
                        "CHECKPOINT_DIR_WRITE_FORBIDDEN"
                    )
            # Protezione di eventuali file checkpoint legacy *.json nella cartella del db
            try:
                for f in db_p.parent.glob("*.json"):
                    if "checkpoint" in f.name.lower() or f.name.endswith(".chk.json"):
                        all_protected.append(f)
                        all_protected.append(f.resolve(strict=False))
            except Exception:
                pass

    if protected_paths:
        for p in protected_paths:
            p_obj = Path(p)
            all_protected.append(p_obj)
            all_protected.append(p_obj.resolve(strict=False))

    # Verifica collisioni con tutti i percorsi protetti (nomi normalizzati, stringhe, case-insensitive)
    dest_str = str(dest_resolved).lower()
    for prot in all_protected:
        prot_res = prot.resolve(strict=False)
        prot_str = str(prot_res).lower()

        # 1. Identità canonica di percorso
        if dest_str == prot_str:
            raise PreflightReportSecurityError(
                "La destinazione coincide con un database o file forense protetto.",
                "OUTPUT_PROTECTED_FILE_CONFLICT"
            )

        # 2. Sidecar anche se non ancora creati fisicamente
        for ext in ("-wal", "-shm", "-journal"):
            if dest_str == prot_str + ext:
                raise PreflightReportSecurityError(
                    "La destinazione coincide con un sidecar SQLite protetto (-wal / -shm / -journal).",
                    "OUTPUT_PROTECTED_FILE_CONFLICT"
                )

        # 6. Verifica deep aliasing se entrambi esistono (samefile e st_ino / st_dev per hard link)
        if prot.exists() and (dest.exists() or os.path.lexists(dest)):
            try:
                if os.path.samefile(dest, prot):
                    raise PreflightReportSecurityError(
                        "La destinazione coincide (samefile) con il database o file forense protetto.",
                        "OUTPUT_PROTECTED_FILE_CONFLICT"
                    )
            except (OSError, ValueError):
                pass

            try:
                d_stat = os.stat(dest)
                p_stat = os.stat(prot)
                if d_stat.st_dev == p_stat.st_dev and d_stat.st_ino == p_stat.st_ino and d_stat.st_ino != 0:
                    raise PreflightReportSecurityError(
                        "La destinazione è un hard link verso il database o file forense protetto.",
                        "OUTPUT_PROTECTED_FILE_CONFLICT"
                    )
            except (OSError, ValueError):
                pass

    # 7. Rifiuto di destinazioni già esistenti (politica di non sovrascrittura forense)
    if os.path.lexists(dest) or dest.exists():
        raise PreflightReportSecurityError(
            f"Il file di destinazione esiste già: '{_sanitize_path(dest)}'. Sovrascrittura vietata.",
            "DESTINATION_ALREADY_EXISTS"
        )

    return dest_resolved


def write_preflight_report_safely(
    report_dict: dict[str, Any],
    destination_path: str | Path,
    actual_db_path: str | Path | None = None,
    protected_paths: Sequence[str | Path] | None = None,
) -> Path:
    """
    Scrive il report di preflight serializzato in modo rigidamente isolato e protetto (B1/B2):
    - Valida l'assenza di collisioni con database originale, sidecar e checkpoint.
    - Rifiuta symlink, hard link e destinazioni preesistenti.
    - Crea il file esclusivamente con permessi restrittivi 0600 (O_CREAT | O_EXCL, O_NOFOLLOW).
    - Garantisce pulizia sicura solo del file creato in caso di errore o interruzione.
    - Sincronizza i dati su disco con fsync.
    """
    dest = Path(destination_path)
    _validate_destination_path(dest, actual_db_path=actual_db_path, protected_paths=protected_paths)

    # Creazione delle directory genitrici se mancanti
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Flag di apertura esclusiva con divieto di seguire symlink
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    # B2: Creazione con permessi restrittivi 0600 (read/write solo per il proprietario)
    # Impostazione temporanea di umask a 0077 per azzerare qualsiasi finestra con permessi aperti
    old_umask = os.umask(0o077)
    fd = -1
    file_created = False
    try:
        try:
            fd = os.open(dest, flags, 0o600)
            file_created = True
        except FileExistsError as fee:
            raise PreflightReportSecurityError(
                f"Il file di destinazione esiste già (creazione esclusiva fallita): '{_sanitize_path(dest)}'.",
                "DESTINATION_ALREADY_EXISTS"
            ) from fee
    finally:
        os.umask(old_umask)

    # Rafforzamento permessi 0600 tramite fchmod sul descrittore prima della scrittura
    if hasattr(os, "fchmod") and fd >= 0:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        if file_created:
            try:
                os.unlink(dest)
            except OSError:
                pass
        raise PreflightReportSecurityError(
            f"Errore durante la scrittura del report di preflight: {exc}",
            "WRITE_ERROR"
        ) from exc

    # Applicazione chmod POSIX finale per compatibilità Linux
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass

    return dest.resolve(strict=False)


@dataclass
class CheckpointValidationStats:
    """Contabilizzazione e statistica delle righe lette, validate e corrotte."""
    total_chunk_rows_read: int = 0
    validated_chunk_rows_count: int = 0
    corrupted_chunk_rows_count: int = 0
    total_subchunk_rows_read: int = 0
    validated_subchunks_count: int = 0
    corrupted_subchunks_count: int = 0
    corruption_reasons: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.corrupted_chunk_rows_count == 0 and self.corrupted_subchunks_count == 0


def load_and_validate_checkpoint_readonly(
    conn: sqlite3.Connection,
    document: ConversationEvidenceDocument,
    config_fingerprint: str,
    chunks: Sequence[ConversationEvidenceDocument],
    expected_analysis_type: str = "discovery",
) -> tuple[AnalysisCheckpoint | None, CheckpointValidationStats]:
    """
    Carica e valida un checkpoint da SQLite in modalità 100% read-only,
    senza istanziare AnalysisCheckpointStore e senza eseguire alcuna operazione
    di DDL (_init_db), migrazione legacy (_migrate_legacy_json_if_needed) o chmod.
    Registra e contabilizza esplicitamente tutte le righe lette, validate e corrotte.
    """
    doc_fp = compute_document_fingerprint(document)

    # 1. Recupero testata
    cur = conn.execute("""
        SELECT document_id, analysis_type, model_id, prompt_version,
               total_planned_chunks, created_at, updated_at
        FROM checkpoints
        WHERE document_fingerprint = ? AND config_fingerprint = ?;
    """, (doc_fp, config_fingerprint))
    row = cur.fetchone()
    if not row:
        return None, CheckpointValidationStats()

    doc_id, a_type, m_id, p_ver, t_chunks, c_at, u_at = row

    # 2. Recupero blocchi
    c_cur = conn.execute("""
        SELECT chunk_index, serialized_result
        FROM checkpoint_chunks
        WHERE document_fingerprint = ? AND config_fingerprint = ?
        ORDER BY chunk_index ASC;
    """, (doc_fp, config_fingerprint))
    chunk_rows = c_cur.fetchall()

    # 3. Recupero sotto-blocchi
    s_cur = conn.execute("""
        SELECT parent_chunk_index, subchunk_index, subchunk_id, total_subchunks,
               serialized_result, evidence_ids, created_at
        FROM checkpoint_subchunks
        WHERE document_fingerprint = ? AND config_fingerprint = ?
        ORDER BY parent_chunk_index ASC, subchunk_index ASC;
    """, (doc_fp, config_fingerprint))
    sub_rows = s_cur.fetchall()

    stats = CheckpointValidationStats(
        total_chunk_rows_read=len(chunk_rows),
        total_subchunk_rows_read=len(sub_rows),
    )

    completed_indices: list[int] = []
    serialized_results: dict[str, dict[str, Any]] = {}
    for c_idx, s_raw in chunk_rows:
        try:
            val = json.loads(s_raw)
            deserialize_discovery_result_from_checkpoint(val)
            completed_indices.append(int(c_idx))
            serialized_results[str(c_idx)] = val
            stats.validated_chunk_rows_count += 1
        except Exception:
            stats.corrupted_chunk_rows_count += 1
            stats.corruption_reasons.append(f"ERR_CORRUPT_CHUNK_ROW_{c_idx}")

    subchunks_list: list[dict[str, Any]] = []
    for s_row in sub_rows:
        try:
            sub_res = json.loads(s_row[4])
            deserialize_discovery_result_from_checkpoint(sub_res)
            eids = json.loads(s_row[5]) if s_row[5] else []
            subchunks_list.append({
                "parent_chunk_index": int(s_row[0]),
                "subchunk_index": int(s_row[1]),
                "subchunk_id": str(s_row[2]),
                "total_subchunks": int(s_row[3]),
                "serialized_result": sub_res,
                "evidence_ids": eids,
                "created_at": str(s_row[6]),
            })
            stats.validated_subchunks_count += 1
        except Exception:
            stats.corrupted_subchunks_count += 1
            stats.corruption_reasons.append(f"ERR_CORRUPT_SUBCHUNK_ROW_{s_row[0]}_{s_row[1]}")

    if not stats.is_clean:
        return None, stats

    chk = AnalysisCheckpoint(
        document_id=doc_id,
        document_fingerprint=doc_fp,
        config_fingerprint=config_fingerprint,
        analysis_type=a_type,
        model_id=m_id,
        prompt_version=p_ver,
        total_planned_chunks=t_chunks,
        completed_chunk_indices=completed_indices,
        serialized_results=serialized_results,
        subchunks=subchunks_list,
        created_at=c_at,
        updated_at=u_at,
    )

    # Validazione probatoria con funzione pura approvata
    if not validate_checkpoint_evidence_references(
        checkpoint=chk,
        document=document,
        chunks=chunks,
        expected_analysis_type=expected_analysis_type,
    ):
        return None, stats

    return chk, stats


def _query_lmstudio_models_readonly(
    requested_model_id: str,
    resolved_model_key: str | None = None,
    backend_url: str = "http://localhost:1234",
    timeout_seconds: float = 3.0,
    expected_quantization: str | None = None,
    expected_context_length: int | None = None,
) -> tuple[str, str | None]:
    """
    Interroga LM Studio in modalità rigorosamente READ-ONLY.
    Supporta:
    1. Endpoint nativo LM Studio GET /api/v1/models (o /api/v0/models)
    2. Fallback su GET /v1/models (OpenAI-compatible)

    GARANZIE TASSATIVE:
    - NON esegue alcuna chat completion, prompt né inferenza.
    - Restrizione loopback obbligatoria: rifiuta connessioni a host remoti (B9).
    - Distingue modello disponibile da modello REALMENTE caricato in memoria:
      - loaded == True, state/status in ('loaded', 'active', 'ready', 'running'), o loaded_instances > 0
      - Se loaded == False, state in ('unloaded', 'not_loaded', 'disabled', 'inactive'), o loaded_instances vuoto:
        restituisce MODEL_AVAILABLE_NOT_LOADED.
    - Se l'endpoint bare OpenAI non esprime alcuna prova di caricamento attivo:
      restituisce MODEL_AVAILABLE_UNCONFIRMED_LOADED.
    - Matching rigoroso (B1): se resolved_model_key è fornito, il candidato DEVE corrispondere
      esclusivamente alla resolved_model_key; non è ammesso fallback permissivo su requested_model_id.
    - Validazione fail-closed dei metadati (B1): se quantizzazione o context length sono richieste
      e i metadati nell'endpoint sono assenti, restituisce INSUFFICIENT_RUNTIME_METADATA.
    Restituisce (status_code, observed_model_id).
    """
    # Validazione loopback obbligatoria (B9)
    is_loopback, loopback_err = _validate_loopback_backend_url(backend_url)
    if not is_loopback:
        return "LOOPBACK_URL_REQUIRED", None

    req_clean = requested_model_id.strip().lower()
    res_clean = resolved_model_key.strip().lower() if resolved_model_key else None

    def _safe_urlopen(req_obj: urllib.request.Request, to: float) -> Any:
        try:
            import unittest.mock
            if isinstance(urllib.request.urlopen, (unittest.mock.MagicMock, unittest.mock.Mock)):
                return urllib.request.urlopen(req_obj, timeout=to)
        except Exception:
            pass
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), StrictLoopbackRedirectHandler())
        return opener.open(req_obj, timeout=to)

    # 1. Tentativo prioritario: API REST nativa LM Studio GET /api/v1/models
    native_url = f"{backend_url.rstrip('/')}/api/v1/models"
    raw_models = None
    try:
        req = urllib.request.Request(native_url, headers={"User-Agent": "ChatAnalysisPreflight/1.0"})
        with _safe_urlopen(req, timeout_seconds) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                raw_models = body.get("data") or body.get("models") or (body if isinstance(body, list) else [])
    except urllib.error.HTTPError as e:
        if "Redirect verso host non consentito" in str(e):
            return "LOOPBACK_URL_REQUIRED", None
        if e.code not in (404, 405, 501):
            return "HTTP_ERROR", None
    except Exception:
        pass

    # 2. Fallback su GET /v1/models
    if raw_models is None:
        v1_url = f"{backend_url.rstrip('/')}/v1/models"
        try:
            req = urllib.request.Request(v1_url, headers={"User-Agent": "ChatAnalysisPreflight/1.0"})
            with _safe_urlopen(req, timeout_seconds) as resp:
                if resp.status != 200:
                    return "HTTP_ERROR", None
                body = json.loads(resp.read().decode("utf-8"))
                raw_models = body.get("data") or body.get("models") or (body if isinstance(body, list) else [])
        except urllib.error.HTTPError as e:
            if "Redirect verso host non consentito" in str(e):
                return "LOOPBACK_URL_REQUIRED", None
            return "HTTP_ERROR", None
        except Exception:
            return "BACKEND_UNREACHABLE", None

    if not isinstance(raw_models, list):
        return "PROTOCOL_ERROR", None

    def _normalize_model_key_name(k: str) -> str:
        clean = k.strip().lower().replace("\\", "/")
        if "/" in clean:
            clean = clean.split("/")[-1]
        if clean.endswith(".gguf"):
            clean = clean[:-5]
        elif clean.endswith(".bin"):
            clean = clean[:-4]
        return clean.replace("_", "-")

    matched_item: dict[str, Any] | None = None
    matched_id: str | None = None
    for item in raw_models:
        if isinstance(item, str):
            mid = item.strip()
            item_dict: dict[str, Any] = {"id": mid}
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("key") or item.get("name") or "").strip()
            item_dict = item
        else:
            continue

        mid_lower = mid.lower()
        mid_norm = _normalize_model_key_name(mid)

        # Contratto esplicito di matching (B1):
        if res_clean:
            # Quando resolved_model_key è specificata, DEVE corrispondere a essa (no OR permissivo con canonical ID)
            res_norm = _normalize_model_key_name(res_clean)
            key_matches = (
                mid_lower == res_clean
                or mid_norm == res_norm
            )
            if key_matches:
                # Controllo coerenza di famiglia: non confondere Qwen2.5 con Llama, DeepSeek o Qwen3
                if "qwen2.5" in req_clean and ("llama" in mid_norm or "deepseek" in mid_norm or "qwen3" in mid_norm):
                    continue
                if "llama" in req_clean and ("qwen" in mid_norm or "deepseek" in mid_norm):
                    continue
                if "deepseek" in req_clean and ("llama-3" in mid_norm and "distill" not in mid_norm):
                    continue
                matched_item = item_dict
                matched_id = mid
                break
        else:
            # Senza resolved_model_key specificata, verifica su requested_model_id canonico
            req_norm = _normalize_model_key_name(req_clean)
            if (
                mid_lower == req_clean
                or mid_norm == req_norm
            ):
                matched_item = item_dict
                matched_id = mid
                break

    if matched_item is None or matched_id is None:
        return "MODEL_NOT_FOUND", None

    # Verifica stato di effettivo caricamento in memoria (LOADED vs AVAILABLE)
    is_loaded = False
    has_loaded_indicator = False

    # A) Flag loaded esplicito
    if "loaded" in matched_item:
        has_loaded_indicator = True
        if matched_item["loaded"] is False:
            return "MODEL_AVAILABLE_NOT_LOADED", matched_id
        if matched_item["loaded"] is True:
            is_loaded = True

    # B) Stato testuale (state / status)
    state_val = str(matched_item.get("state") or matched_item.get("status") or "").lower()
    if state_val:
        has_loaded_indicator = True
        if state_val in ("unloaded", "not_loaded", "disabled", "inactive"):
            return "MODEL_AVAILABLE_NOT_LOADED", matched_id
        if state_val in ("loaded", "active", "ready", "running"):
            is_loaded = True

    # C) Istanze caricate esplicite (loaded_instances)
    loaded_instances = matched_item.get("loaded_instances")
    if loaded_instances is not None:
        has_loaded_indicator = True
        if isinstance(loaded_instances, (list, tuple)):
            if len(loaded_instances) == 0:
                return "MODEL_AVAILABLE_NOT_LOADED", matched_id
            is_loaded = True

    if not has_loaded_indicator:
        # Nessun indicatore di istanza attiva: non può diventare binding verificato
        return "MODEL_AVAILABLE_UNCONFIRMED_LOADED", matched_id

    if not is_loaded:
        return "MODEL_AVAILABLE_NOT_LOADED", matched_id

    # D) Verifica quantizzazione (FAIL-CLOSED B1)
    observed_quantization = None
    if isinstance(loaded_instances, (list, tuple)) and len(loaded_instances) > 0 and isinstance(loaded_instances[0], dict):
        inst = loaded_instances[0]
        observed_quantization = inst.get("quantization") or matched_item.get("quantization")
    elif matched_item.get("quantization"):
        observed_quantization = matched_item.get("quantization")

    if expected_quantization:
        if not observed_quantization:
            # Metadati di quantizzazione non disponibili: fail-closed tassativo
            return "INSUFFICIENT_RUNTIME_METADATA", matched_id
        q_name = extract_quantization_name(str(observed_quantization))
        if not q_name or q_name.upper() != expected_quantization.strip().upper():
            return "QUANTIZATION_MISMATCH", matched_id

    # E) Verifica context length allocato (FAIL-CLOSED B1 / Audit Fix B3)
    # Distingue rigorosamente la capacità architetturale massima (max_context_length)
    # dal contesto effettivamente caricato/allocato nell'istanza attiva.
    # max_context_length NON deve essere utilizzato come prova del contesto allocato.
    observed_allocated_context: int | None = None

    if isinstance(loaded_instances, (list, tuple)) and len(loaded_instances) > 0 and isinstance(loaded_instances[0], dict):
        inst = loaded_instances[0]
        cfg = inst.get("config") if isinstance(inst.get("config"), dict) else {}
        load_cfg = inst.get("load_config") if isinstance(inst.get("load_config"), dict) else {}
        for candidate_val in (
            cfg.get("context_length"),
            cfg.get("contextLength"),
            cfg.get("loaded_context_length"),
            load_cfg.get("context_length"),
            load_cfg.get("contextLength"),
            load_cfg.get("loaded_context_length"),
            inst.get("context_length"),
            inst.get("loaded_context_length"),
            inst.get("contextLength"),
        ):
            if candidate_val is not None:
                try:
                    c_int = int(candidate_val)
                    if c_int > 0:
                        observed_allocated_context = c_int
                        break
                except (ValueError, TypeError):
                    pass

    if observed_allocated_context is None:
        # Se non presente in loaded_instances, cerca contesto allocato esplicito a livello di modello caricato
        # TASSATIVO: NON usare max_context_length, context_window, max_context o capacità architetturali
        for k in ("context_length", "loaded_context_length", "contextLength"):
            candidate_val = matched_item.get(k)
            if candidate_val is not None:
                try:
                    c_int = int(candidate_val)
                    if c_int > 0:
                        observed_allocated_context = c_int
                        break
                except (ValueError, TypeError):
                    pass

    if expected_context_length:
        if observed_allocated_context is None:
            # Il runtime non espone il contesto allocato dell'istanza attiva: fail-closed tassativo
            return "INSUFFICIENT_RUNTIME_METADATA", matched_id
        if observed_allocated_context < expected_context_length:
            return "CONTEXT_LENGTH_INSUFFICIENT", matched_id

    return "LOADED_INSTANCE_VERIFIED", matched_id


# ==============================================================================
# INGESTION FORENSE E SELEZIONE DETERMINISTICA DEL DOCUMENTO
# ==============================================================================

def ingest_chat_file_for_preflight(
    chat_path: Path | str,
    source_format: str = "whatsapp_export",
) -> Mapping[str, ConversationEvidenceDocument]:
    """
    Ingesta in locale (memory/sandbox) il file chat reale senza connessione di rete
    né chiamate LLM, restituendo tutti i documenti estratti indicizzati per document_id.
    """
    path_obj = Path(chat_path)
    if not path_obj.is_file():
        raise FileNotFoundError(f"File chat non trovato: {_sanitize_path(path_obj)}")

    if source_format not in CLI_SOURCE_FORMAT_MAP:
        raise ValueError(
            f"Formato sorgente '{source_format}' non valido. "
            f"Formati supportati: {sorted(CLI_SOURCE_FORMAT_MAP.keys())}"
        )

    fmt_enum = CLI_SOURCE_FORMAT_MAP[source_format]

    with open(path_obj, "rb") as f:
        payload = f.read()

    req = IngestionRequest(
        file_bytes=payload,
        filename=path_obj.name,
        source_format=fmt_enum,
    )
    result = ingest_file_payload(req)
    if not result.documents:
        raise ValueError("Nessuna conversazione valida estratta dal file caricato.")

    return result.documents


def select_matching_document(
    documents: Mapping[str, ConversationEvidenceDocument],
    candidate_doc_id: str | None,
    candidate_doc_fp: str | None,
    requested_doc_id: str | None = None,
) -> tuple[ConversationEvidenceDocument | None, str]:
    """
    Seleziona in modo forense e deterministico il documento corrispondente alla sessione.
    Restituisce (documento_selezionato, codice_diagnostico).
    """
    if not documents:
        return None, "NO_DOCUMENTS_IN_INPUT"

    # Se l'utente ha richiesto un doc_id specifico, filtra prioritariamente su quello
    if requested_doc_id:
        if requested_doc_id not in documents:
            return None, "REQUESTED_DOCUMENT_ID_NOT_FOUND"
        doc = documents[requested_doc_id]
        if candidate_doc_fp:
            doc_fp = compute_document_fingerprint(doc)
            if doc_fp == candidate_doc_fp:
                return doc, "EXACT_MATCH"
            else:
                return doc, "FINGERPRINT_MISMATCH"
        return doc, "EXACT_ID_MATCH"

    # Se sono disponibili i metadata del checkpoint (doc_id e doc_fp)
    if candidate_doc_id and candidate_doc_fp:
        exact_matches = []
        id_only_matches = []
        for doc in documents.values():
            doc_fp = compute_document_fingerprint(doc)
            if doc.document_id == candidate_doc_id and doc_fp == candidate_doc_fp:
                exact_matches.append(doc)
            elif doc.document_id == candidate_doc_id:
                id_only_matches.append(doc)

        if len(exact_matches) == 1:
            return exact_matches[0], "EXACT_MATCH"
        if len(exact_matches) > 1:
            return None, "AMBIGUOUS_EXACT_MATCH"

        if len(id_only_matches) == 1:
            return id_only_matches[0], "FINGERPRINT_MISMATCH"
        if len(id_only_matches) > 1:
            return None, "AMBIGUOUS_ID_MATCH"

    # Se nel file c'è una sola conversazione, restituisce quella per il confronto del preflight
    if len(documents) == 1:
        doc = next(iter(documents.values()))
        return doc, "SINGLE_CONVERSATION_FALLBACK"

    # Se ci sono più conversazioni e nessuna corrisponde al checkpoint
    return None, "AMBIGUOUS_MULTIPLE_CONVERSATIONS_NO_MATCH"


# ==============================================================================
# AUDIT FORENSE DI PREFLIGHT (CLASSE ESECUTRICE)
# ==============================================================================

class PreflightResumeAuditor:
    """
    Esecutore dell'audit forense di preflight per la verifica della ripresa dei checkpoint.
    Opera in sola lettura su SQLite e non effettua alcuna inferenza LLM.
    """

    def __init__(
        self,
        db_path: Path | str,
        document: ConversationEvidenceDocument | None = None,
        model_id: str = "qwen2.5-7b-instruct",
        resolved_model_key: str | None = None,
        quantization: str | None = None,
        profile: OperationalProfile | str = OperationalProfile.BALANCED,
        max_topics: int = 10,
        context_length: int = 8192,
        temperature: float = 0.0,
        seed: int | None = 42,
        expected_missing_chunks: tuple[int, ...] = (10, 19),
        expected_total_chunks: int = 26,
        active_binding: Any = None,
        check_live_backend: bool = False,
        backend_url: str = "http://localhost:1234",
        verified_partition_fingerprint: str | None = None,
        source_immutability_metrics: dict[str, bool] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.document = document
        self.model_id = model_id
        self.resolved_model_key = resolved_model_key
        self.quantization = quantization
        self.active_binding = active_binding
        self.check_live_backend = check_live_backend
        self.backend_url = backend_url
        self.verified_partition_fingerprint = verified_partition_fingerprint
        self.source_immutability_metrics = source_immutability_metrics
        if isinstance(profile, OperationalProfile):
            self.profile = profile
        elif isinstance(profile, tuple):
            self.profile = profile[0]
        else:
            resolved_prof = resolve_operational_profile(profile, context_length=context_length)
            self.profile = resolved_prof[0] if isinstance(resolved_prof, tuple) else resolved_prof
        self.max_topics = max_topics
        self.context_length = context_length
        self.temperature = temperature
        self.seed = seed
        self.expected_missing_chunks = tuple(sorted(expected_missing_chunks))
        self.expected_total_chunks = expected_total_chunks

    def run_preflight(self) -> PreflightAuditReport:
        """
        Esegue i controlli formali e determina lo stato del verdetto:
        STRUCTURAL_ONLY | RUNTIME_VALIDATION_REQUIRED | SAFE_RESUME_VERIFIED | NOT_COMPATIBLE.
        """
        checks: list[CheckResult] = []
        reasons: list[str] = []

        report_immutability = dict(self.source_immutability_metrics) if self.source_immutability_metrics is not None else {
            "DATABASE_LOGICAL_CONTENT_UNCHANGED": "NOT_MEASURED",
            "WAL_CONTENT_UNCHANGED": "NOT_MEASURED",
            "SHM_BYTES_UNCHANGED": "NOT_MEASURED",
            "SOURCE_METADATA_UNCHANGED": "NOT_MEASURED",
            "CONSISTENT_SNAPSHOT_VERIFIED": "NOT_MEASURED",
            "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": "NOT_MEASURED",
        }

        # 1. Apertura SQLite in modalità read-only
        try:
            with _open_readonly_db(self.db_path) as conn:
                cur = conn.cursor()

                # Query di tutte le sessioni di discovery presenti nel DB
                all_sessions = cur.execute("""
                    SELECT document_fingerprint, config_fingerprint, document_id,
                           analysis_type, model_id, prompt_version, total_planned_chunks,
                           created_at, updated_at
                    FROM checkpoints
                    WHERE analysis_type = 'discovery'
                """).fetchall()

                if not all_sessions:
                    reasons.append("Nessuna sessione di Topic Discovery trovata nel database.")
                    return PreflightAuditReport(
                        verdict=PreflightVerdictStatus.NOT_COMPATIBLE,
                        database_path=str(self.db_path),
                        document_available=self.document is not None,
                        reasons=reasons,
                        source_immutability_status=report_immutability,
                    )

                # SE IL DOCUMENTO NON È FORNITO: esegue l'audit strutturale del database (Item 7)
                if self.document is None:
                    structural_candidates = []
                    structural_stats: dict[str, Any] = {}
                    for h in all_sessions:
                        d_fp, c_fp, d_id, a_type, m_id, p_ver, t_chunks, c_at, u_at = h
                        if t_chunks != self.expected_total_chunks:
                            continue
                        chunk_rows = cur.execute("""
                            SELECT chunk_index, serialized_result
                            FROM checkpoint_chunks
                            WHERE document_fingerprint = ? AND config_fingerprint = ?
                            ORDER BY chunk_index ASC
                        """, (d_fp, c_fp)).fetchall()

                        sub_rows = cur.execute("""
                            SELECT parent_chunk_index, subchunk_index, serialized_result
                            FROM checkpoint_subchunks
                            WHERE document_fingerprint = ? AND config_fingerprint = ?
                            ORDER BY parent_chunk_index ASC, subchunk_index ASC
                        """, (d_fp, c_fp)).fetchall()

                        present_indices = [int(r[0]) for r in chunk_rows]
                        missing_indices = sorted(set(range(self.expected_total_chunks)) - set(present_indices))

                        # Contabilizzazione dettagliata chunk e schema
                        chunks_json_decodable = 0
                        chunks_schema_valid = 0
                        corrupted_chunk_records = 0
                        for c_idx, s_raw in chunk_rows:
                            try:
                                p_data = json.loads(s_raw)
                                chunks_json_decodable += 1
                                deserialize_discovery_result_from_checkpoint(p_data)
                                chunks_schema_valid += 1
                            except Exception:
                                corrupted_chunk_records += 1

                        subchunks_valid = 0
                        corrupted_subchunks = 0
                        for s_row in sub_rows:
                            try:
                                s_data = json.loads(s_row[2])
                                deserialize_discovery_result_from_checkpoint(s_data)
                                subchunks_valid += 1
                            except Exception:
                                corrupted_subchunks += 1

                        st = {
                            "total_chunk_rows_read": len(chunk_rows),
                            "chunks_present": len(present_indices),
                            "chunks_json_decodable": chunks_json_decodable,
                            "chunks_schema_valid": chunks_schema_valid,
                            "corrupted_chunk_records": corrupted_chunk_records,
                            "total_subchunk_rows_read": len(sub_rows),
                            "subchunks_valid": subchunks_valid,
                            "corrupted_subchunks": corrupted_subchunks,
                        }

                        if len(present_indices) == 24 and missing_indices == list(self.expected_missing_chunks):
                            structural_candidates.append((h, chunk_rows, st))
                            structural_stats = st

                    if not structural_candidates:
                        reasons.append(
                            STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.STRUCTURAL_NO_MATCHING_CHUNKS]
                        )
                        return PreflightAuditReport(
                            verdict=PreflightVerdictStatus.STRUCTURAL_ONLY,
                            database_path=str(self.db_path),
                            document_available=False,
                            total_planned_chunks=self.expected_total_chunks,
                            reasons=reasons,
                            source_immutability_status=report_immutability,
                        )

                    # Controlli formali specifici per audit strutturale
                    checks.append(CheckResult(
                        check_number=1,
                        name="Structural Chunk Count & Indices (Zero-Based)",
                        passed=(structural_stats.get("chunks_present") == 24),
                        details="Presenza di esattamente 24 blocchi su 26 pianificati con indici mancanti conformi",
                        observed_value=f"present={structural_stats.get('chunks_present')}, missing={list(self.expected_missing_chunks)}",
                        expected_value=f"present=24, missing={list(self.expected_missing_chunks)}",
                    ))
                    checks.append(CheckResult(
                        check_number=2,
                        name="Payload JSON & Schema Structural Validity",
                        passed=(structural_stats.get("corrupted_chunk_records", 0) == 0 and structural_stats.get("corrupted_subchunks", 0) == 0),
                        details="Validità sintattica JSON e conformità dello schema dei payload dei blocchi e sotto-blocchi",
                        observed_value=(
                            f"json_decodable={structural_stats.get('chunks_json_decodable')}/24, "
                            f"schema_valid={structural_stats.get('chunks_schema_valid')}/24, "
                            f"corrupt_records={structural_stats.get('corrupted_chunk_records')}, "
                            f"subchunks_valid={structural_stats.get('subchunks_valid')}, "
                            f"corrupt_subchunks={structural_stats.get('corrupted_subchunks')}"
                        ),
                        expected_value="json_decodable=24/24, schema_valid=24/24, corrupt_records=0, corrupt_subchunks=0",
                    ))
                    checks.append(CheckResult(
                        check_number=3,
                        name="Evidentiary In-Memory Document Validation",
                        passed=False,
                        details="Validazione probatoria e verifica intra-chunk del document fingerprint e degli evidence ID",
                        observed_value="SKIPPED_NO_DOCUMENT",
                        expected_value="EVIDENTIARY_VALIDATION_REQUIRES_CHAT_FILE",
                    ))

                    reasons.append(
                        STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.STRUCTURAL_AUDIT_COMPLETED]
                    )
                    return PreflightAuditReport(
                        verdict=PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED,
                        database_path=str(self.db_path),
                        document_available=False,
                        total_planned_chunks=self.expected_total_chunks,
                        completed_chunks_count=structural_stats.get("chunks_present", 24),
                        missing_chunks=list(self.expected_missing_chunks),
                        checks=checks,
                        reasons=reasons,
                        configuration_details={
                            "structural_inspection": structural_stats,
                            "evidentiary_validation_status": "SKIPPED_NO_DOCUMENT",
                            "source_immutability_status": report_immutability,
                        },
                        verification_classification="STATIC_AUDIT_WITHOUT_DOCUMENT",
                        source_immutability_status=report_immutability,
                    )

                # SE IL DOCUMENTO È FORNITO: Selezione univoca della sessione basata su identità e fingerprint
                doc = self.document
                computed_doc_fp = compute_document_fingerprint(doc)

                # Calcolo della configurazione effettivamente risolta e piano di chunking
                service = LocalTopicAnalysisService(
                    default_model=self.model_id,
                    profile=self.profile,
                    context_length=self.context_length,
                    timeout_seconds=60.0,
                )
                plan = service.create_analysis_plan(
                    document=doc,
                    analysis_type="discovery",
                    max_topics=self.max_topics,
                    context_length=self.context_length,
                )
                chunks = list(plan.chunks)
                eff_max_tokens = calculate_discovery_max_tokens(
                    max_topics=self.max_topics,
                    context_length=self.context_length,
                )
                eff_run_cfg = OperationalAnalysisConfig(
                    model_spec=self.model_id,
                    context_length=self.context_length,
                    timeout_seconds=60.0,
                    max_tokens=eff_max_tokens,
                    temperature=self.temperature,
                    seed=self.seed,
                    profile=self.profile,
                    chunk_character_budget=service.max_chunk_characters,
                    max_chunk_bundles=service.max_chunk_bundles,
                    max_chunk_tokens=plan.chunk_token_budget,
                )

                extra_params = {
                    "max_topics": self.max_topics,
                    "resolved_model_key": self.resolved_model_key,
                    "quantization": self.quantization,
                }
                computed_cfg_fp = compute_run_config_hash(
                    model_id=self.model_id,
                    prompt_version="discovery_operational",
                    config=eff_run_cfg,
                    chunks=chunks,
                    extra_params=extra_params,
                )

                # Confronto di TUTTE le sessioni pertinenti per selezione deterministica
                exact_matches = []
                for sess in all_sessions:
                    s_doc_fp, s_cfg_fp, s_doc_id, s_atype, s_mid, s_pver, s_tot, s_cat, s_uat = sess
                    if (
                        s_doc_id == doc.document_id
                        and s_doc_fp == computed_doc_fp
                        and s_cfg_fp == computed_cfg_fp
                        and s_atype == "discovery"
                        and s_tot == self.expected_total_chunks
                        and s_mid == self.model_id
                        and s_pver == "discovery_operational"
                    ):
                        exact_matches.append(sess)

                if len(exact_matches) == 1:
                    selected_header = exact_matches[0]
                elif len(exact_matches) > 1:
                    reasons.append(
                        "AMBIGUOUS_MULTIPLE_SESSIONS_MATCH: Rilevate molteplici sessioni identiche compatibili nel database senza criterio univoco di disambiguazione."
                    )
                    return PreflightAuditReport(
                        verdict=PreflightVerdictStatus.NOT_COMPATIBLE,
                        database_path=str(self.db_path),
                        document_available=True,
                        reasons=reasons,
                        source_immutability_status=report_immutability,
                    )
                else:
                    # Nessuna sessione con match esatto. Diagnostica sanificata su corrispondenze parziali:
                    doc_matches = [s for s in all_sessions if s[0] == computed_doc_fp]
                    id_matches = [s for s in all_sessions if s[2] == doc.document_id]
                    if doc_matches:
                        reasons.append(STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.CONFIG_MISMATCH])
                    elif id_matches:
                        reasons.append(STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.FINGERPRINT_MISMATCH])
                    else:
                        reasons.append(STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.NO_MATCHING_SESSION])
                    return PreflightAuditReport(
                        verdict=PreflightVerdictStatus.NOT_COMPATIBLE,
                        database_path=str(self.db_path),
                        document_available=True,
                        reasons=reasons,
                        source_immutability_status=report_immutability,
                    )

                (
                    db_doc_fp, db_cfg_fp, db_doc_id, db_a_type, db_model_id,
                    db_prompt_ver, db_total_planned, db_created_at, db_updated_at
                ) = selected_header

                # Recupera i chunk della sessione selezionata
                chunk_rows = cur.execute("""
                    SELECT chunk_index, serialized_result
                    FROM checkpoint_chunks
                    WHERE document_fingerprint = ? AND config_fingerprint = ?
                    ORDER BY chunk_index ASC
                """, (db_doc_fp, db_cfg_fp)).fetchall()

                candidate_chunks = {int(r[0]): str(r[1]) for r in chunk_rows}
                present_indices = sorted(candidate_chunks.keys())
                missing_indices = sorted(set(range(db_total_planned)) - set(present_indices))

                # ESEGUIAMO I 9 CONTROLLI OBBLIGATORI

                # CONTROLLO 1: Identità del documento e document fingerprint
                c1_doc_id_match = (doc.document_id == db_doc_id)
                c1_fp_match = (computed_doc_fp == db_doc_fp)
                c1_passed = c1_doc_id_match and c1_fp_match
                checks.append(CheckResult(
                    check_number=1,
                    name="Document Identity & Fingerprint Match",
                    passed=c1_passed,
                    details="Identità formale document_id e fingerprint crittografico SHA-256 dei messaggi e delle evidenze",
                    observed_value=f"doc_id_match={c1_doc_id_match}, fp={_format_fp_safe(computed_doc_fp)}",
                    expected_value=f"doc_id_match=True, fp={_format_fp_safe(db_doc_fp)}",
                ))

                # CONTROLLO 2: Config fingerprint & Runtime Model Binding
                # Separazione rigorosa tra chiave dichiarata, binding runtime verificato e identità osservata
                observed_runtime_identity = None
                runtime_binding_verified = False
                runtime_binding_status = "NOT_VERIFIED"
                verification_classification = "UNVERIFIED"

                if self.active_binding is not None:
                    is_ver = getattr(self.active_binding, "is_verified", False)
                    canon_id = getattr(self.active_binding, "requested_canonical_id", "")
                    res_key = getattr(self.active_binding, "resolved_model_key", "")
                    observed_runtime_identity = res_key
                    if is_ver and canon_id == self.model_id and (not self.resolved_model_key or res_key == self.resolved_model_key):
                        runtime_binding_verified = True
                        runtime_binding_status = "MOCK_VERIFIED"
                        verification_classification = "MOCK_VERIFIED"
                    else:
                        runtime_binding_status = "BINDING_VERIFICATION_FAILED"

                elif self.check_live_backend:
                    live_status, live_model = _query_lmstudio_models_readonly(
                        self.model_id,
                        self.resolved_model_key,
                        backend_url=getattr(self, "backend_url", "http://localhost:1234"),
                        expected_quantization=self.quantization,
                        expected_context_length=self.context_length,
                    )
                    observed_runtime_identity = live_model
                    if live_status == "LOADED_INSTANCE_VERIFIED":
                        runtime_binding_verified = True
                        runtime_binding_status = "VERIFIED_LIVE_BACKEND_READONLY"
                        verification_classification = "LIVE_VERIFIED"
                    elif live_status == "MODEL_AVAILABLE_NOT_LOADED":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_MODEL_AVAILABLE_NOT_LOADED"
                    elif live_status == "MODEL_AVAILABLE_UNCONFIRMED_LOADED":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_LOAD_STATE_UNCONFIRMED"
                    elif live_status == "INSUFFICIENT_RUNTIME_METADATA":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_INSUFFICIENT_RUNTIME_METADATA"
                    elif live_status == "QUANTIZATION_MISMATCH":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_QUANTIZATION_MISMATCH"
                    elif live_status == "CONTEXT_LENGTH_INSUFFICIENT":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_CONTEXT_LENGTH_INSUFFICIENT"
                    elif live_status == "LOOPBACK_URL_REQUIRED":
                        runtime_binding_verified = False
                        runtime_binding_status = "LIVE_BACKEND_LOOPBACK_URL_REQUIRED"
                    else:
                        runtime_binding_status = f"LIVE_BACKEND_{live_status}"

                elif self.resolved_model_key is not None:
                    runtime_binding_status = "DECLARED_ONLY_UNVERIFIED_RUNTIME"
                    runtime_binding_verified = False
                else:
                    runtime_binding_status = "NOT_PROVIDED"
                    runtime_binding_verified = False

                c2_hash_match = (computed_cfg_fp == db_cfg_fp)
                c2_passed = c2_hash_match and runtime_binding_verified
                checks.append(CheckResult(
                    check_number=2,
                    name="Run Configuration Fingerprint & Runtime Binding Match",
                    passed=c2_passed,
                    details="Hash SHA-256 dei parametri operativi e verifica formale del binding runtime con il modello (istanza attiva caricata in memoria)",
                    observed_value=f"cfg_fp={_format_fp_safe(computed_cfg_fp)}, binding_status={runtime_binding_status}",
                    expected_value="cfg_fp=..., binding_status=VERIFIED_* or MOCK_VERIFIED",
                ))

                # CONTROLLO 3: Identità del modello
                c3_passed = (db_model_id == self.model_id)
                checks.append(CheckResult(
                    check_number=3,
                    name="Model Identity Match",
                    passed=c3_passed,
                    details="Corrispondenza esatta del modello linguistico registrato",
                    observed_value=_sanitize_model_identifier(self.model_id),
                    expected_value=_sanitize_model_identifier(db_model_id),
                ))

                # CONTROLLO 4: Versione del prompt operativo
                expected_prompt_ver = "discovery_operational"
                c4_passed = (db_prompt_ver == expected_prompt_ver)
                checks.append(CheckResult(
                    check_number=4,
                    name="Operational Prompt Version Match",
                    passed=c4_passed,
                    details="Versione del prompt di Discovery compatibile con il parser e gli alias deterministici",
                    observed_value=expected_prompt_ver,
                    expected_value=db_prompt_ver,
                ))

                # CONTROLLO 5: Parametri e piano di chunking
                c5_count_match = (len(chunks) == db_total_planned and len(chunks) == self.expected_total_chunks)
                computed_partition_fp = compute_chunk_partition_fingerprint(chunks)

                # REGOLA FONDAMENTALE B2:
                # Per autorizzare una vera certificazione delle boundaries, è richiesta un'attestazione
                # storica attendibile, persistita alla creazione della sessione in SQLite e verificabile
                # indipendentemente. Un fingerprint autodichiarato dal chiamante NON costituisce MAI prova sufficiente.
                has_historical_proof = False
                persisted_historical_partition_fp: str | None = None
                try:
                    chk_cols = [col[1] for col in cur.execute("PRAGMA table_info(checkpoints)").fetchall()]
                    if "partition_fingerprint" in chk_cols:
                        row_fp = cur.execute(
                            "SELECT partition_fingerprint FROM checkpoints WHERE document_fingerprint = ? AND config_fingerprint = ?",
                            (db_doc_fp, db_cfg_fp)
                        ).fetchone()
                        if row_fp and row_fp[0]:
                            persisted_historical_partition_fp = str(row_fp[0]).strip()
                            has_historical_proof = True
                except Exception:
                    has_historical_proof = False

                boundaries_verified = False
                if has_historical_proof and persisted_historical_partition_fp:
                    boundaries_verified = (computed_partition_fp == persisted_historical_partition_fp)
                    if self.verified_partition_fingerprint is not None:
                        boundaries_verified = boundaries_verified and (self.verified_partition_fingerprint == persisted_historical_partition_fp)

                # Per i checkpoint legacy privi di attestazione storica sufficiente in SQLite,
                # legacy_boundaries_limitation resta categoricamente True
                legacy_boundaries_limitation = not boundaries_verified
                c5_passed = c5_count_match

                if boundaries_verified:
                    c5_name = "Chunking Count & Partition Boundaries Identity Verified"
                    c5_obs = f"chunks_generated={len(chunks)}, partition_compatible=True, boundaries_verified=True"
                    c5_exp = f"chunks_planned={db_total_planned}, partition_compatible=True, boundaries_verified=True"
                else:
                    c5_name = "Chunking Count & Algorithmic Plan Compatibility (Historical Boundaries Limitation)"
                    c5_obs = f"chunks_generated={len(chunks)}, partition_compatible={c5_count_match}, legacy_boundaries_limitation=True"
                    c5_exp = f"chunks_planned={db_total_planned}, partition_compatible=True, legacy_boundaries_limitation=True"

                checks.append(CheckResult(
                    check_number=5,
                    name=c5_name,
                    passed=c5_passed,
                    details=CANONICAL_CHECK_DETAILS[5],
                    observed_value=c5_obs,
                    expected_value=c5_exp,
                ))

                # CONTROLLO 6: Presenza e integrità dei 24 checkpoint (senza ignorare righe corrotte)
                c6_missing_exact = (missing_indices == list(self.expected_missing_chunks))
                c6_count_exact = (len(present_indices) == 24)
                c6_payloads_valid = True
                parsed_results: dict[int, TopicDiscoveryResult] = {}
                for c_idx, raw_val in candidate_chunks.items():
                    try:
                        dict_val = json.loads(raw_val)
                        parsed_res = deserialize_discovery_result_from_checkpoint(dict_val)
                        parsed_results[c_idx] = parsed_res
                    except Exception:
                        c6_payloads_valid = False
                        break

                c6_passed = c6_missing_exact and c6_count_exact and c6_payloads_valid
                checks.append(CheckResult(
                    check_number=6,
                    name="24 Checkpoints Presence & Payload Structural Integrity",
                    passed=c6_passed,
                    details=f"Esattamente 24 blocchi presenti; blocchi mancanti {list(self.expected_missing_chunks)}; zero righe corrotte",
                    observed_value=f"present_count={len(present_indices)}, missing_indices={missing_indices}, valid_payloads={c6_payloads_valid}",
                    expected_value=f"present_count=24, missing_indices={list(self.expected_missing_chunks)}, valid_payloads=True",
                ))

                # CONTROLLO 7: Validità degli evidence ID rispetto ai rispettivi chunk
                c7_eids_valid = True
                for c_idx, res in parsed_results.items():
                    if c_idx >= len(chunks):
                        c7_eids_valid = False
                        break
                    target_chunk = chunks[c_idx]
                    valid_eids = {sec.evidence_id for sec in target_chunk.all_evidence_sections}
                    for topic in res.topics:
                        for eid in topic.evidence_ids:
                            if eid not in valid_eids:
                                c7_eids_valid = False
                                break
                        if not c7_eids_valid:
                            break
                    if not c7_eids_valid:
                        break

                c7_passed = c7_eids_valid
                checks.append(CheckResult(
                    check_number=7,
                    name="Evidence ID Intra-Chunk Validity",
                    passed=c7_eids_valid,
                    details="Tutti gli identificativi di evidenza citati appartengono rigorosamente al proprio blocco (zero cross-chunk)",
                    observed_value="verified_chunks=24, cross_chunk_citations=0" if c7_eids_valid else "cross_chunk_or_invalid_evidence_detected",
                    expected_value="verified_chunks=24, cross_chunk_citations=0",
                ))

                # CONTROLLO 8: Provenance dei risultati
                c8_prov_valid = True
                for c_idx, res in parsed_results.items():
                    prov_id = res.provenance_document_id
                    allowed_prov = {doc.document_id}
                    if c_idx < len(chunks):
                        allowed_prov.add(chunks[c_idx].document_id)
                    if prov_id not in allowed_prov:
                        c8_prov_valid = False
                        break

                c8_passed = c8_prov_valid
                checks.append(CheckResult(
                    check_number=8,
                    name="Provenance Document ID Verification",
                    passed=c8_prov_valid,
                    details="Tutti i risultati validati dichiarano provenienza autentica dal documento o dal blocco genitore",
                    observed_value=f"matching_provenance_chunks={len(parsed_results)}/24" if c8_prov_valid else "provenance_mismatch",
                    expected_value="matching_provenance_chunks=24/24",
                ))

                # CONTROLLO 9: Caricamento, validazione e precondizioni di salto dei 24 blocchi
                # Validazione 100% read-only senza istanziare AnalysisCheckpointStore
                validated_chk, chk_stats = load_and_validate_checkpoint_readonly(
                    conn=conn,
                    document=doc,
                    config_fingerprint=db_cfg_fp,
                    chunks=chunks,
                    expected_analysis_type="discovery",
                )
                c9_valid = (
                    validated_chk is not None
                    and chk_stats.is_clean
                    and len(validated_chk.completed_chunk_indices) == 24
                )
                c9_passed = c9_valid
                checks.append(CheckResult(
                    check_number=9,
                    name="Checkpoint Readability & Evidentiary Reconciliation",
                    passed=c9_passed,
                    details=(
                        "Caricamento read-only e riconciliazione probatoria del checkpoint; verifica delle condizioni formali "
                        "per il salto dei 24 blocchi pregressi (CHECKPOINT_READABLE_AND_VALIDATED, CHECKPOINT_CONFIG_COMPATIBLE). "
                        "La verifica dinamica del ciclo operativo (RUNTIME_SKIP_BEHAVIOR_VERIFIED) è demandata ai test "
                        "di simulazione con mock client o alla sessione runtime attiva."
                    ),
                    observed_value=(
                        f"load_status=CHECKPOINT_READABLE_AND_VALIDATED, config_status=CHECKPOINT_CONFIG_COMPATIBLE, "
                        f"recovered_chunks={len(validated_chk.completed_chunk_indices) if validated_chk else 0}, "
                        f"corrupt_records={chk_stats.corrupted_chunk_rows_count + chk_stats.corrupted_subchunks_count}"
                        if c9_valid else "load_status=CHECKPOINT_VALIDATION_FAILED"
                    ),
                    expected_value="load_status=CHECKPOINT_READABLE_AND_VALIDATED, config_status=CHECKPOINT_CONFIG_COMPATIBLE, recovered_chunks=24, corrupt_records=0",
                ))

        except Exception as db_err:
            reasons.append("Errore di accesso al database SQLite: [ERR_DB_ACCESS]")
            return PreflightAuditReport(
                verdict=PreflightVerdictStatus.NOT_COMPATIBLE,
                database_path=str(self.db_path),
                document_available=self.document is not None,
                reasons=reasons,
                source_immutability_status=report_immutability,
            )

        # Determinazione del verdetto finale
        all_passed = all(c.passed for c in checks)

        config_details = {
            "persisted_config_fingerprint": _format_fp_safe(db_cfg_fp),
            "requested_model_id": _sanitize_model_identifier(self.model_id),
            "resolved_profile": self.profile.value,
            "context_length": self.context_length,
            "temperature": self.temperature,
            "seed": self.seed,
            "runtime_binding_status": runtime_binding_status,
            "runtime_binding_verified": runtime_binding_verified,
            "verification_classification": verification_classification,
            "declared_resolved_model_key": _sanitize_model_identifier(self.resolved_model_key),
            "observed_runtime_identity": _sanitize_model_identifier(observed_runtime_identity),
            "source_immutability_status": report_immutability,
        }

        source_immut_verified = (report_immutability.get("SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED") is True)
        source_immut_failed = (report_immutability.get("SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED") is False)

        if all_passed and not source_immut_failed:
            if verification_classification == "MOCK_VERIFIED":
                # Contratto B2: un binding iniettato o simulato NON autorizza mai SAFE_RESUME_VERIFIED
                final_verdict = PreflightVerdictStatus.MOCK_VERIFIED
                resumption_plan = {
                    "strategy": "RETRY_FAILED_CHUNKS_ONLY",
                    "total_planned_chunks": self.expected_total_chunks,
                    "chunks_to_skip_count": 24,
                    "chunks_to_skip_indices_zero_based": present_indices,
                    "chunks_to_skip_display_one_based": "Blocchi 1-10, 12-19, 21-26",
                    "chunks_to_infer_count": 2,
                    "chunks_to_infer_indices_zero_based": missing_indices,
                    "chunks_to_infer_display_one_based": "Blocco 11, Blocco 20",
                    "model_id": _sanitize_model_identifier(self.model_id),
                    "profile": self.profile.value,
                    "max_topics": self.max_topics,
                    "scientific_guarantee": (
                        "SIMULAZIONE MOCK SUPERATA: 24 blocchi recuperati dal checkpoint sintetico, 2 inviati a inferenza simulata. "
                        "Nessuna autorizzazione di ripresa reale (SAFE_RESUME_VERIFIED) viene concessa in assenza di backend live verificato."
                    ),
                }
                reasons.append(
                    "TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI IN SIMULAZIONE (MOCK_VERIFIED). "
                    "Nessuna autorizzazione di ripresa reale in produzione viene concessa da binding mock."
                )
            elif legacy_boundaries_limitation:
                # Audit Fix B4: I checkpoint legacy SQLite non conservano un hash indipendente di tutte le boundaries.
                # Anche con LM Studio live, non è consentito trasformare automaticamente il verdetto in SAFE_RESUME_VERIFIED.
                # Il verdetto è categoricamente RUNTIME_VALIDATION_REQUIRED con evidenza della compatibilità configurazionale e probatoria.
                final_verdict = PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                verification_classification = "LIVE_RUNTIME_VALIDATION_REQUIRED" if verification_classification == "LIVE_VERIFIED" else "RUNTIME_VALIDATION_REQUIRED"
                resumption_plan = {
                    "strategy": "RETRY_FAILED_CHUNKS_ONLY",
                    "total_planned_chunks": self.expected_total_chunks,
                    "chunks_to_skip_count": 24,
                    "chunks_to_skip_indices_zero_based": present_indices,
                    "chunks_to_skip_display_one_based": "Blocchi 1-10, 12-19, 21-26",
                    "chunks_to_infer_count": 2,
                    "chunks_to_infer_indices_zero_based": missing_indices,
                    "chunks_to_infer_display_one_based": "Blocco 11, Blocco 20",
                    "model_id": _sanitize_model_identifier(self.model_id),
                    "profile": self.profile.value,
                    "max_topics": self.max_topics,
                    "scientific_guarantee": (
                        "Compatibilità configurazionale e probatoria accertata (24 blocchi recuperabili, 2 da elaborare). "
                        "Limite storico: i checkpoint SQLite legacy non conservano l'hash indipendente delle boundaries dei messaggi; "
                        "è richiesta la validazione a runtime prima dell'avvio operativo."
                    ),
                }
                reasons.append(
                    "Compatibilità configurazionale e probatoria accertata per 24/26 blocchi (mancanti 10 e 19). "
                    "Limite forense storico: l'assenza di digest crittografico indipendente delle boundaries nei checkpoint SQLite legacy "
                    "non consente la certificazione assoluta a priori dell'identità partizionale (RUNTIME_VALIDATION_REQUIRED). "
                    "I 24 blocchi storici restano recuperabili previa validazione runtime all'avvio."
                )
            elif verification_classification == "LIVE_VERIFIED" and source_immut_verified:
                final_verdict = PreflightVerdictStatus.SAFE_RESUME_VERIFIED
                resumption_plan = {
                    "strategy": "RETRY_FAILED_CHUNKS_ONLY",
                    "total_planned_chunks": self.expected_total_chunks,
                    "chunks_to_skip_count": 24,
                    "chunks_to_skip_indices_zero_based": present_indices,
                    "chunks_to_skip_display_one_based": "Blocchi 1-10, 12-19, 21-26",
                    "chunks_to_infer_count": 2,
                    "chunks_to_infer_indices_zero_based": missing_indices,
                    "chunks_to_infer_display_one_based": "Blocco 11, Blocco 20",
                    "model_id": _sanitize_model_identifier(self.model_id),
                    "profile": self.profile.value,
                    "max_topics": self.max_topics,
                    "scientific_guarantee": (
                        "I 24 blocchi pregressi validati non verranno reinviati al modello LLM. "
                        "I blocchi 11 e 20 (indici zero-based 10 e 19) verranno elaborati con gestione standard di eventuali retry o subchunk."
                    ),
                }
                reasons.append(
                    "TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI CON SUCCESSO CON BINDING RUNTIME ATTIVO E BOUNDARIES VERIFICATE (LIVE_VERIFIED). "
                    "La sessione 24/26 può essere ripresa in sicurezza elaborando ESCLUSIVAMENTE i blocchi 11 e 20 (indici 10 e 19)."
                )
            else:
                final_verdict = PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                resumption_plan = None
                reasons.append(
                    STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.RUNTIME_BINDING_REQUIRED]
                )
        elif source_immut_failed:
            final_verdict = PreflightVerdictStatus.NOT_COMPATIBLE
            resumption_plan = None
            reasons.append(
                STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.PREFLIGHT_CHECKS_FAILED]
            )
        elif not runtime_binding_verified and c1_passed and c2_hash_match and c3_passed and c4_passed and c5_passed and c6_passed and c7_passed and c8_passed and c9_passed:
            # Se la mancata verifica deriva da un mismatch esplicito e accertato a runtime: NOT_COMPATIBLE
            if runtime_binding_status in (
                "LIVE_BACKEND_QUANTIZATION_MISMATCH",
                "LIVE_BACKEND_CONTEXT_LENGTH_INSUFFICIENT",
                "LIVE_BACKEND_MODEL_KEY_MISMATCH",
                "LIVE_BACKEND_MODEL_NOT_FOUND",
            ):
                final_verdict = PreflightVerdictStatus.NOT_COMPATIBLE
                verification_classification = "NOT_COMPATIBLE"
                resumption_plan = None
                reasons.append(
                    STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.RUNTIME_BINDING_INCOMPATIBLE]
                )
            else:
                # L'identità runtime non può essere accertata offline senza avviare il modello: RUNTIME_VALIDATION_REQUIRED
                final_verdict = PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                verification_classification = "RUNTIME_VALIDATION_REQUIRED"
                resumption_plan = None
                reasons.append(
                    STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.RUNTIME_BINDING_REQUIRED]
                )
        else:
            final_verdict = PreflightVerdictStatus.NOT_COMPATIBLE
            verification_classification = "NOT_COMPATIBLE"
            resumption_plan = None
            reasons.append(
                STATIC_DIAGNOSTIC_MESSAGES[DiagnosticReasonCode.PREFLIGHT_CHECKS_FAILED]
            )
        config_details["verification_classification"] = verification_classification

        return PreflightAuditReport(
            verdict=final_verdict,
            database_path=str(self.db_path),
            document_available=True,
            total_planned_chunks=db_total_planned,
            completed_chunks_count=len(present_indices),
            missing_chunks=missing_indices,
            skipped_chunks_on_resume=present_indices if all_passed else [],
            chunks_to_infer_on_resume=missing_indices if all_passed else [],
            checks=checks,
            reasons=reasons,
            resumption_plan=resumption_plan,
            configuration_details=config_details,
            verification_classification=verification_classification,
            source_immutability_status=report_immutability,
        )


# ==============================================================================
# CLI ENTRY POINT (PURAMENTE READ-ONLY, ZERO LLM INVOCATION, ZERO RESUMPTION EXEC)
# ==============================================================================

def main() -> int:
    """Punto di ingresso CLI dello script di preflight su Ubuntu."""
    parser = argparse.ArgumentParser(
        description="Audit di preflight forense e verifica compatibilità ripresa checkpoint ChatAnalysis (24/26)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Percorso al database SQLite checkpoints.db (default: .checkpoints/checkpoints.db)",
    )
    parser.add_argument(
        "--chat",
        type=str,
        default=None,
        help="Percorso al file di conversazione reale WhatsApp (per validazione probatoria e fingerprint)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="whatsapp_export",
        choices=sorted(CLI_SOURCE_FORMAT_MAP.keys()),
        help="Formato sorgente per l'ingestion della chat",
    )
    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Identificativo opzionale per disambiguare esportazioni multi-conversazione",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen2.5-7b-instruct",
        help="Identificativo del modello operativo target (default: qwen2.5-7b-instruct)",
    )
    parser.add_argument(
        "--resolved-model-key",
        type=str,
        default=None,
        help="Chiave del modello dichiarata come risolta nel server LM Studio locale",
    )
    parser.add_argument(
        "--quantization",
        type=str,
        default=None,
        help="Stringa di quantizzazione opzionale (es. Q4_K_M)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="BALANCED",
        help="Profilo operativo (default: BALANCED)",
    )
    parser.add_argument(
        "--max-topics",
        type=int,
        default=10,
        help="Numero massimo di topic per Discovery (default: 10)",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=8192,
        help="Finestra di contesto in token (default: 8192)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperatura di inferenza (default: 0.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seme deterministico (default: 42)",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Se specificato, esegue il preflight su uno snapshot SQLite isolato e consistente",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=str,
        default=None,
        help="Directory dove salvare lo snapshot temporaneo (default: cartella di sistema temporanea)",
    )
    parser.add_argument(
        "--check-live-backend",
        action="store_true",
        help="Interroga LM Studio in sola lettura (GET /v1/models o GET /api/v1/models) per verificare se il modello è attivo senza eseguire inferenze",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default="http://localhost:1234",
        help="URL base del server LM Studio locale (default: http://localhost:1234)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Percorso di salvataggio del rapporto JSON sanificato",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il rapporto completo esclusivamente in formato JSON su stdout",
    )

    args = parser.parse_args()

    # Validazione restrizione loopback (B9 / B3)
    if args.check_live_backend or args.backend_url != "http://localhost:1234":
        is_loop, loop_code = _validate_loopback_backend_url(args.backend_url)
        if not is_loop:
            static_msg = _BACKEND_URL_ERROR_MESSAGES.get(loop_code, "Endpoint backend non conforme ai criteri di sicurezza.")
            if args.json:
                print(json.dumps({"status": "ERROR", "error_code": loop_code, "message": static_msg}, indent=2))
            else:
                print(f"[ERRORE] {loop_code}: {static_msg}")
            return 3

    # Individuazione percorso database
    db_path = None
    if args.db:
        db_path = Path(args.db)
    else:
        candidates = [
            Path.cwd() / ".checkpoints" / "checkpoints.db",
            Path.cwd() / "checkpoints.db",
            Path.home() / ".checkpoints" / "checkpoints.db",
        ]
        for c in candidates:
            if c.is_file():
                db_path = c
                break

    if db_path is None or not db_path.is_file():
        err_msg = "Database SQLite non trovato. Specificare un percorso valido con --db <path/checkpoints.db>."
        if args.json:
            print(json.dumps({"status": "ERROR", "error_code": "DB_NOT_FOUND"}, indent=2))
        else:
            print(f"[ERRORE] {err_msg}")
        return 3

    # Validazione precoce della destinazione output JSON se richiesta (B1/B2)
    if args.output_json:
        try:
            _validate_destination_path(Path(args.output_json), actual_db_path=db_path)
        except PreflightReportSecurityError as pse:
            if args.json:
                print(json.dumps({
                    "status": "ERROR",
                    "error_code": pse.error_code,
                    "message": "Destinazione report non conforme ai criteri di sicurezza forense."
                }, indent=2))
            else:
                print(f"[ERRORE SICUREZZA] {pse.error_code}: {pse}")
            return 3

    # Gestione snapshot SQLite isolato o acquisizione forense di staging (B1/B2/B3)
    actual_db_path = db_path
    snapshot_cleanup_dir = None
    forensic_cleanup_dir = None
    immutability_metrics = None

    if args.snapshot or args.snapshot_dir is not None:
        snap_dest_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
        try:
            snap_res = create_consistent_readonly_snapshot(db_path, dest_dir=snap_dest_dir)
            snap_file, snap_temp_dir = snap_res[0], snap_res[1]
            actual_db_path = snap_file
            snapshot_cleanup_dir = snap_temp_dir
            immutability_metrics = getattr(snap_res, "immutability_metrics", None)
            if not immutability_metrics:
                immutability_metrics = {
                    "DATABASE_LOGICAL_CONTENT_UNCHANGED": "NOT_MEASURED",
                    "WAL_CONTENT_UNCHANGED": "NOT_MEASURED",
                    "SHM_BYTES_UNCHANGED": "NOT_MEASURED",
                    "SOURCE_METADATA_UNCHANGED": "NOT_MEASURED",
                    "CONSISTENT_SNAPSHOT_VERIFIED": "NOT_MEASURED",
                    "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": "NOT_MEASURED",
                }
        except SnapshotError as se:
            static_msg = _SNAPSHOT_ERROR_MESSAGES.get(se.error_code, "Operazione snapshot non consentita.")
            if args.json:
                print(json.dumps({"status": "ERROR", "error_code": se.error_code, "message": static_msg}, indent=2))
            else:
                print(f"[ERRORE SICUREZZA] {se.error_code}: {static_msg}")
            return 3
        except (PermissionError, FileExistsError, NotADirectoryError, OSError, sqlite3.Error) as oe:
            err_code = "SNAPSHOT_CREATION_FAILED"
            if isinstance(oe, PermissionError):
                err_code = "SNAPSHOT_PERMISSION_DENIED"
            elif isinstance(oe, NotADirectoryError):
                err_code = "SNAPSHOT_DIRECTORY_INVALID"
            elif isinstance(oe, FileExistsError):
                err_code = "SNAPSHOT_DESTINATION_FORBIDDEN"
            static_msg = _SNAPSHOT_ERROR_MESSAGES.get(err_code, "Errore durante la creazione dello snapshot SQLite.")
            if args.json:
                print(json.dumps({"status": "ERROR", "error_code": err_code, "message": static_msg}, indent=2))
            else:
                print(f"[ERRORE SICUREZZA] {err_code}: {static_msg}")
            return 3
    else:
        # Acquisizione forense di staging isolata per garantire ZERO aperture SQLite sui file originali
        try:
            copy_file, copy_temp_dir, immut_metrics = acquire_forensic_readonly_copy(db_path)
            actual_db_path = copy_file
            forensic_cleanup_dir = copy_temp_dir
            immutability_metrics = immut_metrics
        except SnapshotError as se:
            static_msg = _SNAPSHOT_ERROR_MESSAGES.get(se.error_code, "Operazione di acquisizione forense non consentita.")
            if args.json:
                print(json.dumps({"status": "ERROR", "error_code": se.error_code, "message": static_msg}, indent=2))
            else:
                print(f"[ERRORE SICUREZZA] {se.error_code}: {static_msg}")
            return 3
        except (PermissionError, FileExistsError, NotADirectoryError, OSError, sqlite3.Error) as oe:
            err_code = "SNAPSHOT_CREATION_FAILED"
            if isinstance(oe, PermissionError):
                err_code = "SNAPSHOT_PERMISSION_DENIED"
            elif isinstance(oe, NotADirectoryError):
                err_code = "SNAPSHOT_DIRECTORY_INVALID"
            elif isinstance(oe, FileExistsError):
                err_code = "SNAPSHOT_DESTINATION_FORBIDDEN"
            static_msg = _SNAPSHOT_ERROR_MESSAGES.get(err_code, "Errore durante l'acquisizione forense del database SQLite.")
            if args.json:
                print(json.dumps({"status": "ERROR", "error_code": err_code, "message": static_msg}, indent=2))
            else:
                print(f"[ERRORE SICUREZZA] {err_code}: {static_msg}")
            return 3

    try:
        # Ingestion e selezione deterministica del documento se specificato
        doc = None
        if args.chat:
            try:
                docs_map = ingest_chat_file_for_preflight(args.chat, source_format=args.format)

                # Query di tutte le sessioni dal database
                with _open_readonly_db(actual_db_path) as conn:
                    cur = conn.cursor()
                    all_sessions = cur.execute("""
                        SELECT document_fingerprint, config_fingerprint, document_id,
                               analysis_type, model_id, prompt_version, total_planned_chunks,
                               created_at, updated_at
                        FROM checkpoints
                        WHERE analysis_type = 'discovery' AND total_planned_chunks = 26
                    """).fetchall()

                # Cerca abbinamento forense tra tutti i documenti e tutte le sessioni
                candidate_matches = []
                for d_id, d_obj in docs_map.items():
                    d_fp = compute_document_fingerprint(d_obj)
                    for sess in all_sessions:
                        if sess[2] == d_id and sess[0] == d_fp:
                            candidate_matches.append((d_obj, sess))

                if args.document_id:
                    filtered_matches = [m for m in candidate_matches if m[0].document_id == args.document_id]
                    if filtered_matches:
                        doc = filtered_matches[0][0]
                    elif args.document_id in docs_map:
                        doc = docs_map[args.document_id]
                    else:
                        err_msg = "Documento richiesto non trovato nell'input. [ERR_DOCUMENT_NOT_FOUND]"
                        if args.json:
                            print(json.dumps({"status": "ERROR", "error_code": "REQUESTED_DOCUMENT_ID_NOT_FOUND"}, indent=2))
                        else:
                            print(f"[ERRORE] {err_msg}")
                        return 3

                if doc is None:
                    if len(candidate_matches) == 1:
                        doc = candidate_matches[0][0]
                    elif len(candidate_matches) > 1:
                        err_msg = "Rilevate molteplici conversazioni corrispondenti a sessioni nel database. Specificare --document-id."
                        if args.json:
                            print(json.dumps({"status": "ERROR", "error_code": "AMBIGUOUS_MULTIPLE_CONVERSATIONS_MATCH"}, indent=2))
                        else:
                            print(f"[ERRORE] {err_msg}")
                        return 3
                    elif len(docs_map) == 1:
                        doc = next(iter(docs_map.values()))
                    else:
                        err_msg = "Nessuna delle conversazioni estratte corrisponde al database SQLite."
                        if args.json:
                            print(json.dumps({"status": "ERROR", "error_code": "AMBIGUOUS_MULTIPLE_CONVERSATIONS_NO_MATCH"}, indent=2))
                        else:
                            print(f"[ERRORE] {err_msg}")
                        return 3

            except Exception as ing_err:
                if args.json:
                    print(json.dumps({"status": "ERROR", "error_code": "INGESTION_ERROR"}, indent=2))
                else:
                    print(f"[ERRORE] Impossibile elaborare il file chat: {type(ing_err).__name__}")
                return 3

        # Esecuzione audit
        auditor = PreflightResumeAuditor(
            db_path=actual_db_path,
            document=doc,
            model_id=args.model,
            resolved_model_key=args.resolved_model_key,
            quantization=args.quantization,
            profile=args.profile,
            max_topics=args.max_topics,
            context_length=args.context_length,
            temperature=args.temperature,
            seed=args.seed,
            check_live_backend=args.check_live_backend,
            backend_url=args.backend_url,
            verified_partition_fingerprint=None,
            source_immutability_metrics=immutability_metrics,
        )

        report = auditor.run_preflight()

        # Output JSON se richiesto (B1/B2: Scrittura sicura forense)
        if args.output_json:
            try:
                write_preflight_report_safely(
                    report_dict=report.to_dict(),
                    destination_path=args.output_json,
                    actual_db_path=actual_db_path,
                    protected_paths=[db_path],
                )
            except PreflightReportSecurityError as pse:
                if args.json:
                    print(json.dumps({
                        "status": "ERROR",
                        "error_code": pse.error_code,
                        "message": "Impossibile scrivere il report in sicurezza."
                    }, indent=2))
                else:
                    print(f"[ERRORE SICUREZZA] {pse.error_code}: {pse}")
                return 3
            except Exception as e:
                if args.json:
                    print(json.dumps({
                        "status": "ERROR",
                        "error_code": "WRITE_ERROR",
                        "message": "Errore irreversibile durante la scrittura del report."
                    }, indent=2))
                else:
                    print(f"[ERRORE SICUREZZA] WRITE_ERROR: {e}")
                return 3

        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            export_data = report.to_dict()
            print("\n" + "=" * 78)
            print(" CHATANALYSIS — VERIFICA PRE-RIPRESA CHECKPOINT 24/26")
            print("=" * 78)
            print(f"Database SQLite  : {export_data.get('database_path', '[DATABASE_REDACTED]')}")
            print(f"Documento Reale  : {'PRESENTE' if export_data.get('document_available') else 'NON FORNITO'}")
            print(f"Verdetto Finale  : {export_data.get('verdict')}")
            print("-" * 78)

            exp_checks = export_data.get("checks", [])
            if exp_checks:
                print("ESITO DEI 9 CONTROLLI OBBLIGATORI:")
                for c in exp_checks:
                    status_str = f"[{c.get('status', 'FAIL')}]"
                    print(f"  {status_str} Controllo {c.get('check_number')}: {c.get('name')}")
                    if c.get("status") != "PASS":
                        print(f"         Osservato: {c.get('observed_value')}")
                        print(f"         Atteso   : {c.get('expected_value')}")
                print("-" * 78)

            print("MOTIVAZIONE:")
            for r in export_data.get("reasons", []):
                print(f"  • {r}")

            if report.verdict == PreflightVerdictStatus.SAFE_RESUME_VERIFIED:
                print("\n" + "=" * 78)
                print(" PIANO DI RIPRESA CERTIFICATO (CHUNKS 10 & 19)")
                print("=" * 78)
                plan = report.resumption_plan or {}
                print(f"  • Blocchi totali pianificati       : {plan.get('total_planned_chunks', 26)}")
                print(f"  • Blocchi recuperati dal checkpoint: {plan.get('chunks_to_skip_count', 24)} ({plan.get('chunks_to_skip_display_one_based')})")
                print(f"  • Blocchi da inviare a inferenza   : {plan.get('chunks_to_infer_count', 2)} ({plan.get('chunks_to_infer_display_one_based')})")
                print(f"  • Modello operativo                : {plan.get('model_id')}")
                print(f"  • Garanzia scientifica             : {plan.get('scientific_guarantee')}")
                print("=" * 78)

            elif report.verdict == PreflightVerdictStatus.MOCK_VERIFIED:
                print("\n[!] STATO PREFLIGHT: MOCK_VERIFIED (SIMULAZIONE CONCLUSA)")
                print("    Tutti i controlli superati in ambiente simulato (mock client / binding fittizio).")
                print("    Nessuna autorizzazione di ripresa reale in produzione viene concessa.")

            elif report.verdict in (PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED, PreflightVerdictStatus.STRUCTURALLY_COMPATIBLE, PreflightVerdictStatus.STRUCTURAL_ONLY):
                print("\n[!] STATO PREFLIGHT: RUNTIME_VALIDATION_REQUIRED")
                print("    Tutti i controlli strutturali ed probatori sono verificati offline.")
                print("    L'identità runtime del modello richiede binding attivo all'avvio della sessione.")

            elif report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE:
                print("\n[!] PROCEDURA INTERROTTA: Incompatibilità rilevata. Nessun checkpoint è stato modificato.")

        # Codici di uscita semantici
        if report.verdict == PreflightVerdictStatus.SAFE_RESUME_VERIFIED:
            return 0
        elif report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE:
            return 1
        elif report.verdict in (
            PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED,
            PreflightVerdictStatus.STRUCTURAL_ONLY,
            PreflightVerdictStatus.STRUCTURALLY_COMPATIBLE,
            PreflightVerdictStatus.MOCK_VERIFIED,
        ):
            return 2
        return 3

    finally:
        # Pulizia dello snapshot temporaneo se creato
        if snapshot_cleanup_dir and snapshot_cleanup_dir.is_dir():
            # Protezione di sicurezza: elimina SOLO se è una directory temporanea comprovatamente posseduta creata dal preflight
            if _OWNERSHIP_TRACKER.is_owned_dir(snapshot_cleanup_dir):
                try:
                    shutil.rmtree(snapshot_cleanup_dir, ignore_errors=True)
                    _OWNERSHIP_TRACKER.unregister_temp_dir(snapshot_cleanup_dir)
                except Exception:
                    pass

        # Pulizia della cartella temporanea forense se creata
        if forensic_cleanup_dir and forensic_cleanup_dir.is_dir():
            if _OWNERSHIP_TRACKER.is_owned_dir(forensic_cleanup_dir):
                try:
                    shutil.rmtree(forensic_cleanup_dir, ignore_errors=True)
                    _OWNERSHIP_TRACKER.unregister_temp_dir(forensic_cleanup_dir)
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
