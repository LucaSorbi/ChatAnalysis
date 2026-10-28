import sys
sys.path.insert(0, ".")
from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.chunking import split_document_into_chunks
from ai.topics import generate_evidence_alias_maps, serialize_document_for_llm
from ai.lmstudio import LmStudioClient
from ai.structured import TOPIC_DISCOVERY_SCHEMA, validate_topic_discovery_payload

doc = build_synthetic_document("doc_synth_test_3chunks")
chunks = split_document_into_chunks(doc, max_characters=100000, max_bundles=3, max_tokens=2500)
c1 = chunks[1]

id_to_alias, alias_to_id = generate_evidence_alias_maps(c1)
valid_evidence_ids = {s.evidence_id for s in c1.all_evidence_sections}
valid_aliases_str = ", ".join(f"'{k}'" for k in sorted(alias_to_id.keys()))

user_content = serialize_document_for_llm(
    c1,
    evidence_id_map=id_to_alias,
)

system_prompt = f"""TASK: OPEN TOPIC DISCOVERY (Version: topic_discovery_operational_v2)
You are an expert conversation analyst.
Analyze the provided messages and extract all distinct topics, subjects, and activities discussed.

AVAILABLE EVIDENCE ALIASES IN THIS BLOCK ({len(alias_to_id)} total): [{valid_aliases_str}]

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian (e.g. 'Organizzazione viaggio', 'Preparazione esami', 'Attivita sportiva')
   - "short_description": description of what is discussed in Italian
   - "evidence_ids": list of 1 to 3 exact evidence aliases from [{valid_aliases_str}] that support this topic.
3. CRITICAL: Every evidence_id cited MUST be an exact alias chosen ONLY from [{valid_aliases_str}]. Do NOT cite numbers from source_record_id or message_id. Do NOT invent any other ID.
4. Output a JSON object with key "topics" containing the list of discovered topics (at most 8 elements).

MULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Analyze all languages and output labels/descriptions in Italian.
"""

user_message_content = (
    f"{user_content}\n\n"
    f"Based on the conversation evidence above, extract up to 8 main topics and activities discussed.\n"
    f"Available evidence_ids: [{valid_aliases_str}].\n"
    "Return ONLY a valid JSON object matching the schema."
)

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_message_content},
]

client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=60.0)

print("Sending request to LM Studio with explicit valid alias instructions...")
resp = client.chat_completion(
    messages=messages,
    model_id="qwen2.5-7b-instruct",
    temperature=0.0,
    seed=42,
    max_tokens=1024,
    schema=TOPIC_DISCOVERY_SCHEMA,
)

print("\n--- RAW RESPONSE ---")
print(resp.content)

validated = validate_topic_discovery_payload(
    raw_text=resp.content,
    valid_evidence_ids=valid_evidence_ids,
    max_topics=8,
    alias_map=alias_to_id,
)

print("\n--- VALIDATED TOPICS ---")
for t in validated:
    print(f"Topic: '{t.label}' | eids: {t.evidence_ids}")
print(f"TOTAL VALIDATED TOPICS: {len(validated)}")
