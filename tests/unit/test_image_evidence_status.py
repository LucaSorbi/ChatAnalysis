"""
tests/unit/test_image_evidence_status.py
----------------------------------------
Foto WhatsApp: stato veritiero OCR e regressione sul difetto `VisionStatus.COMPLETED`.

SOLO fixture sintetiche (JPEG con testo inventato / senza testo, ZIP WhatsApp temporanei) e motori finti:
esito massimo MOCK. Questi test NON provano che Tesseract giri su Windows.
Distinzione esplicita: l'OCR legge testo scritto nell'immagine; la descrizione di scena (vision) NON è implementata.
"""
from __future__ import annotations

import io
import sys
import zipfile
from typing import Any

import pytest

pytest.importorskip("PIL", reason="Pillow necessario per generare JPEG sintetici")
from PIL import Image, ImageDraw  # noqa: E402

from analysis.checkpoint import compute_document_fingerprint  # noqa: E402
from multimodal.asset_access import MediaAssetRegistry  # noqa: E402
from multimodal.image_status import (  # noqa: E402
    ImageEvidenceState,
    classify_image_bundle,
    format_image_status_message,
    summarize_image_evidence,
)
from multimodal.models import ImageOcrResult, OcrStatus, ResolvedMediaAsset  # noqa: E402
from multimodal.ocr import BaseImageTextExtractor, FakeImageTextExtractor, OcrBackendUnavailableError  # noqa: E402
from multimodal.vision import FakeImageVisionAnalyzer  # noqa: E402
from ui.application import execute_local_media_processing  # noqa: E402
from ui.ingestion import ingest_file_payload  # noqa: E402
from ui.models import SourceFormat  # noqa: E402

SECRET = "ZXQINVENTATO"
PHOTO = "IMG-20261101-WA0001.jpg"
CHAT = (
    "[01/11/2026, 10:00:00] Mario: ciao\n"
    f"[01/11/2026, 10:01:00] Anna: {PHOTO} (file allegato)\n"
)


def _jpeg(with_text: bool) -> bytes:
    im = Image.new("RGB", (900, 1600), "white")  # stesse dimensioni del caso reale descritto (900x1600)
    if with_text:
        ImageDraw.Draw(im).text((30, 700), f"{SECRET} testo inventato", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def _zip(with_photo: bool = True, with_text: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", CHAT)
        if with_photo:
            zf.writestr(PHOTO, _jpeg(with_text))
    return buf.getvalue()


def _ingest(payload: bytes, name: str):
    reg = MediaAssetRegistry()
    res = ingest_file_payload(payload, original_filename=name, source_format=SourceFormat.WHATSAPP_EXPORT, media_registry=reg)
    return res.document_list[0], reg


class RaisingOcr(BaseImageTextExtractor):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def extract(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        raise self.exc


def _photo_state(doc) -> ImageEvidenceState:
    return next(s for s in (classify_image_bundle(b) for b in doc.bundles) if s is not None)


def _derived(doc, kind: str) -> list[Any]:
    return [s for s in doc.all_evidence_sections if s.source_type.value == kind]


# ----------------------------------------------------------------------------
# Presenza della foto e stati
# ----------------------------------------------------------------------------

def test_zip_with_photo_exposes_media_reference_and_not_run_state():
    doc, _ = _ingest(_zip(), "chat.zip")
    photo = next(b for b in doc.bundles if b.message.media_reference)
    assert photo.message.message_type.value == "IMAGE" and photo.message.media_reference == PHOTO
    assert _photo_state(doc) is ImageEvidenceState.OCR_NOT_RUN  # nessuna elaborazione automatica
    assert all(b.image_ocr is None for b in doc.bundles)


@pytest.mark.parametrize("payload, name", [(None, "chat.zip"), (CHAT.encode("utf-8"), "_chat.txt")])
def test_photo_without_file_is_image_not_found(payload, name):
    doc, reg = _ingest(payload if payload is not None else _zip(with_photo=False), name)
    assert not any(b.message.media_reference for b in doc.bundles)  # nessun pulsante in UI
    assert _photo_state(doc) is ImageEvidenceState.IMAGE_NOT_FOUND
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor())
    assert _photo_state(enriched) is ImageEvidenceState.IMAGE_NOT_FOUND
    assert not _derived(enriched, "OCR_TEXT")


def test_user_environment_without_pytesseract_is_ocr_unavailable_not_silent(monkeypatch):
    """Riproduce il caso segnalato: pytesseract non importabile → FAILED, causa esposta come enum."""
    doc, reg = _ingest(_zip(), "chat.zip")
    monkeypatch.setitem(sys.modules, "pytesseract", None)  # import → ImportError, come in un venv senza il pacchetto
    enriched, report = execute_local_media_processing(doc, registry=reg, ocr_backend=None)
    assert report["ocr_available"] is False
    assert enriched.metadata["multimodal_status"] == "FAILED"
    assert (enriched.metadata["multimodal_total_media"], enriched.metadata["multimodal_successful_media"]) == (1, 0)
    assert _photo_state(enriched) is ImageEvidenceState.OCR_UNAVAILABLE
    assert not _derived(enriched, "OCR_TEXT")


@pytest.mark.parametrize("exc, expected", [
    (OcrBackendUnavailableError("tesseract non trovato"), ImageEvidenceState.OCR_UNAVAILABLE),
    (RuntimeError("errore di decodifica sul singolo file"), ImageEvidenceState.OCR_FAILED),
])
def test_backend_exceptions_are_classified_global_vs_per_file(exc, expected):
    doc, reg = _ingest(_zip(), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=RaisingOcr(exc))
    assert _photo_state(enriched) is expected


def test_engine_reported_failure_is_ocr_failed():
    doc, reg = _ingest(_zip(), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(simulate_failure=True))
    assert _photo_state(enriched) is ImageEvidenceState.OCR_FAILED
    assert enriched.metadata["multimodal_status"] == "FAILED"


def test_photo_without_text_is_complete_with_no_derived_evidence():
    doc, reg = _ingest(_zip(with_text=False), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(simulate_no_text=True))
    assert _photo_state(enriched) is ImageEvidenceState.OCR_NO_TEXT
    assert enriched.metadata["multimodal_status"] == "COMPLETE"
    assert not _derived(enriched, "OCR_TEXT") and not _derived(enriched, "VISION_DESCRIPTION")


# ----------------------------------------------------------------------------
# Evidenza derivata: una sola, con provenienza; nessuna duplicazione
# ----------------------------------------------------------------------------

def test_success_adds_exactly_one_derived_ocr_section_with_provenance():
    doc, reg = _ingest(_zip(), "chat.zip")
    n = len(doc.bundles)
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(default_text=f"{SECRET} letto"))
    assert len(enriched.bundles) == n, "nessun messaggio duplicato"
    assert _photo_state(enriched) is ImageEvidenceState.OCR_TEXT_EXTRACTED
    photo = next(b for b in enriched.bundles if b.message.media_reference)
    assert [s.source_type.value for s in photo.text_evidence_sections] == ["ORIGINAL_TEXT", "OCR_TEXT"]
    assert photo.message.text_content == f"{PHOTO} (file allegato)"  # il testo originale non è alterato
    ocr = photo.image_ocr
    assert ocr.message_id == photo.message.message_id and ocr.status == OcrStatus.SUCCESS
    assert ocr.engine and ocr.provenance_asset is not None and ocr.provenance_asset.sha256
    assert not _derived(enriched, "VISION_DESCRIPTION"), "nessuna descrizione di scena: non implementata"


def test_retry_after_failure_reprocesses_and_never_duplicates():
    doc, reg = _ingest(_zip(), "chat.zip")
    failed, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(simulate_failure=True))
    assert failed.metadata["multimodal_status"] == "FAILED"
    ok, _ = execute_local_media_processing(failed, registry=reg, ocr_backend=FakeImageTextExtractor())  # il pulsante rielabora
    again, _ = execute_local_media_processing(ok, registry=reg, ocr_backend=FakeImageTextExtractor())
    assert ok.metadata["multimodal_status"] == again.metadata["multimodal_status"] == "COMPLETE"
    assert len(_derived(again, "OCR_TEXT")) == 1
    assert summarize_image_evidence(again)["OCR_TEXT_EXTRACTED"] == 1


def test_summary_states_are_disjoint_and_sum_to_total(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)  # ocr_backend=None non deve dipendere dall'ambiente di test
    doc, reg = _ingest(_zip(), "chat.zip")
    for backend in (None, FakeImageTextExtractor(), FakeImageTextExtractor(simulate_failure=True)):
        enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=backend)
        s = summarize_image_evidence(enriched)
        assert s["image_messages_total"] == sum(v for k, v in s.items() if k != "image_messages_total") == 1


def test_failed_ocr_does_not_change_document_fingerprint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)  # ocr_backend=None non deve dipendere dall'ambiente di test
    doc, reg = _ingest(_zip(), "chat.zip")
    base = compute_document_fingerprint(doc)
    for backend in (None, FakeImageTextExtractor(simulate_failure=True), RaisingOcr(OcrBackendUnavailableError("x"))):
        enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=backend)
        assert compute_document_fingerprint(enriched) == base
    ok, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(default_text=f"{SECRET} letto"))
    assert compute_document_fingerprint(ok) != base, "un OCR riuscito è un nuovo dato: nuovo documento, vecchi checkpoint intatti"


# ----------------------------------------------------------------------------
# Difetto verificato: VisionStatus.COMPLETED non esiste
# ----------------------------------------------------------------------------

def test_second_pass_with_vision_backend_does_not_raise_and_keeps_single_description():
    doc, reg = _ingest(_zip(), "chat.zip")
    first, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(), vision_backend=FakeImageVisionAnalyzer())
    second, _ = execute_local_media_processing(first, registry=reg, ocr_backend=FakeImageTextExtractor(), vision_backend=FakeImageVisionAnalyzer())
    assert len(_derived(second, "VISION_DESCRIPTION")) == 1
    assert second.metadata["multimodal_status"] == "COMPLETE"


# ----------------------------------------------------------------------------
# Messaggio UI privacy-safe e onesto su OCR vs descrizione di scena
# ----------------------------------------------------------------------------

def test_status_message_is_private_and_states_scene_description_is_not_implemented():
    doc, reg = _ingest(_zip(), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=FakeImageTextExtractor(default_text=f"{SECRET} letto"))
    for d in (doc, enriched):
        msg = format_image_status_message(summarize_image_evidence(d))
        assert "Foto: 1" in msg and "non è implementata" in msg and "OCR" in msg
        assert PHOTO not in msg and SECRET not in msg
    assert format_image_status_message(summarize_image_evidence(_ingest(b"[01/11/2026, 10:00:00] M: solo testo\n", "_chat.txt")[0])) is None


class _RecordingSt:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        def _rec(text: str = "", *a: Any, **k: Any) -> None:
            self.calls.append((name, str(text)))
        return _rec


@pytest.mark.parametrize("variant, level", [("unavailable", "warning"), ("ok", "info")])
def test_ui_render_levels_and_privacy(monkeypatch, variant, level):
    import ui.presentation as pres

    doc, reg = _ingest(_zip(), "chat.zip")
    backend = None if variant == "unavailable" else FakeImageTextExtractor(default_text=f"{SECRET} letto")
    if variant == "unavailable":
        monkeypatch.setitem(sys.modules, "pytesseract", None)
    enriched, _ = execute_local_media_processing(doc, registry=reg, ocr_backend=backend)
    rec = _RecordingSt()
    monkeypatch.setattr(pres, "st", rec)
    pres._render_image_evidence_status(enriched)
    assert len(rec.calls) == 1 and rec.calls[0][0] == level
    assert PHOTO not in rec.calls[0][1] and SECRET not in rec.calls[0][1]
