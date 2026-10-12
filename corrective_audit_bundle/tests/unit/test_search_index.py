"""
tests/unit/test_search_index.py
-------------------------------
Test unitari per EvidenceIndex e risoluzione deterministica delle evidenze:
- Costruzione da ConversationEvidenceDocument, bundle e sezioni
- Rilevamento collisione evidence_id e sollevamento di EvidenceIntegrityError
- Risoluzione positiva di evidence_id esistenti (.get e .resolve)
- Trattamento rigoroso di evidence_id inesistente (None con .get, EvidenceNotFoundError con .resolve)
- Risoluzione multipla con e senza modalità strict
- Preservazione dell'ordine originario del documento
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
from search.index import EvidenceIndex, EvidenceIntegrityError, EvidenceNotFoundError
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_bundle(
    idx: str,
    text: str,
    source: str = "msgstore_db",
) -> MessageEvidenceBundle:
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
class TestSearchIndex:

    def test_build_index_from_document(self):
        b1 = _make_bundle("1", "Messaggio uno")
        b2 = _make_bundle("2", "Messaggio due")
        doc = ConversationEvidenceDocument(
            document_id="doc::1",
            bundles=(b1, b2),
            source_name="msgstore_db",
        )
        index = EvidenceIndex.from_document(doc)
        assert len(index) == 2
        assert index.document_id == "doc::1"
        assert index.all_evidence_ids == (
            "unified:msgstore_db:1::ORIGINAL_TEXT",
            "unified:msgstore_db:2::ORIGINAL_TEXT",
        )

    def test_resolution_existing_and_non_existing(self):
        b1 = _make_bundle("1", "Contenuto primo")
        index = EvidenceIndex.from_bundles([b1])

        eid = "unified:msgstore_db:1::ORIGINAL_TEXT"
        sec = index.get(eid)
        assert sec is not None
        assert sec.text == "Contenuto primo"
        assert index.resolve(eid).text == "Contenuto primo"
        assert eid in index

        # Inesistente: .get() restituisce None, .resolve() solleva EvidenceNotFoundError
        assert index.get("non_existing_eid") is None
        assert "non_existing_eid" not in index
        with pytest.raises(EvidenceNotFoundError, match="Evidence ID non trovato"):
            index.resolve("non_existing_eid")

    def test_resolve_many_strict_and_non_strict(self):
        b1 = _make_bundle("1", "Primo")
        b2 = _make_bundle("2", "Secondo")
        index = EvidenceIndex.from_bundles([b1, b2])

        eid1 = "unified:msgstore_db:1::ORIGINAL_TEXT"
        eid2 = "unified:msgstore_db:2::ORIGINAL_TEXT"

        # Non-strict: ignora ID inesistenti senza fallire
        resolved = index.resolve_many([eid1, "fake_id", eid2], strict=False)
        assert len(resolved) == 2
        assert resolved[0].evidence_id == eid1
        assert resolved[1].evidence_id == eid2

        # Strict: fallisce su ID inesistente
        with pytest.raises(EvidenceNotFoundError, match="Evidence ID non trovato"):
            index.resolve_many([eid1, "fake_id", eid2], strict=True)

    def test_collision_raises_evidence_integrity_error(self):
        sec1 = TextEvidenceSection(
            evidence_id="collision_id",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Testo 1",
            message_id="msg::1",
            source_name="src",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="collision_id",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Testo 2 duplicato",
            message_id="msg::2",
            source_name="src",
            source_record_id="2",
        )
        with pytest.raises(EvidenceIntegrityError, match="Collisione di integrità"):
            EvidenceIndex(sections=[sec1, sec2])

    def test_deterministic_natural_document_order(self):
        bundles = [_make_bundle(str(i), f"Msg {i}") for i in range(10)]
        index = EvidenceIndex.from_bundles(bundles)
        expected_ids = tuple(f"unified:msgstore_db:{i}::ORIGINAL_TEXT" for i in range(10))
        assert index.all_evidence_ids == expected_ids
        assert tuple(sec.evidence_id for sec in index.all_sections) == expected_ids
