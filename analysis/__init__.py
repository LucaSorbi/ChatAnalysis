"""
analysis package
----------------
Layer applicativo di orchestrazione per l'analisi tematica locale e forense.
"""
from __future__ import annotations

from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisNoEvidenceError,
    AnalysisOrigin,
    AttestationLevel,
    ConsistencyResult,
    ConsistencyStatus,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    DocumentAnalysisState,
    InsufficientContextHeadroomError,
    LoadingMode,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
    TopicAnalysisRunMetadata,
    TopicProvenanceError,
)
from analysis.service import LocalTopicAnalysisService
from analysis.export import (
    SCHEMA_VERSION,
    SOFTWARE_NAME,
    build_topic_detection_export_report,
    build_topic_discovery_export_report,
    generate_export_filename,
    serialize_report_to_json,
)

__all__ = [
    "AnalysisNoEvidenceError",
    "AnalysisOrigin",
    "AttestationLevel",
    "ConsistencyResult",
    "ConsistencyStatus",
    "ConsistencyService",
    "DiscoveredTopicRef",
    "DiscoveryChunkOutcome",
    "DocumentAnalysisState",
    "InsufficientContextHeadroomError",
    "LoadingMode",
    "LocalTopicAnalysisService",
    "OperationalAnalysisConfig",
    "OperationalAnalysisFailedError",
    "OperationalProfile",
    "SCHEMA_VERSION",
    "SOFTWARE_NAME",
    "TopicAnalysisRunMetadata",
    "TopicProvenanceError",
    "build_topic_detection_export_report",
    "build_topic_discovery_export_report",
    "generate_export_filename",
    "serialize_report_to_json",
]
