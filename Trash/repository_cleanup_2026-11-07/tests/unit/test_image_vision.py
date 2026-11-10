"""
tests/unit/test_image_vision.py
-------------------------------
Test unitari per la foundation di Image Vision:
- Modelli dati immutabili (VisionStatus, ImageVisionResult)
- Validazione invarianti di provenance e observations
- Comportamento deterministico di FakeImageVisionAnalyzer
- Rifiuto coerente di asset non risolti o non immagine
- Coordinamento in MultimodalVisionPipeline
- Garanzia assoluta: nessuna alterazione di UnifiedMessage.text_content
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest
from PIL import Image

from importer.models import RawRecord
from multimodal.models import (
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    ResolvedMediaAsset,
    VisionStatus,
)
from multimodal.pipeline import MultimodalVisionPipeline
from multimodal.resolver import MediaResolver
from multimodal.vision import (
    BaseImageVisionAnalyzer,
    FakeImageVisionAnalyzer,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_dummy_image_asset(
    sha256: str | None = "vision_sha256",
    status: MediaResolutionStatus = MediaResolutionStatus.RESOLVED,
    media_kind: MediaKind = MediaKind.IMAGE,
) -> ResolvedMediaAsset:
    return ResolvedMediaAsset(
        message_id="unified:msgstore_db:88",
        source_name="msgstore_db",
        source_record_id="88",
        raw_reference="WhatsApp Images/IMG_00088.jpg",
        resolved_path="/path/IMG_00088.jpg",
        media_kind=media_kind,
        status=status,
        file_size_bytes=4096,
        sha256=sha256,
    )


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


@pytest.mark.unit
class TestImageVisionModels:

    def test_vision_status_enum_values(self):
        expected = {"SUCCESS", "NO_CONTENT", "FAILED", "NO_IMAGE", "UNSUPPORTED"}
        actual = {s.value for s in VisionStatus}
        assert actual == expected

    def test_valid_image_vision_result(self):
        asset = _make_dummy_image_asset()
        res = ImageVisionResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=VisionStatus.SUCCESS,
            description="Scrivania da ufficio con faldoni e documenti",
            observations=("Presenza di fogli timbrati", "Timbro circolare rosso"),
            engine="fake-vision",
            model_name="mock-vision",
            provenance_asset=asset,
            metadata={"detail_level": "high"},
        )

        assert res.status == VisionStatus.SUCCESS
        assert res.description == "Scrivania da ufficio con faldoni e documenti"
        assert len(res.observations) == 2
        assert res.provenance_asset is asset
        assert res.asset_sha256 == "vision_sha256"
        assert res.asset_sha256 == asset.sha256

    def test_result_without_asset_has_none_sha256(self):
        res = ImageVisionResult(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            status=VisionStatus.NO_IMAGE,
        )
        assert res.provenance_asset is None
        assert res.asset_sha256 is None

    def test_observations_must_be_strings(self):
        with pytest.raises(ValueError):
            ImageVisionResult(
                message_id="msg:1",
                source_name="src",
                source_record_id="1",
                status=VisionStatus.SUCCESS,
                observations=("Valida", 123),  # type: ignore[arg-type]
            )

    def test_deep_immutability(self):
        res = ImageVisionResult(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            status=VisionStatus.SUCCESS,
            metadata={"nested": {"counter": 1}},
        )
        with pytest.raises(FrozenInstanceError):
            res.status = VisionStatus.FAILED  # type: ignore[misc]
        with pytest.raises(TypeError):
            res.metadata["nested"]["counter"] = 2  # type: ignore[index]

    def test_provenance_mismatch_rejected(self):
        asset = _make_dummy_image_asset()

        # Mismatch message_id
        with pytest.raises(ValueError, match="Incoerenza di provenance"):
            ImageVisionResult(
                message_id="unified:msgstore_db:999",
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=VisionStatus.SUCCESS,
                provenance_asset=asset,
            )

        # Mismatch source_name
        with pytest.raises(ValueError, match="Incoerenza di provenance"):
            ImageVisionResult(
                message_id=asset.message_id,
                source_name="altra_sorgente",
                source_record_id=asset.source_record_id,
                status=VisionStatus.SUCCESS,
                provenance_asset=asset,
            )

        # Mismatch source_record_id
        with pytest.raises(ValueError, match="Incoerenza di provenance"):
            ImageVisionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id="999",
                status=VisionStatus.SUCCESS,
                provenance_asset=asset,
            )


@pytest.mark.unit
class TestFakeImageVisionAnalyzer:

    def test_analyze_resolved_image_success(self):
        analyzer = FakeImageVisionAnalyzer(default_description="Contratto commerciale")
        asset = _make_dummy_image_asset()

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.SUCCESS
        assert res.description == "Contratto commerciale"
        assert len(res.observations) > 0
        assert res.provenance_asset is asset
        assert res.asset_sha256 == asset.sha256

    def test_reject_unresolved_asset(self):
        analyzer = FakeImageVisionAnalyzer()
        asset = _make_dummy_image_asset(status=MediaResolutionStatus.MISSING)

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.NO_IMAGE
        assert res.description == ""
        assert len(res.observations) == 0
        assert "Asset non risolto" in (res.error_message or "")
        assert res.provenance_asset is asset

    def test_reject_non_image_asset(self):
        analyzer = FakeImageVisionAnalyzer()
        asset = _make_dummy_image_asset(media_kind=MediaKind.AUDIO)

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.UNSUPPORTED
        assert res.description == ""
        assert "AUDIO" in (res.error_message or "")

    def test_simulated_failure(self):
        analyzer = FakeImageVisionAnalyzer(simulate_failure=True, failure_error="Simulated model out-of-memory")
        asset = _make_dummy_image_asset()

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.FAILED
        assert res.error_message == "Simulated model out-of-memory"
        assert res.description == ""
        assert res.provenance_asset is asset

    def test_simulated_no_content(self):
        analyzer = FakeImageVisionAnalyzer(simulate_no_content=True)
        asset = _make_dummy_image_asset()

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.NO_CONTENT
        assert res.description == ""
        assert len(res.observations) == 0
        assert res.provenance_asset is asset

    def test_custom_canned_observations(self):
        canned = ("Tavolo in vetro", "Ricevuta fiscale accartocciata")
        analyzer = FakeImageVisionAnalyzer(canned_observations=canned)
        asset = _make_dummy_image_asset()

        res = analyzer.analyze(asset)
        assert res.status == VisionStatus.SUCCESS
        assert res.observations == canned


@pytest.mark.unit
class TestMultimodalVisionPipeline:

    def test_pipeline_vision_analysis_preserves_unified_message(self, tmp_path: Path):
        img_file = tmp_path / "scene.png"
        img = Image.new("RGB", (80, 80), color="blue")
        img.save(img_file)

        resolver = MediaResolver(allowed_roots=[tmp_path])
        analyzer = FakeImageVisionAnalyzer(default_description="Immagine con sfondo blu uniforme")
        pipeline = MultimodalVisionPipeline(resolver=resolver, analyzer=analyzer)

        msg = _create_unified_message(
            source_record_id="201",
            media_ref="scene.png",
            msg_type=CanonicalMessageType.IMAGE,
            text="Guarda la foto",
        )

        asset, vision_res = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.IMAGE
        assert vision_res is not None
        assert vision_res.status == VisionStatus.SUCCESS
        assert vision_res.description == "Immagine con sfondo blu uniforme"
        assert vision_res.provenance_asset is asset

        # VINCOLO CRITICO: Il UnifiedMessage originale non deve essere alterato!
        assert msg.text_content == "Guarda la foto"
        assert msg.media_reference == "scene.png"

    def test_pipeline_skips_text_and_audio_messages(self, tmp_path: Path):
        audio_file = tmp_path / "clip.opus"
        audio_file.write_bytes(b"dummy")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        analyzer = FakeImageVisionAnalyzer()
        pipeline = MultimodalVisionPipeline(resolver=resolver, analyzer=analyzer)

        msg_text = _create_unified_message("202", None, CanonicalMessageType.TEXT, text="Solo testo")
        asset1, res1 = pipeline.process_message(msg_text)
        assert asset1.status == MediaResolutionStatus.NO_REFERENCE
        assert res1 is None

        msg_audio = _create_unified_message("203", "clip.opus", CanonicalMessageType.AUDIO, text=None)
        asset2, res2 = pipeline.process_message(msg_audio)
        assert asset2.status == MediaResolutionStatus.RESOLVED
        assert asset2.media_kind == MediaKind.AUDIO
        assert res2 is None
