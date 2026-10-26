import json
from pathlib import Path

transcript_path = Path(r"C:\Users\Luca\.gemini\antigravity-ide\brain\d14e319d-796f-43c8-9ab2-99229890a270\.system_generated\logs\transcript_full.jsonl")
with open(transcript_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i in range(541, min(580, len(lines))):
    obj = json.loads(lines[i])
    if obj.get("tool_calls"):
        tc = obj["tool_calls"][0]
        print(f"Line {i}: TC {tc.get('name')} -> {str(tc.get('args'))[:160]}")
    elif obj.get("content") and "Output:" in str(obj.get("content")):
        print(f"Line {i}: Content -> {str(obj.get('content'))[:200]}")
