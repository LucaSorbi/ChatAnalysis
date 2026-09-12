"""
multimodal
----------
Package per l'elaborazione multimodale forense:
- Risoluzione sicura e verifica di integrità degli asset multimediali (MediaResolver);
- Speech-to-Text per note vocali e file audio (FasterWhisperTranscriber, FakeAudioTranscriber);
- Pipeline di arricchimento sincrona (MultimodalAudioPipeline).
"""
from __future__ import annotations

from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
    TextEvidenceSection,
)
from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
    TranscriptionStatus,
    VisionStatus,
)
from multimodal.ocr import (
    BaseImageTextExtractor,
    FakeImageTextExtractor,
    OcrBackendError,
    OcrBackendUnavailableError,
    OcrEngineError,
    TesseractImageTextExtractor,
)
from multimodal.pipeline import (
    MultimodalAudioPipeline,
    MultimodalOcrPipeline,
    MultimodalVisionPipeline,
)
from multimodal.resolver import (
    MediaResolutionContext,
    MediaResolver,
    compute_sha256_chunked,
)
from multimodal.transcriber import (
    AudioBackendError,
    AudioBackendUnavailableError,
    AudioModelLoadError,
    BaseAudioTranscriber,
    FakeAudioTranscriber,
    FasterWhisperTranscriber,
)
from multimodal.vision import (
    BaseImageVisionAnalyzer,
    FakeImageVisionAnalyzer,
    VisionBackendError,
    VisionBackendUnavailableError,
    VisionModelLoadError,
)

__all__ = [
    "MediaKind",
    "MediaResolutionStatus",
    "ResolvedMediaAsset",
    "AudioTranscriptSegment",
    "TranscriptionStatus",
    "AudioTranscriptionResult",
    "OcrStatus",
    "OcrTextRegion",
    "ImageOcrResult",
    "VisionStatus",
    "ImageVisionResult",
    "MediaResolutionContext",
    "MediaResolver",
    "compute_sha256_chunked",
    "AudioBackendError",
    "AudioBackendUnavailableError",
    "AudioModelLoadError",
    "BaseAudioTranscriber",
    "FakeAudioTranscriber",
    "FasterWhisperTranscriber",
    "OcrBackendError",
    "OcrBackendUnavailableError",
    "OcrEngineError",
    "BaseImageTextExtractor",
    "FakeImageTextExtractor",
    "TesseractImageTextExtractor",
    "VisionBackendError",
    "VisionBackendUnavailableError",
    "VisionModelLoadError",
    "BaseImageVisionAnalyzer",
    "FakeImageVisionAnalyzer",
    "MultimodalAudioPipeline",
    "MultimodalOcrPipeline",
    "MultimodalVisionPipeline",
    "EvidenceSourceType",
    "TextEvidenceSection",
    "MessageEvidenceBundle",
]


