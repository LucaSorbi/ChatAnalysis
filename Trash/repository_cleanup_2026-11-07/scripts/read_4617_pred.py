with open(r"C:\Users\Luca\.lmstudio\server-logs\2026-10\2026-10-26.1.log", "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

pos_4617 = text.find("task 4617")
pred_pos = text.find("Generated prediction:", pos_4617)
end_pred = text.find('"usage":', pred_pos)
print(text[pred_pos:end_pred + 500].encode("ascii", errors="replace").decode("ascii"))
