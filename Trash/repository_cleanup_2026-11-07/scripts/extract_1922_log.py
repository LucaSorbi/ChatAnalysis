with open(r"C:\Users\Luca\Desktop\Tesi\ChatAnalysis\scripts\extract_run_snippets.py", "w", encoding="utf-8") as out:
    pass

with open(r"C:\Users\Luca\.lmstudio\server-logs\2026-10\2026-10-26.1.log", "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

# Find occurrences of 19:22:19
pos = text.find("2026-10-26 19:22:")
print("Found 19:22 at:", pos)
if pos != -1:
    req_pos = text.rfind("Received request: POST to /v1/chat/completions", 0, pos + 500)
    print("Request pos:", req_pos)
    print("=== SNIPPET AT 19:22 ===")
    print(text[req_pos:req_pos + 4000].encode("ascii", errors="replace").decode("ascii"))
