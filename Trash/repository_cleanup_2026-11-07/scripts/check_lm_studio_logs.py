import os
from pathlib import Path

lm_log = Path(r"C:\Users\Luca\AppData\Roaming\LM Studio\logs\main.log")
if lm_log.exists():
    print(f"LM Studio main.log size: {lm_log.stat().st_size} bytes")
    with open(lm_log, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        print("Contains 'run_disc_3f3731eb04':", "run_disc_3f3731eb04" in content)
        print("Contains 'doc::whatsapp_export':", "doc::whatsapp_export" in content)
        print("Contains 'TOPIC_DISCOVERY_SCHEMA':", "TOPIC_DISCOVERY_SCHEMA" in content)
        print("Contains '\"topics\"':", '"topics"' in content)
        print("Contains 'short_description':", "short_description" in content)
        print("Contains 'evidence_ids':", "evidence_ids" in content)
else:
    print("LM Studio main.log does not exist")
