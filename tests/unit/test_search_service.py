"""
tests/unit/test_search_service.py
---------------------------------
Test unitari per SearchService (Point C):
- Rifiuto categorico di invocazioni ambigue con sia 'document' che 'index' (ValueError)
- Inizializzazione pulita con solo 'document'
- Inizializzazione pulita con solo 'index'
- Inizializzazione di default vuota
"""
import pytest

from ai.models import ConversationEvidenceDocument
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from search.index import EvidenceIndex
from search.service import SearchService
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _sample_document() -> ConversationEvidenceDocument:
    raw = RawRecord(
        source_name="msgstore_db",
        source_path="/path/test",
        source_record_id="1",
        record_type="message",
        raw_fields={"data": "test"},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="msgstore_db",
        source_record_id="1",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content="test",
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id="unified:msgstore_db:1",
        source_name="msgstore_db",
        source_record_id="1",
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content="test",
        media_reference=None,
        provenance_record=norm,
    )
    bundle = MessageEvidenceBundle(message=msg)
    return ConversationEvidenceDocument(
        document_id="doc::1",
        bundles=(bundle,),
        source_name="msgstore_db",
    )


@pytest.mark.unit
class TestSearchServiceAuthoritativeSource:

    def test_both_document_and_index_raises_value_error(self):
        doc = _sample_document()
        index = EvidenceIndex.from_document(doc)

        with pytest.raises(ValueError, match="Fornire 'document' o 'index', non entrambi contemporaneamente"):
            SearchService(document=doc, index=index)

    def test_document_only_initialization(self):
        doc = _sample_document()
        service = SearchService(document=doc)
        assert service.index.document_id == "doc::1"
        assert len(service.index) == 1

    def test_index_only_initialization(self):
        doc = _sample_document()
        index = EvidenceIndex.from_document(doc)
        service = SearchService(index=index)
        assert service.index.document_id == "doc::1"
        assert len(service.index) == 1

    def test_empty_initialization(self):
        service = SearchService()
        assert len(service.index) == 0
