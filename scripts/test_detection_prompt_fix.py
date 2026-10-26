from pathlib import Path
from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.models import TopicQuery, TopicDecision
from ai.lmstudio import LmStudioClient
from ai.serializer import serialize_document_for_llm, generate_evidence_alias_maps
from ai.structured import TOPIC_DETECTION_SCHEMA

zip_path = Path(r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip")
req = IngestionRequest(file_bytes=zip_path.read_bytes(), filename=zip_path.name, source_format=SourceFormat.WHATSAPP_EXPORT)
doc = list(ingest_file_payload(req).documents.values())[0]

service = LocalTopicAnalysisService()
q_absent = TopicQuery(
    topic_id="q_mutuo",
    label="Mutuo ipotecario e compravendita immobiliare",
    description="Richiesta di mutuo ipotecario in banca, stipula di rogito notarile o acquisto di appartamenti.",
)
plan = service.create_analysis_plan(document=doc, analysis_type="detection", topic_query=q_absent)
chk0 = plan.chunks[0]

id_to_alias, alias_to_id = generate_evidence_alias_maps(chk0)
evidence_text = serialize_document_for_llm(chk0, evidence_id_map=id_to_alias)

client = LmStudioClient()

prompt_test = [
    {
        "role": "system",
        "content": """You are an objective forensic evaluator. Determine if the target topic is discussed in the evidence.
TOPIC LABEL: Mutuo ipotecario e compravendita immobiliare
DESCRIPTION: Richiesta di mutuo ipotecario in banca, stipula di rogito notarile o acquisto di appartamenti.

OUTPUT RULES:
1. If the topic is genuinely discussed in the text, return decision 'PRESENT' and cite exact evidence aliases (e.g. ['E001']).
2. If the topic is NOT discussed in the evidence, return decision 'ABSENT', 'evidence_ids': [], and a short rationale in Italian stating it is absent.
3. If ambiguous or fragmentary, return decision 'UNCERTAIN'.
4. DO NOT invent or hallucinate topics, loans, or numbers that are not in the text. Generic tags like <Media omessi> are NOT loans.
5. Provide a short objective rationale in Italian (at least one sentence).""",
    },
    {
        "role": "user",
        "content": f"""{evidence_text}

Based on the forensic evidence data above, evaluate whether 'Mutuo ipotecario e compravendita immobiliare' is PRESENT, ABSENT, or UNCERTAIN.
Return ONLY valid JSON matching the schema:
- If PRESENT: {{"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "Spiegazione..."}}
- If ABSENT: {{"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessun messaggio tratta di mutui o compravendita di immobili."}}""",
    },
]

res = client.chat_completion(
    messages=prompt_test,
    schema=TOPIC_DETECTION_SCHEMA,
    model_id="qwen2.5-7b-instruct",
    temperature=0.0,
    max_tokens=256,
    timeout_seconds=90.0,
)
print("Response content:")
print(res.content)
