with open(r"C:\Users\Luca\.lmstudio\server-logs\2026-10\2026-10-26.1.log", "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

pos = text.find("2026-10-26 20:21")
print("Found 20:21 at:", pos)
if pos != -1:
    print(text[pos:pos+4000].encode("ascii", errors="replace").decode("ascii"))
