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

from typing import Iterable, Mapping, Optional, Sequence

from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
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
from ui.models import (
    DatasetMode,
    DocumentSummary,
    EvidenceFilterCriteria,
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


def get_system_status_info(streamlit_version: str) -> dict[str, str]:
    """
    Restituisce le informazioni diagnostiche e architetturali del sistema.
    """
    return {
        "search_layer": "READY",
        "local_ai_architecture": "REAL PILOT READY",
        "real_lm_studio_benchmark": "DEFERRED",
        "hardware_rationale": "AMD A8-7410 APU privo di istruzioni AVX2, incompatibile con il runtime GGUF/llama.cpp corrente di LM Studio (Invalid CPU architecture).",
        "streamlit_version": streamlit_version,
        "network_status": "NOT REQUIRED (100% offline e locale)",
        "semantic_search": "NOT IMPLEMENTED (ricerca deterministica senza vettori)",
        "embeddings": "NOT IMPLEMENTED (nessun embedding o vector database)",
        "real_file_ingestion": "DEFERRED TO NEXT PHASE (interfaccia preparatoria)",
    }
