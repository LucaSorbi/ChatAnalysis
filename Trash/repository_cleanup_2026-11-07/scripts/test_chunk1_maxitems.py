import sys
sys.path.insert(0, ".")
import copy
from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.chunking import split_document_into_chunks
from ai.topics import TopicDiscoveryAnalyzer
from ai.lmstudio import LmStudioClient
from ai.structured import TOPIC_DISCOVERY_SCHEMA
from analysis.service import calculate_discovery_max_tokens

# Test modified schema with maxItems: 10
schema_with_max = copy.deepcopy(TOPIC_DISCOVERY_SCHEMA)
schema_with_max["properties"]["topics"]["items"]["properties"]["evidence_ids"]["maxItems"] = 10

doc = build_synthetic_document("doc_synth_test_3chunks")
chunks = split_document_into_chunks(doc, max_characters=100000, max_bundles=3, max_tokens=2500)
c1 = chunks[1]

client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=120.0)

class SchemaOverrideClient:
    def __init__(self, inner, schema):
        self.inner = inner
        self.schema = schema
    def complete_chat(self, *args, **kwargs):
        kwargs["schema"] = self.schema
        resp = self.inner.complete_chat(*args, **kwargs)
        print("--- LLM RESPONSE WITH maxItems: 10 ---")
        print(resp.content)
        return resp
    def __getattr__(self, name):
        return getattr(self.inner, name)

v_client = SchemaOverrideClient(client, schema_with_max)
analyzer = TopicDiscoveryAnalyzer(client=v_client, operational_mode=True)

chunk_tokens = calculate_discovery_max_tokens(
    max_topics=8,
    user_max_tokens=None,
    context_length=8192,
)

print(f"Testing with maxItems: 10...")
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
