"""
tests/unit/test_media_processing_lifecycle.py
----------------------------------------------
Test di regressione e audit forense per BLOCKER 5:
- OCR riuscito -> COMPLETE.
- OCR non disponibile -> FAILED (nessuna falsa conferma).
- Un media riuscito e uno fallito -> PARTIAL.
- Retry dopo disponibilità backend -> COMPLETE.
- Retry non duplica evidenze.
- Documento privo di media -> NOT_PROCESSED.
- Errore ZIP -> nessun successo fittizio.
- Isolamento sessione A/B.
- Stato preservato dopo cambio conversazione (A -> B -> A).
- Nessun Fake Vision su dati reali.
"""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import zipfile
import pytest

from ai.models import ConversationEvidenceDocument
from multimodal.asset_access import (
    MediaAssetAccessService,
    MediaAssetRegistry,
    MediaProcessingStatus,
)
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle
from multimodal.models import (
    AudioTranscriptionResult,
    MediaKind,
    OcrStatus,
    TranscriptionStatus,
    ImageOcrResult,
    ResolvedMediaAsset,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    RawRecord,
    TimestampTzStatus,
    ValidationResult,
)
from ui import state
from unified.models import UnifiedMessage


def _make_test_message(
    msg_id: str,
    media_ref: str | None = None,
    text: str = "Test",
    msg_type: CanonicalMessageType = CanonicalMessageType.TEXT,
) -> UnifiedMessage:
    raw = RawRecord(
        source_name="whatsapp",
        source_path="/fake/chat.txt",
        source_record_id=msg_id,
        record_type="message",
        raw_fields={"text": text},
        media_reference=media_ref,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp",
        source_record_id=msg_id,
        record_type="message",
        timestamp=ts,
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
    )
    return UnifiedMessage(
        message_id=f"msg::{msg_id}",
        source_name="whatsapp",
        source_record_id=msg_id,
        source_path="/fake/chat.txt",
        record_type="message",
        timestamp=ts,
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
        provenance_record=norm,
    )


class MockSuccessOcrEngine:
    def extract_text(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        return ImageOcrResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo estratto con successo dall'immagine.",
            engine="tesseract",
            provenance_asset=asset,
        )


class MockFailingOcrEngine:
    def extract_text(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        return ImageOcrResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=OcrStatus.FAILED,
            full_text="",
            engine="tesseract",
            provenance_asset=asset,
            error_message="Errore simulato OCR backend",
        )


class MockSuccessSttEngine:
    def transcribe(self, asset: ResolvedMediaAsset) -> AudioTranscriptionResult:
        return AudioTranscriptionResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Audio vocale trascritto correttamente.",
            detected_language="it",
            engine="faster-whisper",
            model_name="tiny",
            provenance_asset=asset,
        )


def test_ocr_success_leads_to_complete_status(tmp_path: Path):
    """OCR riuscito -> COMPLETE e multimodal_processed=True."""
    zip_path = tmp_path / "chat_img.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.png", b"\x89PNG\r\n\x1a\n_valid_png_bytes")

    msg = _make_test_message("1", media_ref="photo.png", text="Foto allegata", msg_type=CanonicalMessageType.IMAGE)
    doc = ConversationEvidenceDocument(
        document_id="doc_ocr_complete",
        bundles=(MessageEvidenceBundle(message=msg),),
    )

    service = MediaAssetAccessService(archive_path=zip_path)
    enriched_doc = service.process_document_media(
        document=doc,
        ocr_backend=MockSuccessOcrEngine(),
    )

    assert enriched_doc.metadata.get("multimodal_status") == MediaProcessingStatus.COMPLETE.value
    assert enriched_doc.metadata.get("multimodal_processed") is True
    assert enriched_doc.bundles[0].image_ocr is not None
    assert enriched_doc.bundles[0].image_ocr.status == OcrStatus.SUCCESS
    # Sezione evidenza aggiunta
    assert any(sec.source_type == EvidenceSourceType.OCR_TEXT for sec in enriched_doc.all_evidence_sections)


def test_ocr_unavailable_leads_to_failed_status(tmp_path: Path):
    """OCR non disponibile -> FAILED, nessuna evidenza fittizia, multimodal_processed=False."""
    zip_path = tmp_path / "chat_img2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo2.png", b"\x89PNG\r\n\x1a\n_valid_png_bytes")

    msg = _make_test_message("2", media_ref="photo2.png", text="Foto allegata", msg_type=CanonicalMessageType.IMAGE)
    doc = ConversationEvidenceDocument(
        document_id="doc_ocr_failed",
        bundles=(MessageEvidenceBundle(message=msg),),
    )

    service = MediaAssetAccessService(archive_path=zip_path)
    enriched_doc = service.process_document_media(
        document=doc,
        ocr_backend=None,  # Backend non disponibile
    )

    assert enriched_doc.metadata.get("multimodal_status") == MediaProcessingStatus.FAILED.value
    assert enriched_doc.metadata.get("multimodal_processed") is False
    assert enriched_doc.bundles[0].image_ocr is not None
    assert enriched_doc.bundles[0].image_ocr.status == OcrStatus.FAILED
    assert not any(sec.source_type == EvidenceSourceType.OCR_TEXT for sec in enriched_doc.all_evidence_sections)


def test_mixed_media_one_success_one_fail_leads_to_partial_status(tmp_path: Path):
    """Un media riuscito e uno fallito -> PARTIAL e multimodal_processed=False."""
    zip_path = tmp_path / "chat_mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo_ok.png", b"\x89PNG\r\n\x1a\n_ok")
        zf.writestr("audio_fail.opus", b"OggS_bad_audio")

    msg_img = _make_test_message("10", media_ref="photo_ok.png", text="Foto", msg_type=CanonicalMessageType.IMAGE)
    msg_aud = _make_test_message("11", media_ref="audio_fail.opus", text="Audio", msg_type=CanonicalMessageType.AUDIO)
    doc = ConversationEvidenceDocument(
        document_id="doc_mixed",
        bundles=(MessageEvidenceBundle(message=msg_img), MessageEvidenceBundle(message=msg_aud)),
    )

    service = MediaAssetAccessService(archive_path=zip_path)
    # OCR presente (successo), STT assente (fallimento)
    enriched_doc = service.process_document_media(
        document=doc,
        ocr_backend=MockSuccessOcrEngine(),
        stt_backend=None,
    )

    assert enriched_doc.metadata.get("multimodal_status") == MediaProcessingStatus.PARTIAL.value
    assert enriched_doc.metadata.get("multimodal_processed") is False
    assert enriched_doc.bundles[0].image_ocr.status == OcrStatus.SUCCESS
    assert enriched_doc.bundles[1].audio_transcription.status == TranscriptionStatus.FAILED


def test_retry_after_backend_available_reaches_complete_without_duplication(tmp_path: Path):
    """Retry dopo disponibilità backend raggiunge COMPLETE e non duplica evidenze."""
    zip_path = tmp_path / "chat_retry.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.png", b"\x89PNG\r\n\x1a\n_ok")
        zf.writestr("voice.opus", b"OggS_ok")

    msg_img = _make_test_message("20", media_ref="photo.png", text="Foto", msg_type=CanonicalMessageType.IMAGE)
    msg_aud = _make_test_message("21", media_ref="voice.opus", text="Voce", msg_type=CanonicalMessageType.AUDIO)
    doc = ConversationEvidenceDocument(
        document_id="doc_retry",
        bundles=(MessageEvidenceBundle(message=msg_img), MessageEvidenceBundle(message=msg_aud)),
    )

    service = MediaAssetAccessService(archive_path=zip_path)

    # 1. Primo tentativo: solo OCR disponibile -> PARTIAL
    doc_v1 = service.process_document_media(
        document=doc,
        ocr_backend=MockSuccessOcrEngine(),
        stt_backend=None,
    )
    assert doc_v1.metadata.get("multimodal_status") == MediaProcessingStatus.PARTIAL.value
    sections_v1_count = len(doc_v1.all_evidence_sections)
    ocr_sections_v1 = [s for s in doc_v1.all_evidence_sections if s.source_type == EvidenceSourceType.OCR_TEXT]
    assert len(ocr_sections_v1) == 1

    # 2. Secondo tentativo (Retry): STT ora disponibile
    doc_v2 = service.process_document_media(
        document=doc_v1,
        ocr_backend=MockSuccessOcrEngine(),
        stt_backend=MockSuccessSttEngine(),
    )
    assert doc_v2.metadata.get("multimodal_status") == MediaProcessingStatus.COMPLETE.value
    assert doc_v2.metadata.get("multimodal_processed") is True

    # Verifica non duplicazione delle sezioni OCR
    ocr_sections_v2 = [s for s in doc_v2.all_evidence_sections if s.source_type == EvidenceSourceType.OCR_TEXT]
    stt_sections_v2 = [s for s in doc_v2.all_evidence_sections if s.source_type == EvidenceSourceType.STT_TRANSCRIPTION]
    assert len(ocr_sections_v2) == 1, "Le sezioni OCR non devono essere duplicate durante il retry"
    assert len(stt_sections_v2) == 1


def test_document_without_media_not_processed():
    """Documento privo di media -> NOT_PROCESSED, nessun successo fittizio."""
    msg = _make_test_message("30", media_ref=None, text="Messaggio puramente testuale")
    doc = ConversationEvidenceDocument(
        document_id="doc_text_only",
        bundles=(MessageEvidenceBundle(message=msg),),
    )

    service = MediaAssetAccessService(archive_path=None)
    res_doc = service.process_document_media(document=doc)

    assert res_doc.metadata.get("multimodal_status") == MediaProcessingStatus.NOT_PROCESSED.value
    assert res_doc.metadata.get("multimodal_processed") is False


def test_zip_error_no_fictitious_success(tmp_path: Path):
    """Archivio ZIP corrotto o mancante -> FAILED, nessun successo fittizio."""
    bad_zip = tmp_path / "corrupted.zip"
    bad_zip.write_bytes(b"NOT_A_ZIP_FILE_AT_ALL")

    msg = _make_test_message("40", media_ref="attachment.jpg", text="Foto", msg_type=CanonicalMessageType.IMAGE)
    doc = ConversationEvidenceDocument(
        document_id="doc_bad_zip",
        bundles=(MessageEvidenceBundle(message=msg),),
    )

    service = MediaAssetAccessService(archive_path=bad_zip)
    res_doc = service.process_document_media(document=doc, ocr_backend=MockSuccessOcrEngine())

    assert res_doc.metadata.get("multimodal_status") == MediaProcessingStatus.FAILED.value
    assert res_doc.metadata.get("multimodal_processed") is False


def test_session_isolation_and_conversation_switching():
    """Isolamento sessioni A/B e stato preservato al cambio conversazione A -> B -> A."""
    session_state: dict = {}
    state.init_session_state(session_state)

    msg_a = _make_test_message("a1", media_ref="doc_a_pic.png", msg_type=CanonicalMessageType.IMAGE)
    msg_b = _make_test_message("b1", media_ref="doc_b_pic.png", msg_type=CanonicalMessageType.IMAGE)

    doc_a = ConversationEvidenceDocument(
        document_id="chat_alpha",
        bundles=(MessageEvidenceBundle(message=msg_a),),
        metadata={"multimodal_status": "COMPLETE", "multimodal_processed": True},
    )
    doc_b = ConversationEvidenceDocument(
        document_id="chat_beta",
        bundles=(MessageEvidenceBundle(message=msg_b),),
        metadata={"multimodal_status": "NOT_PROCESSED", "multimodal_processed": False},
    )

    session_state["available_documents"] = {doc_a.document_id: doc_a, doc_b.document_id: doc_b}
    session_state["selected_document_id"] = doc_a.document_id
    session_state["conversation_document"] = doc_a

    # Conversazione A ha stato COMPLETE
    assert state.get_conversation_document(session_state).metadata["multimodal_status"] == "COMPLETE"

    # Passaggio a Conversazione B
    state.select_conversation(doc_b.document_id, state=session_state)
    assert state.get_conversation_document(session_state).metadata["multimodal_status"] == "NOT_PROCESSED"

    # Ritorno a Conversazione A: stato preservato intatto
    state.select_conversation(doc_a.document_id, state=session_state)
    assert state.get_conversation_document(session_state).metadata["multimodal_status"] == "COMPLETE"


def test_no_fake_vision_on_real_data(tmp_path: Path):
    """Nessun Fake Vision eseguito su dati reali se vision_backend non è configurato."""
    zip_path = tmp_path / "real_chat.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("scan.jpg", b"\xff\xd8\xff\xe0JFIF_real_photo")

    msg = _make_test_message("50", media_ref="scan.jpg", text="Foto reale", msg_type=CanonicalMessageType.IMAGE)
    doc = ConversationEvidenceDocument(
        document_id="doc_real_no_vision",
        bundles=(MessageEvidenceBundle(message=msg),),
        metadata={"data_classification": "FORENSIC_REAL"},
    )

    service = MediaAssetAccessService(archive_path=zip_path)
    res_doc = service.process_document_media(
        document=doc,
        ocr_backend=MockSuccessOcrEngine(),
        vision_backend=None,  # Vision assente
    )

    # Verifica che image_vision rimanga rigorosamente None
    assert res_doc.bundles[0].image_vision is None
    assert not any(sec.source_type == EvidenceSourceType.VISION_DESCRIPTION for sec in res_doc.all_evidence_sections)
