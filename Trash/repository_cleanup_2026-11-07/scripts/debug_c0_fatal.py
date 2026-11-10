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

doc = build_synthetic_document("doc_synth_test_3chunks")
c0_bundles = doc.bundles[:3]
c0_doc = ConversationEvidenceDocument("chunk0", bundles=c0_bundles)

client = LmStudioClient("http://127.0.0.1:1234")
analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

try:
    res = analyzer.discover_topics(c0_doc, max_topics=6, timeout_seconds=180.0)
    print("SUCCESS on c0:", len(res.topics))
    for t in res.topics:
        print("  -", t.label, t.evidence_ids)
except Exception as e:
    import traceback
    print("FATAL ERROR ON C0:", type(e).__name__, e)
    traceback.print_exc()
