import sys
sys.path.insert(0, ".")
from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.chunking import split_document_into_chunks
from ai.topics import TopicDiscoveryAnalyzer, generate_evidence_alias_maps, serialize_document_for_llm
from ai.lmstudio import LmStudioClient

doc = build_synthetic_document("doc_synth_test_3chunks")
chunks = split_document_into_chunks(doc, max_characters=100000, max_bundles=3, max_tokens=2500)
c1 = chunks[1]

id_to_alias, alias_to_id = generate_evidence_alias_maps(c1)
print("Aliases in c1:")
for k, v in alias_to_id.items():
    print(f"  {k} -> {v}")

user_content = serialize_document_for_llm(
    c1,
    evidence_id_map=id_to_alias,
)

print("\n--- USER CONTENT ---")
print(user_content)
