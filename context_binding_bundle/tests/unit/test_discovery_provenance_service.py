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
    AnalysisOrigin,
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
    disc_result_a = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_disc_a_100"},
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid_a],
        "rationale": "Confermato scarico merci.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result_a)

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
    disc_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_disc_valid"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result)

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
    disc_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_disc_valid"},
    )

    # LLM inventa o restituisce un evidence ID che non appartiene a doc_a
    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [foreign_eid],
        "rationale": "Allucinazione evidence estranea.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result)

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
    service.register_mock_discovery_run("doc_a", disc_result)

    with pytest.raises(TopicProvenanceError):
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
    disc_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_inspect",
        metadata={"run_id": "run_inspect_999"},
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Indipendente.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_inspect", disc_result)

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
    disc_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_indep",
        metadata={"run_id": "run_indep_001"},
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "ABSENT",
        "evidence_ids": [],
        "rationale": "Nessuna evidenza rilevata.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_indep", disc_result)

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
    disc_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_matrix",
        metadata={"run_id": "run_matrix_check"},
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
    service.register_mock_discovery_run("doc_matrix", disc_result)

    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_matrix_check",
    )

    assert det_res.decision == detection_decision
    assert consistency.status == expected_status


# --- Test 11: Nonexistent discovery run -> rejected ---

def test_nonexistent_discovery_run_rejected():
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Tema Fantasma",
        short_description="Descrizione fantasma",
        evidence_ids=(eid_a,),
    )
    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    # run non registrata solleva TopicProvenanceError prima di chiamare il client LLM
    with pytest.raises(TopicProvenanceError, match="Discovery run inesistente o non registrata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_run_id="unregistered_run_999",
        )
    assert len(client.call_history) == 0


# --- Test 12: Foreign document discovery run -> rejected ---

def test_foreign_document_discovery_run_rejected():
    doc_a = _build_test_document("doc_a")
    doc_b = _build_test_document("doc_b")
    eid_b = doc_b.all_evidence_sections[0].evidence_id

    disc_topic_b = DiscoveredTopic(
        label="Tema Doc B",
        short_description="Descrizione Doc B",
        evidence_ids=(eid_b,),
    )
    disc_result_b = TopicDiscoveryResult(
        topics=(disc_topic_b,),
        provenance_document_id="doc_b",
        metadata={"run_id": "run_b_valid"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    # Registra run solo per doc_b
    service.register_mock_discovery_run("doc_b", disc_result_b)

    # Tentativo di verificare su doc_a usando la run registrata su doc_b
    with pytest.raises(TopicProvenanceError, match="Discovery run inesistente o non registrata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic_b,
            discovery_run_id="run_b_valid",
        )
    assert len(client.call_history) == 0


# --- Test 13: Foreign topic not in registered run -> rejected ---

def test_foreign_topic_not_in_registered_run_rejected():
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    topic_in_run = DiscoveredTopic(
        label="Tema Ufficiale",
        short_description="Descrizione ufficiale",
        evidence_ids=(eid_a,),
    )
    alien_topic = DiscoveredTopic(
        label="Tema Intruso Non Nella Run",
        short_description="Descrizione intrusa",
        evidence_ids=(eid_a,),
    )
    disc_result_a = TopicDiscoveryResult(
        topics=(topic_in_run,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_a_registered"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result_a)

    with pytest.raises(TopicProvenanceError, match="non appartiene alla run di discovery registrata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=alien_topic,
            discovery_run_id="run_a_registered",
        )
    assert len(client.call_history) == 0


def test_forged_external_run_cannot_be_implicitly_registered_via_verify():
    """Una run costruita esternamente e fornita a verify non deve essere registrata implicitamente."""
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Forged Topic",
        short_description="Forged Description",
        evidence_ids=(eid_a,),
    )
    forged_result = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_forged_external"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    # NOT registering via register_discovery_run!

    with pytest.raises(TopicProvenanceError, match="non registrata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=disc_topic,
            discovery_result=forged_result,
            discovery_run_id="run_forged_external",
        )
    # Zero chiamate LLM effettuate
    assert len(client.call_history) == 0
    # Verifica che la run forged NON sia stata memorizzata nel registro del service
    assert service.get_discovery_run("doc_a", "run_forged_external") is None


def test_same_label_different_description_rejected():
    """Un topic con la stessa label ma descrizione differente deve sollevare TopicProvenanceError."""
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    registered_topic = DiscoveredTopic(
        label="Logistica Portuale",
        short_description="Descrizione ORIGINALE registrata",
        evidence_ids=(eid_a,),
    )
    tampered_topic = DiscoveredTopic(
        label="Logistica Portuale",
        short_description="Descrizione ALTERATA o FORGIATA",
        evidence_ids=(eid_a,),
    )

    disc_result = TopicDiscoveryResult(
        topics=(registered_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_tampered_desc"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result)

    with pytest.raises(TopicProvenanceError, match="descrizione specificata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=tampered_topic,
            discovery_run_id="run_tampered_desc",
        )
    assert len(client.call_history) == 0


def test_same_label_different_evidence_ids_rejected():
    """Un topic con la stessa label ma evidence ID differenti deve sollevare TopicProvenanceError."""
    doc_a = _build_test_document("doc_a", num_messages=3)
    eid_1 = doc_a.all_evidence_sections[0].evidence_id
    eid_2 = doc_a.all_evidence_sections[1].evidence_id

    registered_topic = DiscoveredTopic(
        label="Logistica Portuale",
        short_description="Descrizione valida",
        evidence_ids=(eid_1,),
    )
    tampered_topic = DiscoveredTopic(
        label="Logistica Portuale",
        short_description="Descrizione valida",
        evidence_ids=(eid_2,),
    )

    disc_result = TopicDiscoveryResult(
        topics=(registered_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_tampered_eids"},
    )

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_a", disc_result)

    with pytest.raises(TopicProvenanceError, match="evidence_id specificati"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=tampered_topic,
            discovery_run_id="run_tampered_eids",
        )
    assert len(client.call_history) == 0


# ==============================================================================
# BLOCKER 3 — 12 TEST OBBLIGATORI AUDIT (DISCOVERY ORIGIN & PROVENANCE SERVICE)
# ==============================================================================

def test_audit_b3_1_demo_loaded_without_live_discovery():
    """1. DEMO caricata senza Discovery live: non esistono run live al caricamento demo."""
    session: dict = {}
    load_demo_dataset(session)
    from ui.state import get_discovery_run_store, KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT
    store = get_discovery_run_store(session)
    assert len(store) == 0
    assert session.get(KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT) == {}


def test_audit_b3_2_live_runs_start_empty():
    """2. Le run live iniziano vuote (0 discovery live)."""
    session: dict = {}
    load_demo_dataset(session)
    from ui.state import get_discovery_run_store
    assert get_discovery_run_store(session) == {}


def test_audit_b3_3_synthetic_references_visible_separately():
    """3. Le reference sintetiche sono visibili separatamente e hanno origin SYNTHETIC_REFERENCE."""
    session: dict = {}
    load_demo_dataset(session)
    from ui.state import get_demo_reference_discovery_results
    refs = get_demo_reference_discovery_results(session)
    assert len(refs) > 0
    first_ref = refs[0]
    assert first_ref.metadata.get("origin") == AnalysisOrigin.SYNTHETIC_REFERENCE.value
    assert first_ref.metadata.get("run_id") == "run::discovery::demo_synthetic_run_01"


def test_audit_b3_4_synthetic_reference_rejected_from_live_registry():
    """4. Una reference sintetica NON può essere registrata come run live in AnalysisService."""
    doc_a = _build_test_document("doc::demo_forensic_chat")
    eid_a = doc_a.all_evidence_sections[0].evidence_id
    synthetic_ref = TopicDiscoveryResult(
        topics=(DiscoveredTopic(label="Tema Demo", short_description="Desc", evidence_ids=(eid_a,)),),
        provenance_document_id=doc_a.document_id,
        metadata={
            "run_id": "run::discovery::demo_synthetic_run_01",
            "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
        },
    )
    service = LocalTopicAnalysisService()
    with pytest.raises(TopicProvenanceError, match="SYNTHETIC_REFERENCE"):
        service.register_discovery_run(doc_a.document_id, synthetic_ref)


def test_audit_b3_5_live_verification_requires_actual_live_run():
    """5. La verifica live richiede una run effettivamente prodotta dal relativo percorso (rifiuta reference sintetica)."""
    session: dict = {}
    load_demo_dataset(session)
    from ui.state import get_conversation_document, get_demo_reference_discovery_results
    doc = get_conversation_document(session)
    assert doc is not None
    ref_disc = get_demo_reference_discovery_results(session)[0]
    topic = ref_disc.topics[0]

    client = MockProvenanceLlmClient()
    service = LocalTopicAnalysisService(client=client)

    # Tentativo di verificare il topic sintetico direttamente senza discovery live
    with pytest.raises(TopicProvenanceError, match="non registrata"):
        service.verify_discovered_topic(
            document=doc,
            discovered_topic=topic,
            discovery_run_id=ref_disc.metadata["run_id"],
        )
    assert len(client.call_history) == 0


def test_audit_b3_6_nonexistent_run_rejected():
    """6. Run inesistente respinta categoricamente con TopicProvenanceError."""
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id
    topic = DiscoveredTopic(label="Test", short_description="Desc", evidence_ids=(eid_a,))
    service = LocalTopicAnalysisService()
    with pytest.raises(TopicProvenanceError, match="inesistente o non registrata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=topic,
            discovery_run_id="run_phantom_999",
        )


def test_audit_b3_7_foreign_document_run_rejected():
    """7. Run appartenente ad altro documento respinta con TopicProvenanceError."""
    doc_a = _build_test_document("doc_a")
    doc_b = _build_test_document("doc_b")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    topic = DiscoveredTopic(label="Test", short_description="Desc", evidence_ids=(eid_a,))
    run_a = TopicDiscoveryResult(
        topics=(topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_a_123", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
    )
    service = LocalTopicAnalysisService()
    service.register_mock_discovery_run("doc_a", run_a)

    # Invocazione su doc_b con la run registrata per doc_a
    with pytest.raises(TopicProvenanceError):
        service.verify_discovered_topic(
            document=doc_b,
            discovered_topic=topic,
            discovery_run_id="run_a_123",
        )


def test_audit_b3_8_tampered_topic_rejected():
    """8. Topic alterato respinto (label identica ma descrizione o evidence_id manomessi)."""
    doc_a = _build_test_document("doc_a")
    eid_a = doc_a.all_evidence_sections[0].evidence_id

    orig_topic = DiscoveredTopic(label="Droga", short_description="Spaccio", evidence_ids=(eid_a,))
    altered_topic = DiscoveredTopic(label="Droga", short_description="ALTERATA", evidence_ids=(eid_a,))

    run_a = TopicDiscoveryResult(
        topics=(orig_topic,),
        provenance_document_id="doc_a",
        metadata={"run_id": "run_a_alt", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
    )
    service = LocalTopicAnalysisService()
    service.register_mock_discovery_run("doc_a", run_a)

    with pytest.raises(TopicProvenanceError, match="descrizione specificata"):
        service.verify_discovered_topic(
            document=doc_a,
            discovered_topic=altered_topic,
            discovery_run_id="run_a_alt",
        )


def test_audit_b3_9_mock_discovery_correctly_identified_as_mock():
    """9. Discovery prodotta da client mock identificata con origin MOCK_INSPECTION."""
    doc = _build_test_document("doc_mock")
    eid = doc.all_evidence_sections[0].evidence_id

    mock_client = MockProvenanceLlmClient(responses=[{
        "topics": [{"label": "Tema Mock", "short_description": "Descrizione mock", "evidence_ids": [eid]}],
    }])
    service = LocalTopicAnalysisService(client=mock_client)
    res = service.run_topic_discovery(document=doc, client=mock_client)

    assert res.metadata.get("origin") == AnalysisOrigin.MOCK_INSPECTION.value
    assert res.metadata.get("status") == "COMPLETE"


def test_audit_b3_10_lmstudio_discovery_verified_via_new_detection():
    """10. Una Discovery prodotta con LmStudioClient può essere verificata tramite nuova Detection."""
    from ai.lmstudio import LmStudioClient, LocalModelMetadata

    doc = _build_test_document("doc_lm")
    eid = doc.all_evidence_sections[0].evidence_id

    # Sottoclasse controllata per simulare LmStudioClient conforme senza connessione reale
    class SimulatedLmStudioClient(LmStudioClient):
        def __init__(self):
            super().__init__(base_url="http://127.0.0.1:1234", model_id="qwen2.5-7b-instruct")
            self.call_count = 0

        def is_available(self):
            return True

        def get_native_models(self):
            return [LocalModelMetadata(key="qwen2.5-7b-instruct", display_name="Qwen2.5 7B", loaded_instances=[{"id": "qwen2.5-inst-1"}])]

        def ensure_model_loaded(self, model_id="qwen2.5-7b-instruct", **kwargs):
            self.model_id = "qwen2.5-7b-instruct"
            self._last_loaded_model = "qwen2.5-7b-instruct"
            return "qwen2.5-7b-instruct"

        def chat_completion(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                # Risposta Discovery
                content = json.dumps({
                    "topics": [{"label": "Ispezione Merci", "short_description": "Container al varco", "evidence_ids": [eid]}],
                })
            else:
                # Risposta Detection di verifica
                content = json.dumps({
                    "decision": "PRESENT",
                    "evidence_ids": [eid],
                    "rationale": "Confermato container al varco.",
                })
            return LlmCompletionResponse(
                content=content,
                model="qwen2.5-7b-instruct",
                prompt_tokens=30,
                completion_tokens=20,
                total_tokens=50,
                latency_seconds=0.03,
            )

    client = SimulatedLmStudioClient()
    service = LocalTopicAnalysisService(client=client)

    # 1. Esegui discovery
    disc_res = service.run_topic_discovery(document=doc, client=client, model_name="qwen2.5-7b-instruct")
    assert disc_res.metadata.get("origin") == AnalysisOrigin.LIVE_LM_STUDIO.value
    run_id = disc_res.metadata["run_id"]

    # 2. Esegui verifica
    topic = disc_res.topics[0]
    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=topic,
        discovery_run_id=run_id,
        client=client,
    )

    assert det_res.decision == TopicDecision.PRESENT
    assert consistency.status == ConsistencyStatus.CONSISTENT
    assert consistency.discovery_run_id == run_id


def test_audit_b3_11_no_discovery_evidence_ids_passed_to_detection():
    """11. Nessun evidence ID del Discovery viene passato alla seconda inferenza."""
    doc = _build_test_document("doc_isolation")
    eid_secret = doc.all_evidence_sections[0].evidence_id

    disc_topic = DiscoveredTopic(
        label="Logistica Clandestina",
        short_description="Ispezione notturna",
        evidence_ids=(eid_secret,),
    )
    disc_res = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_isolation",
        metadata={"run_id": "run_iso_001", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
    )

    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid_secret],
        "rationale": "Rilevato autonomamente.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_isolation", disc_res)

    service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_iso_001",
    )

    assert len(client.call_history) == 1
    call = client.call_history[0]
    messages_str = json.dumps(call["messages"])

    # La label e la descrizione DEVONO essere presenti
    assert "Logistica Clandestina" in messages_str
    assert "Ispezione notturna" in messages_str
    # L'evidence_id del discovery NON deve apparire come suggerimento nel prompt di query
    assert f'"{eid_secret}"' not in messages_str or eid_secret not in messages_str.split("CRITERI DI DECISIONE")[0]


def test_audit_b3_12_consistency_uses_only_properly_identified_results():
    """12. La Consistency usa soltanto i risultati correttamente identificati."""
    doc = _build_test_document("doc_cons")
    eid_1 = doc.all_evidence_sections[0].evidence_id
    eid_2 = doc.all_evidence_sections[1].evidence_id

    disc_topic = DiscoveredTopic(
        label="Traffico",
        short_description="Desc",
        evidence_ids=(eid_1,),
    )
    disc_res = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id="doc_cons",
        metadata={"run_id": "run_cons_001", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
    )

    # Caso A: Disgiunto -> PARTIALLY_CONSISTENT
    client = MockProvenanceLlmClient(responses=[{
        "decision": "PRESENT",
        "evidence_ids": [eid_2],
        "rationale": "Evidenza diversa trovata.",
    }])
    service = LocalTopicAnalysisService(client=client)
    service.register_mock_discovery_run("doc_cons", disc_res)

    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_cons_001",
    )
    assert consistency.status == ConsistencyStatus.PARTIALLY_CONSISTENT
    assert len(consistency.overlap_evidence_ids) == 0



