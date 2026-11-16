"""
tests/unit/test_ai_topic_discovery.py
------------------------------------
Test unitari per TopicDiscoveryAnalyzer:
- Estrazione open topics con supporto evidence_ids valido
- Rifiuto di silent truncation quando topics > max_topics (solleva AiStructuredOutputError)
- Rifiuto di topic privi di citazione, con citazioni duplicate o allucinate
- Gestione documento vuoto
"""
import pytest

from ai.backend import AiStructuredOutputError, FakeLocalLlmClient
from ai.models import ConversationEvidenceDocument
from ai.topics import TopicDiscoveryAnalyzer
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
class TestTopicDiscoveryAnalyzer:

    def test_discover_topics_success(self):
        b1 = _make_bundle("1", "Pianifichiamo il viaggio a Milano")
        b2 = _make_bundle("2", "Prendiamo il treno delle 10")
        doc = ConversationEvidenceDocument(document_id="doc_disc", bundles=(b1, b2))

        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"
        eid2 = "unified:msgstore_db:2::ORIGINAL_TEXT"

        json_resp = f"""
        {{
            "topics": [
                {{
                    "label": "Viaggio a Milano",
                    "short_description": "Organizzazione dello spostamento in treno a Milano",
                    "evidence_ids": ["{eid1}", "{eid2}"]
                }}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDiscoveryAnalyzer(client=client)

        res = analyzer.discover_topics(doc)

        assert len(res.topics) == 1
        top = res.topics[0]
        assert top.label == "Viaggio a Milano"
        assert top.evidence_ids == (eid1, eid2)
        assert res.provenance_document_id == "doc_disc"
        assert res.metadata["prompt_version"] == "topic_discovery_v1"

    def test_discover_topics_exceeding_max_topics_rejected_no_silent_truncation(self):
        b1 = _make_bundle("1", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc_cap", bundles=(b1,))
        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"

        # 3 topic restituiti
        json_resp = f"""
        {{
            "topics": [
                {{"label": "T1", "short_description": "D1", "evidence_ids": ["{eid1}"]}},
                {{"label": "T2", "short_description": "D2", "evidence_ids": ["{eid1}"]}},
                {{"label": "T3", "short_description": "D3", "evidence_ids": ["{eid1}"]}}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDiscoveryAnalyzer(client=client)

        # Cap impostato a 2 -> deve sollevare AiStructuredOutputError senza silent truncation (D5)
        with pytest.raises(AiStructuredOutputError, match="superando il limite consentito"):
            analyzer.discover_topics(doc, max_topics=2)

    def test_discover_topics_rejects_hallucinated_evidence(self):
        b1 = _make_bundle("1", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc_err", bundles=(b1,))

        json_resp = """
        {
            "topics": [
                {"label": "T1", "short_description": "D1", "evidence_ids": ["non_esiste::ORIGINAL_TEXT"]}
            ]
        }
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDiscoveryAnalyzer(client=client)

        with pytest.raises(AiStructuredOutputError, match="cita evidence_id inesistenti"):
            analyzer.discover_topics(doc)

    def test_discover_topics_rejects_empty_evidence_ids(self):
        b1 = _make_bundle("1", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc_empty_eids", bundles=(b1,))

        json_resp = """
        {
            "topics": [
                {"label": "T1", "short_description": "D1", "evidence_ids": []}
            ]
        }
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDiscoveryAnalyzer(client=client)

        with pytest.raises(AiStructuredOutputError, match="deve contenere almeno un evidence_id"):
            analyzer.discover_topics(doc)

    def test_discover_topics_rejects_duplicate_evidence_ids_in_topic(self):
        b1 = _make_bundle("1", "Testo")
        doc = ConversationEvidenceDocument(document_id="doc_dup_eids", bundles=(b1,))
        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"

        json_resp = f"""
        {{
            "topics": [
                {{"label": "T1", "short_description": "D1", "evidence_ids": ["{eid1}", "{eid1}"]}}
            ]
        }}
        """
        client = FakeLocalLlmClient(default_response=json_resp)
        analyzer = TopicDiscoveryAnalyzer(client=client)

        with pytest.raises(AiStructuredOutputError, match="evidence_ids duplicati"):
            analyzer.discover_topics(doc)

    def test_empty_document_returns_empty_topics(self):
        doc = ConversationEvidenceDocument(document_id="empty_doc", bundles=())
        client = FakeLocalLlmClient(default_response="Should not be called")
        analyzer = TopicDiscoveryAnalyzer(client=client)

        res = analyzer.discover_topics(doc)
        assert len(res.topics) == 0
        assert len(client.call_history) == 0
