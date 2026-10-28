import json
import time
import sys
from pathlib import Path

from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.lmstudio import LmStudioClient
from ai.topics import TopicDiscoveryAnalyzer

sys.stdout.reconfigure(encoding="utf-8")
from scripts.export_discovery_26of26 import resolve_input_archive_path
p = resolve_input_archive_path()
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

client = LmStudioClient(base_url="http://127.0.0.1:1234")
analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

for test_idx in (2, 5):
    chunk = plan.chunks[test_idx]
    print(f"\n=======================================================")
    print(f"TEST CHUNK {test_idx}: {len(chunk.bundles)} messaggi, {len(chunk.all_evidence_sections)} evidenze.")
    print(f"=======================================================")
    t0 = time.perf_counter()
    try:
        disc_res = analyzer.discover_topics(
            document=chunk,
            model_id="qwen2.5-7b-instruct",
            temperature=0.0,
            seed=42,
            timeout_seconds=240.0,
            max_topics=8,
            max_tokens=1200,
            operational_mode=True,
            context_length=8192,
        )
        elapsed = time.perf_counter() - t0
        print(f"[OK] Chunk {test_idx} completato in {elapsed:.2f}s!")
        print(f"     Topics estratti: {len(disc_res.topics)}")
        for t in disc_res.topics:
            print(f"     * {t.label}: {len(t.evidence_ids)} evidenze")
        meta = disc_res.metadata
        print(f"     Tokens: completion={meta.get('completion_tokens')}, finish={meta.get('finish_reason')}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[ERR] Chunk {test_idx} fallito in {elapsed:.2f}s: {e}")
