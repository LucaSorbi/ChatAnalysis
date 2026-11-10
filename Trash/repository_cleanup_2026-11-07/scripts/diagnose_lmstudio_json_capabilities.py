#!/usr/bin/env python3
"""
scripts/diagnose_lmstudio_json_capabilities.py
-----------------------------------------------
Diagnostica locale rigorosa, sicura e deterministica delle capacità di Structured Output
di LM Studio su 127.0.0.1:1234.
Utilizza ESCLUSIVAMENTE dati e prompt sintetici, senza PII, senza chat reali.

Probes eseguiti (Fase B):
B1: GET /v1/models (identificativo e stato del modello).
B2: POST /v1/chat/completions senza response_format (inferenza base).
B3: POST con schema JSON MINIMALE e json_schema/strict=True.
B4: POST con TOPIC_DETECTION_SCHEMA esatto del repository ed evidenze sintetiche.
B5: POST con TOPIC_DISCOVERY_SCHEMA esatto ed evidenze sintetiche.
B6: Prova con response_format={"type": "json_object"} per schemi rifiutati (se necessario).
B7: Variazioni controllate su TOPIC_DETECTION_SCHEMA (es. con enum vs senza enum, strict=True vs False)
    per isolare esattamente la proprietà o campo che causa il rifiuto da parte di LM Studio.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# Aggiungi root repository a sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.structured import (
    TOPIC_DETECTION_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA,
    parse_and_validate_json,
    validate_topic_detection_payload,
    validate_topic_discovery_payload,
)
from multimodal.evidence import TextEvidenceSection

BASE_URL = "http://127.0.0.1:1234"


def compute_schema_hash(schema: dict[str, Any]) -> str:
    s = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def send_http_request(
    endpoint: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | None, str]:
    """Invia richiesta HTTP e restituisce (http_code, parsed_json_or_none, raw_body_text)."""
    url = f"{BASE_URL}{endpoint}"
    req_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=req_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            body_bytes = resp.read()
            body_text = body_bytes.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body_text)
            except Exception:
                parsed = None
            return code, parsed, body_text
    except urllib.error.HTTPError as e:
        code = e.code
        err_bytes = e.read()
        err_text = err_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_text)
        except Exception:
            parsed = None
        return code, parsed, err_text
    except Exception as exc:
        return 0, None, f"Network/Connection error: {exc}"


def run_diagnostics() -> dict[str, Any]:
    print("=" * 80)
    print("CHATANALYSIS — DIAGNOSTICA STRONGLY-ISOLATA CAPACITÀ LM STUDIO")
    print("=" * 80)
    print(f"Endpoint: {BASE_URL}")

    results: dict[str, Any] = {"probes": []}

    # B1: GET /v1/models
    print("\n[*] Probe B1: GET /v1/models...")
    code, data, text = send_http_request("/v1/models")
    model_id = None
    if code == 200 and data and "data" in data and len(data["data"]) > 0:
        model_id = data["data"][0].get("id")
        print(f"  [OK] HTTP {code}. Modello disponibile/selezionato: '{model_id}'")
        results["probes"].append({
            "probe_id": "B1",
            "name": "GET /v1/models",
            "status": "PASS",
            "http_code": code,
            "model_id": model_id,
            "error": None,
        })
    else:
        print(f"  [FAIL] HTTP {code}. Impossibile recuperare modello: {text[:200]}")
        results["probes"].append({
            "probe_id": "B1",
            "name": "GET /v1/models",
            "status": "FAIL",
            "http_code": code,
            "model_id": None,
            "error": text[:200],
        })
        return results

    # Prompt sintetico per test
    synthetic_messages = [
        {"role": "system", "content": "Sei un assistente per analisi testuale."},
        {"role": "user", "content": "Evidenze:\n[E001] Buongiorno a tutti.\n[E002] Procediamo con la riunione."},
    ]

    # B2: POST /v1/chat/completions senza response_format
    print("\n[*] Probe B2: POST /v1/chat/completions (SENZA response_format)...")
    payload_b2 = {
        "model": model_id,
        "messages": synthetic_messages,
        "temperature": 0.0,
        "max_tokens": 100,
    }
    t0 = time.perf_counter()
    code, data, text = send_http_request("/v1/chat/completions", method="POST", data=payload_b2, timeout=60.0)
    dur = round(time.perf_counter() - t0, 3)
    if code == 200 and data and "choices" in data:
        content = data["choices"][0]["message"].get("content", "")
        print(f"  [OK] HTTP {code} in {dur}s. Risposta ricevuta ({len(content)} chars).")
        results["probes"].append({
            "probe_id": "B2",
            "name": "Chat completion standard",
            "status": "PASS",
            "http_code": code,
            "duration": dur,
            "error": None,
        })
    else:
        print(f"  [FAIL] HTTP {code} in {dur}s. Errore: {text[:200]}")
        results["probes"].append({
            "probe_id": "B2",
            "name": "Chat completion standard",
            "status": "FAIL",
            "http_code": code,
            "duration": dur,
            "error": text[:200],
        })

    # B3: Schema JSON minimale con json_schema / strict=True
    minimal_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["status", "count"],
        "additionalProperties": False,
    }
    print("\n[*] Probe B3: POST /v1/chat/completions con schema JSON MINIMALE (strict=True)...")
    payload_b3 = {
        "model": model_id,
        "messages": synthetic_messages,
        "temperature": 0.0,
        "max_tokens": 100,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "minimal_output",
                "strict": True,
                "schema": minimal_schema,
            },
        },
    }
    t0 = time.perf_counter()
    code, data, text = send_http_request("/v1/chat/completions", method="POST", data=payload_b3, timeout=60.0)
    dur = round(time.perf_counter() - t0, 3)
    if code == 200:
        print(f"  [OK] HTTP {code} in {dur}s. Schema minimale accettato!")
        results["probes"].append({
            "probe_id": "B3",
            "name": "Minimal schema (strict=True)",
            "status": "PASS",
            "http_code": code,
            "schema_hash": compute_schema_hash(minimal_schema),
            "duration": dur,
        })
    else:
        print(f"  [FAIL] HTTP {code} in {dur}s. Rifiutato: {text[:300]}")
        results["probes"].append({
            "probe_id": "B3",
            "name": "Minimal schema (strict=True)",
            "status": "FAIL",
            "http_code": code,
            "schema_hash": compute_schema_hash(minimal_schema),
            "duration": dur,
            "error": text[:300],
        })

    # B4: POST con TOPIC_DETECTION_SCHEMA esatto
    print("\n[*] Probe B4: POST /v1/chat/completions con TOPIC_DETECTION_SCHEMA ESATTO...")
    payload_b4 = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Sei un analista di testi."},
            {"role": "user", "content": "Argomento: Riunione di lavoro.\nEvidenze:\n[E001] Buongiorno a tutti.\n[E002] Iniziamo la riunione."},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "topic_detection",
                "strict": True,
                "schema": TOPIC_DETECTION_SCHEMA,
            },
        },
    }
    t0 = time.perf_counter()
    code, data, text = send_http_request("/v1/chat/completions", method="POST", data=payload_b4, timeout=60.0)
    dur = round(time.perf_counter() - t0, 3)
    b4_pass = False
    if code == 200 and data and "choices" in data:
        content = data["choices"][0]["message"].get("content", "")
        # Validazione Python completa
        try:
            valid_set = {"E001", "E002"}
            dec, e_ids, rat = validate_topic_detection_payload(content, valid_set)
            print(f"  [OK] HTTP {code} in {dur}s. Validazione Python SUPERATA! Decisione: {dec.value}, Evidenze: {e_ids}")
            b4_pass = True
        except Exception as ve:
            print(f"  [WARN] HTTP {code} ma validazione fallita: {ve}. Content: {content[:200]}")
        results["probes"].append({
            "probe_id": "B4",
            "name": "TOPIC_DETECTION_SCHEMA esatto",
            "status": "PASS" if b4_pass else "VALIDATION_FAILED",
            "http_code": code,
            "schema_hash": compute_schema_hash(TOPIC_DETECTION_SCHEMA),
            "duration": dur,
        })
    else:
        print(f"  [FAIL] HTTP {code} in {dur}s. Rifiuto server: {text[:400]}")
        results["probes"].append({
            "probe_id": "B4",
            "name": "TOPIC_DETECTION_SCHEMA esatto",
            "status": "FAIL",
            "http_code": code,
            "schema_hash": compute_schema_hash(TOPIC_DETECTION_SCHEMA),
            "duration": dur,
            "error": text[:400],
        })

    # B5: POST con TOPIC_DISCOVERY_SCHEMA esatto
    print("\n[*] Probe B5: POST /v1/chat/completions con TOPIC_DISCOVERY_SCHEMA ESATTO...")
    payload_b5 = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Sei un analista di testi."},
            {"role": "user", "content": "Estrai gli argomenti principali.\nEvidenze:\n[E001] Parliamo della revisione del bilancio.\n[E002] I numeri sono positivi."},
        ],
        "temperature": 0.0,
        "max_tokens": 300,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "topic_discovery",
                "strict": True,
                "schema": TOPIC_DISCOVERY_SCHEMA,
            },
        },
    }
    t0 = time.perf_counter()
    code, data, text = send_http_request("/v1/chat/completions", method="POST", data=payload_b5, timeout=60.0)
    dur = round(time.perf_counter() - t0, 3)
    b5_pass = False
    if code == 200 and data and "choices" in data:
        content = data["choices"][0]["message"].get("content", "")
        try:
            valid_set = {"E001", "E002"}
            topics = validate_topic_discovery_payload(content, valid_set, max_topics=5)
            print(f"  [OK] HTTP {code} in {dur}s. Discovery validata: {len(topics)} topic emersi.")
            b5_pass = True
        except Exception as ve:
            print(f"  [WARN] HTTP {code} ma validazione Discovery fallita: {ve}")
        results["probes"].append({
            "probe_id": "B5",
            "name": "TOPIC_DISCOVERY_SCHEMA esatto",
            "status": "PASS" if b5_pass else "VALIDATION_FAILED",
            "http_code": code,
            "schema_hash": compute_schema_hash(TOPIC_DISCOVERY_SCHEMA),
            "duration": dur,
        })
    else:
        print(f"  [FAIL] HTTP {code} in {dur}s. Rifiuto server: {text[:400]}")
        results["probes"].append({
            "probe_id": "B5",
            "name": "TOPIC_DISCOVERY_SCHEMA esatto",
            "status": "FAIL",
            "http_code": code,
            "schema_hash": compute_schema_hash(TOPIC_DISCOVERY_SCHEMA),
            "duration": dur,
            "error": text[:400],
        })

    # B7: Se B4 ha fallito, isoliamo il motivo testando variazioni sistematiche di TOPIC_DETECTION_SCHEMA
    if not b4_pass:
        print("\n[*] Probe B7: Analisi differenziale delle proprietà di TOPIC_DETECTION_SCHEMA...")
        
        # Variazione 1: strict=False invece di True
        payload_v1 = json.loads(json.dumps(payload_b4))
        payload_v1["response_format"]["json_schema"]["strict"] = False
        code_v1, _, text_v1 = send_http_request("/v1/chat/completions", method="POST", data=payload_v1, timeout=60.0)
        print(f"  - Variazione 1 (strict=False): HTTP {code_v1} -> {text_v1[:150]}")
        results["probes"].append({"probe_id": "B7_strict_false", "http_code": code_v1, "error": text_v1[:150]})

        # Variazione 2: Senza enum in decision (solo type: string)
        schema_no_enum = json.loads(json.dumps(TOPIC_DETECTION_SCHEMA))
        del schema_no_enum["properties"]["decision"]["enum"]
        payload_v2 = json.loads(json.dumps(payload_b4))
        payload_v2["response_format"]["json_schema"]["schema"] = schema_no_enum
        code_v2, _, text_v2 = send_http_request("/v1/chat/completions", method="POST", data=payload_v2, timeout=60.0)
        print(f"  - Variazione 2 (senza enum): HTTP {code_v2} -> {text_v2[:150]}")
        results["probes"].append({"probe_id": "B7_no_enum", "http_code": code_v2, "error": text_v2[:150]})

        # Variazione 3: Con description rimosse
        schema_no_desc = json.loads(json.dumps(TOPIC_DETECTION_SCHEMA))
        for p in schema_no_desc["properties"].values():
            p.pop("description", None)
        payload_v3 = json.loads(json.dumps(payload_b4))
        payload_v3["response_format"]["json_schema"]["schema"] = schema_no_desc
        code_v3, _, text_v3 = send_http_request("/v1/chat/completions", method="POST", data=payload_v3, timeout=60.0)
        print(f"  - Variazione 3 (senza description): HTTP {code_v3} -> {text_v3[:150]}")
        results["probes"].append({"probe_id": "B7_no_desc", "http_code": code_v3, "error": text_v3[:150]})

        # Variazione 4: type="json_object"
        payload_v4 = {
            "model": model_id,
            "messages": payload_b4["messages"],
            "temperature": 0.0,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        code_v4, data_v4, text_v4 = send_http_request("/v1/chat/completions", method="POST", data=payload_v4, timeout=60.0)
        print(f"  - Variazione 4 (response_format json_object): HTTP {code_v4} -> {text_v4[:150]}")
        results["probes"].append({"probe_id": "B7_json_object", "http_code": code_v4, "error": text_v4[:150]})

    print("\n" + "=" * 80)
    print("DIAGNOSTICA COMPLETATA")
    print("=" * 80)
    return results


if __name__ == "__main__":
    res = run_diagnostics()
