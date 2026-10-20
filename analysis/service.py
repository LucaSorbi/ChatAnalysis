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
    profile_document_for_analysis,
    split_document_into_chunks,
)
from ai.structured import (
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
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisNoEvidenceError,
    AnalysisOrigin,
    AnalysisPlan,
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ChunkLifecycleStatus,
    ConsistencyResult,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
    TopicAnalysisRunMetadata,
    TopicProvenanceError,
    classify_discovery_exception,
    resolve_operational_profile,
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
)

SYSTEMIC_BACKEND_ERRORS = (
    LmStudioUnavailableError,
    AiModelNotInstalledError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
)


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
    ) -> None:
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
    ) -> list[ChunkAnalysisOutcome]:
        """Esegue suddivisione controllata di un blocco che supera la finestra di contesto."""
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
        outcomes: list[ChunkAnalysisOutcome] = []
        for s_idx, sub in enumerate((sub1, sub2), start=1):
            sub_display_idx = chunk_idx_1based + (s_idx - 1)
            tracker.start_chunk(sub_display_idx, phase=f"Analisi sotto-blocco {s_idx}/2", record_request=False)
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
                )
                outcomes.append(
                    ChunkAnalysisOutcome(
                        chunk_id=sub.document_id,
                        result=res,
                        status="SUCCESS",
                    )
                )
                tracker.chunk_success(sub_display_idx, record_request=False)
            except Exception as s_exc:
                tracker.chunk_failed(sub_display_idx, str(s_exc))
                outcomes.append(
                    ChunkAnalysisOutcome(
                        chunk_id=sub.document_id,
                        result=None,
                        status="FAILED",
                        error_type=type(s_exc).__name__,
                        error_message=str(s_exc),
                    )
                )
        return outcomes

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
    ) -> tuple[list[DiscoveryChunkOutcome], list[TopicDiscoveryResult]]:
        """Esegue suddivisione controllata di un blocco di discovery oversize con persistenza incrementale e recupero dei sotto-blocchi."""
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
        outcomes: list[DiscoveryChunkOutcome] = []
        results: list[TopicDiscoveryResult] = []
        subs = (sub1, sub2)
        total_subs = len(subs)

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
                sub_diag = ChunkDiagnosticRecord(
                    chunk_index=sub_display_idx - 1,
                    chunk_id=sub.document_id,
                    status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
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
                )
                outcomes.append(
                    DiscoveryChunkOutcome(
                        chunk_id=sub.document_id,
                        status="RECOVERED_FROM_CHECKPOINT",
                        result=valid_cached_res,
                        evidence_ids=sub_eids,
                        diagnostic=sub_diag,
                        chunk_index=sub_display_idx - 1,
                        attempts=0,
                        duration_seconds=0.0,
                    )
                )
                results.append(valid_cached_res)
                tracker.chunk_success(sub_display_idx, record_request=False)
                continue

            # Esecuzione inferenza sul sotto-blocco mancante
            tracker.start_chunk(sub_display_idx, phase=f"Analisi sotto-blocco {s_idx}/{total_subs}", record_request=False)
            t_sub_start = time.perf_counter()
            try:
                res = analyzer.discover_topics(
                    document=sub,
                    model_id=resolved_model,
                    temperature=eff_temp,
                    seed=eff_seed,
                    timeout_seconds=self.timeout_seconds,
                    max_topics=chunk_target_topics,
                    operational_mode=True,
                    context_length=self.context_length,
                    on_retry_callback=lambda att, r: tracker.chunk_retry(sub_display_idx, f"sub{s_idx}:{r}"),
                )
                dur = round(time.perf_counter() - t_sub_start, 3)
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
                            result=res,
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

                obs_finish = res.metadata.get("finish_reason")
                sub_diag = ChunkDiagnosticRecord(
                    chunk_index=sub_display_idx - 1,
                    chunk_id=sub.document_id,
                    status=ChunkLifecycleStatus.SUCCESS.value,
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
                    error_detail=None,
                    json_valid=True,
                    schema_valid=True,
                    evidence_valid=True,
                    checkpoint_saved=chk_saved,
                )
                outcomes.append(
                    DiscoveryChunkOutcome(
                        chunk_id=sub.document_id,
                        status="SUCCESS",
                        result=res,
                        evidence_ids=sub_eids,
                        diagnostic=sub_diag,
                        chunk_index=sub_display_idx - 1,
                        attempts=int(res.metadata.get("retries", 0)) + 1,
                        duration_seconds=dur,
                    )
                )
                results.append(res)
                tracker.chunk_success(sub_display_idx, record_request=False)
            except Exception as s_exc:
                cat, phase, safe_desc = classify_discovery_exception(s_exc)
                is_val_err = isinstance(s_exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                tracker.chunk_failed(sub_display_idx, str(s_exc), is_protocol_or_validation=is_val_err)
                sub_diag = ChunkDiagnosticRecord(
                    chunk_index=sub_display_idx - 1,
                    chunk_id=sub.document_id,
                    status=ChunkLifecycleStatus.FAILED.value,
                    attempts=1,
                    duration_seconds=0.0,
                    model_id=resolved_model,
                    http_status=getattr(s_exc, "status_code", None),
                    finish_reason=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    error_type=type(s_exc).__name__,
                    diagnostic_category=cat,
                    failure_phase=phase,
                    error_detail=safe_desc,
                    json_valid=(cat not in (ChunkDiagnosticCategory.JSON_DECODE_ERROR.value, ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value)),
                    schema_valid=False,
                    evidence_valid=False,
                    checkpoint_saved=False,
                )
                outcomes.append(
                    DiscoveryChunkOutcome(
                        chunk_id=sub.document_id,
                        status="FAILED",
                        result=None,
                        evidence_ids=sub_eids,
                        error_type=type(s_exc).__name__,
                        safe_error_code=cat,
                        diagnostic=sub_diag,
                        chunk_index=sub_display_idx - 1,
                        attempts=1,
                        duration_seconds=0.0,
                    )
                )

        if len(results) == total_subs and chk_store is not None and doc_fp and cfg_fp:
            try:
                aggregated_parent = aggregate_topic_discovery(
                    chunk_results=results,
                    parent_document_id=parent_document_id or chunk.document_id,
                    max_topics=chunk_target_topics,
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

            if len(chunks) == 1:
                total_units = 1
                completed_count = 1
                failed_count = 0
                det_status = "SUCCESS"
                if 0 in cached_results:
                    result = cached_results[0]
                    tracker.chunk_success(1, record_request=False)
                    tracker.complete("Topic Detection completata da checkpoint: 1/1 blocchi recuperati.")
                else:
                    tracker.start_chunk(1, phase="Analisi dei messaggi", record_request=False)
                    try:
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
                        )
                        tracker.chunk_success(1, record_request=False)
                        tracker.complete("Topic Detection completata.")
                        if chk_store is not None:
                            try:
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
                                )
                            except Exception as chk_err:
                                tracker.phase = f"Avviso: Salvataggio checkpoint blocco 1 non riuscito ({chk_err})"
                    except FATAL_BACKEND_ERRORS as fatal_err:
                        if (
                            isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                            and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                            and len(chunks[0].bundles) > 1
                        ):
                            tracker.split_chunk(1, subchunks_count=2, failed_prior_request=True, prior_error_msg=str(fatal_err))
                            sub_outcomes = self._process_subchunks_detection(
                                chunk=chunks[0],
                                analyzer=analyzer,
                                topic=topic,
                                strategy=strategy,
                                resolved_model=resolved_model,
                                eff_temp=eff_temp,
                                eff_seed=eff_seed,
                                eff_max_tokens=eff_max_tokens,
                                operational_mode=operational_mode,
                                tracker=tracker,
                                chunk_idx_1based=1,
                            )
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
                                tracker.chunk_failed(1, str(fatal_err))
                                tracker.fail(str(fatal_err))
                                raise
                        else:
                            tracker.chunk_failed(1, str(fatal_err))
                            tracker.fail(str(fatal_err))
                            raise
                    except Exception as exc:
                        tracker.chunk_failed(1, str(exc))
                        tracker.fail(str(exc))
                        raise OperationalAnalysisFailedError(
                            f"Inferenza di Topic Detection fallita sul documento: {exc}"
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
                    failed_chunk_count=int(failed_count),
                    data_classification=classification,
                    timestamp=now_ts,
                    document_id=document.document_id,
                    analysis_type="DETECTION",
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
                meta["chunk_count"] = int(total_units)
                meta["completed_chunk_count"] = int(completed_count)
                meta["failed_chunk_count"] = int(failed_count)
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
            for idx, chunk in enumerate(chunks):
                current_tracker_idx = tracker.completed_chunks + tracker.failed_chunks + 1
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
                    continue

                tracker.start_chunk(current_tracker_idx, phase="Analisi dei messaggi", record_request=False)
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
                    )
                    outcomes.append(
                        ChunkAnalysisOutcome(
                            chunk_id=chunk.document_id,
                            result=res,
                            status="SUCCESS",
                        )
                    )
                    tracker.chunk_success(current_tracker_idx, record_request=False)
                    if chk_store is not None:
                        try:
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
                            )
                        except Exception as chk_err:
                            tracker.phase = f"Avviso: Salvataggio checkpoint blocco {idx + 1} non riuscito ({chk_err})"
                except FATAL_BACKEND_ERRORS as fatal_err:
                    # Se è un CONTEXT_OVERFLOW recuperabile su chunk con più di un bundle, tenta suddivisione
                    if (
                        isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                        and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                        and len(chunk.bundles) > 1
                    ):
                        tracker.split_chunk(current_tracker_idx, subchunks_count=2, failed_prior_request=True, prior_error_msg=str(fatal_err))
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
                            chunk_idx_1based=current_tracker_idx,
                        )
                    elif isinstance(fatal_err, SYSTEMIC_BACKEND_ERRORS):
                        tracker.chunk_failed(current_tracker_idx, str(fatal_err))
                        tracker.fail(str(fatal_err))
                        raise
                    else:
                        tracker.chunk_failed(current_tracker_idx, str(fatal_err))
                        outcomes.append(
                            ChunkAnalysisOutcome(
                                chunk_id=chunk.document_id,
                                result=None,
                                status="FAILED",
                                error_type=type(fatal_err).__name__,
                                error_message=str(fatal_err),
                            )
                        )
                except Exception as exc:
                    tracker.chunk_failed(current_tracker_idx, str(exc))
                    outcomes.append(
                        ChunkAnalysisOutcome(
                            chunk_id=chunk.document_id,
                            result=None,
                            status="FAILED",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
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
            if chk_store is not None:
                chk_store.clear_checkpoint(doc_fp, cfg_fp)
        else:
            tracker.partial(f"Topic Detection completata parzialmente: {completed_count}/{total_units} blocchi analizzati.")
            det_status = "PARTIAL"

        latency = round(time.perf_counter() - start_time, 3)
        now_ts = datetime.now(timezone.utc).isoformat()
        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version=aggregated.metadata.get("prompt_version", "topic_detection_operational_v2"),
            strategy=strategy.value,
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(total_units),
            completed_chunk_count=int(completed_count),
            failed_chunk_count=int(failed_count),
            data_classification=classification,
            timestamp=now_ts,
            document_id=document.document_id,
            analysis_type="DETECTION",
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
        meta["chunk_count"] = int(total_units)
        meta["completed_chunk_count"] = int(completed_count)
        meta["failed_chunk_count"] = int(failed_count)
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
                    if existing_sub0 and len(chunks[0].bundles) > 1:
                        tracker.split_chunk(1, subchunks_count=2, failed_prior_request=False)
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
                            tracker.complete("Topic Discovery completata tramite suddivisione sub-chunk.")
                            disc_status = "COMPLETE"
                        elif completed_count > 0:
                            disc_res = aggregate_topic_discovery(
                                chunk_results=sub_res,
                                parent_document_id=document.document_id,
                                max_topics=eff_max_topics,
                            )
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
                                on_retry_callback=lambda att, r: tracker.chunk_retry(1, r),
                            )
                            dur = round(time.perf_counter() - t_single_start, 3)
                            analyzed_evidence_ids.update(total_evidence_ids)
                            tracker.chunk_success(1, record_request=False)
                            tracker.complete("Topic Discovery completata.")
                            chk_saved = False
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
                                    )
                                    chk_saved = True
                                except Exception as chk_err:
                                    tracker.phase = f"Avviso: Salvataggio checkpoint blocco 1 non riuscito ({chk_err})"

                            diag = ChunkDiagnosticRecord(
                                chunk_index=0,
                                chunk_id=chunks[0].document_id,
                                status=ChunkLifecycleStatus.SUCCESS.value,
                                attempts=int(disc_res.metadata.get("retries", 0)) + 1,
                                duration_seconds=dur,
                                model_id=str(disc_res.metadata.get("model") or resolved_model),
                                http_status=200,
                                finish_reason=disc_res.metadata.get("finish_reason"),
                                prompt_tokens=disc_res.metadata.get("prompt_tokens"),
                                completion_tokens=disc_res.metadata.get("completion_tokens"),
                                error_type=None,
                                diagnostic_category="NONE",
                                failure_phase="completed",
                                error_detail=None,
                                json_valid=True,
                                schema_valid=True,
                                evidence_valid=True,
                                checkpoint_saved=chk_saved,
                            )
                            all_chunk_diagnostics.append(diag)
                            chunk_outcomes.append(
                                DiscoveryChunkOutcome(
                                    chunk_id=chunks[0].document_id,
                                    status="SUCCESS",
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
                                    tracker.phase = f"Avviso: Salvataggio diagnostica blocco 1 su SQLite non riuscito ({diag_err})"
                        except FATAL_BACKEND_ERRORS as fatal_err:
                            if (
                                isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                                and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                                and len(chunks[0].bundles) > 1
                            ):
                                tracker.split_chunk(1, subchunks_count=2, failed_prior_request=True, prior_error_msg=str(fatal_err))
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
                                    tracker.complete("Topic Discovery completata tramite suddivisione sub-chunk.")
                                    disc_status = "COMPLETE"
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
                run_meta = TopicAnalysisRunMetadata(
                    run_id=run_id,
                    origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
                    model=resolved_model,
                    requested_model=(model_name or "").strip() or self.default_model,
                    latency_seconds=latency,
                    prompt_version=disc_res.metadata.get("prompt_version", "topic_discovery_operational_v2"),
                    strategy="DIRECT_MULTILINGUAL",
                    temperature=eff_temp,
                    seed=eff_seed,
                    chunk_count=int(total_units),
                    completed_chunk_count=int(completed_count),
                    failed_chunk_count=int(failed_count),
                    reduction_strategy="none",
                    data_classification=classification,
                    timestamp=now_ts,
                    document_id=document.document_id,
                    analysis_type="DISCOVERY",
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
                )

                meta = dict(disc_res.metadata)
                meta.update(run_meta.to_dict())
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
                if not should_preserve_raw and "raw_response" in meta:
                    del meta["raw_response"]

                final_res = TopicDiscoveryResult(
                    topics=disc_res.topics,
                    provenance_document_id=document.document_id,
                    metadata=meta,
                )
                if isinstance(target_client, LmStudioClient):
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
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.RECOVERED_FROM_CHECKPOINT.value,
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
                        error_detail="Recuperato con successo da checkpoint SQLite",
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=True,
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunk.document_id,
                            status="SUCCESS",
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
                if existing_subs and len(chunk.bundles) > 1:
                    tracker.split_chunk(current_tracker_idx, subchunks_count=2, failed_prior_request=False)
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
                    )
                    chunk_outcomes.extend(sub_outs)
                    successful_results.extend(sub_res)
                    for so in sub_outs:
                        if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"):
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
                    tracker.chunk_retry(current_tracker_idx, reason)

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
                            )
                            chk_saved = True
                        except Exception as chk_err:
                            tracker.phase = f"Avviso: Salvataggio checkpoint blocco {idx + 1} non riuscito ({chk_err})"

                    obs_finish = res.metadata.get("finish_reason")
                    diag = ChunkDiagnosticRecord(
                        chunk_index=idx,
                        chunk_id=chunk.document_id,
                        status=ChunkLifecycleStatus.SUCCESS.value,
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
                        error_detail=None,
                        json_valid=True,
                        schema_valid=True,
                        evidence_valid=True,
                        checkpoint_saved=chk_saved,
                    )
                    all_chunk_diagnostics.append(diag)
                    chunk_outcomes.append(
                        DiscoveryChunkOutcome(
                            chunk_id=chunk.document_id,
                            status="SUCCESS",
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
                            tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({diag_err})"
                except FATAL_BACKEND_ERRORS as fatal_err:
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(fatal_err)
                    is_val_err = isinstance(fatal_err, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                    if (
                        isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError))
                        and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW"
                        and len(chunk.bundles) > 1
                    ):
                        tracker.split_chunk(current_tracker_idx, subchunks_count=2, failed_prior_request=True, prior_error_msg=str(fatal_err))
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
                        )
                        chunk_outcomes.extend(sub_outs)
                        successful_results.extend(sub_res)
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
                        http_st = getattr(fatal_err, "status_code", None)
                        obs_f_reason = getattr(fatal_err, "finish_reason", None)
                        diag = ChunkDiagnosticRecord(
                            chunk_index=idx,
                            chunk_id=chunk.document_id,
                            status=ChunkLifecycleStatus.FAILED.value,
                            attempts=chunk_attempts,
                            duration_seconds=dur,
                            model_id=resolved_model,
                            http_status=http_st,
                            finish_reason=obs_f_reason,
                            prompt_tokens=None,
                            completion_tokens=None,
                            error_type=type(fatal_err).__name__,
                            diagnostic_category=cat,
                            failure_phase=phase,
                            error_detail=safe_desc,
                            json_valid=False,
                            schema_valid=False,
                            evidence_valid=False,
                            checkpoint_saved=False,
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
                                tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({diag_err})"
                except Exception as exc:
                    dur = round(time.perf_counter() - t_chunk_start, 3)
                    cat, phase, safe_desc = classify_discovery_exception(exc)
                    is_val_err = isinstance(exc, (AiStructuredOutputError, AiInvalidEvidenceCitationError, ValueError))
                    tracker.chunk_failed(current_tracker_idx, str(exc), is_protocol_or_validation=is_val_err)
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
                            tracker.phase = f"Avviso: Salvataggio diagnostica blocco {idx + 1} su SQLite non riuscito ({diag_err})"

            total_units = len(chunk_outcomes)
            completed_count = len(successful_results)
            failed_count = total_units - completed_count

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
            if failed_count == 0:
                tracker.complete(f"Topic Discovery completata: emersi {len(aggregated.topics)} argomenti ({completed_count}/{total_units} blocchi completati).")
                disc_status = "COMPLETE"
                if chk_store is not None:
                    try:
                        chk_store.clear_checkpoint(doc_fp, cfg_fp)
                    except Exception:
                        pass
            else:
                tracker.partial(f"Topic Discovery completata parzialmente: emersi {len(aggregated.topics)} argomenti ({completed_count}/{total_units} blocchi completati).")
                disc_status = "PARTIAL"
        finally:
            try:
                target_client.request_listener = old_listener
            except Exception:
                pass

        latency = round(time.perf_counter() - start_time, 3)
        now_ts = datetime.now(timezone.utc).isoformat()

        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version="topic_discovery_operational_v2",
            strategy="DIRECT_MULTILINGUAL",
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(total_units),
            completed_chunk_count=int(completed_count),
            failed_chunk_count=int(failed_count),
            reduction_strategy=aggregated.metadata.get("reduction_strategy", "none"),
            data_classification=classification,
            timestamp=now_ts,
            document_id=document.document_id,
            analysis_type="DISCOVERY",
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
        )

        meta = dict(aggregated.metadata)
        meta.update(run_meta.to_dict())
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
        meta["status"] = disc_status

        if not should_preserve_raw and "raw_response" in meta:
            del meta["raw_response"]

        final_res = TopicDiscoveryResult(
            topics=aggregated.topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )
        if isinstance(target_client, LmStudioClient):
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
        status = "COMPLETE" if completed_count >= total_planned and total_planned > 0 else "PARTIAL"

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

        meta = {
            "run_id": f"persisted_{cfg_fp[:8]}",
            "document_id": document.document_id,
            "origin": "RECOVERED_CHECKPOINT",
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
        }

        persisted_res = TopicDiscoveryResult(
            topics=topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )
        self._record_authoritative_discovery_run(document.document_id, persisted_res)
        return persisted_res

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

        if (
            origin_str == AnalysisOrigin.LIVE_LM_STUDIO.value
            and eff_discovery_run_id not in self._authoritative_live_run_ids
        ):
            raise TopicProvenanceError(
                f"Run '{eff_discovery_run_id}' priva di certificazione autoritativa di esecuzione live. "
                "La Prova del Nove accetta esclusivamente run live registrate dal percorso operativo."
            )

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

