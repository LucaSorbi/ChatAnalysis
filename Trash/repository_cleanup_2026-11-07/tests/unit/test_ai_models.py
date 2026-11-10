"""
tests/unit/test_ai_models.py
----------------------------
Test unitari per i modelli immutabili del package ai:
- Immutabilità profonda e validazione costruttori
- ConversationEvidenceDocument: unicità evidence_id, source_name checking, scope
- TopicQuery, TopicDetectionResult (invarianti PRESENT, ABSENT, duplicati)
- DiscoveredTopic, EvidenceTranslationResult
"""
from dataclasses import FrozenInstanceError
import pytest

from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
    ExperimentModelSpec,
    ModelFamily,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_sample_bundle(idx: str = "1", text: str = "Testo di prova", source: str = "msgstore_db") -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name=source,
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
        source_name=source,
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:{source}:{idx}",
        source_name=source,
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
class TestAiModels:

    def test_conversation_evidence_document_creation_and_immutability(self):
        b1 = _create_sample_bundle("1", "Messaggio 1")
        b2 = _create_sample_bundle("2", "Messaggio 2")
        doc = ConversationEvidenceDocument(
            document_id="doc::test::1",
            bundles=(b1, b2),
            chat_id="chat_1",
            source_name="msgstore_db",
            metadata={"source": "test"},
        )

        assert doc.document_id == "doc::test::1"
        assert doc.message_count == 2
        assert doc.evidence_count == 2
        assert len(doc.all_evidence_sections) == 2
        assert doc.all_evidence_sections[0].text == "Messaggio 1"
        assert doc.all_evidence_sections[1].text == "Messaggio 2"

        with pytest.raises(FrozenInstanceError):
            doc.document_id = "altro"  # type: ignore[misc]

    def test_conversation_evidence_document_invalid_inputs(self):
        with pytest.raises(ValueError, match="document_id deve essere una stringa non vuota"):
            ConversationEvidenceDocument(document_id="", bundles=())

        with pytest.raises(ValueError, match="MessageEvidenceBundle"):
            ConversationEvidenceDocument(document_id="valid", bundles=("not_a_bundle",))  # type: ignore[arg-type]

    def test_conversation_evidence_document_evidence_id_collision_rejected(self):
        # Due bundle con lo stesso message_id generano lo stesso evidence_id
        b1 = _create_sample_bundle("1", "Messaggio 1")
        b2 = _create_sample_bundle("1", "Messaggio 1 duplicato")
        with pytest.raises(ValueError, match="Rilevata collisione di evidence_id"):
            ConversationEvidenceDocument(
                document_id="doc::collision",
                bundles=(b1, b2),
            )

    def test_conversation_evidence_document_source_scope_mismatch_rejected(self):
        b1 = _create_sample_bundle("1", "Messaggio WhatsApp", source="msgstore_db")
        b2 = _create_sample_bundle("2", "Messaggio Cellebrite", source="cellebrite_csv")
        with pytest.raises(ValueError, match="Disallineamento source_name"):
            ConversationEvidenceDocument(
                document_id="doc::mismatch",
                bundles=(b1, b2),
                source_name="msgstore_db",
            )

    def test_topic_query_validation(self):
        query = TopicQuery(
            topic_id="top_1",
            label="Viaggi",
            description="Pianificazione viaggi",
        )
        assert query.topic_id == "top_1"
        assert query.label == "Viaggi"

        with pytest.raises(ValueError, match="topic_id"):
            TopicQuery(topic_id="", label="L", description="D")

    def test_topic_detection_result_invariants(self):
        query = TopicQuery(topic_id="t1", label="L", description="D")

        # PRESENT senza evidence_ids deve fallire
        with pytest.raises(ValueError, match="deve citare almeno un evidence_id"):
            TopicDetectionResult(
                topic=query,
                decision=TopicDecision.PRESENT,
                evidence_ids=(),
                rationale="Spiegazione",
                provenance_document_id="doc1",
            )

        # ABSENT con evidence_ids deve fallire
        with pytest.raises(ValueError, match="deve avere evidence_ids rigorosamente vuoto"):
            TopicDetectionResult(
                topic=query,
                decision=TopicDecision.ABSENT,
                evidence_ids=("eid1",),
                rationale="Spiegazione",
                provenance_document_id="doc1",
            )

        # Duplicati in evidence_ids devono fallire
        with pytest.raises(ValueError, match="identificatori duplicati"):
            TopicDetectionResult(
                topic=query,
                decision=TopicDecision.PRESENT,
                evidence_ids=("eid1", "eid1"),
                rationale="Spiegazione",
                provenance_document_id="doc1",
            )

        # Rationale vuoto deve fallire
        with pytest.raises(ValueError, match="rationale deve essere una stringa non vuota"):
            TopicDetectionResult(
                topic=query,
                decision=TopicDecision.ABSENT,
                evidence_ids=(),
                rationale="   ",
                provenance_document_id="doc1",
            )

        # Casi validi
        res_pres = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid1",),
            rationale="Trovato",
            provenance_document_id="doc1",
        )
        assert res_pres.decision == TopicDecision.PRESENT

        res_abs = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente",
            provenance_document_id="doc1",
        )
        assert res_abs.decision == TopicDecision.ABSENT

    def test_discovered_topic_requires_unique_evidence_ids(self):
        with pytest.raises(ValueError, match="almeno un evidence_id valido"):
            DiscoveredTopic(
                label="Tema",
                short_description="Descrizione",
                evidence_ids=(),
            )

        with pytest.raises(ValueError, match="evidence_ids duplicati"):
            DiscoveredTopic(
                label="Tema",
                short_description="Descrizione",
                evidence_ids=("eid1", "eid1"),
            )

        top = DiscoveredTopic(
            label="Tema",
            short_description="Descrizione",
            evidence_ids=("eid1", "eid2"),
        )
        assert top.label == "Tema"
        assert len(top.evidence_ids) == 2

    def test_experiment_model_spec(self):
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-7b-instruct",
            quantization="Q4_K_M",
        )
        assert spec.family == ModelFamily.QWEN
        assert spec.model_id == "qwen-2.5-7b-instruct"

    def test_evidence_translation_result(self):
        item = EvidenceTranslationItem(
            original_evidence_id="eid1",
            original_language="en",
            translated_text="Testo tradotto",
            target_language="it",
        )
        res = EvidenceTranslationResult(
            translations=(item,),
            provenance_document_id="doc1",
        )
        assert res.get_translation("eid1") == "Testo tradotto"
        assert res.get_translation("non_esiste") is None

        # Duplicati nella stessa EvidenceTranslationResult
        with pytest.raises(ValueError, match="traduzione duplicata"):
            EvidenceTranslationResult(
                translations=(item, item),
                provenance_document_id="doc1",
            )
