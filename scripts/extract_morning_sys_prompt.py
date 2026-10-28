with open(r"C:\Users\Luca\.lmstudio\server-logs\2026-10\2026-10-26.1.log", "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

pos = 587482
# That was req_pos
end_pos = text.find('"choices":', pos)
print(text[pos:pos+4000])
