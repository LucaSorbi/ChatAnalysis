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

prompt_tests = [
    ("Current V2 (as used)", """TASK: OPEN TOPIC DISCOVERY (Version: topic_discovery_operational_v2)
You are an expert forensic assistant. You must analyze the forensic conversation evidence and extract all distinct topics, subjects, activities, and matters discussed by the participants.
When conversation text contains discussions, plans, activities, sports, studies, or meetings, you MUST identify and return them as topics in the JSON array.
Do not return an empty array if topics are discussed in the evidence.

OBJECTIVITY CONSTRAINTS:
- Describe objective topics, events, activities, and matters discussed in the text.
- Do NOT infer personal identities, real names, or demographic profiles.

OUTPUT RULES:
1. Provide a concise 'label' for each topic (in Italian, e.g. 'Organizzazione incontro', 'Preparazione esami', 'Attività sportiva').
2. Provide a 'short_description' explaining objectively what is discussed (in Italian).
3. Cite the exact 'evidence_ids' supporting each topic using ONLY the assigned alias identifiers shown in the evidence block (e.g. 'E001', 'E002'). Each topic must cite at least one valid evidence_id from this block.
4. Return an array of topics matching schema, with at most 4 elements.
"""),
    ("Direct Directive without Negative Preambles", """You are an expert conversational analyst.
Analyze the provided messages and extract the primary topics discussed.

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian
   - "short_description": description of what is discussed in Italian
   - "evidence_ids": list of evidence IDs (e.g. ["E001", "E002"]) that discuss this topic.
3. Every evidence_id cited must be an ID from the provided evidence.
4. Output a JSON object with key "topics" containing the list of discovered topics.
"""),
    ("Italian Native Prompting", """Sei un analista forense esperto nell'analisi di conversazioni.
Il tuo compito è estrarre i temi principali (argomenti, attività, progetti, eventi) discussi nei messaggi della conversazione.

Per ogni argomento individuato:
- "label": Titolo conciso in italiano (es. 'Organizzazione viaggio a Madrid', 'Preparazione esame', 'Allenamento sportivo').
- "short_description": Breve descrizione in italiano dei contenuti discussi.
- "evidence_ids": Lista degli identificatori (es. ["E001", "E002"]) che contengono i messaggi relativi a questo argomento.

Regole:
- Estrai tutti i temi trattati nei messaggi.
- Cita esclusivamente gli evidence_id presenti nel testo fornito.
- Restituisci un oggetto JSON con il campo 'topics'.
""")
]

for name, sys_p in prompt_tests:
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": f"Forensic Evidence:\n{evidence_json}\n\nEstrai i topic discussi nei messaggi sopra in formato JSON."}
    ]
    resp = client.chat_completion(messages=messages, model_id="qwen2.5-7b-instruct", schema=TOPIC_DISCOVERY_SCHEMA, temperature=0.0)
    print(f"*** TEST: {name} ***")
    print("RESPONSE:", repr(resp.content))
    print()
