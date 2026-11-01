"""
multimodal/image_status.py
--------------------------
Stato VERITIERO dei messaggi con foto nel documento (solo derivazione, nessuna esecuzione OCR).

IMPORTANTE — cosa fa e cosa NON fa il progetto sulle immagini:
- OCR (`multimodal/ocr.py`): legge il TESTO SCRITTO nell'immagine con Tesseract (pytesseract + Pillow + binario
  esterno). Non descrive la scena.
- Descrizione/interpretazione della scena (vision/captioning): NON implementata. Esiste solo il contratto
  `BaseImageVisionAnalyzer` e un analizzatore finto per i test; l'app non configura nessun motore reale.
  Una foto senza testo scritto non produce quindi alcun contenuto per l'analisi.

Stati per messaggio:
- IMAGE_NOT_FOUND     il messaggio è una foto ma l'export non contiene il file (nessun `media_reference`).
- OCR_NOT_RUN         file presente, nessun risultato OCR associato (elaborazione on-demand non ancora eseguita).
- OCR_UNAVAILABLE     elaborazione tentata ma il motore OCR non è disponibile (pytesseract o Tesseract assenti).
- OCR_FAILED          motore disponibile ma l'estrazione su quel file è fallita.
- OCR_NO_TEXT         elaborazione riuscita, nessun testo scritto nell'immagine.
- OCR_TEXT_EXTRACTED  elaborazione riuscita con testo: esiste una sezione OCR_TEXT (testo DERIVATO).

Il modulo non legge né restituisce testo, nomi di file o contenuti: solo etichette e conteggi interi.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from multimodal.models import OcrStatus
from multimodal.resolver import _IMAGE_EXTENSIONS

# Chiave dei metadati di ImageOcrResult che registra il MOTIVO strutturato di un fallimento.
OCR_REASON_METADATA_KEY = "ocr_reason"


class ImageEvidenceState(str, Enum):
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
    OCR_NOT_RUN = "OCR_NOT_RUN"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    OCR_FAILED = "OCR_FAILED"
    OCR_NO_TEXT = "OCR_NO_TEXT"
    OCR_TEXT_EXTRACTED = "OCR_TEXT_EXTRACTED"


def _is_image_message(bundle: Any) -> bool:
    msg = bundle.message
    mtype = getattr(getattr(msg, "message_type", None), "value", getattr(msg, "message_type", None))
    if str(mtype).upper() == "IMAGE":
        return True
    if getattr(bundle, "image_ocr", None) is not None:
        return True
    ref = getattr(msg, "media_reference", None)
    if ref and isinstance(ref, str):
        dot = ref.rfind(".")
        return dot >= 0 and ref[dot:].lower() in _IMAGE_EXTENSIONS
    return False


def classify_image_bundle(bundle: Any) -> ImageEvidenceState | None:
    """Stato OCR di un bundle, oppure None se il messaggio non è una foto."""
    if not _is_image_message(bundle):
        return None
    ocr = getattr(bundle, "image_ocr", None)
    if ocr is None:
        ref = getattr(bundle.message, "media_reference", None)
        if not ref or not str(ref).strip():
            return ImageEvidenceState.IMAGE_NOT_FOUND
        return ImageEvidenceState.OCR_NOT_RUN
    if ocr.status == OcrStatus.SUCCESS:
        if ocr.full_text and ocr.full_text.strip():
            return ImageEvidenceState.OCR_TEXT_EXTRACTED
        return ImageEvidenceState.OCR_NO_TEXT
    if ocr.status == OcrStatus.NO_TEXT:
        return ImageEvidenceState.OCR_NO_TEXT
    reason = dict(ocr.metadata or {}).get(OCR_REASON_METADATA_KEY)
    if reason == ImageEvidenceState.OCR_UNAVAILABLE.value:
        return ImageEvidenceState.OCR_UNAVAILABLE
    return ImageEvidenceState.OCR_FAILED


def summarize_image_evidence(document: Any) -> dict[str, int]:
    """Conteggi (solo interi) delle foto per stato; ogni messaggio ha esattamente uno stato (nessun doppio conteggio)."""
    counts = {s.value: 0 for s in ImageEvidenceState}
    for bundle in document.bundles:
        state = classify_image_bundle(bundle)
        if state is not None:
            counts[state.value] += 1
    return {"image_messages_total": sum(counts.values()), **counts}


def format_image_status_message(summary: Mapping[str, int]) -> str | None:
    """Messaggio statico per la UI (italiano): solo conteggi ed etichette, mai nomi di file né testo. None se non ci sono foto."""
    total = int(summary.get("image_messages_total", 0) or 0)
    if total <= 0:
        return None
    labels = (
        (ImageEvidenceState.OCR_TEXT_EXTRACTED, "con testo estratto (OCR, dato derivato)"),
        (ImageEvidenceState.OCR_NO_TEXT, "senza testo scritto"),
        (ImageEvidenceState.IMAGE_NOT_FOUND, "file NON presente nell'export"),
        (ImageEvidenceState.OCR_NOT_RUN, "file presente, non ancora elaborate"),
        (ImageEvidenceState.OCR_UNAVAILABLE, "motore OCR non disponibile (pytesseract o Tesseract assenti)"),
        (ImageEvidenceState.OCR_FAILED, "estrazione OCR fallita"),
    )
    parts = [f"{int(summary.get(s.value, 0))} {label}" for s, label in labels if int(summary.get(s.value, 0))]
    msg = f"Foto: {total} — " + "; ".join(parts) + "."
    msg += (
        " L'OCR legge solo il testo scritto nell'immagine: la descrizione della scena non è implementata "
        "(nessun motore Vision configurato), quindi una foto senza testo non produce contenuto analizzabile."
    )
    return msg
