"""
tests/unit/test_multimodal_evidence.py
--------------------------------------
Test unitari per MessageEvidenceBundle e TextEvidenceSection:
- Creazione e immutabilità profonda di MessageEvidenceBundle
- Validazione concordanza SIMULTANEA (message_id, source_name, source_record_id)
- Validazione obbligatoria catena di provenance completa per risultati SUCCESS
- Estrazione deterministica e ordinata di text_evidence_sections con evidence_id
- Preservazione esatta del testo sorgente (nessuna mutazione o strip nel contenuto)
- Inclusione di VISION_OBSERVATION distinte con ordinamento e ordinal
- Copertura di tutte le combinazioni di evidenze
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from importer.models import RawRecord
from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
    TextEvidenceSection,
)
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    ImageVisionResult,
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
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_message(
    source_record_id: str,
    text: str | None = "Testo originale del messaggio",
    source_name: str = "msgstore_db",
) -> UnifiedMessage:
    raw = RawRecord(
        source_name=source_name,
        source_path="/path/test",
        source_record_id=source_record_id,
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
        source_name=source_name,
        source_record_id=source_record_id,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT if text else CanonicalMessageType.IMAGE,
        text_content=text,
        media_reference=None,
    )
    return UnifiedMessage(
        message_id=f"unified:{source_name}:{source_record_id}",
        source_name=source_name,
        source_record_id=source_record_id,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT if text else CanonicalMessageType.IMAGE,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )


def _make_dummy_asset(msg: UnifiedMessage, media_kind: MediaKind = MediaKind.IMAGE) -> ResolvedMediaAsset:
    return ResolvedMediaAsset(
        message_id=msg.message_id,
        source_name=msg.source_name,
        source_record_id=msg.source_record_id,
        raw_reference="media/file.ext",
        resolved_path="/path/file.ext",
        media_kind=media_kind,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=1024,
        sha256="fake_sha",
        provenance_message=msg,
    )


@pytest.mark.unit
class TestMessageEvidenceBundle:

    def test_bundle_only_original_text(self):
        msg = _create_message("10", text="Messaggio originale senza allegati")
        bundle = MessageEvidenceBundle(message=msg)

        assert bundle.message_id == msg.message_id
        assert bundle.source_name == msg.source_name
        assert bundle.source_record_id == msg.source_record_id
        assert bundle.audio_transcription is None
        assert bundle.image_ocr is None
        assert bundle.image_vision is None

        sections = bundle.text_evidence_sections
        assert len(sections) == 1
        assert sections[0].evidence_id == f"{msg.message_id}::ORIGINAL_TEXT"
        assert sections[0].source_type == EvidenceSourceType.ORIGINAL_TEXT
        assert sections[0].text == "Messaggio originale senza allegati"
        assert sections[0].message_id == msg.message_id
        assert sections[0].source_name == msg.source_name
        assert sections[0].source_record_id == msg.source_record_id

    def test_bundle_exact_text_preserved_no_strip(self):
        # Spazi iniziali e finali voluti per verificare che il testo sia preservato fedelmente
        msg = _create_message("10_spaces", text="   Testo con spazi iniziali e finali \n  ")
        bundle = MessageEvidenceBundle(message=msg)

        sections = bundle.text_evidence_sections
        assert len(sections) == 1
        assert sections[0].text == "   Testo con spazi iniziali e finali \n  "

    def test_bundle_original_text_and_stt(self):
        msg = _create_message("11", text="Ti mando una nota vocale")
        asset = _make_dummy_asset(msg, media_kind=MediaKind.AUDIO)
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="  Trascrizione vocale audio con spazi  ",
            detected_language="it",
            provenance_asset=asset,
        )

        bundle = MessageEvidenceBundle(message=msg, audio_transcription=stt)
        sections = bundle.text_evidence_sections

        assert len(sections) == 2
        assert sections[0].evidence_id == f"{msg.message_id}::ORIGINAL_TEXT"
        assert sections[0].source_type == EvidenceSourceType.ORIGINAL_TEXT
        assert sections[0].text == "Ti mando una nota vocale"

        assert sections[1].evidence_id == f"{msg.message_id}::STT_TRANSCRIPTION"
        assert sections[1].source_type == EvidenceSourceType.STT_TRANSCRIPTION
        assert sections[1].text == "  Trascrizione vocale audio con spazi  "
        assert sections[1].language == "it"
        assert sections[1].message_id == msg.message_id

        # Nessuna mutazione su msg.text_content
        assert msg.text_content == "Ti mando una nota vocale"

    def test_bundle_original_text_and_ocr(self):
        msg = _create_message("12", text="Vedi scontrino")
        asset = _make_dummy_asset(msg, media_kind=MediaKind.IMAGE)
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="TOTALE EURO 45.50",
            language_config="ita+eng",
            provenance_asset=asset,
        )

        bundle = MessageEvidenceBundle(message=msg, image_ocr=ocr)
        sections = bundle.text_evidence_sections

        assert len(sections) == 2
        assert sections[0].evidence_id == f"{msg.message_id}::ORIGINAL_TEXT"
        assert sections[1].evidence_id == f"{msg.message_id}::OCR_TEXT"
        assert sections[1].source_type == EvidenceSourceType.OCR_TEXT
        assert sections[1].text == "TOTALE EURO 45.50"
        assert sections[1].language == "ita+eng"

    def test_bundle_original_text_and_vision_with_observations(self):
        msg = _create_message("13", text="Ecco dove sono")
        asset = _make_dummy_asset(msg, media_kind=MediaKind.IMAGE)
        vision = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.SUCCESS,
            description="Piazza cittadina con fontana centrale e porticato",
            observations=("Fontana in pietra", "Edificio storico con colonne"),
            provenance_asset=asset,
        )

        bundle = MessageEvidenceBundle(message=msg, image_vision=vision)
        sections = bundle.text_evidence_sections

        # Deve generare:
        # 1. ORIGINAL_TEXT
        # 2. VISION_DESCRIPTION
        # 3. VISION_OBSERVATION (idx 0)
        # 4. VISION_OBSERVATION (idx 1)
        assert len(sections) == 4
        assert sections[0].evidence_id == f"{msg.message_id}::ORIGINAL_TEXT"
        assert sections[1].evidence_id == f"{msg.message_id}::VISION_DESCRIPTION"
        assert sections[1].text == "Piazza cittadina con fontana centrale e porticato"

        assert sections[2].evidence_id == f"{msg.message_id}::VISION_OBSERVATION::0"
        assert sections[2].source_type == EvidenceSourceType.VISION_OBSERVATION
        assert sections[2].text == "Fontana in pietra"
        assert sections[2].ordinal == 0

        assert sections[3].evidence_id == f"{msg.message_id}::VISION_OBSERVATION::1"
        assert sections[3].source_type == EvidenceSourceType.VISION_OBSERVATION
        assert sections[3].text == "Edificio storico con colonne"
        assert sections[3].ordinal == 1

    def test_bundle_all_evidences_combined_deterministic_order(self):
        msg = _create_message("14", text="Testo di accompagnamento")
        asset_audio = _make_dummy_asset(msg, media_kind=MediaKind.AUDIO)
        asset_img = _make_dummy_asset(msg, media_kind=MediaKind.IMAGE)

        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Audio trascritto con successo",
            detected_language="it",
            provenance_asset=asset_audio,
        )
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo riconosciuto da OCR",
            language_config="ita",
            provenance_asset=asset_img,
        )
        vision = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.SUCCESS,
            description="Descrizione visiva scena",
            observations=("Elemento 1", "Elemento 2"),
            provenance_asset=asset_img,
        )

        bundle = MessageEvidenceBundle(
            message=msg,
            audio_transcription=stt,
            image_ocr=ocr,
            image_vision=vision,
        )
        sections = bundle.text_evidence_sections

        # Ordine rigido e deterministico:
        # 1. ORIGINAL_TEXT
        # 2. STT_TRANSCRIPTION
        # 3. OCR_TEXT
        # 4. VISION_DESCRIPTION
        # 5. VISION_OBSERVATION::0
        # 6. VISION_OBSERVATION::1
        assert len(sections) == 6
        assert sections[0].source_type == EvidenceSourceType.ORIGINAL_TEXT
        assert sections[1].source_type == EvidenceSourceType.STT_TRANSCRIPTION
        assert sections[2].source_type == EvidenceSourceType.OCR_TEXT
        assert sections[3].source_type == EvidenceSourceType.VISION_DESCRIPTION
        assert sections[4].source_type == EvidenceSourceType.VISION_OBSERVATION
        assert sections[4].ordinal == 0
        assert sections[5].source_type == EvidenceSourceType.VISION_OBSERVATION
        assert sections[5].ordinal == 1

        # Verifica fedeltà testuale e assenza di concatenazioni
        assert msg.text_content == "Testo di accompagnamento"

    def test_failed_or_empty_evidences_excluded_from_sections(self):
        msg = _create_message("15", text=None)  # nessun testo originale
        asset = _make_dummy_asset(msg)

        # STT fallito
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.FAILED,
            full_transcript="",
            provenance_asset=asset,
        )
        # OCR no text
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.NO_TEXT,
            full_text="",
            provenance_asset=asset,
        )
        # Vision no content
        vision = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.NO_CONTENT,
            description="",
            provenance_asset=asset,
        )

        bundle = MessageEvidenceBundle(
            message=msg,
            audio_transcription=stt,
            image_ocr=ocr,
            image_vision=vision,
        )
        # Nessuna sezione deve essere prodotta
        assert len(bundle.text_evidence_sections) == 0

    def test_bundle_deep_immutability(self):
        msg = _create_message("16", text="Originale")
        bundle = MessageEvidenceBundle(message=msg, metadata={"info": {"tag": "val"}})

        with pytest.raises(FrozenInstanceError):
            bundle.message = msg  # type: ignore[misc]

        with pytest.raises(TypeError):
            bundle.metadata["info"]["tag"] = "modificato"  # type: ignore[index]

    # --- Test negativi indipendenti per A1 (message_id, source_name, source_record_id) ---

    def test_provenance_mismatch_audio_message_id_rejected(self):
        msg_a = _create_message("17", text="Test A")
        msg_b = _create_message("99", text="Test B")
        asset_b = _make_dummy_asset(msg_b, media_kind=MediaKind.AUDIO)
        stt_b = AudioTranscriptionResult(
            message_id=msg_b.message_id,
            source_name=msg_b.source_name,
            source_record_id=msg_b.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Audio B",
            provenance_asset=asset_b,
        )
        with pytest.raises(ValueError, match="Discordanza message_id"):
            MessageEvidenceBundle(message=msg_a, audio_transcription=stt_b)

    def test_provenance_mismatch_audio_source_name_rejected(self):
        msg = _create_message("17", text="Test", source_name="msgstore_db")
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name="cellebrite_csv",
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.FAILED,
            full_transcript="",
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_name"):
            MessageEvidenceBundle(message=msg, audio_transcription=stt)

    def test_provenance_mismatch_audio_source_record_id_rejected(self):
        msg = _create_message("17", text="Test")
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id="999_DIVERSO",
            status=TranscriptionStatus.FAILED,
            full_transcript="",
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_record_id"):
            MessageEvidenceBundle(message=msg, audio_transcription=stt)

    def test_provenance_mismatch_ocr_fields_rejected(self):
        msg = _create_message("18", text="Test")

        ocr_bad_msg_id = ImageOcrResult(
            message_id="unified:msgstore_db:BAD",
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza message_id"):
            MessageEvidenceBundle(message=msg, image_ocr=ocr_bad_msg_id)

        ocr_bad_source = ImageOcrResult(
            message_id=msg.message_id,
            source_name="BAD_SOURCE",
            source_record_id=msg.source_record_id,
            status=OcrStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_name"):
            MessageEvidenceBundle(message=msg, image_ocr=ocr_bad_source)

        ocr_bad_rec_id = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id="BAD_REC",
            status=OcrStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_record_id"):
            MessageEvidenceBundle(message=msg, image_ocr=ocr_bad_rec_id)

    def test_provenance_mismatch_vision_fields_rejected(self):
        msg = _create_message("19", text="Test")

        vis_bad_msg_id = ImageVisionResult(
            message_id="unified:msgstore_db:BAD",
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza message_id"):
            MessageEvidenceBundle(message=msg, image_vision=vis_bad_msg_id)

        vis_bad_source = ImageVisionResult(
            message_id=msg.message_id,
            source_name="BAD_SOURCE",
            source_record_id=msg.source_record_id,
            status=VisionStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_name"):
            MessageEvidenceBundle(message=msg, image_vision=vis_bad_source)

        vis_bad_rec_id = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id="BAD_REC",
            status=VisionStatus.FAILED,
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="Discordanza source_record_id"):
            MessageEvidenceBundle(message=msg, image_vision=vis_bad_rec_id)

    # --- Test per A2: catena di provenance obbligatoria per risultati SUCCESS ---

    def test_success_without_provenance_asset_rejected(self):
        msg = _create_message("20", text="Test")

        # Audio SUCCESS senza asset
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Parole",
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="deve possedere un provenance_asset valido"):
            MessageEvidenceBundle(message=msg, audio_transcription=stt)

        # OCR SUCCESS senza asset
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo",
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="deve possedere un provenance_asset valido"):
            MessageEvidenceBundle(message=msg, image_ocr=ocr)

        # Vision SUCCESS senza asset
        vision = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.SUCCESS,
            description="Scena",
            provenance_asset=None,
        )
        with pytest.raises(ValueError, match="deve possedere un provenance_asset valido"):
            MessageEvidenceBundle(message=msg, image_vision=vision)

    def test_success_with_asset_missing_provenance_message_rejected(self):
        msg = _create_message("21", text="Test")
        # Asset senza provenance_message
        orphan_asset = ResolvedMediaAsset(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            raw_reference="test.jpg",
            resolved_path="/test.jpg",
            media_kind=MediaKind.IMAGE,
            status=MediaResolutionStatus.RESOLVED,
            provenance_message=None,
        )

        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo",
            provenance_asset=orphan_asset,
        )
        with pytest.raises(ValueError, match="deve possedere un provenance_message collegato"):
            MessageEvidenceBundle(message=msg, image_ocr=ocr)
