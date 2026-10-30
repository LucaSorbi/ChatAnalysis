"""
scripts/preview_discovery_progress_panel.py
-------------------------------------------
Anteprima LOCALE del pannello di avanzamento della Topic Discovery su un documento SINTETICO breve.

- Nessun LM Studio, nessuna rete, nessun dato reale: il "modello" è un client finto che restituisce topic fissi.
- Usa il vero servizio (`run_topic_discovery`) e la vera callback Streamlit (`ui.presentation`), con un checkpoint
  in una cartella temporanea NUOVA (mai quella dell'app).
- Serve per ispezionare a occhio (o con uno screenshot locale) il pannello e per l'AppTest reale su Windows.

Avvio (Windows, dalla radice del progetto, ambiente con Streamlit installato):
    streamlit run scripts\\preview_discovery_progress_panel.py
Ritardo per messaggio: variabile d'ambiente CHATANALYSIS_PREVIEW_DELAY (secondi, default 0.3; 0 = senza attese).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from ai.backend import AiStructuredOutputError, BaseLocalLlmClient, LlmCompletionResponse  # noqa: E402
from analysis.checkpoint import AnalysisCheckpointStore  # noqa: E402
from analysis.service import LocalTopicAnalysisService  # noqa: E402
from tests.unit.test_detection_microchunk_checkpoint_resume import _doc  # noqa: E402  (documento sintetico)
from ui.presentation import create_streamlit_progress_callback  # noqa: E402

MODEL = "qwen2.5-7b-instruct"
SCENARI = {
    "Successo (3 microchunk)": dict(n=24, fail_last=False, empty=False),
    "Parziale (un microchunk non validato)": dict(n=24, fail_last=True, empty=False),
    "Zero topic con 12 evidenze (non verificato)": dict(n=12, fail_last=False, empty=True),
    "Zero topic con 6 evidenze (conversazione breve)": dict(n=6, fail_last=False, empty=True),
}


class _SyntheticClient(BaseLocalLlmClient):
    def __init__(self, fail_last: bool, empty: bool, last_id: int, delay: float) -> None:
        super().__init__()
        self.model_id, self.fail_last, self.empty, self.last_id, self.delay = MODEL, fail_last, empty, last_id, delay
        self.request_listener = None

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return (MODEL,)

    def chat_completion(self, messages, model_id=None, schema=None, temperature=0.0, seed=42, timeout_seconds=60.0, max_tokens=None):
        ln = self.request_listener
        if ln:
            ln.record_request_sent()
        time.sleep(self.delay)
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        if self.fail_last and f"msg_synthetic_{self.last_id}" in user.replace(" ", ""):
            if ln:
                ln.record_protocol_error("JSONDecodeError")
            raise AiStructuredOutputError("JSONDecodeError sintetico")
        topics = [] if self.empty else [{"label": "Tema sintetico", "short_description": "Descrizione sintetica", "evidence_ids": ["E001"]}]
        if ln:
            ln.record_request_success()
        return LlmCompletionResponse(content=json.dumps({"topics": topics}), model=MODEL, raw_response={"finish_reason": "stop"})


def main() -> None:
    st.title("Anteprima pannello Discovery (dati SINTETICI)")
    st.caption("Nessun LM Studio, nessun dato reale. Checkpoint in una cartella temporanea nuova.")
    scenario = st.radio("Scenario", list(SCENARI), key="scenario")
    delay = float(os.environ.get("CHATANALYSIS_PREVIEW_DELAY", "0.3"))
    if st.button("Avvia Discovery sintetica", key="run"):
        cfg = SCENARI[scenario]
        doc = _doc("synthetic", cfg["n"])
        client = _SyntheticClient(cfg["fail_last"], cfg["empty"], cfg["n"] - 1, delay)
        with tempfile.TemporaryDirectory(prefix="chatanalysis_preview_") as tmp:
            store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp) / "chk")
            svc = LocalTopicAnalysisService(client=client, checkpoint_store=store, enable_checkpointing=True)
            panel = st.empty()
            cb = create_streamlit_progress_callback(panel, title="TOPIC DISCOVERY SINTETICA IN CORSO")
            res = svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, progress_callback=cb)
            status = res.metadata.get("status")
            if status == "COMPLETE":
                st.success(f"Esito del servizio: {status}")
            else:
                st.info(f"Esito del servizio: {status}")


main()
