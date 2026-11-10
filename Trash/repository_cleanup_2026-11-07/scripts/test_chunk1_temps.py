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

user_content = serialize_document_for_llm(c1, evidence_id_map=id_to_alias)

client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=60.0)

for temp in [0.0, 0.1, 0.2, 0.3]:
    print(f"\n==================== TESTING TEMP {temp} ====================")
    valid_aliases_str = ", ".join(sorted(alias_to_id.keys()))
    sys_prompt = f"""TASK: OPEN TOPIC DISCOVERY
You are an expert conversation analyst.
Extract all distinct topics discussed in the messages.

The ONLY valid evidence_id values in this chunk are: {valid_aliases_str}.

Instructions:
1. Identify distinct topics discussed.
2. For each topic, output:
   - "label": title of topic in Italian
   - "short_description": 1-sentence description in Italian
   - "evidence_ids": list of 1 to 2 evidence_ids from [{valid_aliases_str}] supporting this topic.
3. IMPORTANT: Cite ONLY the exact value from the "evidence_id" field ({valid_aliases_str}). Do NOT cite "source_record_id" or numbers like 4, 5, 6.
4. Output JSON matching the schema.

Example:
{{"topics": [{{"label": "Argomento", "short_description": "Descrizione", "evidence_ids": ["E001"]}}]}}
"""
    user_prompt = f"{user_content}\n\nExtract topics. Valid evidence_ids: [{valid_aliases_str}]. Return JSON."
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        resp = client.chat_completion(
            messages=messages,
            model_id="qwen2.5-7b-instruct",
            temperature=temp,
            seed=42,
            max_tokens=512,
            schema=TOPIC_DISCOVERY_SCHEMA,
            timeout_seconds=60.0,
        )
        print("Response:", resp.content)
        validated = validate_topic_discovery_payload(
            raw_text=resp.content,
            valid_evidence_ids=valid_evidence_ids,
            max_topics=8,
            alias_map=alias_to_id,
        )
        print("VALIDATED SUCCESS:")
        for t in validated:
            print(f"  {t.label}: {t.evidence_ids}")
        break
    except Exception as e:
        print("FAILED with:", type(e), e)
