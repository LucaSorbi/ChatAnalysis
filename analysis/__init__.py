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
    "TopicAnalysisRunMetadata",
    "TopicProvenanceError",
]
