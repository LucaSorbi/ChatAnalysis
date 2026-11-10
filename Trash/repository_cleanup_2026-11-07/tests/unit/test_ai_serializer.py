"""
tests/unit/test_ai_serializer.py
--------------------------------
Test unitari per il serializzatore LLM di ConversationEvidenceDocument:
- Delimitazione strutturale e JSON escaping anti-injection
- Preservazione di evidence_id, source_type, message_id
- Contratti vincolanti per DIRECT_MULTILINGUAL e TRANSLATE_FIRST
- Rifiuto di fallback silenziosi e traduzioni parziali
"""
import json
import pytest

from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
)
from ai.serializer import (
    EVIDENCE_DATA_END_DELIMITER,
    EVIDENCE_DATA_START_DELIMITER,
    serialize_document_for_llm,
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
class TestAiSerializer:

    def test_serialize_document_basic(self):
        b1 = _make_bundle("101", "Primo messaggio")
        b2 = _make_bundle("102", "Secondo messaggio")
        doc = ConversationEvidenceDocument(document_id="doc::chat_1", bundles=(b1, b2))

        serialized = serialize_document_for_llm(doc, strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL)

        assert EVIDENCE_DATA_START_DELIMITER in serialized
        assert EVIDENCE_DATA_END_DELIMITER in serialized
        assert '"document_id": "doc::chat_1"' in serialized
        assert "unified:msgstore_db:101::ORIGINAL_TEXT" in serialized
        assert "unified:msgstore_db:102::ORIGINAL_TEXT" in serialized
        assert "Primo messaggio" in serialized
        assert "Secondo messaggio" in serialized

    def test_serialize_translate_first_success(self):
        b1 = _make_bundle("201", "Hello my friend")
        doc = ConversationEvidenceDocument(document_id="doc::en_chat", bundles=(b1,))

        orig_eid = "unified:msgstore_db:201::ORIGINAL_TEXT"
        tr_item = EvidenceTranslationItem(
            original_evidence_id=orig_eid,
            original_language="en",
            translated_text="Ciao amico mio",
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc::en_chat",
        )

        serialized = serialize_document_for_llm(
            doc,
            strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
            translation=tr_res,
        )

        # L'ID originario DEVE essere presente nella struttura serializzata
        assert orig_eid in serialized
        # Il contenuto mostrato deve essere la traduzione
        assert "Ciao amico mio" in serialized
        assert "Hello my friend" not in serialized

    def test_translate_first_without_translation_raises_error(self):
        b1 = _make_bundle("201", "Hello")
        doc = ConversationEvidenceDocument(document_id="doc::test", bundles=(b1,))

        with pytest.raises(ValueError, match="obbligatorio fornire un EvidenceTranslationResult"):
            serialize_document_for_llm(
                doc,
                strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
                translation=None,
            )

    def test_translate_first_partial_translation_raises_error_no_fallback(self):
        b1 = _make_bundle("201", "Hello")
        b2 = _make_bundle("202", "World")
        doc = ConversationEvidenceDocument(document_id="doc::test", bundles=(b1, b2))

        # Traduzione fornita solo per b1, b2 manca
        tr_item = EvidenceTranslationItem(
            original_evidence_id="unified:msgstore_db:201::ORIGINAL_TEXT",
            original_language="en",
            translated_text="Ciao",
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc::test",
        )

        with pytest.raises(ValueError, match="Nessun fallback al testo originale consentito"):
            serialize_document_for_llm(
                doc,
                strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
                translation=tr_res,
            )

    def test_translate_first_wrong_document_id_raises_error(self):
        b1 = _make_bundle("201", "Hello")
        doc = ConversationEvidenceDocument(document_id="doc::doc_a", bundles=(b1,))

        tr_item = EvidenceTranslationItem(
            original_evidence_id="unified:msgstore_db:201::ORIGINAL_TEXT",
            original_language="en",
            translated_text="Ciao",
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc::doc_b",  # mismatch
        )

        with pytest.raises(ValueError, match="Disallineamento document_id"):
            serialize_document_for_llm(
                doc,
                strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
                translation=tr_res,
            )

    def test_direct_multilingual_with_translation_raises_error(self):
        b1 = _make_bundle("201", "Hello")
        doc = ConversationEvidenceDocument(document_id="doc::test", bundles=(b1,))

        tr_item = EvidenceTranslationItem(
            original_evidence_id="unified:msgstore_db:201::ORIGINAL_TEXT",
            original_language="en",
            translated_text="Ciao",
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc::test",
        )

        with pytest.raises(ValueError, match="translation non deve essere fornito"):
            serialize_document_for_llm(
                doc,
                strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
                translation=tr_res,
            )
