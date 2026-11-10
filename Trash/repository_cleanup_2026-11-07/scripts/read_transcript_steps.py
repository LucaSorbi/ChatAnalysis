import json
from pathlib import Path

transcript_path = Path(r"C:\Users\Luca\.gemini\antigravity-ide\brain\d14e319d-796f-43c8-9ab2-99229890a270\.system_generated\logs\transcript_full.jsonl")
with open(transcript_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for s in [485, 486, 489, 490, 491, 492]:
    obj = json.loads(lines[s])
    print(f"\n================ STEP {s} ================")
    if obj.get("tool_calls"):
        for tc in obj["tool_calls"]:
            print(f"[TOOL CALL] {tc.get('name')}: {tc.get('args', {}).get('CommandLine')}")
    if obj.get("content"):
        print(f"[CONTENT]\n{obj.get('content')}")
