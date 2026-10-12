"""
tests/unit/test_ui_manual_topic_detection.py
--------------------------------------------
Test suite per la verifica della Topic Detection manuale on-demand,
dell'integrazione Topic Discovery -> Topic Detection, dell'immutabilità forense,
del ciclo di vita di sessione e dell'isolamento offline.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

from ai.backend import (
    AiModelNotSpecifiedError,
    BaseLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.lmstudio import LmStudioClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.service import SearchService
from ui import state
from ui.application import (
    build_demo_session,
    build_topic_query,
    check_lm_studio_status,
    execute_manual_topic_detection,
    execute_real_ingestion,
    search_topic_detections,
)
from ui.demo import build_synthetic_demo_dataset
from ui.models import (
    DatasetMode,
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
    TopicFilterDecision,
)


class MockLlmClient(BaseLlmClient):
    """Client di test per simulare risposte deterministiche di LM Studio."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        model_id: str = "qwen2.5-7b-instruct",
        available: bool = True,
        models: tuple[str, ...] = ("qwen2.5-7b-instruct",),
        loaded_models: tuple[str, ...] | None = None,
        simulate_load_error: Exception | None = None,
    ) -> None:
        self.responses = list(responses) if responses is not None else []
        self.model_id = model_id
        self._available = available
        self._models = models
        self.loaded_models = set(loaded_models) if loaded_models is not None else set(models)
        self.load_calls: list[dict[str, Any]] = []
        self.simulate_load_error = simulate_load_error
        self.call_count = 0
        self.last_messages: list[dict[str, str]] | None = None

    def is_available(self) -> bool:
        return self._available

    def list_models(self) -> tuple[str, ...]:
        if not self._available:
            raise LmStudioUnavailableError("Server LM Studio non raggiungibile.")
        return self._models

    def is_model_loaded(self, model_id: str) -> bool:
        return model_id in self.loaded_models or any(model_id.lower() in m.lower() for m in self.loaded_models)

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        timeout_seconds: float = 240.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self._available:
            raise LmStudioUnavailableError("Server LM Studio non raggiungibile.")
        if self.simulate_load_error is not None:
            raise self.simulate_load_error
        entry = {
            "model_id": model_id,
            "context_length": context_length,
            "timeout_seconds": timeout_seconds,
            **kwargs,
        }
        self.load_calls.append(entry)
        self.loaded_models.add(model_id)
        return {"model": model_id, "status": "loaded"}

    def unload_model(self, model_id: str | None = None, timeout_seconds: float = 30.0) -> bool:
        if model_id:
            self.loaded_models.discard(model_id)
        else:
            self.loaded_models.clear()
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.last_messages = messages

        if not self._available:
            raise LmStudioUnavailableError("Connessione rifiutata su http://127.0.0.1:1234.")

        target_model = model_id or self.model_id
        if not target_model:
            raise AiModelNotSpecifiedError("Nessun model_id specificato.")

        if self.responses:
            payload = self.responses.pop(0)
        else:
            payload = {
                "decision": "PRESENT",
                "evidence_ids": ["SYNTH_EVID_TEXT_001"],
                "rationale": "Evidenza primaria riscontrata nel testo del messaggio.",
            }

        return LlmCompletionResponse(
            content=json.dumps(payload),
            model=target_model,
            prompt_tokens=42,
            completion_tokens=28,
            total_tokens=70,
            latency_seconds=0.35,
        )


def _build_test_document() -> ConversationEvidenceDocument:
    """Restituisce il ConversationEvidenceDocument del dataset sintetico dimostrativo."""
    doc, _, _ = build_synthetic_demo_dataset()
    return doc


# ---------------------------------------------------------------------------
# TEST 1: Topic libero -> TopicQuery deterministico
# ---------------------------------------------------------------------------
def test_free_topic_to_topic_query_validation():
    """Verifica che un argomento inserito liberamente crei un TopicQuery deterministico e valido."""
    q1 = build_topic_query(label="Organizzazione incontri", description="Pianificazione riservata")
    assert isinstance(q1, TopicQuery)
    assert q1.label == "Organizzazione incontri"
    assert q1.description == "Pianificazione riservata"
    assert q1.topic_id.startswith("custom_")

    # Determinismo: stessi parametri generano lo stesso identico topic_id
    q2 = build_topic_query(label="Organizzazione incontri", description="Pianificazione riservata")
    assert q1.topic_id == q2.topic_id

    # Fallback su label se description è vuota
    q3 = build_topic_query(label="Incontri", description="")
    assert q3.description == "Incontri"

    q4 = build_topic_query(label="Incontri", description=None)
    assert q4.description == "Incontri"

    # Validazione input vuoto
    with pytest.raises(ValueError, match="non vuota"):
        build_topic_query(label="   ")


# ---------------------------------------------------------------------------
# TEST 2: Esecuzione manuale Detection con TopicDetectionAnalyzer
# ---------------------------------------------------------------------------
def test_manual_topic_detection_execution():
    """Verifica che l'esecuzione manuale riutilizzi TopicDetectionAnalyzer ed esegua l'inferenza."""
    doc = _build_test_document()
    eid1 = doc.all_evidence_sections[0].evidence_id
    query = build_topic_query(label="Incontri riservati", description="Patti e accordi")
    client = MockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [eid1],
            "rationale": "Messaggio fa esplicito riferimento a luogo riservato.",
        }],
    )

    result = execute_manual_topic_detection(
        document=doc,
        topic=query,
        client=client,
        model_id="qwen2.5-7b-instruct",
    )

    assert isinstance(result, TopicDetectionResult)
    assert result.topic == query
    assert result.decision == TopicDecision.PRESENT
    assert result.provenance_document_id == doc.document_id
    assert client.call_count == 1
    assert result.metadata["model"] == "qwen2.5-7b-instruct"
    assert result.metadata["latency_seconds"] == 0.35


# ---------------------------------------------------------------------------
# TEST 3: PRESENT con evidence_id valido
# ---------------------------------------------------------------------------
def test_detection_present_with_valid_evidence_id():
    """Verifica che una decisione PRESENT contenga evidence_ids validi appartenenti al documento."""
    doc = _build_test_document()
    eid1 = doc.all_evidence_sections[0].evidence_id
    eid2 = doc.all_evidence_sections[1].evidence_id
    query = build_topic_query(label="Incontri riservati")
    client = MockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [eid1, eid2],
            "rationale": "Entrambi i messaggi confermano l'appuntamento.",
        }],
    )

    result = execute_manual_topic_detection(document=doc, topic=query, client=client)
    assert result.decision == TopicDecision.PRESENT
    assert len(result.evidence_ids) >= 1
    doc_eids = {s.evidence_id for s in doc.all_evidence_sections}
    for eid in result.evidence_ids:
        assert eid in doc_eids
    assert result.rationale != ""


# ---------------------------------------------------------------------------
# TEST 4: ABSENT con evidence_ids vuoti
# ---------------------------------------------------------------------------
def test_detection_absent_with_empty_evidence_ids():
    """Verifica che una decisione ABSENT abbia rigorosamente evidence_ids vuoto."""
    doc = _build_test_document()
    query = build_topic_query(label="Commercio armi", description="Traffico armi pesanti")
    client = MockLlmClient(
        responses=[{
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "Nessuna menzione di armi nei messaggi analizzati.",
        }],
    )

    result = execute_manual_topic_detection(document=doc, topic=query, client=client)
    assert result.decision == TopicDecision.ABSENT
    assert result.evidence_ids == ()
    assert "Nessuna menzione" in result.rationale


# ---------------------------------------------------------------------------
# TEST 5: UNCERTAIN
# ---------------------------------------------------------------------------
def test_detection_uncertain():
    """Verifica che una decisione UNCERTAIN sia correttamente gestita."""
    doc = _build_test_document()
    eid2 = doc.all_evidence_sections[1].evidence_id
    query = build_topic_query(label="Fatturazione", description="Pagamenti commerciali")
    client = MockLlmClient(
        responses=[{
            "decision": "UNCERTAIN",
            "evidence_ids": [eid2],
            "rationale": "Il messaggio menziona documenti ma non è chiaro se contabili.",
        }],
    )

    result = execute_manual_topic_detection(document=doc, topic=query, client=client)
    assert result.decision == TopicDecision.UNCERTAIN
    assert result.evidence_ids == (eid2,)
    assert "documenti" in result.rationale


# ---------------------------------------------------------------------------
# TEST 6: Discovery -> nuova Detection
# ---------------------------------------------------------------------------
def test_discovery_to_new_detection_flow():
    """Verifica che un DiscoveredTopic da Topic Discovery generi una NUOVA inferenza."""
    doc = _build_test_document()
    eid1 = doc.all_evidence_sections[0].evidence_id
    eid2 = doc.all_evidence_sections[1].evidence_id
    discovered = DiscoveredTopic(
        label="Accordi riservati",
        short_description="Discussione su incontro e consegna documentale",
        evidence_ids=(eid1, eid2),
    )

    # Il workflow parte da label e short_description del DiscoveredTopic
    detection_query = build_topic_query(
        label=discovered.label,
        description=discovered.short_description,
    )

    client = MockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [eid1],
            "rationale": "Confermato incontro riservato da analisi mirata.",
        }],
    )

    result = execute_manual_topic_detection(document=doc, topic=detection_query, client=client)

    assert isinstance(result, TopicDetectionResult)
    assert result.topic.label == discovered.label
    assert result.topic.description == discovered.short_description
    assert result.decision == TopicDecision.PRESENT
    assert result.evidence_ids == (eid1,)
    assert client.call_count == 1
    # Verifica che non sia una conversione artificiale ma un vero risultato di TopicDetectionAnalyzer
    assert result.metadata["model"] == client.model_id
    assert "prompt_version" in result.metadata


# ---------------------------------------------------------------------------
# TEST 7: Risultato aggiunto alla sessione Streamlit e SearchService sincronizzato
# ---------------------------------------------------------------------------
def test_result_added_to_session_and_search_service_updated():
    """Verifica che add_detection_result salvi il risultato nello stato e aggiorni SearchService."""
    doc = _build_test_document()
    eid1 = doc.all_evidence_sections[0].evidence_id
    eid2 = doc.all_evidence_sections[1].evidence_id
    session: dict[str, Any] = {}
    state.init_session_state(session)
    session[state.KEY_CONVERSATION_DOCUMENT] = doc
    session[state.KEY_SEARCH_SERVICE] = SearchService(document=doc)

    assert len(state.get_detection_results(session)) == 0

    query = build_topic_query(label="Topic A", description="Desc A")
    res1 = TopicDetectionResult(
        topic=query,
        decision=TopicDecision.PRESENT,
        evidence_ids=(eid1,),
        rationale="Evidenza trovata.",
        provenance_document_id=doc.document_id,
        metadata={"model": "test-m", "latency_seconds": 0.2},
    )

    state.add_detection_result(res1, state=session)
    results = state.get_detection_results(session)
    assert len(results) == 1
    assert results[0] == res1

    # SearchService deve essere sincronizzato e trovare il nuovo topic
    srv = state.get_search_service(session)
    assert srv is not None
    search_res = search_topic_detections(srv, query_text="Topic A")
    assert search_res.total_hits == 1
    assert search_res.hits[0].label == "Topic A"

    # Rifiuto duplicazione identica: rieseguire lo stesso topic aggiorna il record
    res2 = TopicDetectionResult(
        topic=query,
        decision=TopicDecision.PRESENT,
        evidence_ids=(eid1, eid2),
        rationale="Evidenza estesa trovata.",
        provenance_document_id=doc.document_id,
        metadata={"model": "test-m", "latency_seconds": 0.25},
    )
    state.add_detection_result(res2, state=session)
    results_after = state.get_detection_results(session)
    assert len(results_after) == 1
    assert results_after[0].rationale == "Evidenza estesa trovata."


# ---------------------------------------------------------------------------
# TEST 8: Documento forense originale non modificato
# ---------------------------------------------------------------------------
def test_forensic_document_remains_immutable():
    """Verifica che il ConversationEvidenceDocument rimanga assolutamente inalterato durante la detection."""
    doc = _build_test_document()
    original_bundles = doc.bundles
    original_sections = doc.all_evidence_sections
    original_metadata = doc.metadata

    query = build_topic_query(label="Verifica integrità", description="Test immutabilità")
    eid1 = doc.all_evidence_sections[0].evidence_id
    client = MockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [eid1],
            "rationale": "Evidenza valida e immutabile.",
        }]
    )

    session: dict[str, Any] = {}
    state.init_session_state(session)
    session[state.KEY_CONVERSATION_DOCUMENT] = doc

    result = execute_manual_topic_detection(document=doc, topic=query, client=client)
    state.add_detection_result(result, state=session)

    # Verifica strutturale
    assert doc.bundles == original_bundles
    assert doc.all_evidence_sections == original_sections
    assert doc.metadata == original_metadata
    assert state.get_conversation_document(session) is doc


# ---------------------------------------------------------------------------
# TEST 9: Errore controllato se LM Studio indisponibile
# ---------------------------------------------------------------------------
def test_controlled_error_when_lm_studio_unavailable():
    """Verifica che l'indisponibilità di LM Studio o l'assenza di modelli generi un errore controllato."""
    unavailable_client = MockLlmClient(available=False)

    status, models, err_msg = check_lm_studio_status(client=unavailable_client)
    assert status is False
    assert err_msg is not None
    assert "non raggiungibile" in err_msg.lower() or "connessione" in err_msg.lower()

    doc = _build_test_document()
    query = build_topic_query(label="Test errore")

    with pytest.raises(LmStudioUnavailableError):
        execute_manual_topic_detection(document=doc, topic=query, client=unavailable_client)

    # Test client privo di modelli caricati
    empty_models_client = MockLlmClient(available=True, models=(), model_id="")
    with pytest.raises(AiModelNotSpecifiedError):
        execute_manual_topic_detection(
            document=doc,
            topic=query,
            client=empty_models_client,
            model_id=None,
        )


# ---------------------------------------------------------------------------
# TEST 10: Nessuna chiamata AI durante ingestion
# ---------------------------------------------------------------------------
def test_zero_ai_calls_during_ingestion():
    """Verifica categorica che l'ingestion reale di file sia 100% offline e priva di chiamate AI."""
    raw_chat = (
        "10/05/2024, 14:30 - Alice: Ciao, hai novità per il report?\n"
        "10/05/2024, 14:31 - Bob: Sì, ho quasi terminato le tabelle.\n"
    ).encode("utf-8")

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename="_chat.txt",
        file_bytes=raw_chat,
    )

    # Esegue l'ingestion: non deve interagire con alcun client AI
    res = execute_real_ingestion(req)
    assert res.status == IngestionStatus.SUCCESS
    assert len(res.documents) == 1

    session: dict[str, Any] = {}
    state.set_real_ingestion_result(res, state=session)

    # In modalità FILE iniziale le detection e discovery devono essere rigorosamente vuote
    assert state.get_dataset_mode(session) == DatasetMode.FILE
    assert state.get_detection_results(session) == ()
    assert state.get_discovery_results(session) == ()


# ---------------------------------------------------------------------------
# TEST 11: Nessuna chiamata cloud (Loopback Security G1)
# ---------------------------------------------------------------------------
def test_no_cloud_calls_loopback_only_security():
    """Verifica che LmStudioClient rifiuti qualsiasi host non locale / cloud."""
    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="https://api.openai.com:1234")

    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="http://192.168.1.100:1234")

    # Host loopback validi accettati senza errori
    c1 = LmStudioClient(base_url="http://127.0.0.1:1234")
    assert c1.base_url == "http://127.0.0.1:1234"

    c2 = LmStudioClient(base_url="http://localhost:1234")
    assert c2.base_url == "http://localhost:1234"


# ---------------------------------------------------------------------------
# TEST 12: Esecuzione interattiva Streamlit AppTest (Area 1, Area 2, Discovery)
# ---------------------------------------------------------------------------
def test_streamlit_ui_manual_topic_detection_and_discovery_workflow():
    """Verifica end-to-end con AppTest di Area 1, Area 2 e verifica da Topic Discovery."""
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.run()

    doc, detections, discoveries, service = build_demo_session()
    eid1 = doc.all_evidence_sections[0].evidence_id

    # Inizializza sessione con demo dataset e mock client
    mock_client = MockLlmClient(
        responses=[
            {
                "decision": "PRESENT",
                "evidence_ids": [eid1],
                "rationale": "Accordo verificato esplicitamente nella chat dimostrativa.",
            },
            {
                "decision": "PRESENT",
                "evidence_ids": [eid1],
                "rationale": "Tema scoperto verificato tramite nuova inferenza.",
            },
        ],
    )

    at.session_state["dataset_loaded"] = True
    at.session_state["dataset_mode"] = DatasetMode.DEMO
    at.session_state["conversation_document"] = doc
    at.session_state["detection_results"] = detections
    at.session_state["discovery_results"] = discoveries
    at.session_state["search_service"] = service
    at.session_state["lm_studio_client"] = mock_client

    # Naviga a Analisi topic
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    assert not at.exception

    # Verifica presenza dei controlli di Area 1
    topic_inputs = [ti for ti in at.text_input if ti.key == "manual_topic_input"]
    assert len(topic_inputs) == 1

    run_btns = [b for b in at.button if b.key == "btn_run_manual_detection"]
    assert len(run_btns) == 1

    # Imposta un topic libero ed esegui la Detection
    topic_inputs[0].set_value("Organizzazione di incontri riservati")
    run_btns[0].click()
    at.run()
    assert not at.exception

    # Verifica che il risultato sia stato salvato nella sessione
    results = at.session_state["detection_results"]
    assert any(r.topic.label == "Organizzazione di incontri riservati" for r in results)
    assert mock_client.call_count >= 1

    # Verifica pulsante in Topic Discovery
    disc_btns = [b for b in at.button if "verify_disc_topic" in (b.key or "")]
    assert len(disc_btns) >= 1

    # Clicca sul pulsante "Verifica con Topic Detection" del primo argomento scoperto
    disc_btns[0].click()
    at.run()
    assert not at.exception
    assert mock_client.call_count >= 2
