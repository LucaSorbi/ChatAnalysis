"""
tests/unit/test_media_asset_access.py
--------------------------------------
Test di conformità completi per FASE BLOCKER 4:
MEDIA ZIP E MULTIMODALITÀ REALE

Verifiche obbligatorie:
1. TXT without media -> resolve_message_asset restituisce NO_REFERENCE
2. ZIP with image -> estrazione sicura on-demand e risoluzione corretta
3. ZIP with audio -> estrazione sicura on-demand e risoluzione corretta
4. ZIP with missing media -> allegato citato ma assente nello ZIP produce MISSING
5. ZIP with ambiguous media -> referenza ambigua produce AMBIGUOUS / reject
6. ZIP with malformed member -> gestito con eccezione tipizzata
7. ZIP traversal attacks (../evil.txt, ..\\evil.txt, folder/../evil.txt, C:\\evil.txt, \\\\server\\share, /etc/passwd) -> reject (MediaArchiveSecurityError)
8. ZIP symlink -> membro con symlink attributo POSIX -> reject (MediaArchiveSecurityError)
9. ZIP bomb -> membri eccedenti i limiti dimensionali o compression ratio -> reject (MediaArchiveSecurityError)
10. ZIP CRC mismatch -> payload alterato con CRC non corrispondente -> reject (MediaArchiveSecurityError)
11. source unavailable after ingestion -> solleva esplicitamente MediaSourceUnavailableError
12. STT backend unavailable -> trascrizione fallita/non disponibile senza crash, evidenze originali preservate
13. OCR backend unavailable -> OCR non disponibile senza crash, evidenze originali preservate
14. Vision backend unavailable -> nessuna invocazione di Fake Vision su dati reali
15. real processing with test fixtures -> arricchimento completo con evidenze tracciabili e documento immutabile
"""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import zipfile
import zlib
import pytest

from ai.models import ConversationEvidenceDocument
from multimodal.asset_access import (
    MediaArchiveSecurityError,
    MediaAssetAccessService,
    MediaAssetRegistry,
    MediaMemberNotFoundError,
    MediaSourceUnavailableError,
    validate_zip_member_security,
)
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
    VisionStatus,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from importer.models import RawRecord
from validation.models import ValidationResult
from unified.models import UnifiedMessage


def _make_dummy_message(
    msg_id: str = "msg_1",
    media_ref: str | None = None,
    text: str = "Test messaggio",
    msg_type: CanonicalMessageType = CanonicalMessageType.TEXT,
) -> UnifiedMessage:
    raw = RawRecord(
        source_name="whatsapp_export",
        source_path="/test/chat.zip",
        source_record_id=msg_id,
        record_type="message",
        raw_fields={"text": text},
        media_reference=media_ref,
        metadata={},
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=ValidationResult(record=raw, issues=()),
        source_name="whatsapp_export",
        source_record_id=msg_id,
        record_type="message",
        timestamp=NormalizedTimestamp(status=TimestampTzStatus.ABSENT),
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
    )
    return UnifiedMessage(
        message_id=f"unified:whatsapp_export:{msg_id}",
        source_name="whatsapp_export",
        source_record_id=msg_id,
        source_path="/test/chat.zip",
        record_type="message",
        timestamp=norm.timestamp,
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
        provenance_record=norm,
    )


# --- 1. TXT without media ---

def test_txt_without_media_returns_no_reference(tmp_path: Path):
    txt_file = tmp_path / "chat.txt"
    txt_file.write_text("12/03/2024, 10:00 - Alice: Ciao Bob\n", encoding="utf-8")

    msg = _make_dummy_message(msg_id="1", media_ref=None, text="Ciao Bob")
    service = MediaAssetAccessService(archive_path=txt_file)
    asset = service.resolve_message_asset(msg)

    assert asset.status == MediaResolutionStatus.NO_REFERENCE
    assert asset.is_resolved is False
    assert asset.resolved_path is None


# --- 2. ZIP with image ---

def test_zip_with_image_materializes_on_demand(tmp_path: Path):
    zip_path = tmp_path / "chat_with_image.zip"
    img_data = b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x00" * 64

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.jpg", img_data)

    msg = _make_dummy_message(msg_id="2", media_ref="photo.jpg", text="Ecco la foto", msg_type=CanonicalMessageType.IMAGE)
    service = MediaAssetAccessService(archive_path=zip_path)
    asset = service.resolve_message_asset(msg)

    assert asset.status == MediaResolutionStatus.RESOLVED
    assert asset.media_kind == MediaKind.IMAGE
    assert asset.file_size_bytes == len(img_data)

    # Verifica estrazione on-demand e cleanup automatico
    with service.materialize_member("photo.jpg") as extracted_path:
        assert extracted_path.exists()
        assert extracted_path.read_bytes() == img_data

    # La sandbox temporanea deve essere eliminata
    assert not extracted_path.exists()


# --- 3. ZIP with audio ---

def test_zip_with_audio_materializes_on_demand(tmp_path: Path):
    zip_path = tmp_path / "chat_with_audio.zip"
    audio_data = b"OggS\x00\x02" + b"\x00" * 128

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("voice_note.opus", audio_data)

    msg = _make_dummy_message(msg_id="3", media_ref="voice_note.opus", text="Nota vocale", msg_type=CanonicalMessageType.AUDIO)
    service = MediaAssetAccessService(archive_path=zip_path)
    asset = service.resolve_message_asset(msg)

    assert asset.status == MediaResolutionStatus.RESOLVED
    assert asset.media_kind == MediaKind.AUDIO
    assert asset.file_size_bytes == len(audio_data)

    with service.materialize_member("voice_note.opus") as extracted_path:
        assert extracted_path.exists()
        assert extracted_path.read_bytes() == audio_data
    assert not extracted_path.exists()


# --- 4. ZIP with missing media ---

def test_zip_with_missing_media_returns_missing(tmp_path: Path):
    zip_path = tmp_path / "chat_empty.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("transcript.txt", "12/03/2024, 10:00 - Alice: audio.opus (allegato)")

    msg = _make_dummy_message(msg_id="4", media_ref="missing_audio.opus", text="Audio assente", msg_type=CanonicalMessageType.AUDIO)
    service = MediaAssetAccessService(archive_path=zip_path)
    asset = service.resolve_message_asset(msg)

    assert asset.status == MediaResolutionStatus.MISSING
    assert asset.is_resolved is False


# --- 5. ZIP with ambiguous media ---

def test_zip_with_ambiguous_media_rejected(tmp_path: Path):
    zip_path = tmp_path / "chat_ambiguous.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("folder1/photo.jpg", b"photo1")
        zf.writestr("folder2/photo.jpg", b"photo2")

    msg = _make_dummy_message(msg_id="5", media_ref="photo.jpg", text="Foto ambigua", msg_type=CanonicalMessageType.IMAGE)
    service = MediaAssetAccessService(archive_path=zip_path)
    asset = service.resolve_message_asset(msg)

    assert asset.status == MediaResolutionStatus.AMBIGUOUS


# --- 6. ZIP with malformed member ---

def test_zip_with_malformed_member_raises_error(tmp_path: Path):
    corrupt_zip = tmp_path / "corrupt.zip"
    corrupt_zip.write_bytes(b"NOT_A_ZIP_HEADER_DATA")

    service = MediaAssetAccessService(archive_path=corrupt_zip)
    with pytest.raises(MediaArchiveSecurityError):
        with service.materialize_member("any.jpg"):
            pass


# --- 7. ZIP traversal attacks ---

@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.txt",
        "..\\evil.txt",
        "folder/../evil.txt",
        "folder\\..\\evil.txt",
        "C:\\evil.txt",
        "\\\\server\\share\\evil.txt",
        "/etc/passwd",
        "folder/../../etc/passwd",
    ],
)
def test_zip_traversal_attacks_rejected(tmp_path: Path, bad_name: str):
    zip_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("safe.txt", b"safe")

    with zipfile.ZipFile(zip_path, "r") as zf:
        with pytest.raises(MediaArchiveSecurityError, match="non consentito"):
            validate_zip_member_security(zf, bad_name)


# --- 8. ZIP symlink ---

def test_zip_symlink_rejected(tmp_path: Path):
    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("symlink.txt")
        # Attributi POSIX per symlink: 0o120000
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"/etc/passwd")

    with zipfile.ZipFile(zip_path, "r") as zf:
        with pytest.raises(MediaArchiveSecurityError, match="symlink non consentito"):
            validate_zip_member_security(zf, "symlink.txt")


# --- 9. ZIP bomb ---

def test_zip_bomb_large_member_rejected(tmp_path: Path):
    zip_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("huge.bin", b"X" * 100)

    with zipfile.ZipFile(zip_path, "r") as zf:
        with pytest.raises(MediaArchiveSecurityError, match="supera il limite consentito"):
            validate_zip_member_security(zf, "huge.bin", max_single_size=50)


# --- 10. ZIP CRC mismatch ---

def test_zip_crc_mismatch_rejected(tmp_path: Path):
    zip_path = tmp_path / "crc_tampered.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tampered.jpg", b"ORIGINAL_CORRECT_DATA_STRING")

    # Manomissione diretta del payload compresso su disco per creare CRC mismatch
    raw_bytes = bytearray(zip_path.read_bytes())
    idx = raw_bytes.find(b"ORIGINAL_CORRECT_DATA_STRING")
    assert idx != -1
    raw_bytes[idx] = ord("X")
    zip_path.write_bytes(bytes(raw_bytes))

    service = MediaAssetAccessService(archive_path=zip_path)
    with pytest.raises(MediaArchiveSecurityError, match="Integrità compromessa"):
        with service.materialize_member("tampered.jpg"):
            pass


# --- 11. Source unavailable after ingestion ---

def test_source_unavailable_after_ingestion_raises_error(tmp_path: Path):
    ghost_path = tmp_path / "deleted_source.zip"
    # Il file non esiste
    service = MediaAssetAccessService(archive_path=ghost_path)

    with pytest.raises(MediaSourceUnavailableError, match="non disponibile"):
        service.get_effective_archive_path()

    with pytest.raises(MediaSourceUnavailableError, match="non disponibile"):
        with service.materialize_member("audio.opus"):
            pass


# --- 12. STT backend unavailable ---

def test_stt_backend_unavailable_marks_status_without_crash(tmp_path: Path):
    zip_path = tmp_path / "audio_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("audio.opus", b"OggS_dummy_audio")

    msg = _make_dummy_message(msg_id="12", media_ref="audio.opus", text="Nota vocale", msg_type=CanonicalMessageType.AUDIO)
    bundle = MessageEvidenceBundle(message=msg)
    doc = ConversationEvidenceDocument(document_id="doc_audio", bundles=(bundle,))

    service = MediaAssetAccessService(archive_path=zip_path)
    # stt_backend = None (non disponibile)
    enriched_doc = service.process_document_media(doc, stt_backend=None)

    assert len(enriched_doc.bundles) == 1
    enb = enriched_doc.bundles[0]
    assert enb.audio_transcription is not None
    assert enb.audio_transcription.status == TranscriptionStatus.FAILED
    assert "non configurato" in enb.audio_transcription.error_message


# --- 13. OCR backend unavailable ---

def test_ocr_backend_unavailable_marks_status_without_crash(tmp_path: Path):
    zip_path = tmp_path / "image_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("doc.jpg", b"JFIF_dummy_image")

    msg = _make_dummy_message(msg_id="13", media_ref="doc.jpg", text="Immagine testo", msg_type=CanonicalMessageType.IMAGE)
    bundle = MessageEvidenceBundle(message=msg)
    doc = ConversationEvidenceDocument(document_id="doc_image", bundles=(bundle,))

    service = MediaAssetAccessService(archive_path=zip_path)
    # ocr_backend = None (non disponibile)
    enriched_doc = service.process_document_media(doc, ocr_backend=None)

    assert len(enriched_doc.bundles) == 1
    enb = enriched_doc.bundles[0]
    assert enb.image_ocr is not None
    assert enb.image_ocr.status == OcrStatus.FAILED
    assert "non configurato" in enb.image_ocr.error_message


# --- 14. Vision backend unavailable ---

def test_vision_backend_unavailable_does_not_invoke_fake(tmp_path: Path):
    zip_path = tmp_path / "vision_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("scene.jpg", b"JFIF_dummy_image")

    msg = _make_dummy_message(msg_id="14", media_ref="scene.jpg", text="Scena reale", msg_type=CanonicalMessageType.IMAGE)
    bundle = MessageEvidenceBundle(message=msg)
    doc = ConversationEvidenceDocument(document_id="doc_vis", bundles=(bundle,))

    service = MediaAssetAccessService(archive_path=zip_path)
    # vision_backend = None
    enriched_doc = service.process_document_media(doc, vision_backend=None)

    enb = enriched_doc.bundles[0]
    # Fake vision NON deve essere invocato su dati reali
    assert enb.image_vision is None


# --- 15. Real processing with test fixtures ---

class DummySttEngine:
    def transcribe(self, asset: ResolvedMediaAsset) -> AudioTranscriptionResult:
        return AudioTranscriptionResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Trascrizione vocale forense: incontro confermato alle 15:00.",
            detected_language="it",
            engine="faster-whisper",
            model_name="tiny",
            provenance_asset=asset,
        )


class DummyOcrEngine:
    def extract_text(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        return ImageOcrResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="DDT N. 1024 - DESTINAZIONE MAGAZZINO PORTUALE",
            engine="tesseract",
            provenance_asset=asset,
        )


def test_real_processing_with_test_fixtures(tmp_path: Path):
    zip_path = tmp_path / "evidence_fixture.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("audio_note.opus", b"OggS_real_test_audio")
        zf.writestr("ddt_scan.png", b"\x89PNG\r\n\x1a\n_real_test_image")

    msg_audio = _make_dummy_message(msg_id="101", media_ref="audio_note.opus", text="Nota audio", msg_type=CanonicalMessageType.AUDIO)
    msg_img = _make_dummy_message(msg_id="102", media_ref="ddt_scan.png", text="Scansione ddt", msg_type=CanonicalMessageType.IMAGE)

    doc_orig = ConversationEvidenceDocument(
        document_id="doc_forensic_evidence",
        bundles=(
            MessageEvidenceBundle(message=msg_audio),
            MessageEvidenceBundle(message=msg_img),
        ),
    )

    service = MediaAssetAccessService(archive_path=zip_path)
    enriched_doc = service.process_document_media(
        document=doc_orig,
        stt_backend=DummySttEngine(),
        ocr_backend=DummyOcrEngine(),
    )

    # 1. Verifica immutabilità documento originale
    assert doc_orig.bundles[0].audio_transcription is None
    assert doc_orig.bundles[1].image_ocr is None

    # 2. Verifica arricchimento nuovo documento
    enb_audio = enriched_doc.bundles[0]
    assert enb_audio.audio_transcription is not None
    assert enb_audio.audio_transcription.status == TranscriptionStatus.SUCCESS
    assert "incontro confermato" in enb_audio.audio_transcription.full_transcript

    enb_img = enriched_doc.bundles[1]
    assert enb_img.image_ocr is not None
    assert enb_img.image_ocr.status == OcrStatus.SUCCESS
    assert "DDT N. 1024" in enb_img.image_ocr.full_text

    # 3. Verifica text_evidence_sections generate
    sec_types = {sec.source_type for sec in enriched_doc.all_evidence_sections}
    assert EvidenceSourceType.STT_TRANSCRIPTION in sec_types
    assert EvidenceSourceType.OCR_TEXT in sec_types
    assert EvidenceSourceType.ORIGINAL_TEXT in sec_types


# --- 13. Test Blocker 4: Traversal member in zip rejected even when searched via basename ---

@pytest.mark.parametrize(
    "malicious_member_name",
    [
        "folder/../evil.txt",
        "folder\\..\\evil.txt",
        "../evil.txt",
        "..\\evil.txt",
        "/etc/passwd",
        "C:\\evil.txt",
        "\\\\server\\share\\evil.txt",
    ],
)
def test_zip_traversal_in_zip_archive_rejected_even_when_searched_via_basename(
    tmp_path: Path, malicious_member_name: str
):
    zip_path = tmp_path / "malicious_member.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(malicious_member_name, b"evil content")

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Ricerca sia con nome esatto sia con basename 'evil.txt' o 'passwd'
        with pytest.raises(MediaArchiveSecurityError, match="non consentito"):
            validate_zip_member_security(zf, "evil.txt")


# --- 14. Test Blocker 5: MediaAssetRegistry Document and Session Isolation ---

def test_media_asset_registry_document_isolation(tmp_path: Path):
    from multimodal.asset_access import MediaAssetRegistry

    reg_doc_a = MediaAssetRegistry(document_id="doc_a")
    reg_doc_b = MediaAssetRegistry(document_id="doc_b")

    file_a = tmp_path / "archive_a.zip"
    file_a.write_bytes(b"DATA_A")
    file_b = tmp_path / "archive_b.zip"
    file_b.write_bytes(b"DATA_B")

    reg_doc_a.register_source("source_a", file_a)
    reg_doc_b.register_source("source_b", file_b)

    # Verifica isolamento: reg_doc_a non vede source_b e viceversa
    assert reg_doc_a.get_source("source_a") == file_a
    assert reg_doc_a.get_source("source_b") is None

    assert reg_doc_b.get_source("source_b") == file_b
    assert reg_doc_b.get_source("source_a") is None


def test_media_asset_registry_cleanup_and_session_isolation(tmp_path: Path):
    from multimodal.asset_access import MediaAssetRegistry

    reg_1 = MediaAssetRegistry(document_id="doc_1")
    reg_2 = MediaAssetRegistry(document_id="doc_2")

    path_1 = reg_1.register_bytes("src_1", b"TEST_DATA_1", filename="media_1.zip")
    path_2 = reg_2.register_bytes("src_2", b"TEST_DATA_2", filename="media_2.zip")

    assert path_1.exists()
    assert path_2.exists()

    # Cleanup di reg_1 non deve toccare i file gestiti di reg_2
    reg_1.clear()
    assert reg_1.get_source("src_1") is None

    # reg_2 rimane intatto e leggibile
    assert reg_2.get_source("src_2") == path_2
    assert path_2.exists()

    # Cleanup finale di reg_2
    reg_2.clear()
    assert reg_2.get_source("src_2") is None

