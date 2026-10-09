"""
tests/unit/test_ai_detection_multimodal_diagnostics.py
------------------------------------------------------
Test suite diagnostico per la verifica della "Prova del Nove" (Discovery -> Detection),
della serializzazione multimodale di tutti i source type nel prompt LLM,
della gestione rigorosa degli errori senza conversione in ABSENT,
della coerenza di provenance, e dell'isolamento dei prompt benchmark.
"""
from __future__ import annotations

import json
from typing import Any
import pytest

from ai.backend import (
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiStructuredOutputError,
    FakeLocalLlmClient,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.serializer import serialize_document_for_llm
from ai.structured import TOPIC_DETECTION_SCHEMA, TOPIC_DISCOVERY_SCHEMA
from ai.topics import (
    PROMPT_VERSION_DETECTION,
    PROMPT_VERSION_DETECTION_OPERATIONAL,
    PROMPT_VERSION_DISCOVERY,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
)
from multimodal.models import (
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    ResolvedMediaAsset,
    VisionStatus,
)
from ui.application import (
    TopicProvenanceMismatchError,
    build_topic_query,
    execute_manual_topic_detection,
    validate_discovery_provenance,
)
from unified.models import CanonicalMessageType
from ui.demo import _create_synthetic_message, build_synthetic_demo_dataset


# ==============================================================================
# 1. VERIFICARE LABEL CORRETTA DAL DISCOVERY
# ==============================================================================
def test_discovery_to_detection_label_exact():
    """Verifica che la label della TopicQuery derivi esattamente da DiscoveredTopic.label."""
    demo_doc, _, (demo_discovery,) = build_synthetic_demo_dataset()
    disc_topic = demo_discovery.topics[0]
    assert disc_topic.label == "Logistica e Ispezione Depositi"

    query = build_topic_query(label=disc_topic.label, description=disc_topic.short_description)
    assert query.label == "Logistica e Ispezione Depositi"

    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [disc_topic.evidence_ids[0]],
            "rationale": "Verificato.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(demo_doc, query)

    sent_system = client.call_history[-1]["messages"][0]["content"]
    assert "LABEL: Logistica e Ispezione Depositi" in sent_system
    assert res.topic.label == "Logistica e Ispezione Depositi"


# ==============================================================================
# 2. VERIFICARE SHORT_DESCRIPTION CORRETTA
# ==============================================================================
def test_discovery_to_detection_short_description_exact():
    """Verifica che la short_description non sia vuota o generica e sia inclusa nel prompt."""
    demo_doc, _, (demo_discovery,) = build_synthetic_demo_dataset()
    disc_topic = demo_discovery.topics[0]
    expected_desc = "Ispezione visiva del magazzino merci con casse sigillate e sicurezza"
    assert disc_topic.short_description == expected_desc

    query = build_topic_query(label=disc_topic.label, description=disc_topic.short_description)
    assert query.description == expected_desc

    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [disc_topic.evidence_ids[0]],
            "rationale": "Verificato.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    analyzer.detect_topic(demo_doc, query)

    sent_system = client.call_history[-1]["messages"][0]["content"]
    assert f"DESCRIPTION: {expected_desc}" in sent_system


# ==============================================================================
# 3. VERIFICARE STESSO DOCUMENT_ID (PROVENANCE)
# ==============================================================================
def test_discovery_provenance_document_id_matches_active_document():
    """Verifica che la provenance del Discovery corrisponda al document_id attivo."""
    demo_doc, _, (demo_discovery,) = build_synthetic_demo_dataset()
    assert demo_discovery.provenance_document_id == demo_doc.document_id

    # Corrispondenza: nessuna eccezione
    validate_discovery_provenance(
        discovery_provenance_id=demo_discovery.provenance_document_id,
        active_document_id=demo_doc.document_id,
    )

    # Disallineamento: solleva TopicProvenanceMismatchError bloccante
    with pytest.raises(TopicProvenanceMismatchError, match="Discrepanza di provenienza"):
        validate_discovery_provenance(
            discovery_provenance_id="doc::other_chat_999",
            active_document_id=demo_doc.document_id,
        )


# ==============================================================================
# 4. VERIFICARE VISION_DESCRIPTION PRESENTE NEL PROMPT
# ==============================================================================
def test_vision_description_serialized_and_present_in_prompt():
    """Verifica che msg::5::VISION_DESCRIPTION e il contenuto del magazzino siano presenti nel prompt."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    serialized = serialize_document_for_llm(demo_doc)

    assert "msg::5::VISION_DESCRIPTION" in serialized
    assert "magazzino industriale" in serialized
    assert "casse etichettate" in serialized
    assert "porta di sicurezza blindata" in serialized

    # Verifica passaggio nei messaggi LLM
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
            "rationale": "Evidenza fotografica magazzino.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(
        label="Logistica e Ispezione Depositi",
        description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
    )
    analyzer.detect_topic(demo_doc, query)

    user_msg = client.call_history[-1]["messages"][1]["content"]
    assert "msg::5::VISION_DESCRIPTION" in user_msg
    assert "magazzino industriale" in user_msg


# ==============================================================================
# 5. VERIFICARE VISION_OBSERVATION PRESENTE NEL PROMPT E DETECTABILE
# ==============================================================================
def test_vision_observation_serialized_and_detectable():
    """Verifica che VISION_OBSERVATION sia presente nel prompt e permetta verdetto PRESENT se citata."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    serialized = serialize_document_for_llm(demo_doc)

    assert "msg::5::VISION_OBSERVATION::0" in serialized
    assert "Casse di legno sigillate" in serialized
    assert "msg::5::VISION_OBSERVATION::1" in serialized
    assert "Porta di sicurezza blindata" in serialized

    eid = "msg::5::VISION_OBSERVATION::0"
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Osservazione visiva delle casse sigillate.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Casse Sigillate", description="Stoccaggio casse")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (eid,)


# ==============================================================================
# 6. VERIFICARE STT_TRANSCRIPTION PRESENTE NEL PROMPT E DETECTABILE
# ==============================================================================
def test_stt_transcription_serialized_and_detectable():
    """Verifica che STT_TRANSCRIPTION sia presente nel prompt e rilevabile dalla Detection."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    serialized = serialize_document_for_llm(demo_doc)

    assert "msg::6::STT_TRANSCRIPTION" in serialized
    assert "avvocato Rossi" in serialized

    eid = "msg::6::STT_TRANSCRIPTION"
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Trascrizione vocale clausola riservatezza con avvocato.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Consulenza Legale", description="Incontro con avvocato")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (eid,)


# ==============================================================================
# 7. VERIFICARE OCR_TEXT PRESENTE NEL PROMPT E DETECTABILE
# ==============================================================================
def test_ocr_text_serialized_and_detectable():
    """Verifica che una sezione OCR_TEXT sia correttamente inclusa nel prompt e rilevabile."""
    msg = _create_custom_bundle_with_ocr("msg::ocr_1", "FATTURA N. 9842 - IMPORTO SALDATO")
    doc = ConversationEvidenceDocument(document_id="doc::test_ocr", bundles=(msg,))

    serialized = serialize_document_for_llm(doc)
    assert "msg::ocr_1::OCR_TEXT" in serialized
    assert "FATTURA N. 9842" in serialized

    eid = "msg::ocr_1::OCR_TEXT"
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Rilevata fattura saldata tramite OCR immagine.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Fatturazione", description="Fatture e pagamenti")
    res = analyzer.detect_topic(doc, query)

    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (eid,)


# ==============================================================================
# 8. VERIFICARE ORIGINAL_TEXT PRESENTE NEL PROMPT E DETECTABILE
# ==============================================================================
def test_original_text_serialized_and_detectable():
    """Verifica che ORIGINAL_TEXT sia serializzato e supporti la Detection."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    serialized = serialize_document_for_llm(demo_doc)

    assert "msg::1::ORIGINAL_TEXT" in serialized
    assert "incontro preliminare" in serialized

    eid = "msg::1::ORIGINAL_TEXT"
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Messaggio di testo originario su incontro preliminare per accordo.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Pianificazione Incontri", description="Incontro preliminare")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (eid,)


# ==============================================================================
# 9. ERRORE PARSER NON CONVERTITO IN ABSENT
# ==============================================================================
def test_parser_error_never_converted_to_absent():
    """Verifica che errori di JSON malformato o schema non vengano mai convertiti in ABSENT."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    client = FakeLocalLlmClient(
        default_response="Questo testo NON è un JSON valido",
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Test", description="Test")

    with pytest.raises(AiStructuredOutputError):
        analyzer.detect_topic(demo_doc, query)


# ==============================================================================
# 10. EVIDENCE ID INVALID NON CONVERTITO IN ABSENT
# ==============================================================================
def test_invalid_evidence_id_never_converted_to_absent():
    """Verifica che la citazione di un evidence_id inesistente sollevi eccezione e NON ABSENT."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["msg::999::ALLUCINATO"],
            "rationale": "Evidenza inventata.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Test", description="Test")

    with pytest.raises(AiInvalidEvidenceCitationError):
        analyzer.detect_topic(demo_doc, query)


# ==============================================================================
# 11. RESPONSE PRESENT RESTA PRESENT
# ==============================================================================
def test_response_present_remains_present():
    """Verifica che una risposta valida PRESENT non venga alterata o retrocessa."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    eid = "msg::5::VISION_DESCRIPTION"
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Presente con riscontro oggettivo.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Logistica", description="Deposito")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (eid,)
    assert res.rationale == "Presente con riscontro oggettivo."


# ==============================================================================
# 12. RESPONSE ABSENT VALIDA RESTA ABSENT
# ==============================================================================
def test_response_absent_valid_remains_absent():
    """Verifica che una risposta legittimamente ABSENT senza evidenze resti ABSENT."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "Nessuna traccia del topic nel documento.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    query = build_topic_query(label="Traffico Armi", description="Commercio illecito di armi")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.decision == TopicDecision.ABSENT
    assert res.evidence_ids == ()
    assert "Nessuna traccia" in res.rationale


# ==============================================================================
# 13. MISMATCH MODELLO INTERCETTATO
# ==============================================================================
def test_model_mismatch_intercepted():
    """Verifica che un model mismatch tra richiesto e restituito sollevi AiModelMismatchError."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
            "rationale": "Ok",
        }),
        model_name="llama-3.2-3b",
        simulate_model_mismatch="llama-3.2-3b",
    )
    analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct")
    query = build_topic_query(label="Test", description="Test")

    with pytest.raises(AiModelMismatchError, match="Model mismatch in Topic Detection"):
        analyzer.detect_topic(demo_doc, query, model_id="qwen2.5-7b-instruct")


# ==============================================================================
# 14. DUE INFERENZE DISCOVERY/DETECTION REALMENTE SEPARATE
# ==============================================================================
def test_discovery_and_detection_are_strictly_separate_inferences():
    """Verifica che Discovery e Detection abbiano prompt, schemi e chiamate indipendenti."""
    assert TOPIC_DETECTION_SCHEMA != TOPIC_DISCOVERY_SCHEMA
    assert PROMPT_VERSION_DETECTION != PROMPT_VERSION_DISCOVERY

    demo_doc, _, _ = build_synthetic_demo_dataset()

    client_disc = FakeLocalLlmClient(
        default_response=json.dumps({
            "topics": [
                {
                    "label": "Argomento Scoperto A",
                    "short_description": "Descrizione A",
                    "evidence_ids": ["msg::1::ORIGINAL_TEXT"],
                }
            ]
        }),
        model_name="qwen2.5-7b-instruct",
    )
    disc_analyzer = TopicDiscoveryAnalyzer(client=client_disc)
    disc_res = disc_analyzer.discover_topics(demo_doc)

    client_det = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["msg::1::ORIGINAL_TEXT"],
            "rationale": "Verifica mirata indipendente.",
        }),
        model_name="qwen2.5-7b-instruct",
    )
    det_analyzer = TopicDetectionAnalyzer(client=client_det, operational_mode=True)
    det_query = build_topic_query(
        label=disc_res.topics[0].label,
        description=disc_res.topics[0].short_description,
    )
    det_res = det_analyzer.detect_topic(demo_doc, det_query)

    # Verifica che le due chiamate siano distinte e separate
    assert len(client_disc.call_history) == 1
    assert len(client_det.call_history) == 1
    assert client_disc.call_history[0]["schema"] == TOPIC_DISCOVERY_SCHEMA
    assert client_det.call_history[0]["schema"] == TOPIC_DETECTION_SCHEMA
    assert det_res.rationale == "Verifica mirata indipendente."


# ==============================================================================
# 15. BENCHMARK COMPLETAMENTE INVARIATO
# ==============================================================================
def test_benchmark_remains_untouched_and_identical():
    """Verifica che TopicDetectionAnalyzer con operational_mode=False mantenga il prompt standard di benchmark."""
    demo_doc, _, _ = build_synthetic_demo_dataset()
    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "Benchmark standard.",
        }),
        model_name="qwen2.5-7b-instruct",
    )

    # Default operational_mode=False (usato da benchmark e pipeline standard)
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=False)
    query = build_topic_query(label="Benchmark Topic", description="Desc")
    res = analyzer.detect_topic(demo_doc, query)

    assert res.metadata["prompt_version"] == PROMPT_VERSION_DETECTION
    assert res.metadata["prompt_version"] == "topic_detection_v1"
    assert res.metadata["operational_mode"] is False

    system_prompt = client.call_history[-1]["messages"][0]["content"]
    assert "TASK: SPECIFIC TOPIC DETECTION (Version: topic_detection_v1)" in system_prompt
    assert "MULTIMODAL SEMANTIC DIRECTIVE" not in system_prompt


# ==============================================================================
# TEST CASO DI CONTROLLO OBBLIGATORIO (DEMO DATASET)
# ==============================================================================
def test_mandatory_control_case_demo_dataset():
    """
    Caso di controllo obbligatorio:
    Label: 'Logistica e Ispezione Depositi'
    Description: 'Ispezione visiva del magazzino merci con casse sigillate e sicurezza'
    Discovery evidence: 'msg::5::VISION_DESCRIPTION'
    """
    demo_doc, _, (demo_discovery,) = build_synthetic_demo_dataset()
    target_topic = next(t for t in demo_discovery.topics if t.label == "Logistica e Ispezione Depositi")

    assert target_topic.label == "Logistica e Ispezione Depositi"
    assert target_topic.short_description == "Ispezione visiva del magazzino merci con casse sigillate e sicurezza"
    assert "msg::5::VISION_DESCRIPTION" in target_topic.evidence_ids

    # Verifica presenza fisica dell'evidenza nel documento
    sec = next(s for s in demo_doc.all_evidence_sections if s.evidence_id == "msg::5::VISION_DESCRIPTION")
    assert "magazzino industriale" in sec.text
    assert "casse etichettate" in sec.text
    assert "porta di sicurezza blindata" in sec.text

    # Verifica che la UI application layer crei la query corretta e invii al prompt l'evidenza
    query = build_topic_query(
        label=target_topic.label,
        description=target_topic.short_description,
    )

    client = FakeLocalLlmClient(
        default_response=json.dumps({
            "decision": "PRESENT",
            "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
            "rationale": "Riscontro visivo confermato nel magazzino merci con casse e porta blindata.",
        }),
        model_name="qwen2.5-7b-instruct",
        models=("qwen2.5-7b-instruct",),
    )

    det_res = execute_manual_topic_detection(
        document=demo_doc,
        topic=query,
        client=client,
        model_id="qwen2.5-7b-instruct",
        operational_mode=True,
    )

    assert det_res.decision == TopicDecision.PRESENT
    assert det_res.evidence_ids == ("msg::5::VISION_DESCRIPTION",)
    assert det_res.provenance_document_id == demo_doc.document_id
    assert det_res.metadata["operational_mode"] is True
    assert det_res.metadata["prompt_version"] == PROMPT_VERSION_DETECTION_OPERATIONAL
    assert det_res.metadata["raw_response"] is not None

    # Verifica contenuto prompt
    user_payload = client.call_history[-1]["messages"][1]["content"]
    assert "msg::5::VISION_DESCRIPTION" in user_payload
    assert "magazzino industriale" in user_payload


# ==============================================================================
# Helper per creazione bundle con OCR
# ==============================================================================
def _create_custom_bundle_with_ocr(msg_id: str, ocr_text: str) -> MessageEvidenceBundle:
    msg = _create_synthetic_message(
        msg_id=msg_id,
        source_record_id=msg_id,
        text_content="Invio foto allegata",
        message_type=CanonicalMessageType.IMAGE,
        media_reference="test.jpg",
    )
    asset = ResolvedMediaAsset(
        message_id=msg_id,
        source_name=msg.source_name,
        source_record_id=msg_id,
        raw_reference="test.jpg",
        resolved_path="/path/test.jpg",
        media_kind=MediaKind.IMAGE,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=1000,
        sha256="91a82c478104d9b8e72c8194a0293847e6182903847bca8291048b29c8192039",
        provenance_message=msg,
    )
    ocr = ImageOcrResult(
        message_id=msg_id,
        source_name=msg.source_name,
        source_record_id=msg_id,
        status=OcrStatus.SUCCESS,
        full_text=ocr_text,
        engine="tesseract",
        provenance_asset=asset,
    )
    return MessageEvidenceBundle(message=msg, image_ocr=ocr)
