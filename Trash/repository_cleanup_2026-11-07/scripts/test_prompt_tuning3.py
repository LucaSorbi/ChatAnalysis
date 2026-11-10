#!/usr/bin/env python3
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import LmStudioClient
from ai.structured import TOPIC_DISCOVERY_SCHEMA

client = LmStudioClient(base_url="http://127.0.0.1:1234")

evidence_json = """
[
  {"evidence_id": "E001", "text": "Ciao Marco! Ho appena controllato i voli per il nostro viaggio a Madrid il mese prossimo."},
  {"evidence_id": "E002", "text": "Ottimo, ho trovato un hotel in centro vicino a Puerta del Sol e al Museo del Prado."},
  {"evidence_id": "E003", "text": "Perfetto! Prenotiamo subito i biglietti aerei Ryanair e prepariamo le valigie per il weekend."}
]
"""

# Let's isolate the difference between test_prompt_tuning (which worked) and test_prompt_tuning2 (which didn't)
# In test_prompt_tuning:
# user_content was: f"Forensic Evidence:\n{evidence_json}\n\nEstrai i topic discussi nei messaggi sopra in formato JSON."
# In test_prompt_tuning2:
# user_content was: f"=== BEGIN FORENSIC EVIDENCE DATA ===\n{evidence_json}\n=== END FORENSIC EVIDENCE DATA ===\n\nExtract the topics discussed above as JSON."

tests = [
    ("Delimiters effect",
     """You are an expert conversational analyst.
Analyze the provided messages and extract the primary topics discussed.

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian
   - "short_description": description of what is discussed in Italian
   - "evidence_ids": list of evidence IDs (e.g. ["E001", "E002"]) that discuss this topic.
3. Every evidence_id cited must be an ID from the provided evidence.
4. Output a JSON object with key "topics" containing the list of discovered topics.
""",
     f"=== BEGIN FORENSIC EVIDENCE DATA ===\n{evidence_json}\n=== END FORENSIC EVIDENCE DATA ===\n\nEstrai i topic discussi nei messaggi sopra in formato JSON."
    ),
    ("No delimiters, English instruction",
     """You are an expert conversational analyst.
Analyze the provided messages and extract the primary topics discussed.

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian
   - "short_description": description of what is discussed in Italian
   - "evidence_ids": list of evidence IDs (e.g. ["E001", "E002"]) that discuss this topic.
3. Every evidence_id cited must be an ID from the provided evidence.
4. Output a JSON object with key "topics" containing the list of discovered topics.
""",
     f"Forensic Evidence:\n{evidence_json}\n\nExtract the topics discussed in the messages above as JSON."
    ),
    ("Serialized Document as serialized_document_for_llm produces",
     """You are an expert conversational analyst.
Analyze the provided messages and extract the primary topics discussed.

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian
   - "short_description": description of what is discussed in Italian
   - "evidence_ids": list of evidence IDs (e.g. ["E001", "E002"]) that discuss this topic.
3. Every evidence_id cited must be an ID from the provided evidence.
4. Output a JSON object with key "topics" containing the list of discovered topics.
""",
     f'''=== BEGIN FORENSIC EVIDENCE DATA (UNTRUSTED RAW JSON) ===
{{
  "document_id": "test_doc",
  "strategy": "DIRECT_MULTILINGUAL",
  "total_bundles": 3,
  "total_evidence_sections": 3,
  "evidence_sections": [
    {{
      "evidence_id": "E001",
      "source_type": "ORIGINAL_TEXT",
      "text": "Ciao Marco! Ho appena controllato i voli per il nostro viaggio a Madrid il mese prossimo."
    }},
    {{
      "evidence_id": "E002",
      "source_type": "ORIGINAL_TEXT",
      "text": "Ottimo, ho trovato un hotel in centro vicino a Puerta del Sol e al Museo del Prado."
    }},
    {{
      "evidence_id": "E003",
      "source_type": "ORIGINAL_TEXT",
      "text": "Perfetto! Prenotiamo subito i biglietti aerei Ryanair e prepariamo le valigie per il weekend."
    }}
  ]
}}
=== END FORENSIC EVIDENCE DATA ===

Estrai i topic discussi nei messaggi sopra in formato JSON.'''
    )
]

for label, sys_p, user_p in tests:
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_p}
    ]
    resp = client.chat_completion(messages=messages, model_id="qwen2.5-7b-instruct", schema=TOPIC_DISCOVERY_SCHEMA, temperature=0.0)
    print(f"=== {label} ===")
    print("RESPONSE:", repr(resp.content))
    print()
