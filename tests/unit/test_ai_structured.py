"""
tests/unit/test_ai_structured.py
--------------------------------
Test unitari per il modulo ai/structured.py:
- Parser JSON: cattura solo JSONDecodeError, propagazione di TypeError/AttributeError
- Exact object shape: rifiuto di chiavi extra e tipi non corretti (nessuna coercizione indiscriminata)
- Topic Detection: invarianti PRESENT/ABSENT, rifiuto duplicati, validazione rationale
- Topic Discovery: divieto di silent truncation (> max_topics solleva errore), validazione chiavi
- Translation: preservazione lingua originaria, divieto di traduzioni vuote indebite, rifiuto extra/duplicati
"""
import json
import pytest

from ai.backend import AiStructuredOutputError
from ai.models import TextEvidenceSection, TopicDecision
from ai.structured import (
    parse_and_validate_json,
    validate_topic_detection_payload,
    validate_topic_discovery_payload,
    validate_translation_payload,
)
from multimodal.evidence import EvidenceSourceType


def _make_test_section(eid: str, text: str = "Test", lang: str | None = "it") -> TextEvidenceSection:
    return TextEvidenceSection(
        evidence_id=eid,
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        text=text,
        language=lang,
        message_id="msg_1",
        source_name="test_source",
        source_record_id="rec_1",
        ordinal=0,
    )


@pytest.mark.unit
class TestAiStructuredValidation:

    def test_parse_json_valid(self):
        raw = '{"key": "value"}'
        res = parse_and_validate_json(raw)
        assert res == {"key": "value"}

    def test_parse_json_markdown_wrapped(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        res = parse_and_validate_json(raw)
        assert res == {"key": "value"}

    def test_parse_json_invalid_decode_raises_structured_output_error(self):
        with pytest.raises(AiStructuredOutputError, match="non conforme a JSON valido"):
            parse_and_validate_json("{invalid json")

    def test_parse_json_non_dict_raises_structured_output_error(self):
        with pytest.raises(AiStructuredOutputError, match="deve essere un oggetto JSON"):
            parse_and_validate_json("[\"list\", \"instead\", \"of\", \"dict\"]")

    def test_topic_detection_extra_key_rejected(self):
        raw = '{"decision": "ABSENT", "evidence_ids": [], "rationale": "ok", "unexpected_field": true}'
        with pytest.raises(AiStructuredOutputError, match="chiavi extra non consentite"):
            validate_topic_detection_payload(raw, valid_evidence_ids=set())

    def test_topic_detection_wrong_type_rejected_no_coercion(self):
        # decision int invece di string
        raw = '{"decision": 123, "evidence_ids": [], "rationale": "ok"}'
        with pytest.raises(AiStructuredOutputError, match="'decision' deve essere stringa"):
            validate_topic_detection_payload(raw, valid_evidence_ids=set())

        # evidence_ids con int invece di string
        raw2 = '{"decision": "PRESENT", "evidence_ids": [456], "rationale": "ok"}'
        with pytest.raises(AiStructuredOutputError, match="non è una stringa"):
            validate_topic_detection_payload(raw2, valid_evidence_ids={"456"})

    def test_topic_detection_absent_with_evidence_rejected(self):
        raw = '{"decision": "ABSENT", "evidence_ids": ["e1"], "rationale": "assente ma con prova"}'
        with pytest.raises(AiStructuredOutputError, match="decision=ABSENT ma ha allegato evidence_ids"):
            validate_topic_detection_payload(raw, valid_evidence_ids={"e1"})

    def test_topic_detection_duplicate_evidence_ids_rejected(self):
        raw = '{"decision": "PRESENT", "evidence_ids": ["e1", "e1"], "rationale": "trovato"}'
        with pytest.raises(AiStructuredOutputError, match="elementi duplicati"):
            validate_topic_detection_payload(raw, valid_evidence_ids={"e1"})

    def test_topic_detection_empty_rationale_rejected(self):
        raw = '{"decision": "ABSENT", "evidence_ids": [], "rationale": "   "}'
        with pytest.raises(AiStructuredOutputError, match="'rationale' non può essere una stringa vuota"):
            validate_topic_detection_payload(raw, valid_evidence_ids=set())

    def test_topic_discovery_exceeding_max_topics_rejected_no_truncation(self):
        raw = """
        {
            "topics": [
                {"label": "T1", "short_description": "D1", "evidence_ids": ["e1"]},
                {"label": "T2", "short_description": "D2", "evidence_ids": ["e1"]},
                {"label": "T3", "short_description": "D3", "evidence_ids": ["e1"]}
            ]
        }
        """
        with pytest.raises(AiStructuredOutputError, match="superando il limite consentito di 2"):
            validate_topic_discovery_payload(raw, valid_evidence_ids={"e1"}, max_topics=2)

    def test_topic_discovery_duplicate_evidence_ids_in_topic_rejected(self):
        raw = """
        {
            "topics": [
                {"label": "T1", "short_description": "D1", "evidence_ids": ["e1", "e1"]}
            ]
        }
        """
        with pytest.raises(AiStructuredOutputError, match="evidence_ids duplicati"):
            validate_topic_discovery_payload(raw, valid_evidence_ids={"e1"}, max_topics=5)

    def test_translation_validation_preserves_language(self):
        sec = _make_test_section("e1", text="Hello", lang="en")
        raw = '{"translations": [{"evidence_id": "e1", "translated_text": "Ciao"}]}'

        items = validate_translation_payload(raw, expected_sections={"e1": sec}, target_language="it")
        assert len(items) == 1
        assert items[0].original_evidence_id == "e1"
        assert items[0].original_language == "en"
        assert items[0].translated_text == "Ciao"

    def test_translation_validation_rejects_empty_translation_for_non_empty_source(self):
        sec = _make_test_section("e1", text="Hello world", lang="en")
        raw = '{"translations": [{"evidence_id": "e1", "translated_text": "   "}]}'

        with pytest.raises(AiStructuredOutputError, match="Traduzione vuota non ammessa"):
            validate_translation_payload(raw, expected_sections={"e1": sec}, target_language="it")

    def test_translation_validation_allows_empty_translation_for_empty_source(self):
        sec = _make_test_section("e1", text="   ", lang="en")
        raw = '{"translations": [{"evidence_id": "e1", "translated_text": ""}]}'

        items = validate_translation_payload(raw, expected_sections={"e1": sec}, target_language="it")
        assert len(items) == 1
        assert items[0].translated_text == ""
