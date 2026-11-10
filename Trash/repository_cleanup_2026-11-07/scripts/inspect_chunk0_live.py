#!/usr/bin/env python3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.test_live_operational_zero_topics_fix import build_synthetic_document
from ai.lmstudio import LmStudioClient
from ai.topics import TopicDiscoveryAnalyzer
from ai.models import ConversationEvidenceDocument

print("[1] Building synthetic doc...", flush=True)
doc = build_synthetic_document("doc_synth_test_3chunks")
c0_bundles = doc.bundles[:3]
c0_doc = ConversationEvidenceDocument("chunk0", bundles=c0_bundles)

print("[2] Initializing LM Studio client...", flush=True)
client = LmStudioClient("http://127.0.0.1:1234", default_timeout_seconds=60.0)

orig_chat = client.chat_completion
def logged_chat(messages, **kwargs):
    print(f"\n--- CALLING CHAT COMPLETION (model={kwargs.get('model_id')}) ---", flush=True)
    resp = orig_chat(messages, **kwargs)
    print(f"--- GOT RESPONSE (tokens={getattr(resp, 'completion_tokens', None)}, finish={getattr(resp, 'raw_response', {}).get('finish_reason')}) ---", flush=True)
    print(f"Content: {resp.content}", flush=True)
    return resp

client.chat_completion = logged_chat

analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

print("[3] Running discover_topics on chunk 0...", flush=True)
try:
    res = analyzer.discover_topics(
        c0_doc,
        max_topics=6,
        timeout_seconds=60.0,
        on_retry_callback=lambda att, r: print(f"[RETRY CALLBACK] Attempt {att}, reason: {r}", flush=True)
    )
    print(f"\n[4] SUCCESS: Discovered {len(res.topics)} topics!", flush=True)
    for idx, t in enumerate(res.topics, 1):
        print(f"    Topic {idx}: {t.label} (evidences: {t.evidence_ids})", flush=True)
except Exception as e:
    import traceback
    print(f"\n[-] EXCEPTION CAUGHT: {type(e).__name__}: {e}", flush=True)
    traceback.print_exc()
