"""Smoke tests for FasterWhisper real backend.

These tests verify the optional real FasterWhisper speech-to-text engine.
They are marked with `@pytest.mark.smoke` and excluded from default test runs.
To run explicitly: pytest -m smoke
"""

import os
import wave
import struct
import tempfile
from pathlib import Path
import pytest

from multimodal.models import (
    MediaKind,
    MediaResolutionStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
)
from multimodal.transcriber import FasterWhisperTranscriber


@pytest.mark.smoke
class TestFasterWhisperSmoke:
    """Smoke test suite for FasterWhisperTranscriber."""

    def test_faster_whisper_backend_available(self) -> None:
        """Verify that faster-whisper and ctranslate2 are installed and detectable."""
        assert FasterWhisperTranscriber.is_available() is True

    def test_faster_whisper_instantiation(self) -> None:
        """Verify that FasterWhisperTranscriber initializes its attributes cleanly."""
        transcriber = FasterWhisperTranscriber(
            model_size="tiny",
            device="cpu",
            compute_type="int8",
        )
        assert transcriber.model_size == "tiny"
        assert transcriber.device == "cpu"
        assert transcriber.compute_type == "int8"
        assert transcriber._model is None  # Lazy loading: model not loaded yet

    def test_faster_whisper_real_inference(self) -> None:
        """Real model inference test (skipped unless RUN_REAL_WHISPER=1)."""
        if os.environ.get("RUN_REAL_WHISPER") != "1":
            pytest.skip(
                "Real model download skipped. Set RUN_REAL_WHISPER=1 to run real inference test."
            )

        # Generate a minimal valid WAV file (1 second of silence/sine wave at 16kHz)
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test_smoke.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                # 16000 samples of silence (0s)
                data = struct.pack(f"<{16000}h", *([0] * 16000))
                wf.writeframes(data)

            asset = ResolvedMediaAsset(
                status=MediaResolutionStatus.RESOLVED,
                canonical_path=wav_path,
                relative_path="test_smoke.wav",
                media_kind=MediaKind.AUDIO,
                mime_type="audio/wav",
                file_size_bytes=wav_path.stat().st_size,
                sha256="fake_sha256_for_smoke",
            )

            transcriber = FasterWhisperTranscriber(
                model_size="tiny",
                device="cpu",
                compute_type="int8",
            )

            result = transcriber.transcribe_audio(asset)

            assert result.status == TranscriptionStatus.SUCCESS
            assert result.backend == "faster-whisper"
            assert result.model_name == "tiny"
            assert isinstance(result.full_text, str)
            assert isinstance(result.segments, tuple)
