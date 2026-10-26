import os

log_path = os.path.expanduser(r"~/.lmstudio/server-logs/2026-10/2026-10-26.1.log")
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
# Find occurrences of Unexpected empty grammar stack
for idx, line in enumerate(lines):
    if "Unexpected empty grammar stack" in line:
        print(f"\n--- MATCH AT LINE {idx} ---")
        start = max(0, idx - 15)
        end = min(len(lines), idx + 15)
        for j in range(start, end):
            prefix = ">>> " if j == idx else "    "
            print(f"{prefix}{lines[j].strip()}")
