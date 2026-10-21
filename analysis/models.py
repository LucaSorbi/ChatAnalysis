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
    """Origine dell'esecuzione dell'inferenza tematica."""
    LIVE_LM_STUDIO = "LIVE_LM_STUDIO"
    SYNTHETIC_REFERENCE = "SYNTHETIC_REFERENCE"
    MOCK_INSPECTION = "MOCK_INSPECTION"


class LoadingMode(str, Enum):
    """Modalità di caricamento o recupero del risultato di analisi."""
    IN_MEMORY_EXECUTION = "IN_MEMORY_EXECUTION"
    CHECKPOINT_RESTORE = "CHECKPOINT_RESTORE"


class AttestationLevel(str, Enum):
    """Livello di attestazione e tracciabilità forense della run."""
    LIVE_AUTHORITATIVE = "LIVE_AUTHORITATIVE"
    CHECKPOINT_AUTHENTICATED = "CHECKPOINT_AUTHENTICATED"
    MOCK_VERIFIED = "MOCK_VERIFIED"
    UNATTESTED = "UNATTESTED"


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


class TopicProvenanceError(ValueError):
    """Sollevata quando la provenance documentale o di run di un topic scoperto è violata."""
    pass


class InsufficientContextHeadroomError(ValueError):
    """
    Sollevata quando il context length del runtime è insufficiente a contenere
    l'overhead essenziale (output reserved + safety margin + system/schema overhead)
    e un budget minimo vitale per i chunk documentali.
    Non dichiara MAI token disponibili oltre lo spazio reale.
    """
    def __init__(self, context_length: int, total_overhead: int, available_headroom: int) -> None:
        super().__init__(
            f"Context length insufficiente ({context_length} token): overhead richiesto "
            f"({total_overhead} token) supera lo spazio utile (headroom disponibile: {available_headroom} token)."
        )
        self.context_length = context_length
        self.total_overhead = total_overhead
        self.available_headroom = available_headroom


class OperationalProfile(str, Enum):
    """
    Profili operativi per l'analisi tematica locale:
    - PRUDENT: 2500 token per blocco (massima compatibilità e sicurezza con margini ampi)
    - BALANCED: 4200 token per blocco (profilo raccomandato per contesti >= 8192 token)
    - PERFORMANCE: 5200 token per blocco (massimo throughput con blocchi estesi previa verifica del budget)
    """
    PRUDENT = "PRUDENT"
    BALANCED = "BALANCED"
    PERFORMANCE = "PERFORMANCE"


PROFILE_TOKEN_BUDGETS: dict[OperationalProfile, int] = {
    OperationalProfile.PRUDENT: 2500,
    OperationalProfile.BALANCED: 4200,
    OperationalProfile.PERFORMANCE: 5200,
}

PROFILE_CHARACTER_BUDGETS: dict[OperationalProfile, int] = {
    OperationalProfile.PRUDENT: 12000,
    OperationalProfile.BALANCED: 18000,
    OperationalProfile.PERFORMANCE: 24000,
}


def resolve_operational_profile(
    profile: OperationalProfile | str | None,
    context_length: int = 8192,
    reserved_output_tokens: int = 1500,
    safety_margin: int = 1000,
    system_overhead: int = 400,
) -> tuple[OperationalProfile, int, int]:
    """
    Risolve il profilo operativo verificando rigorosamente la sicurezza rispetto al context budget.
    Se il profilo richiesto (es. PERFORMANCE o BALANCED) supera lo spazio utile disponibile,
    effettua il downgrade automatico e sicuro al profilo compatibile più vicino (o PRUDENT).
    Se l'headroom è inferiore a 256 token o negativo, solleva InsufficientContextHeadroomError
    senza dichiarare artificialmente token inesistenti.
    Restituisce (profilo_effettivo, max_chunk_tokens, max_chunk_characters).
    """
    if profile is None:
        requested = OperationalProfile.BALANCED
    elif isinstance(profile, OperationalProfile):
        requested = profile
    else:
        try:
            requested = OperationalProfile(str(profile).strip().upper())
        except ValueError:
            requested = OperationalProfile.BALANCED

    target_tokens = PROFILE_TOKEN_BUDGETS[requested]
    target_chars = PROFILE_CHARACTER_BUDGETS[requested]

    # Spazio utile reale prima del context overflow
    total_overhead = reserved_output_tokens + safety_margin + system_overhead + 128
    available_headroom = context_length - total_overhead

    MIN_HEADROOM_TOKENS = 256
    if available_headroom < MIN_HEADROOM_TOKENS:
        raise InsufficientContextHeadroomError(
            context_length=context_length,
            total_overhead=total_overhead,
            available_headroom=max(0, available_headroom),
        )

    # Verifica di sicurezza preventiva
    if target_tokens > available_headroom:
        if available_headroom >= PROFILE_TOKEN_BUDGETS[OperationalProfile.BALANCED]:
            return OperationalProfile.BALANCED, PROFILE_TOKEN_BUDGETS[OperationalProfile.BALANCED], PROFILE_CHARACTER_BUDGETS[OperationalProfile.BALANCED]
        elif available_headroom >= PROFILE_TOKEN_BUDGETS[OperationalProfile.PRUDENT]:
            return OperationalProfile.PRUDENT, PROFILE_TOKEN_BUDGETS[OperationalProfile.PRUDENT], PROFILE_CHARACTER_BUDGETS[OperationalProfile.PRUDENT]
        else:
            safe_tokens = available_headroom
            return OperationalProfile.PRUDENT, safe_tokens, max(1000, safe_tokens * 4)

    return requested, target_tokens, target_chars


@dataclass(frozen=True)
class OperationalAnalysisConfig:
    """
    Configurazione centralizzata per l'analisi tematica operativa locale.
    Supporta profili operativi espliciti (PRUDENT, BALANCED, PERFORMANCE)
    con verifica della sicurezza rispetto al context budget.
    """
    model_spec: str = "qwen2.5-7b-instruct"
    context_length: int = 8192
    timeout_seconds: float = 240.0
    max_tokens: int = 512
    temperature: float = 0.0
    seed: int | None = 42
    max_topics: int = 10
    profile: OperationalProfile = OperationalProfile.BALANCED
    chunk_character_budget: int = 18000
    max_chunk_bundles: int = 100
    max_chunk_tokens: int | None = 4200

    def with_profile(self, profile: OperationalProfile | str) -> OperationalAnalysisConfig:
        """Restituisce una nuova configurazione applicando il profilo specificato."""
        res_prof, tok_b, char_b = resolve_operational_profile(
            profile=profile,
            context_length=self.context_length,
        )
        return OperationalAnalysisConfig(
            model_spec=self.model_spec,
            context_length=self.context_length,
            timeout_seconds=self.timeout_seconds,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            seed=self.seed,
            max_topics=self.max_topics,
            profile=res_prof,
            chunk_character_budget=char_b,
            max_chunk_bundles=self.max_chunk_bundles,
            max_chunk_tokens=tok_b,
        )


@dataclass(frozen=True)
class AnalysisPlan:
    """
    Piano analitico preventivo e immutabile condiviso tra UI e Service Layer.
    Garantisce perfetta coerenza dei budget di token e del numero di chunk.
    """
    document_id: str
    analysis_type: str  # "detection" | "discovery"
    planned_chunks_count: int
    chunk_token_budget: int
    chunk_character_budget: int
    input_token_budget: int
    output_token_budget: int
    schema: Mapping[str, Any]
    safety_margin: int
    headroom_tokens: int
    profile: OperationalProfile
    max_topics: int | None
    effective_config: OperationalAnalysisConfig
    chunks: tuple[Any, ...] = ()
    plan_hash: str = ""
    requires_dynamic_splitting: bool = False



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
    document_id: str | None = None
    analysis_type: str | None = None
    resolved_model_key: str | None = None
    loaded_instance_id: str | None = None
    api_identifier: str | None = None
    response_model: str | None = None
    context_length: int | None = None
    max_tokens: int | None = None
    start_timestamp: str | None = None
    end_timestamp: str | None = None
    status: str | None = None
    error_classification: str | None = None

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
        if self.document_id:
            d["document_id"] = self.document_id
        if self.analysis_type:
            d["analysis_type"] = self.analysis_type
        if self.resolved_model_key:
            d["resolved_model_key"] = self.resolved_model_key
        if self.loaded_instance_id:
            d["loaded_instance_id"] = self.loaded_instance_id
        if self.api_identifier:
            d["api_identifier"] = self.api_identifier
        if self.response_model:
            d["response_model"] = self.response_model
        if self.context_length is not None:
            d["context_length"] = self.context_length
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.start_timestamp:
            d["start_timestamp"] = self.start_timestamp
        if self.end_timestamp:
            d["end_timestamp"] = self.end_timestamp
        if self.status:
            d["status"] = self.status
        if self.error_classification:
            d["error_classification"] = self.error_classification
        return d


class ChunkLifecycleStatus(str, Enum):
    """Stati del ciclo di vita per singolo blocco di analisi tematica."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERED_FROM_CHECKPOINT = "RECOVERED_FROM_CHECKPOINT"


class ChunkDiagnosticCategory(str, Enum):
    """
    Categorie diagnostiche standardizzate per i fallimenti di chunk (Sezione 8).
    Codici stabili e comprensibili conformi all'audit.
    """
    HTTP_CONTEXT_OVERFLOW = "HTTP_CONTEXT_OVERFLOW"
    HTTP_INVALID_PARAMETERS = "HTTP_INVALID_PARAMETERS"
    HTTP_SCHEMA_REJECTED = "HTTP_SCHEMA_REJECTED"
    TIMEOUT = "TIMEOUT"
    JSON_DECODE_ERROR = "JSON_DECODE_ERROR"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    INVALID_EVIDENCE_ID = "INVALID_EVIDENCE_ID"
    DUPLICATE_EVIDENCE_ID = "DUPLICATE_EVIDENCE_ID"
    EMPTY_EVIDENCE = "EMPTY_EVIDENCE"
    EXCESS_TOPICS = "EXCESS_TOPICS"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    CHECKPOINT_SAVE_ERROR = "CHECKPOINT_SAVE_ERROR"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    INTERNAL_VALIDATION_ERROR = "INTERNAL_VALIDATION_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    NONE = "NONE"


@dataclass
class ChunkDiagnosticRecord:
    """
    Tracciamento diagnostico persistente e sicuro per singolo blocco di analisi (Sezione 8).
    Non contiene il testo delle conversazioni né dati investigativi sensibili.
    """
    chunk_index: int
    chunk_id: str
    run_id: str = ""
    status: str = ChunkLifecycleStatus.PENDING.value
    attempts: int = 1
    duration_seconds: float = 0.0
    model: str = ""
    model_id: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    exception_class: str | None = None
    error_type: str | None = None
    diagnostic_category: str = ChunkDiagnosticCategory.NONE.value
    failure_phase: str = "completed"  # inference, json_parsing, schema_validation, evidence_validation, checkpoint_save, completed, recovered
    json_validation_ok: bool | None = None
    json_valid: bool | None = None
    schema_validation_ok: bool | None = None
    schema_valid: bool | None = None
    evidence_validation_ok: bool | None = None
    evidence_valid: bool | None = None
    checkpoint_save_ok: bool | None = None
    checkpoint_saved: bool | None = None
    details: str | None = None
    error_detail: str | None = None
    request_correlation_id: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if not self.model and self.model_id:
            self.model = self.model_id
        elif not self.model_id and self.model:
            self.model_id = self.model

        if not self.exception_class and self.error_type:
            self.exception_class = self.error_type
        elif not self.error_type and self.exception_class:
            self.error_type = self.exception_class

        if not self.details and self.error_detail:
            self.details = self.error_detail
        elif not self.error_detail and self.details:
            self.error_detail = self.details

        if self.json_validation_ok is None and self.json_valid is not None:
            self.json_validation_ok = self.json_valid
        elif self.json_valid is None and self.json_validation_ok is not None:
            self.json_valid = self.json_validation_ok

        if self.schema_validation_ok is None and self.schema_valid is not None:
            self.schema_validation_ok = self.schema_valid
        elif self.schema_valid is None and self.schema_validation_ok is not None:
            self.schema_valid = self.schema_validation_ok

        if self.evidence_validation_ok is None and self.evidence_valid is not None:
            self.evidence_validation_ok = self.evidence_valid
        elif self.evidence_valid is None and self.evidence_validation_ok is not None:
            self.evidence_valid = self.evidence_validation_ok

        if self.checkpoint_save_ok is None and self.checkpoint_saved is not None:
            self.checkpoint_save_ok = self.checkpoint_saved
        elif self.checkpoint_saved is None and self.checkpoint_save_ok is not None:
            self.checkpoint_saved = self.checkpoint_save_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "chunk_id": self.chunk_id,
            "run_id": self.run_id,
            "status": self.status,
            "attempts": self.attempts,
            "duration_seconds": self.duration_seconds,
            "model": self.model,
            "model_id": self.model,
            "http_status": self.http_status,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "exception_class": self.exception_class,
            "error_type": self.exception_class,
            "diagnostic_category": self.diagnostic_category,
            "failure_phase": self.failure_phase,
            "json_validation_ok": self.json_validation_ok,
            "json_valid": self.json_validation_ok,
            "schema_validation_ok": self.schema_validation_ok,
            "schema_valid": self.schema_validation_ok,
            "evidence_validation_ok": self.evidence_validation_ok,
            "evidence_valid": self.evidence_validation_ok,
            "checkpoint_save_ok": self.checkpoint_save_ok,
            "checkpoint_saved": self.checkpoint_save_ok,
            "details": self.details,
            "error_detail": self.details,
            "request_correlation_id": self.request_correlation_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkDiagnosticRecord:
        return cls(
            chunk_index=int(data.get("chunk_index", 0)),
            chunk_id=str(data.get("chunk_id", "")),
            run_id=str(data.get("run_id", "")),
            status=str(data.get("status", ChunkLifecycleStatus.PENDING.value)),
            attempts=int(data.get("attempts", 1)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            model=str(data.get("model") or data.get("model_id") or ""),
            http_status=data.get("http_status"),
            finish_reason=data.get("finish_reason"),
            prompt_tokens=data.get("prompt_tokens"),
            completion_tokens=data.get("completion_tokens"),
            total_tokens=data.get("total_tokens"),
            exception_class=data.get("exception_class") or data.get("error_type"),
            diagnostic_category=str(data.get("diagnostic_category", ChunkDiagnosticCategory.NONE.value)),
            failure_phase=str(data.get("failure_phase", "completed")),
            json_validation_ok=data.get("json_validation_ok") if "json_validation_ok" in data else data.get("json_valid"),
            schema_validation_ok=data.get("schema_validation_ok") if "schema_validation_ok" in data else data.get("schema_valid"),
            evidence_validation_ok=data.get("evidence_validation_ok") if "evidence_validation_ok" in data else data.get("evidence_valid"),
            checkpoint_save_ok=data.get("checkpoint_save_ok") if "checkpoint_save_ok" in data else data.get("checkpoint_saved"),
            details=data.get("details") or data.get("error_detail"),
            request_correlation_id=data.get("request_correlation_id"),
            timestamp=data.get("timestamp"),
        )


@dataclass
class DiscoveryChunkOutcome:
    """Esito dell'esecuzione di Topic Discovery su un singolo chunk con tracciamento diagnostico."""
    chunk_id: str
    status: str  # "SUCCESS" | "FAILED" | "RECOVERED_FROM_CHECKPOINT"
    result: TopicDiscoveryResult | None = None
    error_type: str | None = None
    safe_error_code: str | None = None
    chunk_index: int = 0
    diagnostic_record: ChunkDiagnosticRecord | None = None
    diagnostic: ChunkDiagnosticRecord | None = None
    attempts: int = 1
    duration_seconds: float = 0.0
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.diagnostic_record is None and self.diagnostic is not None:
            self.diagnostic_record = self.diagnostic
        elif self.diagnostic is None and self.diagnostic_record is not None:
            self.diagnostic = self.diagnostic_record

    def to_dict(self) -> dict[str, Any]:
        diag = self.diagnostic_record or self.diagnostic
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "status": self.status,
            "error_type": self.error_type,
            "safe_error_code": self.safe_error_code,
            "attempts": self.attempts,
            "duration_seconds": self.duration_seconds,
            "evidence_ids": list(self.evidence_ids),
            "diagnostic_record": diag.to_dict() if diag else None,
            "diagnostic": diag.to_dict() if diag else None,
        }


def classify_discovery_exception(
    exc: Exception,
    http_status: int | None = None,
    finish_reason: str | None = None,
) -> tuple[str, str, str]:
    """
    Classifica deterministicamente un'eccezione insorta durante Topic Discovery
    in una delle categorie standard richieste dall'audit (Sezione 8), individuando la fase
    esatta e producendo una descrizione sicura priva di dati personali o chat text.

    Restituisce: (diagnostic_category, failure_phase, safe_details).
    """
    from ai.backend import (
        AiBackendRequestError,
        AiBackendTimeoutError,
        AiInvalidEvidenceCitationError,
        AiModelMismatchError,
        AiStructuredOutputError,
        AnalysisInputTooLargeError,
    )

    if isinstance(exc, AiInvalidEvidenceCitationError):
        return (
            ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value,
            "evidence_validation",
            "Citazione di evidence_id inesistente o estraneo al blocco corrente",
        )

    if isinstance(exc, AiStructuredOutputError):
        msg = str(exc).lower()
        if "duplicat" in msg:
            return (
                ChunkDiagnosticCategory.DUPLICATE_EVIDENCE_ID.value,
                "evidence_validation",
                "Presenza di evidence_ids duplicati all'interno di un argomento",
            )
        if "almeno un evidence_id" in msg or "vuota" in msg:
            return (
                ChunkDiagnosticCategory.EMPTY_EVIDENCE.value,
                "evidence_validation",
                "Argomento scoperto privo di evidence_ids validi di supporto",
            )
        if "superando il limite" in msg or "limite consentito" in msg:
            return (
                ChunkDiagnosticCategory.EXCESS_TOPICS.value,
                "schema_validation",
                "Numero di argomenti generati superiore al limite consentito",
            )
        if (
            "jsondecodeerror" in msg
            or "unterminated string" in msg
            or "non conforme a json" in msg
            or "oggetto json" in msg
        ):
            return (
                ChunkDiagnosticCategory.JSON_DECODE_ERROR.value,
                "json_parsing",
                "Sintassi JSON non valida o testo di risposta malformato",
            )
        if "length" in msg or finish_reason == "length":
            return (
                ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value,
                "inference",
                "Risposta del modello troncata per superamento del limite max_tokens",
            )
        return (
            ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value,
            "schema_validation",
            "Violazione dello schema strutturato (campi mancanti o tipi non conformi)",
        )

    if isinstance(exc, AiBackendTimeoutError):
        return (
            ChunkDiagnosticCategory.TIMEOUT.value,
            "inference",
            "Timeout durante la richiesta HTTP al backend LLM",
        )

    if isinstance(exc, (AiBackendRequestError, AnalysisInputTooLargeError)):
        err_cat = getattr(exc, "error_category", None)
        if err_cat == "CONTEXT_OVERFLOW":
            return (
                ChunkDiagnosticCategory.HTTP_CONTEXT_OVERFLOW.value,
                "inference",
                "Dimensione del prompt eccede la finestra di contesto del modello",
            )
        if err_cat == "SCHEMA_REJECTED":
            return (
                ChunkDiagnosticCategory.HTTP_SCHEMA_REJECTED.value,
                "inference",
                "Schema JSON rifiutato dal server locale LM Studio",
            )
        if err_cat == "INVALID_PARAMS" or http_status == 400 or getattr(exc, "status_code", None) == 400:
            return (
                ChunkDiagnosticCategory.HTTP_INVALID_PARAMETERS.value,
                "inference",
                "Parametri di richiesta HTTP non validi o rifiutati (HTTP 400)",
            )
        return (
            ChunkDiagnosticCategory.HTTP_INVALID_PARAMETERS.value,
            "inference",
            f"Errore nella richiesta HTTP verso LM Studio ({type(exc).__name__})",
        )

    if isinstance(exc, AiModelMismatchError):
        return (
            ChunkDiagnosticCategory.MODEL_MISMATCH.value,
            "inference",
            "Identificatore del modello restituito non corrispondente a quello richiesto",
        )

    if isinstance(exc, TopicProvenanceError):
        return (
            ChunkDiagnosticCategory.PROVENANCE_MISMATCH.value,
            "evidence_validation",
            "Discrepanza di provenienza documentale tra blocco e documento padre",
        )

    if type(exc).__name__ == "CheckpointSaveError":
        return (
            ChunkDiagnosticCategory.CHECKPOINT_SAVE_ERROR.value,
            "checkpoint_save",
            "Errore durante la persistenza del blocco nel database SQLite dei checkpoint",
        )

    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return (
            ChunkDiagnosticCategory.INTERNAL_VALIDATION_ERROR.value,
            "schema_validation",
            f"Errore di validazione interna ({type(exc).__name__})",
        )

    return (
        ChunkDiagnosticCategory.UNKNOWN_ERROR.value,
        "inference",
        f"Errore non classificato ({type(exc).__name__})",
    )


@dataclass(frozen=True)
class DiscoveredTopicRef:
    """
    Riferimento immutabile a un topic scoperto, vincolato al documento e alla run di provenienza.
    """
    document_id: str
    discovery_run_id: str
    topic_index: int
    topic: DiscoveredTopic

    @classmethod
    def from_discovery_result(
        cls,
        discovery_result: TopicDiscoveryResult,
        topic_index: int,
    ) -> DiscoveredTopicRef:
        """Costruisce un riferimento immutabile e verificato a partire da TopicDiscoveryResult."""
        if not isinstance(discovery_result, TopicDiscoveryResult):
            raise TypeError("discovery_result deve essere TopicDiscoveryResult")
        if topic_index < 0 or topic_index >= len(discovery_result.topics):
            raise IndexError(f"topic_index {topic_index} fuori range ({len(discovery_result.topics)} topics)")
        run_id = discovery_result.metadata.get("run_id") if isinstance(discovery_result.metadata, Mapping) else None
        if not run_id:
            raise TopicProvenanceError("discovery_result privo di run_id valido nei metadata")
        return cls(
            document_id=discovery_result.provenance_document_id,
            discovery_run_id=str(run_id),
            topic_index=topic_index,
            topic=discovery_result.topics[topic_index],
        )


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
