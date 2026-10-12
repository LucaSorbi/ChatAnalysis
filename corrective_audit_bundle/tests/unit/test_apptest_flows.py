"""
test_apptest_flows.py
---------------------
Streamlit AppTest per i flussi end-to-end operativi (FASE 28, CRITERI DI ACCETTAZIONE).

Flussi coperti:
A. FILE mode con zero risultati AI: nessun banner differito, Detection disponibile, Discovery disponibile.
B. FILE Topic Detection con fake client: click reale, nuova inferenza, risultato scoped in sessione.
C. FILE Topic Discovery: click reale, nuova inferenza, risultato scoped in sessione.
D. FILE Discovery -> Detection: Discovery, poi seconda Detection call, 2 inferenze indipendenti.
E. DEMO live Discovery: non usa risultati hardcoded come live, effettua inferenza.
F. DEMO live Discovery -> Detection: 2 inferenze indipendenti nel workflow.
G. Multi-conversation switching: Chat A -> Chat B -> Chat A senza perdita / cross contamination.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from search.service import SearchService
from ui import state
from ui.demo import build_synthetic_demo_dataset
from ui.models import (
    DatasetMode,
    ImportedConversationInfo,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
    SourceFormat,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


class MockAppLlmClient(BaseLlmClient):
    """Client di test per Streamlit AppTest con risposte preconfigurate."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []
        self.model_id = "qwen2.5-7b-instruct"

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.call_history.append({"messages": messages, "model_id": model_id})
        if self.responses:
            data = self.responses.pop(0)
        else:
            data = {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna evidenza rilevata."}
        return LlmCompletionResponse(
            content=json.dumps(data),
            model=model_id or self.model_id,
            prompt_tokens=15,
            completion_tokens=15,
            total_tokens=30,
            latency_seconds=0.1,
        )


def _setup_app_with_doc(doc: ConversationEvidenceDocument, mode: DatasetMode = DatasetMode.FILE) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.run()
    at.session_state["dataset_loaded"] = True
    at.session_state["dataset_mode"] = mode
    at.session_state["conversation_document"] = doc
    at.session_state["available_documents"] = {doc.document_id: doc}
    at.session_state["selected_document_id"] = doc.document_id
    state.get_document_analysis_state(doc.document_id, at.session_state)
    return at


def test_apptest_a_file_mode_zero_ai_results_no_deferred_banner():
    """Flusso A: In modalità FILE senza AI, nessun banner 'differito', tab detection e discovery presenti."""
    doc, _, _ = build_synthetic_demo_dataset()
    at = _setup_app_with_doc(doc, mode=DatasetMode.FILE)

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    assert not at.exception

    for info_box in at.info:
        assert "differito alla fase sperimentale finale" not in info_box.value.lower()
        assert "hardware block" not in info_box.value.lower()
        assert "rinvio benchmark" not in info_box.value.lower()

    # Controlli di Detection e Discovery disponibili
    assert any(b.key == "btn_run_manual_detection" for b in at.button)
    assert any(b.key == "btn_run_live_discovery" for b in at.button)


def test_apptest_b_file_topic_detection_click_and_scoped_result():
    """Flusso B: Click reale di Topic Detection in FILE mode salva il risultato nella sessione scoped."""
    doc, _, _ = build_synthetic_demo_dataset()
    eid = doc.all_evidence_sections[0].evidence_id

    mock_client = MockAppLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Evidenza primaria trovata nella chat reale.",
    }])

    at = _setup_app_with_doc(doc, mode=DatasetMode.FILE)
    at.session_state["lm_studio_client"] = mock_client

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    assert not at.exception

    # Compila topic e clicca esecuzione
    topic_in = [ti for ti in at.text_input if ti.key == "manual_topic_input"][0]
    topic_in.set_value("Contratti e accordi riservati")

    run_btn = [b for b in at.button if b.key == "btn_run_manual_detection"][0]
    run_btn.click()
    at.run()
    assert not at.exception

    assert mock_client.call_count == 1
    scoped_dets = state.get_detection_results(at.session_state)
    assert len(scoped_dets) == 1
    assert scoped_dets[0].decision == TopicDecision.PRESENT
    assert scoped_dets[0].evidence_ids == (eid,)


def test_apptest_c_file_topic_discovery_click_and_scoped_result():
    """Flusso C: Click reale di Topic Discovery in FILE mode esegue l'inferenza e salva i risultati."""
    doc, _, _ = build_synthetic_demo_dataset()
    eid = doc.all_evidence_sections[0].evidence_id

    mock_client = MockAppLlmClient(responses=[{
        "topics": [{
            "label": "Forniture e Merci",
            "short_description": "Tracciamento spedizioni e colli",
            "evidence_ids": [eid],
        }]
    }])

    at = _setup_app_with_doc(doc, mode=DatasetMode.FILE)
    at.session_state["lm_studio_client"] = mock_client

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    assert not at.exception

    # Clicca pulsante Discovery LIVE
    disc_btn = [b for b in at.button if b.key == "btn_run_live_discovery"][0]
    disc_btn.click()
    at.run()
    assert not at.exception

    assert mock_client.call_count == 1
    disc_results = state.get_discovery_results(at.session_state)
    assert len(disc_results) == 1
    assert len(disc_results[0].topics) == 1
    assert disc_results[0].topics[0].label == "Forniture e Merci"


def test_apptest_d_file_discovery_to_detection_two_independent_inferences():
    """Flusso D: Discovery LIVE seguita da Verifica con Topic Detection genera 2 chiamate indipendenti."""
    doc, _, _ = build_synthetic_demo_dataset()
    eid = doc.all_evidence_sections[0].evidence_id

    mock_client = MockAppLlmClient(responses=[
        # Chiamata 1: Discovery
        {
            "topics": [{
                "label": "Logistica Depositi",
                "short_description": "Ispezione casse e magazzini",
                "evidence_ids": [eid],
            }]
        },
        # Chiamata 2: Seconda inferenza indipendente (Detection)
        {
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Verifica confermata tramite seconda inferenza.",
        },
    ])

    at = _setup_app_with_doc(doc, mode=DatasetMode.FILE)
    at.session_state["lm_studio_client"] = mock_client

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    # 1. Discovery
    disc_btn = [b for b in at.button if b.key == "btn_run_live_discovery"][0]
    disc_btn.click()
    at.run()
    assert mock_client.call_count == 1

    # 2. Verifica con Topic Detection (Prova del Nove)
    verify_btns = [b for b in at.button if "verify_disc_topic" in (b.key or "")]
    assert len(verify_btns) >= 1
    verify_btns[0].click()
    at.run()
    assert not at.exception

    # call_count dimostra 2 inferenze indipendenti
    assert mock_client.call_count == 2
    dets = state.get_detection_results(at.session_state)
    assert len(dets) == 1
    assert dets[0].decision == TopicDecision.PRESENT


def test_apptest_e_demo_live_discovery_runs_real_inference():
    """Flusso E: In DEMO, i risultati live partono vuoti e il click esegue vera inferenza."""
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.run()
    # Carica demo dataset
    at.session_state["dataset_loaded"] = True
    state.load_demo_dataset(at.session_state)

    # Verifica che live discovery results partano vuoti
    assert state.get_discovery_results(at.session_state) == ()

    mock_client = MockAppLlmClient(responses=[{
        "topics": [{
            "label": "Ispezione Depositi Live",
            "short_description": "Scoperta in tempo reale",
            "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
        }]
    }])
    at.session_state["lm_studio_client"] = mock_client

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    disc_btn = [b for b in at.button if b.key == "btn_run_live_discovery"][0]
    disc_btn.click()
    at.run()
    assert not at.exception

    assert mock_client.call_count == 1
    live_disc = state.get_discovery_results(at.session_state)
    assert len(live_disc) == 1
    assert live_disc[0].topics[0].label == "Ispezione Depositi Live"


def test_apptest_f_demo_live_discovery_to_detection_flow():
    """Flusso F: In DEMO, il flusso completo Discovery LIVE -> Detection esegue 2 inferenze reali."""
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.run()
    state.load_demo_dataset(at.session_state)

    eid = "msg::5::VISION_DESCRIPTION"
    mock_client = MockAppLlmClient(responses=[
        {"topics": [{"label": "Magazzino e Casse", "short_description": "Sicurezza deposito", "evidence_ids": [eid]}]},
        {"decision": "PRESENT", "evidence_ids": [eid], "rationale": "Confermato dalla prova del nove."},
    ])
    at.session_state["lm_studio_client"] = mock_client

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    # Step 1: Discovery
    disc_btn = [b for b in at.button if b.key == "btn_run_live_discovery"][0]
    disc_btn.click()
    at.run()
    assert mock_client.call_count == 1

    # Step 2: Detection
    verify_btns = [b for b in at.button if "verify_disc_topic" in (b.key or "")]
    assert len(verify_btns) >= 1
    verify_btns[0].click()
    at.run()
    assert not at.exception

    assert mock_client.call_count == 2
    assert len(state.get_detection_results(at.session_state)) == 1


def test_apptest_g_multi_conversation_preserves_results_across_switches():
    """Flusso G: Switch A -> B -> A preserva i risultati senza cross contamination."""
    doc, _, _ = build_synthetic_demo_dataset()
    doc_a = ConversationEvidenceDocument(
        document_id="doc_chat_a",
        bundles=doc.bundles[:2],
        source_name="demo_msgstore_db",
    )
    doc_b = ConversationEvidenceDocument(
        document_id="doc_chat_b",
        bundles=doc.bundles[2:4],
        source_name="demo_msgstore_db",
    )

    s: dict[str, Any] = {}
    state.init_session_state(s)
    s["dataset_loaded"] = True
    s["dataset_mode"] = DatasetMode.FILE
    s["available_documents"] = {
        doc_a.document_id: doc_a,
        doc_b.document_id: doc_b,
    }
    state.select_conversation(doc_a.document_id, s)

    # Aggiungi risultato alla Chat A
    res_a = TopicDetectionResult(
        topic=doc.bundles[0].message.source_record_id and TopicDetectionResult.__init__.__annotations__ and TopicDetectionResult(
            topic=build_synthetic_demo_dataset()[1][0].topic,
            decision=TopicDecision.PRESENT,
            evidence_ids=(doc_a.all_evidence_sections[0].evidence_id,),
            rationale="Chat A result",
            provenance_document_id=doc_a.document_id,
        ).topic,
        decision=TopicDecision.PRESENT,
        evidence_ids=(doc_a.all_evidence_sections[0].evidence_id,),
        rationale="Risultato per Chat A",
        provenance_document_id=doc_a.document_id,
    )
    state.add_detection_result(res_a, s)
    assert len(state.get_detection_results(s)) == 1

    # Switch to Chat B
    state.select_conversation(doc_b.document_id, s)
    assert state.get_detection_results(s) == ()

    # Aggiungi risultato alla Chat B
    res_b = TopicDetectionResult(
        topic=res_a.topic,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Risultato per Chat B",
        provenance_document_id=doc_b.document_id,
    )
    state.add_detection_result(res_b, s)
    assert len(state.get_detection_results(s)) == 1
    assert state.get_detection_results(s)[0].decision == TopicDecision.ABSENT

    # Switch BACK to Chat A
    state.select_conversation(doc_a.document_id, s)
    dets_a = state.get_detection_results(s)
    assert len(dets_a) == 1
    assert dets_a[0].decision == TopicDecision.PRESENT
    assert dets_a[0].provenance_document_id == doc_a.document_id
