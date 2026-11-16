"""
tests/unit/test_image_ocr_models.py
-----------------------------------
Test unitari per i modelli di dati del layer OCR:
- Immutabilità profonda di OcrTextRegion e ImageOcrResult
- Validazione dei vincoli (confidence in [0.0, 1.0], bounding_box 4-tupla, provenance_asset)
- Correttezza della property asset_sha256
- Valori dell'Enum OcrStatus
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from multimodal.models import (
    ImageOcrResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
)


def _make_dummy_image_asset(
    sha256: str | None = "abc123sha256",
    status: MediaResolutionStatus = MediaResolutionStatus.RESOLVED,
) -> ResolvedMediaAsset:
    return ResolvedMediaAsset(
        message_id="unified:src:1",
        source_name="src",
        source_record_id="1",
        raw_reference="WhatsApp Images/IMG_00001.jpg",
        resolved_path="/path/IMG_00001.jpg",
        media_kind=MediaKind.IMAGE,
        status=status,
        file_size_bytes=1024,
        sha256=sha256,
    )


@pytest.mark.unit
class TestOcrTextRegion:

    def test_valid_region_creation(self):
        region = OcrTextRegion(
            text="Fattura",
            bounding_box=(10, 20, 100, 30),
            confidence=0.95,
            order_index=0,
        )
        assert region.text == "Fattura"
        assert region.bounding_box == (10, 20, 100, 30)
        assert region.confidence == 0.95
        assert region.order_index == 0

    def test_region_immutability(self):
        region = OcrTextRegion(text="Test")
        with pytest.raises(FrozenInstanceError):
            region.text = "Altro"  # type: ignore[misc]

    def test_confidence_validation_bounds(self):
        # Validi: estremi 0.0 e 1.0
        OcrTextRegion(text="Test", confidence=0.0)
        OcrTextRegion(text="Test", confidence=1.0)

        # Invalidi
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", confidence=-0.01)
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", confidence=1.01)

    def test_bounding_box_validation(self):
        # Valido: tupla o lista di 4 elementi
        r = OcrTextRegion(text="Test", bounding_box=[1, 2, 3, 4])  # type: ignore[arg-type]
        assert r.bounding_box == (1, 2, 3, 4)

        # Invalido: non 4 elementi
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", bounding_box=(1, 2, 3))  # type: ignore[arg-type]

        # Invalido: coordinate negative
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", bounding_box=(-1, 2, 10, 10))

        # Invalido: dimensioni negative
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", bounding_box=(0, 0, -5, 10))

    def test_order_index_validation(self):
        OcrTextRegion(text="Test", order_index=0)
        with pytest.raises(ValueError):
            OcrTextRegion(text="Test", order_index=-1)


@pytest.mark.unit
class TestImageOcrResult:

    def test_valid_result_with_provenance_and_sha256(self):
        asset = _make_dummy_image_asset(sha256="deadbeefcafe")
        result = ImageOcrResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo riconosciuto",
            regions=(
                OcrTextRegion(text="Testo", confidence=0.99),
                OcrTextRegion(text="riconosciuto", confidence=0.98),
            ),
            language_config="ita",
            engine="tesseract",
            engine_version="5.3.0",
            provenance_asset=asset,
            metadata={"source": "test"},
        )

        assert result.status == OcrStatus.SUCCESS
        assert result.full_text == "Testo riconosciuto"
        assert len(result.regions) == 2
        assert result.language_config == "ita"
        assert result.provenance_asset is asset
        assert result.asset_sha256 == "deadbeefcafe"
        assert result.asset_sha256 == asset.sha256

    def test_result_without_asset_has_none_sha256(self):
        result = ImageOcrResult(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            status=OcrStatus.NO_IMAGE,
            full_text="",
        )
        assert result.provenance_asset is None
        assert result.asset_sha256 is None

    def test_invalid_provenance_asset_rejected(self):
        with pytest.raises(ValueError):
            ImageOcrResult(
                message_id="msg:1",
                source_name="src",
                source_record_id="1",
                status=OcrStatus.SUCCESS,
                provenance_asset="non_un_asset",  # type: ignore[arg-type]
            )

    def test_provenance_mismatch_message_id_rejected(self):
        asset = _make_dummy_image_asset()
        with pytest.raises(ValueError) as excinfo:
            ImageOcrResult(
                message_id="unified:src:DIVERSO",
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.SUCCESS,
                provenance_asset=asset,
            )
        assert "Incoerenza di provenance" in str(excinfo.value)

    def test_provenance_mismatch_source_name_rejected(self):
        asset = _make_dummy_image_asset()
        with pytest.raises(ValueError) as excinfo:
            ImageOcrResult(
                message_id=asset.message_id,
                source_name="altra_sorgente",
                source_record_id=asset.source_record_id,
                status=OcrStatus.SUCCESS,
                provenance_asset=asset,
            )
        assert "Incoerenza di provenance" in str(excinfo.value)

    def test_provenance_mismatch_source_record_id_rejected(self):
        asset = _make_dummy_image_asset()
        with pytest.raises(ValueError) as excinfo:
            ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id="999",
                status=OcrStatus.SUCCESS,
                provenance_asset=asset,
            )
        assert "Incoerenza di provenance" in str(excinfo.value)

    def test_result_deep_immutability(self):
        result = ImageOcrResult(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            status=OcrStatus.SUCCESS,
            metadata={"nested": {"key": "val"}},
        )
        with pytest.raises(FrozenInstanceError):
            result.status = OcrStatus.FAILED  # type: ignore[misc]

        with pytest.raises(TypeError):
            result.metadata["nested"]["key"] = "hacked"  # type: ignore[index]

    def test_ocr_status_enum_values(self):
        expected = {"SUCCESS", "NO_TEXT", "FAILED", "NO_IMAGE", "UNSUPPORTED"}
        actual = {s.value for s in OcrStatus}
        assert actual == expected

