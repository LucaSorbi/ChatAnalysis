#!/usr/bin/env python3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import LmStudioClient
from ai.structured import TOPIC_DISCOVERY_SCHEMA

client = LmStudioClient(base_url="http://127.0.0.1:1234")

messages = [
    {
        "role": "system",
        "content": """TASK: OPEN TOPIC DISCOVERY
You are an expert conversation analyst.
Analyze the provided messages and extract all distinct topics, subjects, and activities discussed.

OUTPUT RULES:
1. Provide a concise 'label' for each topic (in Italian, e.g. 'Organizzazione viaggio', 'Preparazione esami', 'Attività sportiva').
2. Provide a 'short_description' explaining objectively what is discussed (in Italian).
3. Cite the exact 'evidence_ids' supporting each topic using ONLY the assigned alias identifiers (e.g. 'E001', 'E002').
4. Return a JSON object with 'topics' matching the schema, with at most 4 elements.
""",
    },
    {
        "role": "user",
        "content": """=== BEGIN FORENSIC EVIDENCE DATA (UNTRUSTED RAW JSON) ===
{
  "document_id": "test_doc",
  "strategy": "DIRECT_MULTILINGUAL",
  "total_bundles": 3,
  "total_evidence_sections": 3,
  "evidence_sections": [
    {
      "evidence_id": "E001",
      "source_type": "ORIGINAL_TEXT",
      "message_id": "m_1",
      "source_name": "s",
      "source_record_id": "1",
      "language": null,
      "ordinal": null,
      "text": "Ciao Marco! Ho appena controllato i voli per il nostro viaggio a Madrid il mese prossimo."
    },
    {
      "evidence_id": "E002",
      "source_type": "ORIGINAL_TEXT",
      "message_id": "m_2",
      "source_name": "s",
      "source_record_id": "2",
      "language": null,
      "ordinal": null,
      "text": "Ottimo, ho trovato un hotel in centro vicino a Puerta del Sol e al Museo del Prado."
    },
    {
      "evidence_id": "E003",
      "source_type": "ORIGINAL_TEXT",
      "message_id": "m_3",
      "source_name": "s",
      "source_record_id": "3",
      "language": null,
      "ordinal": null,
      "text": "Perfetto! Prenotiamo subito i biglietti aerei Ryanair e prepariamo le valigie per il weekend."
    }
  ]
}
=== END FORENSIC EVIDENCE DATA ===

Based on the conversation evidence above, extract up to 4 main topics and activities discussed.
Return ONLY a valid JSON object matching the schema.""",
    },
]

resp = client.chat_completion(
    messages=messages,
    model_id="qwen2.5-7b-instruct",
    schema=TOPIC_DISCOVERY_SCHEMA,
    temperature=0.0,
    timeout_seconds=120.0,
)
print("CONTENT:", repr(resp.content))
