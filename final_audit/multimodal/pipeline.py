"""
multimodal/pipeline.py
----------------------
Pipeline sincrona per l'elaborazione multimodale (Media Resolution + STT).

Principi architetturali:
1. Coordinamento sequenziale e sincrono: UnifiedMessage -> MediaResolver -> ResolvedMediaAsset -> AudioTranscriber -> AudioTranscriptionResult.
2. Il UnifiedMessage originale non viene mai modificato o arricchito internamente nel campo text_content.
3. Il testo trascritto costituisce un'evidenza derivata arricchita collegata all'asset, preservando la netta distinzione
   tra TESTO ORIGINALE DEL MESSAGGIO e TESTO DERIVATO DA STT.
"""
from __future__ import annotations

from typing import Iterable, Iterator

from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    ResolvedMediaAsset,
)
from multimodal.ocr import BaseImageTextExtractor
from multimodal.resolver import MediaResolver
from multimodal.transcriber import BaseAudioTranscriber
from multimodal.vision import BaseImageVisionAnalyzer
from unified.models import UnifiedMessage


class MultimodalAudioPipeline:
    """
    Pipeline per la risoluzione dei file multimediali e la trascrizione dei messaggi vocali.
    """

    def __init__(
        self,
        resolver: MediaResolver,
        transcriber: BaseAudioTranscriber,
    ) -> None:
        self.resolver = resolver
        self.transcriber = transcriber

    def process_message(
        self, message: UnifiedMessage
    ) -> tuple[ResolvedMediaAsset, AudioTranscriptionResult | None]:
        """
        Elabora un singolo UnifiedMessage:
        1. Risolve l'asset multimediale;
        2. Se l'asset è un file audio risolto con successo, invoca il motore STT;
        3. Restituisce la tupla (asset, transcription_result).
        """
        asset = self.resolver.resolve(message)

        # Trascrizione solo se audio risolto fisicamente sul filesystem
        transcription: AudioTranscriptionResult | None = None
        if asset.is_resolved and asset.media_kind == MediaKind.AUDIO:
            transcription = self.transcriber.transcribe(asset)

        return asset, transcription

    def process_messages(
        self, messages: Iterable[UnifiedMessage]
    ) -> Iterator[tuple[ResolvedMediaAsset, AudioTranscriptionResult | None]]:
        """
        Elabora in streaming una sequenza di UnifiedMessage.
        """
        for msg in messages:
            yield self.process_message(msg)


class MultimodalOcrPipeline:
    """
    Pipeline per la risoluzione dei file multimediali e l'estrazione OCR dalle immagini.

    Principi architetturali:
    1. Risolve l'asset fisico tramite MediaResolver.
    2. Invoca l'estrattore OCR solo ed esclusivamente se l'asset è risolto e di tipo IMAGE.
    3. RIGOROSA IMMUTABILITÀ: message.text_content NON viene MAI alterato. Il testo OCR
       risiede unicamente nel modello ImageOcrResult con pieno tracciamento di provenienza.
    """

    def __init__(
        self,
        resolver: MediaResolver,
        extractor: BaseImageTextExtractor,
    ) -> None:
        self.resolver = resolver
        self.extractor = extractor

    def process_message(
        self, message: UnifiedMessage
    ) -> tuple[ResolvedMediaAsset, ImageOcrResult | None]:
        """
        Elabora un singolo UnifiedMessage:
        1. Risolve l'asset multimediale;
        2. Se l'asset è un'immagine risolta con successo, invoca il motore OCR;
        3. Restituisce la tupla (asset, ocr_result).
        """
        asset = self.resolver.resolve(message)

        # OCR solo se immagine risolta fisicamente sul filesystem
        ocr_result: ImageOcrResult | None = None
        if asset.is_resolved and asset.media_kind == MediaKind.IMAGE:
            ocr_result = self.extractor.extract(asset)

        return asset, ocr_result

    def process_messages(
        self, messages: Iterable[UnifiedMessage]
    ) -> Iterator[tuple[ResolvedMediaAsset, ImageOcrResult | None]]:
        """
        Elabora in streaming una sequenza di UnifiedMessage.
        """
        for msg in messages:
            yield self.process_message(msg)


class MultimodalVisionPipeline:
    """
    Pipeline per la risoluzione dei file multimediali e l'analisi del contenuto visivo (Vision).

    Principi architetturali:
    1. Risolve l'asset fisico tramite MediaResolver.
    2. Invoca l'analizzatore Vision solo ed esclusivamente se l'asset è risolto e di tipo IMAGE.
    3. RIGOROSA IMMUTABILITÀ: message.text_content NON viene MAI alterato. La descrizione visiva
       risiede unicamente nel modello ImageVisionResult con pieno tracciamento di provenienza.
    """

    def __init__(
        self,
        resolver: MediaResolver,
        analyzer: BaseImageVisionAnalyzer,
    ) -> None:
        self.resolver = resolver
        self.analyzer = analyzer

    def process_message(
        self, message: UnifiedMessage
    ) -> tuple[ResolvedMediaAsset, ImageVisionResult | None]:
        """
        Elabora un singolo UnifiedMessage:
        1. Risolve l'asset multimediale;
        2. Se l'asset è un'immagine risolta con successo, invoca l'analizzatore Vision;
        3. Restituisce la tupla (asset, vision_result).
        """
        asset = self.resolver.resolve(message)

        # Vision solo se immagine risolta fisicamente sul filesystem
        vision_result: ImageVisionResult | None = None
        if asset.is_resolved and asset.media_kind == MediaKind.IMAGE:
            vision_result = self.analyzer.analyze(asset)

        return asset, vision_result

    def process_messages(
        self, messages: Iterable[UnifiedMessage]
    ) -> Iterator[tuple[ResolvedMediaAsset, ImageVisionResult | None]]:
        """
        Elabora in streaming una sequenza di UnifiedMessage.
        """
        for msg in messages:
            yield self.process_message(msg)


