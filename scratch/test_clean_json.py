import json
import re

raw = '''{
  "topics": [{
     "\tlabel": "Attivita sportiva",
     "\tshort_description": "Discussioni su programmi di allenamento e nutrizione per una corsa a 10 km in domenica, ripetizioni sui 100.", 
     "\tevidence_ids": ["E001", "E002"],
   },
   {
     ""label": "Alimentazione e supplementi",
     "\tshort_description": "Mention of the need for a proper diet",
     "\tevidence_ids": ["E004", "E005"],
   },
   {
     ""label": "Preparazione per la corsa"",
     "\tshort_description": "Discussion on recovery and marathon preparation", 
     "\tevidence_ids": ["E002"],
   }]
}'''

def clean_json(s: str) -> str:
    # 1. Fix doubled quotes or misplaced quotes around keys e.g. ""label": or "label"":
    s = re.sub(r'\"+([a-zA-Z0-9_]+)\"+(\s*:)', r'"\1"\2', s)
    # 2. Fix tab/space inside quotes for keys e.g. "\tlabel":
    s = re.sub(r'\"[\t\s]+([a-zA-Z0-9_]+)[\t\s]*\"(\s*:)', r'"\1"\2', s)
    # 3. Fix doubled quotes at end of string values before comma or newline
    s = re.sub(r'\"+(\s*,)', r'"\1', s)
    s = re.sub(r'\"+(\s*\n)', r'"\1', s)
    # 4. Fix trailing commas before } or ]
    s = re.sub(r',\s*([\]}])', r'\1', s)
    return s

cleaned = clean_json(raw)
data = json.loads(cleaned)
print("SUCCESS! Parsed", len(data["topics"]), "topics:")
for t in data["topics"]:
    print(" -", t["label"], "->", t["evidence_ids"])
