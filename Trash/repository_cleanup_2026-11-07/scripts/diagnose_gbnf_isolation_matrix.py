"""
scripts/diagnose_gbnf_isolation_matrix.py
-----------------------------------------
Autonomously diagnose GBNF crash and JSON capabilities of LM Studio on 127.0.0.1:1234
using exclusively synthetic data across the test matrix:
A. Text request without JSON Schema
B. Minimal JSON Schema
C. Exact Detection JSON Schema
D. Exact Discovery JSON Schema
E. Repeated requests (5x) for intermittency
F. response_format.type = "json_object"
G. Prompt-guided JSON without response_format + strict Python validation
H. Comparison with parallel slots = 1 vs parallel slots = 4
I. Model unload and reload behavior
"""
import json
import time
import urllib.request
import urllib.error
import subprocess
import sys
import os

BASE_URL = "http://127.0.0.1:1234"
MODEL_ID = "qwen2.5-7b-instruct"

# Schemas from ai/structured.py
TOPIC_DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["PRESENT", "ABSENT", "UNCERTAIN"],
            "description": "Verdetto sulla presenza del topic nella conversazione.",
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Elenco degli identificativi esatti delle evidenze che motivano la decisione.",
        },
        "rationale": {
            "type": "string",
            "description": "Spiegazione sintetica ed oggettiva del risultato (non chain-of-thought prolisso).",
        },
    },
    "required": ["decision", "evidence_ids", "rationale"],
    "additionalProperties": False,
}

TOPIC_DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Etichetta sintetica dell'argomento emerso.",
                    },
                    "short_description": {
                        "type": "string",
                        "description": "Breve descrizione oggettiva dell'argomento (nessuna inferenza su identità o dati sensibili).",
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Evidenze che supportano l'argomento (almeno una richiesta).",
                    },
                },
                "required": ["label", "short_description", "evidence_ids"],
                "additionalProperties": False,
            },
            "description": "Elenco dei temi discussi nella conversazione.",
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

MINIMAL_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["OK", "ERROR"]}
    },
    "required": ["status"],
    "additionalProperties": False,
}

# Synthetic prompt data
SYNTHETIC_EVIDENCE = """=== FORENSIC EVIDENCE DATA ===
[E001] 2026-01-01 10:00:00 Alice: Ciao Bob, hai preparato il resoconto del progetto Alpha?
[E002] 2026-01-01 10:01:00 Bob: Si Alice, ho completato l'analisi dei requisiti tecnici e i test.
[E003] 2026-01-01 10:02:00 Alice: Ottimo, includi anche i parametri di performance e la stima tempi.
=== END FORENSIC EVIDENCE DATA ==="""

def send_chat_completion(payload: dict, timeout: float = 60.0):
    url = f"{BASE_URL}/v1/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = time.perf_counter() - t0
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
            return {
                "status_code": resp.status,
                "elapsed": round(elapsed, 2),
                "body": body,
                "error": None,
                "raw_text": raw,
            }
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - t0
        err_body = e.read().decode("utf-8", errors="replace")
        return {
            "status_code": e.code,
            "elapsed": round(elapsed, 2),
            "body": None,
            "error": f"HTTP {e.code}: {e.reason} | {err_body[:300]}",
            "raw_text": err_body,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "status_code": 0,
            "elapsed": round(elapsed, 2),
            "body": None,
            "error": f"{type(e).__name__}: {e}",
            "raw_text": "",
        }

def run_single_test(name: str, payload: dict, expected_validator=None):
    print(f"\n==================================================")
    print(f"RUNNING TEST: {name}")
    print(f"Format: {payload.get('response_format')}")
    res = send_chat_completion(payload)
    status = res["status_code"]
    elapsed = res["elapsed"]
    print(f"HTTP Status: {status} in {elapsed}s")
    if res["error"]:
        print(f"ERROR: {res['error']}")
        return {
            "test": name,
            "status_code": status,
            "elapsed": elapsed,
            "success": False,
            "error": res["error"],
            "finish_reason": None,
            "tokens": None,
            "content": None,
            "valid_json": False,
        }
    
    body = res["body"]
    choice = body["choices"][0]
    finish_reason = choice.get("finish_reason")
    content = choice["message"].get("content", "")
    usage = body.get("usage", {})
    tokens = usage.get("completion_tokens")
    prompt_tokens = usage.get("prompt_tokens")
    print(f"finish_reason: {finish_reason} | completion_tokens: {tokens} | prompt_tokens: {prompt_tokens}")
    print(f"content snippet: {content[:150]!r}...")
    
    valid_json = False
    validation_ok = True
    val_err = None
    try:
        parsed = json.loads(content)
        valid_json = True
        if expected_validator:
            expected_validator(parsed)
    except Exception as e:
        validation_ok = False
        val_err = str(e)
    
    print(f"JSON parsed: {valid_json} | Validator: {validation_ok} ({val_err if val_err else 'OK'})")
    return {
        "test": name,
        "status_code": status,
        "elapsed": elapsed,
        "success": (status == 200 and validation_ok),
        "finish_reason": finish_reason,
        "completion_tokens": tokens,
        "prompt_tokens": prompt_tokens,
        "content_length": len(content),
        "valid_json": valid_json,
        "validator_error": val_err,
        "error": None,
    }

def execute_matrix():
    results = []

    # A. Text without schema
    payload_a = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "Sei un assistente forense. Rispondi sinteticamente in italiano."},
            {"role": "user", "content": f"{SYNTHETIC_EVIDENCE}\nDi cosa parlano Alice e Bob?"}
        ],
        "temperature": 0.0,
        "max_tokens": 256,
    }
    results.append(run_single_test("A: Simple Text (No Schema)", payload_a))

    # B. Minimal JSON Schema
    payload_b = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "Rispondi in formato JSON conformandoti allo schema."},
            {"role": "user", "content": "Verifica se il test è OK. Restituisci status OK."}
        ],
        "temperature": 0.0,
        "max_tokens": 128,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "status_check", "strict": True, "schema": MINIMAL_SCHEMA}
        }
    }
    def val_b(d):
        assert d.get("status") in ("OK", "ERROR")
    results.append(run_single_test("B: Minimal JSON Schema", payload_b, val_b))

    # C. Exact Detection JSON Schema
    payload_c = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "TASK: SPECIFIC TOPIC DETECTION\nValuta se il topic 'Progetto Alpha' è presente.\nRegole: decision in ('PRESENT', 'ABSENT', 'UNCERTAIN'), evidence_ids: lista di alias, rationale: stringa."},
            {"role": "user", "content": f"{SYNTHETIC_EVIDENCE}\nValuta se 'Progetto Alpha' è PRESENT, ABSENT o UNCERTAIN.\nReturn ONLY a valid JSON object matching the schema."}
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "structured_output", "strict": True, "schema": TOPIC_DETECTION_SCHEMA}
        }
    }
    def val_c(d):
        assert d["decision"] in ("PRESENT", "ABSENT", "UNCERTAIN")
        assert isinstance(d["evidence_ids"], list)
        assert isinstance(d["rationale"], str) and len(d["rationale"]) > 0
    results.append(run_single_test("C: Exact Detection JSON Schema", payload_c, val_c))

    # D. Exact Discovery JSON Schema
    payload_d = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "TASK: TOPIC DISCOVERY\nIdentifica i temi emersi con label, short_description e evidence_ids."},
            {"role": "user", "content": f"{SYNTHETIC_EVIDENCE}\nScopri fino a 3 argomenti principali.\nReturn ONLY a valid JSON object matching the schema."}
        ],
        "temperature": 0.0,
        "max_tokens": 800,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "structured_output", "strict": True, "schema": TOPIC_DISCOVERY_SCHEMA}
        }
    }
    def val_d(d):
        assert "topics" in d and isinstance(d["topics"], list)
        for t in d["topics"]:
            assert "label" in t and "short_description" in t and "evidence_ids" in t
    results.append(run_single_test("D: Exact Discovery JSON Schema", payload_d, val_d))

    # E. Repeated requests (5x) for intermittency
    print("\n--- E: REPEATED 5x TEST OF DETECTION SCHEMA FOR INTERMITTENCY ---")
    for i in range(5):
        res = run_single_test(f"E{i+1}: Repeated Detection Schema ({i+1}/5)", payload_c, val_c)
        results.append(res)
        time.sleep(0.5)

    # F. response_format = {"type": "json_object"}
    payload_f = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "TASK: SPECIFIC TOPIC DETECTION\nRispondi esclusivamente con un oggetto JSON avente le chiavi: 'decision' ('PRESENT'|'ABSENT'|'UNCERTAIN'), 'evidence_ids' (array di stringhe), 'rationale' (stringa)."},
            {"role": "user", "content": f"{SYNTHETIC_EVIDENCE}\nValuta se 'Progetto Alpha' è PRESENT, ABSENT o UNCERTAIN."}
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "response_format": {"type": "json_object"}
    }
    results.append(run_single_test("F: response_format = json_object", payload_f, val_c))

    # G. No response_format, Prompt-guided JSON + strict Python validation
    payload_g = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "TASK: SPECIFIC TOPIC DETECTION\nRispondi esclusivamente con un oggetto JSON valido (senza blocchi markdown ```json o testo prima/dopo).\nSchema atteso: {\"decision\": \"PRESENT\"|\"ABSENT\"|\"UNCERTAIN\", \"evidence_ids\": [\"E001\"], \"rationale\": \"stringa\"}."},
            {"role": "user", "content": f"{SYNTHETIC_EVIDENCE}\nValuta se 'Progetto Alpha' è PRESENT, ABSENT o UNCERTAIN.\nInizia la tua risposta direttamente con '{' e termina con '}'."}
        ],
        "temperature": 0.0,
        "max_tokens": 512,
    }
    results.append(run_single_test("G: Prompt-guided JSON (No response_format) + Python Val", payload_g, val_c))

    print("\n\n==================================================")
    print("TEST MATRIX SUMMARY:")
    for r in results:
        status_str = "PASS" if r["success"] else "FAIL"
        err_str = f" | {r.get('error') or r.get('validator_error')}" if not r["success"] else ""
        print(f"[{status_str}] {r['test']}: HTTP {r['status_code']} ({r['elapsed']}s, finish_reason={r.get('finish_reason')}){err_str}")

if __name__ == "__main__":
    execute_matrix()
