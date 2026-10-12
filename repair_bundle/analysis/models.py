"""
analysis/models.py
------------------
Modelli di dominio e DTO per l'orchestrazione dell'analisi tematica operativa (AI locale).

Principi architetturali:
- TIPAGGIO FORTE E IMMUTABILITÀ: Dataclass frozen per preservare l'integrità dei risultati.
- SEPARAZIONE RIGOROSA: Distinzione tra risultati live, riferimenti sintetici e mock.
- METADATA FORENSI: Tracciamento di run_id, provenance, chunking, modello e latenze.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ai.models import (
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)


class AnalysisOrigin(str, Enum):
    """Origine dell'esecuzione dell'analisi tematica."""
    LIVE_LM_STUDIO = "LIVE_LM_STUDIO"
    SYNTHETIC_REFERENCE = "SYNTHETIC_REFERENCE"
    MOCK_INSPECTION = "MOCK_INSPECTION"


class ConsistencyStatus(str, Enum):
    """
    Stato di coerenza tra Topic Discovery e successiva Topic Detection indipendente.

    Regole:
    - CONSISTENT: Detection = PRESENT e vi è almeno un evidence_id in comune.
    - PARTIALLY_CONSISTENT: Detection = PRESENT ma le evidenze citate sono disgiunte.
    - DISCREPANCY_ABSENT: Discovery ha individuato il topic ma Detection = ABSENT.
    - DISCREPANCY_UNCERTAIN: Discovery ha individuato il topic ma Detection = UNCERTAIN.
    """
    CONSISTENT = "CONSISTENT"
    PARTIALLY_CONSISTENT = "PARTIALLY_CONSISTENT"
    DISCREPANCY_ABSENT = "DISCREPANCY_ABSENT"
    DISCREPANCY_UNCERTAIN = "DISCREPANCY_UNCERTAIN"


@dataclass(frozen=True)
class ConsistencyResult:
    """
    Esito della verifica di consistenza tra Topic Discovery e Topic Detection ("prova del nove").
    """
    document_id: str
    discovery_run_id: str | None
    detection_run_id: str | None
    topic_label: str
    discovery_evidence_ids: tuple[str, ...]
    detection_evidence_ids: tuple[str, ...]
    overlap_evidence_ids: tuple[str, ...]
    status: ConsistencyStatus
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TopicAnalysisRunMetadata:
    """
    Metadata di tracciamento e diagnostica per una singola esecuzione di analisi.
    """
    run_id: str
    origin: AnalysisOrigin
    model: str
    requested_model: str
    latency_seconds: float
    prompt_version: str
    strategy: str
    temperature: float
    seed: int | None
    chunk_count: int
    completed_chunk_count: int
    error_count: int = 0
    timestamp: str | None = None


@dataclass
class DocumentAnalysisState:
    """
    Stato di analisi scoped per singolo ConversationEvidenceDocument (document_id).
    Garantisce l'isolamento tra conversazioni senza perdita o sovrascrittura di risultati.
    """
    document_id: str
    detection_results: tuple[TopicDetectionResult, ...] = ()
    discovery_results: tuple[TopicDiscoveryResult, ...] = ()
    consistency_results: tuple[ConsistencyResult, ...] = ()
    last_detection: TopicDetectionResult | None = None
    last_discovery: TopicDiscoveryResult | None = None
    selected_model: str = "qwen2.5-7b-instruct"
    provenance: dict[str, Any] = field(default_factory=dict)
