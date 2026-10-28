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
c1_bundles = doc.bundles[3:6]
c1_doc = ConversationEvidenceDocument("chunk1", bundles=c1_bundles)

client = LmStudioClient("http://127.0.0.1:1234")
analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

try:
    res = analyzer.discover_topics(c1_doc, max_topics=6, timeout_seconds=180.0)
    print("SUCCESS on c1:", len(res.topics))
    for t in res.topics:
        print("  -", t.label, t.evidence_ids)
except Exception as e:
    import traceback
    print("FATAL ERROR ON C1:", type(e).__name__, e)
    traceback.print_exc()
