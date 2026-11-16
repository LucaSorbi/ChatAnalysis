"""
tests/unit/test_ai_translation.py
---------------------------------
Test unitari per EvidenceTranslator (strategia TRANSLATE_FIRST):
- Traduzione strutturata delle sezioni di evidenza
- Preservazione dell'original_language della sezione originaria
- Rifiuto di risposte con evidenze omesse, duplicate, extra o con traduzione vuota indebita
- Rifiuto di chiavi extra
- Model mismatch checking
"""
import pytest

from ai.backend import (
    AiModelMismatchError,
    AiStructuredOutputError,
    FakeLocalLlmClient,
)
from ai.models import ConversationEvidenceDocument
from ai.translation import EvidenceTranslator
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle
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
class TestEvidenceTranslator:

    def test_translation_success_and_language_preservation(self):
        b1 = _make_bundle("1", "Where are you going?")
        doc = ConversationEvidenceDocument(document_id="doc_trans", bundles=(b1,))
        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"

        json_resp = f"""
        {{
            "translations": [
                {{
                    "evidence_id": "{eid1}",
                    "translated_text": "Dove stai andando?"
                }}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        translator = EvidenceTranslator(client=client)

        res = translator.translate_document(doc, target_language="it")

        assert len(res.translations) == 1
        assert res.translations[0].original_evidence_id == eid1
        assert res.translations[0].translated_text == "Dove stai andando?"
        assert res.translations[0].target_language == "it"
        assert res.get_translation(eid1) == "Dove stai andando?"

    def test_translation_empty_text_rejected_if_source_non_empty(self):
        b1 = _make_bundle("1", "Where are you going?")
        doc = ConversationEvidenceDocument(document_id="doc_empty_tr", bundles=(b1,))
        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"

        json_resp = f"""
        {{
            "translations": [
                {{
                    "evidence_id": "{eid1}",
                    "translated_text": "   "
                }}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        translator = EvidenceTranslator(client=client)

        with pytest.raises(AiStructuredOutputError, match="Traduzione vuota non ammessa"):
            translator.translate_document(doc)

    def test_translation_extra_keys_rejected(self):
        b1 = _make_bundle("1", "Where are you going?")
        doc = ConversationEvidenceDocument(document_id="doc_extra_tr", bundles=(b1,))
        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"

        json_resp = f"""
        {{
            "translations": [
                {{
                    "evidence_id": "{eid1}",
                    "translated_text": "Dove vai?",
                    "extra_key": "bad"
                }}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        translator = EvidenceTranslator(client=client)

        with pytest.raises(AiStructuredOutputError, match="chiavi non conformi"):
            translator.translate_document(doc)

    def test_translation_missing_evidence_rejected(self):
        b1 = _make_bundle("1", "Text 1")
        b2 = _make_bundle("2", "Text 2")
        doc = ConversationEvidenceDocument(document_id="doc_missing", bundles=(b1, b2))

        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"
        json_resp = f"""
        {{
            "translations": [
                {{"evidence_id": "{eid1}", "translated_text": "Testo 1 tradotto"}}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        translator = EvidenceTranslator(client=client)

        with pytest.raises(AiStructuredOutputError, match="Evidenze mancanti"):
            translator.translate_document(doc)

    def test_translation_duplicate_evidence_rejected(self):
        b1 = _make_bundle("1", "Text 1")
        doc = ConversationEvidenceDocument(document_id="doc_dup", bundles=(b1,))

        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"
        json_resp = f"""
        {{
            "translations": [
                {{"evidence_id": "{eid1}", "translated_text": "Trad 1"}},
                {{"evidence_id": "{eid1}", "translated_text": "Trad 2"}}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        translator = EvidenceTranslator(client=client)

        with pytest.raises(AiStructuredOutputError, match="Traduzione duplicata"):
            translator.translate_document(doc)

    def test_translation_model_mismatch_detected(self):
        b1 = _make_bundle("1", "Text 1")
        doc = ConversationEvidenceDocument(document_id="doc_mism", bundles=(b1,))

        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"
        json_resp = f"""
        {{
            "translations": [
                {{"evidence_id": "{eid1}", "translated_text": "Trad 1"}}
            ]
        }}
        """
        client = FakeLocalLlmClient(
            default_response=json_resp,
            simulate_model_mismatch="wrong-model",
        )
        translator = EvidenceTranslator(client=client)

        with pytest.raises(AiModelMismatchError, match="Model mismatch in Translation"):
            translator.translate_document(doc, model_id="expected-model")

    def test_empty_document_returns_empty_translation(self):
        doc = ConversationEvidenceDocument(document_id="empty_doc", bundles=())
        client = FakeLocalLlmClient(default_response="Should not be called")
        translator = EvidenceTranslator(client=client)

        res = translator.translate_document(doc)
        assert len(res.translations) == 0
