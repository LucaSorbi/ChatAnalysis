import os

log_path = os.path.expanduser(r"~/.lmstudio/server-logs/2026-10/2026-10-26.1.log")
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

# find position of 147510 or 147505
for task_id in ["147510", "147505", "147502", "116487"]:
    idx = text.find(f"task {task_id}")
    if idx != -1:
        print(f"\n==================== TASK {task_id} FOUND AT {idx} ====================")
        snippet = text[max(0, idx - 4000): min(len(text), idx + 2000)]
        print(snippet)
        break
