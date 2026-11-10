import json
import urllib.request
import time

url = "http://127.0.0.1:1234/v1/chat/completions"

headers = {"Content-Type": "application/json"}

# Test synthetic discovery payload
schema = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "short_description": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["label", "short_description", "evidence_ids"],
                "additionalProperties": False
            }
        }
    },
    "required": ["topics"],
    "additionalProperties": False
}

payload = {
    "model": "qwen2.5-7b-instruct",
    "messages": [
        {"role": "system", "content": "Sei un analista forense esperto in chat. Identifica fino a 3 argomenti principali con evidence_ids."},
        {"role": "user", "content": "Testo evidenze:\n[E001] Ciao Marco, ci vediamo alle 15 per la corsa?\n[E002] Ciao Luca, sì perfetto alle Cascine.\n[E003] Porti anche la borraccia e le scarpe nuove?"}
    ],
    "temperature": 0.0,
    "seed": 42,
    "max_tokens": 512,
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_output",
            "strict": True,
            "schema": schema
        }
    }
}

print("Invio richiesta a LM Studio...")
start = time.perf_counter()
req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        elapsed = time.perf_counter() - start
        body = resp.read().decode("utf-8")
        print(f"HTTP Status: {resp.status} in {elapsed:.2f}s")
        data = json.loads(body)
        print("Response content:")
        print(data["choices"][0]["message"]["content"])
        print("Finish reason:", data["choices"][0].get("finish_reason"))
        print("Usage:", data.get("usage"))
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="ignore")
    print(f"HTTP Error {e.code}: {err_body}")
except Exception as e:
    print(f"Error: {e}")
