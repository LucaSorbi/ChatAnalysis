import sys
from pathlib import Path
from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from analysis.checkpoint import compute_document_fingerprint, compute_run_config_hash

sys.stdout.reconfigure(encoding="utf-8")
p = Path(r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip")
req = IngestionRequest(
    file_bytes=p.read_bytes(),
    filename=p.name,
    source_format=SourceFormat.WHATSAPP_EXPORT,
)
res = ingest_file_payload(req)
doc = list(res.documents.values())[0]

service = LocalTopicAnalysisService(
    base_url="http://127.0.0.1:1234",
    default_model="qwen2.5-7b-instruct",
)

plan = service.create_analysis_plan(
    document=doc,
    analysis_type="discovery",
    max_topics=8,
    user_max_tokens=None,
    context_length=8192,
)

print(f"Total planned chunks: {plan.planned_chunks_count}")
print(f"Number of chunks in plan: {len(plan.chunks)}")
doc_fp = compute_document_fingerprint(doc)
cfg_fp = compute_run_config_hash(
    model_id="qwen2.5-7b-instruct",
    prompt_version="topic_discovery_operational_v2",
    config=service.config,
    chunks=plan.chunks,
)
print(f"Document Fingerprint: {doc_fp}")
print(f"Config Fingerprint:   {cfg_fp}")
print(f"Expected in Checkpoint: 089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a")
print(f"Matches Checkpoint Config Fingerprint: {cfg_fp == '089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a'}")

for idx, c in enumerate(plan.chunks):
    eids = [s.evidence_id for s in c.all_evidence_sections]
    print(f"Chunk #{idx:2d}: bundles={len(c.bundles):2d}, evidences={len(eids):2d}, range=[{eids[0]} .. {eids[-1]}]")
