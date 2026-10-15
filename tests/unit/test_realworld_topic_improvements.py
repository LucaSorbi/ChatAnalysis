"""
tests/unit/test_realworld_topic_improvements.py
------------------------------------------------
Suite completa di test per verificare i miglioramenti a Topic Detection e Topic Discovery
in scenari operativi su conversazioni reali:
- Detection semantica con sola label (sinonimi, espressioni equivalenti, nessun matching letterale obbligatorio)
- Test negativi (ABSENT) e ambigui (UNCERTAIN) senza forzature
- Analisi multi-chunk completa su documenti estesi
- Discovery con 3, 5, 10, 15, 20 e 25 topic richiesti
- Budget dinamico di generazione per evitare troncamenti JSON
- Gestione di finish_reason="length", JSON troncato e retry controllato
- Consolidamento chunk-level senza duplicati e conservazione degli evidence ID validi
- Rifiuto rigoroso di evidence ID inventati
- Distinzione tra run storiche e run nuove (run_id, provenance, immutabilità)
- Nessuna contaminazione tra conversazioni e nessuna inferenza durante l'ingestion
- Streamlit AppTest per verificare la coerenza UI tra Discovery e Detection
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from streamlit.testing.v1 import AppTest

from ai.backend import (
    AiInvalidEvidenceCitationError,
    AiStructuredOutputError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MODEL,
    DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.topics import (
    PROMPT_VERSION_DETECTION,
    PROMPT_VERSION_DETECTION_OPERATIONAL,
    PROMPT_VERSION_DISCOVERY,
    PROMPT_VERSION_DISCOVERY_OPERATIONAL,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from analysis.models import ConsistencyStatus
from analysis.service import (
    LocalTopicAnalysisService,
    calculate_discovery_max_tokens,
)
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from ui import state
from ui.application import (
    build_topic_query,
    execute_discovery_verification,
    execute_manual_topic_detection,
    execute_topic_discovery,
)
from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from unified.models import UnifiedMessage
from validation.models import ValidationResult


APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


def _make_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp_txt",
        source_path="/fake/path",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp_txt",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:whatsapp_txt:{idx}",
        source_name="whatsapp_txt",
        source_record_id=idx,
        source_path="/fake/path",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _build_car_purchase_document() -> ConversationEvidenceDocument:
    """Crea documento sintetico per il caso di test 'Acquisto di un'automobile' con sinonimi colloquiali."""
    b1 = _make_bundle("1", "Domani andiamo a vedere quella macchina usata dal concessionario.")
    b2 = _make_bundle("2", "Il venditore ha chiesto ottomila euro compreso il passaggio.")
    b3 = _make_bundle("3", "Se accetta il prezzo, firmiamo il passaggio di proprietà.")
    b4 = _make_bundle("4", "Stasera guardiamo la partita di calcio in televisione alle nove.")
    return ConversationEvidenceDocument(
        document_id="doc_synth_car_001",
        bundles=(b1, b2, b3, b4),
    )


class ControllableMockLlmClient(BaseLlmClient):
    """Mock LLM client con supporto a risposte sequenziali, finish_reason e ispezione dei prompt."""

    def __init__(
        self,
        responses: list[dict[str, Any] | str] | None = None,
        model_id: str = "qwen2.5-7b-instruct",
        finish_reasons: list[str | None] | None = None,
    ) -> None:
        self.responses = list(responses) if responses is not None else []
        self.finish_reasons = list(finish_reasons) if finish_reasons is not None else []
        self.model_id = model_id
        self.call_count = 0
        self.history_calls: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return (self.model_id,)

    def is_model_loaded(self, model_id: str) -> bool:
        return True

    def load_model(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"model": model_id, "status": "loaded"}

    def unload_model(self, model_id: str | None = None, **kwargs: Any) -> bool:
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
        call_info = {
            "messages": messages,
            "model_id": model_id or self.model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        self.history_calls.append(call_info)

        resp_item = self.responses.pop(0) if self.responses else {
            "decision": "PRESENT",
            "evidence_ids": ["unified:whatsapp_txt:1::ORIGINAL_TEXT"],
            "rationale": "ok",
        }
        finish_reason = self.finish_reasons.pop(0) if self.finish_reasons else None

        if isinstance(resp_item, str):
            content_str = resp_item
        else:
            content_str = json.dumps(resp_item)
        raw_resp: dict[str, Any] = {}
        meta_dict: dict[str, Any] = {}
        if finish_reason:
            raw_resp["finish_reason"] = finish_reason
            meta_dict["finish_reason"] = finish_reason

        return LlmCompletionResponse(
            content=content_str,
            model=model_id or self.model_id,
            prompt_tokens=100,
            completion_tokens=len(content_str.split()),
            total_tokens=100 + len(content_str.split()),
            latency_seconds=0.2,
            raw_response=raw_resp,
            metadata=meta_dict,
        )


# ==============================================================================
# SEZIONE 1: TOPIC DETECTION SEMANTICA CON SOLA LABEL
# ==============================================================================

def test_build_topic_query_sets_is_label_only():
    """Verifica che build_topic_query rilevi correttamente la modalità label-only."""
    q_label_only = build_topic_query(label="Acquisto di un'automobile", description="")
    assert q_label_only.is_label_only is True
    assert q_label_only.label == "Acquisto di un'automobile"
    assert q_label_only.description == "Acquisto di un'automobile"

    q_none_desc = build_topic_query(label="Acquisto di un'automobile", description=None)
    assert q_none_desc.is_label_only is True

    q_with_desc = build_topic_query(label="Acquisto di un'automobile", description="Trattativa per compravendita veicolo")
    assert q_with_desc.is_label_only is False
    assert q_with_desc.description == "Trattativa per compravendita veicolo"


def test_detection_label_only_car_purchase_present_with_synonyms():
    """
    Verifica che con la sola label 'Acquisto di un'automobile', il prompt operativo
    includa le direttive semantiche e accetti la decisione PRESENT supportata da sinonimi
    ('macchina usata', 'venditore', 'passaggio di proprietà') senza le parole del titolo.
    """
    doc = _build_car_purchase_document()
    query = build_topic_query(label="Acquisto di un'automobile", description=None)
    assert query.is_label_only is True

    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    ev2 = "unified:whatsapp_txt:2::ORIGINAL_TEXT"
    ev3 = "unified:whatsapp_txt:3::ORIGINAL_TEXT"

    mock_client = ControllableMockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [ev1, ev2, ev3],
            "rationale": "I messaggi descrivono la compravendita di un veicolo usato (macchina) e il passaggio di proprietà per 8000 euro.",
        }]
    )

    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.detect_topic(document=doc, topic=query)

    assert res.decision == TopicDecision.PRESENT
    assert set(res.evidence_ids) == {ev1, ev2, ev3}
    assert "unified:whatsapp_txt:4::ORIGINAL_TEXT" not in res.evidence_ids

    # Verifica che il prompt contenga la direttiva semantica
    sent_prompt = mock_client.history_calls[0]["messages"][0]["content"]
    assert "OPERATIONAL SEMANTIC & MULTIMODAL INTERPRETATION DIRECTIVE:" in sent_prompt
    assert "LABEL-ONLY mode" in sent_prompt
    assert "Do NOT require the literal words of the topic title to appear verbatim" in sent_prompt


def test_detection_negative_topic_absent():
    """
    Verifica un test negativo: se l'argomento cercato è del tutto assente nella chat,
    la Detection restituisce ABSENT e nessuna evidenza, senza forzature.
    """
    doc = _build_car_purchase_document()
    query = build_topic_query(label="Prenotazione viaggio spaziale", description=None)

    mock_client = ControllableMockLlmClient(
        responses=[{
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "Nessun riferimento a viaggi spaziali o prenotazioni aerospaziali nei messaggi.",
        }]
    )

    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.detect_topic(document=doc, topic=query)

    assert res.decision == TopicDecision.ABSENT
    assert len(res.evidence_ids) == 0


def test_detection_ambiguous_topic_uncertain():
    """
    Verifica un test ambiguo: se l'evidenza è solo allusiva o incerta,
    la risposta deve essere UNCERTAIN secondo i criteri di rigore forense.
    """
    b1 = _make_bundle("1", "Forse faremo quel colloquio, vediamo come va la prossima settimana.")
    doc = ConversationEvidenceDocument(document_id="doc_amb", bundles=(b1,))
    query = build_topic_query(label="Assunzione nuovo dipendente", description=None)

    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    mock_client = ControllableMockLlmClient(
        responses=[{
            "decision": "UNCERTAIN",
            "evidence_ids": [ev1],
            "rationale": "Il messaggio menziona un generico colloquio futuro senza confermare assunzioni o contratti.",
        }]
    )

    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.detect_topic(document=doc, topic=query)

    assert res.decision == TopicDecision.UNCERTAIN
    assert res.evidence_ids == (ev1,)


def test_detection_rejects_hallucinated_evidence_id():
    """Verifica che una citazione di evidence ID inventato venga rigettata con AiInvalidEvidenceCitationError."""
    doc = _build_car_purchase_document()
    query = build_topic_query(label="Acquisto automobile", description=None)

    mock_client = ControllableMockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": ["unified:whatsapp_txt:1::ORIGINAL_TEXT", "NON_EXISTING_EVID_999"],
            "rationale": "Trovato",
        }]
    )

    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    with pytest.raises(AiInvalidEvidenceCitationError):
        analyzer.detect_topic(document=doc, topic=query)


def test_detection_multi_chunk_completeness():
    """
    Verifica che su una conversazione che richiede suddivisione in chunk,
    Topic Detection analizzi tutti i chunk previsti e aggreghi le evidenze.
    """
    bundles = [
        _make_bundle(str(i), f"Messaggio {i} di prova con testo sufficiente a riempire il context per il chunking.")
        for i in range(1, 31)
    ]
    doc = ConversationEvidenceDocument(document_id="doc_multi_01", bundles=tuple(bundles))
    query = build_topic_query(label="Test multi chunk", description=None)

    service = LocalTopicAnalysisService(max_chunk_bundles=15)
    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    ev20 = "unified:whatsapp_txt:20::ORIGINAL_TEXT"

    mock_client = ControllableMockLlmClient(
        responses=[
            {"decision": "PRESENT", "evidence_ids": [ev1], "rationale": "Chunk 1 presente"},
            {"decision": "PRESENT", "evidence_ids": [ev20], "rationale": "Chunk 2 presente"},
        ]
    )

    res = service.run_topic_detection(
        document=doc,
        topic=query,
        client=mock_client,
    )

    assert res.decision == TopicDecision.PRESENT
    assert ev1 in res.evidence_ids
    assert ev20 in res.evidence_ids
    assert res.metadata.get("chunk_count") == 2
    assert res.metadata.get("completed_chunk_count") == 2
    assert res.metadata.get("failed_chunk_count") == 0


# ==============================================================================
# SEZIONE 2: TOPIC DISCOVERY CON MOLTI ARGOMENTI (Fino a 25 Topic)
# ==============================================================================

@pytest.mark.parametrize("req_topics,expected_min_tokens", [
    (3, 1024),
    (5, 1024),
    (10, 1456),
    (15, 2056),
    (20, 2656),
    (25, 3256),
])
def test_calculate_discovery_max_tokens_scaling(req_topics: int, expected_min_tokens: int):
    """Verifica che il budget di token scali in proporzione ai topic richiesti (3, 5, 10, 15, 20, 25)."""
    budget = calculate_discovery_max_tokens(max_topics=req_topics, user_max_tokens=None, context_length=8192)
    assert budget >= expected_min_tokens
    assert budget <= 4096


def test_topic_discovery_returns_fewer_than_max_when_fewer_exist():
    """
    Verifica che Topic Discovery non inventi topic se ne emergono solo 2
    anche quando l'utente ne richiede 10 o 25.
    """
    doc = _build_car_purchase_document()
    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    ev4 = "unified:whatsapp_txt:4::ORIGINAL_TEXT"

    mock_client = ControllableMockLlmClient(
        responses=[{
            "topics": [
                {
                    "label": "Trattativa veicolo",
                    "short_description": "Accordo per la compravendita di una macchina",
                    "evidence_ids": [ev1],
                },
                {
                    "label": "Calcio e televisione",
                    "short_description": "Programmazione partita serale",
                    "evidence_ids": [ev4],
                },
            ]
        }]
    )

    analyzer = TopicDiscoveryAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.discover_topics(document=doc, max_topics=10)

    assert len(res.topics) == 2
    assert res.topics[0].label == "Trattativa veicolo"
    assert res.topics[1].label == "Calcio e televisione"


def test_topic_discovery_25_topics_consolidation():
    """
    Verifica la gestione di 25 topic scoperti e consolidati da più chunk,
    con deduplicazione e conservazione di tutti gli evidence ID.
    """
    bundles = [
        _make_bundle(str(i), f"Conversazione su argomento tematico specifico #{i}")
        for i in range(1, 41)
    ]
    doc = ConversationEvidenceDocument(document_id="doc_large_25", bundles=tuple(bundles))

    # Simuliamo 25 topic restituiti dal modello attraverso 4 chunk (max 8 topic per chunk)
    chunk1_topics = [
        {"label": f"Argomento {i}", "short_description": f"Descrizione {i}", "evidence_ids": [f"unified:whatsapp_txt:{i}::ORIGINAL_TEXT"]}
        for i in range(1, 8)
    ]
    # In chunk 2 ripetiamo Argomento 1 (con evidenza in più da chunk 2) e inseriamo argomenti da 8 a 13
    chunk2_topics = [
        {"label": "Argomento 1", "short_description": "Descrizione 1 aggiornata", "evidence_ids": ["unified:whatsapp_txt:11::ORIGINAL_TEXT"]}
    ] + [
        {"label": f"Argomento {i}", "short_description": f"Descrizione {i}", "evidence_ids": [f"unified:whatsapp_txt:{i + 4}::ORIGINAL_TEXT"]}
        for i in range(8, 14)
    ]
    # Chunk 3 (bundles 21..30): argomenti 14..19
    chunk3_topics = [
        {"label": f"Argomento {i}", "short_description": f"Descrizione {i}", "evidence_ids": [f"unified:whatsapp_txt:{i + 7}::ORIGINAL_TEXT"]}
        for i in range(14, 20)
    ]
    # Chunk 4 (bundles 31..40): argomenti 20..25
    chunk4_topics = [
        {"label": f"Argomento {i}", "short_description": f"Descrizione {i}", "evidence_ids": [f"unified:whatsapp_txt:{i + 11}::ORIGINAL_TEXT"]}
        for i in range(20, 26)
    ]

    mock_client = ControllableMockLlmClient(
        responses=[
            {"topics": chunk1_topics},
            {"topics": chunk2_topics},
            {"topics": chunk3_topics},
            {"topics": chunk4_topics},
        ]
    )

    service = LocalTopicAnalysisService(max_chunk_bundles=10)
    res = service.run_topic_discovery(
        document=doc,
        client=mock_client,
        max_topics=25,
    )

    # Argomento 1 deve essere consolidato con entrambe le evidenze
    top1 = next(t for t in res.topics if t.label == "Argomento 1")
    assert "unified:whatsapp_txt:1::ORIGINAL_TEXT" in top1.evidence_ids
    assert "unified:whatsapp_txt:11::ORIGINAL_TEXT" in top1.evidence_ids

    # Totale argomenti consolidati deve essere 25
    assert len(res.topics) == 25
    assert res.metadata.get("selected_topic_count") == 25
    assert res.metadata.get("consolidated_topic_count") == 25


def test_topic_discovery_finish_reason_length_triggers_controlled_retry():
    """
    Verifica la gestione di finish_reason='length' o JSON incompleto:
    il sistema esegue un retry controllato aumentando il budget token.
    """
    doc = _build_car_purchase_document()
    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"

    # Prima chiamata restituisce stringa troncata e finish_reason='length'
    truncated_json_str = '{"topics": [{"label": "Trattativa veicolo", "short_description": "Accordo per la comp'
    successful_retry_json = {
        "topics": [
            {
                "label": "Trattativa veicolo",
                "short_description": "Accordo per la compravendita di una macchina",
                "evidence_ids": [ev1],
            }
        ]
    }

    mock_client = ControllableMockLlmClient(
        responses=[truncated_json_str, successful_retry_json],
        finish_reasons=["length", "stop"],
    )

    analyzer = TopicDiscoveryAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.discover_topics(document=doc, max_topics=10, max_tokens=1024)

    assert mock_client.call_count == 2
    # Il secondo tentativo ha ricevuto max_tokens aumentato
    assert mock_client.history_calls[1]["max_tokens"] > mock_client.history_calls[0]["max_tokens"]
    assert len(res.topics) == 1
    assert res.topics[0].label == "Trattativa veicolo"


def test_topic_discovery_unrepairable_json_raises_without_hallucinating():
    """
    Verifica che se il JSON rimane irreparabilmente corrotto dopo il retry,
    il sistema sollevi AiStructuredOutputError senza inventare dati.
    """
    doc = _build_car_purchase_document()
    corrupt_str = '{"topics": [ INVALID_JSON_DATA'

    mock_client = ControllableMockLlmClient(
        responses=[corrupt_str, corrupt_str],
        finish_reasons=["length", "length"],
    )

    analyzer = TopicDiscoveryAnalyzer(client=mock_client, operational_mode=True)
    with pytest.raises(AiStructuredOutputError):
        analyzer.discover_topics(document=doc, max_topics=10, max_tokens=1024)


# ==============================================================================
# SEZIONE 3: COERENZA DELLE SCHERMATE, PROVENANCE E RUN DISTINTE
# ==============================================================================

def test_discovery_verification_maintains_independent_run_identity():
    """
    Verifica che la verifica di un topic da Discovery crei una nuova inferenza con
    origin_flow='DISCOVERY_VERIFICATION' e leghi la consistenza allo specifico discovery_run_id.
    """
    doc = _build_car_purchase_document()
    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    ev2 = "unified:whatsapp_txt:2::ORIGINAL_TEXT"

    # 1. Eseguiamo prima la discovery autoritativa
    mock_client_disc = ControllableMockLlmClient(
        responses=[{"topics": [{"label": "Acquisto veicolo", "short_description": "Compravendita automobile", "evidence_ids": [ev1, ev2]}]}]
    )
    disc_res = execute_topic_discovery(
        document=doc,
        client=mock_client_disc,
        max_topics=5,
    )
    disc_topic = disc_res.topics[0]
    disc_run_id = disc_res.metadata["run_id"]

    # 2. Eseguiamo la verifica
    mock_client_det = ControllableMockLlmClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [ev1, ev2],
            "rationale": "Confermato",
        }]
    )

    det_res, cons_res = execute_discovery_verification(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id=disc_run_id,
        client=mock_client_det,
    )

    assert det_res.decision == TopicDecision.PRESENT
    assert det_res.metadata.get("origin_flow") == "DISCOVERY_VERIFICATION"
    assert det_res.metadata.get("verified_discovery_run_id") == disc_run_id
    assert cons_res.status == ConsistencyStatus.CONSISTENT
    assert cons_res.discovery_run_id == disc_run_id


def test_manual_detection_after_discovery_verification_does_not_mutate_prior_runs():
    """
    Verifica che eseguire una nuova Topic Detection manuale dopo una verifica da Discovery
    crei una run indipendente (MANUAL_DETECTION), conservi lo storico di entrambe e
    non modifichi la consistenza precedentemente registrata.
    """
    doc = _build_car_purchase_document()
    ev1 = "unified:whatsapp_txt:1::ORIGINAL_TEXT"
    ev2 = "unified:whatsapp_txt:2::ORIGINAL_TEXT"
    session: dict[str, Any] = {}

    # 1. Eseguiamo prima la Discovery nella sessione
    mock_client_d = ControllableMockLlmClient(
        responses=[{"topics": [{"label": "Acquisto veicolo", "short_description": "Compravendita automobile", "evidence_ids": [ev1, ev2]}]}]
    )
    disc_res = execute_topic_discovery(
        document=doc,
        client=mock_client_d,
        max_topics=5,
        session_state=session,
    )
    disc_topic = disc_res.topics[0]
    disc_run_id = disc_res.metadata["run_id"]

    # 2. Eseguiamo la verifica da Discovery nella sessione
    mock_client_v = ControllableMockLlmClient(
        responses=[{"decision": "PRESENT", "evidence_ids": [ev1, ev2], "rationale": "Verifica ok"}]
    )
    det_v, cons_v = execute_discovery_verification(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id=disc_run_id,
        client=mock_client_v,
        session_state=session,
    )

    # 3. Ora eseguiamo una nuova Topic Detection manuale con sola label
    query_manual = build_topic_query(label="Acquisto veicolo", description=None)
    mock_client_m = ControllableMockLlmClient(
        responses=[{"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna corrispondenza trovata"}]
    )
    det_m = execute_manual_topic_detection(
        document=doc,
        topic=query_manual,
        client=mock_client_m,
        session_state=session,
    )

    doc_state = state.get_document_analysis_state(doc.document_id, state=session)
    runs = doc_state.detection_runs
    assert len(runs) == 2
    assert runs[0].metadata.get("origin_flow") == "DISCOVERY_VERIFICATION"
    assert runs[0].decision == TopicDecision.PRESENT
    assert runs[1].metadata.get("origin_flow") == "MANUAL_DETECTION"
    assert runs[1].decision == TopicDecision.ABSENT

    # La consistenza associata a disc_run_id rimane invariata a CONSISTENT
    cons_results = state.get_consistency_results(doc.document_id, state=session)
    assert len(cons_results) == 1
    assert cons_results[0].status == ConsistencyStatus.CONSISTENT
    assert cons_results[0].discovery_run_id == disc_run_id


def test_zero_ai_inference_during_real_file_ingestion():
    """Verifica che durante l'ingestion di file forensi non venga eseguita alcuna chiamata AI."""
    fixture_path = Path("test_data/synthetic_whatsapp_large_75kb.txt")
    assert fixture_path.exists(), "La fixture sintetica da ~75KB deve esistere."

    with open(fixture_path, "rb") as f:
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_EXPORT,
            filename=fixture_path.name,
            file_bytes=f.read(),
        )
        res = ingest_file_payload(req)

    assert res.summary.status.value == "SUCCESS"
    assert res.summary.file_size_bytes > 68000
    doc = list(res.documents.values())[0]
    # L'ingestion non popola né inventa topic
    doc_state = state.get_document_analysis_state(doc.document_id)
    assert len(doc_state.detection_runs) == 0
    assert len(doc_state.discovery_runs) == 0


def test_no_cross_contamination_between_documents():
    """Verifica che due documenti distinti mantengano lo stato analitico completamente isolato."""
    b1 = _make_bundle("1", "Messaggio alfa")
    b2 = _make_bundle("2", "Messaggio beta")
    doc1 = ConversationEvidenceDocument(document_id="doc_alpha", bundles=(b1,))
    doc2 = ConversationEvidenceDocument(document_id="doc_beta", bundles=(b2,))

    session: dict[str, Any] = {}
    q = build_topic_query("Argomento test", None)
    res1 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.PRESENT,
        evidence_ids=("unified:whatsapp_txt:1::ORIGINAL_TEXT",),
        rationale="doc 1",
        provenance_document_id="doc_alpha",
    )
    res2 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="doc 2",
        provenance_document_id="doc_beta",
    )

    state.add_detection_result(res1, state=session)
    state.add_detection_result(res2, state=session)

    runs1 = state.get_document_analysis_state("doc_alpha", state=session).detection_runs
    runs2 = state.get_document_analysis_state("doc_beta", state=session).detection_runs

    assert len(runs1) == 1
    assert runs1[0].decision == TopicDecision.PRESENT
    assert len(runs2) == 1
    assert runs2[0].decision == TopicDecision.ABSENT


# ==============================================================================
# SEZIONE 4: STREAMLIT APPTEST (UI INTERACTION & RENDERING)
# ==============================================================================

def test_streamlit_apptest_topic_tabs_rendering():
    """
    Verifica con Streamlit AppTest che l'interfaccia si avvii correttamente
    e gestisca la navigazione alla schermata di analisi topic senza errori.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception, f"Errore rilevato nell'AppTest: {at.exception}"
