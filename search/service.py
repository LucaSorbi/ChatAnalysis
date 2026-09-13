"""
search/service.py
-----------------
Servizio unificato (SearchService) per orchestrare l'indicizzazione, la ricerca sulle
evidenze testuali e l'interrogazione dei risultati tematici generati dal layer AI.

Principi architetturali:
1. SORGENTE AUTOREVOLE UNICA: Rifiuta l'ambiguità; se vengono forniti contemporaneamente
   sia 'document' che 'index', solleva un ValueError esplicito.
2. FACADE COMPLETO E DETERMINISTICO: Fornisce un punto d'accesso coerente e sicuro
   per i layer superiori e per la futura interfaccia Streamlit.
3. DISACCOPPIAMENTO RIGOROSO: Non invoca LLM, non usa vettori né database esterni,
   opera interamente in memoria in modo offline e deterministico.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from multimodal.evidence import TextEvidenceSection
from search.engine import EvidenceSearchEngine
from search.index import EvidenceIndex
from search.models import (
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    MatchMode,
    SearchViewResult,
    TopicSearchHit,
    TopicSearchResult,
)
from search.topics import TopicEvidenceIntegrityError, TopicSearchEngine
from search.views import hit_to_view_result, hits_to_view_results


class SearchService:
    """
    Servizio unificato di ricerca deterministica e forensic provenance.
    """

    def __init__(
        self,
        document: ConversationEvidenceDocument | None = None,
        index: EvidenceIndex | None = None,
        detection_results: Sequence[TopicDetectionResult] | None = None,
        discovery_results: Sequence[TopicDiscoveryResult] | TopicDiscoveryResult | None = None,
    ) -> None:
        # Point C: Scelta di una sola sorgente autorevole
        if document is not None and index is not None:
            raise ValueError(
                "Fornire 'document' o 'index', non entrambi contemporaneamente: scegliere una sola sorgente autorevole."
            )

        if index is not None:
            self._index = index
        elif document is not None:
            self._index = EvidenceIndex.from_document(document)
        else:
            self._index = EvidenceIndex(sections=())

        self._evidence_engine = EvidenceSearchEngine(self._index)
        self._topic_engine = TopicSearchEngine(
            evidence_index=self._index,
            detection_results=detection_results,
            discovery_results=discovery_results,
        )

    @property
    def index(self) -> EvidenceIndex:
        """Restituisce l'indice deterministico delle evidenze."""
        return self._index

    @property
    def evidence_engine(self) -> EvidenceSearchEngine:
        """Restituisce il motore di ricerca delle evidenze."""
        return self._evidence_engine

    @property
    def topic_engine(self) -> TopicSearchEngine:
        """Restituisce il motore di ricerca tematica."""
        return self._topic_engine

    # --- Evidence Operations ---

    def search_evidence(self, query: EvidenceSearchQuery) -> EvidenceSearchResult:
        """Esegue una query di ricerca deterministica sulle evidenze testuali."""
        return self._evidence_engine.search(query)

    def search_evidence_views(self, query: EvidenceSearchQuery) -> tuple[SearchViewResult, ...]:
        """Esegue la ricerca sulle evidenze restituendo una tupla di SearchViewResult per la UI."""
        result = self.search_evidence(query)
        return hits_to_view_results(result.hits)

    def resolve_evidence(self, evidence_id: str) -> TextEvidenceSection:
        """Risolve un evidence_id alla TextEvidenceSection originaria (solleva EvidenceNotFoundError se assente)."""
        return self._index.resolve(evidence_id)

    def get_evidence(self, evidence_id: str) -> TextEvidenceSection | None:
        """Risolve un evidence_id restituendo None se assente."""
        return self._index.get(evidence_id)

    # --- Topic Operations ---

    def search_detections(
        self,
        topic_id: str | None = None,
        decision: TopicDecision | str | None = None,
        query_text: str | None = None,
    ) -> TopicSearchResult:
        """Cerca e filtra sui risultati di Topic Detection."""
        return self._topic_engine.search_detections(
            topic_id=topic_id,
            decision=decision,
            query_text=query_text,
        )

    def search_discoveries(
        self,
        query_text: str | None = None,
    ) -> TopicSearchResult:
        """Cerca tra gli argomenti emersi da Open Topic Discovery."""
        return self._topic_engine.search_discoveries(query_text=query_text)

    def search_topic_views(
        self,
        query_text: str | None = None,
        decision: TopicDecision | str | None = None,
    ) -> tuple[SearchViewResult, ...]:
        """Restituisce i risultati tematici come SearchViewResult per la UI."""
        res = self._topic_engine.search_all(query_text=query_text, decision=decision)
        return hits_to_view_results(res.hits)
