import hashlib
from pathlib import Path
from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.checkpoint import compute_document_fingerprint

p = Path("test_data/synthetic_whatsapp_large_75kb.txt")
if p.exists():
    req = IngestionRequest(
        file_bytes=p.read_bytes(),
        filename=p.name,
        source_format=SourceFormat.WHATSAPP_EXPORT,
    )
    res = ingest_file_payload(req)
    print("Ingested documents:", len(res.documents))
    for doc_id, doc in res.documents.items():
        fp = compute_document_fingerprint(doc)
        print(f"Doc ID: {doc_id}")
        print(f"Doc Fingerprint: {fp}")
        print(f"Message count: {doc.message_count}")
        print(f"Evidence count: {doc.evidence_count}")
