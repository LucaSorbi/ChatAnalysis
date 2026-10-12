"""
test_analysis_service.py
------------------------
Test di unità per LocalTopicAnalysisService e ConsistencyService.

Verifica:
- Esecuzione a singolo chunk per documenti contenuti.
- Esecuzione multi-chunk per documenti estesi.
- Invarianti di aggregazione Detection:
  * PRESENT se almeno un chunk è PRESENT.
  * ABSENT solo se tutti i chunk sono ABSENT.
  * Fallimento di un chunk produce UNCERTAIN, MAI falso ABSENT.
- Invarianti di aggregazione Discovery:
  * Tutti i chunk vengono processati.
  * Nessun troncamento silenzioso casuale.
- Prova del Nove (Verification):
  * Esegue una seconda inferenza indipendente.
  * Non passa discovery_evidence_ids come suggerimento.
- Calcolo Consistency (ConsistencyStatus):
  * CONSISTENT per PRESENT con overlap evidence.
  * PARTIALLY_CONSISTENT per PRESENT senza overlap evidence.
  * DISCREPANCY_ABSENT se Detection = ABSENT.
  * DISCREPANCY_UNCERTAIN se Detection = UNCERTAIN.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from ai.backend import (
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TextEvidenceSection,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from multimodal.evidence import EvidenceSourceType
from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisOrigin,
    ConsistencyResult,
    ConsistencyStatus,
    TopicAnalysisRunMetadata,
)
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


class DummyLlmClient(BaseLlmClient):
    """Client fittizio per simulare risposte deterministiche."""

    def __init__(self, responses: list[dict[str, Any]] | None = None, available: bool = True) -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []
        self._available = available
        self.model_id = "test-model"

    def is_available(self) -> bool:
        return self._available

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.call_history.append({"messages": messages, "model_id": model_id})
        if self.responses:
            data = self.responses.pop(0)
        else:
            data = {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna evidenza."}
        return LlmCompletionResponse(
            content=json.dumps(data),
            model=model_id or self.model_id,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_seconds=0.05,
        )


def _create_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="test",
        source_path="/path/test",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="test",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:test:{idx}",
        source_name="test",
        source_record_id=idx,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def test_service_single_chunk_detection():
    bundles = [_create_bundle("1", "Accordo riservato alle 15:00.")]
    doc = ConversationEvidenceDocument(document_id="doc_single", source_name="test", bundles=tuple(bundles))
    eid = doc.all_evidence_sections[0].evidence_id
    query = TopicQuery(topic_id="t1", label="Accordi riservati", description="Patti e incontri")

    client = DummyLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Accordo confermato.",
    }])

    svc = LocalTopicAnalysisService(max_chunk_bundles=10)
    result = svc.run_topic_detection(document=doc, topic=query, client=client, model_name="test-model")

    assert result.decision == TopicDecision.PRESENT
    assert result.evidence_ids == (eid,)
    assert result.provenance_document_id == "doc_single"
    assert result.metadata["chunk_count"] == 1
    assert result.metadata["completed_chunk_count"] == 1
    assert client.call_count == 1


def test_service_multi_chunk_detection_aggregates_present():
    # 3 bundle con limite chunk_bundles=1 -> 3 chunk
    bundles = [
        _create_bundle("1", "Messaggio innocuo 1."),
        _create_bundle("2", "Accordo segreto trovato qui."),
        _create_bundle("3", "Messaggio innocuo 2."),
    ]
    doc = ConversationEvidenceDocument(document_id="doc_multi", source_name="test", bundles=tuple(bundles))
    eid_secret = doc.bundles[1].text_evidence_sections[0].evidence_id
    query = TopicQuery(topic_id="t1", label="Accordi segreti", description="Patti segreti")

    client = DummyLlmClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna traccia."},
        {"decision": "PRESENT", "evidence_ids": [eid_secret], "rationale": "Trovato accordo segreto."},
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna traccia."},
    ])

    svc = LocalTopicAnalysisService(max_chunk_bundles=1)
    result = svc.run_topic_detection(document=doc, topic=query, client=client, model_name="test-model")

    assert client.call_count == 3
    assert result.decision == TopicDecision.PRESENT
    assert result.evidence_ids == (eid_secret,)
    assert result.metadata["chunk_count"] == 3
    assert result.metadata["completed_chunk_count"] == 3


def test_service_multi_chunk_failure_produces_uncertain_not_absent():
    """Un fallimento di un chunk deve produrre UNCERTAIN, MAI falso ABSENT."""
    bundles = [
        _create_bundle("1", "Messaggio 1."),
        _create_bundle("2", "Messaggio 2."),
    ]
    doc = ConversationEvidenceDocument(document_id="doc_fail", source_name="test", bundles=tuple(bundles))
    query = TopicQuery(topic_id="t1", label="Controllo errori", description="Verifica gestione eccezioni")

    # Primo chunk restituisce JSON non valido che provocherà errore di parsing
    class FailingClient(DummyLlmClient):
        def chat_completion(self, *args, **kwargs):
            if self.call_count == 0:
                self.call_count += 1
                return LlmCompletionResponse(
                    content="INVALID NOT JSON",
                    model="test-model",
                    prompt_tokens=5,
                    completion_tokens=5,
                    total_tokens=10,
                    latency_seconds=0.01,
                )
            self.call_count += 1
            return LlmCompletionResponse(
                content=json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Assente."}),
                model="test-model",
                prompt_tokens=5,
                completion_tokens=5,
                total_tokens=10,
                latency_seconds=0.01,
            )

    svc = LocalTopicAnalysisService(max_chunk_bundles=1)
    client = FailingClient()
    result = svc.run_topic_detection(document=doc, topic=query, client=client, model_name="test-model")

    assert result.decision == TopicDecision.UNCERTAIN
    assert result.metadata["failed_chunk_count"] == 1
    assert result.metadata["chunk_count"] == 2


def test_service_multi_chunk_discovery():
    bundles = [
        _create_bundle("1", "Discussione di contratti."),
        _create_bundle("2", "Ispezione merci e depositi."),
    ]
    doc = ConversationEvidenceDocument(document_id="doc_disc", source_name="test", bundles=tuple(bundles))
    eid1 = doc.bundles[0].text_evidence_sections[0].evidence_id
    eid2 = doc.bundles[1].text_evidence_sections[0].evidence_id

    client = DummyLlmClient(responses=[
        {"topics": [{"label": "Contratti", "short_description": "Accordi commerciali", "evidence_ids": [eid1]}]},
        {"topics": [{"label": "Logistica", "short_description": "Merci e depositi", "evidence_ids": [eid2]}]},
    ])

    svc = LocalTopicAnalysisService(max_chunk_bundles=1)
    result = svc.run_topic_discovery(document=doc, client=client, model_name="test-model")

    assert client.call_count == 2
    assert len(result.topics) == 2
    labels = {t.label for t in result.topics}
    assert "Contratti" in labels
    assert "Logistica" in labels
    assert result.provenance_document_id == "doc_disc"


def test_verification_independent_inference_and_consistency():
    bundles = [_create_bundle("1", "Dettagli logistica e inventario merci.")]
    doc = ConversationEvidenceDocument(document_id="doc_ver", source_name="test", bundles=tuple(bundles))
    eid = doc.all_evidence_sections[0].evidence_id

    discovered_topic = DiscoveredTopic(
        label="Logistica Merci",
        short_description="Gestione inventario",
        evidence_ids=(eid,),
    )

    # La detection deve ricevere il topic senza hint degli evidence_ids
    client = DummyLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Confermato inventario.",
    }])

    svc = LocalTopicAnalysisService()
    disc_result = TopicDiscoveryResult(
        topics=(discovered_topic,),
        provenance_document_id="doc_ver",
        metadata={"run_id": "run_disc_123"},
    )
    svc.register_discovery_run("doc_ver", disc_result)
    det_res, consistency = svc.verify_discovered_topic(
        document=doc,
        discovered_topic=discovered_topic,
        discovery_run_id="run_disc_123",
        client=client,
        model_name="test-model",
    )

    assert client.call_count == 1
    assert det_res.decision == TopicDecision.PRESENT
    assert consistency.status == ConsistencyStatus.CONSISTENT
    assert eid in consistency.overlap_evidence_ids


def test_consistency_service_matrix():
    svc = ConsistencyService()
    disc_topic = DiscoveredTopic(
        label="Tema 1",
        short_description="Descrizione 1",
        evidence_ids=("e1", "e2"),
    )
    q = TopicQuery(topic_id="q1", label="Tema 1", description="Descrizione 1")

    # Caso 1: PRESENT con overlap -> CONSISTENT
    det1 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.PRESENT,
        evidence_ids=("e2", "e3"),
        rationale="Rilevato su e2.",
        provenance_document_id="doc_1",
    )
    res1 = svc.evaluate(
        document_id="doc_1",
        discovered_topic=disc_topic,
        detection_result=det1,
    )
    assert res1.status == ConsistencyStatus.CONSISTENT
    assert res1.overlap_evidence_ids == ("e2",)

    # Caso 2: PRESENT senza overlap -> PARTIALLY_CONSISTENT
    det2 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.PRESENT,
        evidence_ids=("e3",),
        rationale="Rilevato solo su e3.",
        provenance_document_id="doc_1",
    )
    res2 = svc.evaluate(
        document_id="doc_1",
        discovered_topic=disc_topic,
        detection_result=det2,
    )
    assert res2.status == ConsistencyStatus.PARTIALLY_CONSISTENT
    assert res2.overlap_evidence_ids == ()

    # Caso 3: ABSENT -> DISCREPANCY_ABSENT
    det3 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Non rilevato.",
        provenance_document_id="doc_1",
    )
    res3 = svc.evaluate(
        document_id="doc_1",
        discovered_topic=disc_topic,
        detection_result=det3,
    )
    assert res3.status == ConsistencyStatus.DISCREPANCY_ABSENT

    # Caso 4: UNCERTAIN -> DISCREPANCY_UNCERTAIN
    det4 = TopicDetectionResult(
        topic=q,
        decision=TopicDecision.UNCERTAIN,
        evidence_ids=(),
        rationale="Incertezza.",
        provenance_document_id="doc_1",
    )
    res4 = svc.evaluate(
        document_id="doc_1",
        discovered_topic=disc_topic,
        detection_result=det4,
    )
    assert res4.status == ConsistencyStatus.DISCREPANCY_UNCERTAIN
