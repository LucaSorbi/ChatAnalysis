import os

log_path = os.path.expanduser(r"~/.lmstudio/server-logs/2026-10/2026-10-26.1.log")
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

target = '"decision": "PRESENT"'
idx = text.rfind(target)
if idx != -1:
    print(f"Found target at index {idx}")
    start = max(0, idx - 2000)
    end = min(len(text), idx + 500)
    print("=== LOG SNIPPET ===")
    print(text[start:end])
else:
    print("Target not found")
