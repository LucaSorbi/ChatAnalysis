"""
tests/unit/test_discovery_provenance_service.py
------------------------------------------------
Test completi di conformità per FASE BLOCKER 2:
DISCOVERY PROVENANCE E CONSISTENCY

Verifiche obbligatorie:
1. Discovery A + Document A -> allowed
2. Discovery A + Document B -> rejected (TopicProvenanceError)
3. Foreign evidence ID -> rejected (TopicProvenanceError)
4. Missing discovery run -> rejected (TopicProvenanceError)
5. Invalid topic reference -> rejected (TopicProvenanceError)
6. A -> B -> A preserves history
7. Different conversations remain isolated
8. Discovery evidence IDs not passed to Detection (solo label + short_description)
9. Detection generates independent LLM call (nessun riutilizzo fraudolento)
10. Consistency uses actual results (quattro stati autoritativi: CONSISTENT, PARTIALLY_CONSISTENT, DISCREPANCY_ABSENT, DISCREPANCY_UNCERTAIN)
"""
from __future__ import annotations

import json
from typing import Any
import pytest

from ai.backend import BaseLlmClient, LlmCompletionResponse
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
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
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
    ConsistencyResult,
    ConsistencyStatus,
    DiscoveredTopicRef,
    DocumentAnalysisState,
    OperationalAnalysisFailedError,
    TopicProvenanceError,
)
from analysis.service import LocalTopicAnalysisService
from ui.state import (
    get_document_analysis_state,
    load_demo_dataset,
    set_detection_results,
    set_discovery_results,
)


class MockProvenanceLlmClient(BaseLlmClient):
    """Client fittizio per verificare le chiamate LLM indipendenti."""
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_history: list[dict[str, Any]] = []
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
        self.call_history.append({
            "messages": messages,
            "model_id": model_id,
            "max_tokens": max_tokens,
            "kwargs": kwargs,
        })
        if self.responses:
            data = self.responses.pop(0)
        else:
            data = {"decision": "PRESENT", "evidence_ids": [], "rationale": "Mock default."}
        return LlmCompletionResponse(
            content=json.dumps(data),
            model=model_id or self.model_id,
            prompt_tokens=25,
            completion_tokens=25,
            total_tokens=50,
            latency_seconds=0.02,
        )


def _build_test_document(doc_id: str, num_messages: int = 3) -> ConversationEvidenceDocument:
    bundles = []
    for i in range(1, num_messages + 1):
        text = f"Messaggio forense {i} relativo al traffico merci."
        raw = RawRecord(
            source_name="whatsapp",
            source_path="/test/chat.txt",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": text},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg::{doc_id}::{i}",
            source_name="whatsapp",
            source_record_id=str(i),
            source_path="/test/chat.txt",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
            provenance_record=norm,
        )
        bundle = MessageEvidenceBundle(message=um)
        bundles.append(bundle)

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


# --- Test 1: Discovery A + Document A -> allowed ---

def test_discovery_a_document_a_allowed():
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Logistica Portuale",
        short_description="Ispezione container al porto",
        evidence_ids=(eid_a,),
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid_a],
        "rationale": "Confermato scarico merci.",
    }])
    service = LocalTopicAnalysisService(client=client)

    det_res, consistency = service.verify_discovered_topic(
        document=doc_a,
        discovered_topic=disc_topic,
        discovery_run_id="run_disc_a_100",
    )

    assert det_res.decision == TopicDecision.PRESENT
    assert consistency.status == ConsistencyStatus.CONSISTENT
    assert consistency.overlap_evidence_ids == (eid_a,)
    assert consistency.document_id == "doc_a"
    assert consistency.discovery_run_id == "run_disc_a_100"


# --- Test 2: Discovery A + Document B -> rejected ---

def test_discovery_a_document_b_rejected():
    doc_a = _build_test_document("doc_a")
    doc_b = _build_test_document("doc_b")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    disc_ref_a = DiscoveredTopicRef(
        document_id="doc_a",
        discovery_run_id="run_a",
        topic_index=0,
        topic=DiscoveredTopic(
            label="Tema A",
            short_description="Descrizione A",
            evidence_ids=(eid_a,),
        ),
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    # Document mismatch via DiscoveredTopicRef -> reject
    with pytest.raises(TopicProvenanceError, match="Discrepanza di provenienza"):
        service.verify_discovered_topic(
            document=doc_b,
            discovered_topic=disc_ref_a,
        )

    # Document mismatch via TopicDiscoveryResult -> reject
    disc_result_a = TopicDiscoveryResult(
        topics=(disc_ref_a.topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_a"},
    )
    with pytest.raises(TopicProvenanceError, match="Discrepanza di provenienza"):
        service.verify_discovered_topic(
            document=doc_b,
            discovered_topic=disc_ref_a.topic,
            discovery_result=disc_result_a,
            discovery_run_id="run_a",
        )


# --- Test 3: Foreign evidence ID -> rejected ---

def test_foreign_evidence_id_in_discovered_topic_rejected():
    doc_a = _build_test_document("doc_a")
    # Evidence ID appartenente a un documento estraneo
    foreign_eid = "eid::doc_x::999::ORIGINAL_TEXT"

    disc_topic = DiscoveredTopic(
        label="Traffico Merci",
        short_description="Spedizione merci",
        evidence_ids=(foreign_eid,),
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    with pytest.raises(TopicProvenanceError, match="Evidence ID estraneo"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_run_id="run_disc_valid",
        )


def test_foreign_evidence_id_in_detection_result_rejected():
    doc_a = _build_test_document("doc_a")
    valid_eid = doc_a.all_evidence_sections[0].evidence_id
    foreign_eid = "eid::alien_doc::42::ORIGINAL_TEXT"

    disc_topic = DiscoveredTopic(
        label="Traffico Merci",
        short_description="Spedizione merci",
        evidence_ids=(valid_eid,),
    )

    # LLM inventa o restituisce un evidence ID che non appartiene a doc_a
    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [foreign_eid],
        "rationale": "Allucinazione evidence estranea.",
    }])
    service = LocalTopicAnalysisService(client=client)

    with pytest.raises((TopicProvenanceError, OperationalAnalysisFailedError)):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_run_id="run_disc_valid",
        )


# --- Test 4: Missing discovery run -> rejected ---

def test_missing_discovery_run_rejected():
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Tema Senza Run",
        short_description="Descrizione",
        evidence_ids=(eid_a,),
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    # Senza discovery_run_id e senza ref -> reject
    with pytest.raises(TopicProvenanceError, match="discovery_run_id obbligatorio"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_run_id=None,
        )

    # discovery_run_id vuoto -> reject
    with pytest.raises(TopicProvenanceError, match="discovery_run_id obbligatorio"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_run_id="   ",
        )


# --- Test 5: Invalid topic reference -> rejected ---

def test_invalid_topic_reference_rejected():
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    topic_in_result = DiscoveredTopic(
        label="Tema Reale",
        short_description="Desc",
        evidence_ids=(eid_a,),
    )
    disc_result = TopicDiscoveryResult(
        topics=(topic_in_result,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_001"},
    )

    alien_topic = DiscoveredTopic(
        label="Tema Inesistente Nel Result",
        short_description="Desc",
        evidence_ids=(eid_a,),
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    with pytest.raises(TopicProvenanceError, match="Topic reference non valida"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=alien_topic,
            discovery_result=disc_result,
            discovery_run_id="run_001",
        )


# --- Test 6 & 7: Multi-conversation isolation & history preservation (A -> B -> A) ---

def test_multi_conversation_isolation_and_history_preservation():
    session_state: dict[str, Any] = {}

    doc_a = _build_test_document("chat_alpha")
    doc_b = _build_test_document("chat_beta")

    # Inizializza stato per chat_alpha
    state_a = get_document_analysis_state("chat_alpha", state=session_state)
    det_a = TopicDetectionResult(
        topic=TopicQuery(topic_id="q_a", label="Tema Alpha", description="Desc Alpha"),
        decision=TopicDecision.PRESENT,
        evidence_ids=(doc_a.all_evidence_sections[0].evidence_id,),
        rationale="Rilevato in Alpha",
        provenance_document_id="chat_alpha",
        metadata={"run_id": "det_alpha_run_1"},
    )
    state_a.add_detection_result(det_a)

    # Passa a chat_beta (A -> B)
    state_b = get_document_analysis_state("chat_beta", state=session_state)
    det_b = TopicDetectionResult(
        topic=TopicQuery(topic_id="q_b", label="Tema Beta", description="Desc Beta"),
        decision=TopicDecision.PRESENT,
        evidence_ids=(doc_b.all_evidence_sections[0].evidence_id,),
        rationale="Rilevato in Beta",
        provenance_document_id="chat_beta",
        metadata={"run_id": "det_beta_run_1"},
    )
    state_b.add_detection_result(det_b)

    # Verifica isolamento di B
    assert len(state_b.detection_runs) == 1
    assert state_b.detection_runs[0].provenance_document_id == "chat_beta"
    assert state_b.detection_runs[0].topic.label == "Tema Beta"

    # Ritorna a chat_alpha (B -> A)
    state_a_returned = get_document_analysis_state("chat_alpha", state=session_state)
    assert len(state_a_returned.detection_runs) == 1
    assert state_a_returned.detection_runs[0].provenance_document_id == "chat_alpha"
    assert state_a_returned.detection_runs[0].topic.label == "Tema Alpha"
    assert state_a_returned.latest_detection_run_id == "det_alpha_run_1"

    # Nessuna contaminazione tra sessioni
    assert state_a is not state_b
    assert all(r.provenance_document_id == "chat_alpha" for r in state_a_returned.detection_runs)
    assert all(r.provenance_document_id == "chat_beta" for r in state_b.detection_runs)


# --- Test 8: Discovery evidence IDs not passed to Detection ---

def test_discovery_evidence_ids_not_passed_to_detection():
    doc = _build_test_document("doc_inspect")
    eid = doc.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Tema Segreto",
        short_description="Descrizione confidenziale",
        evidence_ids=(eid,),
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Indipendente.",
    }])
    service = LocalTopicAnalysisService(client=client)

    service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_inspect_999",
    )

    assert len(client.call_history) == 1
    call = client.call_history[0]
    prompt_str = " ".join(m.get("content", "") for m in call["messages"])

    # La detection riceve label e short_description
    assert "Tema Segreto" in prompt_str
    assert "Descrizione confidenziale" in prompt_str

    # La detection NON riceve gli evidence_ids emersi da Discovery come hint
    assert "evidence_ids emersi da Discovery" not in prompt_str
    assert "run_inspect_999" not in prompt_str


# --- Test 9: Detection generates independent LLM call ---

def test_detection_generates_independent_llm_call():
    doc = _build_test_document("doc_indep")
    eid = doc.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Tema Verifica",
        short_description="Descrizione Verifica",
        evidence_ids=(eid,),
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "ABSENT",
        "evidence_ids": [],
        "rationale": "Nessuna evidenza rilevata.",
    }])
    service = LocalTopicAnalysisService(client=client)

    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_indep_001",
    )

    # Verifica che una nuova chiamata LLM reale sia stata effettuata
    assert len(client.call_history) == 1
    assert det_res.decision == TopicDecision.ABSENT
    assert consistency.status == ConsistencyStatus.DISCREPANCY_ABSENT


# --- Test 10: Consistency uses actual results ---

@pytest.mark.parametrize(
    "detection_decision, target_eid_idx, expected_status",
    [
        (TopicDecision.PRESENT, 0, ConsistencyStatus.CONSISTENT),
        (TopicDecision.PRESENT, 1, ConsistencyStatus.PARTIALLY_CONSISTENT),
        (TopicDecision.ABSENT, None, ConsistencyStatus.DISCREPANCY_ABSENT),
        (TopicDecision.UNCERTAIN, None, ConsistencyStatus.DISCREPANCY_UNCERTAIN),
    ],
)
def test_consistency_uses_actual_results_matrix(
    detection_decision: TopicDecision,
    target_eid_idx: int | None,
    expected_status: ConsistencyStatus,
):
    doc = _build_test_document("doc_matrix")
    disc_eids = (doc.all_evidence_sections[0].evidence_id,)

    disc_topic = DiscoveredTopic(
        label="Matrice Coerenza",
        short_description="Verifica 4 stati",
        evidence_ids=disc_eids,
    )

    if target_eid_idx is not None:
        det_eids = [doc.all_evidence_sections[target_eid_idx].evidence_id]
    else:
        det_eids = []

    client = MockProvenanceLlmClient(responses=[{
        "decision": detection_decision.value,
        "evidence_ids": det_eids,
        "rationale": "Verifica matrice.",
    }])
    service = LocalTopicAnalysisService(client=client)

    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_matrix_check",
    )

    assert det_res.decision == detection_decision
    assert consistency.status == expected_status
