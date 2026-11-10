import urllib.request, json
from pathlib import Path
from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.models import TopicQuery
from ai.serializer import serialize_document_for_llm, generate_evidence_alias_maps
from ai.structured import TOPIC_DETECTION_SCHEMA
from scripts.export_discovery_26of26 import resolve_input_archive_path
zip_path = resolve_input_archive_path()
req = IngestionRequest(file_bytes=zip_path.read_bytes(), filename=zip_path.name, source_format=SourceFormat.WHATSAPP_EXPORT)
doc = list(ingest_file_payload(req).documents.values())[0]

service = LocalTopicAnalysisService()
q_absent = TopicQuery(
    topic_id="q_mutuo_real",
    label="Mutuo ipotecario e compravendita immobiliare",
    description="Richiesta di mutuo ipotecario in banca, stipula di rogito notarile o acquisto di appartamenti.",
)
q_present = TopicQuery(
    topic_id="q_correria_real",
    label="Correria e uscite a correre",
    description="Discussione su appuntamenti per andare a correre, inclusi giorni e orari di ritrovo per correre insieme.",
)

plan = service.create_analysis_plan(document=doc, analysis_type="detection", topic_query=q_absent)
chk0 = plan.chunks[0]
id_to_alias, alias_to_id = generate_evidence_alias_maps(chk0)
evidence_text = serialize_document_for_llm(chk0, evidence_id_map=id_to_alias)

rule_4_cite = "4. CITE ONLY EXACT evidence_ids that are present in the evidence block using their assigned aliases (e.g. 'E001', 'E002'). DO NOT fabricate or invent IDs."

def run_test(topic):
    sys_msg = f"""You are an objective forensic document evaluator.
TASK: SPECIFIC TOPIC DETECTION
TARGET TOPIC: {topic.label}
DESCRIPTION: {topic.description}

DECISION RULES:
- If the target topic is clearly discussed in the conversation, return 'PRESENT' and cite exact evidence aliases in 'evidence_ids'.
- If the target topic is NOT discussed in the conversation, return 'ABSENT' and 'evidence_ids': []. Do NOT make far-fetched connections.
- If ambiguous, return 'UNCERTAIN'.
{rule_4_cite}
- CRITICAL INVARIANT: You can NEVER output 'PRESENT' with an empty 'evidence_ids' list. If there is no specific evidence to cite, the decision MUST be 'ABSENT'.
- Provide a short, objective rationale in Italian explaining the decision.

CONCRETE EXAMPLES:
- If target is 'Sport e corsa' and text says 'Ci vediamo alle 18 per correre':
  Output: {{"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "I partecipanti si accordano per andare a correre."}}
- If target is 'Mutui e acquisto casa' and text says 'Ci vediamo alle 18 per correre':
  Output: {{"decision": "ABSENT", "evidence_ids": [], "rationale": "I messaggi parlano di corsa; non viene menzionato alcun mutuo o compravendita immobiliare."}}

Return ONLY a valid JSON object matching the schema."""

    user_msg = f"""{evidence_text}

Based on the forensic evidence data above, evaluate whether '{topic.label}' is PRESENT, ABSENT, or UNCERTAIN.
- If the conversation does NOT discuss '{topic.label}', decision MUST be 'ABSENT' with 'evidence_ids': [].
- If PRESENT, you MUST cite at least one exact alias ID (e.g. ['E005']) in 'evidence_ids'. If you cannot cite any evidence, you MUST return 'ABSENT'."""

    payload = {
        "model": "qwen2.5-7b-instruct",
        "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
        "temperature": 0.0,
        "max_tokens": 384,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": TOPIC_DETECTION_SCHEMA
            }
        }
    }
    req_http = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_http, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        return choice.get("message", {}).get("content")

print("--- TESTING ABSENT ---")
out_absent = run_test(q_absent)
print("ABSENT RAW:\n", out_absent)

print("--- TESTING PRESENT ---")
out_present = run_test(q_present)
print("PRESENT RAW:\n", out_present)
