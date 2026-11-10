"""
tests/unit/test_ai_topic_detection.py
-------------------------------------
Test unitari per TopicDetectionAnalyzer:
- Verdetto PRESENT con citazione valida
- Verdetto ABSENT con lista vuota (e rigetto se contiene evidenze)
- Verdetto UNCERTAIN per ambiguità
- Rifiuto di evidence_id allucinati, duplicati o con tipi non corretti
- Rifiuto di chiavi extra
- Verifica Model Mismatch
- Contratti TRANSLATE_FIRST vs DIRECT_MULTILINGUAL
"""
import pytest

from ai.backend import (
    AiModelMismatchError,
    AiStructuredOutputError,
    FakeLocalLlmClient,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
    TopicDecision,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer
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


def _make_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="msgstore_db",
        source_path="/path/test",
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
        source_name="msgstore_db",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:msgstore_db:{idx}",
        source_name="msgstore_db",
        source_record_id=idx,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


@pytest.mark.unit
class TestTopicDetectionAnalyzer:

    def test_topic_present_success(self):
        b1 = _make_bundle("1", "Vado a Roma domani in treno")
        doc = ConversationEvidenceDocument(document_id="doc1", bundles=(b1,))

        eid = "unified:msgstore_db:1::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "PRESENT", "evidence_ids": ["{eid}"], "rationale": "Il messaggio menziona il viaggio a Roma."}}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="top_travel", label="Viaggi", description="Spostamenti e viaggi")

        res = analyzer.detect_topic(doc, query)

        assert res.decision == TopicDecision.PRESENT
        assert res.evidence_ids == (eid,)
        assert "Roma" in res.rationale
        assert res.provenance_document_id == "doc1"
        assert res.metadata["prompt_version"] == "topic_detection_v1"

    def test_topic_absent_success(self):
        b1 = _make_bundle("2", "Il report contabile è stato inviato")
        doc = ConversationEvidenceDocument(document_id="doc2", bundles=(b1,))

        json_resp = '{"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessun riferimento a viaggi."}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="top_travel", label="Viaggi", description="Spostamenti e viaggi")

        res = analyzer.detect_topic(doc, query)

        assert res.decision == TopicDecision.ABSENT
        assert res.evidence_ids == ()

    def test_topic_absent_with_evidence_rejected(self):
        b1 = _make_bundle("2b", "Report contabile")
        doc = ConversationEvidenceDocument(document_id="doc2b", bundles=(b1,))

        eid = "unified:msgstore_db:2b::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "ABSENT", "evidence_ids": ["{eid}"], "rationale": "Tema assente ma allego evidenza"}}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="top_travel", label="Viaggi", description="Spostamenti")

        with pytest.raises(AiStructuredOutputError, match="decision=ABSENT ma ha allegato evidence_ids"):
            analyzer.detect_topic(doc, query)

    def test_topic_uncertain_success(self):
        b1 = _make_bundle("3", "Forse ci vediamo là se riesco a partire")
        doc = ConversationEvidenceDocument(document_id="doc3", bundles=(b1,))

        eid = "unified:msgstore_db:3::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "UNCERTAIN", "evidence_ids": ["{eid}"], "rationale": "Riferimento vago a una possibile partenza."}}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="top_travel", label="Viaggi", description="Spostamenti e viaggi")

        res = analyzer.detect_topic(doc, query)

        assert res.decision == TopicDecision.UNCERTAIN
        assert res.evidence_ids == (eid,)

    def test_duplicate_evidence_ids_rejected(self):
        b1 = _make_bundle("3b", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc3b", bundles=(b1,))

        eid = "unified:msgstore_db:3b::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "PRESENT", "evidence_ids": ["{eid}", "{eid}"], "rationale": "Duplicato"}}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="elementi duplicati"):
            analyzer.detect_topic(doc, query)

    def test_extra_json_keys_rejected(self):
        b1 = _make_bundle("3c", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc3c", bundles=(b1,))

        eid = "unified:msgstore_db:3c::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "PRESENT", "evidence_ids": ["{eid}"], "rationale": "Ok", "extra_key": 123}}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="chiavi extra non consentite"):
            analyzer.detect_topic(doc, query)

    def test_wrong_type_in_evidence_ids_no_coercion(self):
        b1 = _make_bundle("3d", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc3d", bundles=(b1,))

        json_resp = '{"decision": "PRESENT", "evidence_ids": [123], "rationale": "Numero invece di stringa"}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="non è una stringa"):
            analyzer.detect_topic(doc, query)

    def test_model_mismatch_raises_ai_model_mismatch_error(self):
        b1 = _make_bundle("3e", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc3e", bundles=(b1,))

        eid = "unified:msgstore_db:3e::ORIGINAL_TEXT"
        json_resp = f'{{"decision": "PRESENT", "evidence_ids": ["{eid}"], "rationale": "Ok"}}'
        # Client che risponde con un modello diverso da quello atteso
        client = FakeLocalLlmClient(
            default_response=json_resp,
            simulate_model_mismatch="different-model-id",
        )

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiModelMismatchError, match="Model mismatch in Topic Detection"):
            analyzer.detect_topic(doc, query, model_id="expected-model-id")

    def test_hallucinated_evidence_id_rejected(self):
        b1 = _make_bundle("4", "Messaggio reale")
        doc = ConversationEvidenceDocument(document_id="doc4", bundles=(b1,))

        json_resp = '{"decision": "PRESENT", "evidence_ids": ["unified:msgstore_db:INVENTATO::ORIGINAL_TEXT"], "rationale": "Allucinazione"}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="evidence_id inesistenti o allucinati"):
            analyzer.detect_topic(doc, query)

    def test_present_without_evidence_rejected(self):
        b1 = _make_bundle("5", "Messaggio reale")
        doc = ConversationEvidenceDocument(document_id="doc5", bundles=(b1,))

        json_resp = '{"decision": "PRESENT", "evidence_ids": [], "rationale": "Manca prova"}'
        client = FakeLocalLlmClient(default_response=json_resp)

        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="decision=PRESENT ma la lista evidence_ids è vuota"):
            analyzer.detect_topic(doc, query)

    def test_malformed_json_raises_ai_structured_output_error(self):
        b1 = _make_bundle("6", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc6", bundles=(b1,))

        client = FakeLocalLlmClient(default_response="Questo non è JSON, ma testo libero")
        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="non conforme a JSON valido"):
            analyzer.detect_topic(doc, query)

    def test_invalid_decision_enum_raises_ai_structured_output_error(self):
        b1 = _make_bundle("7", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc7", bundles=(b1,))

        json_resp = '{"decision": "MAYBE", "evidence_ids": [], "rationale": "Non previsto"}'
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        with pytest.raises(AiStructuredOutputError, match="Decisione 'MAYBE' non valida"):
            analyzer.detect_topic(doc, query)

    def test_empty_document_returns_absent_without_llm_call(self):
        doc = ConversationEvidenceDocument(document_id="empty_doc", bundles=())
        client = FakeLocalLlmClient(default_response="Should not be called")
        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="L", description="D")

        res = analyzer.detect_topic(doc, query)
        assert res.decision == TopicDecision.ABSENT
        assert len(client.call_history) == 0

    def test_translate_first_integration_success(self):
        b1 = _make_bundle("8", "Hello world")
        doc = ConversationEvidenceDocument(document_id="doc8", bundles=(b1,))

        eid = "unified:msgstore_db:8::ORIGINAL_TEXT"
        tr_item = EvidenceTranslationItem(
            original_evidence_id=eid,
            original_language="en",
            translated_text="Ciao mondo",
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc8",
        )

        json_resp = f'{{"decision": "PRESENT", "evidence_ids": ["{eid}"], "rationale": "Saluto rilevato"}}'
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDetectionAnalyzer(client=client)
        query = TopicQuery(topic_id="t", label="Saluti", description="Formule di saluto")

        res = analyzer.detect_topic(
            doc,
            query,
            strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
            translation=tr_res,
        )
        assert res.decision == TopicDecision.PRESENT
        assert res.evidence_ids == (eid,)
