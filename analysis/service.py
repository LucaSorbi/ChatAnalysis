"""
analysis/service.py
-------------------
Servizio applicativo unificato per l'analisi tematica locale (Topic Detection e Topic Discovery).

Principi architetturali:
- UNICA FONTE DI ORCHESTRAZIONE AI: DEMO e FILE attraversano lo stesso identico service layer.
- CICLO DI VITA UNICO: Un solo client e una sola preparazione modello per operazione.
- CHUNKING INTEGRATO: Suddivisione trasparente di conversazioni grandi e aggregazione multi-chunk
  consapevole dei guasti (nessun troncamento silenzioso, nessun falso ABSENT su errore parziale,
  propagazione immediata degli errori sistemici, fallimento globale se zero chunk riusciti).
- ISOLAMENTO STREAMLIT: Modulo puro Python, indipendente da framework web e interamente testabile.
- PRIVACY: Soppressione della raw LLM response su dati reali determinata da data_classification autorevole.
- METADATA FORENSI COMPLETI: run_id, chunk_count (int), model provenance, ranking reduction strategy.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import time
import uuid
from typing import Any, Mapping, MutableMapping, MutableSet, Sequence

logger = logging.getLogger(__name__)

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiInvalidEvidenceCitationError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
    BaseLlmClient,
    BaseLocalLlmClient,
    LmStudioUnavailableError,
)
from ai.chunking import (
    ChunkAnalysisOutcome,
    aggregate_topic_detection,
    aggregate_topic_discovery,
    is_substantive_message_bundle,
    profile_document_for_analysis,
    split_chunk_into_microchunks,
    split_document_into_chunks,
)
from ai.json_failure import finish_reason_from_kind
from ai.structured import (
    DEGENERATION_POLICY_STRUCTURAL,
    TOPIC_DETECTION_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA,
)
from ai.token_budget import (
    DEFAULT_SAFETY_MARGIN_TOKENS,
    calculate_dynamic_output_budget,
    calculate_token_budget_plan,
    estimate_request_input_tokens,
    estimate_schema_tokens,
)
from analysis.progress import (
    AnalysisProgressCallback,
    AnalysisProgressEvent,
    AnalysisProgressTracker,
)
from analysis.export import get_git_commit
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    CheckpointSaveError,
    compute_document_fingerprint,
    compute_run_config_hash,
    deserialize_detection_result_from_checkpoint,
    deserialize_discovery_result_from_checkpoint,
    validate_checkpoint_evidence_references,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MODEL,
    DEFAULT_OPERATIONAL_TEMPERATURE,
    DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
    LmStudioClient,
    ResolvedOperationalModel,
    get_model_display_name,
    resolve_installed_model_id,
    resolve_operational_model_from_metadata,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.topics import (
    PROMPT_VERSION_DISCOVERY_OPERATIONAL,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisNoEvidenceError,
    AnalysisOrigin,
    AnalysisPlan,
    AttestationLevel,
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ChunkLifecycleStatus,
    ConsistencyResult,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    LoadingMode,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
    TopicAnalysisRunMetadata,
    TopicProvenanceError,
    classify_discovery_exception,
    resolve_operational_profile,
    safe_exception_text,
)

FATAL_BACKEND_ERRORS = (
    LmStudioUnavailableError,
    AiModelNotInstalledError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
    AiBackendProtocolError,
    AiBackendTimeoutError,
    AiBackendRequestError,
    AnalysisInputTooLargeError,
    AiStructuredOutputError,
)

SYSTEMIC_BACKEND_ERRORS = (
    LmStudioUnavailableError,
    AiModelNotInstalledError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
)

PROGRAMMING_ERRORS = (
    NameError,
    AttributeError,
    ImportError,
    SyntaxError,
    UnboundLocalError,
    TypeError,
    KeyError,
    IndexError,
    AssertionError,
)

# --- Topic Detection a microchunk: limiti espliciti e finiti (nessun loop infinito) ---
DETECTION_MICROCHUNK_MAX_SUBSTANTIVE = 8
DETECTION_MICROCHUNK_MAX_DEPTH = 3
DETECTION_MAX_CONSECUTIVE_TIMEOUTS = 3
# Budget finiti (revisione PR #1): il breaker sui timeout consecutivi non contiene i pattern misti
# (timeout alternati a errori non-timeout che lo azzerano). Limiti aggiuntivi, tutti con resume dai checkpoint:
DETECTION_MAX_TOTAL_TIMEOUTS = 10  # timeout complessivi nella run, poi interruzione (progresso validato conservato)
DETECTION_MICROCHUNK_TIME_BUDGET_SECONDS = 600.0  # oltre: niente ulteriori bisezioni né nodi nuovi per quel microchunk
NON_BISECTABLE_REQUEST_CATEGORIES = frozenset(
    {"HTTP_INVALID_PARAMETERS", "INVALID_PARAMETERS", "HTTP_SCHEMA_REJECTED", "UNSUPPORTED_SCHEMA"}
)


def new_detection_stats() -> dict[str, int]:
    """Contatori numerici (nessun testo) per la diagnostica della Detection a microchunk."""
    return {
        "detect_calls": 0,
        "retries": 0,
        "timeouts": 0,
        "consecutive_timeouts": 0,
        "bisections": 0,
        "completed": 0,
        "recovered": 0,
        "failed": 0,
        "checkpoint_writes_ok": 0,
        "checkpoint_write_failures": 0,
        # Osservabilità additiva (solo contatori): fallimenti di OGNI nodo (foglie e nodi poi bisecati),
        # fallimenti per degenerazione, tipo di innesco, attempts delle sole foglie.
        "node_failures": 0,
        "degenerate_failures": 0,
        "trigger_char_run": 0,
        "trigger_tab_run": 0,
        "trigger_pattern_loop": 0,
        "leaf_attempts": 0,
    }


RESUME_ORIGIN_CHECKPOINT = "CHECKPOINT_VALIDATED"
RESUME_ORIGIN_COMPUTED = "COMPUTED_THIS_RUN"
RESUME_ORIGIN_MIXED = "MIXED_CHECKPOINT_AND_COMPUTED"
_DEGENERATION_TRIGGER_KINDS = frozenset({"CHAR_RUN", "TAB_RUN", "PATTERN_LOOP"})
_TRIGGER_STAT_KEY = {
    "CHAR_RUN": "trigger_char_run",
    "TAB_RUN": "trigger_tab_run",
    "PATTERN_LOOP": "trigger_pattern_loop",
}


def _bump(stats: dict[str, int], key: str, by: int = 1) -> None:
    stats[key] = int(stats.get(key, 0)) + int(by)


def _opt_int(value: Any) -> int | None:
    """Intero oppure None (non disponibile): mai uno zero inventato; i booleani non sono numeri."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def detection_failure_metrics(exc: BaseException | None, bundle_count: int) -> dict[str, Any]:
    """
    Metriche NUMERICHE/ENUM di un nodo Detection fallito (nessun testo del modello né della conversazione).
    I valori non disponibili restano None. Non legge né modifica il messaggio dell'eccezione.
    """
    deg = getattr(exc, "degeneration", None)
    deg = deg if isinstance(deg, dict) else {}
    kind = deg.get("trigger_kind")
    fr = getattr(exc, "finish_reason", None)
    return {
        "bundle_count": int(bundle_count),
        "trigger_kind": kind if kind in _DEGENERATION_TRIGGER_KINDS else None,
        "pattern_length": _opt_int(deg.get("pattern_length")),
        "repetition_count": _opt_int(deg.get("repetition_count")),
        "run_length": _opt_int(deg.get("run_length")),
        "response_length": _opt_int(deg.get("response_length")),
        "finish_reason": fr if isinstance(fr, str) else None,
        # cumulativo sui tentativi del nodo (come i token dei nodi riusciti)
        "completion_tokens_cumulative": _opt_int(getattr(exc, "completion_tokens", None)),
    }


def note_detection_node_failure(stats: dict[str, int], category: str, metrics: Mapping[str, Any]) -> None:
    """Contatori di un nodo Detection fallito (foglia o nodo poi bisecato): solo numeri."""
    _bump(stats, "node_failures")
    if category == ChunkDiagnosticCategory.DEGENERATE_OUTPUT.value:
        _bump(stats, "degenerate_failures")
        key = _TRIGGER_STAT_KEY.get(str(metrics.get("trigger_kind") or ""))
        if key:
            _bump(stats, key)


def discovery_citation_metrics(exc: BaseException | None, bundle_count: int) -> dict[str, Any] | None:
    """
    Metriche NUMERICHE di una citazione rifiutata in Topic Discovery: solo interi ed etichette di forma (enum chiuso).
    None se l'errore non è una citazione invalida o il riepilogo non è disponibile (non si falsifica 0).
    """
    summary = getattr(exc, "citation_summary", None)
    if not isinstance(exc, AiInvalidEvidenceCitationError) or not isinstance(summary, Mapping):
        return None
    return {
        "bundle_count": int(bundle_count),
        "citations_total_count": _opt_int(summary.get("citations_total_count")),
        "citations_invalid_count": _opt_int(summary.get("citations_invalid_count")),
        "invalid_citation_shapes": dict(summary.get("invalid_citation_shapes") or {}),
        "aliases_presented_count": _opt_int(getattr(exc, "aliases_presented_count", None)),
        "aliases_accepted_count": _opt_int(getattr(exc, "aliases_accepted_count", None)),
    }


def _merge_diag_metrics(*parts: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Unione di dizionari di metriche numeriche (None se tutti assenti). Le chiavi non si sovrappongono."""
    merged: dict[str, Any] = {}
    for part in parts:
        if isinstance(part, Mapping):
            merged.update(part)
    return merged or None


def discovery_failure_metrics(
    exc: BaseException | None,
    *,
    bisection_calls: int | None = 0,
    bisection_depth_max: int = 0,
    discarded_success_evidence_count: int = 0,
) -> dict[str, Any] | None:
    """
    Telemetria NUMERICA additiva di un blocco Discovery fallito (o riuscito dopo bisezione): chiamate HTTP effettive,
    profondità di bisezione, evidenze validate ma non accreditate, dettaglio per tentativo (enum chiusi/interi).
    Nessun testo, nessun hash, nessuna finestra di risposta. `telemetry_provenance`: MEASURED solo se l'eccezione porta
    la telemetria dei tentativi; altrimenti NOT_AVAILABLE e i conteggi di chiamate restano None (mai 0 inventato).
    """
    tel = getattr(exc, "attempt_telemetry", None)
    own = getattr(exc, "llm_calls", None)
    measured = isinstance(tel, tuple) and isinstance(own, int) and not isinstance(own, bool)
    total: int | None = None
    if measured and bisection_calls is not None:
        total = int(own) + int(bisection_calls)
    return {
        "telemetry_provenance": "MEASURED" if measured else "NOT_AVAILABLE",
        "llm_calls_own": int(own) if measured else None,
        "llm_calls_bisection": int(bisection_calls) if (measured and bisection_calls is not None) else None,
        "llm_calls_total": total,
        "bisection_depth_max": int(bisection_depth_max),
        "discarded_success_evidence_count": int(discarded_success_evidence_count),
        "attempts_detail": [dict(a) for a in tel] if measured else [],
    }


def _outcome_discarded_evidence(outcome: Any) -> int:
    """Evidenze validate da chiamate riuscite dentro un nodo fallito, ma non accreditate (0 se non disponibile)."""
    dm = getattr(getattr(outcome, "diagnostic", None), "diagnostic_metrics", None)
    val = dm.get("discarded_success_evidence_count") if isinstance(dm, Mapping) else None
    return int(val) if isinstance(val, int) and not isinstance(val, bool) else 0


def _outcome_llm_calls(outcome: Any) -> tuple[int | None, int]:
    """(chiamate HTTP, profondità di bisezione) di un esito di microchunk; chiamate None se non misurabili."""
    diag = getattr(outcome, "diagnostic", None)
    dm = getattr(diag, "diagnostic_metrics", None)
    if isinstance(dm, Mapping) and "llm_calls_total" in dm:
        total = dm.get("llm_calls_total")
        return (int(total) if isinstance(total, int) else None), int(dm.get("bisection_depth_max") or 0)
    att = getattr(outcome, "attempts", None)
    return (int(att) if isinstance(att, int) else None), 0


class _DetectionSplitPartial(Exception):
    """Controllo di flusso interno: split Detection concluso con copertura incompleta."""


def calculate_discovery_max_tokens(
    max_topics: int,
    user_max_tokens: int | None = None,
    context_length: int = 8192,
    estimated_prompt_tokens: int | None = None,
    safety_margin: int = DEFAULT_SAFETY_MARGIN_TOKENS,
) -> int:
    """
    Calcola un budget dinamico di token di completamento per Topic Discovery operativa,
    proporzionato al numero di topic richiesti e vincolato all'occupazione effettiva del prompt
    e alla context window del modello.
    Garantisce tassativamente di non superare mai l'headroom disponibile.
    """
    needed = max_topics * 120 + 256
    if estimated_prompt_tokens is not None:
        available_headroom = max(0, context_length - estimated_prompt_tokens - safety_margin)
        if available_headroom <= 0:
            return 0
        if user_max_tokens is not None and user_max_tokens > 256:
            return min(user_max_tokens, available_headroom)
        safe_target = min(needed, available_headroom)
        return min(available_headroom, max(min(1024, available_headroom), safe_target))
    else:
        max_safe = max(1024, context_length - 4096)
        if user_max_tokens is not None and user_max_tokens > 512:
            return min(user_max_tokens, max_safe)
        return max(1024, min(needed, max_safe))


class LocalTopicAnalysisService:
    """
    Servizio unificato per l'esecuzione di Topic Detection e Topic Discovery
    mediante modelli linguistici locali (LM Studio).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        config: OperationalAnalysisConfig | None = None,
        default_model: str | None = None,
        context_length: int | None = None,
        timeout_seconds: float | None = None,
        max_chunk_characters: int | None = None,
        max_chunk_bundles: int | None = None,
        max_chunk_tokens: int | None = None,
        profile: OperationalProfile | str | None = None,
        client: BaseLlmClient | None = None,
        discovery_run_store: MutableMapping[str, dict[str, TopicDiscoveryResult]] | None = None,
        authoritative_live_run_ids: MutableSet[str] | None = None,
        checkpoint_store: AnalysisCheckpointStore | None = None,
        enable_checkpointing: bool = False,
        checkpoint_dir: str | Path | None = None,
    ) -> None:
        if (checkpoint_dir is not None or enable_checkpointing) and checkpoint_store is None:
            checkpoint_store = AnalysisCheckpointStore(checkpoint_dir=checkpoint_dir)
        cfg = config or OperationalAnalysisConfig()
        if profile is not None:
            cfg = cfg.with_profile(profile)
        if (
            context_length is not None
            or timeout_seconds is not None
            or max_chunk_characters is not None
            or max_chunk_bundles is not None
            or max_chunk_tokens is not None
            or default_model is not None
        ):
            cfg = OperationalAnalysisConfig(
                model_spec=default_model or cfg.model_spec,
                context_length=context_length or cfg.context_length,
                timeout_seconds=timeout_seconds or cfg.timeout_seconds,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                seed=cfg.seed,
                max_topics=cfg.max_topics,
                profile=cfg.profile,
                chunk_character_budget=max_chunk_characters or cfg.chunk_character_budget,
                max_chunk_bundles=max_chunk_bundles if max_chunk_bundles is not None else cfg.max_chunk_bundles,
                max_chunk_tokens=max_chunk_tokens if max_chunk_tokens is not None else cfg.max_chunk_tokens,
            )
        self.config = cfg
        self.profile = cfg.profile
        self.base_url = base_url
        self.default_model = cfg.model_spec
        self.context_length = cfg.context_length
        self.timeout_seconds = cfg.timeout_seconds
        self.max_chunk_characters = cfg.chunk_character_budget
        self.max_chunk_bundles = cfg.max_chunk_bundles
        self.max_chunk_tokens = cfg.max_chunk_tokens
        self.default_max_tokens = cfg.max_tokens
        self._injected_client = client
        # Registro document-scoped delle discovery run per validazione formale di provenance
        self._discovery_runs: MutableMapping[str, dict[str, TopicDiscoveryResult]] = (
            discovery_run_store if discovery_run_store is not None else {}
        )
        # Insieme dei run_id autoritativi generati internamente da run_topic_discovery()
        self._authoritative_live_run_ids: MutableSet[str] = (
            authoritative_live_run_ids if authoritative_live_run_ids is not None else set()
        )
        # Gestore di checkpoint e ripresa: attivo se checkpoint_store è fornito o enable_checkpointing è True
        self.checkpoint_store = checkpoint_store
        self.enable_checkpointing = enable_checkpointing or (checkpoint_store is not None)
        # Cache per piani di analisi invariati a parità di documento e parametri
        self._analysis_plan_cache: dict[str, AnalysisPlan] = {}

    def _calculate_effective_chunk_token_budget(
        self,
        context_length: int,
        reserved_output_tokens: int,
        schema: Mapping[str, Any] | None = None,
        system_prompt_overhead: int = 400,
        safety_margin: int = DEFAULT_SAFETY_MARGIN_TOKENS,
        raise_on_insufficient: bool = False,
    ) -> int:
        """
        Calcola dinamicamente il budget prudenziale di token per singolo chunk documentale,
        considerando l'intero prompt (system + frame), lo schema JSON, il budget di output
        e il safety margin rispetto alla context window reale del modello.
        Non dichiara MAI disponibile uno spazio che non esiste (headroom reale senza clamp arbitrario).
        """
        schema_tokens = estimate_schema_tokens(schema) if schema else 128
        total_overhead = reserved_output_tokens + safety_margin + system_prompt_overhead + schema_tokens + 32
        available_headroom = max(0, context_length - total_overhead)
        if available_headroom < 256:
            if raise_on_insufficient or available_headroom <= 0:
                raise AnalysisInputTooLargeError(
                    f"Spazio di contesto disponibile ({available_headroom} token) insufficiente "
                    f"per un blocco documentale rispetto a context_length={context_length} "
                    f"e total_overhead={total_overhead} token."
                )
            return available_headroom
        configured = self.max_chunk_tokens if self.max_chunk_tokens is not None else 2500
        return min(configured, available_headroom)

    def create_analysis_plan(
        self,
        document: ConversationEvidenceDocument,
        analysis_type: str = "detection",
        max_topics: int | None = None,
        user_max_tokens: int | None = None,
        context_length: int | None = None,
        profile: OperationalProfile | str | None = None,
        topic_query: TopicQuery | None = None,
    ) -> AnalysisPlan:
        """
        Unica funzione autoritativa di pianificazione delle analisi (Topic Detection e Discovery).
        Garantisce che la profilazione preventiva della UI e l'esecuzione effettiva del servizio
        utilizzino esattamente lo stesso piano immutabile di chunking e budget di token.
        Per Discovery, calcola la riserva di output proporzionata a max_topics (3, 10, 25 topic).
        """
        ctx_len = context_length or self.context_length
        eff_profile = profile or self.profile
        cfg = self.config.with_profile(eff_profile) if profile else self.config

        schema = TOPIC_DETECTION_SCHEMA if analysis_type == "detection" else TOPIC_DISCOVERY_SCHEMA
        eff_max_topics = max_topics if max_topics is not None else cfg.max_topics

        doc_fp = compute_document_fingerprint(document)
        cache_key = (
            f"{document.document_id}::{doc_fp}::{analysis_type}::{cfg.profile.value}::"
            f"{eff_max_topics}::{ctx_len}::{user_max_tokens}::{cfg.chunk_character_budget}::"
            f"{cfg.max_chunk_bundles}::{cfg.max_chunk_tokens}"
        )
        if cache_key in self._analysis_plan_cache:
            return self._analysis_plan_cache[cache_key]

        if analysis_type == "discovery":
            out_tokens = calculate_discovery_max_tokens(
                max_topics=eff_max_topics,
                user_max_tokens=user_max_tokens,
                context_length=ctx_len,
            )
        else:
            out_tokens = user_max_tokens or cfg.max_tokens

        eff_tokens = self._calculate_effective_chunk_token_budget(
            context_length=ctx_len,
            reserved_output_tokens=out_tokens,
            schema=schema,
        )

        chunks = split_document_into_chunks(
            document=document,
            max_characters=cfg.chunk_character_budget,
            max_bundles=cfg.max_chunk_bundles,
            max_tokens=eff_tokens,
        )

        schema_tokens = estimate_schema_tokens(schema) if schema else 128
        safety_margin = DEFAULT_SAFETY_MARGIN_TOKENS
        overhead = out_tokens + safety_margin + 400 + schema_tokens + 32
        headroom = max(0, ctx_len - overhead)

        plan_hash_payload = {
            "doc_id": document.document_id,
            "type": analysis_type,
            "chunks_count": len(chunks),
            "chunk_tokens": eff_tokens,
            "out_tokens": out_tokens,
            "ctx_len": ctx_len,
            "profile": cfg.profile.value if hasattr(cfg.profile, "value") else str(cfg.profile),
            "max_topics": eff_max_topics if analysis_type == "discovery" else None,
        }
        plan_hash = hashlib.sha256(json.dumps(plan_hash_payload, sort_keys=True).encode("utf-8")).hexdigest()

        plan = AnalysisPlan(
            document_id=document.document_id,
            analysis_type=analysis_type,
            planned_chunks_count=len(chunks),
            chunk_token_budget=eff_tokens,
            chunk_character_budget=cfg.chunk_character_budget,
            input_token_budget=eff_tokens,
            output_token_budget=out_tokens,
            schema=schema,
            safety_margin=safety_margin,
            headroom_tokens=headroom,
            profile=cfg.profile,
            max_topics=eff_max_topics if analysis_type == "discovery" else None,
            effective_config=cfg,
            chunks=tuple(chunks),
            plan_hash=plan_hash,
            requires_dynamic_splitting=False,
        )
        self._analysis_plan_cache[cache_key] = plan
        return plan

    def profile_document(
        self,
        document: ConversationEvidenceDocument,
        analysis_type: str = "detection",
        reserved_output_tokens: int | None = None,
        context_length: int | None = None,
        max_topics: int | None = None,
        profile: OperationalProfile | str | None = None,
    ) -> DocumentAnalysisProfile:
        """
        Esegue la profilazione preventiva del documento basandosi sul piano immutabile unificato.
        Risolve qualsiasi discrepanza di budget o chunking tra anteprima ed esecuzione reale.
        """
        plan = self.create_analysis_plan(
            document=document,
            analysis_type=analysis_type,
            max_topics=max_topics,
            user_max_tokens=reserved_output_tokens,
            context_length=context_length,
            profile=profile,
        )
        return profile_document_for_analysis(
            document=document,
            max_characters=plan.chunk_character_budget,
            max_bundles=plan.effective_config.max_chunk_bundles,
            max_tokens=plan.chunk_token_budget,
        )

    def _are_discovery_results_identical(
        self, existing: TopicDiscoveryResult, incoming: TopicDiscoveryResult
    ) -> bool:
        """
        Verifica l'identità integrale di due TopicDiscoveryResult per consentire
        idempotenza stretta evitando qualsiasi mutazione o alterazione dello store.
        Effettua una comparazione strutturale completa del risultato dataclass:
        - provenance_document_id
        - topics completi e loro ordine (label, short_description, evidence_ids)
        - metadata integrali (tutte le chiavi e valori: run_id, origin, model,
          status, timestamp, context_length, latency, chunk count, parametri
          ed eventuali metadata aggiuntivi o annidati).
        Nessun elenco parziale di chiavi viene utilizzato.
        """
        if existing is incoming:
            return True
        if not isinstance(existing, TopicDiscoveryResult) or not isinstance(incoming, TopicDiscoveryResult):
            return False

        return existing == incoming

    def _record_authoritative_discovery_run(
        self, document_id: str, discovery_result: TopicDiscoveryResult
    ) -> str:
        """
        Registrazione autoritativa interna per run prodotte da questo service.
        Politica append-only e immutabile:
        Una volta registrata una run per (document_id, run_id):
        - Se la nuova run coincide integralmente con quella registrata: no-op idempotente.
        - Se i contenuti differiscono: solleva TopicProvenanceError senza alterare lo store.
        """
        eff_run_id = str(discovery_result.metadata.get("run_id", "")).strip()
        if not eff_run_id:
            raise TopicProvenanceError("Impossibile registrare la run: run_id mancante o vuoto.")

        if document_id not in self._discovery_runs:
            self._discovery_runs[document_id] = {}

        existing = self._discovery_runs[document_id].get(eff_run_id)
        if existing is not None:
            if not self._are_discovery_results_identical(existing, discovery_result):
                raise TopicProvenanceError(
                    f"Immutabilità violata: la run '{eff_run_id}' per il documento '{document_id}' "
                    "è già registrata con contenuti differenti e non può essere sovrascritta o modificata."
                )
            return eff_run_id

        self._discovery_runs[document_id][eff_run_id] = discovery_result
        return eff_run_id

    def register_discovery_run(self, document_id: str, discovery_result: TopicDiscoveryResult) -> str:
        """
        Interfaccia di registrazione / validazione per discovery run.
        Rifiuta categoricamente run sintetiche, mock non autorizzate o run live non verificate.
        Non consente la sostituzione o l'alterazione di run già registrate (store append-only).
        """
        if not isinstance(document_id, str) or not document_id.strip():
            raise TopicProvenanceError("document_id deve essere una stringa non vuota per registrare una discovery run.")
        if not isinstance(discovery_result, TopicDiscoveryResult):
            raise TypeError(f"discovery_result deve essere TopicDiscoveryResult, ottenuto: {type(discovery_result)}")
        if discovery_result.provenance_document_id != document_id:
            raise TopicProvenanceError(
                f"Discrepanza di provenienza: discovery_result.provenance_document_id "
                f"'{discovery_result.provenance_document_id}' != document_id '{document_id}'."
            )
        run_id = discovery_result.metadata.get("run_id") if isinstance(discovery_result.metadata, Mapping) else None
        if not run_id or not str(run_id).strip():
            raise TopicProvenanceError("discovery_result privo di run_id valido nei metadata.")
        eff_run_id = str(run_id).strip()

        # Politica FAIL-CLOSED sull'origine
        run_origin = discovery_result.metadata.get("origin") if isinstance(discovery_result.metadata, Mapping) else None
        if run_origin is None or not str(run_origin).strip():
            raise TopicProvenanceError(
                f"Impossibile registrare la run '{eff_run_id}': origin mancante nei metadata (politica fail-closed)."
            )

        origin_str = run_origin.value if isinstance(run_origin, AnalysisOrigin) else str(run_origin).strip()
        valid_origins = {o.value for o in AnalysisOrigin}

        # Origin non riconosciuta -> respinta categoricamente
        if origin_str not in valid_origins:
            raise TopicProvenanceError(
                f"Impossibile registrare la run '{eff_run_id}': origin '{run_origin}' non riconosciuta o non valida (politica fail-closed)."
            )

        # SYNTHETIC_REFERENCE -> respinta categoricamente dal registro live
        if origin_str == AnalysisOrigin.SYNTHETIC_REFERENCE.value:
            raise TopicProvenanceError(
                f"Impossibile registrare la run '{eff_run_id}': l'origine è SYNTHETIC_REFERENCE. "
                "Le reference sintetiche dimostrative non possono essere registrate nel registro autorevole delle Discovery live."
            )

        is_recovered = bool(
            discovery_result.metadata.get("is_persisted_recovery")
            or discovery_result.metadata.get("loading_mode") == LoadingMode.CHECKPOINT_RESTORE.value
            or discovery_result.metadata.get("recovery_mode") == "CHECKPOINT_RESTORE"
        )

        if not is_recovered:
            # MOCK_INSPECTION sul percorso di registrazione live -> respinta: deve usare il test seam esplicito
            if origin_str == AnalysisOrigin.MOCK_INSPECTION.value:
                raise TopicProvenanceError(
                    f"Impossibile registrare la run '{eff_run_id}' come live: l'origine è MOCK_INSPECTION. "
                    "Le run mock devono essere registrate esclusivamente tramite il test seam esplicito register_mock_discovery_run()."
                )

            # Protezione anti-falsificazione: una run con origin LIVE_LM_STUDIO NON può essere iniettata arbitrariamente dall'esterno
            # senza essere stata effettivamente generata dall'esecuzione operativa run_topic_discovery()
            if origin_str == AnalysisOrigin.LIVE_LM_STUDIO.value and eff_run_id not in self._authoritative_live_run_ids:
                raise TopicProvenanceError(
                    f"Tentativo di registrazione arbitraria di una run live non certificata ('{eff_run_id}'). "
                    "Le run con origine LIVE_LM_STUDIO possono essere registrate esclusivamente dal percorso autoritativo run_topic_discovery()."
                )
        else:
            # Gestione autenticata di una run recuperata: verifica rigorosa contro SQLite
            if not self.enable_checkpointing or self.checkpoint_store is None:
                raise TopicProvenanceError(
                    f"Impossibile registrare la run recuperata '{eff_run_id}': checkpointing disabilitato o store non disponibile (politica fail-closed)."
                )
            cfg_fp = discovery_result.metadata.get("config_fingerprint")
            doc_fp = discovery_result.metadata.get("document_fingerprint")
            if not cfg_fp or not doc_fp:
                raise TopicProvenanceError(
                    f"Impossibile registrare la run recuperata '{eff_run_id}': metadati di fingerprint incompleti o assenti (politica fail-closed)."
                )
            chk_data = self.checkpoint_store.load_checkpoint(str(doc_fp).strip(), str(cfg_fp).strip())
            if chk_data is None or chk_data.document_id != document_id or chk_data.analysis_type != "discovery":
                raise TopicProvenanceError(
                    f"Tentativo di registrazione arbitraria di una run recuperata non autenticata ('{eff_run_id}') "
                    f"per il documento '{document_id}' (politica fail-closed)."
                )
            if not chk_data.completed_chunk_indices and not chk_data.serialized_results and not chk_data.subchunks:
                raise TopicProvenanceError(
                    f"Tentativo di registrazione di run recuperata '{eff_run_id}' priva di chunk validati."
                )
            chk_origin = getattr(chk_data, "origin", None) or ""
            if chk_origin and chk_origin in {o.value for o in AnalysisOrigin} and str(chk_origin).strip() != origin_str:
                raise TopicProvenanceError(
                    f"Discrepanza di origine per la run recuperata '{eff_run_id}': metadata='{origin_str}' != checkpoint='{chk_origin}'."
                )

        # Se la run live è già registrata, verifica che la registrazione esterna non tenti di sovrascriverla o alterarla
        existing = self._discovery_runs.get(document_id, {}).get(eff_run_id)
        if existing is not None:
            if not self._are_discovery_results_identical(existing, discovery_result):
                raise TopicProvenanceError(
                    f"Immutabilità violata: la run '{eff_run_id}' per il documento '{document_id}' "
                    "è già registrata e non può essere sovrascritta o sostituita da una registrazione esterna."
                )
            return eff_run_id

        # Se la run dichiara LIVE_LM_STUDIO ma non è presente nello store locale per questo documento,
        # rifiuta l'iniezione esterna: solo run_topic_discovery() registra run autoritative.
        raise TopicProvenanceError(
            f"Tentativo di registrazione arbitraria di una run live ('{eff_run_id}') non originata internamente per il documento '{document_id}'."
        )

    def register_mock_discovery_run(self, document_id: str, discovery_result: TopicDiscoveryResult) -> str:
        """
        Interfaccia esplicita per i test: inietta controllatamente un risultato di Topic Discovery marcato come MOCK_INSPECTION.
        Protegge l'immutabilità dello store e l'isolamento:
        - Non può sovrascrivere una run live autoritativa esistente.
        - Non può utilizzare un run_id appartenente all'insieme delle run live autoritative.
        - Non può sovrascrivere una run mock esistente con contenuti differenti.
        - Idempotente se il risultato coincide integralmente.
        """
        if not isinstance(document_id, str) or not document_id.strip():
            raise TopicProvenanceError("document_id deve essere una stringa non vuota per registrare una mock discovery run.")
        if not isinstance(discovery_result, TopicDiscoveryResult):
            raise TypeError(f"discovery_result deve essere TopicDiscoveryResult, ottenuto: {type(discovery_result)}")
        if discovery_result.provenance_document_id != document_id:
            raise TopicProvenanceError(
                f"Discrepanza di provenienza: discovery_result.provenance_document_id "
                f"'{discovery_result.provenance_document_id}' != document_id '{document_id}'."
            )
        run_id = discovery_result.metadata.get("run_id") if isinstance(discovery_result.metadata, Mapping) else None
        if not run_id or not str(run_id).strip():
            raise TopicProvenanceError("discovery_result privo di run_id valido nei metadata.")
        eff_run_id = str(run_id).strip()

        # Non può utilizzare un run_id appartenente all'insieme delle run live autoritative
        if eff_run_id in self._authoritative_live_run_ids:
            raise TopicProvenanceError(
                f"Impossibile registrare la run mock '{eff_run_id}': l'identificativo appartiene a una run live autoritativa."
            )

        existing = self._discovery_runs.get(document_id, {}).get(eff_run_id)
        if existing is not None:
            existing_origin = existing.metadata.get("origin") if isinstance(existing.metadata, Mapping) else None
            existing_origin_str = existing_origin.value if isinstance(existing_origin, AnalysisOrigin) else str(existing_origin or "").strip()
            if existing_origin_str == AnalysisOrigin.LIVE_LM_STUDIO.value:
                raise TopicProvenanceError(
                    f"Impossibile sovrascrivere la run live autoritativa '{eff_run_id}' con una run mock."
                )

        meta = dict(discovery_result.metadata) if isinstance(discovery_result.metadata, Mapping) else {}
        meta["origin"] = AnalysisOrigin.MOCK_INSPECTION.value
        mock_res = TopicDiscoveryResult(
            topics=discovery_result.topics,
            provenance_document_id=document_id,
            metadata=meta,
        )

        if existing is not None:
            if not self._are_discovery_results_identical(existing, mock_res):
                raise TopicProvenanceError(
                    f"Immutabilità violata: la run mock '{eff_run_id}' per il documento '{document_id}' "
                    "è già registrata con contenuti differenti e non può essere sovrascritta."
                )
            return eff_run_id

        return self._record_authoritative_discovery_run(document_id, mock_res)

    def get_discovery_run(self, document_id: str, run_id: str) -> TopicDiscoveryResult | None:
        """Restituisce il risultato di una discovery run registrata per il documento o None."""
        return self._discovery_runs.get(document_id, {}).get(str(run_id).strip())

    def _prepare_client_and_model(
        self,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
    ) -> tuple[BaseLlmClient, str]:
        """
        Prepara in un singolo passaggio autoritativo il client e il modello operativo.
        """
        active_client = client or self._injected_client
        req_model = (
            (model_name or "").strip()
            or getattr(active_client, "model_id", None)
            or self.default_model
        )
        target_client = active_client or LmStudioClient(base_url=self.base_url, model_id=req_model)

        if hasattr(target_client, "is_available") and not target_client.is_available():
            raise LmStudioUnavailableError(
                f"LM Studio non raggiungibile su {self.base_url}. Verificare che il server locale sia attivo."
            )

        # Se il client implementa ensure_model_loaded, effettua pre-flight / load
        if hasattr(target_client, "ensure_model_loaded"):
            resolved = target_client.ensure_model_loaded(
                model_id=req_model,
                context_length=self.context_length,
                timeout_seconds=self.timeout_seconds,
            )
            return target_client, resolved

        # Per client generici o mock che implementano get_native_models o list_models
        if hasattr(target_client, "get_native_models"):
            native_models = target_client.get_native_models()
            res_meta = resolve_operational_model_from_metadata(native_models, req_model)
            if hasattr(target_client, "load_model") and hasattr(target_client, "is_model_loaded"):
                if not target_client.is_model_loaded(res_meta.model_key):
                    target_client.load_model(
                        res_meta.model_key,
                        context_length=self.context_length,
                        timeout_seconds=self.timeout_seconds,
                    )
            return target_client, res_meta.model_key

        if hasattr(target_client, "list_models"):
            models = target_client.list_models()
            resolved = resolve_installed_model_id(req_model, models)
            if not resolved:
                display = get_model_display_name(req_model)
                raise AiModelNotInstalledError(f"Il modello {display} non è installato in LM Studio.")
            if hasattr(target_client, "load_model") and hasattr(target_client, "is_model_loaded"):
                if not target_client.is_model_loaded(resolved):
                    target_client.load_model(
                        resolved,
                        context_length=self.context_length,
                        timeout_seconds=self.timeout_seconds,
                    )
            return target_client, resolved

        # Per client generici o mock con load_model
        if hasattr(target_client, "load_model") and hasattr(target_client, "is_model_loaded"):
            if not target_client.is_model_loaded(req_model):
                target_client.load_model(
                    req_model,
                    context_length=self.context_length,
                    timeout_seconds=self.timeout_seconds,
                )

        if not req_model:
            raise AiModelNotSpecifiedError("Nessun model_id specificato.")

        return target_client, req_model

    def _is_synthetic_document(self, document: ConversationEvidenceDocument) -> bool:
        """
        Determina se il documento contiene dati sintetici autorizzati per il debug.
        Verifica unicamente flag autorevoli di classificazione, senza euristiche stringa su document_id.
        """
        if document.metadata and document.metadata.get("data_classification") == "SYNTHETIC_DEMO":
            return True
        return False

    # ------------------------------------------------------------------
    # TOPIC DETECTION A MICROCHUNK (pre-splitting, checkpoint, bisezione limitata)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_live_lms_client(client: Any) -> bool:
        return isinstance(client, LmStudioClient) or isinstance(getattr(client, "inner", None), LmStudioClient)

    @staticmethod
    def _halve_detection_document(
        doc: ConversationEvidenceDocument, suffix_a: str, suffix_b: str
    ) -> tuple[ConversationEvidenceDocument, ConversationEvidenceDocument]:
        """Bisezione deterministica dei bundle: nessuna evidenza persa o duplicata."""
        mid = len(doc.bundles) // 2
        return (
            ConversationEvidenceDocument(
                document_id=f"{doc.document_id}{suffix_a}",
                bundles=doc.bundles[:mid],
                chat_id=doc.chat_id,
                source_name=doc.source_name,
            ),
            ConversationEvidenceDocument(
                document_id=f"{doc.document_id}{suffix_b}",
                bundles=doc.bundles[mid:],
                chat_id=doc.chat_id,
                source_name=doc.source_name,
            ),
        )

    def _should_presplit_detection(
        self,
        chunk: ConversationEvidenceDocument,
        operational_mode: bool,
        chk_store: Any,
        doc_fp: str,
        cfg_fp: str,
        parent_chunk_index: int,
    ) -> bool:
        """
        Stessa regola di innesco della Discovery: pre-split deterministico se il parent supera
        DETECTION_MICROCHUNK_MAX_SUBSTANTIVE messaggi sostanziali, oppure se esistono già sotto-blocchi
        persistiti per questo parent (ripresa). Solo in modalità operativa (i percorsi di benchmark
        congelati restano invariati).
        """
        if not operational_mode or len(chunk.bundles) <= 1:
            return False
        subst = sum(1 for b in chunk.bundles if is_substantive_message_bundle(b))
        if subst > DETECTION_MICROCHUNK_MAX_SUBSTANTIVE:
            return True
        if chk_store is not None and doc_fp and cfg_fp:
            try:
                return bool(chk_store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=parent_chunk_index))
            except Exception:
                return False
        return False

    def _plan_detection_subchunks(
        self, chunk: ConversationEvidenceDocument, operational_mode: bool
    ) -> list[ConversationEvidenceDocument]:
        """Piano deterministico dei sotto-blocchi (microchunk, o due metà come ripiego)."""
        if operational_mode:
            subs = list(split_chunk_into_microchunks(chunk, max_substantive_messages=DETECTION_MICROCHUNK_MAX_SUBSTANTIVE))
            if len(subs) > 1 or len(chunk.bundles) <= 1:
                return subs
        if len(chunk.bundles) <= 1:
            return [chunk]
        return list(self._halve_detection_document(chunk, "::sub::0", "::sub::1"))

    @staticmethod
    def _recover_detection_subchunk(
        chk_store: Any,
        doc_fp: str,
        cfg_fp: str,
        parent_chunk_index: int,
        subchunk_index: int,
        sub: ConversationEvidenceDocument,
        parent_chunk: ConversationEvidenceDocument,
        parent_document_id: str,
        topic: TopicQuery,
        origin: str,
    ) -> TopicDetectionResult | None:
        """
        Recupera un microchunk Detection già validato. Fail-closed: qualunque incoerenza
        (id, insieme esatto di evidenze, topic, provenienza, origine, verdetto) restituisce None
        e il microchunk viene re-inferito; nulla viene mai cancellato o migrato.
        """
        if chk_store is None or not doc_fp or not cfg_fp:
            return None
        try:
            cached = chk_store.get_subchunk(
                document_fingerprint=doc_fp,
                config_fingerprint=cfg_fp,
                parent_chunk_index=parent_chunk_index,
                subchunk_index=subchunk_index,
            )
            if cached is None:
                return None
            sub_eids = {s.evidence_id for s in sub.all_evidence_sections}
            if cached.get("subchunk_id") != sub.document_id:
                return None
            if set(cached.get("evidence_ids", [])) != sub_eids:
                return None
            res = deserialize_detection_result_from_checkpoint(cached["serialized_result"])
            if res.provenance_document_id not in (parent_document_id, parent_chunk.document_id, sub.document_id):
                return None
            if res.topic.topic_id != topic.topic_id or res.topic.label != topic.label:
                return None
            if not set(res.evidence_ids).issubset(sub_eids):
                return None
            if res.decision == TopicDecision.PRESENT and not res.evidence_ids:
                return None
            if res.decision == TopicDecision.ABSENT and res.evidence_ids:
                return None
            if origin and res.metadata.get("origin") != origin:
                return None
            return res
        except Exception as rec_err:
            logger.warning("Rivalidazione microchunk Detection da checkpoint non riuscita (%s).", type(rec_err).__name__)
            return None

    @staticmethod
    def _merge_detection_halves(
        topic: TopicQuery,
        sub: ConversationEvidenceDocument,
        res_a: TopicDetectionResult,
        res_b: TopicDetectionResult,
    ) -> TopicDetectionResult:
        """Consolida due metà ENTRAMBE riuscite con le regole forensi di aggregazione (PRESENT/ABSENT/UNCERTAIN)."""
        agg = aggregate_topic_detection(
            outcomes=[res_a, res_b],
            topic=topic,
            parent_document_id=sub.document_id,
            expected_chunk_count=2,
        )
        meta = dict(agg.metadata)
        meta["bisected"] = True
        meta["attempts"] = int(res_a.metadata.get("attempts", 1) or 1) + int(res_b.metadata.get("attempts", 1) or 1)
        for key in ("prompt_version", "model", "strategy", "operational_mode"):
            if res_a.metadata.get(key) is not None:
                meta[key] = res_a.metadata.get(key)
        return TopicDetectionResult(
            topic=agg.topic,
            decision=agg.decision,
            evidence_ids=agg.evidence_ids,
            rationale=agg.rationale,
            provenance_document_id=sub.document_id,
            metadata=meta,
        )

    def _execute_detection_microchunk(
        self,
        sub: ConversationEvidenceDocument,
        analyzer: TopicDetectionAnalyzer,
        topic: TopicQuery,
        strategy: AnalysisLanguageStrategy,
        resolved_model: str,
        eff_temp: float,
        eff_seed: int | None,
        eff_max_tokens: int,
        operational_mode: bool,
        tracker: AnalysisProgressTracker,
        stats: dict[str, int],
        parent_idx: int,
        depth: int = 0,
        deadline: float | None = None,
    ) -> tuple[list[ChunkAnalysisOutcome], TopicDetectionResult | None, list[ChunkDiagnosticRecord]]:
        """
        Esegue UN microchunk con bisezione adattiva LIMITATA (profondità massima
        DETECTION_MICROCHUNK_MAX_DEPTH, solo su errori recuperabili, mai su timeout) e circuit breaker
        sui timeout consecutivi. Restituisce (outcomes, risultato consolidato, diagnostiche):
        il risultato consolidato è non-None SOLO se l'intero microchunk è stato validato.
        """
        t0 = time.perf_counter()
        if deadline is None:
            deadline = t0 + DETECTION_MICROCHUNK_TIME_BUDGET_SECONDS
        elif depth > 0 and t0 >= deadline:
            # Budget di tempo del microchunk esaurito: nessuna nuova chiamata, il nodo è un fallimento esplicito.
            return (
                [
                    ChunkAnalysisOutcome(
                        chunk_id=sub.document_id,
                        result=None,
                        status="FAILED",
                        error_type="TimeBudgetExhausted",
                        error_message="Budget di tempo del microchunk esaurito.",
                    )
                ],
                None,
                [
                    ChunkDiagnosticRecord(
                        chunk_index=parent_idx,
                        chunk_id=sub.document_id,
                        status=ChunkLifecycleStatus.FAILED.value,
                        attempts=0,
                        duration_seconds=0.0,
                        model_id=resolved_model,
                        error_type="TimeBudgetExhausted",
                        diagnostic_category="TIME_BUDGET_EXHAUSTED",
                        failure_phase="budget",
                        error_detail="Budget di tempo del microchunk esaurito.",
                        checkpoint_saved=False,
                        diagnostic_metrics=detection_failure_metrics(None, len(sub.bundles)),
                    )
                ],
            )
        attempts_box = {"n": 1}

        def _on_retry(att: int, reason: str) -> None:
            attempts_box["n"] = max(attempts_box["n"], att + 1)
            stats["retries"] += 1
            tracker.record_retry(reason)

        stats["detect_calls"] += 1
        try:
            res = analyzer.detect_topic(
                document=sub,
                topic=topic,
                strategy=strategy,
                model_id=resolved_model,
                temperature=eff_temp,
                seed=eff_seed,
                timeout_seconds=self.timeout_seconds,
                max_tokens=eff_max_tokens,
                operational_mode=operational_mode,
                context_length=self.context_length,
                on_retry_callback=_on_retry,
            )
        except Exception as exc:
            if isinstance(exc, (*PROGRAMMING_ERRORS, *SYSTEMIC_BACKEND_ERRORS)):
                raise
            cat, phase, safe_desc = classify_discovery_exception(exc)
            # Osservabilità (solo numeri/enum): vale per OGNI nodo fallito, anche quelli poi bisecati.
            metrics = detection_failure_metrics(exc, len(sub.bundles))
            note_detection_node_failure(stats, cat, metrics)
            if isinstance(exc, AiBackendTimeoutError):
                stats["timeouts"] += 1
                stats["consecutive_timeouts"] += 1
                if (
                    stats["consecutive_timeouts"] >= DETECTION_MAX_CONSECUTIVE_TIMEOUTS
                    or stats["timeouts"] >= DETECTION_MAX_TOTAL_TIMEOUTS
                ):
                    # Backend non reattivo: interrompe l'analisi (il progresso già validato resta nei checkpoint)
                    raise
            else:
                stats["consecutive_timeouts"] = 0

            bisectable = (
                isinstance(exc, (AiBackendRequestError, AnalysisInputTooLargeError, AiStructuredOutputError))
                and getattr(exc, "error_category", None) not in NON_BISECTABLE_REQUEST_CATEGORIES
                and len(sub.bundles) > 1
                and depth < DETECTION_MICROCHUNK_MAX_DEPTH
                and time.perf_counter() < deadline
            )
            if bisectable:
                stats["bisections"] += 1
                tracker.microchunk_bisected()
                half_a, half_b = self._halve_detection_document(sub, f"::d{depth + 1}a", f"::d{depth + 1}b")
                kw = dict(
                    analyzer=analyzer, topic=topic, strategy=strategy, resolved_model=resolved_model,
                    eff_temp=eff_temp, eff_seed=eff_seed, eff_max_tokens=eff_max_tokens,
                    operational_mode=operational_mode, tracker=tracker, stats=stats,
                    parent_idx=parent_idx, depth=depth + 1, deadline=deadline,
                )
                out_a, res_a, diag_a = self._execute_detection_microchunk(sub=half_a, **kw)
                out_b, res_b, diag_b = self._execute_detection_microchunk(sub=half_b, **kw)
                if res_a is not None and res_b is not None:
                    merged = self._merge_detection_halves(topic, sub, res_a, res_b)
                    return (
                        [ChunkAnalysisOutcome(chunk_id=sub.document_id, result=merged, status="SUCCESS")],
                        merged,
                        diag_a + diag_b,
                    )
                return out_a + out_b, None, diag_a + diag_b

            leaf_attempts = int(getattr(exc, "attempts", attempts_box["n"]))
            _bump(stats, "leaf_attempts", leaf_attempts)
            diag = ChunkDiagnosticRecord(
                chunk_index=parent_idx,
                chunk_id=sub.document_id,
                status=ChunkLifecycleStatus.FAILED.value,
                attempts=leaf_attempts,
                duration_seconds=round(time.perf_counter() - t0, 3),
                model_id=resolved_model,
                http_status=getattr(exc, "status_code", None),
                error_type=type(exc).__name__,
                diagnostic_category=cat,
                failure_phase=phase,
                error_detail=safe_desc,
                checkpoint_saved=False,
                # finish_reason/token restano NELLA struttura annidata: i campi di primo livello alimentano
                # il controllo di "risposte verificabili registrate" dell'export e non vanno alterati.
                diagnostic_metrics=metrics,
            )
            return (
                [
                    ChunkAnalysisOutcome(
                        chunk_id=sub.document_id,
                        result=None,
                        status="FAILED",
                        error_type=type(exc).__name__,
                        error_message=safe_desc,
                    )
                ],
                None,
                [diag],
            )

        stats["consecutive_timeouts"] = 0
        _bump(stats, "leaf_attempts", int(res.metadata.get("attempts", attempts_box["n"])))
        diag = ChunkDiagnosticRecord(
            chunk_index=parent_idx,
            chunk_id=sub.document_id,
            status=ChunkLifecycleStatus.SUCCESS.value,
            attempts=int(res.metadata.get("attempts", attempts_box["n"])),
            duration_seconds=round(time.perf_counter() - t0, 3),
            model_id=resolved_model,
            http_status=200,
            finish_reason=res.metadata.get("finish_reason"),
            prompt_tokens=res.metadata.get("prompt_tokens"),
            completion_tokens=res.metadata.get("completion_tokens"),
            total_tokens=res.metadata.get("total_tokens"),
            diagnostic_category="SUCCESS",
            failure_phase="none",
            error_detail="",
            json_valid=True,
            schema_valid=True,
            evidence_valid=True,
            checkpoint_saved=False,
        )
        return [ChunkAnalysisOutcome(chunk_id=sub.document_id, result=res, status="SUCCESS")], res, [diag]

    def _process_subchunks_detection(
        self,
        chunk: ConversationEvidenceDocument,
        analyzer: TopicDetectionAnalyzer,
        topic: TopicQuery,
        strategy: AnalysisLanguageStrategy,
        resolved_model: str,
        eff_temp: float,
        eff_seed: int | None,
        eff_max_tokens: int,
        operational_mode: bool,
        tracker: AnalysisProgressTracker,
        chunk_idx_1based: int,
        *,
        chk_store: Any = None,
        doc_fp: str = "",
        cfg_fp: str = "",
        parent_chunk_index: int = 0,
        parent_document_id: str = "",
        total_planned_chunks: int = 1,
        prompt_version: str = "detection_operational",
        origin: str = "",
        run_id: str = "",
        diagnostics_out: list[ChunkDiagnosticRecord] | None = None,
        stats: dict[str, int] | None = None,
        legacy_chunk_accounting: bool = False,
    ) -> list[ChunkAnalysisOutcome]:
        """
        Esegue la Detection microchunk per microchunk con persistenza incrementale: ogni microchunk
        validato viene salvato su SQLite e, in caso di ripresa, SALTATO senza nuova inferenza.
        Un microchunk fallito NON viene salvato (sarà ritentato) e NON certifica il parent.
        """
        stats = stats if stats is not None else new_detection_stats()
        diag_sink = diagnostics_out if diagnostics_out is not None else []
        parent_document_id = parent_document_id or chunk.document_id
        subs = self._plan_detection_subchunks(chunk, operational_mode)
        total_subs = len(subs)
        tracker.begin_microchunks(chunk_idx_1based, total_subs)
        outcomes: list[ChunkAnalysisOutcome] = []

        for s_idx0, sub in enumerate(subs):
            sub_eids = tuple(s.evidence_id for s in sub.all_evidence_sections)
            cached = self._recover_detection_subchunk(
                chk_store, doc_fp, cfg_fp, parent_chunk_index, s_idx0, sub, chunk, parent_document_id, topic, origin
            )
            if cached is not None:
                stats["recovered"] += 1
                outcomes.append(ChunkAnalysisOutcome(chunk_id=sub.document_id, result=cached, status="SUCCESS"))
                diag_sink.append(
                    ChunkDiagnosticRecord(
                        chunk_index=parent_chunk_index,
                        chunk_id=sub.document_id,
                        status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
                        attempts=0,
                        duration_seconds=0.0,
                        model_id=resolved_model,
                        diagnostic_category="NONE",
                        failure_phase="recovered",
                        error_detail="Recuperato da checkpoint SQLite",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=True,
                    )
                )
                tracker.microchunk_done(recovered=True)
                if legacy_chunk_accounting:
                    tracker.chunk_success(chunk_idx_1based + s_idx0, record_request=False)
                continue

            tracker.start_chunk(
                chunk_idx_1based,
                phase=f"Analisi micro-chunk {s_idx0 + 1}/{total_subs}",
                record_request=False,
            )
            sub_outcomes, sub_res, sub_diags = self._execute_detection_microchunk(
                sub=sub,
                analyzer=analyzer,
                topic=topic,
                strategy=strategy,
                resolved_model=resolved_model,
                eff_temp=eff_temp,
                eff_seed=eff_seed,
                eff_max_tokens=eff_max_tokens,
                operational_mode=operational_mode,
                tracker=tracker,
                stats=stats,
                parent_idx=parent_chunk_index,
            )
            diag_sink.extend(sub_diags)
            outcomes.extend(sub_outcomes)
            if sub_res is None:
                stats["failed"] += 1
                tracker.microchunk_failed()
                if legacy_chunk_accounting:
                    tracker.chunk_failed(chunk_idx_1based + s_idx0, "sotto-blocco non validato", record_request=False)
                continue

            stats["completed"] += 1
            if chk_store is not None and doc_fp and cfg_fp:
                try:
                    meta = {k: v for k, v in dict(sub_res.metadata).items() if k != "raw_response"}
                    if origin:
                        meta["origin"] = origin
                    if run_id:
                        meta["run_id"] = run_id
                    chk_store.save_subchunk(
                        document_id=parent_document_id,
                        document_fingerprint=doc_fp,
                        config_fingerprint=cfg_fp,
                        parent_chunk_index=parent_chunk_index,
                        subchunk_index=s_idx0,
                        total_subchunks=total_subs,
                        result=TopicDetectionResult(
                            topic=sub_res.topic,
                            decision=sub_res.decision,
                            evidence_ids=sub_res.evidence_ids,
                            rationale=sub_res.rationale,
                            provenance_document_id=sub_res.provenance_document_id,
                            metadata=meta,
                        ),
                        subchunk_id=sub.document_id,
                        evidence_ids=sub_eids,
                        total_planned_chunks=total_planned_chunks,
                        analysis_type="detection",
                        model_id=resolved_model,
                        prompt_version=prompt_version,
                        origin=origin,
                        run_id=run_id,
                    )
                    for d in sub_diags:
                        d.checkpoint_saved = True
                    stats["checkpoint_writes_ok"] += 1
                except Exception as save_err:
                    # L'inferenza è valida, ma la durabilità/resume di questo microchunk NON è garantita:
                    # il fallimento è contato e reso osservabile (non cambia la decisione semantica).
                    stats["checkpoint_write_failures"] += 1
                    logger.warning("Salvataggio microchunk Detection su SQLite non riuscito (%s).", type(save_err).__name__)
            tracker.microchunk_done(recovered=False)
            if legacy_chunk_accounting:
                tracker.chunk_success(chunk_idx_1based + s_idx0, record_request=False)
        return outcomes

    def _run_detection_split_core(
        self,
        *,
        document: ConversationEvidenceDocument,
        chunk: ConversationEvidenceDocument,
        parent_idx: int,
        tracker_idx: int,
        total_planned_chunks: int,
        analyzer: TopicDetectionAnalyzer,
        topic: TopicQuery,
        strategy: AnalysisLanguageStrategy,
        resolved_model: str,
        eff_temp: float,
        eff_seed: int | None,
        eff_max_tokens: int,
        operational_mode: bool,
        tracker: AnalysisProgressTracker,
        chk_store: Any,
        doc_fp: str,
        cfg_fp: str,
        prompt_version: str,
        origin: str,
        run_id: str,
        stats: dict[str, int],
        legacy_chunk_accounting: bool = False,
    ) -> tuple[list[ChunkAnalysisOutcome], TopicDetectionResult | None, list[ChunkDiagnosticRecord]]:
        """
        Detection di UN parent via microchunk. Il parent è certificato (risultato non-None, checkpoint
        del parent salvato) SOLO se TUTTI i microchunk sono validati; altrimenti nessun SUCCESS spurio.
        """
        diag_sink: list[ChunkDiagnosticRecord] = []
        try:
            sub_outcomes = self._process_subchunks_detection(
                chunk=chunk,
                analyzer=analyzer,
                topic=topic,
                strategy=strategy,
                resolved_model=resolved_model,
                eff_temp=eff_temp,
                eff_seed=eff_seed,
                eff_max_tokens=eff_max_tokens,
                operational_mode=operational_mode,
                tracker=tracker,
                chunk_idx_1based=tracker_idx,
                chk_store=chk_store,
                doc_fp=doc_fp,
                cfg_fp=cfg_fp,
                parent_chunk_index=parent_idx,
                parent_document_id=document.document_id,
                total_planned_chunks=total_planned_chunks,
                prompt_version=prompt_version,
                origin=origin,
                run_id=run_id,
                diagnostics_out=diag_sink,
                stats=stats,
                legacy_chunk_accounting=legacy_chunk_accounting,
            )
        finally:
            tracker.end_microchunks()

        if not sub_outcomes or any(o.status != "SUCCESS" for o in sub_outcomes):
            return sub_outcomes, None, diag_sink

        parent_res = aggregate_topic_detection(
            outcomes=sub_outcomes,
            topic=topic,
            parent_document_id=chunk.document_id,
            expected_chunk_count=len(sub_outcomes),
        )
        meta = dict(parent_res.metadata)
        meta.update(
            {
                "origin": origin,
                "run_id": run_id,
                "prompt_version": prompt_version,
                "model": resolved_model,
                "split_via_microchunks": True,
                "microchunk_count": len(sub_outcomes),
            }
        )
        parent_res = TopicDetectionResult(
            topic=parent_res.topic,
            decision=parent_res.decision,
            evidence_ids=parent_res.evidence_ids,
            rationale=parent_res.rationale,
            provenance_document_id=chunk.document_id,
            metadata=meta,
        )
        if chk_store is not None and doc_fp and cfg_fp:
            try:
                chk_store.save_chunk(
                    document_id=document.document_id,
                    document_fingerprint=doc_fp,
                    config_fingerprint=cfg_fp,
                    chunk_idx=parent_idx,
                    result=parent_res,
                    total_planned_chunks=total_planned_chunks,
                    analysis_type="detection",
                    model_id=resolved_model,
                    prompt_version=prompt_version,
                    origin=origin,
                    run_id=run_id,
                )
                stats["checkpoint_writes_ok"] += 1
            except Exception as save_err:
                stats["checkpoint_write_failures"] += 1
                logger.warning("Salvataggio del parent Detection non riuscito (%s).", type(save_err).__name__)
        return sub_outcomes, parent_res, diag_sink

    def _run_detection_parent_split(
        self,
        *,
        all_chunk_diagnostics: list[ChunkDiagnosticRecord],
        chunk: ConversationEvidenceDocument,
        parent_idx: int,
        tracker_idx: int,
        total_planned_chunks: int,
        document: ConversationEvidenceDocument,
        tracker: AnalysisProgressTracker,
        chk_store: Any,
        doc_fp: str,
        cfg_fp: str,
        resolved_model: str,
        prompt_version: str,
        legacy_chunk_accounting: bool = False,
        requests_before: int | None = None,
        **core_kwargs: Any,
    ) -> tuple[list[ChunkAnalysisOutcome], bool]:
        """
        Percorso multi-chunk: split del parent + contabilità a livello di parent (una sola volta).
        Con legacy_chunk_accounting=True (split reattivo dopo un tentativo sul parent intero) il tracker
        conta i sotto-blocchi come blocchi, come nel comportamento storico; il parent non viene ricontato.
        """
        t0 = time.perf_counter()
        _wf_stats = core_kwargs.get("stats") or {}
        _wf_before = int(_wf_stats.get("checkpoint_write_failures", 0))
        # Istantanea per la provenienza del resume e la spesa di QUESTA esecuzione (solo contatori).
        _req_before = int(requests_before) if requests_before is not None else int(tracker.requests_sent)
        _snap_before = {k: int(_wf_stats.get(k, 0)) for k in ("recovered", "completed", "failed", "leaf_attempts")}
        try:
            sub_outcomes, parent_res, diag_sink = self._run_detection_split_core(
                document=document,
                chunk=chunk,
                parent_idx=parent_idx,
                tracker_idx=tracker_idx,
                total_planned_chunks=total_planned_chunks,
                tracker=tracker,
                chk_store=chk_store,
                doc_fp=doc_fp,
                cfg_fp=cfg_fp,
                resolved_model=resolved_model,
                prompt_version=prompt_version,
                legacy_chunk_accounting=legacy_chunk_accounting,
                **core_kwargs,
            )
        except Exception as abort_exc:
            tracker.chunk_failed(tracker_idx, type(abort_exc).__name__, record_request=False)
            tracker.fail(f"Topic Detection interrotta: {type(abort_exc).__name__}")
            raise
        all_chunk_diagnostics.extend(diag_sink)
        ok = parent_res is not None
        dur = round(time.perf_counter() - t0, 3)
        first_failed = self._first_real_failure(diag_sink)
        _rec = int(_wf_stats.get("recovered", 0)) - _snap_before["recovered"]
        _comp = int(_wf_stats.get("completed", 0)) - _snap_before["completed"]
        _fail = int(_wf_stats.get("failed", 0)) - _snap_before["failed"]
        _computed_leaf_attempts = int(_wf_stats.get("leaf_attempts", 0)) - _snap_before["leaf_attempts"]
        _delta_req = int(tracker.requests_sent) - _req_before
        parent_diag = ChunkDiagnosticRecord(
            chunk_index=parent_idx,
            chunk_id=chunk.document_id,
            status=(ChunkLifecycleStatus.SUCCESS.value if ok else ChunkLifecycleStatus.FAILED.value),
            attempts=sum(int(d.attempts or 0) for d in diag_sink),
            duration_seconds=dur,
            model_id=resolved_model,
            http_status=200 if ok else None,
            diagnostic_category="SUCCESS" if ok else (first_failed.diagnostic_category if first_failed else "UNKNOWN_ERROR"),
            failure_phase="none" if ok else (first_failed.failure_phase if first_failed else "inference"),
            error_detail="" if ok else f"{sum(1 for o in sub_outcomes if o.status != 'SUCCESS')} micro-chunk non validati",
            # vero solo se TUTTE le scritture di questo parent (microchunk + parent) sono riuscite
            checkpoint_saved=ok and chk_store is not None and int(_wf_stats.get("checkpoint_write_failures", 0)) == _wf_before,
            # Il tipo di errore del primo nodo realmente fallito (nessun valore inventato se non c'è).
            error_type=(first_failed.error_type if (not ok and first_failed is not None) else None),
            diagnostic_metrics=(None if ok else self._parent_failure_metrics(diag_sink, first_failed)),
            resume_provenance=self._parent_resume_provenance(
                recovered=_rec,
                computed=_comp,
                failed=_fail,
                http_delta=_delta_req,
                computed_leaf_attempts=_computed_leaf_attempts,
                via_microchunks=True,
            ),
        )
        all_chunk_diagnostics.append(parent_diag)
        if chk_store is not None and doc_fp and cfg_fp:
            try:
                chk_store.save_chunk_diagnostic(
                    document_id=document.document_id,
                    document_fingerprint=doc_fp,
                    config_fingerprint=cfg_fp,
                    diagnostic=parent_diag,
                    total_planned_chunks=total_planned_chunks,
                    analysis_type="detection",
                    model_id=resolved_model,
                    prompt_version=prompt_version,
                )
            except Exception:
                pass
        if ok:
            if not legacy_chunk_accounting:
                tracker.chunk_success(tracker_idx, record_request=False)
            return [ChunkAnalysisOutcome(chunk_id=chunk.document_id, result=parent_res, status="SUCCESS")], True
        if not legacy_chunk_accounting:
            tracker.chunk_failed(tracker_idx, "micro-chunk non validati", record_request=False)
        return sub_outcomes, False

    @staticmethod
    def _first_real_failure(diag_sink: Sequence[ChunkDiagnosticRecord]) -> ChunkDiagnosticRecord | None:
        """
        Primo nodo realmente fallito, in ordine di esecuzione. I nodi chiusi dal budget di tempo sono una CONSEGUENZA
        di un fallimento precedente: valgono come causa solo se non esiste altro fallimento.
        """
        failed = [d for d in diag_sink if d.status == ChunkLifecycleStatus.FAILED.value]
        for d in failed:
            if d.diagnostic_category != "TIME_BUDGET_EXHAUSTED":
                return d
        return failed[0] if failed else None

    @staticmethod
    def _parent_failure_metrics(
        diag_sink: Sequence[ChunkDiagnosticRecord], first_failed: ChunkDiagnosticRecord | None
    ) -> dict[str, Any]:
        """Aggregato NUMERICO dei nodi foglia falliti di un parent (categorie e tipi di innesco: solo conteggi)."""
        failed = [d for d in diag_sink if d.status == ChunkLifecycleStatus.FAILED.value]
        by_category: dict[str, int] = {}
        by_trigger: dict[str, int] = {}
        for d in failed:
            by_category[d.diagnostic_category] = by_category.get(d.diagnostic_category, 0) + 1
            kind = (d.diagnostic_metrics or {}).get("trigger_kind")
            if kind in _DEGENERATION_TRIGGER_KINDS:
                by_trigger[kind] = by_trigger.get(kind, 0) + 1
        first: dict[str, Any] | None = None
        if first_failed is not None:
            first = {
                "diagnostic_category": first_failed.diagnostic_category,
                "failure_phase": first_failed.failure_phase,
                "error_type": first_failed.error_type,
                **dict(first_failed.diagnostic_metrics or {}),
            }
        return {
            "failed_leaf_count": len(failed),
            "failure_category_counts": by_category,
            "degeneration_trigger_counts": by_trigger,
            "first_failure": first,
        }

    @staticmethod
    def _parent_resume_provenance(
        *,
        recovered: int,
        computed: int,
        failed: int,
        http_delta: int,
        computed_leaf_attempts: int,
        via_microchunks: bool,
    ) -> dict[str, Any]:
        """
        Origine del risultato del parent e spesa di QUESTA esecuzione. Gli attempts storici dei checkpoint non vengono
        riscritti: `original_attempts` è None (non disponibile) finché non è registrato esplicitamente.
        `current_run_http_requests` è None se sono stati eseguiti nodi ma il client non ha riportato richieste.
        """
        if recovered > 0 and computed == 0 and failed == 0:
            origin = RESUME_ORIGIN_CHECKPOINT
        elif recovered == 0:
            origin = RESUME_ORIGIN_COMPUTED
        else:
            origin = RESUME_ORIGIN_MIXED
        executed = computed_leaf_attempts > 0
        http: int | None = int(http_delta) if (http_delta > 0 or not executed) else None
        return {
            "result_origin": origin,
            "via_microchunks": bool(via_microchunks),
            # Contatori di microchunk: None quando il parent è stato elaborato in una sola chiamata (nessun microchunk).
            "microchunks_recovered": int(recovered) if via_microchunks else None,
            "microchunks_computed": int(computed) if via_microchunks else None,
            "microchunks_failed": int(failed) if via_microchunks else None,
            "current_run_http_requests": http,
            "original_attempts": None,
        }

    @staticmethod
    def _detection_diagnostics_summary(
        tracker: AnalysisProgressTracker,
        det_stats: Mapping[str, int],
        parent_total: int,
        parent_completed: int,
        start_time: float,
        diagnostics: Sequence[ChunkDiagnosticRecord] | None = None,
    ) -> dict[str, Any]:
        """Diagnostica numerica sanificata della Detection (nessun testo di conversazione né output del modello)."""
        # Disponibilità del trasporto: se sono stati eseguiti nodi ma il client non ha riportato richieste,
        # il conteggio HTTP è "non disponibile" (None), NON zero.
        transport_available = not (int(tracker.requests_sent) == 0 and int(det_stats.get("leaf_attempts", 0)) > 0)
        prov = [d.resume_provenance for d in (diagnostics or ()) if d.resume_provenance is not None]

        def _tally(origin: str) -> int | None:
            return sum(1 for p in prov if p.get("result_origin") == origin) if prov else None

        return {
            "parent_chunks_total": int(parent_total),
            "parent_chunks_completed": int(parent_completed),
            "parent_chunks_failed": int(parent_total) - int(parent_completed),
            "parents_recovered_from_checkpoint": _tally(RESUME_ORIGIN_CHECKPOINT),
            "parents_computed_this_run": _tally(RESUME_ORIGIN_COMPUTED),
            "parents_mixed_checkpoint_and_computed": _tally(RESUME_ORIGIN_MIXED),
            "microchunks_total": int(tracker.microchunks_total),
            "microchunks_completed": int(tracker.microchunks_completed),
            "microchunks_recovered_from_checkpoint": int(tracker.microchunks_recovered),
            "microchunks_computed_this_run": max(0, int(tracker.microchunks_completed) - int(tracker.microchunks_recovered)),
            "microchunks_failed": int(tracker.microchunks_failed),
            "leaf_attempts_total": int(det_stats.get("leaf_attempts", 0)),
            "node_failures": int(det_stats.get("node_failures", 0)),
            "degenerate_failures": int(det_stats.get("degenerate_failures", 0)),
            # Chiavi piatte (il consumatore storico serializza `dict(summary)` con json.dumps: niente mappe annidate).
            "degeneration_trigger_char_run": int(det_stats.get("trigger_char_run", 0)),
            "degeneration_trigger_tab_run": int(det_stats.get("trigger_tab_run", 0)),
            "degeneration_trigger_pattern_loop": int(det_stats.get("trigger_pattern_loop", 0)),
            "http_requests_basis": "REQUEST_LISTENER" if transport_available else "NOT_AVAILABLE",
            "http_requests_successful": int(tracker.requests_successful) if transport_available else None,
            "http_requests_in_flight": int(tracker.requests_in_flight) if transport_available else None,
            "http_requests_accounting_consistent": bool(tracker.check_requests_invariant()) if transport_available else None,
            "detect_topic_calls": int(det_stats.get("detect_calls", 0)),
            "http_requests_sent": int(tracker.requests_sent) if transport_available else None,
            "http_requests_failed": int(tracker.requests_failed) if transport_available else None,
            "retries": int(tracker.retries_count),
            "bisections": int(det_stats.get("bisections", 0)),
            "timeouts": int(det_stats.get("timeouts", 0)),
            "callback_errors": int(tracker.callback_errors_count),
            "checkpoint_writes_ok": int(det_stats.get("checkpoint_writes_ok", 0)),
            "checkpoint_write_failures": int(det_stats.get("checkpoint_write_failures", 0)),
            "checkpoint_persistence": (
                "WRITE_FAILURES" if det_stats.get("checkpoint_write_failures", 0) > 0
                else ("OK" if det_stats.get("checkpoint_writes_ok", 0) > 0 else "NOT_ATTEMPTED")
            ),
            "resume_guaranteed": bool(det_stats.get("checkpoint_write_failures", 0) == 0),
            "duration_seconds": round(time.perf_counter() - start_time, 3),
        }

    def _execute_microchunk_with_adaptive_fallback(
        self,
        sub: ConversationEvidenceDocument,
        analyzer: TopicDiscoveryAnalyzer,
        resolved_model: str,
        eff_temp: float,
        eff_seed: int | None,
        chunk_target_topics: int,
        tracker: AnalysisProgressTracker,
        sub_display_idx: int,
        eff_max_tokens: int | None,
        depth: int = 0,
        max_depth: int = 3,
        parent_document_id: str = "",
    ) -> tuple[DiscoveryChunkOutcome, TopicDiscoveryResult | None]:
        """
        Esegue un singolo microchunk con fallback adattivo per bisezione (divide-and-conquer)
        fino a profondità massima di 3 livelli in caso di errori recuperabili (GBNF, troncamento, decode).
        """
        t_sub_start = time.perf_counter()
        sub_attempts = 1
        sub_eids = tuple(s.evidence_id for s in sub.all_evidence_sections)

        def _on_sub_retry(att: int, r: str) -> None:
            nonlocal sub_attempts
            sub_attempts = max(sub_attempts, att + 1)
            tracker.record_retry(f"sub_d{depth}:{r}")

        micro_target_topics = min(chunk_target_topics, max(2, min(5, len(sub.bundles))))
        micro_max_tokens = min(eff_max_tokens or self.default_max_tokens or 640, 640 if depth == 0 else 448)
        try:
            res = analyzer.discover_topics(
                document=sub,
                model_id=resolved_model,
                temperature=eff_temp,
                seed=eff_seed,
                timeout_seconds=self.timeout_seconds,
                max_topics=micro_target_topics,
                max_tokens=micro_max_tokens,
                operational_mode=True,
                context_length=self.context_length,
                on_retry_callback=_on_sub_retry,
            )
            dur = round(time.perf_counter() - t_sub_start, 3)
            obs_finish = res.metadata.get("finish_reason")
            num_extracted = len(res.topics)
            thematic_st = "TOPICS_EXTRACTED" if num_extracted > 0 else "EMPTY_TOPICS"
            sub_diag = ChunkDiagnosticRecord(
                chunk_index=sub_display_idx - 1,
                chunk_id=sub.document_id,
                status=ChunkLifecycleStatus.SUCCESS.value,
                thematic_status=thematic_st,
                attempts=int(res.metadata.get("retries", 0)) + 1,
                duration_seconds=dur,
                model_id=str(res.metadata.get("model") or resolved_model),
                http_status=200,
                finish_reason=obs_finish,
                prompt_tokens=res.metadata.get("prompt_tokens"),
                completion_tokens=res.metadata.get("completion_tokens"),
                error_type=None,
                diagnostic_category="NONE",
                failure_phase="completed",
                error_detail=None if num_extracted > 0 else "Elaborato con successo ma senza argomenti estratti",
                json_valid=True,
                schema_valid=True,
                evidence_valid=True,
                checkpoint_saved=False,
                topics_count=num_extracted,
            )
            outcome = DiscoveryChunkOutcome(
                chunk_id=sub.document_id,
                status="SUCCESS",
                thematic_status=thematic_st,
                result=res,
                evidence_ids=sub_eids,
                diagnostic=sub_diag,
                chunk_index=sub_display_idx - 1,
                attempts=sub_diag.attempts,
                duration_seconds=dur,
            )
            return outcome, res
        except Exception as s_exc:
            if isinstance(s_exc, PROGRAMMING_ERRORS):
                tracker.chunk_failed(sub_display_idx, str(s_exc))
                tracker.fail(str(s_exc))
                raise

            cat, phase, safe_desc = classify_discovery_exception(s_exc)

            # Valuta se l'errore è recuperabile mediante suddivisione adattiva
            recoverable_cats = {
                ChunkDiagnosticCategory.HTTP_CONTEXT_OVERFLOW.value,
                ChunkDiagnosticCategory.GBNF_GRAMMAR_ERROR.value,
                ChunkDiagnosticCategory.DEGENERATE_OUTPUT.value,
                ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value,
                ChunkDiagnosticCategory.JSON_DECODE_ERROR.value,
                ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value,
                ChunkDiagnosticCategory.DUPLICATE_EVIDENCE_ID.value,
                ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value,
                ChunkDiagnosticCategory.RETRY_EXHAUSTED.value,
                ChunkDiagnosticCategory.UNKNOWN_ERROR.value,
            }
            can_bisect = (
                (cat in recoverable_cats or isinstance(s_exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, AiBackendRequestError)))
                and len(sub.bundles) > 1
                and depth < max_depth
            )

            bis_calls: int | None = 0
            bis_depth = 0
            discarded_ev = 0
            if can_bisect:
                tracker.microchunk_bisected()
                mid = len(sub.bundles) // 2
                half_a = ConversationEvidenceDocument(
                    document_id=f"{sub.document_id}::d{depth + 1}a",
                    bundles=sub.bundles[:mid],
                    chat_id=sub.chat_id,
                    source_name=sub.source_name,
                )
                half_b = ConversationEvidenceDocument(
                    document_id=f"{sub.document_id}::d{depth + 1}b",
                    bundles=sub.bundles[mid:],
                    chat_id=sub.chat_id,
                    source_name=sub.source_name,
                )
                logger.info(
                    "Bisezione adattiva microchunk %s (profondità %d/%d) a causa di %s",
                    sub.document_id, depth + 1, max_depth, cat,
                )
                half_topics = max(2, min(4, chunk_target_topics // 2))
                out_a, res_a = self._execute_microchunk_with_adaptive_fallback(
                    sub=half_a,
                    analyzer=analyzer,
                    resolved_model=resolved_model,
                    eff_temp=eff_temp,
                    eff_seed=eff_seed,
                    chunk_target_topics=half_topics,
                    tracker=tracker,
                    sub_display_idx=sub_display_idx,
                    eff_max_tokens=eff_max_tokens,
                    depth=depth + 1,
                    max_depth=max_depth,
                    parent_document_id=parent_document_id,
                )
                _ca, _da = _outcome_llm_calls(out_a)
                bis_calls = (bis_calls + _ca) if (bis_calls is not None and _ca is not None) else None
                bis_depth = max(bis_depth, _da + 1)
                if res_a is None:
                    discarded_ev += _outcome_discarded_evidence(out_a)
                if res_a is not None:
                    discarded_ev += len(out_a.evidence_ids)
                    out_b, res_b = self._execute_microchunk_with_adaptive_fallback(
                        sub=half_b,
                        analyzer=analyzer,
                        resolved_model=resolved_model,
                        eff_temp=eff_temp,
                        eff_seed=eff_seed,
                        chunk_target_topics=half_topics,
                        tracker=tracker,
                        sub_display_idx=sub_display_idx,
                        eff_max_tokens=eff_max_tokens,
                        depth=depth + 1,
                        max_depth=max_depth,
                        parent_document_id=parent_document_id,
                    )
                    _cb, _db = _outcome_llm_calls(out_b)
                    bis_calls = (bis_calls + _cb) if (bis_calls is not None and _cb is not None) else None
                    bis_depth = max(bis_depth, _db + 1)
                    if res_b is None:
                        discarded_ev += _outcome_discarded_evidence(out_b)
                    if res_b is not None:
                        discarded_ev = 0  # entrambe riuscite: nulla va perso
                        # Entrambe le metà riuscite: consolidamento delle due parti
                        agg_sub = aggregate_topic_discovery(
                            chunk_results=[res_a, res_b],
                            parent_document_id=parent_document_id or sub.document_id,
                            max_topics=chunk_target_topics,
                        )
                        dur = round(time.perf_counter() - t_sub_start, 3)
                        tot_att = (out_a.attempts or 1) + (out_b.attempts or 1)
                        sub_diag = ChunkDiagnosticRecord(
                            chunk_index=sub_display_idx - 1,
                            chunk_id=sub.document_id,
                            status=ChunkLifecycleStatus.SUCCESS.value,
                            thematic_status="TOPICS_EXTRACTED" if agg_sub.topics else "EMPTY_TOPICS",
                            attempts=tot_att,
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=200,
                            finish_reason="stop",
                            prompt_tokens=(res_a.metadata.get("prompt_tokens") or 0) + (res_b.metadata.get("prompt_tokens") or 0),
                            completion_tokens=(res_a.metadata.get("completion_tokens") or 0) + (res_b.metadata.get("completion_tokens") or 0),
                            error_type=None,
                            diagnostic_category="NONE",
                            failure_phase="completed_via_adaptive_bisection",
                            error_detail=None,
                            json_valid=True,
                            schema_valid=True,
                            evidence_valid=True,
                            checkpoint_saved=False,
                            topics_count=len(agg_sub.topics),
                            diagnostic_metrics=discovery_failure_metrics(
                                s_exc, bisection_calls=bis_calls, bisection_depth_max=bis_depth,
                            ),
                        )
                        outcome = DiscoveryChunkOutcome(
                            chunk_id=sub.document_id,
                            status="SUCCESS",
                            thematic_status=sub_diag.thematic_status,
                            result=agg_sub,
                            evidence_ids=sub_eids,
                            diagnostic=sub_diag,
                            chunk_index=sub_display_idx - 1,
                            attempts=tot_att,
                            duration_seconds=dur,
                        )
                        return outcome, agg_sub

            dur = round(time.perf_counter() - t_sub_start, 3)
            is_val_err = isinstance(s_exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
            eff_sub_att = int(getattr(s_exc, "attempts", sub_attempts))
            fail_metrics = discovery_failure_metrics(
                s_exc, bisection_calls=bis_calls, bisection_depth_max=bis_depth,
                discarded_success_evidence_count=discarded_ev,
            )
            att_tel = getattr(s_exc, "attempt_telemetry", None)
            last_att = att_tel[-1] if isinstance(att_tel, tuple) and att_tel else None
            sum_tokens = None
            if last_att is not None and all(isinstance(a.get("completion_tokens"), int) for a in att_tel):
                sum_tokens = sum(int(a["completion_tokens"]) for a in att_tel)
            sub_diag = ChunkDiagnosticRecord(
                chunk_index=sub_display_idx - 1,
                chunk_id=sub.document_id,
                status=ChunkLifecycleStatus.FAILED.value,
                thematic_status="NONE",
                attempts=eff_sub_att,
                duration_seconds=dur,
                model_id=resolved_model,
                # Prima questi campi erano fissi a None anche se l'ultimo tentativo li aveva restituiti: ora solo valori
                # MISURATI dall'ultimo tentativo (None se la telemetria non è disponibile).
                http_status=getattr(s_exc, "status_code", None) or (200 if (last_att and last_att.get("response_received")) else None),
                finish_reason=finish_reason_from_kind(last_att.get("finish_reason_kind")) if last_att else None,
                prompt_tokens=None,
                completion_tokens=sum_tokens,
                error_type=type(s_exc).__name__,
                diagnostic_category=cat,
                failure_phase=phase,
                error_detail=safe_desc,
                json_valid=(cat not in (ChunkDiagnosticCategory.JSON_DECODE_ERROR.value, ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value)),
                # JSON e schema sono strutturalmente validi quando a fallire è SOLO la verifica delle citazioni
                # (coerente con il ramo dei blocchi principali); prima era sempre False (imprecisione diagnostica).
                schema_valid=isinstance(s_exc, AiInvalidEvidenceCitationError),
                evidence_valid=False,
                checkpoint_saved=False,
                topics_count=0,
                diagnostic_metrics=_merge_diag_metrics(discovery_citation_metrics(s_exc, len(sub.bundles)), fail_metrics),
            )
            outcome = DiscoveryChunkOutcome(
                chunk_id=sub.document_id,
                status="FAILED",
                result=None,
                evidence_ids=sub_eids,
                error_type=type(s_exc).__name__,
                safe_error_code=cat,
                diagnostic=sub_diag,
                chunk_index=sub_display_idx - 1,
                attempts=eff_sub_att,
                duration_seconds=dur,
            )
            return outcome, None

    def _process_subchunks_discovery(
        self,
        chunk: ConversationEvidenceDocument,
        analyzer: TopicDiscoveryAnalyzer,
        resolved_model: str,
        eff_temp: float,
        eff_seed: int | None,
        chunk_target_topics: int,
        tracker: AnalysisProgressTracker,
        chunk_idx_1based: int,
        chk_store: Any = None,
        doc_fp: str = "",
        cfg_fp: str = "",
        parent_chunk_index: int = 0,
        parent_document_id: str = "",
        total_planned_chunks: int = 1,
        eff_max_tokens: int | None = None,
        origin: str = "",
        run_id: str = "",
    ) -> tuple[list[DiscoveryChunkOutcome], list[TopicDiscoveryResult]]:
        """Esegue suddivisione preventiva e adattiva di un blocco di discovery con persistenza incrementale e recupero dei sotto-blocchi."""
        subs = split_chunk_into_microchunks(chunk, max_substantive_messages=8)
        if len(subs) <= 1 and len(chunk.bundles) > 1:
            mid = len(chunk.bundles) // 2
            sub1 = ConversationEvidenceDocument(
                document_id=f"{chunk.document_id}::sub::0",
                bundles=chunk.bundles[:mid],
                chat_id=chunk.chat_id,
                source_name=chunk.source_name,
            )
            sub2 = ConversationEvidenceDocument(
                document_id=f"{chunk.document_id}::sub::1",
                bundles=chunk.bundles[mid:],
                chat_id=chunk.chat_id,
                source_name=chunk.source_name,
            )
            subs = [sub1, sub2]
        outcomes: list[DiscoveryChunkOutcome] = []
        results: list[TopicDiscoveryResult] = []
        total_subs = len(subs)

        # Contabilità a microchunk (totale dinamico per blocco principale); i parent restano contati dal chiamante.
        tracker.begin_microchunks(chunk_idx_1based, total_subs)
        try:
            for s_idx, sub in enumerate(subs, start=1):
                sub_idx_0based = s_idx - 1
                sub_display_idx = chunk_idx_1based + sub_idx_0based
                sub_eids = tuple(s.evidence_id for s in sub.all_evidence_sections)

                # Controllo recupero da checkpoint se abilitato
                cached_sub = None
                if chk_store is not None and doc_fp and cfg_fp:
                    try:
                        cached_sub = chk_store.get_subchunk(
                            document_fingerprint=doc_fp,
                            config_fingerprint=cfg_fp,
                            parent_chunk_index=parent_chunk_index,
                            subchunk_index=sub_idx_0based,
                        )
                    except Exception as c_err:
                        logger.warning("Errore interrogazione sotto-blocco da checkpoint: %s", c_err)
                        cached_sub = None

                # Rivalidazione forense del sotto-blocco recuperato
                valid_cached_res = None
                if cached_sub is not None:
                    try:
                        cand_res = deserialize_discovery_result_from_checkpoint(cached_sub["serialized_result"])
                        cand_prov = getattr(cand_res, "provenance_document_id", None)
                        if cand_prov in (parent_document_id, chunk.document_id, sub.document_id):
                            all_sub_eids_set = set(sub_eids)
                            eids_valid = True
                            for t in cand_res.topics:
                                if not set(t.evidence_ids).issubset(all_sub_eids_set):
                                    eids_valid = False
                                    break
                            if eids_valid:
                                valid_cached_res = cand_res
                    except Exception as val_c_err:
                        logger.warning("Rivalidazione sotto-blocco fallita: %s", val_c_err)
                        valid_cached_res = None

                if valid_cached_res is not None:
                    obs_finish = valid_cached_res.metadata.get("finish_reason")
                    num_extracted = len(valid_cached_res.topics)
                    thematic_st = "TOPICS_EXTRACTED" if num_extracted > 0 else "EMPTY_TOPICS"
                    sub_diag = ChunkDiagnosticRecord(
                        chunk_index=sub_display_idx - 1,
                        chunk_id=sub.document_id,
                        status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
                        thematic_status=thematic_st,
                        attempts=0,
                        duration_seconds=0.0,
                        model_id=resolved_model,
                        http_status=None,
                        finish_reason=obs_finish,
                        prompt_tokens=valid_cached_res.metadata.get("prompt_tokens"),
                        completion_tokens=valid_cached_res.metadata.get("completion_tokens"),
                        error_type=None,
                        diagnostic_category="NONE",
                        failure_phase="recovered",
                        error_detail="Recuperato con successo da checkpoint SQLite",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=True,
                        topics_count=num_extracted,
                    )
                    outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=sub.document_id,
                            status="RECOVERED_FROM_CHECKPOINT",
                            thematic_status=thematic_st,
                            result=valid_cached_res,
                            evidence_ids=sub_eids,
                            diagnostic=sub_diag,
                            chunk_index=sub_display_idx - 1,
                            attempts=0,
                            duration_seconds=0.0,
                        )
                    )
                    results.append(valid_cached_res)
                    tracker.microchunk_done(recovered=True)
                    continue

                # Esecuzione inferenza sul microchunk mancante
                tracker.start_chunk(chunk_idx_1based, phase=f"Analisi micro-chunk {s_idx}/{total_subs}", record_request=False)
                sub_outcome, sub_res = self._execute_microchunk_with_adaptive_fallback(
                    sub=sub,
                    analyzer=analyzer,
                    resolved_model=resolved_model,
                    eff_temp=eff_temp,
                    eff_seed=eff_seed,
                    chunk_target_topics=chunk_target_topics,
                    tracker=tracker,
                    sub_display_idx=sub_display_idx,
                    eff_max_tokens=eff_max_tokens,
                    depth=0,
                    max_depth=3,
                    parent_document_id=parent_document_id or chunk.document_id,
                )

                if sub_res is not None:
                    if origin or run_id:
                        s_m = dict(sub_res.metadata)
                        if origin:
                            s_m["origin"] = origin
                        if run_id:
                            s_m["run_id"] = run_id
                        sub_res = TopicDiscoveryResult(
                            topics=sub_res.topics,
                            provenance_document_id=sub_res.provenance_document_id,
                            metadata=s_m,
                        )
                    chk_saved = False
                    if chk_store is not None and doc_fp and cfg_fp:
                        try:
                            chk_store.save_subchunk(
                                document_id=parent_document_id or chunk.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                parent_chunk_index=parent_chunk_index,
                                subchunk_index=sub_idx_0based,
                                total_subchunks=total_subs,
                                result=sub_res,
                                subchunk_id=sub.document_id,
                                evidence_ids=sub_eids,
                                total_planned_chunks=total_planned_chunks,
                                analysis_type="discovery",
                                model_id=resolved_model,
                                prompt_version="discovery_operational",
                            )
                            chk_saved = True
                        except Exception as s_save_err:
                            logger.warning("Salvataggio sotto-blocco %s su SQLite non riuscito: %s", sub.document_id, s_save_err)

                    if sub_outcome.diagnostic:
                        sub_outcome.diagnostic.checkpoint_saved = chk_saved
                    outcomes.append(sub_outcome)
                    results.append(sub_res)
                    tracker.microchunk_done()
                else:
                    outcomes.append(sub_outcome)
                    tracker.microchunk_failed()

        finally:
            tracker.end_microchunks()

        if len(results) == total_subs and chk_store is not None and doc_fp and cfg_fp:
            try:
                aggregated_parent = aggregate_topic_discovery(
                    chunk_results=results,
                    parent_document_id=parent_document_id or chunk.document_id,
                    max_topics=chunk_target_topics,
                )
                # Guardia fail-closed sulla provenienza (Audit 27/10/2026):
                # un blocco padre consolidato non può essere marcato LIVE_LM_STUDIO se uno dei
                # sotto-blocchi recuperati da checkpoint aveva origine mancante o non coincidente.
                recovered_sub_origins = [
                    (o.result.metadata.get("origin") if o.result and isinstance(o.result.metadata, Mapping) else None)
                    for o in outcomes
                    if o.status == "RECOVERED_FROM_CHECKPOINT"
                ]
                all_recovered_subs_match_origin = all(
                    bool(ro) and ro == origin for ro in recovered_sub_origins
                )
                eff_parent_origin = origin if (origin and all_recovered_subs_match_origin) else ""
                if eff_parent_origin or run_id:
                    agg_m = dict(aggregated_parent.metadata)
                    if eff_parent_origin:
                        agg_m["origin"] = eff_parent_origin
                    if run_id:
                        agg_m["run_id"] = run_id
                    aggregated_parent = TopicDiscoveryResult(
                        topics=aggregated_parent.topics,
                        provenance_document_id=aggregated_parent.provenance_document_id,
                        metadata=agg_m,
                    )
                chk_store.save_chunk(
                    document_id=parent_document_id or chunk.document_id,
                    document_fingerprint=doc_fp,
                    config_fingerprint=cfg_fp,
                    chunk_idx=parent_chunk_index,
                    result=aggregated_parent,
                    total_planned_chunks=total_planned_chunks,
                    analysis_type="discovery",
                    model_id=resolved_model,
                    prompt_version="discovery_operational",
                    origin=eff_parent_origin,
                    run_id=run_id,
                )
            except Exception as agg_save_err:
                logger.warning("Salvataggio aggregato blocco padre %d non riuscito: %s", parent_chunk_index, agg_save_err)

        return outcomes, results

    def run_topic_detection(
        self,
        document: ConversationEvidenceDocument,
        topic: TopicQuery,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        temperature: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        operational_mode: bool = True,
        preserve_raw_response: bool = False,
        progress_callback: AnalysisProgressCallback | None = None,
    ) -> TopicDetectionResult:
        """
        Esegue Topic Detection con supporto trasparente al chunking multi-chunk e progress callback.
        """
        # Verifica preventiva: documento privo di evidenze testuali non è analizzabile
        if not document.bundles or not document.all_evidence_sections:
            raise AnalysisNoEvidenceError(
                f"Il documento '{document.document_id}' non contiene sezioni di evidenza analizzabili."
            )

        eff_temp = temperature if temperature is not None else self.config.temperature
        eff_seed = seed if seed is not None else self.config.seed
        eff_max_tokens = max_tokens if max_tokens is not None else self.config.max_tokens

        run_id = f"run_det_{uuid.uuid4().hex[:10]}"
        start_time = time.perf_counter()
        start_iso = datetime.now(timezone.utc).isoformat()
        current_git_commit = get_git_commit()

        target_client, resolved_model = self._prepare_client_and_model(client, model_name)

        analyzer = TopicDetectionAnalyzer(
            client=target_client,
            default_model_id=resolved_model,
            operational_mode=operational_mode,
        )

        plan = self.create_analysis_plan(
            document=document,
            analysis_type="detection",
            user_max_tokens=eff_max_tokens,
            context_length=self.context_length,
            topic_query=topic,
        )
        chunks = list(plan.chunks)
        eff_chunk_tokens = plan.chunk_token_budget

        tracker = AnalysisProgressTracker(
            total_chunks=len(chunks),
            callback=progress_callback,
            initial_phase="Analisi dei messaggi",
        )

        is_synthetic = self._is_synthetic_document(document)
        should_preserve_raw = preserve_raw_response or is_synthetic
        classification = "SYNTHETIC_DEMO" if is_synthetic else "REAL_FORENSIC"
        det_stats = new_detection_stats()
        single_legacy_split = False  # True solo se il parent singolo è stato diviso dal percorso reattivo storico
        det_prompt_version = "detection_operational" if operational_mode else "detection"
        det_origin = (
            AnalysisOrigin.LIVE_LM_STUDIO.value
            if self._is_live_lms_client(target_client)
            else AnalysisOrigin.MOCK_INSPECTION.value
        )

        # Checkpoint: caricamento risultati validati pregressi se abilitato
        chk_store = self.checkpoint_store if self.enable_checkpointing else None
        doc_fp = ""
        cfg_fp = ""
        cached_results: dict[int, TopicDetectionResult] = {}
        if chk_store is not None:
            doc_fp = compute_document_fingerprint(document)
            eff_run_cfg = OperationalAnalysisConfig(
                model_spec=resolved_model,
                context_length=self.context_length,
                timeout_seconds=self.timeout_seconds,
                max_tokens=eff_max_tokens,
                temperature=eff_temp,
                seed=eff_seed,
                profile=self.profile,
                chunk_character_budget=self.max_chunk_characters,
                max_chunk_bundles=self.max_chunk_bundles,
                max_chunk_tokens=eff_chunk_tokens,
            )
            cfg_fp = compute_run_config_hash(
                model_id=resolved_model,
                prompt_version="detection_operational" if operational_mode else "detection",
                config=eff_run_cfg,
                topic_query=topic,
                chunks=chunks,
                extra_params={
                    "strategy": strategy.value if hasattr(strategy, "value") else str(strategy),
                    "operational_mode": operational_mode,
                    "loaded_instance_id": (
                        getattr(target_client, "_loaded_instance_id", None)
                        or (getattr(target_client, "_active_binding", None).loaded_instance_id if getattr(target_client, "_active_binding", None) else None)
                    ),
                    "resolved_model_key": (
                        getattr(target_client, "_active_binding", None).resolved_model_key
                        if getattr(target_client, "_active_binding", None)
                        else getattr(target_client, "_last_loaded_model_key", None)
                    ),
                    "quantization": (
                        getattr(target_client, "_active_binding", None).quantization
                        if getattr(target_client, "_active_binding", None)
                        else None
                    ),
                },
            )
            checkpoint = chk_store.load_and_validate_checkpoint(
                document=document,
                config_fingerprint=cfg_fp,
                chunks=chunks,
                expected_topic_query=topic,
                expected_analysis_type="detection",
            )
            if checkpoint is not None:
                for s_idx, s_data in checkpoint.serialized_results.items():
                    try:
                        cached_results[int(s_idx)] = deserialize_detection_result_from_checkpoint(s_data)
                    except Exception:
                        pass
                rec_count = len(cached_results)
                rem_count = max(0, len(chunks) - rec_count)
                if rec_count > 0:
                    tracker.phase = f"Risultati precedenti recuperati: {rec_count} chunk | Chunk ancora da elaborare: {rem_count}"

        old_listener = getattr(target_client, "request_listener", None)
        try:
            try:
                target_client.request_listener = tracker
            except Exception:
                pass

            all_chunk_diagnostics: list[ChunkDiagnosticRecord] = []

            if len(chunks) == 1:
                total_units = 1
                completed_count = 1
                failed_count = 0
                det_status = "SUCCESS"
                if 0 in cached_results:
                    result = cached_results[0]
                    tracker.chunk_success(1, record_request=False)
                    tracker.complete("Topic Detection completata da checkpoint: 1/1 blocchi recuperati.")
                    all_chunk_diagnostics.append(
                        ChunkDiagnosticRecord(
                            chunk_index=0,
                            chunk_id=chunks[0].document_id,
                            status=ChunkLifecycleStatus.SUCCESS.value,
                            attempts=1,
                            duration_seconds=0.0,
                            model_id=resolved_model,
                            http_status=200,
                            diagnostic_category="SUCCESS",
                            failure_phase="none",
                            error_detail="",
                            json_valid=True,
                            schema_valid=True,
                            evidence_valid=True,
                            checkpoint_saved=True,
                        )
                    )
                else:
                    tracker.start_chunk(1, phase="Analisi dei messaggi", record_request=False)
                    t_chunk_start = time.perf_counter()
                    chunk_attempts = 1
                    single_from_split = False
                    single_legacy_split = False
                    single_wf_before = int(det_stats.get("checkpoint_write_failures", 0))

                    def _on_single_detect_retry(att: int, reason: str) -> None:
                        nonlocal chunk_attempts
                        chunk_attempts = max(chunk_attempts, att + 1)

                    try:
                        if self._should_presplit_detection(chunks[0], operational_mode, chk_store, doc_fp, cfg_fp, 0):
                            sub_outcomes, split_parent_res, split_diags = self._run_detection_split_core(
                                document=document,
                                chunk=chunks[0],
                                parent_idx=0,
                                tracker_idx=1,
                                total_planned_chunks=1,
                                analyzer=analyzer,
                                topic=topic,
                                strategy=strategy,
                                resolved_model=resolved_model,
                                eff_temp=eff_temp,
                                eff_seed=eff_seed,
                                eff_max_tokens=eff_max_tokens,
                                operational_mode=operational_mode,
                                tracker=tracker,
                                chk_store=chk_store,
                                doc_fp=doc_fp,
                                cfg_fp=cfg_fp,
                                prompt_version=det_prompt_version,
                                origin=det_origin,
                                run_id=run_id,
                                stats=det_stats,
                            )
                            all_chunk_diagnostics.extend(split_diags)
                            if split_parent_res is None:
                                # Almeno un microchunk non validato: il parent NON è certificato (nessun SUCCESS spurio).
                                total_units = len(sub_outcomes)
                                completed_count = sum(1 for o in sub_outcomes if o.status == "SUCCESS")
                                failed_count = total_units - completed_count
                                if completed_count == 0:
                                    raise OperationalAnalysisFailedError(
                                        f"Tutti i {total_units} micro-chunk di Topic Detection sono falliti."
                                    )
                                result = aggregate_topic_detection(
                                    outcomes=sub_outcomes,
                                    topic=topic,
                                    parent_document_id=document.document_id,
                                    expected_chunk_count=total_units,
                                )
                                det_status = "PARTIAL"
                                tracker.chunk_failed(1, "micro-chunk non validati", record_request=False)
                                tracker.partial(
                                    f"Topic Detection parziale tramite micro-chunk ({completed_count}/{total_units} validati)."
                                )
                                raise _DetectionSplitPartial()
                            result = split_parent_res
                            single_from_split = True
                        else:
                            result = analyzer.detect_topic(
                                document=chunks[0],
                                topic=topic,
                                strategy=strategy,
                                model_id=resolved_model,
                                temperature=eff_temp,
                                seed=eff_seed,
                                timeout_seconds=self.timeout_seconds,
                                max_tokens=eff_max_tokens,
                                operational_mode=operational_mode,
                                context_length=self.context_length,
                                on_retry_callback=_on_single_detect_retry,
                            )
                        dur = round(time.perf_counter() - t_chunk_start, 3)
                        tracker.chunk_success(1, record_request=False)
                        tracker.complete("Topic Detection completata.")
                        diag = ChunkDiagnosticRecord(
                            chunk_index=0,
                            chunk_id=chunks[0].document_id,
                            status=ChunkLifecycleStatus.SUCCESS.value,
                            attempts=int(result.metadata.get("attempts", chunk_attempts)),
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=200,
                            finish_reason=result.metadata.get("finish_reason"),
                            prompt_tokens=result.metadata.get("prompt_tokens"),
                            completion_tokens=result.metadata.get("completion_tokens"),
                            total_tokens=result.metadata.get("total_tokens"),
                            diagnostic_category="SUCCESS",
                            failure_phase="none",
                            error_detail="",
                            json_valid=True,
                            schema_valid=True,
                            evidence_valid=True,
                            checkpoint_saved=False,
                        )
                        all_chunk_diagnostics.append(diag)
                        if chk_store is not None and single_from_split:
                            # Parent già persistito (o fallito e contato) dal core a microchunk: nessun secondo salvataggio.
                            diag.checkpoint_saved = (
                                int(det_stats.get("checkpoint_write_failures", 0)) == single_wf_before
                            )
                        if chk_store is not None and not single_from_split:
                            try:
                                eff_origin_val = (
                                    AnalysisOrigin.LIVE_LM_STUDIO.value
                                    if isinstance(target_client, LmStudioClient)
                                    else AnalysisOrigin.MOCK_INSPECTION.value
                                )
                                res_m = dict(result.metadata)
                                res_m["origin"] = eff_origin_val
                                res_m["run_id"] = run_id
                                result = TopicDetectionResult(
                                    topic=result.topic,
                                    decision=result.decision,
                                    evidence_ids=result.evidence_ids,
                                    rationale=result.rationale,
                                    provenance_document_id=result.provenance_document_id,
                                    metadata=res_m,
                                )
                                chk_store.save_chunk(
                                    document_id=document.document_id,
                                    document_fingerprint=doc_fp,
                                    config_fingerprint=cfg_fp,
                                    chunk_idx=0,
                                    result=result,
                                    total_planned_chunks=1,
                                    analysis_type="detection",
                                    model_id=resolved_model,
                                    prompt_version="detection_operational" if operational_mode else "detection",
                                    origin=eff_origin_val,
                                    run_id=run_id,
                                )
                                det_stats["checkpoint_writes_ok"] += 1
                                diag.checkpoint_saved = True
                                chk_store.save_chunk_diagnostic(
                                    document_id=document.document_id,
                                    document_fingerprint=doc_fp,
                                    config_fingerprint=cfg_fp,
                                    diagnostic=diag,
                                    total_planned_chunks=1,
                                    analysis_type="detection",
                                    model_id=resolved_model,
                                    prompt_version="detection_operational" if operational_mode else "detection",
                                )
                            except Exception as chk_err:
                                if not diag.checkpoint_saved:
                                    det_stats["checkpoint_write_failures"] += 1
                                tracker.phase = f"Avviso: Salvataggio checkpoint blocco 1 non riuscito ({type(chk_err).__name__})"
                    except _DetectionSplitPartial:
                        pass  # copertura incompleta già contabilizzata: prosegue con esito PARTIAL
                    except FATAL_BACKEND_ERRORS as fatal_err:
                        dur = round(time.perf_counter() - t_chunk_start, 3)
                        cat, phase, safe_desc = classify_discovery_exception(fatal_err)
                        is_truncation = (
                            isinstance(fatal_err, AiStructuredOutputError)
                            and (
                                getattr(fatal_err, "finish_reason", None) == "length"
                                or "finish_reason=length" in str(fatal_err)
                                or "Unterminated string" in str(fatal_err)
                                or "JSONDecodeError" in str(fatal_err)
                                or "prima del completamento" in str(fatal_err)
                            )
                        )
                        is_context_overflow = (
                            isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                            and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                        )
                        if (is_context_overflow or is_truncation) and len(chunks[0].bundles) > 1:
                            tracker.split_chunk(
                                1,
                                subchunks_count=len(self._plan_detection_subchunks(chunks[0], operational_mode)),
                                failed_prior_request=True,
                                prior_error_msg=type(fatal_err).__name__,
                            )
                            sub_outcomes, _split_parent, split_diags = self._run_detection_split_core(
                                document=document,
                                chunk=chunks[0],
                                parent_idx=0,
                                tracker_idx=1,
                                total_planned_chunks=1,
                                analyzer=analyzer,
                                topic=topic,
                                strategy=strategy,
                                resolved_model=resolved_model,
                                eff_temp=eff_temp,
                                eff_seed=eff_seed,
                                eff_max_tokens=eff_max_tokens,
                                operational_mode=operational_mode,
                                tracker=tracker,
                                chk_store=chk_store,
                                doc_fp=doc_fp,
                                cfg_fp=cfg_fp,
                                prompt_version=det_prompt_version,
                                origin=det_origin,
                                run_id=run_id,
                                stats=det_stats,
                                legacy_chunk_accounting=True,
                            )
                            single_legacy_split = True
                            all_chunk_diagnostics.extend(split_diags)
                            total_units = len(sub_outcomes)
                            completed_count = sum(1 for o in sub_outcomes if o.status == "SUCCESS")
                            failed_count = total_units - completed_count
                            if completed_count == total_units:
                                result = aggregate_topic_detection(
                                    outcomes=sub_outcomes,
                                    topic=topic,
                                    parent_document_id=document.document_id,
                                    expected_chunk_count=total_units,
                                )
                                tracker.complete("Topic Detection completata tramite suddivisione sub-chunk.")
                                det_status = "SUCCESS"
                            elif completed_count > 0:
                                result = aggregate_topic_detection(
                                    outcomes=sub_outcomes,
                                    topic=topic,
                                    parent_document_id=document.document_id,
                                    expected_chunk_count=total_units,
                                )
                                tracker.partial(f"Topic Detection parziale tramite suddivisione sub-chunk ({completed_count}/{total_units} sotto-blocchi completati).")
                                det_status = "PARTIAL"
                            else:
                                tracker.chunk_failed(1, safe_exception_text(fatal_err))
                                tracker.fail(safe_exception_text(fatal_err))
                                raise
                        else:
                            diag = ChunkDiagnosticRecord(
                                chunk_index=0,
                                chunk_id=chunks[0].document_id,
                                status=ChunkLifecycleStatus.FAILED.value,
                                attempts=1,
                                duration_seconds=dur,
                                model_id=resolved_model,
                                http_status=getattr(fatal_err, "status_code", None),
                                error_type=type(fatal_err).__name__,
                                diagnostic_category=cat,
                                failure_phase=phase,
                                error_detail=safe_desc,
                                checkpoint_saved=False,
                            )
                            all_chunk_diagnostics.append(diag)
                            if chk_store is not None:
                                try:
                                    chk_store.save_chunk_diagnostic(
                                        document_id=document.document_id,
                                        document_fingerprint=doc_fp,
                                        config_fingerprint=cfg_fp,
                                        diagnostic=diag,
                                        total_planned_chunks=1,
                                        analysis_type="detection",
                                        model_id=resolved_model,
                                        prompt_version="detection_operational" if operational_mode else "detection",
                                    )
                                except Exception:
                                    pass
                            tracker.chunk_failed(1, safe_exception_text(fatal_err))
                            tracker.fail(safe_exception_text(fatal_err))
                            raise
                    except Exception as exc:
                        dur = round(time.perf_counter() - t_chunk_start, 3)
                        cat, phase, safe_desc = classify_discovery_exception(exc)
                        diag = ChunkDiagnosticRecord(
                            chunk_index=0,
                            chunk_id=chunks[0].document_id,
                            status=ChunkLifecycleStatus.FAILED.value,
                            attempts=1,
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=getattr(exc, "status_code", None),
                            error_type=type(exc).__name__,
                            diagnostic_category=cat,
                            failure_phase=phase,
                            error_detail=safe_desc,
                            checkpoint_saved=False,
                        )
                        all_chunk_diagnostics.append(diag)
                        if chk_store is not None:
                            try:
                                chk_store.save_chunk_diagnostic(
                                    document_id=document.document_id,
                                    document_fingerprint=doc_fp,
                                    config_fingerprint=cfg_fp,
                                    diagnostic=diag,
                                    total_planned_chunks=1,
                                    analysis_type="detection",
                                    model_id=resolved_model,
                                    prompt_version="detection_operational" if operational_mode else "detection",
                                )
                            except Exception:
                                pass
                        tracker.chunk_failed(1, safe_exception_text(exc))
                        tracker.fail(safe_exception_text(exc))
                        raise OperationalAnalysisFailedError(
                            f"Inferenza di Topic Detection fallita sul documento: {safe_exception_text(exc)}"
                        ) from exc

                latency = round(time.perf_counter() - start_time, 3)
                now_ts = datetime.now(timezone.utc).isoformat()
                run_meta = TopicAnalysisRunMetadata(
                    run_id=run_id,
                    origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
                    model=resolved_model,
                    requested_model=(model_name or "").strip() or self.default_model,
                    latency_seconds=latency,
                    prompt_version=result.metadata.get("prompt_version", "topic_detection_operational_v1"),
                    strategy=strategy.value,
                    temperature=eff_temp,
                    seed=eff_seed,
                    chunk_count=int(total_units),
                    completed_chunk_count=int(completed_count),
                    error_count=int(failed_count),
                    failed_chunk_count=int(failed_count),
                    data_classification=classification,
                    timestamp=now_ts,
                    document_id=document.document_id,
                    analysis_type="DETECTION",
                    git_commit=current_git_commit,
                    resolved_model_key=(
                        getattr(target_client, "_active_binding", None).resolved_model_key
                        if getattr(target_client, "_active_binding", None)
                        else (getattr(target_client, "_last_loaded_model_key", None) or (model_name or self.default_model))
                    ),
                    loaded_instance_id=(
                        getattr(target_client, "_loaded_instance_id", None)
                        or (getattr(target_client, "_active_binding", None).loaded_instance_id if getattr(target_client, "_active_binding", None) else None)
                        or "UNKNOWN (not provided by LM Studio)"
                    ),
                    api_identifier=getattr(target_client, "model_id", resolved_model),
                    response_model=result.metadata.get("model") or resolved_model,
                    context_length=self.context_length,
                    max_tokens=eff_max_tokens,
                    start_timestamp=start_iso,
                    end_timestamp=now_ts,
                    status=det_status,
                    error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
                )

                meta = dict(result.metadata)
                meta.update(run_meta.to_dict())
                meta["git_commit"] = current_git_commit
                meta["error_count"] = int(failed_count)
                if not single_legacy_split:
                    # Pre-split: chunk_* = PARENT (qui sempre 1); microchunk_* = unità di inferenza (separate).
                    # Il percorso reattivo storico (single_legacy_split) conserva la contabilità a sotto-blocchi.
                    meta["chunk_count"] = 1
                    meta["completed_chunk_count"] = 1 if det_status == "SUCCESS" else 0
                    meta["failed_chunk_count"] = 0 if det_status == "SUCCESS" else 1
                meta["microchunk_count"] = int(tracker.microchunks_total)
                meta["microchunk_completed_count"] = int(tracker.microchunks_completed)
                meta["microchunk_failed_count"] = int(tracker.microchunks_failed)
                meta["chunk_diagnostics"] = [d.to_dict() for d in all_chunk_diagnostics]
                meta["detection_diagnostics"] = self._detection_diagnostics_summary(tracker, det_stats, 1, 1 if det_status == "SUCCESS" else 0, start_time, all_chunk_diagnostics)
                is_label_only = getattr(topic, "is_label_only", False) or (topic.label.strip().casefold() == topic.description.strip().casefold())
                meta["query_mode"] = "LABEL_ONLY" if is_label_only else "LABEL_AND_DESCRIPTION"
                meta["is_label_only"] = is_label_only
                meta["origin_flow"] = meta.get("origin_flow", "MANUAL_DETECTION")
                meta["input_label"] = topic.label
                meta["input_description"] = topic.description if not is_label_only else None
                if not should_preserve_raw and "raw_response" in meta:
                    del meta["raw_response"]

                return TopicDetectionResult(
                    topic=result.topic,
                    decision=result.decision,
                    evidence_ids=result.evidence_ids,
                    rationale=result.rationale,
                    provenance_document_id=document.document_id,
                    metadata=meta,
                )

            # Multi-chunk: esecuzione chunk-by-chunk con tracking dei fallimenti e progress
            outcomes: list[ChunkAnalysisOutcome] = []
            parent_starts: list[int] = []  # indice in `outcomes` da cui iniziano gli esiti di ciascun parent
            recovered_parent_count = 0  # parent il cui risultato proviene INTERAMENTE da checkpoint validati
            for idx, chunk in enumerate(chunks):
                parent_starts.append(len(outcomes))
                current_tracker_idx = tracker.completed_chunks + tracker.failed_chunks + 1
                chunk_req_before = int(tracker.requests_sent)
                if idx in cached_results:
                    res = cached_results[idx]
                    outcomes.append(
                        ChunkAnalysisOutcome(
                            chunk_id=chunk.document_id,
                            result=res,
                            status="SUCCESS",
                        )
                    )
                    tracker.chunk_success(current_tracker_idx, record_request=False)
                    all_chunk_diagnostics.append(
                        ChunkDiagnosticRecord(
                            chunk_index=idx,
                            chunk_id=chunk.document_id,
                            status=ChunkLifecycleStatus.SUCCESS.value,
                            attempts=1,
                            duration_seconds=0.0,
                            model_id=resolved_model,
                            http_status=200,
                            diagnostic_category="SUCCESS",
                            failure_phase="none",
                            error_detail="",
                            json_valid=True,
                            schema_valid=True,
                            evidence_valid=True,
                            checkpoint_saved=True,
                            # `attempts=1` è il valore storico (segnaposto): la provenienza vera è qui, esplicita.
                            resume_provenance={
                                "result_origin": RESUME_ORIGIN_CHECKPOINT,
                                "via_microchunks": None,
                                "microchunks_recovered": None,
                                "microchunks_computed": None,
                                "microchunks_failed": None,
                                "current_run_http_requests": 0,
                                "original_attempts": None,
                            },
                        )
                    )
                    recovered_parent_count += 1
                    continue

                tracker.start_chunk(current_tracker_idx, phase="Analisi dei messaggi", record_request=False)
                t_chunk_start = time.perf_counter()
                chunk_attempts = 1

                def _on_chunk_detect_retry(att: int, reason: str) -> None:
                    nonlocal chunk_attempts
                    chunk_attempts = max(chunk_attempts, att + 1)

                if self._should_presplit_detection(chunk, operational_mode, chk_store, doc_fp, cfg_fp, idx):
                    # Pre-splitting deterministico (come la Discovery): nessuna chiamata sul parent intero.
                    split_outcomes, _split_ok = self._run_detection_parent_split(
                        all_chunk_diagnostics=all_chunk_diagnostics,
                        document=document,
                        chunk=chunk,
                        parent_idx=idx,
                        tracker_idx=current_tracker_idx,
                        total_planned_chunks=len(chunks),
                        analyzer=analyzer,
                        topic=topic,
                        strategy=strategy,
                        resolved_model=resolved_model,
                        eff_temp=eff_temp,
                        eff_seed=eff_seed,
                        eff_max_tokens=eff_max_tokens,
                        operational_mode=operational_mode,
                        tracker=tracker,
                        chk_store=chk_store,
                        doc_fp=doc_fp,
                        cfg_fp=cfg_fp,
                        prompt_version=det_prompt_version,
                        origin=det_origin,
                        run_id=run_id,
                        stats=det_stats,
                        requests_before=chunk_req_before,
                    )
                    outcomes.extend(split_outcomes)
                    if (all_chunk_diagnostics[-1].resume_provenance or {}).get("result_origin") == RESUME_ORIGIN_CHECKPOINT:
                        recovered_parent_count += 1
                    continue

                try:
                    res = analyzer.detect_topic(
                        document=chunk,
                        topic=topic,
                        strategy=strategy,
                        model_id=resolved_model,
                        temperature=eff_temp,
                        seed=eff_seed,
                        timeout_seconds=self.timeout_seconds,
                        max_tokens=eff_max_tokens,
                        operational_mode=operational_mode,
                        context_length=self.context_length,
                        on_retry_callback=_on_chunk_detect_retry,
                    )
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    outcomes.append(
                        ChunkAnalysisOutcome(
                            chunk_id=chunk.document_id,
                            result=res,
                            status="SUCCESS",
                        )
                    )
                    tracker.chunk_success(current_tracker_idx, record_request=False)
                    _ok_attempts = int(res.metadata.get("attempts", chunk_attempts))
                    _bump(det_stats, "leaf_attempts", _ok_attempts)
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.SUCCESS.value,
                        attempts=int(res.metadata.get("attempts", chunk_attempts)),
                        duration_seconds=dur,
                        model_id=resolved_model,
                        http_status=200,
                        finish_reason=res.metadata.get("finish_reason"),
                        prompt_tokens=res.metadata.get("prompt_tokens"),
                        completion_tokens=res.metadata.get("completion_tokens"),
                        total_tokens=res.metadata.get("total_tokens"),
                        diagnostic_category="SUCCESS",
                        failure_phase="none",
                        error_detail="",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=(chk_store is not None),
                        resume_provenance=self._parent_resume_provenance(
                            recovered=0, computed=1, failed=0,
                            http_delta=int(tracker.requests_sent) - chunk_req_before,
                            computed_leaf_attempts=_ok_attempts, via_microchunks=False,
                        ),
                    )
                    all_chunk_diagnostics.append(diag)
                    if chk_store is not None:
                        try:
                            eff_origin_val = (
                                AnalysisOrigin.LIVE_LM_STUDIO.value
                                if isinstance(target_client, LmStudioClient)
                                else AnalysisOrigin.MOCK_INSPECTION.value
                            )
                            res_m = dict(res.metadata)
                            res_m["origin"] = eff_origin_val
                            res_m["run_id"] = run_id
                            res = TopicDetectionResult(
                                topic=res.topic,
                                decision=res.decision,
                                evidence_ids=res.evidence_ids,
                                rationale=res.rationale,
                                provenance_document_id=res.provenance_document_id,
                                metadata=res_m,
                            )
                            chk_store.save_chunk(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                chunk_idx=idx,
                                result=res,
                                total_planned_chunks=len(chunks),
                                analysis_type="detection",
                                model_id=resolved_model,
                                prompt_version="detection_operational" if operational_mode else "detection",
                                origin=eff_origin_val,
                                run_id=run_id,
                            )
                            chk_store.save_chunk_diagnostic(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                diagnostic=diag,
                                total_planned_chunks=len(chunks),
                                analysis_type="detection",
                                model_id=resolved_model,
                                prompt_version="detection_operational" if operational_mode else "detection",
                            )
                        except Exception as chk_err:
                            tracker.phase = f"Avviso: Salvataggio checkpoint blocco {idx + 1} non riuscito ({type(chk_err).__name__})"
                except FATAL_BACKEND_ERRORS as fatal_err:
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(fatal_err)
                    # Se è un CONTEXT_OVERFLOW o troncamento token recuperabile su chunk con più di un bundle, tenta suddivisione
                    is_truncation = (
                        isinstance(fatal_err, AiStructuredOutputError)
                        and (
                            getattr(fatal_err, "finish_reason", None) == "length"
                            or "finish_reason=length" in str(fatal_err)
                            or "Unterminated string" in str(fatal_err)
                            or "JSONDecodeError" in str(fatal_err)
                            or "prima del completamento" in str(fatal_err)
                            or "degenerazione ciclica" in str(fatal_err)
                        )
                    )
                    is_context_overflow = (
                        isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                        and getattr(fatal_err, "error_category", None) in ("CONTEXT_OVERFLOW", "INFERENCE_BACKEND_ERROR")
                    )
                    if (is_context_overflow or is_truncation) and len(chunk.bundles) > 1:
                        tracker.split_chunk(
                            current_tracker_idx,
                            subchunks_count=len(self._plan_detection_subchunks(chunk, operational_mode)),
                            failed_prior_request=True,
                            prior_error_msg=type(fatal_err).__name__,
                        )
                        split_outcomes, _split_ok = self._run_detection_parent_split(
                            all_chunk_diagnostics=all_chunk_diagnostics,
                            document=document,
                            chunk=chunk,
                            parent_idx=idx,
                            tracker_idx=current_tracker_idx,
                            total_planned_chunks=len(chunks),
                            analyzer=analyzer,
                            topic=topic,
                            strategy=strategy,
                            resolved_model=resolved_model,
                            eff_temp=eff_temp,
                            eff_seed=eff_seed,
                            eff_max_tokens=eff_max_tokens,
                            operational_mode=operational_mode,
                            tracker=tracker,
                            chk_store=chk_store,
                            doc_fp=doc_fp,
                            cfg_fp=cfg_fp,
                            prompt_version=det_prompt_version,
                            origin=det_origin,
                            run_id=run_id,
                            stats=det_stats,
                            legacy_chunk_accounting=True,
                            requests_before=chunk_req_before,
                        )
                        outcomes.extend(split_outcomes)
                    else:
                        _fail_metrics = detection_failure_metrics(fatal_err, len(chunk.bundles))
                        note_detection_node_failure(det_stats, cat, _fail_metrics)
                        _bump(det_stats, "leaf_attempts", int(chunk_attempts))
                        diag = ChunkDiagnosticRecord(
                            chunk_index=idx,
                            chunk_id=chunk.document_id,
                            status=ChunkLifecycleStatus.FAILED.value,
                            attempts=int(chunk_attempts),
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=getattr(fatal_err, "status_code", None),
                            error_type=type(fatal_err).__name__,
                            diagnostic_category=cat,
                            failure_phase=phase,
                            error_detail=safe_desc,
                            checkpoint_saved=False,
                            diagnostic_metrics=_fail_metrics,
                            resume_provenance=self._parent_resume_provenance(
                                recovered=0, computed=0, failed=1,
                                http_delta=int(tracker.requests_sent) - chunk_req_before,
                                computed_leaf_attempts=int(chunk_attempts), via_microchunks=False,
                            ),
                        )
                        all_chunk_diagnostics.append(diag)
                        if chk_store is not None:
                            try:
                                chk_store.save_chunk_diagnostic(
                                    document_id=document.document_id,
                                    document_fingerprint=doc_fp,
                                    config_fingerprint=cfg_fp,
                                    diagnostic=diag,
                                    total_planned_chunks=len(chunks),
                                    analysis_type="detection",
                                    model_id=resolved_model,
                                    prompt_version="detection_operational" if operational_mode else "detection",
                                )
                            except Exception:
                                pass
                        tracker.chunk_failed(current_tracker_idx, safe_exception_text(fatal_err))
                        should_abort = (
                            isinstance(fatal_err, SYSTEMIC_BACKEND_ERRORS)
                            or not operational_mode
                            or (isinstance(fatal_err, AiBackendTimeoutError) and self.enable_checkpointing)
                        )
                        if should_abort:
                            tracker.fail(safe_exception_text(fatal_err))
                            raise
                        outcomes.append(
                            ChunkAnalysisOutcome(
                                chunk_id=chunk.document_id,
                                result=None,
                                status="FAILED",
                                error_type=type(fatal_err).__name__,
                                error_message=safe_exception_text(fatal_err),
                            )
                        )
                except Exception as exc:
                    if not operational_mode or isinstance(exc, PROGRAMMING_ERRORS):
                        tracker.chunk_failed(current_tracker_idx, safe_exception_text(exc))
                        tracker.fail(safe_exception_text(exc))
                        raise
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(exc)
                    _fail_metrics = detection_failure_metrics(exc, len(chunk.bundles))
                    note_detection_node_failure(det_stats, cat, _fail_metrics)
                    _gen_attempts = int(getattr(exc, "attempts", chunk_attempts))
                    _bump(det_stats, "leaf_attempts", _gen_attempts)
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.FAILED.value,
                        attempts=int(getattr(exc, "attempts", chunk_attempts)),
                        duration_seconds=dur,
                        model_id=resolved_model,
                        http_status=getattr(exc, "status_code", None),
                        error_type=type(exc).__name__,
                        diagnostic_category=cat,
                        failure_phase=phase,
                        error_detail=safe_desc,
                        checkpoint_saved=False,
                        diagnostic_metrics=_fail_metrics,
                        resume_provenance=self._parent_resume_provenance(
                            recovered=0, computed=0, failed=1,
                            http_delta=int(tracker.requests_sent) - chunk_req_before,
                            computed_leaf_attempts=_gen_attempts, via_microchunks=False,
                        ),
                    )
                    all_chunk_diagnostics.append(diag)
                    if chk_store is not None:
                        try:
                            chk_store.save_chunk_diagnostic(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                diagnostic=diag,
                                total_planned_chunks=len(chunks),
                                analysis_type="detection",
                                model_id=resolved_model,
                                prompt_version="detection_operational" if operational_mode else "detection",
                            )
                        except Exception:
                            pass
                    tracker.chunk_failed(current_tracker_idx, safe_exception_text(exc))
                    outcomes.append(
                        ChunkAnalysisOutcome(
                            chunk_id=chunk.document_id,
                            result=None,
                            status="FAILED",
                            error_type=type(exc).__name__,
                            error_message=safe_exception_text(exc),
                        )
                    )
        finally:
            try:
                target_client.request_listener = old_listener
            except Exception:
                pass

        total_units = len(outcomes)
        completed_count = sum(1 for o in outcomes if o.status == "SUCCESS")
        failed_count = total_units - completed_count
        # Contabilità SEPARATA: parent pianificati (len(chunks)) vs unità di inferenza (microchunk/sotto-blocchi).
        _bounds = parent_starts + [len(outcomes)]
        parent_total = len(chunks)
        parent_completed = sum(
            1
            for _i in range(len(parent_starts))
            if _bounds[_i + 1] > _bounds[_i] and all(o.status == "SUCCESS" for o in outcomes[_bounds[_i]:_bounds[_i + 1]])
        )
        if completed_count == 0:
            tracker.fail(f"Tutti i {total_units} blocchi di Topic Detection sono falliti.")
            raise OperationalAnalysisFailedError(
                f"Tutti i {total_units} chunk di Topic Detection sono falliti durante l'inferenza."
            )

        tracker.start_consolidation()
        aggregated = aggregate_topic_detection(
            outcomes=outcomes,
            topic=topic,
            parent_document_id=document.document_id,
            expected_chunk_count=total_units,
        )
        if failed_count == 0:
            tracker.complete(f"Topic Detection completata: {completed_count}/{total_units} blocchi analizzati.")
            det_status = "SUCCESS"
            # INVARIANTE DI PERSISTENZA (revisione PR #1): NON cancellare i checkpoint completati.
            # clear_checkpoint() rimuoveva a cascata checkpoints, checkpoint_chunks e checkpoint_subchunks:
            # dopo un SUCCESS multi-chunk non restava più nulla per ricarico/riavvio/audit. Come per la Discovery,
            # i checkpoint SQLite restano l'archivio autorevole; la cancellazione è solo esplicita e manuale.
        else:
            tracker.partial(f"Topic Detection completata parzialmente: {completed_count}/{total_units} blocchi analizzati.")
            det_status = "PARTIAL"

        latency = round(time.perf_counter() - start_time, 3)
        now_ts = datetime.now(timezone.utc).isoformat()
        is_live_lms_final = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if is_live_lms_final else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version=aggregated.metadata.get("prompt_version", "topic_detection_operational_v2"),
            strategy=strategy.value,
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(total_units),
            completed_chunk_count=int(completed_count),
            error_count=int(failed_count),
            failed_chunk_count=int(failed_count),
            data_classification=classification,
            timestamp=now_ts,
            document_id=document.document_id,
            analysis_type="DETECTION",
            git_commit=current_git_commit,
            resolved_model_key=(
                getattr(target_client, "_active_binding", None).resolved_model_key
                if getattr(target_client, "_active_binding", None)
                else (getattr(target_client, "_last_loaded_model_key", None) or (model_name or self.default_model))
            ),
            loaded_instance_id=(
                getattr(target_client, "_loaded_instance_id", None)
                or (getattr(target_client, "_active_binding", None).loaded_instance_id if getattr(target_client, "_active_binding", None) else None)
                or "UNKNOWN (not provided by LM Studio)"
            ),
            api_identifier=getattr(target_client, "model_id", resolved_model),
            response_model=aggregated.metadata.get("model") or resolved_model,
            context_length=self.context_length,
            max_tokens=eff_max_tokens,
            start_timestamp=start_iso,
            end_timestamp=now_ts,
            status=det_status,
            error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
        )

        meta = dict(aggregated.metadata)
        meta.update(run_meta.to_dict())
        meta["git_commit"] = current_git_commit
        meta["error_count"] = int(failed_count)
        # chunk_* = PARENT pianificati (es. 26), mai il numero di microchunk; microchunk_* = unità di inferenza.
        meta["chunk_count"] = int(parent_total)
        meta["completed_chunk_count"] = int(parent_completed)
        meta["failed_chunk_count"] = int(parent_total - parent_completed)
        # Parent il cui risultato proviene interamente da checkpoint validati (prima era sempre assente => 0).
        meta["recovered_chunk_count"] = int(recovered_parent_count)
        meta["microchunk_count"] = int(tracker.microchunks_total)
        meta["microchunk_completed_count"] = int(tracker.microchunks_completed)
        meta["microchunk_failed_count"] = int(tracker.microchunks_failed)
        meta["subchunks_used"] = bool(tracker.microchunks_total > 0)
        if det_status == "PARTIAL":
            meta["is_partial_detection"] = True
            meta["coverage_status"] = "PARTIAL"
        meta["chunk_diagnostics"] = [d.to_dict() for d in all_chunk_diagnostics]
        meta["detection_diagnostics"] = self._detection_diagnostics_summary(tracker, det_stats, parent_total, parent_completed, start_time, all_chunk_diagnostics)
        is_label_only = getattr(topic, "is_label_only", False) or (topic.label.strip().casefold() == topic.description.strip().casefold())
        meta["query_mode"] = "LABEL_ONLY" if is_label_only else "LABEL_AND_DESCRIPTION"
        meta["is_label_only"] = is_label_only
        meta["origin_flow"] = meta.get("origin_flow", "MANUAL_DETECTION")
        meta["input_label"] = topic.label
        meta["input_description"] = topic.description if not is_label_only else None
        if not should_preserve_raw and "raw_response" in meta:
            del meta["raw_response"]

        return TopicDetectionResult(
            topic=aggregated.topic,
            decision=aggregated.decision,
            evidence_ids=aggregated.evidence_ids,
            rationale=aggregated.rationale,
            provenance_document_id=document.document_id,
            metadata=meta,
        )

    def run_topic_discovery(
        self,
        document: ConversationEvidenceDocument,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
        max_topics: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        preserve_raw_response: bool = False,
        progress_callback: AnalysisProgressCallback | None = None,
        retry_failed_only: bool = False,
        tracker: AnalysisProgressTracker | None = None,
    ) -> TopicDiscoveryResult:
        """
        Esegue Topic Discovery LIVE con supporto trasparente al chunking multi-chunk e progress callback.
        """
        if not document.bundles or not document.all_evidence_sections:
            raise AnalysisNoEvidenceError(
                f"Il documento '{document.document_id}' non contiene sezioni di evidenza analizzabili."
            )

        total_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}
        total_evidence_count = len(total_evidence_ids)
        analyzed_evidence_ids: set[str] = set()

        eff_max_topics = max_topics if max_topics is not None else self.config.max_topics
        eff_temp = temperature if temperature is not None else self.config.temperature
        eff_seed = seed if seed is not None else self.config.seed

        run_id = f"run_disc_{uuid.uuid4().hex[:10]}"
        start_time = time.perf_counter()
        start_iso = datetime.now(timezone.utc).isoformat()
        current_git_commit = get_git_commit()

        target_client, resolved_model = self._prepare_client_and_model(client, model_name)

        analyzer = TopicDiscoveryAnalyzer(
            client=target_client,
            default_model_id=resolved_model,
            operational_mode=True,
        )

        eff_max_tokens = calculate_discovery_max_tokens(
            max_topics=eff_max_topics,
            user_max_tokens=max_tokens,
            context_length=self.context_length,
        )

        plan = self.create_analysis_plan(
            document=document,
            analysis_type="discovery",
            max_topics=eff_max_topics,
            user_max_tokens=max_tokens,
            context_length=self.context_length,
        )
        chunks = list(plan.chunks)
        eff_chunk_tokens = plan.chunk_token_budget

        if tracker is None:
            tracker = AnalysisProgressTracker(
                total_chunks=len(chunks),
                callback=progress_callback,
                initial_phase="Analisi dei messaggi",
            )

        is_synthetic = self._is_synthetic_document(document)
        should_preserve_raw = preserve_raw_response or is_synthetic
        classification = "SYNTHETIC_DEMO" if is_synthetic else "REAL_FORENSIC"

        # Checkpoint: caricamento risultati validati pregressi se abilitato
        chk_store = self.checkpoint_store if self.enable_checkpointing else None
        doc_fp = ""
        cfg_fp = ""
        cached_discovery_results: dict[int, TopicDiscoveryResult] = {}
        if chk_store is not None:
            doc_fp = compute_document_fingerprint(document)
            eff_run_cfg = OperationalAnalysisConfig(
                model_spec=resolved_model,
                context_length=self.context_length,
                timeout_seconds=self.timeout_seconds,
                max_tokens=eff_max_tokens,
                temperature=eff_temp,
                seed=eff_seed,
                profile=self.profile,
                chunk_character_budget=self.max_chunk_characters,
                max_chunk_bundles=self.max_chunk_bundles,
                max_chunk_tokens=eff_chunk_tokens,
            )
            cfg_fp = compute_run_config_hash(
                model_id=resolved_model,
                prompt_version="discovery_operational",
                config=eff_run_cfg,
                chunks=chunks,
                extra_params={
                    "max_topics": eff_max_topics,
                    # Policy di accettazione (comportamentale): fa parte dell'identità della run, quindi i checkpoint
                    # creati con la policy storica (v1) NON vengono riusati con la v2.
                    "acceptance_policy": DEGENERATION_POLICY_STRUCTURAL,
                    "resolved_model_key": (
                        getattr(target_client, "_active_binding", None).resolved_model_key
                        if getattr(target_client, "_active_binding", None)
                        else getattr(target_client, "_last_loaded_model_key", None)
                    ),
                    "quantization": (
                        getattr(target_client, "_active_binding", None).quantization
                        if getattr(target_client, "_active_binding", None)
                        else None
                    ),
                },
            )
            checkpoint = chk_store.load_and_validate_checkpoint(
                document=document,
                config_fingerprint=cfg_fp,
                chunks=chunks,
                expected_analysis_type="discovery",
            )
            if checkpoint is not None:
                for s_idx, s_data in checkpoint.serialized_results.items():
                    try:
                        cached_discovery_results[int(s_idx)] = deserialize_discovery_result_from_checkpoint(s_data)
                    except Exception:
                        pass
                rec_count = len(cached_discovery_results)
                rem_count = max(0, len(chunks) - rec_count)
                if rec_count > 0:
                    tracker.phase = f"Risultati precedenti recuperati: {rec_count} chunk | Chunk ancora da elaborare: {rem_count}"

        # Validazione e gestione semantica rigorosa di retry_failed_only
        if retry_failed_only:
            if chk_store is None:
                raise OperationalAnalysisFailedError(
                    "Impossibile eseguire la riprova selettiva: il sistema di checkpointing non è abilitato o configurato. "
                    "È necessario avviare una nuova Topic Discovery completa."
                )
            if checkpoint is None:
                raise OperationalAnalysisFailedError(
                    f"Impossibile eseguire la riprova selettiva: nessun checkpoint compatibile trovato per il documento "
                    f"'{document.document_id}' (fingerprint: {doc_fp[:8] if doc_fp else 'N/D'}) "
                    f"con la configurazione richiesta (model={resolved_model}). "
                    f"È necessario avviare una nuova Topic Discovery completa."
                )
            existing_all_subchunks = chk_store.get_subchunks(doc_fp, cfg_fp) if chk_store else []
            if not cached_discovery_results and not existing_all_subchunks:
                raise OperationalAnalysisFailedError(
                    f"Impossibile eseguire la riprova selettiva: il checkpoint per il documento '{document.document_id}' "
                    f"non contiene alcun blocco o sotto-blocco precedentemente validato da recuperare. "
                    f"È necessario avviare una nuova Topic Discovery completa."
                )
            succeeded_indices = set(cached_discovery_results.keys())
            missing_indices = [i for i in range(len(chunks)) if i not in succeeded_indices]
            if not missing_indices:
                tracker.phase = f"Tutti i {len(chunks)} blocchi risultano già validati nel checkpoint. Consolidamento in corso..."
            else:
                missing_chunks_display = [i + 1 for i in missing_indices]
                tracker.phase = (
                    f"Piano di recupero selettivo: {len(succeeded_indices)}/{len(chunks)} blocchi validati da checkpoint, "
                    f"{len(missing_indices)} blocchi mancanti da elaborare ({missing_chunks_display})."
                )

        old_listener = getattr(target_client, "request_listener", None)
        try:
            try:
                target_client.request_listener = tracker
            except Exception:
                pass

            if len(chunks) == 1:
                total_units = 1
                completed_count = 1
                failed_count = 0
                disc_status = "COMPLETE"
                recovered_chunk_count = 0
                chunk_outcomes: list[DiscoveryChunkOutcome] = []
                all_chunk_diagnostics: list[ChunkDiagnosticRecord] = []
                if 0 in cached_discovery_results:
                    disc_res = cached_discovery_results[0]
                    recovered_chunk_count = 1
                    analyzed_evidence_ids.update(total_evidence_ids)
                    diag = ChunkDiagnosticRecord(
                        chunk_index=0,
                        chunk_id=chunks[0].document_id,
                        status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
                        attempts=0,
                        duration_seconds=0.0,
                        model_id=resolved_model,
                        http_status=None,
                        finish_reason=disc_res.metadata.get("finish_reason"),
                        prompt_tokens=disc_res.metadata.get("prompt_tokens"),
                        completion_tokens=disc_res.metadata.get("completion_tokens"),
                        error_type=None,
                        diagnostic_category="NONE",
                        failure_phase="recovered",
                        error_detail="Recuperato con successo da checkpoint SQLite",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=True,
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunks[0].document_id,
                            status="SUCCESS",
                            result=disc_res,
                            evidence_ids=tuple(total_evidence_ids),
                            diagnostic=diag,
                            attempts=0,
                            duration_seconds=0.0,
                        )
                    )
                    tracker.chunk_success(1, record_request=False)
                    tracker.complete("Topic Discovery completata da checkpoint: 1/1 blocchi recuperati.")
                else:
                    existing_sub0 = chk_store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=0) if chk_store else []
                    substantive_count0 = sum(1 for b in chunks[0].bundles if is_substantive_message_bundle(b))
                    if (substantive_count0 > 8 or existing_sub0) and len(chunks[0].bundles) > 1:
                        planned_subs = split_chunk_into_microchunks(chunks[0], max_substantive_messages=8)
                        total_planned_subs = len(planned_subs) if len(planned_subs) > 1 else 2
                        # I sotto-blocchi sono contati dai contatori microchunk_*: il totale dei blocchi principali non cresce.
                        is_live_lms = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
                        eff_origin_s = AnalysisOrigin.LIVE_LM_STUDIO.value if is_live_lms else AnalysisOrigin.MOCK_INSPECTION.value
                        sub_outs, sub_res = self._process_subchunks_discovery(
                            chunk=chunks[0],
                            analyzer=analyzer,
                            resolved_model=resolved_model,
                            eff_temp=eff_temp,
                            eff_seed=eff_seed,
                            chunk_target_topics=eff_max_topics,
                            tracker=tracker,
                            chunk_idx_1based=1,
                            chk_store=chk_store,
                            doc_fp=doc_fp,
                            cfg_fp=cfg_fp,
                            parent_chunk_index=0,
                            parent_document_id=document.document_id,
                            total_planned_chunks=1,
                            eff_max_tokens=eff_max_tokens,
                            origin=eff_origin_s,
                            run_id=run_id,
                        )
                        chunk_outcomes.extend(sub_outs)
                        for so in sub_outs:
                            if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"):
                                analyzed_evidence_ids.update(so.evidence_ids)
                            if so.diagnostic:
                                all_chunk_diagnostics.append(so.diagnostic)
                        total_units = len(sub_outs)
                        completed_count = len(sub_res)
                        failed_count = total_units - completed_count
                        if completed_count == total_units:
                            disc_res = aggregate_topic_discovery(
                                chunk_results=sub_res,
                                parent_document_id=document.document_id,
                                max_topics=eff_max_topics,
                            )
                            tracker.chunk_success(1, record_request=False)
                            if len(disc_res.topics) > 0:
                                tracker.complete("Topic Discovery completata tramite suddivisione sub-chunk.")
                                disc_status = "COMPLETE"
                            elif total_evidence_count >= 10:
                                tracker.complete("Topic Discovery completata tecnicamente ma tematicamente non verificata: 0 argomenti emersi (sub-chunk).", thematic_unverified=True)
                                disc_status = "COMPLETE_EMPTY_UNVERIFIED"
                            else:
                                tracker.complete("Topic Discovery completata: nessun argomento emerso.", no_topics=True)
                                disc_status = "COMPLETE_NO_TOPICS"
                        elif completed_count > 0:
                            disc_res = aggregate_topic_discovery(
                                chunk_results=sub_res,
                                parent_document_id=document.document_id,
                                max_topics=eff_max_topics,
                            )
                            tracker.chunk_failed(1, "micro-chunk non validati", record_request=False)
                            tracker.partial(f"Topic Discovery parziale tramite suddivisione sub-chunk ({completed_count}/{total_units} sotto-blocchi completati).")
                            disc_status = "PARTIAL"
                        else:
                            tracker.chunk_failed(1, "Elaborazione sotto-blocchi fallita")
                            tracker.fail("Elaborazione sotto-blocchi fallita")
                            raise OperationalAnalysisFailedError("Tutti i sotto-blocchi sono falliti")
                    else:
                        tracker.start_chunk(1, phase="Analisi dei messaggi", record_request=False)
                        t_single_start = time.perf_counter()
                        try:
                            disc_res = analyzer.discover_topics(
                                document=chunks[0],
                                model_id=resolved_model,
                                temperature=eff_temp,
                                seed=eff_seed,
                                timeout_seconds=self.timeout_seconds,
                                max_topics=eff_max_topics,
                                max_tokens=eff_max_tokens,
                                operational_mode=True,
                                context_length=self.context_length,
                                on_retry_callback=lambda att, r: tracker.record_retry(r),
                            )
                            dur = round(time.perf_counter() - t_single_start, 3)
                            analyzed_evidence_ids.update(total_evidence_ids)
                            tracker.chunk_success(1, record_request=False)
                            num_extracted = len(disc_res.topics)
                            if num_extracted > 0:
                                tracker.complete(f"Topic Discovery completata: emersi {num_extracted} argomenti.")
                                disc_status = "COMPLETE"
                            else:
                                is_substantial = total_evidence_count >= 10
                                if is_substantial:
                                    tracker.complete("Topic Discovery completata tecnicamente ma tematicamente non verificata: 0 argomenti emersi.", thematic_unverified=True)
                                    disc_status = "COMPLETE_EMPTY_UNVERIFIED"
                                else:
                                    tracker.complete("Topic Discovery completata: nessun argomento emerso.", no_topics=True)
                                    disc_status = "COMPLETE_NO_TOPICS"
                            chk_saved = False
                            eff_origin_val = (
                                AnalysisOrigin.LIVE_LM_STUDIO.value
                                if isinstance(target_client, LmStudioClient)
                                else AnalysisOrigin.MOCK_INSPECTION.value
                            )
                            disc_m = dict(disc_res.metadata)
                            disc_m["origin"] = eff_origin_val
                            disc_m["run_id"] = run_id
                            disc_res = TopicDiscoveryResult(
                                topics=disc_res.topics,
                                provenance_document_id=disc_res.provenance_document_id,
                                metadata=disc_m,
                            )
                            if chk_store is not None:
                                try:
                                    chk_store.save_chunk(
                                        document_id=document.document_id,
                                        document_fingerprint=doc_fp,
                                        config_fingerprint=cfg_fp,
                                        chunk_idx=0,
                                        result=disc_res,
                                        total_planned_chunks=1,
                                        analysis_type="discovery",
                                        model_id=resolved_model,
                                        prompt_version="discovery_operational",
                                        origin=eff_origin_val,
                                        run_id=run_id,
                                    )
                                    chk_saved = True
                                except Exception as chk_err:
                                    tracker.phase = f"Avviso: Salvataggio checkpoint blocco 1 non riuscito ({type(chk_err).__name__})"

                            thematic_st = "TOPICS_EXTRACTED" if num_extracted > 0 else "EMPTY_TOPICS"
                            diag = ChunkDiagnosticRecord(
                                chunk_index=0,
                                chunk_id=chunks[0].document_id,
                                status=ChunkLifecycleStatus.SUCCESS.value,
                                thematic_status=thematic_st,
                                attempts=int(disc_res.metadata.get("retries", 0)) + 1,
                                duration_seconds=dur,
                                model_id=str(disc_res.metadata.get("model") or resolved_model),
                                http_status=200,
                                finish_reason=disc_res.metadata.get("finish_reason"),
                                prompt_tokens=disc_res.metadata.get("prompt_tokens"),
                                completion_tokens=disc_res.metadata.get("completion_tokens"),
                                error_type=None,
                                diagnostic_category="NONE",
                                failure_phase="completed" if num_extracted > 0 else "empty_output",
                                error_detail=None if num_extracted > 0 else "Elaborato con successo ma senza argomenti estratti",
                                json_valid=True,
                                schema_valid=True,
                                evidence_valid=True,
                                checkpoint_saved=chk_saved,
                                topics_count=num_extracted,
                            )
                            all_chunk_diagnostics.append(diag)
                            chunk_outcomes.append(
                                DiscoveryChunkOutcome(
                                    chunk_id=chunks[0].document_id,
                                    status="SUCCESS",
                                    thematic_status=thematic_st,
                                    result=disc_res,
                                    evidence_ids=tuple(total_evidence_ids),
                                    diagnostic=diag,
                                    attempts=diag.attempts,
                                    duration_seconds=dur,
                                )
                            )
                            if chk_store is not None:
                                try:
                                    chk_store.save_chunk_diagnostic(
                                        document_id=document.document_id,
                                        document_fingerprint=doc_fp,
                                        config_fingerprint=cfg_fp,
                                        diagnostic=diag,
                                        total_planned_chunks=1,
                                        analysis_type="discovery",
                                        model_id=resolved_model,
                                        prompt_version="discovery_operational",
                                    )
                                except Exception as diag_err:
                                    logger.warning("Salvataggio diagnostica blocco 1 non riuscito: %s", diag_err)
                                    tracker.phase = f"Avviso: Salvataggio diagnostica blocco 1 su SQLite non riuscito ({type(diag_err).__name__})"
                        except FATAL_BACKEND_ERRORS as fatal_err:
                            is_truncation = (
                                isinstance(fatal_err, AiStructuredOutputError)
                                and (
                                    getattr(fatal_err, "finish_reason", None) == "length"
                                    or "finish_reason=length" in str(fatal_err)
                                    or "Unterminated string" in str(fatal_err)
                                    or "JSONDecodeError" in str(fatal_err)
                                    or "prima del completamento" in str(fatal_err)
                                )
                            )
                            is_context_overflow = (
                                isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                                and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                            )
                            if (is_context_overflow or is_truncation) and len(chunks[0].bundles) > 1:
                                tracker.split_chunk(1, subchunks_count=2, failed_prior_request=True, prior_error_msg=type(fatal_err).__name__, grow_total=False)
                                is_live_lms_s = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
                                eff_origin_s = AnalysisOrigin.LIVE_LM_STUDIO.value if is_live_lms_s else AnalysisOrigin.MOCK_INSPECTION.value
                                sub_outs, sub_res = self._process_subchunks_discovery(
                                    chunk=chunks[0],
                                    analyzer=analyzer,
                                    resolved_model=resolved_model,
                                    eff_temp=eff_temp,
                                    eff_seed=eff_seed,
                                    chunk_target_topics=eff_max_topics,
                                    tracker=tracker,
                                    chunk_idx_1based=1,
                                    chk_store=chk_store,
                                    doc_fp=doc_fp,
                                    cfg_fp=cfg_fp,
                                    parent_chunk_index=0,
                                    parent_document_id=document.document_id,
                                    total_planned_chunks=1,
                                    eff_max_tokens=eff_max_tokens,
                                    origin=eff_origin_s,
                                    run_id=run_id,
                                )
                                chunk_outcomes.extend(sub_outs)
                                for so in sub_outs:
                                    if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"):
                                        analyzed_evidence_ids.update(so.evidence_ids)
                                    if so.diagnostic:
                                        all_chunk_diagnostics.append(so.diagnostic)
                                total_units = len(sub_outs)
                                completed_count = len(sub_res)
                                failed_count = total_units - completed_count
                                # Il parent e' SUCCESS solo se tutti i microchunk sono validati (stessa regola del pre-split).
                                if completed_count == total_units:
                                    tracker.chunk_success(1, record_request=False)
                                elif completed_count > 0:
                                    tracker.chunk_failed(1, "micro-chunk non validati", record_request=False)
                                if completed_count == total_units:
                                    disc_res = aggregate_topic_discovery(
                                        chunk_results=sub_res,
                                        parent_document_id=document.document_id,
                                        max_topics=eff_max_topics,
                                    )
                                    if len(disc_res.topics) > 0:
                                        tracker.complete("Topic Discovery completata tramite suddivisione sub-chunk.")
                                        disc_status = "COMPLETE"
                                    elif total_evidence_count >= 10:
                                        tracker.complete("Topic Discovery completata tecnicamente ma tematicamente non verificata: 0 argomenti emersi (sub-chunk).", thematic_unverified=True)
                                        disc_status = "COMPLETE_EMPTY_UNVERIFIED"
                                    else:
                                        tracker.complete("Topic Discovery completata: nessun argomento emerso.", no_topics=True)
                                        disc_status = "COMPLETE_NO_TOPICS"
                                elif completed_count > 0:
                                    disc_res = aggregate_topic_discovery(
                                        chunk_results=sub_res,
                                        parent_document_id=document.document_id,
                                        max_topics=eff_max_topics,
                                    )
                                    tracker.partial(f"Topic Discovery parziale tramite suddivisione sub-chunk ({completed_count}/{total_units} sotto-blocchi completati).")
                                    disc_status = "PARTIAL"
                                else:
                                    tracker.chunk_failed(1, str(fatal_err))
                                    tracker.fail(str(fatal_err))
                                    raise
                            else:
                                tracker.chunk_failed(1, str(fatal_err))
                                tracker.fail(str(fatal_err))
                                raise
                        except Exception as exc:
                            dur = round(time.perf_counter() - t_single_start, 3)
                            cat, phase, safe_desc = classify_discovery_exception(exc)
                            is_val_err = isinstance(exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                            tracker.chunk_failed(1, str(exc), is_protocol_or_validation=is_val_err)
                            tracker.fail(str(exc))
                            raise OperationalAnalysisFailedError(
                                f"Inferenza di Topic Discovery fallita sul documento: {exc}"
                            ) from exc

                latency = round(time.perf_counter() - start_time, 3)
                now_ts = datetime.now(timezone.utc).isoformat()
                is_live_lms_single = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
                has_unverified_recovered_single = False
                recovered_origins_single: set[str] = set()
                if recovered_chunk_count > 0 and 0 in cached_discovery_results:
                    c0_orig = (cached_discovery_results[0].metadata.get("origin") if isinstance(cached_discovery_results[0].metadata, Mapping) else None) or ""
                    recovered_origins_single.add(c0_orig)
                    if c0_orig != AnalysisOrigin.LIVE_LM_STUDIO.value:
                        has_unverified_recovered_single = True
                for co in chunk_outcomes:
                    if co.status == "RECOVERED_FROM_CHECKPOINT":
                        co_orig = (co.result.metadata.get("origin") if co.result and isinstance(co.result.metadata, Mapping) else None) or ""
                        recovered_origins_single.add(co_orig)
                        if co_orig != AnalysisOrigin.LIVE_LM_STUDIO.value:
                            has_unverified_recovered_single = True
                is_verified_live_single = is_live_lms_single and not has_unverified_recovered_single
                run_meta = TopicAnalysisRunMetadata(
                    run_id=run_id,
                    origin=AnalysisOrigin.LIVE_LM_STUDIO if is_verified_live_single else AnalysisOrigin.MOCK_INSPECTION,
                    model=resolved_model,
                    requested_model=(model_name or "").strip() or self.default_model,
                    latency_seconds=latency,
                    prompt_version=disc_res.metadata.get("prompt_version", "topic_discovery_operational_v2"),
                    strategy="DIRECT_MULTILINGUAL",
                    temperature=eff_temp,
                    seed=eff_seed,
                    chunk_count=int(total_units),
                    completed_chunk_count=int(completed_count),
                    error_count=int(failed_count),
                    failed_chunk_count=int(failed_count),
                    reduction_strategy="none",
                    data_classification=classification,
                    timestamp=now_ts,
                    document_id=document.document_id,
                    analysis_type="DISCOVERY",
                    git_commit=current_git_commit,
                    resolved_model_key=(
                        getattr(target_client, "_active_binding", None).resolved_model_key
                        if getattr(target_client, "_active_binding", None)
                        else (getattr(target_client, "_last_loaded_model_key", None) or (model_name or self.default_model))
                    ),
                    loaded_instance_id=(
                        getattr(target_client, "_loaded_instance_id", None)
                        or (getattr(target_client, "_active_binding", None).loaded_instance_id if getattr(target_client, "_active_binding", None) else None)
                        or "UNKNOWN (not provided by LM Studio)"
                    ),
                    api_identifier=getattr(target_client, "model_id", resolved_model),
                    response_model=disc_res.metadata.get("model") or resolved_model,
                    context_length=self.context_length,
                    max_tokens=eff_max_tokens,
                    start_timestamp=start_iso,
                    end_timestamp=now_ts,
                    status=disc_status,
                    error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
                    original_chunk_count=1,
                    microchunk_count=int(len(all_chunk_diagnostics)),
                )

                meta = dict(disc_res.metadata)
                meta.update(run_meta.to_dict())
                meta["git_commit"] = current_git_commit
                meta["original_chunk_count"] = 1
                meta["microchunk_count"] = int(len(all_chunk_diagnostics))
                meta["error_count"] = int(failed_count)
                meta["chunk_count"] = int(total_units)
                meta["expected_chunk_count"] = int(total_units)
                meta["completed_chunk_count"] = int(completed_count)
                meta["failed_chunk_count"] = int(failed_count)
                meta["recovered_chunk_count"] = int(recovered_chunk_count)
                meta["status"] = disc_status
                meta["chunk_outcomes"] = [o.to_dict() for o in chunk_outcomes]
                meta["chunk_diagnostics"] = [d.to_dict() for d in all_chunk_diagnostics]
                meta["total_evidence_count"] = int(total_evidence_count)
                meta["analyzed_evidence_count"] = int(len(analyzed_evidence_ids))
                meta["unanalyzed_evidence_count"] = max(0, int(total_evidence_count - len(analyzed_evidence_ids)))
                meta["evidence_citation_protocol"] = "deterministic_alias_v1"
                meta["acceptance_policy"] = DEGENERATION_POLICY_STRUCTURAL
                if is_verified_live_single:
                    meta["provenance_verified"] = True
                    meta["authoritative_execution_certified"] = True
                    meta["chain_of_custody_intact"] = True
                elif is_live_lms_single and has_unverified_recovered_single:
                    meta["origin"] = (
                        AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value
                        if AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value in recovered_origins_single
                        else None
                    )
                    meta["provenance_verified"] = False
                    meta["authoritative_execution_certified"] = False
                    meta["chain_of_custody_intact"] = False
                    meta["attestation_level"] = (
                        AttestationLevel.PROVENANCE_NOT_VERIFIED.value
                        if AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value in recovered_origins_single
                        else AttestationLevel.UNATTESTED.value
                    )
                if not should_preserve_raw and "raw_response" in meta:
                    del meta["raw_response"]

                final_res = TopicDiscoveryResult(
                    topics=disc_res.topics,
                    provenance_document_id=document.document_id,
                    metadata=meta,
                )
                if is_verified_live_single:
                    self._authoritative_live_run_ids.add(run_id)
                self._record_authoritative_discovery_run(document.document_id, final_res)
                return final_res

            # Multi-chunk discovery con tracking esplicito
            chunk_outcomes: list[DiscoveryChunkOutcome] = []
            successful_results: list[TopicDiscoveryResult] = []
            all_chunk_diagnostics: list[ChunkDiagnosticRecord] = []
            recovered_chunk_count = 0

            chunk_target_topics = min(eff_max_topics, max(5, min(8, eff_max_topics)))

            for idx, chunk in enumerate(chunks):
                current_tracker_idx = tracker.completed_chunks + tracker.failed_chunks + 1
                chunk_eids = {s.evidence_id for s in chunk.all_evidence_sections}
                if idx in cached_discovery_results:
                    res = cached_discovery_results[idx]
                    num_extracted = len(res.topics)
                    thematic_st = "TOPICS_EXTRACTED" if num_extracted > 0 else "EMPTY_TOPICS"
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
                        thematic_status=thematic_st,
                        attempts=0,
                        duration_seconds=0.0,
                        model_id=resolved_model,
                        http_status=None,
                        finish_reason=res.metadata.get("finish_reason"),
                        prompt_tokens=res.metadata.get("prompt_tokens"),
                        completion_tokens=res.metadata.get("completion_tokens"),
                        error_type=None,
                        diagnostic_category="NONE",
                        failure_phase="recovered",
                        error_detail="Recuperato con successo da checkpoint SQLite" if num_extracted > 0 else "Recuperato da checkpoint SQLite (output tematico vuoto)",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=True,
                        topics_count=num_extracted,
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunk.document_id,
                            status="RECOVERED_FROM_CHECKPOINT",
                            thematic_status=thematic_st,
                            result=res,
                            evidence_ids=tuple(chunk_eids),
                            diagnostic=diag,
                            attempts=0,
                            duration_seconds=0.0,
                        )
                    )
                    successful_results.append(res)
                    analyzed_evidence_ids.update(chunk_eids)
                    recovered_chunk_count += 1
                    tracker.chunk_success(current_tracker_idx, record_request=False)
                    continue

                existing_subs = chk_store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=idx) if chk_store else []
                substantive_count = sum(1 for b in chunk.bundles if is_substantive_message_bundle(b))
                if (substantive_count > 8 or existing_subs) and len(chunk.bundles) > 1:
                    planned_subs = split_chunk_into_microchunks(chunk, max_substantive_messages=8)
                    total_planned_subs = len(planned_subs) if len(planned_subs) > 1 else 2
                    # I sotto-blocchi sono contati dai contatori microchunk_*: il totale dei blocchi principali non cresce.
                    is_live_lms = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
                    eff_origin_s = AnalysisOrigin.LIVE_LM_STUDIO.value if is_live_lms else AnalysisOrigin.MOCK_INSPECTION.value
                    sub_outs, sub_res = self._process_subchunks_discovery(
                        chunk=chunk,
                        analyzer=analyzer,
                        resolved_model=resolved_model,
                        eff_temp=eff_temp,
                        eff_seed=eff_seed,
                        chunk_target_topics=chunk_target_topics,
                        tracker=tracker,
                        chunk_idx_1based=current_tracker_idx,
                        chk_store=chk_store,
                        doc_fp=doc_fp,
                        cfg_fp=cfg_fp,
                        parent_chunk_index=idx,
                        parent_document_id=document.document_id,
                        total_planned_chunks=len(chunks),
                        eff_max_tokens=eff_max_tokens,
                        origin=eff_origin_s,
                        run_id=run_id,
                    )
                    chunk_outcomes.extend(sub_outs)
                    if len(sub_res) == len(sub_outs):
                        # Tutti i microchunk del chunk padre sono completati: consolida il chunk padre
                        parent_agg = aggregate_topic_discovery(
                            chunk_results=sub_res,
                            parent_document_id=chunk.document_id,
                            max_topics=chunk_target_topics,
                        )
                        successful_results.append(parent_agg)
                        tracker.chunk_success(current_tracker_idx, record_request=False)
                    else:
                        # Un microchunk non validato: il blocco principale NON è SUCCESS
                        tracker.chunk_failed(current_tracker_idx, "micro-chunk non validati", record_request=False)
                    for so in sub_outs:
                        if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT", "SUCCESS_WITH_TOPICS", "EMPTY_TOPICS", "RECOVERED_EMPTY"):
                            analyzed_evidence_ids.update(so.evidence_ids)
                        if so.diagnostic:
                            all_chunk_diagnostics.append(so.diagnostic)
                    continue

                tracker.start_chunk(current_tracker_idx, phase="Analisi dei messaggi", record_request=False)
                chunk_tokens = calculate_discovery_max_tokens(
                    max_topics=chunk_target_topics,
                    user_max_tokens=max_tokens,
                    context_length=self.context_length,
                )
                t_chunk_start = time.perf_counter()
                chunk_attempts = 1

                def _on_chunk_retry(att: int, reason: str):
                    nonlocal chunk_attempts
                    chunk_attempts = att + 1
                    tracker.record_retry(reason)

                try:
                    res = analyzer.discover_topics(
                        document=chunk,
                        model_id=resolved_model,
                        temperature=eff_temp,
                        seed=eff_seed,
                        timeout_seconds=self.timeout_seconds,
                        max_topics=chunk_target_topics,
                        max_tokens=chunk_tokens,
                        operational_mode=True,
                        context_length=self.context_length,
                        on_retry_callback=_on_chunk_retry,
                    )
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    chk_saved = False
                    eff_origin_val = (
                        AnalysisOrigin.LIVE_LM_STUDIO.value
                        if isinstance(target_client, LmStudioClient)
                        else AnalysisOrigin.MOCK_INSPECTION.value
                    )
                    res_m = dict(res.metadata)
                    res_m["origin"] = eff_origin_val
                    res_m["run_id"] = run_id
                    res = TopicDiscoveryResult(
                        topics=res.topics,
                        provenance_document_id=res.provenance_document_id,
                        metadata=res_m,
                    )
                    if chk_store is not None:
                        try:
                            chk_store.save_chunk(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                chunk_idx=idx,
                                result=res,
                                total_planned_chunks=len(chunks),
                                analysis_type="discovery",
                                model_id=resolved_model,
                                prompt_version="discovery_operational",
                                origin=eff_origin_val,
                                run_id=run_id,
                            )
                            chk_saved = True
                        except Exception as chk_err:
                            tracker.phase = f"Avviso: Salvataggio checkpoint blocco {idx + 1} non riuscito ({type(chk_err).__name__})"

                    obs_finish = res.metadata.get("finish_reason")
                    num_extracted = len(res.topics)
                    thematic_st = "TOPICS_EXTRACTED" if num_extracted > 0 else "EMPTY_TOPICS"
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.SUCCESS.value,
                        thematic_status=thematic_st,
                        attempts=int(res.metadata.get("retries", 0)) + 1,
                        duration_seconds=dur,
                        model_id=str(res.metadata.get("model") or resolved_model),
                        http_status=200,
                        finish_reason=obs_finish,
                        prompt_tokens=res.metadata.get("prompt_tokens"),
                        completion_tokens=res.metadata.get("completion_tokens"),
                        error_type=None,
                        diagnostic_category="NONE",
                        failure_phase="completed" if num_extracted > 0 else "empty_output",
                        error_detail=None if num_extracted > 0 else "Elaborato con successo ma senza argomenti estratti",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=chk_saved,
                        topics_count=num_extracted,
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunk.document_id,
                            status="SUCCESS",
                            thematic_status=thematic_st,
                            result=res,
                            evidence_ids=tuple(chunk_eids),
                            diagnostic=diag,
                            attempts=diag.attempts,
                            duration_seconds=dur,
                        )
                    )
                    successful_results.append(res)
                    analyzed_evidence_ids.update(chunk_eids)
                    tracker.chunk_success(current_tracker_idx, record_request=False)
                    if chk_store is not None:
                        try:
                            chk_store.save_chunk_diagnostic(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                diagnostic=diag,
                                total_planned_chunks=len(chunks),
                                analysis_type="discovery",
                                model_id=resolved_model,
                                prompt_version="discovery_operational",
                            )
                        except Exception as diag_err:
                            logger.warning("Salvataggio diagnostica blocco %d non riuscito: %s", idx + 1, diag_err)
                            tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({type(diag_err).__name__})"
                except FATAL_BACKEND_ERRORS as fatal_err:
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(fatal_err)
                    is_val_err = isinstance(fatal_err, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                    is_truncation = (
                        isinstance(fatal_err, AiStructuredOutputError)
                        and (
                            getattr(fatal_err, "finish_reason", None) == "length"
                            or "finish_reason=length" in str(fatal_err)
                            or "Unterminated string" in str(fatal_err)
                            or "JSONDecodeError" in str(fatal_err)
                            or "prima del completamento" in str(fatal_err)
                        )
                    )
                    is_context_overflow = (
                        isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                        and getattr(fatal_err, "error_category", None) in ("CONTEXT_OVERFLOW", "INFERENCE_BACKEND_ERROR")
                    )
                    if (is_context_overflow or is_truncation) and len(chunk.bundles) > 1:
                        tracker.split_chunk(current_tracker_idx, subchunks_count=2, failed_prior_request=True, prior_error_msg=type(fatal_err).__name__, grow_total=False)
                        is_live_lms_fb = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
                        eff_origin_fb = AnalysisOrigin.LIVE_LM_STUDIO.value if is_live_lms_fb else AnalysisOrigin.MOCK_INSPECTION.value
                        sub_outs, sub_res = self._process_subchunks_discovery(
                            chunk=chunk,
                            analyzer=analyzer,
                            resolved_model=resolved_model,
                            eff_temp=eff_temp,
                            eff_seed=eff_seed,
                            chunk_target_topics=chunk_target_topics,
                            tracker=tracker,
                            chunk_idx_1based=current_tracker_idx,
                            chk_store=chk_store,
                            doc_fp=doc_fp,
                            cfg_fp=cfg_fp,
                            parent_chunk_index=idx,
                            parent_document_id=document.document_id,
                            total_planned_chunks=len(chunks),
                            eff_max_tokens=eff_max_tokens,
                            origin=eff_origin_fb,
                            run_id=run_id,
                        )
                        chunk_outcomes.extend(sub_outs)
                        if len(sub_res) == len(sub_outs):
                            parent_agg = aggregate_topic_discovery(
                                chunk_results=sub_res,
                                parent_document_id=chunk.document_id,
                                max_topics=chunk_target_topics,
                            )
                            successful_results.append(parent_agg)
                            tracker.chunk_success(current_tracker_idx, record_request=False)
                        else:
                            tracker.chunk_failed(current_tracker_idx, "micro-chunk non validati", record_request=False)
                        for so in sub_outs:
                            if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"):
                                analyzed_evidence_ids.update(so.evidence_ids)
                            if so.diagnostic:
                                all_chunk_diagnostics.append(so.diagnostic)
                    elif isinstance(fatal_err, SYSTEMIC_BACKEND_ERRORS):
                        tracker.chunk_failed(current_tracker_idx, str(fatal_err), is_protocol_or_validation=is_val_err)
                        tracker.fail(str(fatal_err))
                        raise
                    else:
                        tracker.chunk_failed(current_tracker_idx, str(fatal_err), is_protocol_or_validation=is_val_err)
                        http_st = getattr(fatal_err, "status_code", None) or (200 if isinstance(fatal_err, (AiInvalidEvidenceCitationError, AiStructuredOutputError)) else None)
                        obs_f_reason = getattr(fatal_err, "finish_reason", None)
                        eff_att = int(getattr(fatal_err, "attempts", chunk_attempts))
                        is_inv_ev = (cat == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value or isinstance(fatal_err, AiInvalidEvidenceCitationError))
                        diag = ChunkDiagnosticRecord(
                            chunk_index=idx,
                            chunk_id=chunk.document_id,
                            status=ChunkLifecycleStatus.FAILED.value,
                            attempts=eff_att,
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=http_st,
                            finish_reason=obs_f_reason,
                            prompt_tokens=getattr(fatal_err, "prompt_tokens", None),
                            completion_tokens=getattr(fatal_err, "completion_tokens", None),
                            error_type=type(fatal_err).__name__,
                            diagnostic_category=cat,
                            failure_phase=phase,
                            error_detail=safe_desc,
                            json_valid=(cat != ChunkDiagnosticCategory.JSON_DECODE_ERROR.value and not (isinstance(fatal_err, AiStructuredOutputError) and "JSON" in str(fatal_err))),
                            schema_valid=is_inv_ev,
                            evidence_valid=False,
                            checkpoint_saved=False,
                            diagnostic_metrics=_merge_diag_metrics(discovery_citation_metrics(fatal_err, len(chunk.bundles)), discovery_failure_metrics(fatal_err)),
                        )
                        all_chunk_diagnostics.append(diag)
                        chunk_outcomes.append(
                            DiscoveryChunkOutcome(
                                chunk_id=chunk.document_id,
                                status="FAILED",
                                result=None,
                                error_type=type(fatal_err).__name__,
                                safe_error_code=cat,
                                chunk_index=idx,
                                diagnostic=diag,
                                attempts=chunk_attempts,
                                duration_seconds=dur,
                            )
                        )
                        if chk_store is not None:
                            try:
                                chk_store.save_chunk_diagnostic(
                                    document_id=document.document_id,
                                    document_fingerprint=doc_fp,
                                    config_fingerprint=cfg_fp,
                                    diagnostic=diag,
                                    total_planned_chunks=len(chunks),
                                    analysis_type="discovery",
                                    model_id=resolved_model,
                                    prompt_version="discovery_operational",
                                )
                            except Exception as diag_err:
                                logger.warning("Salvataggio diagnostica blocco %d non riuscito: %s", idx + 1, diag_err)
                                tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({type(diag_err).__name__})"
                except Exception as exc:
                    if isinstance(exc, PROGRAMMING_ERRORS):
                        tracker.chunk_failed(current_tracker_idx, str(exc))
                        tracker.fail(str(exc))
                        raise
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(exc)
                    is_val_err = isinstance(exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                    tracker.chunk_failed(current_tracker_idx, str(exc), is_protocol_or_validation=is_val_err)
                    if isinstance(exc, AiInvalidEvidenceCitationError):
                        # Solo conteggi e forme: i valori citati dal modello non vanno nei log (potrebbero contenere testo).
                        _inv_metrics = discovery_citation_metrics(exc, len(chunk.bundles)) or {}
                        logger.warning(
                            "Chunk %d (indice %d) validazione fallita: INVALID_EVIDENCE_ID dopo %d tentativi. Citazioni non valide: %s (forme: %s)",
                            current_tracker_idx,
                            idx,
                            chunk_attempts,
                            _inv_metrics.get("citations_invalid_count", len(getattr(exc, "invalid_evidence_ids", ()))),
                            _inv_metrics.get("invalid_citation_shapes"),
                        )
                    http_st = getattr(exc, "status_code", None)
                    if isinstance(exc, (AiInvalidEvidenceCitationError, AiStructuredOutputError)):
                        http_st = 200
                    if cat == ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value:
                        f_reason = "length"
                    else:
                        f_reason = getattr(exc, "finish_reason", None)
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.FAILED.value,
                        attempts=chunk_attempts,
                        duration_seconds=dur,
                        model_id=resolved_model,
                        http_status=http_st,
                        finish_reason=f_reason,
                        prompt_tokens=None,
                        completion_tokens=None,
                        error_type=type(exc).__name__,
                        diagnostic_category=cat,
                        failure_phase=phase,
                        error_detail=safe_desc,
                        json_valid=(cat not in (ChunkDiagnosticCategory.JSON_DECODE_ERROR.value, ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value)),
                        schema_valid=(cat not in (
                            ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value,
                            ChunkDiagnosticCategory.EXCESS_TOPICS.value,
                            ChunkDiagnosticCategory.INTERNAL_VALIDATION_ERROR.value,
                            ChunkDiagnosticCategory.JSON_DECODE_ERROR.value,
                            ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value,
                        )),
                        evidence_valid=False,
                        checkpoint_saved=False,
                        diagnostic_metrics=_merge_diag_metrics(discovery_citation_metrics(exc, len(chunk.bundles)), discovery_failure_metrics(exc)),
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunk.document_id,
                            status="FAILED",
                            result=None,
                            error_type=type(exc).__name__,
                            safe_error_code=cat,
                            diagnostic=diag,
                            attempts=chunk_attempts,
                            duration_seconds=dur,
                        )
                    )
                    if chk_store is not None:
                        try:
                            chk_store.save_chunk_diagnostic(
                                document_id=document.document_id,
                                document_fingerprint=doc_fp,
                                config_fingerprint=cfg_fp,
                                diagnostic=diag,
                                total_planned_chunks=len(chunks),
                                analysis_type="discovery",
                                model_id=resolved_model,
                                prompt_version="discovery_operational",
                            )
                        except Exception as diag_err:
                            logger.warning("Salvataggio diagnostica blocco %d non riuscito: %s", idx + 1, diag_err)
                            tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({type(diag_err).__name__})"

            total_units = len(chunks)
            completed_count = len(successful_results)
            failed_count = max(0, total_units - completed_count)

            if not successful_results:
                tracker.fail(f"Tutti i {total_units} blocchi di Topic Discovery sono falliti.")
                raise OperationalAnalysisFailedError(
                    f"Tutti i {total_units} chunk di Topic Discovery sono falliti durante l'inferenza."
                )

            tracker.start_consolidation()
            aggregated = aggregate_topic_discovery(
                chunk_results=successful_results,
                parent_document_id=document.document_id,
                max_topics=eff_max_topics,
            )
            total_topics_discovered = len(aggregated.topics)
            if failed_count == 0:
                if total_topics_discovered > 0:
                    tracker.complete(f"Topic Discovery completata: emersi {total_topics_discovered} argomenti ({completed_count}/{total_units} blocchi completati).")
                    disc_status = "COMPLETE"
                else:
                    is_substantial = total_evidence_count >= 10
                    if is_substantial:
                        tracker.complete(f"Topic Discovery completata tecnicamente ma tematicamente non verificata: 0 argomenti emersi ({completed_count}/{total_units} blocchi completati).", thematic_unverified=True)
                        disc_status = "COMPLETE_EMPTY_UNVERIFIED"
                    else:
                        tracker.complete(f"Topic Discovery completata: nessun argomento emerso su conversazione breve ({completed_count}/{total_units} blocchi completati).", no_topics=True)
                        disc_status = "COMPLETE_NO_TOPICS"
                # INVARIANTE FORENSE E PERSISTENZA: NON invocare MAI clear_checkpoint(doc_fp, cfg_fp)!
                # I checkpoint SQLite rappresentano l'archivio autorevole e permanente per:
                # 1) Ricaricamento pagina e sessione
                # 2) Riavvio di Streamlit e del sistema operativo
                # 3) Selezione di un'altra conversazione e successivo ritorno alla prima
                # 4) Prova del Nove indipendente tra Discovery e Detection.
            else:
                tracker.partial(f"Topic Discovery completata parzialmente: emersi {total_topics_discovered} argomenti ({completed_count}/{total_units} blocchi completati).")
                disc_status = "PARTIAL"
        finally:
            try:
                target_client.request_listener = old_listener
            except Exception:
                pass

        latency = round(time.perf_counter() - start_time, 3)
        now_ts = datetime.now(timezone.utc).isoformat()

        is_live_lms_client = isinstance(target_client, LmStudioClient) or isinstance(getattr(target_client, "inner", None), LmStudioClient)
        has_unverified_recovered_origin = False
        recovered_origins_set: set[str] = set()
        if recovered_chunk_count > 0:
            for c_idx, c_res in cached_discovery_results.items():
                c_orig = (c_res.metadata.get("origin") if isinstance(c_res.metadata, Mapping) else None) or ""
                recovered_origins_set.add(c_orig)
                if c_orig != AnalysisOrigin.LIVE_LM_STUDIO.value:
                    has_unverified_recovered_origin = True
        for co in chunk_outcomes:
            if co.status == "RECOVERED_FROM_CHECKPOINT":
                co_orig = (co.result.metadata.get("origin") if co.result and isinstance(co.result.metadata, Mapping) else None) or ""
                recovered_origins_set.add(co_orig)
                if co_orig != AnalysisOrigin.LIVE_LM_STUDIO.value:
                    has_unverified_recovered_origin = True
        is_live_lms_final = is_live_lms_client and not has_unverified_recovered_origin
        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if is_live_lms_final else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version=PROMPT_VERSION_DISCOVERY_OPERATIONAL,
            strategy="DIRECT_MULTILINGUAL",
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(total_units),
            completed_chunk_count=int(completed_count),
            error_count=int(failed_count),
            failed_chunk_count=int(failed_count),
            reduction_strategy=aggregated.metadata.get("reduction_strategy", "none"),
            data_classification=classification,
            timestamp=now_ts,
            document_id=document.document_id,
            analysis_type="DISCOVERY",
            git_commit=current_git_commit,
            resolved_model_key=(
                getattr(target_client, "_active_binding", None).resolved_model_key
                if getattr(target_client, "_active_binding", None)
                else (getattr(target_client, "_last_loaded_model_key", None) or (model_name or self.default_model))
            ),
            loaded_instance_id=(
                getattr(target_client, "_loaded_instance_id", None)
                or (getattr(target_client, "_active_binding", None).loaded_instance_id if getattr(target_client, "_active_binding", None) else None)
                or "UNKNOWN (not provided by LM Studio)"
            ),
            api_identifier=getattr(target_client, "model_id", resolved_model),
            response_model=resolved_model,
            context_length=self.context_length,
            max_tokens=eff_max_tokens,
            start_timestamp=start_iso,
            end_timestamp=now_ts,
            status=disc_status,
            error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
            original_chunk_count=int(len(chunks)),
            microchunk_count=int(len(all_chunk_diagnostics)),
        )

        meta = dict(aggregated.metadata)
        meta.update(run_meta.to_dict())
        meta["git_commit"] = current_git_commit
        meta["original_chunk_count"] = int(len(chunks))
        meta["microchunk_count"] = int(len(all_chunk_diagnostics))
        meta["error_count"] = int(failed_count)
        meta["chunk_count"] = int(total_units)
        meta["expected_chunk_count"] = int(total_units)
        meta["completed_chunk_count"] = int(completed_count)
        meta["failed_chunk_count"] = int(failed_count)
        meta["recovered_chunk_count"] = int(recovered_chunk_count)
        meta["status"] = disc_status
        meta["chunk_outcomes"] = [o.to_dict() for o in chunk_outcomes]
        meta["chunk_diagnostics"] = [d.to_dict() for d in all_chunk_diagnostics]
        meta["total_evidence_count"] = int(total_evidence_count)
        meta["analyzed_evidence_count"] = int(len(analyzed_evidence_ids))
        meta["unanalyzed_evidence_count"] = max(0, int(total_evidence_count - len(analyzed_evidence_ids)))
        meta["evidence_citation_protocol"] = "deterministic_alias_v1"
        meta["acceptance_policy"] = DEGENERATION_POLICY_STRUCTURAL
        meta["status"] = disc_status
        if doc_fp:
            meta["document_fingerprint"] = doc_fp
        if cfg_fp:
            meta["config_fingerprint"] = cfg_fp
        meta["max_topics"] = eff_max_topics
        if is_live_lms_final:
            meta["provenance_verified"] = True
            meta["authoritative_execution_certified"] = True
            meta["chain_of_custody_intact"] = True
        elif is_live_lms_client and has_unverified_recovered_origin:
            meta["origin"] = (
                AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value
                if AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value in recovered_origins_set
                else None
            )
            meta["provenance_verified"] = False
            meta["authoritative_execution_certified"] = False
            meta["chain_of_custody_intact"] = False
            meta["attestation_level"] = (
                AttestationLevel.PROVENANCE_NOT_VERIFIED.value
                if AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value in recovered_origins_set
                else AttestationLevel.UNATTESTED.value
            )

        if not should_preserve_raw and "raw_response" in meta:
            del meta["raw_response"]

        final_res = TopicDiscoveryResult(
            topics=aggregated.topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )
        if is_live_lms_final:
            self._authoritative_live_run_ids.add(run_id)
        self._record_authoritative_discovery_run(document.document_id, final_res)
        return final_res

    def load_persisted_discovery_result(
        self,
        document: ConversationEvidenceDocument,
        model_name: str | None = None,
        max_topics: int | None = None,
        profile: OperationalProfile | None = None,
        allow_incompatible_model: bool = False,
    ) -> TopicDiscoveryResult | None:
        """
        Ripristina dal database SQLite lo stato e la diagnostica dell'ultima esecuzione di Topic Discovery
        per il documento, consentendo alla UI Streamlit di mostrare risultati e diagnostica anche dopo riavvio.
        Distingue rigorosamente lo storico compatibile con il modello richiesto da checkpoint eseguiti con altri modelli.
        """
        if not self.enable_checkpointing or self.checkpoint_store is None:
            return None

        chk_store = self.checkpoint_store
        doc_fp = compute_document_fingerprint(document)

        # Risoluzione modello richiesto se fornito
        resolved_model = None
        if model_name:
            try:
                _, resolved_model = self._prepare_client_and_model(None, model_name)
            except Exception:
                resolved_model = model_name.strip()

        chk_meta = None
        is_model_compatible = True
        is_compatible_for_retry = True

        if resolved_model:
            chk_meta = chk_store.get_latest_checkpoint_metadata_for_document(
                document_id=document.document_id,
                document_fingerprint=doc_fp,
                analysis_type="discovery",
                model_id=resolved_model,
            )

        if chk_meta is None:
            latest_any = chk_store.get_latest_checkpoint_metadata_for_document(
                document_id=document.document_id,
                document_fingerprint=doc_fp,
                analysis_type="discovery",
            )
            if latest_any is None:
                return None

            if resolved_model and latest_any["model_id"] != resolved_model:
                if not allow_incompatible_model:
                    return None
                is_model_compatible = False
                is_compatible_for_retry = False
            chk_meta = latest_any

        cfg_fp = chk_meta["config_fingerprint"]
        total_planned = chk_meta["total_planned_chunks"]
        updated_at = chk_meta["updated_at"]
        saved_model = chk_meta["model_id"]
        saved_pver = chk_meta["prompt_version"]

        eff_max_topics = max_topics if max_topics is not None else self.config.max_topics
        plan = self.create_analysis_plan(
            document=document,
            analysis_type="discovery",
            max_topics=eff_max_topics,
            context_length=self.context_length,
        )
        chunks = list(plan.chunks)

        checkpoint = chk_store.load_and_validate_checkpoint(
            document=document,
            config_fingerprint=cfg_fp,
            chunks=chunks,
            expected_analysis_type="discovery",
        )

        cached_discovery_results: dict[int, TopicDiscoveryResult] = {}
        if checkpoint is not None:
            for s_idx, s_data in checkpoint.serialized_results.items():
                try:
                    cached_discovery_results[int(s_idx)] = deserialize_discovery_result_from_checkpoint(s_data)
                except Exception:
                    pass

        saved_subchunks = chk_store.get_subchunks(
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
        )

        diagnostics = chk_store.get_chunk_diagnostics(
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
        )

        if not cached_discovery_results and not saved_subchunks and not diagnostics:
            return None

        total_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}
        total_evidence_count = len(total_evidence_ids)
        analyzed_evidence_ids: set[str] = set()

        for c_idx, res in cached_discovery_results.items():
            if c_idx < len(chunks):
                analyzed_evidence_ids.update({s.evidence_id for s in chunks[c_idx].all_evidence_sections})

        for sub in saved_subchunks:
            analyzed_evidence_ids.update(sub.get("evidence_ids", []))

        completed_count = len(cached_discovery_results)
        failed_count = max(0, total_planned - completed_count)

        all_topic_results = []
        if cached_discovery_results:
            all_topic_results.extend([cached_discovery_results[k] for k in sorted(cached_discovery_results.keys())])
        elif saved_subchunks:
            for sub in saved_subchunks:
                try:
                    s_res = deserialize_discovery_result_from_checkpoint(sub["serialized_result"])
                    all_topic_results.append(s_res)
                except Exception:
                    pass

        if all_topic_results:
            aggregated = aggregate_topic_discovery(
                chunk_results=all_topic_results,
                parent_document_id=document.document_id,
                max_topics=eff_max_topics,
            )
            topics = aggregated.topics
        else:
            topics = ()

        if completed_count >= total_planned and total_planned > 0:
            if len(topics) > 0:
                status = "COMPLETE"
            elif total_evidence_count >= 10:
                status = "COMPLETE_EMPTY_UNVERIFIED"
            else:
                status = "COMPLETE_NO_TOPICS"
        else:
            status = "PARTIAL"

        persisted_origin = None
        valid_origins = {o.value for o in AnalysisOrigin}
        # Ricostruzione rigorosa e fail-closed dell'origine dal checkpoint (Audit 27/10/2026):
        # l'origine è attestata solo se TUTTI i blocchi recuperati hanno un'origine valida e concorde.
        if cached_discovery_results:
            chunk_origins = [
                (cr.metadata.get("origin") if isinstance(cr.metadata, Mapping) else None) or ""
                for cr in cached_discovery_results.values()
            ]
            if all(o in valid_origins for o in chunk_origins) and len(set(chunk_origins)) == 1:
                cand_o = chunk_origins[0]
                header_o = chk_meta.get("origin") or (getattr(checkpoint, "origin", None) if checkpoint else None) or ""
                if header_o in ("", cand_o):
                    persisted_origin = cand_o
        elif saved_subchunks:
            sub_origins = []
            for sub in saved_subchunks:
                s_res = sub.get("serialized_result", {})
                if isinstance(s_res, str):
                    try:
                        s_res = json.loads(s_res)
                    except Exception:
                        s_res = {}
                o = (s_res.get("metadata", {}).get("origin") if isinstance(s_res, Mapping) else None) or ""
                sub_origins.append(o)
            if all(o in valid_origins for o in sub_origins) and len(set(sub_origins)) == 1:
                cand_o = sub_origins[0]
                header_o = chk_meta.get("origin") or ""
                if header_o in ("", cand_o):
                    persisted_origin = cand_o

        orig_run_id = chk_meta.get("run_id") or getattr(checkpoint, "run_id", "") or f"persisted_{cfg_fp[:8]}"
        if persisted_origin == AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value:
            attestation = AttestationLevel.PROVENANCE_NOT_VERIFIED.value
        elif persisted_origin:
            attestation = AttestationLevel.CHECKPOINT_AUTHENTICATED.value
        else:
            attestation = AttestationLevel.UNATTESTED.value

        meta = {
            "run_id": f"persisted_{cfg_fp[:8]}",
            "original_run_id": orig_run_id,
            "document_id": document.document_id,
            "origin": persisted_origin,
            "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
            "recovery_mode": "CHECKPOINT_RESTORE",
            "attestation_level": attestation,
            "model": saved_model,
            "historical_model": saved_model,
            "requested_model": resolved_model or saved_model,
            "is_model_compatible": is_model_compatible,
            "is_compatible_for_retry": is_compatible_for_retry,
            "prompt_version": saved_pver,
            "timestamp": updated_at,
            "chunk_count": total_planned,
            "completed_chunk_count": completed_count,
            "failed_chunk_count": failed_count,
            "recovered_chunk_count": completed_count,
            "status": status,
            "total_evidence_count": total_evidence_count,
            "analyzed_evidence_count": len(analyzed_evidence_ids),
            "unanalyzed_evidence_count": max(0, total_evidence_count - len(analyzed_evidence_ids)),
            "chunk_diagnostics": [d.to_dict() for d in diagnostics],
            "is_persisted_recovery": True,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
        }

        persisted_res = TopicDiscoveryResult(
            topics=topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )
        self._record_authoritative_discovery_run(document.document_id, persisted_res)
        return persisted_res

    def _authenticate_recovered_discovery_run_for_verification(
        self,
        document: ConversationEvidenceDocument,
        registered_run: TopicDiscoveryResult,
        run_id: str,
    ) -> None:
        """
        Autentica rigorosamente una discovery run recuperata da SQLite per la Prova del Nove.
        Garanzie forensi:
        1. Checkpointing abilitato e store disponibile (fail-closed).
        2. Config fingerprint e document fingerprint presenti nei metadata.
        3. Document fingerprint coincidente al 100% con il documento attivo.
        4. Record SQLite presente, con document_id e analysis_type='discovery' corrispondenti.
        5. Validazione referenziale completa di tutti i chunk e sotto-blocchi (validate_checkpoint_evidence_references).
        6. Verifica dell'identità dei topic recuperati: non devono essere stati alterati o modificati.
        7. Verifica che l'origine dell'inferenza sia autenticata nel checkpoint (fail-closed se mancante).
        """
        if not self.enable_checkpointing or self.checkpoint_store is None:
            raise TopicProvenanceError(
                f"Impossibile verificare la run recuperata '{run_id}': checkpointing disabilitato o store non disponibile (politica fail-closed)."
            )
        meta = registered_run.metadata if isinstance(registered_run.metadata, Mapping) else {}
        cfg_fp = meta.get("config_fingerprint")
        doc_fp = meta.get("document_fingerprint")
        if not cfg_fp or not doc_fp:
            raise TopicProvenanceError(
                f"Impossibile verificare la run recuperata '{run_id}': metadati di fingerprint incompleti o assenti (politica fail-closed)."
            )
        active_doc_fp = compute_document_fingerprint(document)
        if str(doc_fp).strip() != active_doc_fp:
            raise TopicProvenanceError(
                f"Documento diverso: il document fingerprint del checkpoint ('{str(doc_fp)[:8]}...') "
                f"non corrisponde al documento attivo ('{active_doc_fp[:8]}...'). Verifica rifiutata (politica fail-closed)."
            )
        chk_data = self.checkpoint_store.load_checkpoint(str(doc_fp).strip(), str(cfg_fp).strip())
        if chk_data is None:
            raise TopicProvenanceError(
                f"Fingerprint non compatibile o checkpoint assente in SQLite per la run recuperata '{run_id}' (politica fail-closed)."
            )
        if chk_data.document_id != document.document_id or chk_data.analysis_type != "discovery":
            raise TopicProvenanceError(
                f"Checkpoint incompatibile: document_id '{chk_data.document_id}' o tipo '{chk_data.analysis_type}' "
                f"non corrispondono a '{document.document_id}' (politica fail-closed)."
            )
        if not chk_data.completed_chunk_indices and not chk_data.serialized_results and not chk_data.subchunks:
            raise TopicProvenanceError(
                f"Run recuperata '{run_id}' priva di blocchi validati nel checkpoint SQLite."
            )
        plan = self.create_analysis_plan(
            document=document,
            analysis_type="discovery",
            max_topics=self.config.max_topics,
            context_length=self.context_length,
        )
        if not validate_checkpoint_evidence_references(checkpoint=chk_data, document=document, chunks=list(plan.chunks)):
            raise TopicProvenanceError(
                f"Run recuperata alterata: integrità referenziale dei checkpoint compromessa per la run '{run_id}' (politica fail-closed)."
            )
        chk_origin = getattr(chk_data, "origin", None) or ""
        run_origin = meta.get("origin")
        if not run_origin or run_origin not in {o.value for o in AnalysisOrigin}:
            raise TopicProvenanceError(
                f"Impossibile verificare la run recuperata '{run_id}': origine mancante o non ricostruibile (politica fail-closed)."
            )
        if chk_origin and chk_origin in {o.value for o in AnalysisOrigin} and str(chk_origin).strip() != str(run_origin).strip():
            raise TopicProvenanceError(
                f"Discrepanza di origine per la run recuperata '{run_id}': metadata='{run_origin}' != checkpoint='{chk_origin}'."
            )

        # 6. Ricostruzione deterministica dell'aggregazione multi-chunk dai blocchi validati in SQLite
        # per autenticare i topic aggregati (fusione evidence_ids, ranking deterministico, deduplicazione).
        deserialized_chunk_results: list[TopicDiscoveryResult] = []
        if chk_data.serialized_results:
            sorted_indices = sorted(
                chk_data.serialized_results.keys(),
                key=lambda x: int(x) if str(x).isdigit() else str(x),
            )
            for s_idx in sorted_indices:
                s_raw = chk_data.serialized_results[s_idx]
                try:
                    c_res = deserialize_discovery_result_from_checkpoint(s_raw)
                    deserialized_chunk_results.append(c_res)
                except Exception:
                    pass
        elif chk_data.subchunks:
            for sub in chk_data.subchunks:
                sub_raw = sub.get("serialized_result")
                if isinstance(sub_raw, dict):
                    try:
                        c_res = deserialize_discovery_result_from_checkpoint(sub_raw)
                        deserialized_chunk_results.append(c_res)
                    except Exception:
                        pass

        eff_max_topics = getattr(self.config, "max_topics", 10) or 10
        if deserialized_chunk_results:
            reconstructed_aggregate = aggregate_topic_discovery(
                chunk_results=deserialized_chunk_results,
                parent_document_id=document.document_id,
                max_topics=eff_max_topics,
            )
        else:
            reconstructed_aggregate = TopicDiscoveryResult(
                topics=(),
                provenance_document_id=document.document_id,
                metadata={"aggregation": "empty"},
            )

        from ai.chunking import canonicalize_topic_label, normalize_topic_label

        checkpoint_topics: dict[str, DiscoveredTopic] = {}
        for t in reconstructed_aggregate.topics:
            checkpoint_topics[t.label.strip().casefold()] = t
            ckey = canonicalize_topic_label(t.label) or normalize_topic_label(t.label)
            if ckey:
                checkpoint_topics[ckey.strip().casefold()] = t

        if len(registered_run.topics) != len(reconstructed_aggregate.topics):
            raise TopicProvenanceError(
                f"Discrepanza nel numero di topic per la run '{run_id}': registrati={len(registered_run.topics)} != aggregati_checkpoint={len(reconstructed_aggregate.topics)} (politica fail-closed)."
            )

        valid_evidence_ids = {sec.evidence_id for sec in document.all_evidence_sections}

        for reg_t in registered_run.topics:
            norm_lbl = reg_t.label.strip().casefold()
            reg_ckey = (canonicalize_topic_label(reg_t.label) or normalize_topic_label(reg_t.label) or "").strip().casefold()
            chk_t = checkpoint_topics.get(norm_lbl) or (checkpoint_topics.get(reg_ckey) if reg_ckey else None)
            if chk_t is None:
                raise TopicProvenanceError(
                    f"Topic modificato o non autentico: il topic '{reg_t.label}' non è presente nei blocchi del checkpoint SQLite."
                )
            if reg_t.short_description.strip() != chk_t.short_description.strip():
                raise TopicProvenanceError(
                    f"Topic modificato: la descrizione di '{reg_t.label}' è stata alterata rispetto al checkpoint."
                )
            if tuple(reg_t.evidence_ids) != tuple(chk_t.evidence_ids):
                raise TopicProvenanceError(
                    f"Topic modificato: gli evidence_id di '{reg_t.label}' sono stati alterati rispetto al checkpoint."
                )
            if not set(reg_t.evidence_ids).issubset(valid_evidence_ids):
                raise TopicProvenanceError(
                    f"Evidenze estranee nel topic '{reg_t.label}': contiene evidence_id non appartenenti al documento attivo."
                )

    def verify_discovered_topic(
        self,
        document: ConversationEvidenceDocument,
        discovered_topic: DiscoveredTopic | DiscoveredTopicRef,
        discovery_result: TopicDiscoveryResult | None = None,
        discovery_run_id: str | None = None,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        temperature: float | None = None,
        seed: int | None = None,
        preserve_raw_response: bool = False,
        progress_callback: Any = None,
    ) -> tuple[TopicDetectionResult, ConsistencyResult]:
        """
        Esegue la 'Prova del Nove':
        Seconda inferenza di Topic Detection completamente INDIPENDENTE sul topic scoperto.

        Garanzie forensi e architetturali:
        1. VALIDAZIONE DOCUMENTO: Il documento deve essere ConversationEvidenceDocument e contenere evidenze.
        2. VALIDAZIONE PROVENANCE:
           - Se DiscoveredTopicRef, ref.document_id deve corrispondere a document.document_id.
           - Se discovery_result fornito, provenance_document_id deve corrispondere e il topic deve appartenervi.
           - discovery_run_id è OBBLIGATORIO (diretto, dal ref o dal discovery_result) per tracciabilità forense.
           - La run di discovery DEVE essere registrata nel service per il documento specificato.
           - Il topic da verificare DEVE appartenere alla run registrata.
        3. VALIDAZIONE EVIDENCE ID:
           - Tutti gli evidence_ids del DiscoveredTopic devono appartenere al documento attivo (nessun ID estraneo).
        4. ISOLAMENTO INFERENZIALE:
           - La Topic Detection riceve ESCLUSIVAMENTE label e short_description del DiscoveredTopic.
           - Nessun evidence_id o decisione pregressa viene passato alla seconda inferenza.
        5. VALIDAZIONE EVIDENCE ID DETECTION:
           - Tutti gli evidence_ids restituiti dalla Detection devono appartenere al documento attivo.
        6. VALUTAZIONE DI CONSISTENZA:
           - Valutata a posteriori da ConsistencyService su risultati effettivi con run_id tracciati.
        """
        if not isinstance(document, ConversationEvidenceDocument):
            raise TypeError(f"document deve essere ConversationEvidenceDocument, ottenuto: {type(document)}")

        valid_evidence_ids = {sec.evidence_id for sec in document.all_evidence_sections}
        if not valid_evidence_ids:
            raise AnalysisNoEvidenceError(
                f"Documento '{document.document_id}' privo di sezioni di evidenza analizzabili."
            )

        if isinstance(discovered_topic, DiscoveredTopicRef):
            ref = discovered_topic
            if ref.document_id != document.document_id:
                raise TopicProvenanceError(
                    f"Discrepanza di provenienza: DiscoveredTopicRef.document_id "
                    f"'{ref.document_id}' != document.document_id '{document.document_id}'."
                )
            if discovery_run_id is None:
                discovery_run_id = ref.discovery_run_id
            elif str(discovery_run_id).strip() != str(ref.discovery_run_id).strip():
                raise TopicProvenanceError(
                    f"Discrepanza di run_id: DiscoveredTopicRef.discovery_run_id "
                    f"'{ref.discovery_run_id}' != discovery_run_id '{discovery_run_id}'."
                )
            actual_topic = ref.topic
        elif isinstance(discovered_topic, DiscoveredTopic):
            actual_topic = discovered_topic
        else:
            raise TypeError(
                f"discovered_topic deve essere DiscoveredTopic o DiscoveredTopicRef, ottenuto: {type(discovered_topic)}"
            )

        # Validazione discovery_result se fornito (senza auto-registrazione di run non verificate)
        if discovery_result is not None:
            if not isinstance(discovery_result, TopicDiscoveryResult):
                raise TypeError(
                    f"discovery_result deve essere TopicDiscoveryResult, ottenuto: {type(discovery_result)}"
                )
            if discovery_result.provenance_document_id != document.document_id:
                raise TopicProvenanceError(
                    f"Discrepanza di provenienza: discovery_result.provenance_document_id "
                    f"'{discovery_result.provenance_document_id}' != document.document_id '{document.document_id}'."
                )
            res_run_id = (
                discovery_result.metadata.get("run_id")
                if isinstance(discovery_result.metadata, Mapping)
                else None
            )
            if res_run_id:
                if discovery_run_id is None:
                    discovery_run_id = str(res_run_id)
                elif str(res_run_id).strip() != str(discovery_run_id).strip():
                    raise TopicProvenanceError(
                        f"Discrepanza di run_id: discovery_result run_id '{res_run_id}' != discovery_run_id '{discovery_run_id}'."
                    )

        # Discovery run_id è rigorosamente obbligatorio
        if not discovery_run_id or not str(discovery_run_id).strip():
            raise TopicProvenanceError(
                "discovery_run_id obbligatorio: la run di Topic Discovery deve avere provenienza tracciabile."
            )
        eff_discovery_run_id = str(discovery_run_id).strip()

        # Verifica obbligatoria che la run di discovery esista effettivamente nel registro del service per questo documento
        # (Le run devono essere prodotte dal percorso autoritativo run_topic_discovery, mai registrate arbitrariamente qui)
        registered_run = self.get_discovery_run(document.document_id, eff_discovery_run_id)
        if registered_run is None:
            if (
                discovery_result is not None
                and discovery_result.provenance_document_id == document.document_id
                and bool(
                    discovery_result.metadata.get("is_persisted_recovery")
                    or discovery_result.metadata.get("loading_mode") == LoadingMode.CHECKPOINT_RESTORE.value
                    or discovery_result.metadata.get("recovery_mode") == "CHECKPOINT_RESTORE"
                )
            ):
                self.register_discovery_run(document.document_id, discovery_result)
                registered_run = self.get_discovery_run(document.document_id, eff_discovery_run_id)
            if registered_run is None:
                raise TopicProvenanceError(
                    f"Discovery run inesistente o non registrata per il documento '{document.document_id}': '{eff_discovery_run_id}'."
                )

        run_origin = (
            registered_run.metadata.get("origin")
            if isinstance(registered_run.metadata, Mapping)
            else None
        )
        if run_origin is None or not str(run_origin).strip():
            raise TopicProvenanceError(
                f"Impossibile verificare la run '{eff_discovery_run_id}': origin mancante nei metadata (politica fail-closed)."
            )

        origin_str = run_origin.value if isinstance(run_origin, AnalysisOrigin) else str(run_origin).strip()
        if origin_str not in {o.value for o in AnalysisOrigin}:
            raise TopicProvenanceError(
                f"Impossibile verificare la run '{eff_discovery_run_id}': origin '{run_origin}' non riconosciuta (politica fail-closed)."
            )

        if origin_str == AnalysisOrigin.SYNTHETIC_REFERENCE.value:
            raise TopicProvenanceError(
                f"Impossibile verificare la run '{eff_discovery_run_id}': l'origine è SYNTHETIC_REFERENCE. "
                "La Prova del Nove operativa richiede una Topic Discovery realmente eseguita (LIVE_LM_STUDIO o MOCK_INSPECTION), "
                "non una reference sintetica dimostrativa."
            )

        is_recovered = bool(
            registered_run.metadata.get("is_persisted_recovery")
            or registered_run.metadata.get("loading_mode") == LoadingMode.CHECKPOINT_RESTORE.value
            or registered_run.metadata.get("recovery_mode") == "CHECKPOINT_RESTORE"
        )

        if origin_str == AnalysisOrigin.LIVE_LM_STUDIO.value:
            if not is_recovered:
                if eff_discovery_run_id not in self._authoritative_live_run_ids:
                    raise TopicProvenanceError(
                        f"Run '{eff_discovery_run_id}' priva di certificazione autoritativa di esecuzione live. "
                        "La Prova del Nove accetta esclusivamente run live registrate dal percorso operativo."
                    )
            else:
                self._authenticate_recovered_discovery_run_for_verification(document, registered_run, eff_discovery_run_id)
        elif is_recovered:
            self._authenticate_recovered_discovery_run_for_verification(document, registered_run, eff_discovery_run_id)

        # Verifica obbligatoria che il topic appartenga alla run registrata con identità positiva completa
        matching_in_run = [
            t for t in registered_run.topics
            if t.label.strip().casefold() == actual_topic.label.strip().casefold()
        ]
        if not matching_in_run:
            raise TopicProvenanceError(
                f"Topic '{actual_topic.label}' non appartiene alla run di discovery registrata '{eff_discovery_run_id}'."
            )

        reg_topic = matching_in_run[0]

        # Validazione rigorosa dell'identità del topic: short_description ed evidence_ids
        # Rifiuta topic con stessa label ma descrizione o evidence_id alterati
        if actual_topic.short_description.strip() != reg_topic.short_description.strip():
            raise TopicProvenanceError(
                f"Discrepanza nei dati del topic per '{actual_topic.label}': la descrizione specificata "
                f"('{actual_topic.short_description}') non corrisponde a quella della run registrata ('{reg_topic.short_description}')."
            )

        if tuple(actual_topic.evidence_ids) != tuple(reg_topic.evidence_ids):
            raise TopicProvenanceError(
                f"Discrepanza nei dati del topic per '{actual_topic.label}': gli evidence_id specificati "
                f"({actual_topic.evidence_ids}) non corrispondono a quelli della run registrata ({reg_topic.evidence_ids})."
            )

        # Validazione degli evidence_ids del topic scoperto rispetto al documento
        for eid in actual_topic.evidence_ids:
            if eid not in valid_evidence_ids:
                raise TopicProvenanceError(
                    f"Evidence ID estraneo '{eid}' presente nel topic scoperto '{actual_topic.label}': "
                    f"non appartiene al documento '{document.document_id}'."
                )

        # Query costruita SOLO con label e short_description (nessun hint o evidence_id discovery)
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", actual_topic.label.lower()).strip("_")[:24] or "topic"
        h = hashlib.sha256(f"{actual_topic.label}::{actual_topic.short_description}".encode("utf-8")).hexdigest()[:8]
        query = TopicQuery(
            topic_id=f"verify_{slug}_{h}",
            label=actual_topic.label.strip(),
            description=actual_topic.short_description.strip(),
        )

        # Seconda inferenza di Topic Detection completamente INDIPENDENTE
        try:
            det_result = self.run_topic_detection(
                document=document,
                topic=query,
                client=client,
                model_name=model_name,
                strategy=strategy,
                temperature=temperature,
                seed=seed,
                preserve_raw_response=preserve_raw_response,
                progress_callback=progress_callback,
            )
        except (AiInvalidEvidenceCitationError, AiStructuredOutputError) as det_err:
            raise TopicProvenanceError(
                f"Errore di conformità evidenze durante la verifica indipendente: {det_err}"
            ) from det_err

        # Validazione degli evidence_ids restituiti dalla detection rispetto al documento
        for eid in det_result.evidence_ids:
            if eid not in valid_evidence_ids:
                raise TopicProvenanceError(
                    f"Evidence ID estraneo '{eid}' restituito dalla Topic Detection: "
                    f"non appartiene al documento '{document.document_id}'."
                )

        det_meta = dict(det_result.metadata)
        det_meta["origin_flow"] = "DISCOVERY_VERIFICATION"
        det_meta["query_mode"] = "LABEL_AND_DESCRIPTION"
        det_meta["verified_discovery_run_id"] = eff_discovery_run_id
        det_meta["input_label"] = actual_topic.label
        det_meta["input_description"] = actual_topic.short_description
        det_result = TopicDetectionResult(
            topic=det_result.topic,
            decision=det_result.decision,
            evidence_ids=det_result.evidence_ids,
            rationale=det_result.rationale,
            provenance_document_id=det_result.provenance_document_id,
            metadata=det_meta,
        )

        det_run_id = (
            det_result.metadata.get("run_id")
            if isinstance(det_result.metadata, Mapping)
            else None
        )

        consistency = ConsistencyService.evaluate(
            document_id=document.document_id,
            discovered_topic=actual_topic,
            detection_result=det_result,
            discovery_run_id=eff_discovery_run_id,
            detection_run_id=det_run_id,
        )

        return det_result, consistency

