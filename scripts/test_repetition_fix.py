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


def run():
    zip_path = r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip"
    with open(zip_path, "rb") as f:
        data = f.read()

    req = IngestionRequest(source_format=SourceFormat.WHATSAPP_EXPORT, filename="Chat WhatsApp con Gre.zip", file_bytes=data)
    doc = ingest_file_payload(req).documents["doc::whatsapp_export::04691438289a::cb5ef8378d12"]

    service = LocalTopicAnalysisService(base_url="http://127.0.0.1:1234", default_model="qwen2.5-7b-instruct")
    plan = service.create_analysis_plan(document=doc, analysis_type="discovery", max_topics=service.config.max_topics, context_length=service.context_length)
    chunks = list(plan.chunks)
    c2 = chunks[2]
    mid = len(c2.bundles) // 2
    sub0 = ConversationEvidenceDocument(document_id=f"{c2.document_id}::sub::0", bundles=c2.bundles[:mid], chat_id=c2.chat_id, source_name=c2.source_name)

    id_to_alias, alias_to_id = generate_evidence_alias_maps(sub0)
    user_content = serialize_document_for_llm(sub0, evidence_id_map=id_to_alias)

    sys_prompt = f"{SYSTEM_SECURITY_DIRECTIVE}\nTASK: OPEN TOPIC DISCOVERY\n" \
        "Analyze the forensic conversation evidence and discover the main topics.\n" \
        "Find between 1 and 3 topics. If only 1 or 2 topics genuinely exist in the text, return ONLY 1 or 2 topics.\n" \
        "DO NOT invent extra topics if no more distinct subjects exist in the text.\n" \
        "OUTPUT FORMAT: Output ONLY a valid JSON object matching the schema:\n" \
        '{\n  "topics": [\n    {"label": "string", "short_description": "string", "evidence_ids": ["E001"]}\n  ]\n}\n' \
        "Output raw JSON only starting with { and ending with }."

    payload = {
        "model": "qwen2.5-7b-instruct",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content + "\n\nOutput pure valid JSON matching the schema."}
        ],
        "max_tokens": 600,
        "temperature": 0.0,
    }
    req = urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req)
    raw_text = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
    print("Raw response:\n", raw_text, flush=True)

    topics = validate_topic_discovery_payload(
        raw_text=raw_text,
        valid_evidence_ids={s.evidence_id for s in sub0.all_evidence_sections},
        max_topics=4,
        alias_map=alias_to_id,
    )
    print("\n[VALIDATION SUCCESS] Discovered genuinely supported topics:", len(topics), flush=True)
    for t in topics:
        print(f" - {t.label}: {len(t.evidence_ids)} evidences -> {t.evidence_ids}", flush=True)


if __name__ == "__main__":
    run()
