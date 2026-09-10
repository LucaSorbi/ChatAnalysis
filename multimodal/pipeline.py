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

from multimodal.models import AudioTranscriptionResult, MediaKind, ResolvedMediaAsset
from multimodal.resolver import MediaResolver
from multimodal.transcriber import BaseAudioTranscriber
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
