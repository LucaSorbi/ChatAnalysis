import re, json

raw_from_qwen = '''{
  \"topics\": [{
     \"	label\": \"Attività sportiva\",
     \"	short_description\": \"Discussioni su programmi di allenamento e nutrizione per una corsa a 10 km in domenica, ripetizioni sui 100[\". 
     \"	evidence_ids\": [\"E001\", \"E002\"],
   },
   {
     \"\"label\": \"Alimentazione e supplementi\",
     \"	short_description\": \"Mention of the need for a proper diet and the testing of dietary and mineral salts before a marathon, with a specific reference to hydration with electrolyte water in the middle of the race\",
     \"	evidence_ids\": [\"E004\", \"E005\"],
   },
   {
     \"\"label\": \"Preparazione per la corsa\",
     \"	short_description\": \"Discussion on recovery and marathon preparation, focusing on 10[\". 
     \"	evidence_ids\": [\"E002\"],
}]
}'''

# Let's clean quotes and keys:
clean = raw_from_qwen.replace('\\"', '"')
clean = re.sub(r'\"+([a-zA-Z0-9_]+)\"+', r'"\1"', clean)
clean = re.sub(r'\"[\t\s]+([a-zA-Z0-9_]+)[\t\s]*\"', r'"\1"', clean)

topics_extracted = []
# Find each topic block:
blocks = re.findall(r'\{\s*"label":\s*"([^"]+)",\s*"short_description":\s*"(.*?)"[,\s]+"evidence_ids":\s*\[([^\]]*)\]', clean, re.DOTALL)
print("Found blocks via regex:", len(blocks))
for label, desc, eids_raw in blocks:
    eids = [re.sub(r'[^a-zA-Z0-9_:]', '', e) for e in eids_raw.split(',') if re.sub(r'[^a-zA-Z0-9_:]', '', e)]
    topics_extracted.append({
        "label": label.strip(),
        "short_description": desc.strip(),
        "evidence_ids": eids
    })

print("Extracted topics:", json.dumps(topics_extracted, indent=2))
