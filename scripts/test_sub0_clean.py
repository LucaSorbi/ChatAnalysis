import json
import urllib.request
from pathlib import Path

from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.topics import generate_evidence_alias_maps, serialize_document_for_llm, validate_topic_discovery_payload, TOPIC_DISCOVERY_SCHEMA
from ai.models import ConversationEvidenceDocument

zip_path = Path(r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip")
req = IngestionRequest(source_format=SourceFormat.WHATSAPP_EXPORT, filename="Chat WhatsApp con Gre.zip", file_bytes=zip_path.read_bytes())
doc = ingest_file_payload(req).documents["doc::whatsapp_export::04691438289a::cb5ef8378d12"]
service = LocalTopicAnalysisService(base_url="http://127.0.0.1:1234")
plan = service.create_analysis_plan(document=doc, analysis_type="discovery")
c2 = list(plan.chunks)[2]
mid = len(c2.bundles) // 2
sub0 = ConversationEvidenceDocument(document_id=f"{c2.document_id}::sub::0", bundles=c2.bundles[:mid])
id_to_alias, alias_to_id = generate_evidence_alias_maps(sub0)
uc = serialize_document_for_llm(sub0, evidence_id_map=id_to_alias)

user_msg = (
    f"{uc}\n\n"
    "Based on the forensic evidence data above, discover up to 4 main distinct topics discussed in this conversation.\n"
    "Return ONLY a valid JSON object matching this schema:\n"
    '{"topics": [{"label": "string", "short_description": "string", "evidence_ids": ["E001"]}]}'
)

sys_msg = (
    "You are a forensic text analysis expert. Analyze the evidence objectively.\n"
    "Respond ONLY with a valid JSON object starting with { and ending with }.\n"
    "Never generate text outside the JSON object."
)

payload = {
    "model": "qwen2.5-7b-instruct",
    "messages": [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ],
    "temperature": 0.0,
    "max_tokens": 800,
}

req_http = urllib.request.Request(
    "http://127.0.0.1:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(req_http, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        print("MODEL CONTENT:\n", content)
        topics = validate_topic_discovery_payload(
            raw_text=content,
            valid_evidence_ids={s.evidence_id for s in sub0.all_evidence_sections},
            max_topics=4,
            alias_map=alias_to_id,
        )
        print("\nSUCCESSFULLY VALIDATED TOPICS:", len(topics))
        for t in topics:
            print(f" - {t.label}: {t.short_description} | evidence: {t.evidence_ids}")
except Exception as e:
    import traceback
    traceback.print_exc()
