"""
scripts/execute_clean_real_discovery.py
---------------------------------------
Executes clean real Topic Discovery on the original conversation from 0/26.
Requirements:
- 26 chunks planned.
- Immediate saving of every validated chunk to SQLite checkpoints.db.
- Strict evidence verification (no invalid or hallucinated evidence_ids).
- Detailed diagnostics of each chunk.
- Resume capability without re-running successful chunks.
- Subchunk splitting if truncation or overflow occurs.
- Safe logging without leaking private message text.
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
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint, compute_run_config_hash
from analysis.progress import AnalysisProgressEvent

def progress_callback(event: AnalysisProgressEvent):
    pct = round(event.percentage, 1)
    print(f"[{event.phase}] Chunk {event.current_chunk_index}/{event.total_chunks} "
          f"({pct}%) | ReqSent: {event.requests_sent} | ReqOK: {event.requests_successful} | "
          f"Err: {event.requests_failed} | Retries: {event.retries_count} | {event.status_message}")
    sys.stdout.flush()

def main():
    print("=================================================================")
    print("   CHATANALYSIS — NUOVA TOPIC DISCOVERY REALE (CLEAN 0/26)       ")
    print("=================================================================")
    
    zip_path = Path(r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip")
    if not zip_path.exists():
        print(f"[FATAL] Original chat archive not found at: {zip_path}")
        sys.exit(1)

    print(f"Loading document from: {zip_path.name} ({zip_path.stat().st_size} bytes)...")
    req = IngestionRequest(
        file_bytes=zip_path.read_bytes(),
        filename=zip_path.name,
        source_format=SourceFormat.WHATSAPP_EXPORT,
    )
    res = ingest_file_payload(req)
    doc = list(res.documents.values())[0]
    print(f"Document loaded: '{doc.document_id}' | {len(doc.bundles)} message bundles.")

    client = LmStudioClient(base_url="http://127.0.0.1:1234")
    if not client.is_available():
        print("[FATAL] LM Studio not available on 127.0.0.1:1234")
        sys.exit(1)

    model_name = "qwen2.5-7b-instruct"
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=model_name,
        enable_checkpointing=True,
        checkpoint_dir=".checkpoints",
    )

    doc_fp = compute_document_fingerprint(doc)
    print(f"Document Fingerprint: {doc_fp}")
    
    # Check existing state in SQLite
    chk_store = AnalysisCheckpointStore(checkpoint_dir=".checkpoints")
    existing_chk = chk_store.load_checkpoint(doc_fp, "bfb4295c25abe73e4a0e19dc6036c1499e218ac1439214660de6c0ad62ce1ec5")
    existing_count = len(existing_chk.completed_chunk_indices) if existing_chk else 0
    print(f"Initial Checkpoints in SQLite: {existing_count} completed chunks.")

    t_start = time.perf_counter()
    try:
        discovery_res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name=model_name,
            max_topics=8,
            temperature=0.0,
            seed=42,
            progress_callback=progress_callback,
        )
        total_time = round(time.perf_counter() - t_start, 2)
        print("\n=================================================================")
        print(f"  TOPIC DISCOVERY RUN COMPLETED IN {total_time}s")
        print("=================================================================")
        print(f"Total Discovered Topics: {len(discovery_res.topics)}")
        for idx, t in enumerate(discovery_res.topics, 1):
            print(f"  Topic #{idx:02d}: '{t.label}' ({len(t.evidence_ids)} evidenze)")
            print(f"    Descrizione: {t.short_description[:80]}...")
            
    except Exception as e:
        total_time = round(time.perf_counter() - t_start, 2)
        print(f"\n[PARTIAL / INTERRUPTED] Run halted after {total_time}s with error: {type(e).__name__}: {e}")

    # Final tally of checkpoints and diagnostics
    final_chk = chk_store.load_checkpoint(doc_fp, "bfb4295c25abe73e4a0e19dc6036c1499e218ac1439214660de6c0ad62ce1ec5")
    completed_chunks = len(final_chk.completed_chunk_indices) if final_chk else 0
    final_diags = chk_store.get_chunk_diagnostics(doc_fp, "bfb4295c25abe73e4a0e19dc6036c1499e218ac1439214660de6c0ad62ce1ec5")
    print("\n=== FINAL CHECKPOINT STATUS ===")
    print(f"Completed Chunks: {completed_chunks}/26")
    print(f"Diagnostic Records: {len(final_diags)}")
    successes = sum(1 for d in final_diags if d.get("status") in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"))
    failures = sum(1 for d in final_diags if d.get("status") == "FAILED")
    print(f"Summary: {successes} SUCCESS / RECOVERED, {failures} FAILED.")
    
    for d in sorted(final_diags, key=lambda x: int(x.get("chunk_index", 0))):
        c_idx = d.get("chunk_index")
        st = d.get("status")
        cat = d.get("diagnostic_category") or "NONE"
        dur = d.get("duration_seconds", 0)
        tok = d.get("completion_tokens")
        print(f"  Chunk {int(c_idx):02d}: {st:10s} | {dur:6.1f}s | tok={tok} | cat={cat}")

if __name__ == "__main__":
    main()
