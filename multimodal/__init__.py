"""
multimodal
----------
Package per l'elaborazione multimodale forense:
- Risoluzione sicura e verifica di integrità degli asset multimediali (MediaResolver);
- Speech-to-Text per note vocali e file audio (FasterWhisperTranscriber, FakeAudioTranscriber);
- Pipeline di arricchimento sincrona (MultimodalAudioPipeline).
"""
from __future__ import annotations

from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    MediaKind,
    MediaResolutionStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
)
from multimodal.pipeline import MultimodalAudioPipeline
from multimodal.resolver import (
    MediaResolutionContext,
    MediaResolver,
    compute_sha256_chunked,
)
from multimodal.transcriber import (
    BaseAudioTranscriber,
    FakeAudioTranscriber,
    FasterWhisperTranscriber,
)

__all__ = [
    "MediaKind",
    "MediaResolutionStatus",
    "ResolvedMediaAsset",
    "AudioTranscriptSegment",
    "TranscriptionStatus",
    "AudioTranscriptionResult",
    "MediaResolutionContext",
    "MediaResolver",
    "compute_sha256_chunked",
    "BaseAudioTranscriber",
    "FakeAudioTranscriber",
    "FasterWhisperTranscriber",
    "MultimodalAudioPipeline",
]
