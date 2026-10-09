"""
ui/application.py
-----------------
Application layer per la UI Streamlit.

Principi architetturali:
- LOGICA PURA E TESTABILE: Tutte le funzioni qui definite sono funzioni pure Python,
  completamente indipendenti dal rendering Streamlit e dal framework Web.
- DISACCOPPIAMENTO RIGOROSO: Non importa Streamlit, non apre socket di rete,
  non accede all'hardware né ad API esterne.
- INTEGRITÀ FORENSE: Utilizza SearchService e i modelli di dominio frozen,
  preservando la provenance originale e l'ordine naturale delle evidenze.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Mapping, Optional, Sequence

from ai.backend import (
    AiBackendProtocolError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    LmStudioUnavailableError,
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
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.models import (
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    MatchMode,
    SearchViewResult,
    TopicSearchHit,
    TopicSearchResult,
)
from search.service import SearchService
from search.views import hit_to_view_result, hits_to_view_results
from ui.demo import build_synthetic_demo_dataset
from ui.ingestion import ingest_file_payload
from ui.models import (
    DatasetMode,
    DocumentSummary,
    EvidenceFilterCriteria,
    ImportedConversationInfo,
    IngestionRequest,
    IngestionResult,
    SourceFormat,
    TopicFilterDecision,
)


def build_demo_session() -> tuple[
    ConversationEvidenceDocument,
    tuple[TopicDetectionResult, ...],
    tuple[TopicDiscoveryResult, ...],
    SearchService,
]:
    """
    Inizializza una sessione demo completa costruendo il dataset sintetico
    e l'istanza SearchService associata.
    """
    doc, detections, discoveries = build_synthetic_demo_dataset()
    service = SearchService(
        document=doc,
        detection_results=detections,
        discovery_results=discoveries,
    )
    return doc, detections, discoveries, service


def build_search_service(
    document: ConversationEvidenceDocument | None = None,
    detection_results: Sequence[TopicDetectionResult] | None = None,
    discovery_results: Sequence[TopicDiscoveryResult] | TopicDiscoveryResult | None = None,
) -> SearchService:
    """Costruisce in modo deterministico un'istanza di SearchService."""
    return SearchService(
        document=document,
        detection_results=detection_results,
        discovery_results=discovery_results,
    )


def summarize_document(
    document: ConversationEvidenceDocument,
    detection_results: Sequence[TopicDetectionResult] = (),
    discovery_results: Sequence[TopicDiscoveryResult] = (),
) -> DocumentSummary:
    """
    Calcola il riepilogo aggregato e deterministico per la schermata Panoramica.
    """
    sections = document.all_evidence_sections
    counts: dict[str, int] = {}
    languages: set[str] = set()

    for sec in sections:
        source_key = sec.source_type.value if hasattr(sec.source_type, "value") else str(sec.source_type)
        counts[source_key] = counts.get(source_key, 0) + 1
        if sec.language:
            languages.add(sec.language)

    # Conteggio topic scoperti
    disc_count = 0
    for dr in discovery_results:
        disc_count += len(dr.topics)

    return DocumentSummary(
        document_id=document.document_id,
        bundle_count=len(document.bundles),
        section_count=len(sections),
        counts_by_source_type=counts,
        languages=tuple(sorted(languages)),
        topic_detection_count=len(detection_results),
        topic_discovery_count=disc_count,
    )


def get_evidence_filter_options(document: ConversationEvidenceDocument) -> dict[str, list[str]]:
    """
    Estrae le opzioni di filtro disponibili nel documento per la vista di esplorazione.
    """
    sections = document.all_evidence_sections
    source_types: set[str] = set()
    languages: set[str] = set()
    source_names: set[str] = set()

    for sec in sections:
        st_val = sec.source_type.value if hasattr(sec.source_type, "value") else str(sec.source_type)
        source_types.add(st_val)
        if sec.language:
            languages.add(sec.language)
        if sec.source_name:
            source_names.add(sec.source_name)

    return {
        "source_types": ["TUTTI"] + sorted(source_types),
        "languages": ["TUTTE"] + sorted(languages),
        "source_names": ["TUTTE"] + sorted(source_names),
    }


def filter_evidence_sections(
    document: ConversationEvidenceDocument,
    criteria: EvidenceFilterCriteria,
) -> list[TextEvidenceSection]:
    """
    Filtra le sezioni del documento preservando l'ordine naturale delle evidenze.
    """
    sections = list(document.all_evidence_sections)
    filtered: list[TextEvidenceSection] = []

    for sec in sections:
        st_val = sec.source_type.value if hasattr(sec.source_type, "value") else str(sec.source_type)
        if criteria.source_type and criteria.source_type != "TUTTI" and st_val != criteria.source_type:
            continue
        if criteria.language and criteria.language != "TUTTE" and sec.language != criteria.language:
            continue
        if criteria.source_name and criteria.source_name != "TUTTE" and sec.source_name != criteria.source_name:
            continue
        filtered.append(sec)

    return filtered


def execute_evidence_search(
    search_service: SearchService,
    query_text: str,
    match_mode: MatchMode = MatchMode.PHRASE,
    source_type: Optional[str] = None,
    language: Optional[str] = None,
    source_name: Optional[str] = None,
    limit: Optional[int] = None,
) -> EvidenceSearchResult:
    """
    Costruisce la query ed esegue la ricerca testuale delegandola integralmente al SearchService.
    Ritorna un EvidenceSearchResult vuoto se query_text è vuoto o whitespace.
    """
    clean_text = (query_text or "").strip()
    if not clean_text:
        return EvidenceSearchResult(
            query=EvidenceSearchQuery(query_text="<empty>", match_mode=match_mode),
            total_hits=0,
            hits=(),
        )

    st_tuple = None
    if source_type and source_type != "TUTTI":
        st_tuple = (EvidenceSourceType(source_type),)

    lang_val = language if (language and language != "TUTTE") else None
    src_val = source_name if (source_name and source_name != "TUTTE") else None

    query = EvidenceSearchQuery(
        query_text=clean_text,
        match_mode=match_mode,
        source_types=st_tuple,
        language=lang_val,
        source_name=src_val,
        limit=limit,
    )
    return search_service.search_evidence(query)


def search_topic_detections(
    search_service: SearchService,
    query_text: Optional[str] = None,
    decision_filter: TopicFilterDecision = TopicFilterDecision.ALL,
    limit: Optional[int] = None,
) -> TopicSearchResult:
    """
    Interroga i risultati di Topic Detection tramite SearchService.
    """
    decision_val = None
    if decision_filter != TopicFilterDecision.ALL:
        decision_val = TopicDecision(decision_filter.value)

    res = search_service.search_detections(
        query_text=(query_text.strip() if query_text and query_text.strip() else None),
        decision=decision_val,
    )
    if limit is not None and limit > 0 and len(res.hits) > limit:
        return TopicSearchResult(
            total_hits=res.total_hits,
            hits=res.hits[:limit],
            metadata=dict(res.metadata),
        )
    return res


def search_topic_discoveries(
    search_service: SearchService,
    query_text: Optional[str] = None,
    limit: Optional[int] = None,
) -> TopicSearchResult:
    """
    Interroga gli argomenti di Open Topic Discovery tramite SearchService.
    """
    res = search_service.search_discoveries(
        query_text=(query_text.strip() if query_text and query_text.strip() else None),
    )
    if limit is not None and limit > 0 and len(res.hits) > limit:
        return TopicSearchResult(
            total_hits=res.total_hits,
            hits=res.hits[:limit],
            metadata=dict(res.metadata),
        )
    return res


def search_all_topic_views(
    search_service: SearchService,
    query_text: Optional[str] = None,
    decision_filter: TopicFilterDecision = TopicFilterDecision.ALL,
) -> tuple[SearchViewResult, ...]:
    """
    Restituisce tutti i risultati tematici come DTO SearchViewResult per la UI.
    """
    decision_val = None
    if decision_filter != TopicFilterDecision.ALL:
        decision_val = TopicDecision(decision_filter.value)

    clean_text = query_text.strip() if query_text and query_text.strip() else None
    return search_service.search_topic_views(query_text=clean_text, decision=decision_val)


def execute_real_ingestion(request: IngestionRequest) -> IngestionResult:
    """
    Esegue l'ingestion reale di un file caricato delegando all'ingestion bridge.
    Funzione pura applicativa senza dipendenze Streamlit.
    """
    return ingest_file_payload(request)


def build_search_service_for_conversation(
    document: ConversationEvidenceDocument,
) -> SearchService:
    """
    Costruisce in modo deterministico un SearchService per una specifica
    conversazione estratta dal dataset reale, con risultati AI vuoti.
    """
    return SearchService(
        document=document,
        detection_results=(),
        discovery_results=(),
    )


def list_available_conversations(result: IngestionResult) -> list[ImportedConversationInfo]:
    """Restituisce l'elenco ordinato delle conversazioni disponibili nel risultato di ingestion."""
    return list(result.summary.conversations)


def get_system_status_info(streamlit_version: str) -> dict[str, str]:
    """
    Restituisce le informazioni diagnostiche e architetturali del sistema.
    """
    return {
        "search_layer": "READY",
        "local_ai_architecture": "READY - LOCAL LM STUDIO",
        "real_lm_studio_benchmark": "READY / EXTERNAL BENCHMARK RUNNER",
        "hardware_rationale": "PORTABLE / HOST-INDEPENDENT (nessun vincolo hardware hardcoded; configurazione registrata nei report benchmark)",
        "streamlit_version": streamlit_version,
        "network_status": "LOCAL ONLY / LOOPBACK FOR LM STUDIO",
        "semantic_search": "NOT IMPLEMENTED (ricerca deterministica senza vettori)",
        "embeddings": "NOT IMPLEMENTED (nessun embedding o vector database)",
        "real_file_ingestion": "INTEGRATED (WhatsApp msgstore, wa.db, TXT/ZIP export, Cellebrite CSV, JSON, XML)",
    }


def build_topic_query(
    label: str,
    description: str | None = None,
    topic_id: str | None = None,
) -> TopicQuery:
    """
    Costruisce un TopicQuery deterministico e validato.
    Se description è vuota o None, viene utilizzata la label come descrizione.
    Se topic_id non è specificato, viene generato un identificativo deterministico basato su slug e hash.
    """
    if not isinstance(label, str) or not label.strip():
        raise ValueError("L'argomento (label) deve essere una stringa non vuota.")

    clean_label = label.strip()
    if description is not None and isinstance(description, str) and description.strip():
        clean_desc = description.strip()
    else:
        clean_desc = clean_label

    if not topic_id:
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", clean_label.lower()).strip("_")[:24] or "topic"
        h = hashlib.sha256(f"{clean_label}::{clean_desc}".encode("utf-8")).hexdigest()[:8]
        topic_id = f"custom_{slug}_{h}"

    return TopicQuery(topic_id=topic_id, label=clean_label, description=clean_desc)


def check_lm_studio_status(
    base_url: str = "http://127.0.0.1:1234",
    client: BaseLlmClient | None = None,
) -> tuple[bool, tuple[str, ...], str | None]:
    """
    Verifica la disponibilità di LM Studio locale ed elenca i modelli disponibili.
    Restituisce (is_available, models_tuple, error_message).
    """
    target_client = client or LmStudioClient(base_url=base_url)
    try:
        if hasattr(target_client, "is_available"):
            if not target_client.is_available():
                return False, (), f"LM Studio non raggiungibile su {base_url}."
        if hasattr(target_client, "list_models"):
            models = target_client.list_models()
            return True, tuple(models), None
        return True, (), None
    except (LmStudioUnavailableError, AiBackendProtocolError, OSError) as exc:
        return False, (), f"Errore di connessione a LM Studio: {exc}"
    except Exception as exc:
        return False, (), f"Errore durante l'interrogazione di LM Studio: {exc}"


def prepare_operational_model(
    client: BaseLlmClient | None = None,
    base_url: str = "http://127.0.0.1:1234",
    requested_model: str | None = None,
    timeout_seconds: float = DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
) -> str:
    """
    Pre-flight e auto-load del modello locale per Topic Detection operativa.
    Garantisce che il modello sia installato e caricato senza richiedere comandi manuali CLI 'lms load'.
    """
    target = (requested_model or "").strip() or DEFAULT_OPERATIONAL_MODEL
    target_client = client or LmStudioClient(base_url=base_url)

    if hasattr(target_client, "is_available") and not target_client.is_available():
        raise LmStudioUnavailableError(
            f"LM Studio non raggiungibile su {base_url}. Verificare che il server locale sia attivo."
        )

    if hasattr(target_client, "ensure_model_loaded"):
        return target_client.ensure_model_loaded(
            model_id=target,
            context_length=DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
            timeout_seconds=timeout_seconds,
        )

    # Fallback per client generici / mock privi di ensure_model_loaded
    if hasattr(target_client, "list_models"):
        models = target_client.list_models()
        resolved = resolve_installed_model_id(target, models)
        if not resolved:
            display = get_model_display_name(target)
            raise AiModelNotInstalledError(f"Il modello {display} non è installato in LM Studio.")
        if hasattr(target_client, "is_model_loaded") and hasattr(target_client, "load_model"):
            if not target_client.is_model_loaded(resolved):
                target_client.load_model(
                    resolved,
                    context_length=DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
                    timeout_seconds=timeout_seconds,
                )
        return resolved

    return target


class TopicProvenanceMismatchError(ValueError):
    """Sollevata quando il topic scoperto appartiene a un documento diverso da quello attivo."""
    pass


def validate_discovery_provenance(
    discovery_provenance_id: str | None,
    active_document_id: str,
) -> None:
    """
    Verifica che il topic scoperto appartenga esattamente al documento attivo.
    Solleva TopicProvenanceMismatchError in caso di discrepanza.
    """
    if not isinstance(active_document_id, str) or not active_document_id.strip():
        raise ValueError("active_document_id deve essere una stringa non vuota.")
    if discovery_provenance_id is not None and discovery_provenance_id != active_document_id:
        raise TopicProvenanceMismatchError(
            f"Discrepanza di provenienza: discovery document '{discovery_provenance_id}' "
            f"!= active document '{active_document_id}'."
        )


def execute_manual_topic_detection(
    document: ConversationEvidenceDocument,
    topic: TopicQuery,
    client: BaseLlmClient | None = None,
    model_id: str | None = None,
    base_url: str = "http://127.0.0.1:1234",
    strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
    temperature: float = DEFAULT_OPERATIONAL_TEMPERATURE,
    seed: int | None = 42,
    timeout_seconds: float = DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
    max_tokens: int | None = DEFAULT_OPERATIONAL_MAX_TOKENS,
    operational_mode: bool = True,
) -> TopicDetectionResult:
    """
    Esegue una vera inferenza di Topic Detection sulla conversazione corrente
    utilizzando il motore TopicDetectionAnalyzer e il client LM Studio locale.
    Gestisce autonomamente pre-flight e auto-load del modello operativo senza
    richiedere comandi manuali CLI 'lms load'.
    """
    target_client = client or LmStudioClient(base_url=base_url, model_id=model_id)

    # Pre-flight e auto-load del modello (default: qwen2.5-7b-instruct, timeout: 240s)
    target_model = prepare_operational_model(
        client=target_client,
        base_url=base_url,
        requested_model=model_id,
        timeout_seconds=timeout_seconds,
    )

    if hasattr(target_client, "model_id"):
        target_client.model_id = target_model

    analyzer = TopicDetectionAnalyzer(
        client=target_client,
        default_model_id=target_model,
        operational_mode=operational_mode,
    )
    return analyzer.detect_topic(
        document=document,
        topic=topic,
        strategy=strategy,
        model_id=target_model,
        temperature=temperature,
        seed=seed,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        operational_mode=operational_mode,
    )


