from pathlib import Path
import json, re

# Let's get the exact string as received by python from LM Studio JSON response:
p = sorted(Path(r"C:\Users\Luca\.lmstudio\server-logs\2026-10").glob("*.log"))[-1]
txt = p.read_text("utf-8", "ignore")

# Find the last "Generated prediction: {" block:
pred_idx = txt.rfind("Generated prediction: {")
block = txt[pred_idx + len("Generated prediction: "):]
end_block = block.find("\n[2026-")
if end_block != -1:
    block = block[:end_block]

pred_json = json.loads(block)
raw_content = pred_json["choices"][0]["message"]["content"]
print("=== PYTHON RAW CONTENT ===")
print(raw_content)

def repair_llm_json(s: str) -> str:
    # 1. Strip backslashes before quotes
    s = re.sub(r'\\+\"', '"', s)
    # 2. Fix multiple quotes around keys e.g. ""label":
    s = re.sub(r'\"+([a-zA-Z0-9_]+)\"+(\s*:)', r'"\1"\2', s)
    # 3. Fix whitespace/tabs inside quotes for keys e.g. "\tlabel":
    s = re.sub(r'\"[\t\s]+([a-zA-Z0-9_]+)[\t\s]*\"(\s*:)', r'"\1"\2', s)
    # 4. Fix doubled quotes at end of values before comma or newline
    s = re.sub(r'\"+(\s*,)', r'"\1', s)
    s = re.sub(r'\"+(\s*\n)', r'"\1', s)
    # 5. Fix trailing commas before } or ]
    s = re.sub(r',\s*([\]}])', r'\1', s)
    return s

repaired = repair_llm_json(raw_content)
print("=== REPAIRED CONTENT ===")
print(repaired)
parsed = json.loads(repaired)
print("SUCCESSFULLY PARSED!", list(parsed.keys()))
for t in parsed["topics"]:
    print(" - Topic:", t.get("label"), "| EIDs:", t.get("evidence_ids"))
