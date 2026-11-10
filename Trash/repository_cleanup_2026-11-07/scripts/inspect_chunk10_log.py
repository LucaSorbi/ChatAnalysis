import os

log_path = os.path.expanduser(r"~/.lmstudio/server-logs/2026-10/2026-10-26.1.log")
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

print(f"Total lines in log: {len(lines)}")
# Find occurrences from the last 30 minutes
matches = []
for i, line in enumerate(lines[-2000:]):
    if "E0" in line or "invalid" in line.lower() or "alias" in line.lower() or "chunk" in line.lower():
        matches.append((i, line.strip()))

print(f"Matches count: {len(matches)}")
for idx, m in matches[-20:]:
    print(m[:140])
