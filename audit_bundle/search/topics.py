"""
search/topics.py
----------------
Motore di ricerca e interrogazione deterministica sui risultati AI già generati
(TopicDetectionResult e TopicDiscoveryResult).

Principi architetturali:
1. STRICT TOPIC → EVIDENCE PROVENANCE: Se esiste un EvidenceIndex e un risultato tematico
   cita evidence_ids, TUTTI gli ID devono essere rigorosamente risolvibili. Qualsiasi ID
   mancante solleva immediatamente TopicEvidenceIntegrityError (nessuna omissione silenziosa).
2. DOCUMENT PROVENANCE VALIDATION: Se l'EvidenceIndex possiede un document_id, tutti i risultati
   AI (detection e discovery) devono appartenere a tale medesimo documento
   (result.provenance_document_id == index.document_id), altrimenti solleva TopicEvidenceIntegrityError.
3. NESSUNA INFERENZA AI: I risultati AI sono considerati artefatti preesistenti e immutabili;
   il componente non effettua alcuna chiamata LLM o inferenza locale.
4. FILTRAGGIO E RICERCA DETERMINISTICI: Supporta lookup per topic_id, filtraggio per decision
   (PRESENT, ABSENT, UNCERTAIN) e ricerca testuale su label/description mediante NFKC + casefold.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ai.models import (
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from multimodal.evidence import TextEvidenceSection
from search.index import EvidenceIndex
from search.models import TopicSearchHit, TopicSearchResult
from search.normalization import normalize_for_search


class TopicEvidenceIntegrityError(ValueError):
    """
    Sollevata in caso di violazione dell'integrità probatoria o documentale
    tra i risultati tematici AI e l'EvidenceIndex del documento.
    """
    pass


class TopicSearchEngine:
    """
    Motore deterministico per l'interrogazione di risultati di Topic Detection e Topic Discovery
    con validazione stretta della provenance e integrità referenziale.
    """

    def __init__(
        self,
        evidence_index: EvidenceIndex | None = None,
        detection_results: Sequence[TopicDetectionResult] | None = None,
        discovery_results: Sequence[TopicDiscoveryResult] | TopicDiscoveryResult | None = None,
    ) -> None:
        self._index = evidence_index
        self._detections: tuple[TopicDetectionResult, ...] = (
            tuple(detection_results) if detection_results is not None else ()
        )

        if discovery_results is None:
            self._discoveries: tuple[TopicDiscoveryResult, ...] = ()
        elif isinstance(discovery_results, TopicDiscoveryResult):
            self._discoveries = (discovery_results,)
        else:
            self._discoveries = tuple(discovery_results)

        # 1. Validazione Document Provenance (Point B)
        if self._index is not None and self._index.document_id is not None:
            doc_id = self._index.document_id
            for det in self._detections:
                if det.provenance_document_id != doc_id:
                    raise TopicEvidenceIntegrityError(
                        f"Disallineamento document provenance in TopicDetectionResult: atteso '{doc_id}', "
                        f"trovato '{det.provenance_document_id}' per il topic '{det.topic.topic_id}'."
                    )
            for disc in self._discoveries:
                if disc.provenance_document_id != doc_id:
                    raise TopicEvidenceIntegrityError(
                        f"Disallineamento document provenance in TopicDiscoveryResult: atteso '{doc_id}', "
                        f"trovato '{disc.provenance_document_id}'."
                    )

        # 2. Validazione Referenziale degli evidence_ids verso l'EvidenceIndex (Point A)
        if self._index is not None:
            for det in self._detections:
                for eid in det.evidence_ids:
                    if eid not in self._index:
                        raise TopicEvidenceIntegrityError(
                            f"Evidence ID '{eid}' citato in TopicDetectionResult ('{det.topic.topic_id}') "
                            f"non è presente nell'EvidenceIndex del documento."
                        )
            for disc in self._discoveries:
                for topic in disc.topics:
                    for eid in topic.evidence_ids:
                        if eid not in self._index:
                            raise TopicEvidenceIntegrityError(
                                f"Evidence ID '{eid}' citato in TopicDiscoveryResult ('{topic.label}') "
                                f"non è presente nell'EvidenceIndex del documento."
                            )

    @property
    def evidence_index(self) -> EvidenceIndex | None:
        return self._index

    def _resolve_sections(self, evidence_ids: tuple[str, ...]) -> tuple[TextEvidenceSection, ...]:
        """
        Risolve in modo rigido e deterministico tutti gli evidence_ids verso le rispettive TextEvidenceSection.
        Solleva TopicEvidenceIntegrityError se anche un solo ID non esiste nell'indice.
        """
        if self._index is None or not evidence_ids:
            return ()
        resolved: list[TextEvidenceSection] = []
        for eid in evidence_ids:
            sec = self._index.get(eid)
            if sec is None:
                raise TopicEvidenceIntegrityError(
                    f"Evidence ID '{eid}' citato nei risultati AI non è presente nell'EvidenceIndex del documento."
                )
            resolved.append(sec)
        return tuple(resolved)

    def search_detections(
        self,
        topic_id: str | None = None,
        decision: TopicDecision | str | None = None,
        query_text: str | None = None,
    ) -> TopicSearchResult:
        """
        Filtra e ricerca sui risultati di TopicDetectionResult.

        Parametri:
        ----------
        topic_id : str | None
            Lookup esatto su topic_id (es. 'T01').
        decision : TopicDecision | str | None
            Filtro per decisione (PRESENT, ABSENT, UNCERTAIN).
        query_text : str | None
            Filtro testuale su label o description (ricerca case-insensitive NFKC).
        """
        target_decision: TopicDecision | None = None
        if decision is not None:
            target_decision = TopicDecision(decision) if not isinstance(decision, TopicDecision) else decision

        norm_query = normalize_for_search(query_text) if query_text and query_text.strip() else None

        hits: list[TopicSearchHit] = []

        for det in self._detections:
            # 1. Filtro topic_id
            if topic_id is not None and det.topic.topic_id != topic_id:
                continue

            # 2. Filtro decision
            if target_decision is not None and det.decision != target_decision:
                continue

            # 3. Filtro testuale su label o description
            if norm_query is not None:
                norm_label = normalize_for_search(det.topic.label)
                norm_desc = normalize_for_search(det.topic.description)
                if norm_query not in norm_label and norm_query not in norm_desc:
                    continue

            # Risoluzione evidence_ids in modalità strict
            matched_sections = self._resolve_sections(det.evidence_ids)

            hit = TopicSearchHit(
                topic_id=det.topic.topic_id,
                label=det.topic.label,
                description=det.topic.description,
                source_kind="TOPIC_DETECTION",
                decision=det.decision,
                evidence_ids=det.evidence_ids,
                matched_sections=matched_sections,
                raw_result=det,
                metadata={
                    "rationale": det.rationale,
                    "provenance_document_id": det.provenance_document_id,
                },
            )
            hits.append(hit)

        return TopicSearchResult(hits=tuple(hits), total_hits=len(hits))

    def search_discoveries(
        self,
        query_text: str | None = None,
    ) -> TopicSearchResult:
        """
        Cerca tra gli argomenti emersi da TopicDiscoveryResult.

        Parametri:
        ----------
        query_text : str | None
            Filtro testuale cercato nella label o nella short_description
            (ricerca deterministica NFKC + casefold).
        """
        norm_query = normalize_for_search(query_text) if query_text and query_text.strip() else None
        hits: list[TopicSearchHit] = []

        for disc_doc in self._discoveries:
            for topic in disc_doc.topics:
                # Filtro testuale su label o short_description
                if norm_query is not None:
                    norm_label = normalize_for_search(topic.label)
                    norm_desc = normalize_for_search(topic.short_description)
                    if norm_query not in norm_label and norm_query not in norm_desc:
                        continue

                matched_sections = self._resolve_sections(topic.evidence_ids)

                hit = TopicSearchHit(
                    topic_id=topic.label,
                    label=topic.label,
                    description=topic.short_description,
                    source_kind="TOPIC_DISCOVERY",
                    decision=None,
                    evidence_ids=topic.evidence_ids,
                    matched_sections=matched_sections,
                    raw_result=topic,
                    metadata={"provenance_document_id": disc_doc.provenance_document_id},
                )
                hits.append(hit)

        return TopicSearchResult(hits=tuple(hits), total_hits=len(hits))

    def search_all(
        self,
        query_text: str | None = None,
        decision: TopicDecision | str | None = None,
    ) -> TopicSearchResult:
        """
        Esegue la ricerca combinata su Topic Detection e Topic Discovery.
        """
        det_res = self.search_detections(decision=decision, query_text=query_text)
        disc_res = self.search_discoveries(query_text=query_text)

        combined = det_res.hits + disc_res.hits
        return TopicSearchResult(hits=combined, total_hits=len(combined))
