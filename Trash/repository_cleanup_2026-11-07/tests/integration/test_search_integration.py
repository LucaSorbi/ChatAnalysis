"""
tests/integration/test_search_integration.py
--------------------------------------------
Integration test sintetico end-to-end per il Search Layer (FASE M):
UnifiedMessage
  → MessageEvidenceBundle
  → ConversationEvidenceDocument
  → TopicDetectionResult / TopicDiscoveryResult (fake)
  → SearchService
  → Search results & SearchViewResult

Verifica la completa catena di provenance forense dalle viste fino alla
TextEvidenceSection originaria e al UnifiedMessage, operando al 100% in locale
senza rete né LM Studio.
"""
import pytest

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from importer.models import RawRecord
from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
    TextEvidenceSection,
)
from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    ImageOcrResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
    TranscriptionStatus,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from search.models import EvidenceSearchQuery, MatchMode, SearchViewResult
from search.service import SearchService
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_synthetic_pipeline_environment() -> tuple[
    ConversationEvidenceDocument,
    list[TopicDetectionResult],
    TopicDiscoveryResult,
]:
    source = "msgstore_db"

    # Messaggio 1: puramente testuale
    raw1 = RawRecord(
        source_name=source,
        source_path="/path/test1.db",
        source_record_id="101",
        record_type="message",
        raw_fields={"body": "Confermo il versamento della quota societaria."},
        media_reference=None,
        metadata={},
    )
    val1 = ValidationResult(record=raw1, issues=())
    ts1 = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm1 = NormalizedRecord(
        raw_record=raw1,
        validation_result=val1,
        source_name=source,
        source_record_id="101",
        record_type="message",
        timestamp=ts1,
        message_type=CanonicalMessageType.TEXT,
        text_content="Confermo il versamento della quota societaria.",
        media_reference=None,
    )
    msg1 = UnifiedMessage(
        message_id=f"unified:{source}:101",
        source_name=source,
        source_record_id="101",
        source_path="/path/test1.db",
        record_type="message",
        timestamp=ts1,
        message_type=CanonicalMessageType.TEXT,
        text_content="Confermo il versamento della quota societaria.",
        media_reference=None,
        provenance_record=norm1,
    )
    bundle1 = MessageEvidenceBundle(message=msg1)

    # Messaggio 2: multimediale con audio (STT) e immagine (OCR)
    raw2 = RawRecord(
        source_name=source,
        source_path="/path/test1.db",
        source_record_id="102",
        record_type="message",
        raw_fields={"body": "Ecco la documentazione allegata"},
        media_reference="media/screenshot.png",
        metadata={},
    )
    val2 = ValidationResult(record=raw2, issues=())
    ts2 = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm2 = NormalizedRecord(
        raw_record=raw2,
        validation_result=val2,
        source_name=source,
        source_record_id="102",
        record_type="message",
        timestamp=ts2,
        message_type=CanonicalMessageType.IMAGE,
        text_content="Ecco la documentazione allegata",
        media_reference="media/screenshot.png",
    )
    msg2 = UnifiedMessage(
        message_id=f"unified:{source}:102",
        source_name=source,
        source_record_id="102",
        source_path="/path/test1.db",
        record_type="message",
        timestamp=ts2,
        message_type=CanonicalMessageType.IMAGE,
        text_content="Ecco la documentazione allegata",
        media_reference="media/screenshot.png",
        provenance_record=norm2,
    )

    asset_img = ResolvedMediaAsset(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        raw_reference="media/screenshot.png",
        resolved_path="/path/media/screenshot.png",
        media_kind=MediaKind.IMAGE,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=4096,
        sha256="11223344",
        provenance_message=msg2,
    )
    ocr_res = ImageOcrResult(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        status=OcrStatus.SUCCESS,
        full_text="FATTURA N. 987 - IMPORTO: 50.000 EURO - BONIFICO ESTERO",
        regions=(OcrTextRegion("FATTURA N. 987 - IMPORTO: 50.000 EURO - BONIFICO ESTERO", order_index=0),),
        language_config="ita",
        provenance_asset=asset_img,
    )

    asset_audio = ResolvedMediaAsset(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        raw_reference="media/audio.opus",
        resolved_path="/path/media/audio.opus",
        media_kind=MediaKind.AUDIO,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=8192,
        sha256="55667788",
        provenance_message=msg2,
    )
    stt_res = AudioTranscriptionResult(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        status=TranscriptionStatus.SUCCESS,
        full_transcript="Ho appena inviato il file con la ricevuta bancaria.",
        segments=(AudioTranscriptSegment(0.0, 3.0, "Ho appena inviato il file con la ricevuta bancaria."),),
        detected_language="it",
        provenance_asset=asset_audio,
    )

    bundle2 = MessageEvidenceBundle(
        message=msg2,
        audio_transcription=stt_res,
        image_ocr=ocr_res,
    )

    doc = ConversationEvidenceDocument(
        document_id="doc::case_001",
        bundles=(bundle1, bundle2),
        source_name=source,
    )

    # Risultati Topic AI simulati
    eid_ocr = f"{msg2.message_id}::OCR_TEXT"
    eid_stt = f"{msg2.message_id}::STT_TRANSCRIPTION"

    topic_det = TopicDetectionResult(
        topic=TopicQuery(
            topic_id="TOPIC_FATTURE",
            label="Emissione Fatture",
            description="Discussione di fatture o pagamenti esteri",
        ),
        decision=TopicDecision.PRESENT,
        evidence_ids=(eid_ocr,),
        rationale="L'evidenza OCR della fattura 987 attesta l'importo di 50.000 euro.",
        provenance_document_id=doc.document_id,
    )

    topic_disc = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Ricevute Finanziarie",
                short_description="Scambio di ricevute per bonifici esteri",
                evidence_ids=(eid_stt,),
            ),
        ),
        provenance_document_id=doc.document_id,
    )

    return doc, [topic_det], topic_disc


@pytest.mark.integration
class TestSearchIntegration:

    def test_full_pipeline_to_search_and_provenance_resolution(self):
        doc, detections, discoveries = _create_synthetic_pipeline_environment()

        service = SearchService(
            document=doc,
            detection_results=detections,
            discovery_results=discoveries,
        )

        # 1. Ricerca evidenze testuali con PHRASE su OCR
        q_ocr = EvidenceSearchQuery(query_text="bonifico estero", match_mode=MatchMode.PHRASE)
        res_ocr = service.search_evidence(q_ocr)

        assert res_ocr.total_hits == 1
        hit = res_ocr.hits[0]
        assert hit.evidence_id == "unified:msgstore_db:102::OCR_TEXT"
        assert hit.source_type == EvidenceSourceType.OCR_TEXT
        assert hit.message_id == "unified:msgstore_db:102"
        assert hit.source_name == "msgstore_db"
        assert hit.source_record_id == "102"
        assert "50.000 EURO" in hit.original_text

        # Verifica catena di provenance diretta: hit.section è la sezione originaria
        section = hit.section
        assert section.evidence_id == hit.evidence_id
        assert section.source_name == "msgstore_db"

        # 2. Ricerca evidenze su STT vocale con ALL_TERMS
        q_stt = EvidenceSearchQuery(query_text="ricevuta bancaria", match_mode=MatchMode.ALL_TERMS)
        res_stt = service.search_evidence(q_stt)
        assert res_stt.total_hits == 1
        assert res_stt.hits[0].source_type == EvidenceSourceType.STT_TRANSCRIPTION

        # 3. Risoluzione deterministica dell'evidenza tramite ID
        resolved_sec = service.resolve_evidence(hit.evidence_id)
        assert resolved_sec is section

        # 4. Ricerca e risoluzione Topic Detection -> Evidenze
        topic_res = service.search_detections(topic_id="TOPIC_FATTURE")
        assert topic_res.total_hits == 1
        top_hit = topic_res.hits[0]
        assert top_hit.decision == TopicDecision.PRESENT
        assert len(top_hit.matched_sections) == 1
        assert top_hit.matched_sections[0].evidence_id == "unified:msgstore_db:102::OCR_TEXT"
        assert top_hit.matched_sections[0] is section

        # 5. Ricerca Topic Discovery -> Evidenze
        disc_res = service.search_discoveries(query_text="ricevute finanziarie")
        assert disc_res.total_hits == 1
        disc_hit = disc_res.hits[0]
        assert disc_hit.label == "Ricevute Finanziarie"
        assert len(disc_hit.matched_sections) == 1
        assert disc_hit.matched_sections[0].evidence_id == "unified:msgstore_db:102::STT_TRANSCRIPTION"

        # 6. Generazione SearchViewResult per la UI
        evidence_views = service.search_evidence_views(q_ocr)
        assert len(evidence_views) == 1
        ev_view = evidence_views[0]
        assert isinstance(ev_view, SearchViewResult)
        assert ev_view.result_type == "EVIDENCE"
        assert ev_view.display_text == hit.original_text
        assert ev_view.original_text == hit.original_text
        assert ev_view.evidence_id == "unified:msgstore_db:102::OCR_TEXT"
        assert ev_view.message_id == "unified:msgstore_db:102"
        assert ev_view.provenance["source_record_id"] == "102"

        topic_views = service.search_topic_views(query_text="Fatture")
        assert len(topic_views) >= 1
        top_view = topic_views[0]
        assert isinstance(top_view, SearchViewResult)
        assert top_view.result_type == "TOPIC_DETECTION"
        assert top_view.display_text == detections[0].topic.description
        assert top_view.original_text is None
        assert top_view.topic_decision == TopicDecision.PRESENT
        assert "Emissione Fatture" in top_view.title
