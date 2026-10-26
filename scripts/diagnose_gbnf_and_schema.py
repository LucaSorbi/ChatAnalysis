import json
import urllib.request
import time
import sys

sys.stdout.reconfigure(encoding="utf-8")
url = "http://127.0.0.1:1234/v1/chat/completions"

headers = {"Content-Type": "application/json"}

# 1. TOPIC_DISCOVERY_SCHEMA con strict: True
schema_strict = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "short_description": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["label", "short_description", "evidence_ids"],
                "additionalProperties": False
            }
        }
    },
    "required": ["topics"],
    "additionalProperties": False
}

def test_variant(name, rf_payload, messages, max_tokens=512, seed=42):
    print("=" * 70)
    print(f"PROVA: {name}")
    payload = {
        "model": "qwen2.5-7b-instruct",
        "messages": messages,
        "temperature": 0.0,
        "seed": seed,
        "max_tokens": max_tokens,
    }
    if rf_payload is not None:
        payload["response_format"] = rf_payload

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            elapsed = time.perf_counter() - t0
            raw_body = resp.read().decode("utf-8")
            data = json.loads(raw_body)
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
            usage = data.get("usage", {})
            print(f"[OK] HTTP {resp.status} in {elapsed:.2f}s | finish: {finish} | tokens: {usage.get('completion_tokens')}")
            # Validazione JSON lato Python
            try:
                parsed = json.loads(content)
                print(f"     JSON valido: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
                if "topics" in parsed:
                    print(f"     Topics count: {len(parsed['topics'])}")
            except Exception as pe:
                print(f"     [WARN] JSONDecodeError: {pe}")
                print(f"     Contenuto parziale: {content[:150]}...")
            return True, elapsed, None
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - t0
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[ERR] HTTP {e.code} in {elapsed:.2f}s: {err_body}")
        return False, elapsed, err_body
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[EXC] in {elapsed:.2f}s: {e}")
        return False, elapsed, str(e)

# Messaggi per test
msgs_simple = [
    {"role": "system", "content": "Identifica fino a 3 argomenti con evidence_ids."},
    {"role": "user", "content": "Testo evidenze:\n[E1] Domani riunione ore 10.\n[E2] Ricevuto, porto il report.\n[E3] Chiama anche Stefano."}
]

# Prova 1: strict True
test_variant("json_schema strict=True", {
    "type": "json_schema",
    "json_schema": {"name": "discovery", "strict": True, "schema": schema_strict}
}, msgs_simple)

# Prova 2: strict False
schema_non_strict = dict(schema_strict)
test_variant("json_schema strict=False", {
    "type": "json_schema",
    "json_schema": {"name": "discovery", "strict": False, "schema": schema_non_strict}
}, msgs_simple)

# Prova 3: response_format text con JSON instructions nel prompt
msgs_prompt_json = [
    {"role": "system", "content": "Sei un analista. Rispondi ESCLUSIVAMENTE con un JSON valido contenente la chiave 'topics'. Non aggiungere testo introduttivo o conclusivo. Schema: {\"topics\": [{\"label\": string, \"short_description\": string, \"evidence_ids\": [string]}]}"},
    {"role": "user", "content": "Testo evidenze:\n[E1] Domani riunione ore 10.\n[E2] Ricevuto, porto il report.\n[E3] Chiama anche Stefano."}
]
test_variant("type=text (prompt-driven JSON)", {"type": "text"}, msgs_prompt_json)

