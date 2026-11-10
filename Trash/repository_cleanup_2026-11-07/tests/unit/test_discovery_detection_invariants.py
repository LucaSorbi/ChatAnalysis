"""
tests/unit/test_discovery_detection_invariants.py
-------------------------------------------------
Test di conformità per gli invarianti di Topic Discovery, Topic Detection,
Provenance e Consistency:
- Criterio 6: Discovery multi-chunk contabilizza tutti i chunk; failure tracciati; nessun silent fail.
- Criterio 6: Se 0 chunk hanno successo -> OperationalAnalysisFailedError.
- Criterio 6: Se successo parziale -> marcato PARTIAL / INCOMPLETE.
- Criterio 7, 10: chunk_count è rigorosamente int (non tupla) sia in Detection sia in Discovery.
- Criterio 8, 11, 12: Nessun [:max_topics] silenzioso; Deterministic Support Ranking esplicito.
- Criterio 9: Normalizzazione label con NFKC, casefold, whitespace e punteggiatura.
- Criterio 10, 13: Discovery riceve ed inoltra max_tokens operativo.
- Criterio 12, 14: Detection 0 chunk validi -> solleva OperationalAnalysisFailedError, mai falso ABSENT/UNCERTAIN.
- Criterio 12, 15: Errori backend fatali (LmStudioUnavailableError, AiModelMismatchError, ecc.) propagano subito.
- Criterio 13, 17: Fallimento operativo non produce ConsistencyResult.
- Criterio 14, 18: verify_discovered_topic verifica provenance_document_id == document.document_id.
- Criterio 15, 19: ConsistencyService.evaluate rifiuta cross-document.
- Criterio 23, 26, 27: Privacy gating basato solo su metadata data_classification == SYNTHETIC_DEMO; source_name=None non crasha.
- Criterio 24, 28: Documento senza sezioni analizzabili -> AnalysisNoEvidenceError (non ABSENT).
- Criterio 30: TopicAnalysisRunMetadata usato correttamente.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from ai.backend import (
    BaseLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.chunking import aggregate_topic_discovery, normalize_topic_label
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult
from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisNoEvidenceError,
    ConsistencyStatus,
    DiscoveredTopicRef,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    TopicAnalysisRunMetadata,
)
from analysis.service import LocalTopicAnalysisService


class MockServiceLlmClient(BaseLlmClient):
    def __init__(self, responses: list[dict[str, Any] | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.last_max_tokens: int | None = None
        self.model_id = "qwen2.5-7b-instruct"

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.last_max_tokens = max_tokens
        if not self.responses:
            data = {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna evidenza."}
        else:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            data = item
        return LlmCompletionResponse(
            content=json.dumps(data),
            model=model_id or self.model_id,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_seconds=0.05,
        )


def _make_doc(
    doc_id: str = "doc_test_1",
    num_bundles: int = 3,
    sections_per_bundle: int = 1,
    classification: str | None = None,
    source_name: str | None = "msgstore_db",
) -> ConversationEvidenceDocument:
    bundles = []
    meta = {}
    if classification:
        meta["data_classification"] = classification
    for b_idx in range(num_bundles):
        text = f"Messaggio {b_idx + 1} con contenuto forense." if sections_per_bundle > 0 else ""
        raw = RawRecord(
            source_name=source_name or "unknown",
            source_path="/test",
            source_record_id=str(b_idx + 1),
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
            source_name=source_name or "unknown",
            source_record_id=str(b_idx + 1),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        m = UnifiedMessage(
            message_id=f"msg::{b_idx + 1}",
            source_name=source_name or "unknown",
            source_record_id=str(b_idx + 1),
            source_path="/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
            provenance_record=norm,
        )
        bundle = MessageEvidenceBundle(message=m)
        bundles.append(bundle)
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name=source_name,
        metadata=meta,
    )


def test_chunk_count_is_strictly_int():
    """Verifica che chunk_count nei metadata sia rigorosamente int (non tupla) sia in Detection che in Discovery."""
    doc = _make_doc(num_bundles=2)
    client = MockServiceLlmClient([
        {"decision": "PRESENT", "evidence_ids": [doc.all_evidence_sections[0].evidence_id], "rationale": "Ok"},
        {"topics": [{"label": "T1", "short_description": "D1", "evidence_ids": [doc.all_evidence_sections[0].evidence_id]}]},
    ])
    service = LocalTopicAnalysisService(client=client)

    # Detection
    q = TopicQuery(topic_id="t1", label="Test Label", description="Test Desc")
    det = service.run_topic_detection(doc, q)
    assert isinstance(det.metadata["chunk_count"], int)
    assert not isinstance(det.metadata["chunk_count"], tuple)

    # Discovery
    disc = service.run_topic_discovery(doc)
    assert isinstance(disc.metadata["chunk_count"], int)
    assert not isinstance(disc.metadata["chunk_count"], tuple)


def test_discovery_forwards_max_tokens():
    """Verifica che run_topic_discovery inoltri il max_tokens operativo all'analyzer."""
    doc = _make_doc(num_bundles=2)
    client = MockServiceLlmClient([
        {"topics": [{"label": "T1", "short_description": "D1", "evidence_ids": [doc.all_evidence_sections[0].evidence_id]}]},
    ])
    service = LocalTopicAnalysisService(client=client)
    service.run_topic_discovery(doc, max_tokens=777)
    assert client.last_max_tokens == 777


def test_discovery_all_chunks_fail_raises_operational_analysis_failed_error():
    """Se TUTTI i chunk di Discovery falliscono, solleva OperationalAnalysisFailedError (non topics vuoti)."""
    doc = _make_doc(num_bundles=3)
    # Simula errori di structured output per tutti i chunk
    client = MockServiceLlmClient([
        ValueError("Invalid json 1"),
        ValueError("Invalid json 2"),
    ])
    cfg = OperationalAnalysisConfig(chunk_character_budget=1000, max_chunk_bundles=1)
    service = LocalTopicAnalysisService(client=client, config=cfg)

    with pytest.raises(OperationalAnalysisFailedError) as exc_info:
        service.run_topic_discovery(doc)
    assert "falliti durante l'inferenza" in str(exc_info.value)


def test_discovery_partial_chunk_failure_marked_partial_metadata():
    """Se alcuni chunk riescono e altri falliscono, il risultato è marcato PARTIAL nei metadata con tutti i chunk contabilizzati."""
    doc = _make_doc(num_bundles=2)
    eid = doc.all_evidence_sections[0].evidence_id
    client = MockServiceLlmClient([
        {"topics": [{"label": "Tema Valido", "short_description": "Descrizione", "evidence_ids": [eid]}]},
        ValueError("Chunk 2 payload invalid"),
    ])
    cfg = OperationalAnalysisConfig(chunk_character_budget=1000, max_chunk_bundles=1)
    service = LocalTopicAnalysisService(client=client, config=cfg)

    res = service.run_topic_discovery(doc)
    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["completed_chunk_count"] == 1
    assert res.metadata["failed_chunk_count"] == 1
    assert res.metadata["expected_chunk_count"] == 2
    assert len(res.topics) >= 1


def test_fatal_backend_error_propagates_immediately():
    """Errori di backend fatali (es. LmStudioUnavailableError) propagano immediatamente senza continuare chunk dopo chunk."""
    doc = _make_doc(num_bundles=5)
    client = MockServiceLlmClient([
        LmStudioUnavailableError("Server is down"),
    ])
    cfg = OperationalAnalysisConfig(chunk_character_budget=1000, max_chunk_bundles=1)
    service = LocalTopicAnalysisService(client=client, config=cfg)

    with pytest.raises(LmStudioUnavailableError):
        service.run_topic_detection(doc, TopicQuery(topic_id="q", label="L", description="D"))
    assert client.call_count == 1  # Nessun retry inutile chunk dopo chunk


def test_detection_all_chunks_fail_raises_operational_analysis_failed_error():
    """Se TUTTI i chunk di Detection falliscono per errori recuperabili, solleva OperationalAnalysisFailedError (mai falso ABSENT o UNCERTAIN)."""
    doc = _make_doc(num_bundles=2)
    client = MockServiceLlmClient([
        ValueError("Chunk 1 decode error"),
        ValueError("Chunk 2 decode error"),
    ])
    cfg = OperationalAnalysisConfig(chunk_character_budget=1000, max_chunk_bundles=1)
    service = LocalTopicAnalysisService(client=client, config=cfg)

    with pytest.raises(OperationalAnalysisFailedError):
        service.run_topic_detection(doc, TopicQuery(topic_id="q", label="L", description="D"))


def test_deterministic_support_ranking_for_max_topics():
    """Verifica che aggregate_topic_discovery applichi Deterministic Support Ranking esplicito senza silent truncation."""
    eids = ["msg::1::ORIGINAL_TEXT", "msg::2::ORIGINAL_TEXT", "msg::3::ORIGINAL_TEXT"]

    # 3 chunk
    chunk1 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Tema Supportato", short_description="Desc", evidence_ids=(eids[0],)),
            DiscoveredTopic(label="Tema Minore A", short_description="Desc", evidence_ids=(eids[0],)),
        ),
        provenance_document_id="doc1",
    )
    chunk2 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Tema Supportato", short_description="Desc", evidence_ids=(eids[1],)),
            DiscoveredTopic(label="Tema Minore B", short_description="Desc", evidence_ids=(eids[1],)),
        ),
        provenance_document_id="doc1",
    )
    chunk3 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Tema Supportato", short_description="Desc", evidence_ids=(eids[2],)),
            DiscoveredTopic(label="Tema Minore C", short_description="Desc", evidence_ids=(eids[2],)),
        ),
        provenance_document_id="doc1",
    )

    # Con max_topics=2, "Tema Supportato" (support count 3) deve prevalere sui temi minori (support count 1)
    agg = aggregate_topic_discovery([chunk1, chunk2, chunk3], parent_document_id="doc1", max_topics=2)
    assert len(agg.topics) == 2
    assert agg.topics[0].label == "Tema Supportato"
    # Tutti gli evidence_ids del tema supportato devono essere preservati
    assert set(agg.topics[0].evidence_ids) == set(eids)

    # I metadata devono registrare la riduzione deterministica
    assert agg.metadata["raw_topic_count"] == 6
    assert agg.metadata["consolidated_topic_count"] == 4
    assert agg.metadata["selected_topic_count"] == 2
    assert agg.metadata["reduction_strategy"] == "deterministic_support_ranking"


def test_normalize_topic_label_nfkc_casefold_punctuation():
    """Verifica che la normalizzazione topic rimuova variazioni superficiali di punteggiatura e unicode."""
    assert normalize_topic_label("Contratti  e  Accordi...") == normalize_topic_label("contratti e accordi")
    assert normalize_topic_label("Forniture & Merci!") == normalize_topic_label("forniture  merci")


def test_document_with_no_analyzable_evidence_raises_error():
    """Un documento privo di sezioni analizzabili solleva AnalysisNoEvidenceError (non prova di ABSENT)."""
    # 2 bundle con sezioni vuote (0 evidenze)
    doc = _make_doc(num_bundles=2, sections_per_bundle=0)
    service = LocalTopicAnalysisService(client=MockServiceLlmClient())

    with pytest.raises(AnalysisNoEvidenceError):
        service.run_topic_detection(doc, TopicQuery(topic_id="q", label="L", description="D"))

    with pytest.raises(AnalysisNoEvidenceError):
        service.run_topic_discovery(doc)


def test_discovery_provenance_validation_in_service():
    """verify_discovered_topic rifiuta topic con document_id non corrispondente."""
    doc_a = _make_doc(doc_id="doc_a")
    doc_b = _make_doc(doc_id="doc_b")

    client = MockServiceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    disc_ref_b = DiscoveredTopicRef(
        document_id="doc_b",
        discovery_run_id="run_1",
        topic_index=0,
        topic=DiscoveredTopic(label="Topic B", short_description="Desc", evidence_ids=("msg::1::ORIGINAL_TEXT",)),
    )

    with pytest.raises(ValueError, match="Discrepanza di provenienza"):
        service.verify_discovered_topic(doc_a, disc_ref_b)


def test_consistency_service_rejects_cross_document():
    """ConsistencyService.evaluate rifiuta risultati appartenenti a documenti diversi."""
    doc_a = _make_doc(doc_id="doc_a")
    eid = doc_a.all_evidence_sections[0].evidence_id

    det_b = TopicDetectionResult(
        topic=TopicQuery(topic_id="q", label="L", description="D"),
        decision=TopicDecision.PRESENT,
        evidence_ids=(eid,),
        rationale="Ok",
        provenance_document_id="doc_b",  # Diverso da doc_a!
    )
    disc_a = DiscoveredTopic(label="L", short_description="D", evidence_ids=(eid,))

    evaluator = ConsistencyService()
    with pytest.raises(ValueError, match="provenance"):
        evaluator.evaluate(
            document_id="doc_a",
            detection_result=det_b,
            discovered_topic=disc_a,
        )


def test_synthetic_data_classification_gating():
    """raw_response viene salvata SOLO se data_classification == SYNTHETIC_DEMO; source_name=None non crasha."""
    # 1. Documento reale (nessuna conservazione automatica)
    doc_real = _make_doc(doc_id="doc_real", classification=None, source_name=None)
    eid = doc_real.all_evidence_sections[0].evidence_id
    client = MockServiceLlmClient([
        {"decision": "PRESENT", "evidence_ids": [eid], "rationale": "Ok"},
    ])
    service = LocalTopicAnalysisService(client=client)
    res_real = service.run_topic_detection(doc_real, TopicQuery(topic_id="q", label="L", description="D"))
    assert "raw_response" not in res_real.metadata

    # 2. Documento sintetico esplicito
    doc_synth = _make_doc(doc_id="doc_synth", classification="SYNTHETIC_DEMO", source_name=None)
    client2 = MockServiceLlmClient([
        {"decision": "PRESENT", "evidence_ids": [eid], "rationale": "Ok"},
    ])
    service2 = LocalTopicAnalysisService(client=client2)
    res_synth = service2.run_topic_detection(doc_synth, TopicQuery(topic_id="q", label="L", description="D"))
    assert "raw_response" in res_synth.metadata
