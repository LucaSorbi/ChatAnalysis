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
import re
import time
import uuid
from typing import Any, Mapping, MutableMapping, MutableSet, Sequence

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    BaseLocalLlmClient,
    LmStudioUnavailableError,
)
from ai.chunking import (
    ChunkAnalysisOutcome,
    aggregate_topic_detection,
    aggregate_topic_discovery,
    split_document_into_chunks,
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
    ConsistencyResult,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    TopicAnalysisRunMetadata,
    TopicProvenanceError,
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
)


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
        client: BaseLlmClient | None = None,
        discovery_run_store: MutableMapping[str, dict[str, TopicDiscoveryResult]] | None = None,
        authoritative_live_run_ids: MutableSet[str] | None = None,
    ) -> None:
        cfg = config or OperationalAnalysisConfig()
        self.config = cfg
        self.base_url = base_url
        self.default_model = default_model or cfg.model_spec
        self.context_length = context_length or cfg.context_length
        self.timeout_seconds = timeout_seconds or cfg.timeout_seconds
        self.max_chunk_characters = max_chunk_characters or cfg.chunk_character_budget
        self.max_chunk_bundles = max_chunk_bundles or cfg.max_chunk_bundles
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

    def _record_authoritative_discovery_run(
        self, document_id: str, discovery_result: TopicDiscoveryResult
    ) -> str:
        """Registrazione autoritativa interna per run prodotte da questo service."""
        eff_run_id = str(discovery_result.metadata.get("run_id", "")).strip()
        if document_id not in self._discovery_runs:
            self._discovery_runs[document_id] = {}
        self._discovery_runs[document_id][eff_run_id] = discovery_result
        return eff_run_id

    def register_discovery_run(self, document_id: str, discovery_result: TopicDiscoveryResult) -> str:
        """
        Registra una run di Topic Discovery per il documento specificato, vincolandola
        alla provenance documentale. Restituisce il discovery_run_id registrato.
        Rifiuta categorizatione di run sintetiche o run con pretesa origine live non verificate.
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

        return self._record_authoritative_discovery_run(document_id, discovery_result)

    def register_mock_discovery_run(self, document_id: str, discovery_result: TopicDiscoveryResult) -> str:
        """
        Interfaccia esplicita per i test: inietta controllatamente un risultato di Topic Discovery marcato come MOCK_INSPECTION.
        """
        meta = dict(discovery_result.metadata) if isinstance(discovery_result.metadata, Mapping) else {}
        meta["origin"] = AnalysisOrigin.MOCK_INSPECTION.value
        mock_res = TopicDiscoveryResult(
            topics=discovery_result.topics,
            provenance_document_id=document_id,
            metadata=meta,
        )
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
    ) -> TopicDetectionResult:
        """
        Esegue Topic Detection con supporto trasparente al chunking multi-chunk.
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

        chunks = split_document_into_chunks(
            document,
            max_characters=self.max_chunk_characters,
            max_bundles=self.max_chunk_bundles,
        )

        is_synthetic = self._is_synthetic_document(document)
        should_preserve_raw = preserve_raw_response or is_synthetic
        classification = "SYNTHETIC_DEMO" if is_synthetic else "REAL_FORENSIC"

        if len(chunks) == 1:
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
                )
            except FATAL_BACKEND_ERRORS:
                raise
            except Exception as exc:
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
                chunk_count=1,
                completed_chunk_count=1,
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
                status="SUCCESS",
                error_classification=None,
            )

            meta = dict(result.metadata)
            meta.update(run_meta.to_dict())
            meta["chunk_count"] = 1
            meta["completed_chunk_count"] = 1
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

        # Multi-chunk: esecuzione chunk-by-chunk con tracking dei fallimenti
        outcomes: list[ChunkAnalysisOutcome] = []
        for idx, chunk in enumerate(chunks):
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
                )
                outcomes.append(
                    ChunkAnalysisOutcome(
                        chunk_id=chunk.document_id,
                        result=res,
                        status="SUCCESS",
                    )
                )
            except FATAL_BACKEND_ERRORS:
                # Errori sistemici/fatali propagano immediatamente
                raise
            except Exception as exc:
                outcomes.append(
                    ChunkAnalysisOutcome(
                        chunk_id=chunk.document_id,
                        result=None,
                        status="FAILED",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )

        completed_count = sum(1 for o in outcomes if o.status == "SUCCESS")
        if completed_count == 0:
            raise OperationalAnalysisFailedError(
                f"Tutti i {len(chunks)} chunk di Topic Detection sono falliti durante l'inferenza."
            )

        aggregated = aggregate_topic_detection(
            outcomes=outcomes,
            topic=topic,
            parent_document_id=document.document_id,
            expected_chunk_count=len(chunks),
        )

        latency = round(time.perf_counter() - start_time, 3)
        failed_count = len(chunks) - completed_count
        now_ts = datetime.now(timezone.utc).isoformat()
        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version=aggregated.metadata.get("prompt_version", "topic_detection_operational_v1"),
            strategy=strategy.value,
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(len(chunks)),
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
            status="PARTIAL" if failed_count > 0 else "SUCCESS",
            error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
        )

        meta = dict(aggregated.metadata)
        meta.update(run_meta.to_dict())
        meta["chunk_count"] = int(len(chunks))
        meta["completed_chunk_count"] = int(completed_count)
        meta["failed_chunk_count"] = int(failed_count)
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
    ) -> TopicDiscoveryResult:
        """
        Esegue Topic Discovery LIVE con supporto trasparente al chunking multi-chunk.
        """
        if not document.bundles or not document.all_evidence_sections:
            raise AnalysisNoEvidenceError(
                f"Il documento '{document.document_id}' non contiene sezioni di evidenza analizzabili."
            )

        eff_max_topics = max_topics if max_topics is not None else self.config.max_topics
        eff_temp = temperature if temperature is not None else self.config.temperature
        eff_seed = seed if seed is not None else self.config.seed
        eff_max_tokens = max_tokens if max_tokens is not None else self.config.max_tokens

        run_id = f"run_disc_{uuid.uuid4().hex[:10]}"
        start_time = time.perf_counter()
        start_iso = datetime.now(timezone.utc).isoformat()

        target_client, resolved_model = self._prepare_client_and_model(client, model_name)

        analyzer = TopicDiscoveryAnalyzer(
            client=target_client,
            default_model_id=resolved_model,
            operational_mode=True,
        )

        chunks = split_document_into_chunks(
            document,
            max_characters=self.max_chunk_characters,
            max_bundles=self.max_chunk_bundles,
        )

        is_synthetic = self._is_synthetic_document(document)
        should_preserve_raw = preserve_raw_response or is_synthetic
        classification = "SYNTHETIC_DEMO" if is_synthetic else "REAL_FORENSIC"

        if len(chunks) == 1:
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
                )
            except FATAL_BACKEND_ERRORS:
                raise
            except Exception as exc:
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
                prompt_version=disc_res.metadata.get("prompt_version", "topic_discovery_v1"),
                strategy="DIRECT_MULTILINGUAL",
                temperature=eff_temp,
                seed=eff_seed,
                chunk_count=1,
                completed_chunk_count=1,
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
                status="COMPLETE",
                error_classification=None,
            )

            meta = dict(disc_res.metadata)
            meta.update(run_meta.to_dict())
            meta["chunk_count"] = 1
            meta["completed_chunk_count"] = 1
            if not should_preserve_raw and "raw_response" in meta:
                del meta["raw_response"]

            final_res = TopicDiscoveryResult(
                topics=disc_res.topics,
                provenance_document_id=document.document_id,
                metadata=meta,
            )
            self._authoritative_live_run_ids.add(run_id)
            self._record_authoritative_discovery_run(document.document_id, final_res)
            return final_res

        # Multi-chunk discovery con tracking esplicito
        chunk_outcomes: list[DiscoveryChunkOutcome] = []
        successful_results: list[TopicDiscoveryResult] = []

        for chunk in chunks:
            try:
                res = analyzer.discover_topics(
                    document=chunk,
                    model_id=resolved_model,
                    temperature=eff_temp,
                    seed=eff_seed,
                    timeout_seconds=self.timeout_seconds,
                    max_topics=eff_max_topics,
                    max_tokens=eff_max_tokens,
                    operational_mode=True,
                )
                chunk_outcomes.append(
                    DiscoveryChunkOutcome(
                        chunk_id=chunk.document_id,
                        status="SUCCESS",
                        result=res,
                    )
                )
                successful_results.append(res)
            except FATAL_BACKEND_ERRORS:
                raise
            except Exception as exc:
                chunk_outcomes.append(
                    DiscoveryChunkOutcome(
                        chunk_id=chunk.document_id,
                        status="FAILED",
                        result=None,
                        error_type=type(exc).__name__,
                        safe_error_code="CHUNK_DISCOVERY_ERROR",
                    )
                )

        if not successful_results:
            raise OperationalAnalysisFailedError(
                f"Tutti i {len(chunks)} chunk di Topic Discovery sono falliti durante l'inferenza."
            )

        aggregated = aggregate_topic_discovery(
            chunk_results=successful_results,
            parent_document_id=document.document_id,
            max_topics=eff_max_topics,
        )

        latency = round(time.perf_counter() - start_time, 3)
        completed_count = len(successful_results)
        failed_count = len(chunks) - completed_count
        now_ts = datetime.now(timezone.utc).isoformat()

        run_meta = TopicAnalysisRunMetadata(
            run_id=run_id,
            origin=AnalysisOrigin.LIVE_LM_STUDIO if isinstance(target_client, LmStudioClient) else AnalysisOrigin.MOCK_INSPECTION,
            model=resolved_model,
            requested_model=(model_name or "").strip() or self.default_model,
            latency_seconds=latency,
            prompt_version="topic_discovery_v1",
            strategy="DIRECT_MULTILINGUAL",
            temperature=eff_temp,
            seed=eff_seed,
            chunk_count=int(len(chunks)),
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
            status="PARTIAL" if failed_count > 0 else "COMPLETE",
            error_classification="PARTIAL_CHUNK_FAILURE" if failed_count > 0 else None,
        )

        meta = dict(aggregated.metadata)
        meta.update(run_meta.to_dict())
        meta["chunk_count"] = int(len(chunks))
        meta["expected_chunk_count"] = int(len(chunks))
        meta["completed_chunk_count"] = int(completed_count)
        meta["failed_chunk_count"] = int(failed_count)
        if failed_count > 0:
            meta["status"] = "PARTIAL"
        else:
            meta["status"] = "COMPLETE"

        if not should_preserve_raw and "raw_response" in meta:
            del meta["raw_response"]

        final_res = TopicDiscoveryResult(
            topics=aggregated.topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )
        self._authoritative_live_run_ids.add(run_id)
        self._record_authoritative_discovery_run(document.document_id, final_res)
        return final_res

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
        )

        # Validazione degli evidence_ids restituiti dalla detection rispetto al documento
        for eid in det_result.evidence_ids:
            if eid not in valid_evidence_ids:
                raise TopicProvenanceError(
                    f"Evidence ID estraneo '{eid}' restituito dalla Topic Detection: "
                    f"non appartiene al documento '{document.document_id}'."
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

