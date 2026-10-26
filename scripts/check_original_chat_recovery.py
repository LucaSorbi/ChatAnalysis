import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat
from analysis.service import LocalTopicAnalysisService
from ai.lmstudio import LmStudioClient
from analysis.checkpoint import compute_document_fingerprint, compute_run_config_hash

zip_path = r'C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip'
with open(zip_path, 'rb') as f:
    data = f.read()

req = IngestionRequest(source_format=SourceFormat.WHATSAPP_EXPORT, filename='Chat WhatsApp con Gre.zip', file_bytes=data)
res = ingest_file_payload(req)
doc = res.documents[res.summary.selected_document_id]

doc_fp = compute_document_fingerprint(doc)
print("Document ID:", doc.document_id)
print("Document Fingerprint:", doc_fp)
assert doc_fp == "e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478"
print("[OK] Document fingerprint matches exactly!")

service = LocalTopicAnalysisService(
    base_url='http://127.0.0.1:1234',
    default_model='qwen2.5-7b-instruct',
    checkpoint_dir='.checkpoints_live_recovery_work',
    enable_checkpointing=True,
)
client = LmStudioClient()
target_client, resolved_model = service._prepare_client_and_model(client, 'qwen2.5-7b-instruct')
plan = service.create_analysis_plan(
    document=doc,
    analysis_type='discovery',
    max_topics=service.config.max_topics,
    context_length=service.context_length,
)
chunks = list(plan.chunks)
print(f"Total planned chunks: {len(chunks)}")

chk = service.checkpoint_store.load_and_validate_checkpoint(
    document=doc,
    config_fingerprint="089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a",
    chunks=chunks,
    expected_analysis_type='discovery',
)
print("Checkpoint loaded:", chk is not None)
if chk:
    print("Validated chunk indices:", sorted(chk.completed_chunk_indices))
    print("Serialized results count:", len(chk.serialized_results))
