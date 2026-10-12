"""
search/engine.py
----------------
Motore di ricerca deterministico per TextEvidenceSection.

Principi architetturali:
1. DETERMINISMO ASSOLUTO: L'ordine dei risultati è rigorosamente stabile e riproducibile:
   segue l'ordine originario del ConversationEvidenceDocument (ordine dei bundle -> ordine
   delle TextEvidenceSection nel bundle). Nessun riordinamento per timestamp o euristico.
2. MATCHING RIGOROSO (WHOLE-TERM PER ALL_TERMS/ANY_TERM):
   - PHRASE: sottostringa esatta continua post NFKC + casefold.
   - EXACT: uguaglianza dell'intero testo normalizzato.
   - ALL_TERMS / ANY_TERM: matching basato su token interi Unicode (set di termini),
     evitando falsi positivi di sottostringa (es. 'art' non matcha 'partita').
3. SEMANTICA TOTAL_HITS E LIMIT:
   - total_hits riflette sempre il numero totale effettivo di evidenze corrispondenti a query e filtri.
   - hits contiene solo i primi N risultati fino a limit (se specificato).
   - metadata traccia returned_hits e truncated.
4. ZERO MODIFICHE SULL'INPUT: Nessun oggetto sorgente (ConversationEvidenceDocument,
   MessageEvidenceBundle, TextEvidenceSection, UnifiedMessage) viene alterato.
5. PROVENANCE INTEGRALE: L'hit restituito contiene il testo sorgente esatto (original_text)
   e il puntatore immutabile all'oggetto TextEvidenceSection d'origine.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ai.models import ConversationEvidenceDocument
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.index import EvidenceIndex
from search.models import (
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    MatchMode,
)
from search.normalization import normalize_for_search, tokenize_terms


class EvidenceSearchEngine:
    """
    Motore deterministico per l'esecuzione di query di ricerca testuale
    su collezioni indicizzate di TextEvidenceSection.
    """

    def __init__(
        self,
        target: EvidenceIndex | ConversationEvidenceDocument | Iterable[TextEvidenceSection],
    ) -> None:
        if isinstance(target, EvidenceIndex):
            self._index = target
        elif isinstance(target, ConversationEvidenceDocument):
            self._index = EvidenceIndex.from_document(target)
        else:
            self._index = EvidenceIndex(sections=target)

    @property
    def index(self) -> EvidenceIndex:
        """Restituisce l'indice di evidenze sottostante."""
        return self._index

    def search(self, query: EvidenceSearchQuery) -> EvidenceSearchResult:
        """
        Esegue la ricerca deterministica delle evidenze secondo la query fornita.

        Parametri:
        ----------
        query : EvidenceSearchQuery
            Query immutabile con criteri di testo, modalità di matching e filtri opzionali.

        Restituisce:
        ------------
        EvidenceSearchResult
            Risultato aggregato immutabile con total_hits calcolato su tutte le sezioni
            e hits limitato ai primi N risultati se configurato.
        """
        if not isinstance(query, EvidenceSearchQuery):
            raise ValueError(f"query deve essere un'istanza di EvidenceSearchQuery, ricevuto {type(query)}")

        norm_query = normalize_for_search(query.query_text)
        query_terms = tokenize_terms(query.query_text)

        all_matching_hits: list[EvidenceSearchHit] = []

        # Scansione deterministica lineare nell'ordine originario del documento
        for section in self._index.all_sections:
            # 1. Filtro source_type
            if query.source_types is not None and section.source_type not in query.source_types:
                continue

            # 2. Filtro source_name
            if query.source_name is not None and section.source_name != query.source_name:
                continue

            # 3. Filtro language (case-insensitive)
            if query.language is not None:
                sec_lang = (section.language or "").strip().casefold()
                q_lang = query.language.strip().casefold()
                if sec_lang != q_lang:
                    continue

            # 4. Matching testuale deterministico
            norm_sec_text = normalize_for_search(section.text)
            matched_terms: tuple[str, ...] = ()
            is_match = False

            if query.match_mode == MatchMode.PHRASE:
                if norm_query in norm_sec_text:
                    is_match = True
                    matched_terms = (query.query_text.strip(),)

            elif query.match_mode == MatchMode.EXACT:
                if norm_query == norm_sec_text:
                    is_match = True
                    matched_terms = (query.query_text.strip(),)

            elif query.match_mode == MatchMode.ALL_TERMS:
                sec_terms_set = set(tokenize_terms(section.text))
                if query_terms and all(term in sec_terms_set for term in query_terms):
                    is_match = True
                    matched_terms = query_terms

            elif query.match_mode == MatchMode.ANY_TERM:
                sec_terms_set = set(tokenize_terms(section.text))
                found_terms = tuple(term for term in query_terms if term in sec_terms_set)
                if found_terms:
                    is_match = True
                    matched_terms = found_terms

            if is_match:
                hit = EvidenceSearchHit(
                    evidence_id=section.evidence_id,
                    source_type=section.source_type,
                    message_id=section.message_id,
                    source_name=section.source_name,
                    source_record_id=section.source_record_id,
                    language=section.language,
                    original_text=section.text,
                    section=section,
                    matched_terms=matched_terms,
                )
                all_matching_hits.append(hit)

        total_hits = len(all_matching_hits)

        # Applicazione semantica del limit
        if query.limit is not None:
            returned_hits = tuple(all_matching_hits[:query.limit])
        else:
            returned_hits = tuple(all_matching_hits)

        truncated = total_hits > len(returned_hits)
        metadata = {
            "document_id": self._index.document_id,
            "returned_hits": len(returned_hits),
            "total_hits": total_hits,
            "truncated": truncated,
        }

        return EvidenceSearchResult(
            query=query,
            hits=returned_hits,
            total_hits=total_hits,
            metadata=metadata,
        )
