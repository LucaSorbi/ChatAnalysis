"""
analysis/service.py
-------------------
Servizio applicativo unificato per l'analisi tematica locale (Topic Detection e Topic Discovery).

Principi architetturali:
- UNICA FONTE DI ORCHESTRAZIONE AI: DEMO e FILE attraversano lo stesso identico service layer.
- CICLO DI VITA UNICO: Un solo client e una sola preparazione modello per operazione.
- CHUNKING INTEGRATO: Suddivisione trasparente di conversazioni grandi e aggregazione multi-chunk
  consapevole dei guasti (nessun troncamento silenzioso, nessun falso ABSENT su errore parziale).
- ISOLAMENTO STREAMLIT: Modulo puro Python, indipendente da framework web e interamente testabile.
- PRIVACY: Soppressione della raw LLM response su dati reali a meno di esplicita modalità diagnostica.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Any, Sequence

from ai.backend import (
    BaseLlmClient,
    BaseLocalLlmClient,
    LmStudioUnavailableError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
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
    get_model_display_name,
    resolve_installed_model_id,
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
    AnalysisOrigin,
    ConsistencyResult,
)


class LocalTopicAnalysisService:
    """
    Servizio unificato per l'esecuzione di Topic Detection e Topic Discovery
    mediante modelli linguistici locali (LM Studio).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        default_model: str = DEFAULT_OPERATIONAL_MODEL,
        context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
        timeout_seconds: float = DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
        max_chunk_characters: int = 7500,
        max_chunk_bundles: int = 25,
    ) -> None:
        self.base_url = base_url
        self.default_model = default_model
        self.context_length = context_length
        self.timeout_seconds = timeout_seconds
        self.max_chunk_characters = max_chunk_characters
        self.max_chunk_bundles = max_chunk_bundles

    def _prepare_client_and_model(
        self,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
    ) -> tuple[BaseLlmClient, str]:
        """
        Prepara in un singolo passaggio autoritativo il client e il modello operativo.
        """
        req_model = (
            (model_name or "").strip()
            or getattr(client, "model_id", None)
            or self.default_model
        )
        target_client = client or LmStudioClient(base_url=self.base_url, model_id=req_model)

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

        # Per client generici o mock che implementano list_models
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

    def run_topic_detection(
        self,
        document: ConversationEvidenceDocument,
        topic: TopicQuery,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        temperature: float = DEFAULT_OPERATIONAL_TEMPERATURE,
        seed: int | None = 42,
        max_tokens: int | None = DEFAULT_OPERATIONAL_MAX_TOKENS,
        operational_mode: bool = True,
        preserve_raw_response: bool = False,
    ) -> TopicDetectionResult:
        """
        Esegue Topic Detection con supporto trasparente al chunking multi-chunk.
        """
        if not document.bundles:
            return TopicDetectionResult(
                topic=topic,
                decision=TopicDecision.ABSENT,
                evidence_ids=(),
                rationale="La conversazione non contiene evidenze forensi da analizzare.",
                provenance_document_id=document.document_id,
                metadata={"status": "empty_document"},
            )

        run_id = f"run_det_{uuid.uuid4().hex[:10]}"
        start_time = time.perf_counter()

        target_client, resolved_model = self._prepare_client_and_model(client, model_name)

        analyzer = TopicDetectionAnalyzer(
            client=target_client,
            default_model_id=resolved_model,
            operational_mode=operational_mode,
        )

        # Suddivisione in chunk
        chunks = split_document_into_chunks(
            document,
            max_characters=self.max_chunk_characters,
            max_bundles=self.max_chunk_bundles,
        )

        is_synthetic = (
            document.source_name == "synthetic_demo"
            or "demo" in document.document_id.lower()
            or "synthetic" in document.source_name.lower()
        )
        should_preserve_raw = preserve_raw_response or is_synthetic

        if len(chunks) == 1:
            # Singolo chunk: esecuzione diretta
            result = analyzer.detect_topic(
                document=chunks[0],
                topic=topic,
                strategy=strategy,
                model_id=resolved_model,
                temperature=temperature,
                seed=seed,
                timeout_seconds=self.timeout_seconds,
                max_tokens=max_tokens,
                operational_mode=operational_mode,
            )
            # Riassegna provenance al documento padre
            meta = dict(result.metadata)
            meta["run_id"] = run_id
            meta["origin"] = (
                AnalysisOrigin.LIVE_LM_STUDIO.value
                if isinstance(target_client, LmStudioClient)
                else AnalysisOrigin.MOCK_INSPECTION.value
            )
            meta["model"] = resolved_model
            meta["requested_model"] = (model_name or "").strip() or self.default_model
            meta["chunk_count"] = 1
            meta["completed_chunk_count"] = 1
            if "latency_seconds" not in meta or meta["latency_seconds"] is None:
                meta["latency_seconds"] = time.perf_counter() - start_time
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
                    temperature=temperature,
                    seed=seed,
                    timeout_seconds=self.timeout_seconds,
                    max_tokens=max_tokens,
                    operational_mode=operational_mode,
                )
                outcomes.append(
                    ChunkAnalysisOutcome(
                        chunk_id=chunk.document_id,
                        result=res,
                        status="SUCCESS",
                    )
                )
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

        aggregated = aggregate_topic_detection(
            outcomes=outcomes,
            topic=topic,
            parent_document_id=document.document_id,
            expected_chunk_count=len(chunks),
        )

        meta = dict(aggregated.metadata)
        meta["run_id"] = run_id
        meta["origin"] = (
            AnalysisOrigin.LIVE_LM_STUDIO.value
            if isinstance(target_client, LmStudioClient)
            else AnalysisOrigin.MOCK_INSPECTION.value
        )
        meta["model"] = resolved_model
        meta["requested_model"] = (model_name or "").strip() or self.default_model
        meta["chunk_count"] = len(chunks)
        meta["completed_chunk_count"] = sum(1 for o in outcomes if o.status == "SUCCESS")
        meta["failed_chunk_count"] = sum(1 for o in outcomes if o.status != "SUCCESS")
        meta["latency_seconds"] = time.perf_counter() - start_time
        if not preserve_raw_response and "raw_response" in meta:
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
        max_topics: int = 10,
        temperature: float = DEFAULT_OPERATIONAL_TEMPERATURE,
        seed: int | None = 42,
        preserve_raw_response: bool = False,
    ) -> TopicDiscoveryResult:
        """
        Esegue Topic Discovery LIVE con supporto trasparente al chunking multi-chunk.
        """
        if not document.bundles:
            return TopicDiscoveryResult(
                topics=(),
                provenance_document_id=document.document_id,
                metadata={"status": "empty_document"},
            )

        run_id = f"run_disc_{uuid.uuid4().hex[:10]}"
        start_time = time.perf_counter()

        target_client, resolved_model = self._prepare_client_and_model(client, model_name)

        analyzer = TopicDiscoveryAnalyzer(
            client=target_client,
            default_model_id=resolved_model,
        )

        # Suddivisione in chunk
        chunks = split_document_into_chunks(
            document,
            max_characters=self.max_chunk_characters,
            max_bundles=self.max_chunk_bundles,
        )

        is_synthetic = (
            document.source_name == "synthetic_demo"
            or "demo" in document.document_id.lower()
            or "synthetic" in document.source_name.lower()
        )
        should_preserve_raw = preserve_raw_response or is_synthetic

        if len(chunks) == 1:
            disc_res = analyzer.discover_topics(
                document=chunks[0],
                model_id=resolved_model,
                temperature=temperature,
                seed=seed,
                timeout_seconds=self.timeout_seconds,
                max_topics=max_topics,
            )
            meta = dict(disc_res.metadata)
            meta["run_id"] = run_id
            meta["origin"] = (
                AnalysisOrigin.LIVE_LM_STUDIO.value
                if isinstance(target_client, LmStudioClient)
                else AnalysisOrigin.MOCK_INSPECTION.value
            )
            meta["model"] = resolved_model
            meta["requested_model"] = (model_name or "").strip() or self.default_model
            meta["chunk_count"] = 1
            meta["completed_chunk_count"] = 1
            if "latency_seconds" not in meta or meta["latency_seconds"] is None:
                meta["latency_seconds"] = time.perf_counter() - start_time
            if not should_preserve_raw and "raw_response" in meta:
                del meta["raw_response"]

            return TopicDiscoveryResult(
                topics=disc_res.topics,
                provenance_document_id=document.document_id,
                metadata=meta,
            )

        # Multi-chunk discovery
        chunk_results: list[TopicDiscoveryResult] = []
        for chunk in chunks:
            try:
                res = analyzer.discover_topics(
                    document=chunk,
                    model_id=resolved_model,
                    temperature=temperature,
                    seed=seed,
                    timeout_seconds=self.timeout_seconds,
                    max_topics=max_topics,
                )
                chunk_results.append(res)
            except Exception:
                # Se un chunk fallisce, continuiamo con i rimanenti senza arrestare l'intera discovery
                continue

        aggregated = aggregate_topic_discovery(
            chunk_results=chunk_results,
            parent_document_id=document.document_id,
            max_topics=max_topics,
        )

        meta = dict(aggregated.metadata)
        meta["run_id"] = run_id
        meta["origin"] = (
            AnalysisOrigin.LIVE_LM_STUDIO.value
            if isinstance(target_client, LmStudioClient)
            else AnalysisOrigin.MOCK_INSPECTION.value
        )
        meta["model"] = resolved_model
        meta["requested_model"] = (model_name or "").strip() or self.default_model
        meta["chunk_count"] = len(chunks),
        meta["completed_chunk_count"] = len(chunk_results)
        meta["latency_seconds"] = time.perf_counter() - start_time
        if not preserve_raw_response and "raw_response" in meta:
            del meta["raw_response"]

        return TopicDiscoveryResult(
            topics=aggregated.topics,
            provenance_document_id=document.document_id,
            metadata=meta,
        )

    def verify_discovered_topic(
        self,
        document: ConversationEvidenceDocument,
        discovered_topic: DiscoveredTopic,
        discovery_run_id: str | None = None,
        client: BaseLlmClient | None = None,
        model_name: str | None = None,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        temperature: float = DEFAULT_OPERATIONAL_TEMPERATURE,
        seed: int | None = 42,
        preserve_raw_response: bool = False,
    ) -> tuple[TopicDetectionResult, ConsistencyResult]:
        """
        Esegue la "Prova del Nove":
        Seconda inferenza di Topic Detection completamente INDIPENDENTE sul topic scoperto.

        Vincoli imperativi:
        - Riceve SOLO label e short_description del DiscoveredTopic.
        - NON riceve discovered_topic.evidence_ids (nessun suggerimento).
        - Esegue una nuova chiamata LLM completa sul documento.
        - Valuta la consistenza tramite ConsistencyService.
        """
        if not isinstance(discovered_topic, DiscoveredTopic):
            raise TypeError(f"discovered_topic deve essere DiscoveredTopic, ottenuto: {type(discovered_topic)}")

        # Costruisce la query di detection indipendente
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", discovered_topic.label.lower()).strip("_")[:24] or "topic"
        h = hashlib.sha256(f"{discovered_topic.label}::{discovered_topic.short_description}".encode("utf-8")).hexdigest()[:8]
        query = TopicQuery(
            topic_id=f"verify_{slug}_{h}",
            label=discovered_topic.label.strip(),
            description=discovered_topic.short_description.strip(),
        )

        # Seconda inferenza indipendente
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

        det_run_id = det_result.metadata.get("run_id")

        # Valutazione di consistenza
        consistency = ConsistencyService.evaluate(
            document_id=document.document_id,
            discovered_topic=discovered_topic,
            detection_result=det_result,
            discovery_run_id=discovery_run_id,
            detection_run_id=det_run_id,
        )

        return det_result, consistency
