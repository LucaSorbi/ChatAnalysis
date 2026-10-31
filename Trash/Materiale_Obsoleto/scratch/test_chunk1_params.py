import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import LmStudioClient
from scripts.verify_live_discovery_provenance import create_synthetic_chunk_doc
from ai.topics import TopicDiscoveryAnalyzer

chunk_1_data = [
    ("Paolo", "Questa domenica organizziamo una sessione di corsa lunga di trenta chilometri."),
    ("Elena", "Ottimo, per l'allenamento di ripetute in pista che intervallo di recupero adottiamo?"),
    ("Paolo", "Tre minuti di camminata attiva. Dobbiamo testare anche l'integrazione energetica a rilascio lento."),
    ("Elena", "Perfetto, un piano alimentare ricco di carboidrati prima della prova sara' decisivo."),
    ("Paolo", "Preparero' le borracce con i sali minerali per garantire una corretta idratazione."),
]

doc = create_synthetic_chunk_doc(1, "Maratona", chunk_1_data)
client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=90.0)
analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)

print("Starting clean Chunk 1 test...")
res = analyzer.discover_topics(document=doc, model_id="qwen2.5-7b-instruct", max_topics=3, max_tokens=600, temperature=0.0, seed=42)
print("Finished Chunk 1!")
print("Topics count:", len(res.topics))
for t in res.topics:
    print(" -", t.label, ":", t.short_description)
    print("   citations:", t.evidence_ids)
