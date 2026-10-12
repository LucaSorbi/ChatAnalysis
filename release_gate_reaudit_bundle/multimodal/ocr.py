"""
multimodal/ocr.py
-----------------
Interfaccia e motori per l'estrazione ottica del testo da immagini (OCR).

Principi architetturali:
1. BaseImageTextExtractor definisce il contratto astratto per consentire l'intercambiabilità dei motori OCR.
2. FakeImageTextExtractor consente l'esecuzione di test unitari veloci, deterministici e offline senza dipendenze binarie.
3. TesseractImageTextExtractor implementa l'integrazione con Tesseract OCR e Pillow in modalità sicura (decompression bomb protection, gestione file corrotti/troncati).
4. TASK DI SOLA ESTRAZIONE: nessuna traduzione o generazione di testo (OCR != Generative AI); il testo originale viene preservato.
5. Regioni di testo con bounding box, confidenza normalizzata [0.0, 1.0] e indice d'ordine rigorosamente preservati.
6. Error handling controllato: errore sul singolo file restituisce OcrStatus.FAILED senza corrompere la pipeline o provocare crash.
7. Fallimento globale del motore solleva eccezioni dedicate (OcrBackendUnavailableError, OcrEngineError).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
import shutil
import subprocess
from typing import Any

from multimodal.models import (
    ImageOcrResult,
    MediaKind,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
)


class OcrBackendError(Exception):
    """Eccezione base per errori del backend di estrazione OCR."""
    pass


class OcrBackendUnavailableError(OcrBackendError):
    """Sollevata quando il motore OCR o il binario nativo (es. tesseract) non sono disponibili."""
    pass


class OcrEngineError(OcrBackendError):
    """Sollevata quando l'inizializzazione globale o la configurazione del motore OCR fallisce."""
    pass


class BaseImageTextExtractor(ABC):
    """
    Contratto astratto per motori di Optical Character Recognition (OCR).
    """

    @abstractmethod
    def extract(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        """
        Estrae testo da un asset immagine risolto producendo un ImageOcrResult immutabile.
        """
        raise NotImplementedError

    def extract_text(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        """Alias amichevole di extract()."""
        return self.extract(asset)

    @classmethod
    def is_available(cls) -> bool:
        """Indica se le dipendenze runtime del motore sono installate e funzionanti."""
        return True


class FakeImageTextExtractor(BaseImageTextExtractor):
    """
    Motore OCR sintetico e deterministico per test di unità e integrazione.
    Non richiede binari esterni o modelli e funziona completamente offline.
    """

    def __init__(
        self,
        default_text: str = "Testo estratto da immagine di test",
        canned_regions: tuple[OcrTextRegion, ...] | None = None,
        language_config: str = "ita+eng",
        default_language: str | None = None,
        simulate_failure: bool = False,
        failure_error: str = "Simulated OCR decoding error",
        simulate_no_text: bool = False,
    ) -> None:
        self.default_text = default_text
        self.canned_regions = canned_regions
        self.language_config = default_language if default_language is not None else language_config
        self.default_language = self.language_config
        self.simulate_failure = simulate_failure
        self.failure_error = failure_error
        self.simulate_no_text = simulate_no_text

    def extract(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        # 1. Verifica che l'asset sia risolto
        if not asset.is_resolved:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.NO_IMAGE,
                full_text="",
                regions=(),
                language_config=self.language_config,
                engine="fake-ocr",
                provenance_asset=asset,
                error_message=f"Asset non risolto (status={asset.status.value})",
            )

        # 2. Verifica che il media sia un'immagine
        if asset.media_kind != MediaKind.IMAGE:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.UNSUPPORTED,
                full_text="",
                regions=(),
                language_config=self.language_config,
                engine="fake-ocr",
                provenance_asset=asset,
                error_message=f"Tipo media non supportato per OCR ({asset.media_kind.value})",
            )

        # 3. Simulazione errore controllato
        if self.simulate_failure:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.FAILED,
                full_text="",
                regions=(),
                language_config=self.language_config,
                engine="fake-ocr",
                provenance_asset=asset,
                error_message=self.failure_error,
            )

        # 4. Simulazione nessun testo presente nell'immagine
        if self.simulate_no_text:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.NO_TEXT,
                full_text="",
                regions=(),
                language_config=self.language_config,
                engine="fake-ocr",
                provenance_asset=asset,
            )

        # 5. Estrazione simulata di successo
        if self.canned_regions is not None:
            regions = self.canned_regions
            full_text = " ".join(r.text for r in regions if r.text).strip()
        else:
            regions = (
                OcrTextRegion(text="Testo", bounding_box=(10, 10, 50, 20), confidence=0.95, order_index=0),
                OcrTextRegion(text="estratto", bounding_box=(65, 10, 60, 20), confidence=0.92, order_index=1),
            )
            full_text = self.default_text

        return ImageOcrResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text=full_text,
            regions=regions,
            language_config=self.language_config,
            engine="fake-ocr",
            provenance_asset=asset,
        )


class TesseractImageTextExtractor(BaseImageTextExtractor):
    """
    Motore OCR reale basato su Tesseract OCR (invocato tramite pytesseract) e Pillow.

    Caratteristiche:
    - Controllo rigoroso delle dipendenze binarie senza percorsi hardcoded.
    - Apertura protetta delle immagini tramite Pillow con de-allocation sicura e controllo decompression bomb.
    - Gestione trasparente di file immagine corrotti, troncati o a dimensione zero senza crash.
    - Parsing strutturato dell'output tabellare (image_to_data) per preservare coordinate e confidenza.
    - Se nessun testo viene rilevato, restituisce OcrStatus.NO_TEXT.
    - Errori di decodifica o esecuzione per-file restituiscono OcrStatus.FAILED controllato.
    """

    MAX_PIXELS: int = 50_000_000  # 50 Megapixel guardrail contro attacchi decompression bomb

    def __init__(
        self,
        tesseract_cmd: str | None = None,
        lang: str = "ita+eng",
        psm: int = 3,
        timeout_seconds: int = 30,
    ) -> None:
        self.tesseract_cmd = tesseract_cmd
        self.lang = lang
        self.psm = psm
        self.timeout_seconds = timeout_seconds
        self._version: str | None = None

    @classmethod
    def is_available(cls, tesseract_cmd: str | None = None) -> bool:
        """
        Verifica se pytesseract e il binario nativo tesseract sono disponibili ed eseguibili.
        Non presume percorsi prefissati di sistema.
        Cattura solo le eccezioni attese prodotte da import o invocazione del binario.
        Eccezioni inattese o bug programmatici propagano liberamente.
        """
        try:
            import pytesseract
        except ImportError:
            return False

        cmd = tesseract_cmd or getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        # 1. Verifica presenza nel PATH
        if shutil.which(cmd):
            return True

        # 2. Tentativo diretto di esecuzione versione
        old_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        try:
            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            pytesseract.get_tesseract_version()
            return True
        except (OSError, subprocess.SubprocessError, pytesseract.TesseractNotFoundError, pytesseract.TesseractError):
            return False
        finally:
            pytesseract.pytesseract.tesseract_cmd = old_cmd

    @contextmanager
    def _temporary_tesseract_cmd(self):
        """
        Context manager sicuro per impostare temporaneamente tesseract_cmd
        senza lasciare modifiche globali persistenti in pytesseract.
        """
        try:
            import pytesseract
        except ImportError as err:
            raise OcrBackendUnavailableError(
                "Il package 'pytesseract' non è installato nell'ambiente Python."
            ) from err

        old_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        try:
            if self.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
            yield pytesseract
        finally:
            pytesseract.pytesseract.tesseract_cmd = old_cmd

    def check_engine_available(self) -> str:
        """
        Verifica la disponibilità del motore OCR e ne restituisce la versione.
        Solleva OcrBackendUnavailableError se pytesseract o il binario non sono reperibili.
        Errori programmatici o eccezioni inattese propagano liberamente.
        """
        if self._version is not None:
            return self._version

        with self._temporary_tesseract_cmd() as pytesseract:
            try:
                version = str(pytesseract.get_tesseract_version())
                self._version = version
                return version
            except (OSError, subprocess.SubprocessError, pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as err:
                raise OcrBackendUnavailableError(
                    f"Il binario nativo Tesseract non è disponibile o non è eseguibile ({err}). "
                    "Specificare tesseract_cmd oppure installare Tesseract nel PATH di sistema."
                ) from err

    def extract(self, asset: ResolvedMediaAsset) -> ImageOcrResult:
        # 1. Filtro su asset risolto
        if not asset.is_resolved or not asset.resolved_path:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.NO_IMAGE,
                full_text="",
                regions=(),
                language_config=self.lang,
                engine="tesseract",
                provenance_asset=asset,
                error_message=f"Asset non risolto sul filesystem (status={asset.status.value})",
            )

        # 2. Filtro su media_kind IMAGE
        if asset.media_kind != MediaKind.IMAGE:
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.UNSUPPORTED,
                full_text="",
                regions=(),
                language_config=self.lang,
                engine="tesseract",
                provenance_asset=asset,
                error_message=f"Media kind {asset.media_kind.value} non supportato per OCR (richiesto IMAGE)",
            )

        # 3. Controllo globale motore (se non disponibile, solleva OcrBackendUnavailableError)
        version = self.check_engine_available()

        # 4. Estrazione OCR protetta per-file
        try:
            from pytesseract import Output
            from PIL import Image

            with Image.open(asset.resolved_path) as img:
                # Controllo locale del budget pixel per prevenire decompression bomb senza modificare lo stato globale
                if (img.width * img.height) > self.MAX_PIXELS:
                    return ImageOcrResult(
                        message_id=asset.message_id,
                        source_name=asset.source_name,
                        source_record_id=asset.source_record_id,
                        status=OcrStatus.FAILED,
                        full_text="",
                        regions=(),
                        language_config=self.lang,
                        engine="tesseract",
                        engine_version=version,
                        provenance_asset=asset,
                        error_message=f"Image exceeds maximum pixel budget ({img.width * img.height} > {self.MAX_PIXELS})",
                    )

                # Forza il caricamento dei pixel per individuare troncamenti o corruzioni
                img.load()

                config = f"--psm {self.psm}"
                with self._temporary_tesseract_cmd() as pytesseract:
                    data: dict[str, list[Any]] = pytesseract.image_to_data(
                        img,
                        lang=self.lang,
                        config=config,
                        output_type=Output.DICT,
                        timeout=self.timeout_seconds,
                    )

            # Parsing delle regioni di testo
            text_items = data.get("text", [])
            conf_items = data.get("conf", [])
            left_items = data.get("left", [])
            top_items = data.get("top", [])
            width_items = data.get("width", [])
            height_items = data.get("height", [])

            regions: list[OcrTextRegion] = []
            order_idx = 0
            for i in range(len(text_items)):
                word = str(text_items[i]).strip()
                if not word:
                    continue

                try:
                    conf_raw = float(conf_items[i])
                except (ValueError, TypeError, IndexError):
                    conf_raw = -1.0

                # Tesseract usa conf -1 per delimitatori/header strutturali
                if conf_raw < 0:
                    continue

                # Normalizzazione confidenza nell'intervallo [0.0, 1.0]
                conf_norm = round(min(max(conf_raw / 100.0, 0.0), 1.0), 4)

                try:
                    bbox = (
                        int(left_items[i]),
                        int(top_items[i]),
                        int(width_items[i]),
                        int(height_items[i]),
                    )
                except (ValueError, TypeError, IndexError):
                    bbox = None

                regions.append(
                    OcrTextRegion(
                        text=word,
                        bounding_box=bbox,
                        confidence=conf_norm,
                        order_index=order_idx,
                    )
                )
                order_idx += 1

            full_text = " ".join(r.text for r in regions if r.text).strip()

            if not full_text:
                return ImageOcrResult(
                    message_id=asset.message_id,
                    source_name=asset.source_name,
                    source_record_id=asset.source_record_id,
                    status=OcrStatus.NO_TEXT,
                    full_text="",
                    regions=(),
                    language_config=self.lang,
                    engine="tesseract",
                    engine_version=version,
                    provenance_asset=asset,
                )

            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.SUCCESS,
                full_text=full_text,
                regions=tuple(regions),
                language_config=self.lang,
                engine="tesseract",
                engine_version=version,
                provenance_asset=asset,
            )

        except Exception as e:
            # Per-file failure: si applica ESCLUSIVAMENTE ad asset già validati sul filesystem.
            # Intercetta errori locali al file (Pillow decode error, file troncato,
            # DecompressionBombError, timeout Tesseract su singola immagine).
            # I guasti globali del motore hanno già sollevato OcrBackendUnavailableError al punto 3.
            return ImageOcrResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=OcrStatus.FAILED,
                full_text="",
                regions=(),
                language_config=self.lang,
                engine="tesseract",
                engine_version=version,
                provenance_asset=asset,
                error_message=f"Per-file image decoding/OCR error: {type(e).__name__}: {e}",
            )

