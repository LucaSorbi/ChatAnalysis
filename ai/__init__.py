"""
ai
--
Package per la Local LLM Foundation, integrazione con LM Studio, Topic Detection,
Open Topic Discovery e sperimentazione multilingue orientata alla riproducibilità.
"""
from ai.backend import (
    AiBackendError,
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendUnavailableError,
    AiModelMismatchError,
    AiModelNotSpecifiedError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
    BaseLocalLlmClient,
    FakeLocalLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
    AiBackendTimeoutError,
)
from ai.benchmark import (
    TopicDetectionMetrics,
    create_synthetic_benchmark_documents,
    run_synthetic_benchmark,
    run_synthetic_discovery_benchmark,
    save_benchmark_report,
)
from ai.chunking import (
    ChunkAnalysisOutcome,
    aggregate_topic_detection,
    aggregate_topic_discovery,
    split_document_into_chunks,
    verify_translated_document_size,
)
from ai.discovery import (
    ModelDiscoveryReport,
    discover_models_on_client,
    infer_model_family,
)
from ai.hardware import (
    HardwareProfile,
    probe_local_hardware,
)
from ai.lmstudio import LmStudioClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
    ExperimentModelSpec,
    ModelFamily,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.serializer import (
    EVIDENCE_DATA_END_DELIMITER,
    EVIDENCE_DATA_START_DELIMITER,
    SYSTEM_SECURITY_DIRECTIVE,
    serialize_document_for_llm,
)
from ai.topics import (
    PROMPT_VERSION_DETECTION,
    PROMPT_VERSION_DISCOVERY,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from ai.translation import (
    PROMPT_VERSION_TRANSLATION,
    EvidenceTranslator,
)

__all__ = [
    # Models
    "ModelFamily",
    "ExperimentModelSpec",
    "ConversationEvidenceDocument",
    "TopicQuery",
    "TopicDecision",
    "TopicDetectionResult",
    "DiscoveredTopic",
    "TopicDiscoveryResult",
    "AnalysisLanguageStrategy",
    "EvidenceTranslationItem",
    "EvidenceTranslationResult",
    # Backend & Clients
    "BaseLocalLlmClient",
    "FakeLocalLlmClient",
    "LlmCompletionResponse",
    "LmStudioClient",
    # Exceptions
    "AiBackendError",
    "AiBackendUnavailableError",
    "LmStudioUnavailableError",
    "AiBackendTimeoutError",
    "AiStructuredOutputError",
    "AnalysisInputTooLargeError",
    "AiModelNotSpecifiedError",
    "AiModelMismatchError",
    "AiBackendProtocolError",
    "AiBackendRequestError",
    # Analyzers
    "TopicDetectionAnalyzer",
    "TopicDiscoveryAnalyzer",
    "EvidenceTranslator",
    # Serializer
    "serialize_document_for_llm",
    "EVIDENCE_DATA_START_DELIMITER",
    "EVIDENCE_DATA_END_DELIMITER",
    "SYSTEM_SECURITY_DIRECTIVE",
    # Chunking & Aggregation
    "split_document_into_chunks",
    "verify_translated_document_size",
    "aggregate_topic_detection",
    "aggregate_topic_discovery",
    "ChunkAnalysisOutcome",
    # Hardware & Discovery
    "HardwareProfile",
    "probe_local_hardware",
    "ModelDiscoveryReport",
    "discover_models_on_client",
    "infer_model_family",
    # Benchmark
    "create_synthetic_benchmark_documents",
    "run_synthetic_benchmark",
    "run_synthetic_discovery_benchmark",
    "save_benchmark_report",
    "TopicDetectionMetrics",
    # Versions
    "PROMPT_VERSION_DETECTION",
    "PROMPT_VERSION_DISCOVERY",
    "PROMPT_VERSION_TRANSLATION",
]
