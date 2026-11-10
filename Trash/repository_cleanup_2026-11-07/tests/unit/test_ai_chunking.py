"""
tests/unit/test_ai_chunking.py
------------------------------
Test unitari per il modulo ai/chunking.py:
- Suddivisione deterministica di ConversationEvidenceDocument in chunk
- Gestione di sezioni oversize e bundle atomici oversize (AnalysisInputTooLargeError)
- Verifica espansione post-traduzione (TRANSLATE_FIRST)
- Aggregazione multi-chunk con consapevolezza dei guasti e conteggio atteso (PRESENT, ABSENT, UNCERTAIN)
- Aggregazione per Open Topic Discovery
"""
import pytest

from ai.backend import AnalysisInputTooLargeError
from ai.chunking import (
    ChunkAnalysisOutcome,
    aggregate_topic_detection,
    aggregate_topic_discovery,
    split_document_into_chunks,
    verify_translated_document_size,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
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
class TestAiChunking:

    def test_split_small_document_returns_single_chunk(self):
        b1 = _make_bundle("1", "Breve testo")
        doc = ConversationEvidenceDocument(document_id="doc_small", bundles=(b1,))

        chunks = split_document_into_chunks(doc, max_characters=1000, max_bundles=10)
        assert len(chunks) == 1
        assert chunks[0].document_id == "doc_small"

    def test_split_multi_bundle_document_exceeding_max_bundles(self):
        bundles = tuple(_make_bundle(str(i), f"Messaggio {i}") for i in range(10))
        doc = ConversationEvidenceDocument(document_id="doc_large", bundles=bundles)

        chunks = split_document_into_chunks(doc, max_characters=10000, max_bundles=3)
        assert len(chunks) == 4
        assert chunks[0].document_id == "doc_large::chunk::0"
        assert chunks[0].message_count == 3
        assert chunks[1].document_id == "doc_large::chunk::1"
        assert chunks[1].message_count == 3
        assert chunks[3].document_id == "doc_large::chunk::3"
        assert chunks[3].message_count == 1

    def test_split_oversize_section_raises_analysis_input_too_large(self):
        oversize_text = "A" * 500
        bundle = _make_bundle("1", oversize_text)
        doc = ConversationEvidenceDocument(document_id="doc_oversize", bundles=(bundle,))

        with pytest.raises(AnalysisInputTooLargeError, match="supera il limite massimo consentito per chunk"):
            split_document_into_chunks(doc, max_characters=200, max_bundles=10)

    def test_split_atomic_bundle_oversize_raises_analysis_input_too_large(self):
        from multimodal.models import AudioTranscriptionResult, MediaKind, MediaResolutionStatus, ResolvedMediaAsset, TranscriptionStatus

        b_base = _make_bundle("1", "B" * 150)
        msg = b_base.message
        asset = ResolvedMediaAsset(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            raw_reference=None,
            resolved_path=None,
            status=MediaResolutionStatus.RESOLVED,
            media_kind=MediaKind.AUDIO,
            provenance_message=msg,
        )
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="C" * 150,
            provenance_asset=asset,
        )
        bundle = MessageEvidenceBundle(message=msg, audio_transcription=stt)
        doc = ConversationEvidenceDocument(document_id="doc_bundle_oversize", bundles=(bundle,))

        # Ciascuna delle 2 sezioni ha 150 caratteri (<= 250), ma il bundle ne ha 300 (> 250)
        with pytest.raises(AnalysisInputTooLargeError, match="supera da solo il limite massimo"):
            split_document_into_chunks(doc, max_characters=250, max_bundles=10)

    def test_verify_translated_document_size_raises_on_expanded_text(self):
        bundle = _make_bundle("1", "Short text")
        doc = ConversationEvidenceDocument(document_id="doc_tr_size", bundles=(bundle,))

        eid = "unified:msgstore_db:1::ORIGINAL_TEXT"
        tr_item = EvidenceTranslationItem(
            original_evidence_id=eid,
            original_language="en",
            translated_text="E" * 1000,  # traduzione espansa
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc_tr_size",
        )

        with pytest.raises(AnalysisInputTooLargeError, match="supera il limite massimo per chunk"):
            verify_translated_document_size(doc, tr_res, max_characters=500)

    def test_verify_translated_document_size_missing_translation_raises_value_error(self):
        bundle = _make_bundle("1", "Short text")
        doc = ConversationEvidenceDocument(document_id="doc_missing_tr", bundles=(bundle,))
        tr_res = EvidenceTranslationResult(
            translations=(),  # nessuna traduzione per l'evidenza
            provenance_document_id="doc_missing_tr",
        )
        with pytest.raises(ValueError, match="Traduzione mancante per evidence_id"):
            verify_translated_document_size(doc, tr_res, max_characters=500)

    def test_verify_translated_document_size_empty_valid_translation_no_fallback(self):
        # Testo originale lungo (supererebbe 50 caratteri se cadesse in fallback)
        bundle = _make_bundle("1", "X" * 100)
        doc = ConversationEvidenceDocument(document_id="doc_empty_tr", bundles=(bundle,))

        eid = "unified:msgstore_db:1::ORIGINAL_TEXT"
        tr_item = EvidenceTranslationItem(
            original_evidence_id=eid,
            original_language="en",
            translated_text="",  # stringa vuota valida
            target_language="it",
        )
        tr_res = EvidenceTranslationResult(
            translations=(tr_item,),
            provenance_document_id="doc_empty_tr",
        )
        # Se cadesse in fallback al testo originale (100 char), solleverebbe AnalysisInputTooLargeError con max_characters=50
        # Invece con la traduzione vuota (0 char) passa senza sollevare errore!
        verify_translated_document_size(doc, tr_res, max_characters=50)

    def test_verify_translated_document_size_raises_on_bundle_oversize(self):
        # 2 sezioni, ciascuna da 400 char, somma 800 char > 500 char
        from multimodal.models import ImageOcrResult, MediaKind, MediaResolutionStatus, OcrStatus, ResolvedMediaAsset
        bundle_base = _make_bundle("1", "A" * 10)
        msg = bundle_base.message
        asset = ResolvedMediaAsset(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            raw_reference="image.jpg",
            resolved_path="/path/image.jpg",
            media_kind=MediaKind.IMAGE,
            status=MediaResolutionStatus.RESOLVED,
            file_size_bytes=100,
            sha256="abc",
            provenance_message=msg,
        )
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="B" * 10,
            provenance_asset=asset,
        )
        bundle = MessageEvidenceBundle(message=msg, image_ocr=ocr)
        doc = ConversationEvidenceDocument(document_id="doc_bundle_oversize", bundles=(bundle,))

        assert len(bundle.text_evidence_sections) == 2
        sec1 = bundle.text_evidence_sections[0]
        sec2 = bundle.text_evidence_sections[1]

        tr_item1 = EvidenceTranslationItem(sec1.evidence_id, "it", "A" * 400, "it")
        tr_item2 = EvidenceTranslationItem(sec2.evidence_id, "it", "B" * 400, "it")
        tr_res = EvidenceTranslationResult((tr_item1, tr_item2), provenance_document_id="doc_bundle_oversize")

        with pytest.raises(AnalysisInputTooLargeError, match="bundle tradotto per il messaggio"):
            verify_translated_document_size(doc, tr_res, max_characters=500)


    def test_aggregate_topic_detection_present_rule(self):
        query = TopicQuery(topic_id="top1", label="Viaggi", description="D")
        parent_id = "doc_parent"

        res1 = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Non presente nel chunk 0",
            provenance_document_id="doc_parent::chunk::0",
        )
        res2 = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid_travel_1",),
            rationale="Trovato viaggio nel chunk 1",
            provenance_document_id="doc_parent::chunk::1",
        )

        agg = aggregate_topic_detection([res1, res2], query, parent_id, expected_chunk_count=2)

        assert agg.decision == TopicDecision.PRESENT
        assert agg.evidence_ids == ("eid_travel_1",)
        assert agg.provenance_document_id == parent_id
        assert "chunk" in agg.rationale

    def test_aggregate_topic_detection_all_absent_rule(self):
        query = TopicQuery(topic_id="top1", label="Viaggi", description="D")
        parent_id = "doc_parent"

        res1 = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente chunk 0",
            provenance_document_id="doc_parent::chunk::0",
        )
        res2 = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente chunk 1",
            provenance_document_id="doc_parent::chunk::1",
        )

        agg = aggregate_topic_detection([res1, res2], query, parent_id, expected_chunk_count=2)
        assert agg.decision == TopicDecision.ABSENT
        assert agg.evidence_ids == ()

    def test_aggregate_topic_detection_failed_chunk_prevents_absent(self):
        query = TopicQuery(topic_id="top1", label="Viaggi", description="D")
        parent_id = "doc_parent"

        outcome_ok = ChunkAnalysisOutcome(
            chunk_id="chunk_0",
            result=TopicDetectionResult(
                topic=query,
                decision=TopicDecision.ABSENT,
                evidence_ids=(),
                rationale="Assente",
                provenance_document_id="chunk_0",
            ),
            status="SUCCESS",
        )
        outcome_failed = ChunkAnalysisOutcome(
            chunk_id="chunk_1",
            result=None,
            status="FAILED",
            error_type="TIMEOUT",
        )

        agg = aggregate_topic_detection([outcome_ok, outcome_failed], query, parent_id, expected_chunk_count=2)
        # Non può essere ABSENT se un chunk è fallito
        assert agg.decision == TopicDecision.UNCERTAIN
        assert "falliti" in agg.rationale

    def test_aggregate_topic_detection_missing_chunk_prevents_absent(self):
        query = TopicQuery(topic_id="top1", label="Viaggi", description="D")
        parent_id = "doc_parent"

        outcome_ok = ChunkAnalysisOutcome(
            chunk_id="chunk_0",
            result=TopicDetectionResult(
                topic=query,
                decision=TopicDecision.ABSENT,
                evidence_ids=(),
                rationale="Assente",
                provenance_document_id="chunk_0",
            ),
            status="SUCCESS",
        )

        # 1 chunk su 3 attesi -> non può essere ABSENT
        agg = aggregate_topic_detection([outcome_ok], query, parent_id, expected_chunk_count=3)
        assert agg.decision == TopicDecision.UNCERTAIN
        assert "su 3 attesi" in agg.rationale

    def test_aggregate_topic_discovery_fuses_identical_labels(self):
        t1 = DiscoveredTopic(label="Tema 1", short_description="Desc A", evidence_ids=("e1",))
        res1 = TopicDiscoveryResult(topics=(t1,), provenance_document_id="doc::chunk::0")

        t2 = DiscoveredTopic(label="tema 1", short_description="Desc B", evidence_ids=("e2",))
        res2 = TopicDiscoveryResult(topics=(t2,), provenance_document_id="doc::chunk::1")

        agg = aggregate_topic_discovery([res1, res2], parent_document_id="doc", max_topics=10)

        assert len(agg.topics) == 1
        assert agg.topics[0].label == "Tema 1"
        assert set(agg.topics[0].evidence_ids) == {"e1", "e2"}
