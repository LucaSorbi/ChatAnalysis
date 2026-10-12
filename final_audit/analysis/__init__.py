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
    ConsistencyResult,
    ConsistencyStatus,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    DocumentAnalysisState,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    TopicAnalysisRunMetadata,
)
from analysis.service import LocalTopicAnalysisService

__all__ = [
    "AnalysisNoEvidenceError",
    "AnalysisOrigin",
    "ConsistencyResult",
    "ConsistencyStatus",
    "ConsistencyService",
    "DiscoveredTopicRef",
    "DiscoveryChunkOutcome",
    "DocumentAnalysisState",
    "LocalTopicAnalysisService",
    "OperationalAnalysisConfig",
    "OperationalAnalysisFailedError",
    "TopicAnalysisRunMetadata",
]
