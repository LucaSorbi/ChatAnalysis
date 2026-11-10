#!/usr/bin/env python3
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.structured import validate_topic_discovery_payload

raw = """{
  "topics": [
    {
      "label": "incontro a Madrid",
      "short_description": "Voli e hotel a Madrid",
      "evidence_ids": ["E001", "E002"]
    },
    {
      "label": "prontegie valigie",
      "short_description": "Preparazione valigie",
      "evidence_ids": ["E001", "E002", "E002"]
    }
  ]
}"""

valid_ids = {'msg_1::ORIGINAL_TEXT', 'msg_2::ORIGINAL_TEXT', 'msg_3::ORIGINAL_TEXT'}
alias_map = {'E001': 'msg_1::ORIGINAL_TEXT', 'E002': 'msg_2::ORIGINAL_TEXT', 'E003': 'msg_3::ORIGINAL_TEXT'}

try:
    res = validate_topic_discovery_payload(raw, valid_ids, max_topics=6, alias_map=alias_map)
except Exception as str_err:
    if "duplicat" in str(str_err).lower():
        import json
        from ai.structured import parse_and_validate_json
        parsed = parse_and_validate_json(raw)
        for t_dict in parsed.get("topics", []):
            if isinstance(t_dict, dict) and isinstance(t_dict.get("evidence_ids"), list):
                t_dict["evidence_ids"] = list(dict.fromkeys(t_dict["evidence_ids"]))
        res = validate_topic_discovery_payload(json.dumps(parsed), valid_ids, max_topics=6, alias_map=alias_map)
        print("DEDUPLICATION SUCCESS:", len(res))
        for t in res:
            print("  -", t.label, t.evidence_ids)
    else:
        raise
