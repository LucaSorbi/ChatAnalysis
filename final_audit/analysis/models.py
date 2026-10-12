"""
analysis/models.py
------------------
Modelli di dominio e DTO per l'orchestrazione dell'analisi tematica operativa (AI locale).

Principi architetturali:
- TIPAGGIO FORTE E IMMUTABILITÀ: Dataclass frozen per preservare l'integrità dei risultati.
- SEPARAZIONE RIGOROSA: Distinzione tra risultati live, riferimenti sintetici e mock.
- METADATA FORENSI: Tracciamento di run_id, provenance, chunking, modello e latenze.
- MODELLO DI RUN AUTORITATIVO: Ogni esecuzione produce un run_id immutabile.
- CONFIGURAZIONE CENTRALIZZATA: OperationalAnalysisConfig per evitare magic numbers.
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


class OperationalAnalysisFailedError(Exception):
    """Sollevata quando l'analisi tematica fallisce completamente (zero chunk riusciti)."""
    pass


class AnalysisNoEvidenceError(Exception):
    """Sollevata quando il documento non contiene alcuna sezione di evidenza analizzabile."""
    pass


@dataclass(frozen=True)
class OperationalAnalysisConfig:
    """
    Configurazione centralizzata per l'analisi tematica operativa locale.
    Elimina i magic numbers sparsi e documenta il budget prudenziale.
    """
    model_spec: str = "qwen2.5-7b-instruct"
    context_length: int = 8192
    timeout_seconds: float = 240.0
    max_tokens: int = 512
    temperature: float = 0.0
    seed: int | None = 42
    max_topics: int = 10
    chunk_character_budget: int = 7500  # Budget prudenziale per context 8192 (~1800-2000 token stimati)
    max_chunk_bundles: int = 25


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
    failed_chunk_count: int = 0
    reduction_strategy: str | None = None
    data_classification: str = "REAL_OR_RESTRICTED"
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Converte i metadata in un dizionario sicuro per inclusione nel risultato."""
        d = {
            "run_id": self.run_id,
            "origin": self.origin.value if hasattr(self.origin, "value") else str(self.origin),
            "model": self.model,
            "requested_model": self.requested_model,
            "latency_seconds": self.latency_seconds,
            "prompt_version": self.prompt_version,
            "strategy": self.strategy,
            "temperature": self.temperature,
            "seed": self.seed,
            "chunk_count": int(self.chunk_count),
            "completed_chunk_count": int(self.completed_chunk_count),
            "error_count": int(self.error_count),
            "failed_chunk_count": int(self.failed_chunk_count),
            "data_classification": self.data_classification,
        }
        if self.reduction_strategy:
            d["reduction_strategy"] = self.reduction_strategy
        if self.timestamp:
            d["timestamp"] = self.timestamp
        return d


@dataclass(frozen=True)
class DiscoveryChunkOutcome:
    """Esito dell'esecuzione di Topic Discovery su un singolo chunk."""
    chunk_id: str
    status: str  # "SUCCESS" | "FAILED"
    result: TopicDiscoveryResult | None = None
    error_type: str | None = None
    safe_error_code: str | None = None


@dataclass(frozen=True)
class DiscoveredTopicRef:
    """
    Riferimento immutabile a un topic scoperto, vincolato al documento e alla run di provenienza.
    """
    document_id: str
    discovery_run_id: str
    topic_index: int
    topic: DiscoveredTopic


@dataclass
class DocumentAnalysisState:
    """
    Stato di analisi scoped per singolo ConversationEvidenceDocument (document_id).
    Garantisce l'isolamento tra conversazioni senza perdita o sovrascrittura di risultati.
    Mantiene la cronologia immutabile delle run (detection_runs, discovery_runs, consistency_runs)
    e fa da unica fonte di verità per la conversazione.
    """
    document_id: str
    detection_runs: tuple[TopicDetectionResult, ...] = ()
    discovery_runs: tuple[TopicDiscoveryResult, ...] = ()
    consistency_runs: tuple[ConsistencyResult, ...] = ()
    latest_detection_run_id: str | None = None
    latest_discovery_run_id: str | None = None
    selected_model: str = "qwen2.5-7b-instruct"
    cached_evidence_index: Any | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    # Proprietà di compatibilità verso le viste
    @property
    def detection_results(self) -> tuple[TopicDetectionResult, ...]:
        latest_by_topic: dict[str, TopicDetectionResult] = {}
        for r in self.detection_runs:
            key = r.topic.topic_id if (r.topic and r.topic.topic_id) else (r.topic.label if r.topic else str(id(r)))
            latest_by_topic[key] = r
        return tuple(latest_by_topic.values())

    @detection_results.setter
    def detection_results(self, val: Sequence[TopicDetectionResult]) -> None:
        self.detection_runs = tuple(val)
        if self.detection_runs:
            self.latest_detection_run_id = self.detection_runs[-1].metadata.get("run_id")

    @property
    def discovery_results(self) -> tuple[TopicDiscoveryResult, ...]:
        return self.discovery_runs

    @discovery_results.setter
    def discovery_results(self, val: Sequence[TopicDiscoveryResult]) -> None:
        self.discovery_runs = tuple(val)
        if self.discovery_runs:
            self.latest_discovery_run_id = self.discovery_runs[-1].metadata.get("run_id")

    @property
    def consistency_results(self) -> tuple[ConsistencyResult, ...]:
        return self.consistency_runs

    @consistency_results.setter
    def consistency_results(self, val: Sequence[ConsistencyResult]) -> None:
        self.consistency_runs = tuple(val)

    @property
    def last_detection(self) -> TopicDetectionResult | None:
        return self.detection_runs[-1] if self.detection_runs else None

    @last_detection.setter
    def last_detection(self, val: TopicDetectionResult | None) -> None:
        if val is not None and (not self.detection_runs or self.detection_runs[-1] != val):
            self.add_detection_result(val)

    @property
    def last_discovery(self) -> TopicDiscoveryResult | None:
        return self.discovery_runs[-1] if self.discovery_runs else None

    @last_discovery.setter
    def last_discovery(self, val: TopicDiscoveryResult | None) -> None:
        if val is not None and (not self.discovery_runs or self.discovery_runs[-1] != val):
            self.add_discovery_result(val)

    def add_detection_result(self, result: TopicDetectionResult) -> None:
        if self.detection_runs and self.detection_runs[-1] == result:
            return
        run_id = result.metadata.get("run_id") if hasattr(result, "metadata") and isinstance(result.metadata, Mapping) else None
        if run_id and any(isinstance(r.metadata, Mapping) and r.metadata.get("run_id") == run_id for r in self.detection_runs):
            return
        self.detection_runs = self.detection_runs + (result,)
        self.latest_detection_run_id = run_id

    def add_discovery_result(self, result: TopicDiscoveryResult) -> None:
        if self.discovery_runs and self.discovery_runs[-1] == result:
            return
        run_id = result.metadata.get("run_id") if hasattr(result, "metadata") and isinstance(result.metadata, Mapping) else None
        if run_id and any(isinstance(r.metadata, Mapping) and r.metadata.get("run_id") == run_id for r in self.discovery_runs):
            return
        self.discovery_runs = self.discovery_runs + (result,)
        self.latest_discovery_run_id = run_id

    def add_consistency_result(self, result: ConsistencyResult) -> None:
        if self.consistency_runs and self.consistency_runs[-1] == result:
            return
        self.consistency_runs = self.consistency_runs + (result,)
