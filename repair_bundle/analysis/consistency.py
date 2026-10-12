"""
analysis/consistency.py
-----------------------
Servizio autoritativo per la valutazione della consistenza tra Topic Discovery e Topic Detection.

Principi architetturali (FASE 7):
- LOGICA DI BUSINESS DISACCOPPIATA DALLA VIEW: Nessun calcolo di consistenza in presentation.py.
- VALUTAZIONE STRUTTURATA: Distinzione tra CONSISTENT (con overlap), PARTIALLY_CONSISTENT (senza overlap),
  DISCREPANCY_ABSENT e DISCREPANCY_UNCERTAIN.
- INTEGRITÀ: Errori operativi non sono convertiti in status di consistency, ma propagati come eccezioni.
"""
from __future__ import annotations

from typing import Sequence

from ai.models import (
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
)
from analysis.models import ConsistencyResult, ConsistencyStatus


class ConsistencyService:
    """
    Servizio per il confronto indipendente tra un topic scoperto (Topic Discovery)
    e la sua successiva verifica mirata (Topic Detection).
    """

    @staticmethod
    def evaluate(
        document_id: str,
        discovered_topic: DiscoveredTopic,
        detection_result: TopicDetectionResult,
        discovery_run_id: str | None = None,
        detection_run_id: str | None = None,
    ) -> ConsistencyResult:
        """
        Valuta deterministicamente la consistenza tra Discovery e Detection.

        Regole:
        1. Se Detection = PRESENT:
           - Con almeno 1 evidence_id in comune -> CONSISTENT
           - Con 0 evidence_id in comune -> PARTIALLY_CONSISTENT (entrambi confermano il tema, ma su evidenze distinte)
        2. Se Detection = ABSENT:
           -> DISCREPANCY_ABSENT
        3. Se Detection = UNCERTAIN:
           -> DISCREPANCY_UNCERTAIN
        """
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("document_id deve essere una stringa non vuota.")
        if not isinstance(discovered_topic, DiscoveredTopic):
            raise TypeError(f"discovered_topic deve essere DiscoveredTopic, ottenuto: {type(discovered_topic)}")
        if not isinstance(detection_result, TopicDetectionResult):
            raise TypeError(f"detection_result deve essere TopicDetectionResult, ottenuto: {type(detection_result)}")

        discovery_eids = tuple(discovered_topic.evidence_ids)
        detection_eids = tuple(detection_result.evidence_ids)

        overlap = tuple(eid for eid in discovery_eids if eid in detection_eids)

        decision = detection_result.decision

        if decision == TopicDecision.PRESENT:
            if overlap:
                status = ConsistencyStatus.CONSISTENT
                rationale = (
                    f"Topic confermato PRESENT da entrambe le inferenze indipendenti con "
                    f"{len(overlap)} evidenza/e in comune ({', '.join(overlap)})."
                )
            else:
                status = ConsistencyStatus.PARTIALLY_CONSISTENT
                rationale = (
                    f"Topic confermato PRESENT da entrambe le inferenze indipendenti, ma su evidenze distinte "
                    f"(Discovery: {len(discovery_eids)} evidenze, Detection: {len(detection_eids)} evidenze)."
                )
        elif decision == TopicDecision.ABSENT:
            status = ConsistencyStatus.DISCREPANCY_ABSENT
            rationale = (
                f"Discrepanza: argomento individuato in Topic Discovery ma valutato ABSENT nella Topic Detection "
                f"indipendente. Rationale Detection: {detection_result.rationale}"
            )
        else:  # UNCERTAIN
            status = ConsistencyStatus.DISCREPANCY_UNCERTAIN
            rationale = (
                f"Esito incerto: argomento individuato in Topic Discovery ma valutato UNCERTAIN nella Topic Detection "
                f"indipendente. Rationale Detection: {detection_result.rationale}"
            )

        return ConsistencyResult(
            document_id=document_id,
            discovery_run_id=discovery_run_id,
            detection_run_id=detection_run_id,
            topic_label=discovered_topic.label,
            discovery_evidence_ids=discovery_eids,
            detection_evidence_ids=detection_eids,
            overlap_evidence_ids=overlap,
            status=status,
            rationale=rationale,
            metadata={
                "discovery_evidence_count": len(discovery_eids),
                "detection_evidence_count": len(detection_eids),
                "overlap_count": len(overlap),
                "detection_decision": decision.value,
            },
        )
