"""
tests/integration/test_ocr_pipeline_integration.py
--------------------------------------------------
Test di integrazione per la pipeline MultimodalOcrPipeline:
- Risoluzione dell'asset e invocazione controllata del motore OCR
- Garanzia assoluta: UnifiedMessage.text_content NON viene MAI alterato
- Rifiuto coerente di messaggi solo testo e messaggi audio da parte del layer OCR
- Gestione in streaming su batch di messaggi eterogenei
- Integrazione con file immagine reali di test_data (inclusi stub troncati)
- Integrità completa della catena di provenance:
  ImageOcrResult -> ResolvedMediaAsset -> UnifiedMessage -> NormalizedRecord -> RawRecord
"""
from __future__ import annotations

from pathlib import Path
import pytest
from PIL import Image

from importer.models import RawRecord
from multimodal.models import (
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
)
from multimodal.ocr import FakeImageTextExtractor, TesseractImageTextExtractor
from multimodal.pipeline import MultimodalOcrPipeline
from multimodal.resolver import MediaResolutionContext, MediaResolver
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_unified_message(
    source_record_id: str,
    media_ref: str | None,
    msg_type: CanonicalMessageType,
    text: str | None = "Testo originale messaggio",
    source_name: str = "msgstore_db",
) -> UnifiedMessage:
    raw = RawRecord(
        source_name=source_name,
        source_path="/path/test",
        source_record_id=source_record_id,
        record_type="message",
        raw_fields={"data": text},
        media_reference=media_ref,
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
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
    )
    return UnifiedMessage(
        message_id=f"unified:{source_name}:{source_record_id}",
        source_name=source_name,
        source_record_id=source_record_id,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=msg_type,
        text_content=text,
        media_reference=media_ref,
        provenance_record=norm,
    )


@pytest.mark.integration
class TestMultimodalOcrPipelineIntegration:

    def test_pipeline_ocr_extraction_preserves_unified_message(self, tmp_path: Path):
        """Verifica che l'elaborazione OCR estragga il testo senza intaccare text_content originale."""
        # Crea immagine fittizia
        img_file = tmp_path / "doc.png"
        img = Image.new("RGB", (100, 30), color="white")
        img.save(img_file)

        resolver = MediaResolver(allowed_roots=[tmp_path])
        extractor = FakeImageTextExtractor(default_text="IBAN IT99A0123456789")
        pipeline = MultimodalOcrPipeline(resolver=resolver, extractor=extractor)

        msg = _create_unified_message(
            source_record_id="101",
            media_ref="doc.png",
            msg_type=CanonicalMessageType.IMAGE,
            text="Ecco il documento richiesto",
        )

        asset, ocr_res = pipeline.process_message(msg)

        # Asset risolto
        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.IMAGE
        assert asset.file_size_bytes is not None and asset.file_size_bytes > 0
        assert asset.sha256 is not None

        # Risultato OCR
        assert ocr_res is not None
        assert ocr_res.status == OcrStatus.SUCCESS
        assert ocr_res.full_text == "IBAN IT99A0123456789"
        assert ocr_res.provenance_asset is asset
        assert ocr_res.asset_sha256 == asset.sha256

        # VINCOLO CRITICO: il messaggio unificato NON deve essere modificato
        assert msg.text_content == "Ecco il documento richiesto"
        assert msg.media_reference == "doc.png"

        # Verifica catena di provenance completa
        assert ocr_res.provenance_asset.provenance_message is msg
        assert ocr_res.provenance_asset.provenance_message.provenance_record is not None
        assert ocr_res.provenance_asset.provenance_message.provenance_record.raw_record is not None

    def test_pipeline_text_message_skips_ocr(self, tmp_path: Path):
        """Messaggi solo testo non producono alcun risultato OCR."""
        resolver = MediaResolver(allowed_roots=[tmp_path])
        extractor = FakeImageTextExtractor()
        pipeline = MultimodalOcrPipeline(resolver=resolver, extractor=extractor)

        msg = _create_unified_message(
            source_record_id="102",
            media_ref=None,
            msg_type=CanonicalMessageType.TEXT,
            text="Messaggio ordinario",
        )

        asset, ocr_res = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.NO_REFERENCE
        assert ocr_res is None
        assert msg.text_content == "Messaggio ordinario"

    def test_pipeline_audio_message_skips_ocr(self, tmp_path: Path):
        """Messaggi vocali/audio non attivano il motore OCR."""
        audio_file = tmp_path / "nota.opus"
        audio_file.write_bytes(b"dummy_audio")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        extractor = FakeImageTextExtractor()
        pipeline = MultimodalOcrPipeline(resolver=resolver, extractor=extractor)

        msg = _create_unified_message(
            source_record_id="103",
            media_ref="nota.opus",
            msg_type=CanonicalMessageType.AUDIO,
            text=None,
        )

        asset, ocr_res = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.AUDIO
        assert ocr_res is None

    def test_pipeline_streaming_batch_processing(self, tmp_path: Path):
        """Elaborazione batch in streaming con mix di messaggi testo, immagine e audio."""
        img_file = tmp_path / "scan.jpg"
        img = Image.new("RGB", (50, 50), color="white")
        img.save(img_file)

        resolver = MediaResolver(allowed_roots=[tmp_path])
        extractor = FakeImageTextExtractor(default_text="Scansione ricevuta")
        pipeline = MultimodalOcrPipeline(resolver=resolver, extractor=extractor)

        messages = [
            _create_unified_message("1", None, CanonicalMessageType.TEXT, text="Ciao"),
            _create_unified_message("2", "scan.jpg", CanonicalMessageType.IMAGE, text="Vedi allegato"),
            _create_unified_message("3", "missing.jpg", CanonicalMessageType.IMAGE, text="Non c'e'"),
        ]

        results = list(pipeline.process_messages(messages))
        assert len(results) == 3

        # Msg 1: Testo -> no OCR
        assert results[0][0].status == MediaResolutionStatus.NO_REFERENCE
        assert results[0][1] is None

        # Msg 2: Immagine presente -> OCR eseguito
        assert results[1][0].status == MediaResolutionStatus.RESOLVED
        assert results[1][1] is not None
        assert results[1][1].status == OcrStatus.SUCCESS

        # Msg 3: Immagine mancante -> asset MISSING, OCR non eseguito
        assert results[2][0].status == MediaResolutionStatus.MISSING
        assert results[2][1] is None

    def test_pipeline_with_real_truncated_image_dataset(self, monkeypatch):
        """
        Verifica integrazione con l'immagine reale IMG_00003.jpg presente in test_data/whatsapp_export/.
        Essendo uno stub di 220 byte troncato, TesseractImageTextExtractor deve gestirlo come FAILED
        senza provocare crash non controllati.
        """
        repo_root = Path(__file__).resolve().parent.parent.parent
        wa_dir = repo_root / "test_data" / "whatsapp_export"

        resolver = MediaResolver(allowed_roots=[wa_dir])
        extractor = TesseractImageTextExtractor()
        # Simula motore disponibile per verificare la decodifica Pillow sul file reale
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        pipeline = MultimodalOcrPipeline(resolver=resolver, extractor=extractor)

        msg = _create_unified_message(
            source_record_id="3",
            media_ref="WhatsApp Images/IMG_00003.jpg",
            msg_type=CanonicalMessageType.IMAGE,
            text="Foto troncata di test",
        )

        asset, ocr_res = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.file_size_bytes == 220
        assert ocr_res is not None
        # Pillow intercetta il troncamento e restituisce FAILED controllato
        assert ocr_res.status == OcrStatus.FAILED
        assert "image decoding/OCR error" in (ocr_res.error_message or "")
        assert ocr_res.provenance_asset is asset
