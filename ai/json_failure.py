"""
ai/json_failure.py
------------------
Telemetria SOLO NUMERICA / ENUM CHIUSI di un fallimento di parsing JSON o di un tentativo LLM.

Privacy (vincolante): nessuna funzione di questo modulo restituisce, conserva o logga testo della risposta del
modello, finestre attorno alla posizione dell'errore, hash di contenuto o nomi di file. Dal testo si ricavano
esclusivamente lunghezze e posizioni intere; dall'eccezione `json.JSONDecodeError` si usa solo la CLASSE del messaggio
(costanti della libreria standard), mai il messaggio completo.

Il modulo non modifica alcuna decisione di accettazione: serve solo a distinguere, a posteriori e senza contenuti, un
troncamento da un JSON malformato, da una risposta vuota, da testo extra, ecc.
"""
from __future__ import annotations

import json
from typing import Any

# --- Enum chiusi -------------------------------------------------------------------------------------------------

JSON_FAILURE_KINDS = (
    "EMPTY_RESPONSE",
    "UNTERMINATED_STRING",
    "EXPECTING_VALUE",
    "EXPECTING_DELIMITER",
    "EXTRA_DATA",
    "INVALID_ESCAPE",
    "INVALID_CONTROL_CHAR",
    "NOT_AN_OBJECT",
    "OTHER_SYNTAX",
)

FINISH_REASON_KINDS = ("STOP", "LENGTH", "OTHER", "MISSING")

ATTEMPT_OUTCOMES = (
    "OK",
    "EMPTY_TOPICS",
    "INVALID_CITATION",
    "JSON_SYNTAX",
    "TRUNCATED_LENGTH",
    "DEGENERATE",
    "OTHER_STRUCTURED",
    "BACKEND_ERROR",
    "OTHER",
    "UNKNOWN",
)

# Causa di un rifiuto di VALIDAZIONE del payload Topic Discovery (JSON sintatticamente valido ma non conforme).
# Sottodivide l'esito OTHER_STRUCTURED; nessun testo, solo enum chiuso.
VALIDATION_KINDS = (
    "TOP_KEYS_MISMATCH",
    "TOPICS_NOT_LIST",
    "TOPIC_COUNT_EXCEEDS_LIMIT",
    "TOPIC_NOT_OBJECT",
    "TOPIC_KEYS_MISMATCH",
    "LABEL_INVALID",
    "DESCRIPTION_INVALID",
    "EVIDENCE_IDS_NOT_LIST",
    "EVIDENCE_IDS_DUPLICATED",
    "EVIDENCE_ID_NOT_STRING",
    "EVIDENCE_IDS_EMPTY",
    "OTHER_VALIDATION",
)

TELEMETRY_PROVENANCE = ("MEASURED", "NOT_AVAILABLE")

# Prefissi dei messaggi di json.JSONDecodeError (costanti CPython) → enum chiuso.
_MSG_PREFIX_TO_KIND = (
    ("Unterminated string", "UNTERMINATED_STRING"),
    ("Expecting value", "EXPECTING_VALUE"),
    ("Expecting ',' delimiter", "EXPECTING_DELIMITER"),
    ("Expecting ':' delimiter", "EXPECTING_DELIMITER"),
    ("Expecting property name", "EXPECTING_DELIMITER"),
    ("Extra data", "EXTRA_DATA"),
    ("Invalid \\escape", "INVALID_ESCAPE"),
    ("Invalid \\u", "INVALID_ESCAPE"),
    ("Invalid control character", "INVALID_CONTROL_CHAR"),
)


def finish_reason_kind(value: Any) -> str:
    """Enum chiuso del finish_reason ricevuto dal server (MISSING se assente)."""
    if value is None:
        return "MISSING"
    s = str(value).strip().lower()
    if s == "stop":
        return "STOP"
    if s == "length":
        return "LENGTH"
    return "OTHER"


def finish_reason_from_kind(kind: Any) -> str | None:
    """Valore da riportare nel campo `finish_reason` storico (stringhe già in allowlist dell'export)."""
    return {"STOP": "stop", "LENGTH": "length", "OTHER": "unknown"}.get(str(kind))


def describe_json_failure(text: Any, err: BaseException | None = None, *, not_an_object: bool = False) -> dict[str, Any]:
    """
    Descrive un fallimento di parsing JSON con SOLI interi/enum chiusi.

    `text` è il testo effettivamente passato a json.loads (serve solo per misurarne la lunghezza); `err` è la
    JSONDecodeError (ne usiamo la classe di messaggio e pos/lineno/colno, non il testo).
    """
    s = text if isinstance(text, str) else ""
    n = len(s)
    out: dict[str, Any] = {
        "json_failure_kind": "OTHER_SYNTAX",
        "json_error_offset": None,
        "json_error_line": None,
        "json_error_col": None,
        "json_error_at_end": None,
        "response_chars": n,
    }
    if not s.strip():
        out["json_failure_kind"] = "EMPTY_RESPONSE"
        return out
    if not_an_object:
        out["json_failure_kind"] = "NOT_AN_OBJECT"
        return out
    if isinstance(err, json.JSONDecodeError):
        msg = str(err.msg)
        for prefix, kind in _MSG_PREFIX_TO_KIND:
            if msg.startswith(prefix):
                out["json_failure_kind"] = kind
                break
        pos = int(err.pos)
        out["json_error_offset"] = pos
        out["json_error_line"] = int(err.lineno)
        out["json_error_col"] = int(err.colno)
        out["json_error_at_end"] = bool(pos >= len(s.rstrip()))
    return out
