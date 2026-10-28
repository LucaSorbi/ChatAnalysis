import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import LmStudioClient
from scripts.verify_live_discovery_provenance import create_synthetic_chunk_doc
from ai.topics import TopicDiscoveryAnalyzer

chunk_2_data = [
    ("Roberto", "Ho smontato l'orologio da tasca del 1920: lo scappamento a leva presenta incrostazioni di olio secco."),
    ("Sofia", "Hai già fatto il lavaggio ad ultrasuoni dei rubini e del bilanciere?"),
    ("Roberto", "Sì, e ho sostituito la molla motrice principale che aveva perso elasticità."),
    ("Sofia", "Attento quando lubrifichi con l'olio sintetico Moebius, ne basta una frazione di goccia sulle palette dell'ancora."),
    ("Roberto", "Regolazione completata al cronocomparatore: anticipo di soli 2 secondi al giorno."),
]

doc = create_synthetic_chunk_doc(2, "Orologeria", chunk_2_data)
client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=90.0)
analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

print("Starting Chunk 2 test...")
res = analyzer.discover_topics(document=doc, model_id="qwen2.5-7b-instruct", max_topics=3, max_tokens=600, temperature=0.0, seed=42)
print("Finished Chunk 2!")
print("Topics count:", len(res.topics))
for t in res.topics:
    print(" -", t.label, ":", t.short_description)
    print("   citations:", t.evidence_ids)
