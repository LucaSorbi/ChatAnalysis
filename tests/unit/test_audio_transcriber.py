"""
tests/unit/test_audio_transcriber.py
------------------------------------
Test unitari per il layer di trascrizione audio STT:
- Funzionamento di FakeAudioTranscriber per test deterministici
- Preservazione dei segmenti temporali e del loro ordinamento
- Preservazione della lingua rilevata e della probabilità
- Rifiuto di asset non risolti (NO_AUDIO)
- Rifiuto di asset non audio (UNSUPPORTED)
- Gestione controllata dei fallimenti (FAILED) senza crash
- Lazy loading di FasterWhisperTranscriber (nessun download/caricamento modello su init)
- Coordinamento MultimodalAudioPipeline ed estraneità rispetto a UnifiedMessage.text_content
- Immutabilità profonda dei modelli di trascrizione
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    MediaKind,
    MediaResolutionStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
)
from multimodal.pipeline import MultimodalAudioPipeline
from multimodal.resolver import MediaResolver
from multimodal.transcriber import FakeAudioTranscriber, FasterWhisperTranscriber
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from importer.models import RawRecord
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_dummy_asset(
    status: MediaResolutionStatus = MediaResolutionStatus.RESOLVED,
    media_kind: MediaKind = MediaKind.AUDIO,
    resolved_path: str | None = "/path/test.opus",
) -> ResolvedMediaAsset:
    return ResolvedMediaAsset(
        message_id="unified:msgstore_db:10",
        source_name="msgstore_db",
        source_record_id="10",
        raw_reference="WhatsApp Audio/AUD_00010.opus",
        resolved_path=resolved_path,
        media_kind=media_kind,
        status=status,
        file_size_bytes=282,
        sha256="fake_sha256",
    )


@pytest.mark.unit
class TestFakeAudioTranscriber:

    def test_transcribe_resolved_audio_success(self):
        transcriber = FakeAudioTranscriber(default_language="it", default_probability=0.97)
        asset = _make_dummy_asset(status=MediaResolutionStatus.RESOLVED, media_kind=MediaKind.AUDIO)

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.SUCCESS
        assert res.message_id == asset.message_id
        assert res.detected_language == "it"
        assert res.language_probability == 0.97
        assert len(res.segments) > 0
        assert res.full_transcript == "Nota vocale di test. Contenuto sintetico verificato."

        # Verifica ordinamento e campi dei segmenti
        prev_end = 0.0
        for s in res.segments:
            assert s.start_seconds >= prev_end
            assert s.end_seconds > s.start_seconds
            assert len(s.text) > 0
            prev_end = s.end_seconds

    def test_reject_unresolved_asset(self):
        transcriber = FakeAudioTranscriber()
        asset = _make_dummy_asset(status=MediaResolutionStatus.MISSING, resolved_path=None)

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.NO_AUDIO
        assert res.full_transcript == ""
        assert len(res.segments) == 0
        assert "Asset non risolto" in (res.error_message or "")

    def test_reject_non_audio_asset(self):
        transcriber = FakeAudioTranscriber()
        asset = _make_dummy_asset(
            status=MediaResolutionStatus.RESOLVED,
            media_kind=MediaKind.IMAGE,
            resolved_path="/path/test.jpg",
        )

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.UNSUPPORTED
        assert res.full_transcript == ""
        assert len(res.segments) == 0
        assert "IMAGE" in (res.error_message or "")

    def test_simulated_failure_handled_gracefully(self):
        transcriber = FakeAudioTranscriber(simulate_failure=True, failure_error="Simulated decoding corruption")
        asset = _make_dummy_asset()

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.FAILED
        assert res.error_message == "Simulated decoding corruption"
        assert res.full_transcript == ""

    def test_custom_canned_segments(self):
        custom_segs = (
            AudioTranscriptSegment(start_seconds=0.0, end_seconds=1.2, text="Uno"),
            AudioTranscriptSegment(start_seconds=1.2, end_seconds=2.4, text="Due"),
            AudioTranscriptSegment(start_seconds=2.4, end_seconds=3.6, text="Tre"),
        )
        transcriber = FakeAudioTranscriber(canned_segments=custom_segs, default_language="es")
        asset = _make_dummy_asset()

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.SUCCESS
        assert res.segments == custom_segs
        assert res.full_transcript == "Uno Due Tre"
        assert res.detected_language == "es"


@pytest.mark.unit
class TestFasterWhisperTranscriberUnit:

    def test_lazy_loading_model_not_loaded_on_init(self):
        """Verifica che la creazione dell'istanza non carichi il modello né effettui download."""
        transcriber = FasterWhisperTranscriber(model_name="tiny", device="cpu", compute_type="int8")
        assert transcriber.is_model_loaded() is False

    def test_reject_unresolved_without_loading_model(self):
        """Un asset non risolto deve essere rifiutato prima di caricare il modello."""
        transcriber = FasterWhisperTranscriber(model_name="tiny", device="cpu")
        asset = _make_dummy_asset(status=MediaResolutionStatus.MISSING, resolved_path=None)

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.NO_AUDIO
        assert transcriber.is_model_loaded() is False

    def test_reject_non_audio_without_loading_model(self):
        """Un asset immagine deve essere rifiutato senza caricare il modello."""
        transcriber = FasterWhisperTranscriber(model_name="tiny", device="cpu")
        asset = _make_dummy_asset(status=MediaResolutionStatus.RESOLVED, media_kind=MediaKind.IMAGE)

        res = transcriber.transcribe(asset)
        assert res.status == TranscriptionStatus.UNSUPPORTED
        assert transcriber.is_model_loaded() is False


@pytest.mark.unit
class TestMultimodalAudioPipeline:

    def _create_message(
        self, source_record_id: str, media_ref: str | None, msg_type: CanonicalMessageType, text: str | None = "Originale"
    ) -> UnifiedMessage:
        raw = RawRecord(
            source_name="src",
            source_path="/path",
            source_record_id=source_record_id,
            record_type="message",
            raw_fields={},
            media_reference=media_ref,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="src",
            source_record_id=source_record_id,
            record_type="message",
            timestamp=ts,
            message_type=msg_type,
            text_content=text,
            media_reference=media_ref,
        )
        return UnifiedMessage(
            message_id=f"unified:src:{source_record_id}",
            source_name="src",
            source_record_id=source_record_id,
            source_path="/path",
            record_type="message",
            timestamp=ts,
            message_type=msg_type,
            text_content=text,
            media_reference=media_ref,
            provenance_record=norm,
        )

    def test_pipeline_audio_transcription_preserves_unified_message(self, tmp_path: Path):
        audio_file = tmp_path / "voice.opus"
        audio_file.write_bytes(b"audio_dummy")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        transcriber = FakeAudioTranscriber(default_language="it")
        pipeline = MultimodalAudioPipeline(resolver=resolver, transcriber=transcriber)

        msg = self._create_message("m1", "voice.opus", CanonicalMessageType.AUDIO, text="Testo originale messaggio")
        asset, transcript = pipeline.process_message(msg)

        # Asset risolto
        assert asset.is_resolved is True
        assert asset.media_kind == MediaKind.AUDIO

        # Trascrizione effettuata
        assert transcript is not None
        assert transcript.status == TranscriptionStatus.SUCCESS
        assert transcript.full_transcript == "Nota vocale di test. Contenuto sintetico verificato."

        # IMPORTANTE: Il UnifiedMessage non deve essere toccato!
        assert msg.text_content == "Testo originale messaggio"
        assert msg.media_reference == "voice.opus"

    def test_pipeline_text_message_produces_no_transcription(self, tmp_path: Path):
        resolver = MediaResolver(allowed_roots=[tmp_path])
        transcriber = FakeAudioTranscriber()
        pipeline = MultimodalAudioPipeline(resolver=resolver, transcriber=transcriber)

        msg = self._create_message("m2", None, CanonicalMessageType.TEXT, text="Solo testo")
        asset, transcript = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.NO_REFERENCE
        assert transcript is None
        assert msg.text_content == "Solo testo"

    def test_pipeline_image_message_produces_no_transcription(self, tmp_path: Path):
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"image_dummy")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        transcriber = FakeAudioTranscriber()
        pipeline = MultimodalAudioPipeline(resolver=resolver, transcriber=transcriber)

        msg = self._create_message("m3", "photo.jpg", CanonicalMessageType.IMAGE, text="Guarda la foto")
        asset, transcript = pipeline.process_message(msg)

        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.IMAGE
        # Nessuna trascrizione su immagini
        assert transcript is None
        assert msg.text_content == "Guarda la foto"


@pytest.mark.unit
class TestTranscriptionModelsDeepImmutability:

    def test_segment_immutability(self):
        seg = AudioTranscriptSegment(start_seconds=1.0, end_seconds=2.5, text="Ciao")
        with pytest.raises(FrozenInstanceError):
            seg.text = "Modificato"  # type: ignore[misc]

    def test_transcription_result_immutability(self):
        res = AudioTranscriptionResult(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Ciao a tutti",
            metadata={"extra": {"score": 99}},
        )
        with pytest.raises(FrozenInstanceError):
            res.status = TranscriptionStatus.FAILED  # type: ignore[misc]

        with pytest.raises(TypeError):
            res.metadata["extra"]["score"] = 0  # type: ignore[index]
