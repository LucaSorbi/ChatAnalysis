import sys
sys.path.insert(0, ".")
from ai.topics import TopicDiscoveryAnalyzer
from ai.lmstudio import LmStudioClient
from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.chunking import split_document_into_chunks
from analysis.service import calculate_discovery_max_tokens

doc = build_synthetic_document("doc_synth_test_3chunks")
chunks = split_document_into_chunks(doc, max_characters=100000, max_bundles=3, max_tokens=2500)
c1 = chunks[1]

client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=120.0)

class VerboseClient:
    def __init__(self, inner):
        self.inner = inner
    def complete_chat(self, *args, **kwargs):
        print("\n--- LLM REQUEST ---")
        messages = kwargs.get("messages", [])
        for m in messages:
            print(f"[{m['role']}]: {m['content'][:150]}...")
        resp = self.inner.complete_chat(*args, **kwargs)
        print("--- LLM RESPONSE ---")
        print(resp.content)
        return resp
    def __getattr__(self, name):
        return getattr(self.inner, name)

v_client = VerboseClient(client)
analyzer = TopicDiscoveryAnalyzer(client=v_client, operational_mode=True)

chunk_tokens = calculate_discovery_max_tokens(
    max_topics=8,
    user_max_tokens=None,
    context_length=8192,
)

print(f"Running discover_topics with max_topics=8, max_tokens={chunk_tokens}...")
try:
    res = analyzer.discover_topics(
        document=c1,
        model_id="qwen2.5-7b-instruct",
        max_topics=8,
        max_tokens=chunk_tokens,
        operational_mode=True,
    )
    print("\nSUCCESS:")
    for t in res.topics:
        print(" Topic:", t.label, "eids:", t.evidence_ids)
except Exception as e:
    print("\nFAILED:", type(e), e)
    if hasattr(e, "response") and e.response:
        print("Response content:", getattr(e.response, "content", None))
