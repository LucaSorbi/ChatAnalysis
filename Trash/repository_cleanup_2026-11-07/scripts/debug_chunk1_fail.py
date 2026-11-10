import sys
sys.path.insert(0, ".")
from pathlib import Path
from ai.topics import TopicDiscoveryAnalyzer
from ai.lmstudio import LmStudioClient
from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.chunking import split_document_into_chunks

doc = build_synthetic_document("doc_synth_test_3chunks")
chunks = split_document_into_chunks(doc, max_characters=100000, max_bundles=3, max_tokens=2500)

print(f"Total chunks: {len(chunks)}")
c1 = chunks[1]
print("Chunk 1 doc id:", c1.document_id)
print("Chunk 1 valid evidence IDs:")
for s in c1.all_evidence_sections:
    print(" ", s.evidence_id)

client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=120.0)
analyzer = TopicDiscoveryAnalyzer(client=client, operational_mode=True)

try:
    res = analyzer.discover_topics(c1, model_id="qwen2.5-7b-instruct")
    print("Success on c1:")
    for t in res.topics:
        print(" Topic:", t.label, "eids:", t.evidence_ids)
except Exception as e:
    print("Caught exception on c1:", type(e), e)
    if hasattr(e, "response") and e.response:
        print("Raw response:", getattr(e.response, "content", None))
