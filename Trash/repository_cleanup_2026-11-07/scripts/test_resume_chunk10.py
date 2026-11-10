"""
scripts/test_resume_chunk10.py
------------------------------
Test recovering the 25 completed chunks from SQLite in 0 seconds,
and processing only Chunk 10.
"""
import sys
import os
import time
from pathlib import Path

# Ensure PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.lmstudio import LmStudioClient
from analysis.progress import AnalysisProgressEvent

def progress_callback(event: AnalysisProgressEvent):
    pct = round(event.percentage, 1)
    print(f"[{event.phase}] Chunk {event.current_chunk_index}/{event.total_chunks} "
          f"({pct}%) | ReqSent: {event.requests_sent} | ReqOK: {event.requests_successful} | "
          f"Err: {event.requests_failed} | Retries: {event.retries_count} | {event.status_message}")
    sys.stdout.flush()

def main():
    from scripts.export_discovery_26of26 import resolve_input_archive_path
    zip_path = resolve_input_archive_path()
    req = IngestionRequest(
        file_bytes=zip_path.read_bytes(),
        filename=zip_path.name,
        source_format=SourceFormat.WHATSAPP_EXPORT,
    )
    res = ingest_file_payload(req)
    doc = list(res.documents.values())[0]

    client = LmStudioClient(base_url="http://127.0.0.1:1234")
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model="qwen2.5-7b-instruct",
        enable_checkpointing=True,
        checkpoint_dir=".checkpoints",
    )

    t0 = time.perf_counter()
    try:
        disc_res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
            temperature=0.0,
            seed=42,
            progress_callback=progress_callback,
        )
        print(f"\n[SUCCESS] Discovery finished in {time.perf_counter() - t0:.1f}s!")
        print(f"Total topics: {len(disc_res.topics)}")
        for idx, t in enumerate(disc_res.topics, 1):
            print(f"  Topic #{idx}: '{t.label}' ({len(t.evidence_ids)} evidenze)")
    except Exception as e:
        print(f"\n[PARTIAL / INTERRUPTED] {type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
