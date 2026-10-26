"""
scripts/test_chunk2_subchunk_live.py
------------------------------------
Test mirato di subchunk splitting su Chunk 2 della conversazione reale con LM Studio.
Dimostra che la suddivisione controllata in 2 subchunk supera definitivamente il limite
di token e produce 2 sotto-blocchi validi salvati nel database SQLite.
"""
import os
import sys
import time
from pathlib import Path

os.environ["CHATANALYSIS_CHECKPOINT_DIR"] = ".checkpoints_live_recovery_work"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.lmstudio import LmStudioClient
from analysis.checkpoint import compute_document_fingerprint, compute_run_config_hash
from analysis.models import OperationalAnalysisConfig
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


def main():
    print("=== TEST LIVE SUBCHUNK SPLITTING SU CHUNK 2 ===", flush=True)
    zip_path = r"C:\Users\Luca\AppData\Local\Temp\session_media_l7yparo7\Chat WhatsApp con Gre.zip"
    with open(zip_path, "rb") as f:
        data = f.read()

    req = IngestionRequest(source_format=SourceFormat.WHATSAPP_EXPORT, filename="Chat WhatsApp con Gre.zip", file_bytes=data)
    doc = ingest_file_payload(req).documents["doc::whatsapp_export::04691438289a::cb5ef8378d12"]

    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model="qwen2.5-7b-instruct",
        checkpoint_dir=".checkpoints_live_recovery_work",
        enable_checkpointing=True,
    )
    client = LmStudioClient()
    plan = service.create_analysis_plan(document=doc, analysis_type="discovery", max_topics=service.config.max_topics, context_length=service.context_length)
    chunks = list(plan.chunks)
    c2 = chunks[2]
    print(f"Chunk 2 Document ID: {c2.document_id}", flush=True)
    print(f"Chunk 2 bundles: {len(c2.bundles)}, sezioni evidenza: {len(c2.all_evidence_sections)}", flush=True)

    # Inizializza tracker
    from ai.topics import TopicDiscoveryAnalyzer
    from analysis.service import AnalysisProgressTracker
    analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)
    tracker = AnalysisProgressTracker(total_chunks=1)

    doc_fp = compute_document_fingerprint(doc)
    cfg_fp = "089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a"

    print("\nEsecuzione _process_subchunks_discovery su Chunk 2...", flush=True)
    t0 = time.perf_counter()
    sub_outs, sub_res = service._process_subchunks_discovery(
        chunk=c2,
        analyzer=analyzer,
        resolved_model="qwen2.5-7b-instruct",
        eff_temp=0.0,
        eff_seed=42,
        chunk_target_topics=4,
        tracker=tracker,
        chunk_idx_1based=3,
        chk_store=service.checkpoint_store,
        doc_fp=doc_fp,
        cfg_fp=cfg_fp,
        parent_chunk_index=2,
        parent_document_id=doc.document_id,
        total_planned_chunks=26,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    print(f"\nSubchunks completati in {elapsed}s!", flush=True)
    print(f"Subchunk outcomes count: {len(sub_outs)}", flush=True)
    for i, so in enumerate(sub_outs):
        print(f" - Subchunk {i}: status={so.status}, topics={len(so.result.topics) if so.result else 0}, evidence_ids={len(so.evidence_ids)}", flush=True)
        if so.diagnostic:
            print(f"   Diagnostic: duration={so.diagnostic.duration_seconds}s, finish_reason={so.diagnostic.finish_reason}, tokens={so.diagnostic.completion_tokens}", flush=True)

    # Verifica persistenza in SQLite
    import sqlite3
    conn = sqlite3.connect(".checkpoints_live_recovery_work/checkpoints.db")
    subs_saved = conn.execute("SELECT subchunk_index, LENGTH(serialized_result) FROM checkpoint_subchunks WHERE parent_chunk_index=2;").fetchall()
    print(f"Subchunks persistiti in SQLite: {subs_saved}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
