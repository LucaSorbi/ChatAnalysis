"""
multimodal/transcriber.py
-------------------------
Interfaccia e implementazioni per la trascrizione vocale (Speech-to-Text).

Principi architetturali:
1. BaseAudioTranscriber definisce il contratto astratto per consentire l'intercambiabilità dei motori STT.
2. FakeAudioTranscriber consente l'esecuzione di test unitari veloci, deterministici e offline senza download di modelli.
3. FasterWhisperTranscriber implementa l'integrazione reale con faster-whisper in modalità CPU lazy-loaded.
4. TASK DI SOLA TRASCRIZIONE: nessuna traduzione automatica (STT != Translation); la lingua originale viene preservata.
5. Segmenti temporali (start, end, text) rigorosamente preservati e ordinati.
6. Error handling controllato: per-file failure restituisce TranscriptionStatus.FAILED senza corrompere la pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    MediaKind,
    MediaResolutionStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
)


class BaseAudioTranscriber(ABC):
    """
    Contratto astratto per motori di Speech-to-Text.
    """

    @abstractmethod
    def transcribe(self, asset: ResolvedMediaAsset) -> AudioTranscriptionResult:
        """
        Trascrive un asset audio risolto producendo un AudioTranscriptionResult immutabile.
        """
        raise NotImplementedError

    def transcribe_audio(self, asset: ResolvedMediaAsset) -> AudioTranscriptionResult:
        """Alias amichevole di transcribe()."""
        return self.transcribe(asset)

    @classmethod
    def is_available(cls) -> bool:
        """Indica se le dipendenze runtime del motore sono installate e funzionanti."""
        return True


class FakeAudioTranscriber(BaseAudioTranscriber):
    """
    Motore STT sintetico per test di unità e integrazione.
    Non richiede modelli neurali, dipendenze pesanti o connessioni di rete.
    """

    def __init__(
        self,
        default_language: str = "it",
        default_probability: float = 0.98,
        simulate_failure: bool = False,
        failure_error: str = "Simulated decoder error",
        canned_segments: tuple[AudioTranscriptSegment, ...] | None = None,
    ) -> None:
        self.default_language = default_language
        self.default_probability = default_probability
        self.simulate_failure = simulate_failure
        self.failure_error = failure_error
        self.canned_segments = canned_segments

    def transcribe(self, asset: ResolvedMediaAsset) -> AudioTranscriptionResult:
        # 1. Verifica che l'asset sia risolto
        if not asset.is_resolved:
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.NO_AUDIO,
                full_transcript="",
                segments=(),
                engine="fake-whisper",
                model_name="mock",
                device="cpu",
                compute_type="none",
                error_message=f"Asset non risolto (status={asset.status.value})",
            )

        # 2. Verifica che sia audio
        if asset.media_kind != MediaKind.AUDIO:
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.UNSUPPORTED,
                full_transcript="",
                segments=(),
                engine="fake-whisper",
                model_name="mock",
                device="cpu",
                compute_type="none",
                error_message=f"Tipo media non supportato per STT ({asset.media_kind.value})",
            )

        # 3. Simulazione errore controllato
        if self.simulate_failure:
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.FAILED,
                full_transcript="",
                segments=(),
                engine="fake-whisper",
                model_name="mock",
                device="cpu",
                compute_type="none",
                error_message=self.failure_error,
            )

        # 4. Trascrizione simulata
        if self.canned_segments is not None:
            segments = self.canned_segments
        else:
            segments = (
                AudioTranscriptSegment(start_seconds=0.0, end_seconds=2.5, text="Nota vocale di test."),
                AudioTranscriptSegment(start_seconds=2.5, end_seconds=5.0, text="Contenuto sintetico verificato."),
            )

        full_text = " ".join(s.text for s in segments if s.text).strip()
        return AudioTranscriptionResult(
            message_id=asset.message_id,
            source_name=asset.source_name,
            source_record_id=asset.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript=full_text,
            segments=segments,
            detected_language=self.default_language,
            language_probability=self.default_probability,
            engine="fake-whisper",
            model_name="mock",
            device="cpu",
            compute_type="none",
        )


class FasterWhisperTranscriber(BaseAudioTranscriber):
    """
    Implementazione reale del motore STT basata su faster-whisper (CTranslate2).

    Caratteristiche:
    - Caricamento pigro (lazy loading): il modello non viene scaricato o instanziato finché
      non viene invocata la prima trascrizione o il metodo load_model().
    - Configurazione esplicita per CPU: device="cpu", compute_type="int8".
    - Nessuna dipendenza CUDA obbligatoria.
    - Trascrizione rigorosamente nella lingua d'origine (task="transcribe").
    """

    def __init__(
        self,
        model_name: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        cpu_threads: int = 4,
        *,
        model_size: str | None = None,
    ) -> None:
        actual_model = model_size if model_size is not None else model_name
        self.model_name = actual_model
        self.model_size = actual_model
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.cpu_threads = cpu_threads
        self._model: Any = None

    @classmethod
    def is_available(cls) -> bool:
        """Verifica se faster-whisper e ctranslate2 sono importabili e funzionanti."""
        try:
            import ctranslate2  # noqa: F401
            import faster_whisper  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def load_model(self) -> Any:
        """Carica il modello in memoria (lazy loading)."""
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as err:
                raise ImportError(
                    "Il package 'faster-whisper' non è installato. "
                    "Installalo con: pip install faster-whisper"
                ) from err

            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root,
                cpu_threads=self.cpu_threads,
            )
        return self._model

    def is_model_loaded(self) -> bool:
        """True se il modello è già stato caricato in RAM."""
        return self._model is not None

    def transcribe(
        self,
        asset: ResolvedMediaAsset,
        language: str | None = None,
        beam_size: int = 5,
    ) -> AudioTranscriptionResult:
        # 1. Filtro su asset risolto
        if not asset.is_resolved or not asset.resolved_path:
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.NO_AUDIO,
                full_transcript="",
                segments=(),
                engine="faster-whisper",
                model_name=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                error_message=f"Asset non risolto sul filesystem (status={asset.status.value})",
            )

        # 2. Filtro su media_kind AUDIO
        if asset.media_kind != MediaKind.AUDIO:
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.UNSUPPORTED,
                full_transcript="",
                segments=(),
                engine="faster-whisper",
                model_name=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                error_message=f"Media kind {asset.media_kind.value} non supportato per STT (richiesto AUDIO)",
            )

        # 3. Caricamento modello e trascrizione protetta
        try:
            model = self.load_model()
            # Task esplicito 'transcribe' (NO translate)
            segments_gen, info = model.transcribe(
                asset.resolved_path,
                beam_size=beam_size,
                task="transcribe",
                language=language,
            )

            segments_list: list[AudioTranscriptSegment] = []
            for s in segments_gen:
                clean_text = s.text.strip()
                segments_list.append(
                    AudioTranscriptSegment(
                        start_seconds=round(float(s.start), 3),
                        end_seconds=round(float(s.end), 3),
                        text=clean_text,
                    )
                )

            full_text = " ".join(s.text for s in segments_list if s.text).strip()
            detected_lang = getattr(info, "language", None)
            lang_prob = round(float(info.language_probability), 4) if hasattr(info, "language_probability") else None

            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.SUCCESS,
                full_transcript=full_text,
                segments=tuple(segments_list),
                detected_language=detected_lang,
                language_probability=lang_prob,
                engine="faster-whisper",
                model_name=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                metadata={
                    "duration": getattr(info, "duration", None),
                    "file_path": asset.resolved_path,
                },
            )

        except Exception as e:
            # Per-file failure: non blocca la pipeline
            return AudioTranscriptionResult(
                message_id=asset.message_id,
                source_name=asset.source_name,
                source_record_id=asset.source_record_id,
                status=TranscriptionStatus.FAILED,
                full_transcript="",
                segments=(),
                engine="faster-whisper",
                model_name=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                error_message=str(e),
                metadata={"file_path": asset.resolved_path},
            )
