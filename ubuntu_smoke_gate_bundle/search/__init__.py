"""
search
======
Search Layer Foundation per l'analisi forense e l'interrogazione delle chat.

Fornisce ricerca deterministica, indicizzazione O(1) delle evidenze multimodali,
lookup sui risultati di Topic Detection e Topic Discovery e adapter per la UI,
in totale assenza di dipendenze da runtime LLM, vector database o chiamate di rete.
"""
from __future__ import annotations

from search.engine import EvidenceSearchEngine
from search.index import EvidenceIndex, EvidenceIntegrityError, EvidenceNotFoundError
from search.models import (
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    MatchMode,
    SearchViewResult,
    TopicSearchHit,
    TopicSearchResult,
)
from search.normalization import (
    normalize_for_search,
    tokenize_query_terms,
    tokenize_terms,
)
from search.service import SearchService
from search.topics import TopicEvidenceIntegrityError, TopicSearchEngine
from search.views import hit_to_view_result, hits_to_view_results

__all__ = [
    "MatchMode",
    "EvidenceSearchQuery",
    "EvidenceSearchHit",
    "EvidenceSearchResult",
    "SearchViewResult",
    "TopicSearchHit",
    "TopicSearchResult",
    "normalize_for_search",
    "tokenize_terms",
    "tokenize_query_terms",
    "EvidenceIndex",
    "EvidenceIntegrityError",
    "EvidenceNotFoundError",
    "TopicEvidenceIntegrityError",
    "EvidenceSearchEngine",
    "TopicSearchEngine",
    "SearchService",
    "hit_to_view_result",
    "hits_to_view_results",
]
