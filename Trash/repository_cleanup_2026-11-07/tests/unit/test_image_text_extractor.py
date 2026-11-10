"""
tests/unit/test_image_text_extractor.py
---------------------------------------
Test unitari per i motori di estrazione OCR:
- Funzionamento di FakeImageTextExtractor per test veloci, offline e deterministici
- Gestione di asset non risolti (NO_IMAGE)
- Gestione di asset di tipo non immagine (UNSUPPORTED)
- Gestione di simulazione fallimento controllato (FAILED)
- Gestione di immagini prive di testo (NO_TEXT)
- Verifica della disponibilità di TesseractImageTextExtractor (is_available)
- Errore globale backend se binario assente (OcrBackendUnavailableError)
- Gestione protetta dei file troncati o corrotti sul filesystem (FAILED senza crash)
- Parsing e normalizzazione corretta delle coordinate e confidenze Tesseract
- Smoke test condizionato su Tesseract reale se installato
"""
from __future__ import annotations

from pathlib import Path
import pytest
from PIL import Image

from multimodal.models import (
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
)
from multimodal.ocr import (
    FakeImageTextExtractor,
    OcrBackendUnavailableError,
    TesseractImageTextExtractor,
)


def _make_image_asset(
    resolved_path: str | None = "/path/test.jpg",
    status: MediaResolutionStatus = MediaResolutionStatus.RESOLVED,
    media_kind: MediaKind = MediaKind.IMAGE,
    sha256: str | None = "fake_image_sha256",
) -> ResolvedMediaAsset:
    return ResolvedMediaAsset(
        message_id="unified:msgstore_db:50",
        source_name="msgstore_db",
        source_record_id="50",
        raw_reference="WhatsApp Images/IMG_00050.jpg",
        resolved_path=resolved_path,
        media_kind=media_kind,
        status=status,
        file_size_bytes=2048,
        sha256=sha256,
    )


@pytest.mark.unit
class TestFakeImageTextExtractor:

    def test_extract_resolved_image_success(self):
        extractor = FakeImageTextExtractor(default_text="Documento di prova")
        asset = _make_image_asset()

        res = extractor.extract(asset)
        assert res.status == OcrStatus.SUCCESS
        assert res.message_id == asset.message_id
        assert res.full_text == "Documento di prova"
        assert len(res.regions) > 0
        assert res.provenance_asset is asset
        assert res.asset_sha256 == asset.sha256
        assert res.language_config == "ita+eng"

    def test_reject_unresolved_asset(self):
        extractor = FakeImageTextExtractor()
        asset = _make_image_asset(status=MediaResolutionStatus.MISSING, resolved_path=None)

        res = extractor.extract(asset)
        assert res.status == OcrStatus.NO_IMAGE
        assert res.full_text == ""
        assert len(res.regions) == 0
        assert "Asset non risolto" in (res.error_message or "")
        assert res.provenance_asset is asset

    def test_reject_non_image_asset(self):
        extractor = FakeImageTextExtractor()
        asset = _make_image_asset(media_kind=MediaKind.AUDIO)

        res = extractor.extract(asset)
        assert res.status == OcrStatus.UNSUPPORTED
        assert res.full_text == ""
        assert len(res.regions) == 0
        assert "AUDIO" in (res.error_message or "")

    def test_simulated_failure_handled_gracefully(self):
        extractor = FakeImageTextExtractor(simulate_failure=True, failure_error="Simulated OCR crash")
        asset = _make_image_asset()

        res = extractor.extract(asset)
        assert res.status == OcrStatus.FAILED
        assert res.error_message == "Simulated OCR crash"
        assert res.full_text == ""
        assert res.provenance_asset is asset

    def test_simulated_no_text(self):
        extractor = FakeImageTextExtractor(simulate_no_text=True)
        asset = _make_image_asset()

        res = extractor.extract(asset)
        assert res.status == OcrStatus.NO_TEXT
        assert res.full_text == ""
        assert len(res.regions) == 0
        assert res.provenance_asset is asset

    def test_custom_canned_regions(self):
        canned = (
            OcrTextRegion(text="Codice", bounding_box=(0, 0, 50, 15), confidence=0.99, order_index=0),
            OcrTextRegion(text="Fiscale", bounding_box=(55, 0, 60, 15), confidence=0.98, order_index=1),
        )
        extractor = FakeImageTextExtractor(canned_regions=canned)
        asset = _make_image_asset()

        res = extractor.extract(asset)
        assert res.status == OcrStatus.SUCCESS
        assert res.regions == canned
        assert res.full_text == "Codice Fiscale"


@pytest.mark.unit
class TestTesseractImageTextExtractorUnit:

    def test_is_available_returns_bool_without_crashing(self):
        """is_available() deve verificare la disponibilità restituendo True/False senza eccezioni."""
        avail = TesseractImageTextExtractor.is_available()
        assert isinstance(avail, bool)

    def test_is_available_propagates_unexpected_programming_errors(self, monkeypatch):
        """Requisito A4: Eccezioni inattese (es. TypeError) in is_available() non devono essere mascherate."""
        import pytesseract

        def buggy_version():
            raise TypeError("Bug programmatico inatteso nel wrapper pytesseract")

        monkeypatch.setattr("shutil.which", lambda cmd: None)
        monkeypatch.setattr(pytesseract, "get_tesseract_version", buggy_version)

        with pytest.raises(TypeError, match="Bug programmatico inatteso"):
            TesseractImageTextExtractor.is_available()

    def test_decompression_bomb_check_is_local_without_mutating_pillow_globals(self, tmp_path: Path, monkeypatch):
        """Requisito A6: Verifica che il superamento di MAX_PIXELS fallisca in modo controllato senza alterare Image.MAX_IMAGE_PIXELS."""
        initial_global_max = Image.MAX_IMAGE_PIXELS

        valid_img = tmp_path / "large.png"
        img = Image.new("RGB", (200, 200), color="white")
        img.save(valid_img)

        extractor = TesseractImageTextExtractor()
        extractor.MAX_PIXELS = 1000  # 200*200 = 40000 > 1000
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        asset = _make_image_asset(resolved_path=str(valid_img))
        res = extractor.extract(asset)

        assert res.status == OcrStatus.FAILED
        assert "maximum pixel budget" in (res.error_message or "")
        # Lo stato globale Pillow non deve essere stato modificato
        assert Image.MAX_IMAGE_PIXELS == initial_global_max

    def test_reject_unresolved_without_checking_engine(self):
        """Asset non risolto deve essere rifiutato prima di invocare il motore."""
        extractor = TesseractImageTextExtractor()
        asset = _make_image_asset(status=MediaResolutionStatus.MISSING, resolved_path=None)

        res = extractor.extract(asset)
        assert res.status == OcrStatus.NO_IMAGE
        assert res.provenance_asset is asset


    def test_reject_non_image_without_checking_engine(self):
        """Asset non immagine (es. AUDIO) deve essere rifiutato prima del motore."""
        extractor = TesseractImageTextExtractor()
        asset = _make_image_asset(media_kind=MediaKind.AUDIO)

        res = extractor.extract(asset)
        assert res.status == OcrStatus.UNSUPPORTED
        assert res.provenance_asset is asset

    def test_backend_unavailable_raises_ocr_backend_unavailable_error(self, monkeypatch):
        """Se tesseract non è installato o reperibile, solleva OcrBackendUnavailableError."""
        import unittest.mock as mock

        extractor = TesseractImageTextExtractor()
        # Simula assenza tesseract
        monkeypatch.setattr("pytesseract.get_tesseract_version", mock.MagicMock(side_effect=FileNotFoundError("tesseract not found")))
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        asset = _make_image_asset(resolved_path="/fake/path.jpg")
        with pytest.raises(OcrBackendUnavailableError) as excinfo:
            extractor.extract(asset)
        assert "Tesseract non è disponibile" in str(excinfo.value)

    def test_missing_pytesseract_module_raises_ocr_backend_unavailable_error(self, monkeypatch):
        """Se il package pytesseract non è importabile, solleva OcrBackendUnavailableError."""
        import sys
        extractor = TesseractImageTextExtractor()
        monkeypatch.setitem(sys.modules, "pytesseract", None)

        asset = _make_image_asset(resolved_path="/fake/path.jpg")
        with pytest.raises(OcrBackendUnavailableError):
            extractor.extract(asset)

    def test_corrupted_or_truncated_file_produces_failed_status_without_crash(self, tmp_path: Path, monkeypatch):
        """File immagine troncato (come gli stub JPEG) produce OcrStatus.FAILED controllato."""
        # Crea un finto file troncato (solo header JFIF incompleto)
        truncated_img = tmp_path / "truncated.jpg"
        truncated_img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00")

        extractor = TesseractImageTextExtractor()
        # Simula motore disponibile
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        asset = _make_image_asset(resolved_path=str(truncated_img))
        res = extractor.extract(asset)

        assert res.status == OcrStatus.FAILED
        assert res.full_text == ""
        assert res.provenance_asset is asset
        assert "image decoding/OCR error" in (res.error_message or "")

    def test_zero_byte_file_produces_failed_status_without_crash(self, tmp_path: Path, monkeypatch):
        """File a zero byte produce OcrStatus.FAILED controllato."""
        zero_img = tmp_path / "zero.jpg"
        zero_img.write_bytes(b"")

        extractor = TesseractImageTextExtractor()
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        asset = _make_image_asset(resolved_path=str(zero_img))
        res = extractor.extract(asset)

        assert res.status == OcrStatus.FAILED
        assert res.full_text == ""
        assert res.provenance_asset is asset
        assert "image decoding/OCR error" in (res.error_message or "")

    def test_mocked_successful_ocr_parses_regions_and_confidence(self, tmp_path: Path, monkeypatch):
        """Verifica parsing delle coordinate e normalizzazione confidenza da image_to_data."""
        import unittest.mock as mock

        # Crea un'immagine valida con Pillow
        valid_img = tmp_path / "valid.png"
        img = Image.new("RGB", (100, 50), color="white")
        img.save(valid_img)

        extractor = TesseractImageTextExtractor()
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        # Finto output di pytesseract.image_to_data
        fake_data = {
            "text": ["", "Codice", "", "Fiscale", ""],
            "conf": [-1, "95.5", -1, "88.0", -1],
            "left": [0, 10, 0, 60, 0],
            "top": [0, 5, 0, 5, 0],
            "width": [0, 45, 0, 40, 0],
            "height": [0, 20, 0, 20, 0],
        }
        monkeypatch.setattr("pytesseract.image_to_data", mock.MagicMock(return_value=fake_data))

        asset = _make_image_asset(resolved_path=str(valid_img))
        res = extractor.extract(asset)

        assert res.status == OcrStatus.SUCCESS
        assert res.full_text == "Codice Fiscale"
        assert len(res.regions) == 2

        reg1 = res.regions[0]
        assert reg1.text == "Codice"
        assert reg1.bounding_box == (10, 5, 45, 20)
        assert reg1.confidence == 0.955
        assert reg1.order_index == 0

        reg2 = res.regions[1]
        assert reg2.text == "Fiscale"
        assert reg2.bounding_box == (60, 5, 40, 20)
        assert reg2.confidence == 0.88
        assert reg2.order_index == 1

    def test_mocked_empty_ocr_produces_no_text_status(self, tmp_path: Path, monkeypatch):
        """Immagine valida ma priva di testo produce OcrStatus.NO_TEXT."""
        import unittest.mock as mock

        valid_img = tmp_path / "blank.png"
        img = Image.new("RGB", (50, 50), color="white")
        img.save(valid_img)

        extractor = TesseractImageTextExtractor()
        monkeypatch.setattr(extractor, "check_engine_available", lambda: "5.3.0")

        # Finto output senza parole (solo delimitatori vuoti)
        fake_data = {
            "text": ["", ""],
            "conf": [-1, -1],
            "left": [0, 0],
            "top": [0, 0],
            "width": [0, 0],
            "height": [0, 0],
        }
        monkeypatch.setattr("pytesseract.image_to_data", mock.MagicMock(return_value=fake_data))

        asset = _make_image_asset(resolved_path=str(valid_img))
        res = extractor.extract(asset)

        assert res.status == OcrStatus.NO_TEXT
        assert res.full_text == ""
        assert len(res.regions) == 0

    def test_tesseract_cmd_global_state_not_leaked(self, monkeypatch):
        """Verifica che un custom tesseract_cmd non modifichi lo stato globale di pytesseract."""
        import pytesseract

        original_cmd = pytesseract.pytesseract.tesseract_cmd
        custom_cmd = "/custom/path/tesseract_executable"

        extractor = TesseractImageTextExtractor(tesseract_cmd=custom_cmd)

        # Mock per verificare che durante l'esecuzione il comando sia temporaneamente impostato
        seen_cmd = []

        def mock_version():
            seen_cmd.append(pytesseract.pytesseract.tesseract_cmd)
            return "5.3.0"

        monkeypatch.setattr(pytesseract, "get_tesseract_version", mock_version)

        ver = extractor.check_engine_available()
        assert ver == "5.3.0"
        assert seen_cmd == [custom_cmd]
        # Dopo l'esecuzione, il valore globale deve essere identico a quello iniziale
        assert pytesseract.pytesseract.tesseract_cmd == original_cmd

    def test_unexpected_error_propagates_in_check_engine_available(self, monkeypatch):
        """Errori programmatici o inattesi non devono essere mascherati come OcrBackendUnavailableError."""
        import pytesseract

        def buggy_version():
            raise TypeError("Errore di programmazione inatteso")

        monkeypatch.setattr(pytesseract, "get_tesseract_version", buggy_version)

        extractor = TesseractImageTextExtractor()
        with pytest.raises(TypeError, match="Errore di programmazione inatteso"):
            extractor.check_engine_available()


@pytest.mark.smoke
def test_real_tesseract_smoke_if_installed(tmp_path: Path):
    """Smoke test opzionale: se Tesseract è installato nel sistema, esegue un OCR reale su immagine sintetica."""
    if not TesseractImageTextExtractor.is_available():
        pytest.skip("Tesseract OCR binary non disponibile su questa macchina ospite")

    from PIL import ImageDraw
    img_path = tmp_path / "smoke_ocr.png"
    img = Image.new("RGB", (200, 60), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 20), "FORENSIC", fill="black")
    img.save(img_path)

    extractor = TesseractImageTextExtractor()
    asset = _make_image_asset(resolved_path=str(img_path))
    res = extractor.extract(asset)

    assert res.status in (OcrStatus.SUCCESS, OcrStatus.NO_TEXT)
    if res.status == OcrStatus.SUCCESS:
        assert "FORENSIC" in res.full_text.upper()
