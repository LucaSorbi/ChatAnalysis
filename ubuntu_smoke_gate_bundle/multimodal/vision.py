"""
multimodal/vision.py
--------------------
Interfaccia e motori per l'analisi del contenuto visivo delle immagini (Image Vision).

Principi architetturali e distinzione concettuale:
1. OCR risponde alla domanda: "Quale testo è scritto nell'immagine?" (Optical Character Recognition).
2. Vision risponde alla domanda: "Che cosa è rappresentato visivamente nell'immagine?" (Scene/Object Description).
3. I due risultati sono tenuti rigorosamente separati nei rispettivi modelli (ImageOcrResult vs ImageVisionResult).
4. VINCOLO DEONTOLOGICO E FORENSE ASSOLUTO:
   La foundation Vision NON include né consente alcuna funzionalità di:
   - Riconoscimento facciale (face recognition / face matching);
   - Identificazione di persone fisiche o inferenza di identità reale;
   - Inferenza di attributi biometrici o categorie sensibili.
   L'obiettivo è esclusivamente descrivere il contesto della scena e gli oggetti inanimati/documentali.
5. BaseImageVisionAnalyzer definisce il contratto astratto per consentire l'integrazione di futuri modelli locali.
6. FakeImageVisionAnalyzer fornisce un backend sintetico e deterministico per test offline senza modelli pesanti.
7. Sicurezza degli input: il Vision Analyzer riceve esclusivamente ResolvedMediaAsset già validati dal MediaResolver
   con status RESOLVED e media_kind IMAGE, senza mai accedere a percorsi arbitrari non autorizzati.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from multimodal.models import (
    ImageVisionResult,
    MediaKind,
    ResolvedMediaAsset,
    VisionStatus,
)


class VisionBackendError(Exception):
    """Eccezione base per errori del backend di analisi visiva."""
    pass


class VisionBackendUnavailableError(VisionBackendError):
    """Sollevata quando il motore Vision o il runtime neurale non sono disponibili."""
    pass


class VisionModelLoadError(VisionBackendError):
    """Sollevata quando l'inizializzazione o il caricamento del modello Vision fallisce."""
    pass


class BaseImageVisionAnalyzer(ABC):
    """
    Contratto astratto per analizzatori di contenuto visivo delle immagini.
    """

    @abstractmethod
    def analyze(self, asset: ResolvedMediaAsset) -> ImageVisionResult:
        """
        Analizza visivamente un asset immagine risolto producendo un ImageVisionResult immutabile.
        """
        raise NotImplementedError

    def analyze_image(self, asset: ResolvedMediaAsset) -> ImageVisionResult:
        """Alias amichevole di analyze()."""
        return self.analyze(asset)

    @classmethod
    def is_available(cls) -> bool:
        """Indica se il runtime e le dipendenze del modello Vision sono disponibili."""
        return True


class FakeImageVisionAnalyzer(BaseImageVisionAnalyzer):
    """
    Analizzatore Vision sintetico e deterministico per test di unità e integrazione.
    Non richiede modelli neurali, GPU o connessioni esterne.
    """

    def __init__(
        self,
        default_description: str = "Scena interna con documenti e oggetti su una scrivania.",
        canned_observations: tuple[str, ...] | None = None,
        simulate_failure: bool = False,
        failure_error: str = "Simulated Vision model failure",
        simulate_no_content: bool = False,
    ) -> None:
        self.default_description = default_description
        self.canned_observations = canned_observations
        self.simulate_failure = simulate_failure
        self.failure_error = failure_error
        self.simulate_no_content = simulate_no_content

    def analyze(self, asset: ResolvedMediaAsset) -> ImageVisionResult:
        # 1. Verifica che l'asset sia risolto
        if not asset.is_resolved:
            return ImageVisionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=VisionStatus.NO_IMAGE,
                description="",
                observations=(),
                engine="fake-vision",
                model_name="mock-vision",
                provenance_asset=asset,
                error_message=f"Asset non risolto (status={asset.status.value})",
            )

        # 2. Verifica che il media sia un'immagine
        if asset.media_kind != MediaKind.IMAGE:
            return ImageVisionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=VisionStatus.UNSUPPORTED,
                description="",
                observations=(),
                engine="fake-vision",
                model_name="mock-vision",
                provenance_asset=asset,
                error_message=f"Tipo media non supportato per analisi visiva ({asset.media_kind.value})",
            )

        # 3. Simulazione errore controllato
        if self.simulate_failure:
            return ImageVisionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=VisionStatus.FAILED,
                description="",
                observations=(),
                engine="fake-vision",
                model_name="mock-vision",
                provenance_asset=asset,
                error_message=self.failure_error,
            )

        # 4. Simulazione nessun contenuto saliente identificato
        if self.simulate_no_content:
            return ImageVisionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=VisionStatus.NO_CONTENT,
                description="",
                observations=(),
                engine="fake-vision",
                model_name="mock-vision",
                provenance_asset=asset,
            )

        # 5. Analisi simulata con successo
        if self.canned_observations is not None:
            observations = self.canned_observations
        else:
            observations = (
                "Documento cartaceo visibile in primo piano",
                "Superficie di lavoro in legno",
                "Illuminazione artificiale diffusa",
            )

        return ImageVisionResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=VisionStatus.SUCCESS,
            description=self.default_description,
            observations=observations,
            engine="fake-vision",
            model_name="mock-vision",
            provenance_asset=asset,
        )
