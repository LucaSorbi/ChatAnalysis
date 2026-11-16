"""
tests/unit/test_live_windows_json_capabilities_and_fixes.py
-----------------------------------------------------------
Test di unità e adversarial specifici per la diagnosi e risoluzione
dell'incidente Windows LM Studio (25/10/2026):
- Distinzione tra INFERENCE_BACKEND_ERROR (empty grammar stack) e UNSUPPORTED_STRUCTURED_OUTPUT
- Retry operativo abilitato per INFERENCE_BACKEND_ERROR in Detection e Discovery
- Rifiuto rigoroso di allucinazioni di evidence_id (fail closed forense)
- Rifiuto di stati incoerenti (PRESENT senza prove, ABSENT con prove)
- Sanitizzazione PII nei messaggi di errore
"""

import json
import pytest
from unittest.mock import MagicMock

from ai.backend import (
    AiBackendRequestError,
    AiInvalidEvidenceCitationError,
    AiStructuredOutputError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
)
from ai.lmstudio import classify_lmstudio_http_error
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    TopicDecision,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer
from importer.models import RawRecord
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_synth_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="synth_src",
        source_path="/synth/p",
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
        source_name="synth_src",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:synth_src:{idx}",
        source_name="synth_src",
        source_record_id=idx,
        source_path="/synth/p",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


@pytest.mark.unit
def test_classify_empty_grammar_stack_as_inference_backend_error():
    """
    Verifica che l'errore di stream llama.cpp 'Unexpected empty grammar stack'
    venga classificato come INFERENCE_BACKEND_ERROR e NON come UNSUPPORTED_STRUCTURED_OUTPUT.
    """
    raw_body = b'{"error": {"message": "Engine protocol predict stream returned an error: {\\"code\\":500,\\"message\\":\\"got exception: Unexpected empty grammar stack after accepting piece: G (38)\\",\\"type\\":\\"server_error\\"}"}}'
    err = classify_lmstudio_http_error(400, "Bad Request", raw_body)
    assert err.error_category == "INFERENCE_BACKEND_ERROR"
    assert "motore di inferenza locale" in err.safe_reason
    assert "stream/grammar stack error" in err.safe_reason
    # Non deve essere confuso con backend non supportante JSON schema
    assert "Il backend non accetta il formato JSON strutturato richiesto" not in err.safe_reason


@pytest.mark.unit
def test_classify_genuine_unsupported_schema_syntax():
    """
    Verifica che un reale rifiuto di schema JSON venga ancora classificato come UNSUPPORTED_STRUCTURED_OUTPUT.
    """
    raw_body = b'{"error": {"message": "Invalid schema: $defs is not supported in json_schema response_format"}}'
    err = classify_lmstudio_http_error(400, "Bad Request", raw_body)
    assert err.error_category == "UNSUPPORTED_STRUCTURED_OUTPUT"
    assert "Il backend non accetta il formato JSON strutturato richiesto" in err.safe_reason


@pytest.mark.unit
def test_detection_retries_on_inference_backend_error():
    """
    Verifica che un errore INFERENCE_BACKEND_ERROR (transitorio del motore)
    permetta il retry operativo invece di fallire immediatamente a vuoto.
    """
    doc = ConversationEvidenceDocument(
        document_id="doc_retry",
        bundles=(_make_synth_bundle("1", "Parliamo del progetto alfa."),),
    )
    topic = TopicQuery(topic_id="top1", label="Progetto Alfa", description="Discussione progetto alfa")

    client = MagicMock()
    client.is_equivalent_model.return_value = True

    # Tentativo 1: INFERENCE_BACKEND_ERROR (simula crash stack GBNF)
    # Tentativo 2: Successo con risposta valida
    transient_err = AiBackendRequestError(
        message="Stream grammar stack error",
        status_code=400,
        error_category="INFERENCE_BACKEND_ERROR",
        safe_reason="Errore motore inferenza",
        technical_details="HTTP 400 [INFERENCE_BACKEND_ERROR]",
        model_id="qwen2.5-7b-instruct",
    )
    valid_resp = LlmCompletionResponse(
        content='{"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "Menziona progetto alfa."}',
        model="qwen2.5-7b-instruct",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
    )

    client.chat_completion.side_effect = [transient_err, valid_resp]

    analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)
    res = analyzer.detect_topic(doc, topic, strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL)

    assert res.decision == TopicDecision.PRESENT
    assert len(res.evidence_ids) == 1
    assert client.chat_completion.call_count == 2
    assert res.metadata.get("retries", 0) >= 1


@pytest.mark.unit
def test_detection_fails_closed_on_unsupported_structured_output():
    """
    Verifica che UNSUPPORTED_STRUCTURED_OUTPUT fallisca immediatamente chiuso
    senza retry inutili e senza downgrade a testo libero.
    """
    doc = ConversationEvidenceDocument(
        document_id="doc_fatal",
        bundles=(_make_synth_bundle("1", "Test content"),),
    )
    topic = TopicQuery(topic_id="top1", label="Test", description="Desc")

    client = MagicMock()
    client.is_equivalent_model.return_value = True

    fatal_err = AiBackendRequestError(
        message="Format not supported",
        status_code=400,
        error_category="UNSUPPORTED_STRUCTURED_OUTPUT",
        safe_reason="Il backend non accetta il formato JSON strutturato",
        technical_details="HTTP 400 [UNSUPPORTED_STRUCTURED_OUTPUT]",
        model_id="qwen2.5-7b-instruct",
    )
    client.chat_completion.side_effect = fatal_err

    analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)
    with pytest.raises(AiBackendRequestError) as exc_info:
        analyzer.detect_topic(doc, topic)

    assert exc_info.value.error_category == "UNSUPPORTED_STRUCTURED_OUTPUT"
    # Un solo tentativo: nessun loop inutile
    assert client.chat_completion.call_count == 1


@pytest.mark.unit
def test_adversarial_detection_rejects_hallucinated_evidence():
    """
    Verifica che il client forense rigetti categoricamente evidenze inventate dal modello.
    """
    doc = ConversationEvidenceDocument(
        document_id="doc_hallucination",
        bundles=(_make_synth_bundle("1", "Testo reale."),),
    )
    topic = TopicQuery(topic_id="top1", label="Test", description="Desc")

    client = MagicMock()
    client.is_equivalent_model.return_value = True

    # Il modello cita E999 che non esiste
    hallucinated_resp = LlmCompletionResponse(
        content='{"decision": "PRESENT", "evidence_ids": ["E999"], "rationale": "Inventato."}',
        model="qwen2.5-7b-instruct",
    )
    client.chat_completion.return_value = hallucinated_resp

    analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)
    with pytest.raises(AiInvalidEvidenceCitationError):
        analyzer.detect_topic(doc, topic)


@pytest.mark.unit
def test_adversarial_detection_rejects_incoherent_present_absent():
    """
    Verifica che decisioni PRESENT senza prove o ABSENT con prove vengano respinte.
    """
    doc = ConversationEvidenceDocument(
        document_id="doc_incoherent",
        bundles=(_make_synth_bundle("1", "Testo."),),
    )
    topic = TopicQuery(topic_id="top1", label="Test", description="Desc")

    client = MagicMock()
    client.is_equivalent_model.return_value = True

    # 1. PRESENT senza evidence_ids -> AiStructuredOutputError
    client.chat_completion.return_value = LlmCompletionResponse(
        content='{"decision": "PRESENT", "evidence_ids": [], "rationale": "Presente ma senza prove."}',
        model="qwen2.5-7b-instruct",
    )
    analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen2.5-7b-instruct", operational_mode=True)
    with pytest.raises(AiStructuredOutputError):
        analyzer.detect_topic(doc, topic)

    # 2. ABSENT con evidence_ids -> AiStructuredOutputError
    client.chat_completion.return_value = LlmCompletionResponse(
        content='{"decision": "ABSENT", "evidence_ids": ["E001"], "rationale": "Assente ma con prove."}',
        model="qwen2.5-7b-instruct",
    )
    with pytest.raises(AiStructuredOutputError):
        analyzer.detect_topic(doc, topic)
