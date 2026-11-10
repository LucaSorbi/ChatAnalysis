import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.models import ConversationEvidenceDocument
from ai.topics import (
    SYSTEM_SECURITY_DIRECTIVE,
    generate_evidence_alias_maps,
    serialize_document_for_llm,
    validate_topic_discovery_payload,
)
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat
from scripts.export_discovery_26of26 import resolve_input_archive_path
zip_path = resolve_input_archive_path()
with open(zip_path, "rb") as f:
    data = f.read()

req = IngestionRequest(source_format=SourceFormat.WHATSAPP_EXPORT, filename=zip_path.name, file_bytes=data)
doc = list(ingest_file_payload(req).documents.values())[0]

service = LocalTopicAnalysisService(base_url="http://127.0.0.1:1234", default_model="qwen2.5-7b-instruct")
plan = service.create_analysis_plan(document=doc, analysis_type="discovery", max_topics=service.config.max_topics, context_length=service.context_length)
chunks = list(plan.chunks)
c2 = chunks[2]
mid = len(c2.bundles) // 2
sub0 = ConversationEvidenceDocument(document_id=f"{c2.document_id}::sub::0", bundles=c2.bundles[:mid], chat_id=c2.chat_id, source_name=c2.source_name)

id_to_alias, alias_to_id = generate_evidence_alias_maps(sub0)
user_content = serialize_document_for_llm(sub0, evidence_id_map=id_to_alias)

sys_prompt = f"{SYSTEM_SECURITY_DIRECTIVE}\nTASK: OPEN TOPIC DISCOVERY\n" \
    "Analyze the forensic conversation evidence and discover the main topics. Find up to 4 distinct topics.\n" \
    "OUTPUT FORMAT: Output ONLY a valid JSON object starting with { and ending with }.\n" \
    'Schema: {"topics": [{"label": "string", "short_description": "string", "evidence_ids": ["E001"]}]}\n' \
    "Output raw JSON only without markdown code blocks."

payload = {
    "model": "qwen2.5-7b-instruct",
    "messages": [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content + "\n\nReturn pure JSON matching the schema."}
    ],
    "max_tokens": 600,
    "temperature": 0.0,
}
req = urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
try:
    resp = urllib.request.urlopen(req)
    res_body = json.loads(resp.read().decode())
    raw_text = res_body["choices"][0]["message"]["content"]
    print("Raw response:\n", raw_text)
    topics = validate_topic_discovery_payload(
        raw_text=raw_text,
        valid_evidence_ids={s.evidence_id for s in sub0.all_evidence_sections},
        max_topics=4,
        alias_map=alias_to_id,
    )
    print("VALIDATION PASSED! Valid topics discovered:", len(topics))
    for t in topics:
        print(f" - {t.label}: {len(t.evidence_ids)} evidences ({t.evidence_ids})")
except Exception as e:
    import traceback
    traceback.print_exc()
