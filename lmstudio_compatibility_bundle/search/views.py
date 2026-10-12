"""
search/views.py
---------------
Adapter immutabili per convertire hit di ricerca in SearchViewResult
uniformi per la futura UI Streamlit.

Principi architetturali:
1. DISACCOPPIAMENTO TOTALE: Nessuna dipendenza da Streamlit, HTML o CSS.
2. RAPPRESENTAZIONE UNIFORME E CHIARA:
   - display_text: testo principale da mostrare a schermo.
   - original_text: valorizzato esclusivamente per riscontri forensi di tipo EVIDENCE,
     rigorosamente None per i risultati AI.
3. PROVENANCE COMPLETA: I metadati di provenance sono incapsulati in mapping immutabili.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from search.models import EvidenceSearchHit, SearchViewResult, TopicSearchHit


def hit_to_view_result(hit: EvidenceSearchHit | TopicSearchHit) -> SearchViewResult:
    """
    Converte un singolo EvidenceSearchHit o TopicSearchHit in un SearchViewResult immutabile.
    """
    if isinstance(hit, EvidenceSearchHit):
        return SearchViewResult(
            result_type="EVIDENCE",
            title=f"[{hit.source_type.value}] {hit.message_id} ({hit.source_name})",
            display_text=hit.original_text,
            original_text=hit.original_text,
            evidence_id=hit.evidence_id,
            evidence_ids=(hit.evidence_id,),
            message_id=hit.message_id,
            source_type=hit.source_type,
            language=hit.language,
            topic_decision=None,
            provenance={
                "evidence_id": hit.evidence_id,
                "message_id": hit.message_id,
                "source_name": hit.source_name,
                "source_record_id": hit.source_record_id,
                "ordinal": hit.section.ordinal,
                "matched_terms": hit.matched_terms,
            },
        )
    elif isinstance(hit, TopicSearchHit):
        dec_suffix = f" [{hit.decision.value}]" if hit.decision is not None else ""
        first_msg_id = hit.matched_sections[0].message_id if hit.matched_sections else None
        return SearchViewResult(
            result_type=hit.source_kind,
            title=f"Topic: {hit.label}{dec_suffix}",
            display_text=hit.description,
            original_text=None,  # La descrizione generata dall'AI non è testo originale forense
            evidence_id=hit.evidence_ids[0] if hit.evidence_ids else None,
            evidence_ids=hit.evidence_ids,
            message_id=first_msg_id,
            source_type=None,
            language=None,
            topic_decision=hit.decision,
            provenance={
                "topic_id": hit.topic_id,
                "label": hit.label,
                "description": hit.description,
                "source_kind": hit.source_kind,
                "evidence_ids": hit.evidence_ids,
                "resolved_section_count": len(hit.matched_sections),
                "metadata": dict(hit.metadata),
            },
        )
    else:
        raise TypeError(f"Tipo di hit non supportato: {type(hit)}")


def hits_to_view_results(
    hits: Iterable[EvidenceSearchHit | TopicSearchHit],
) -> tuple[SearchViewResult, ...]:
    """
    Converte una sequenza ordinata di hit in una tupla immutabile di SearchViewResult.
    """
    return tuple(hit_to_view_result(h) for h in hits)
