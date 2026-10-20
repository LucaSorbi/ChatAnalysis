"""
tests/unit/test_topic_discovery_detection_stabilization.py
-----------------------------------------------------------
Test di unità per la stabilizzazione della Topic Discovery e Topic Detection (Parti B, C, D).

Verifica:
- Parte B: Topic Discovery 24/26 chunk completati, fallimenti agli indici 10 e 19.
  * Stato globale PARTIAL, conservazione di 24 risultati validi, diagnostica dei chunk falliti.
  * Nessuna dichiarazione prematura di COMPLETE.
  * Ripresa selettiva (retry) tramite SQLite checkpoint: solo i blocchi 10 e 19 vengono re-inferiti.
  * Isolamento checkpoint tra documenti diversi (nessuna contaminazione).
- Parte C: Topic Detection manuale 17/26 chunk completati.
  * Se almeno un blocco è PRESENT: verdetto PRESENT e stato PARTIAL.
  * Se nessun blocco è PRESENT: verdetto UNCERTAIN (MAI falso ABSENT) e stato PARTIAL.
  * Se tutti i 26 blocchi sono ABSENT con successo: verdetto ABSENT e stato SUCCESS.
  * Se tutti i 26 blocchi falliscono: errore operativo OperationalAnalysisFailedError.
- Parte D: Prova del Nove indipendente e caso di controllo multimodale.
  * Isolamento inferenziale: la seconda inferenza riceve ESCLUSIVAMENTE label e description.
  * ConsistencyService valuta consistenza post-hoc senza fuga di evidence ID.
  * Caso di controllo 'Logistica e Ispezione Depositi' con 'msg::5::VISION_DESCRIPTION'.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    FakeLocalLlmClient,
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
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.consistency import ConsistencyService
from analysis.models import (
    AnalysisOrigin,
    ChunkDiagnosticCategory,
    ChunkLifecycleStatus,
    ConsistencyStatus,
    OperationalAnalysisFailedError,
    OperationalProfile,
)
from analysis.service import LocalTopicAnalysisService
from unified.models import UnifiedMessage
from validation.models import ValidationResult
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from ui.demo import build_synthetic_demo_dataset


class MockScriptedLlmClient(FakeLocalLlmClient):
    """Client mock programmabile per simulare risposte e fallimenti selettivi per chunk."""

    def __init__(
        self,
        canned_discovery_topics: list[dict[str, Any]] | None = None,
        discovery_failures: dict[int, Exception] | None = None,
        canned_detection_responses: dict[int, dict[str, Any]] | None = None,
        detection_failures: dict[int, Exception] | None = None,
        model_name: str = "qwen2.5-7b-instruct",
    ):
        super().__init__(model_name=model_name, models=(model_name,))
        self.canned_discovery_topics = canned_discovery_topics or [
            {"label": "Logistica e Magazzino", "short_description": "Operazioni di stoccaggio e carico merci."}
        ]
        self.discovery_failures = discovery_failures or {}
        self.canned_detection_responses = canned_detection_responses or {}
        self.detection_failures = detection_failures or {}
        self.requested_chunks: list[int] = []

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> LlmCompletionResponse:
        self.call_history.append({"messages": messages, "model_id": model_id})
        prompt_text = " ".join(m.get("content", "") for m in messages)
        eff_model = model_id or self.model_name

        # Rileva indice di chunk dal contenuto del messaggio (es. "blocco X" o "chunk X")
        match = re.search(r"chunk\s+(\d+)|blocco\s+(\d+)", prompt_text, re.IGNORECASE)
        chunk_idx = int(match.group(1) or match.group(2)) if match else len(self.requested_chunks)
        self.requested_chunks.append(chunk_idx)

        # 1. Ramo Discovery
        if "topic discovery" in prompt_text.lower() or "discovery" in prompt_text.lower() or "topics" in prompt_text.lower():
            if chunk_idx in self.discovery_failures:
                raise self.discovery_failures[chunk_idx]

            # Rileva evidence ID o alias presenti nel prompt per referenziarli validamente
            found_eids = re.findall(r'"evidence_id":\s*"([^"]+)"', prompt_text)
            sample_eid = found_eids[0] if found_eids else f"msg::{chunk_idx * 3}::ORIGINAL_TEXT"

            payload = {
                "topics": [
                    {
                        "label": t["label"],
                        "short_description": t["short_description"],
                        "evidence_ids": [sample_eid],
                    }
                    for t in self.canned_discovery_topics
                ]
            }
            return LlmCompletionResponse(
                content=json.dumps(payload),
                model=eff_model,
                prompt_tokens=100,
                completion_tokens=50,
            )

        # 2. Ramo Detection
        if chunk_idx in self.detection_failures:
            raise self.detection_failures[chunk_idx]

        if chunk_idx in self.canned_detection_responses:
            resp = self.canned_detection_responses[chunk_idx]
            return LlmCompletionResponse(
                content=json.dumps(resp),
                model=eff_model,
                prompt_tokens=80,
                completion_tokens=40,
            )

        # Default ABSENT se non specificato
        default_resp = {
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": f"Nessuna evidenza rilevata nel chunk {chunk_idx}.",
        }
        return LlmCompletionResponse(
            content=json.dumps(default_resp),
            model=eff_model,
            prompt_tokens=80,
            completion_tokens=40,
        )


def _create_synthetic_bundle(msg_id: int, text: str, source_type: EvidenceSourceType = EvidenceSourceType.ORIGINAL_TEXT) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp",
        source_path="/fake/chat.txt",
        source_record_id=str(msg_id),
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=None,
        naive_datetime=None,
        iso_string="2026-10-18T10:00:00Z",
        raw_value="18/10/2026, 10:00",
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp",
        source_record_id=str(msg_id),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg::{msg_id}",
        source_name="whatsapp",
        source_record_id=str(msg_id),
        source_path="/fake/chat.txt",
        record_type="message",
        timestamp=norm.timestamp,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _create_synthetic_26_chunk_document(doc_id: str = "doc_whatsapp_synth_26chunks") -> ConversationEvidenceDocument:
    """Costruisce documento da 78 messaggi (3 messaggi da 3000 caratteri) -> 26 chunk con BALANCED."""
    bundles = []
    for i in range(78):
        chunk_num = i // 3
        text = f"Messaggio {i} appartenente al chunk {chunk_num} per test sintetico. " + "X" * 3000
        bundles.append(_create_synthetic_bundle(i, text))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp",
    )


# ==============================================================================
# PARTE B: TOPIC DISCOVERY 24/26 CHUNK, DIAGNOSTICA E RETRY SELETTIVO
# ==============================================================================

def test_discovery_24_of_26_chunks_with_failures_at_10_and_19():
    """
    Parte B: Topic Discovery su 26 chunk con fallimenti simulati ai blocchi 10 e 19.
    Verifica:
    - Conservazione rigorosa dei 24 chunk validati.
    - Tracciamento diagnostico esplicito dei fallimenti (TIMEOUT e SCHEMA_VALIDATION).
    - Stato globale PARTIAL (nessuna dichiarazione di COMPLETE).
    - Persistenza SQLite dei 24 checkpoint e delle 2 diagnostiche.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "checkpoints.db"
        chk_store = AnalysisCheckpointStore(db_path)
        doc = _create_synthetic_26_chunk_document("doc_discovery_24_of_26")

        failures = {
            10: AiBackendTimeoutError("Timeout di 240 secondi superato sul chunk 10"),
            19: AiStructuredOutputError("Schema JSON invalido restituito dal modello sul chunk 19"),
        }
        client = MockScriptedLlmClient(discovery_failures=failures)

        service = LocalTopicAnalysisService(
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            context_length=8192,
            checkpoint_store=chk_store,
            enable_checkpointing=True,
        )

        res = service.run_topic_discovery(document=doc, client=client)

        assert res.metadata["status"] == "PARTIAL", "La Topic Discovery parziale DEVE avere stato PARTIAL!"
        assert res.metadata["chunk_count"] == 26
        assert res.metadata["completed_chunk_count"] == 24
        assert res.metadata["failed_chunk_count"] == 2
        assert len(res.topics) >= 1, "I topic emersi dai 24 chunk validi devono essere consolidati!"

        # Verifica diagnostica dei due chunk falliti
        diag_list = res.metadata.get("chunk_diagnostics", [])
        failed_diags = [d for d in diag_list if d["status"] == ChunkLifecycleStatus.FAILED.value]
        assert len(failed_diags) == 2
        diag_indices = {d["chunk_index"] for d in failed_diags}
        assert diag_indices == {10, 19}

        diag_10 = next(d for d in failed_diags if d["chunk_index"] == 10)
        assert diag_10["diagnostic_category"] == ChunkDiagnosticCategory.TIMEOUT.value
        assert diag_10["status"] == ChunkLifecycleStatus.FAILED.value

        diag_19 = next(d for d in failed_diags if d["chunk_index"] == 19)
        assert diag_19["diagnostic_category"] in (
            ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value,
            ChunkDiagnosticCategory.JSON_DECODE_ERROR.value,
        )

        # Verifica persistenza su SQLite: 24 chunk validati salvati
        doc_fp = compute_document_fingerprint(doc)
        with chk_store._connect() as conn:
            cur = conn.execute(
                "SELECT chunk_index FROM checkpoint_chunks WHERE document_fingerprint = ? ORDER BY chunk_index ASC",
                (doc_fp,),
            )
            saved_indices = [row[0] for row in cur.fetchall()]
        assert len(saved_indices) == 24
        assert set(saved_indices) == set(range(26)) - {10, 19}


def test_discovery_selective_retry_recovers_only_missing_chunks():
    """
    Parte B3: Retry selettivo.
    Dato un checkpoint con 24/26 chunk completati (mancanti 10 e 19):
    Un retry ri-esegue ESCLUSIVAMENTE i blocchi 10 e 19, riutilizzando i 24 validati da SQLite.
    A completamento, lo stato globale diventa COMPLETE.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "checkpoints.db"
        chk_store = AnalysisCheckpointStore(db_path)
        doc = _create_synthetic_26_chunk_document("doc_selective_retry")

        # Run 1: falliscono 10 e 19
        client1 = MockScriptedLlmClient(
            discovery_failures={
                10: AiBackendTimeoutError("Timeout blocco 10"),
                19: AiBackendTimeoutError("Timeout blocco 19"),
            }
        )
        service = LocalTopicAnalysisService(
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            context_length=8192,
            checkpoint_store=chk_store,
            enable_checkpointing=True,
        )
        res1 = service.run_topic_discovery(document=doc, client=client1)
        assert res1.metadata["status"] == "PARTIAL"
        assert res1.metadata["completed_chunk_count"] == 24

        # Run 2: nessun fallimento (i blocchi 10 e 19 ora completano)
        client2 = MockScriptedLlmClient()
        res2 = service.run_topic_discovery(document=doc, client=client2)

        # Verifica che il client abbia ricevuto SOLO i blocchi 10 e 19
        # (I blocchi 0..9, 11..18, 20..25 sono stati recuperati da SQLite!)
        assert set(client2.requested_chunks) == {10, 19}, (
            f"Il retry deve invocare SOLO i chunk mancanti (10 e 19)! Invocati: {client2.requested_chunks}"
        )
        assert res2.metadata["status"] == "COMPLETE"
        assert res2.metadata["completed_chunk_count"] == 26
        assert res2.metadata["failed_chunk_count"] == 0
        assert res2.metadata["recovered_chunk_count"] == 24


def test_discovery_checkpoint_cross_document_isolation():
    """Parte B3: Isolamento checkpoint: un documento differente non deve mai riutilizzare i checkpoint."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "checkpoints.db"
        chk_store = AnalysisCheckpointStore(db_path)
        doc_a = _create_synthetic_26_chunk_document("doc_A")
        doc_b = _create_synthetic_26_chunk_document("doc_B")

        client1 = MockScriptedLlmClient(discovery_failures={10: AiBackendTimeoutError("timeout")})
        service = LocalTopicAnalysisService(
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            context_length=8192,
            checkpoint_store=chk_store,
            enable_checkpointing=True,
        )
        service.run_topic_discovery(document=doc_a, client=client1)

        # Esecuzione su Doc B: deve eseguire tutti i 26 blocchi da zero
        client2 = MockScriptedLlmClient()
        res_b = service.run_topic_discovery(document=doc_b, client=client2)

        assert len(client2.requested_chunks) == 26
        assert res_b.metadata["recovered_chunk_count"] == 0


# ==============================================================================
# PARTE C: TOPIC DETECTION MANUALE E REGOLA 17/26 CHUNK
# ==============================================================================

def test_detection_17_of_26_completed_with_evidence_is_present_and_partial():
    """
    Parte C2/C3: Topic Detection con 17 chunk completati e 9 falliti.
    Dato che uno dei 17 chunk completati contiene evidenze:
    - Verdetto tematico: PRESENT
    - Stato di esecuzione: PARTIAL (mai falsamente COMPLETO)
    - Metadata: chunk_count=26, completed_chunk_count=17, failed_chunk_count=9.
    """
    doc = _create_synthetic_26_chunk_document("doc_detection_17_present")
    topic = TopicQuery(
        topic_id="query_logistica",
        label="Logistica e Magazzino",
        description="Operazioni di movimentazione merci e stoccaggio.",
    )

    # Chunk 0: PRESENT con evidenza
    # Chunk 1..16: ABSENT
    # Chunk 17..25: Fallimento (timeout operativo)
    canned_responses = {
        0: {
            "decision": "PRESENT",
            "evidence_ids": ["msg::0::ORIGINAL_TEXT"],
            "rationale": "Evidenza rilevata di movimentazione merci nel primo chunk.",
        }
    }
    detection_failures = {
        i: AiBackendTimeoutError(f"Timeout operativo al chunk {i}")
        for i in range(17, 26)
    }

    client = MockScriptedLlmClient(
        canned_detection_responses=canned_responses,
        detection_failures=detection_failures,
    )

    service = LocalTopicAnalysisService(
        default_model="qwen2.5-7b-instruct",
        profile=OperationalProfile.BALANCED,
        context_length=8192,
    )

    res = service.run_topic_detection(document=doc, topic=topic, client=client)

    assert res.decision == TopicDecision.PRESENT, "PRESENT vince se sostenuto da evidenze valide!"
    assert res.evidence_ids == ("msg::0::ORIGINAL_TEXT",)
    assert res.metadata["status"] == "PARTIAL", "L'esecuzione incompleta (17/26) deve avere stato PARTIAL!"
    assert res.metadata["chunk_count"] == 26
    assert res.metadata["completed_chunk_count"] == 17
    assert res.metadata["failed_chunk_count"] == 9


def test_detection_17_of_26_completed_without_evidence_is_uncertain():
    """
    Parte C2/C3: Topic Detection con 17 chunk completati senza evidenze e 9 falliti.
    - Verdetto tematico: UNCERTAIN (MAI ABSENT, perché i 9 chunk falliti potrebbero contenere il topic!)
    - Stato di esecuzione: PARTIAL.
    """
    doc = _create_synthetic_26_chunk_document("doc_detection_17_uncertain")
    topic = TopicQuery(
        topic_id="query_armi",
        label="Traffico di Armi",
        description="Compravendita non autorizzata di armamenti pesanti.",
    )

    # Chunks 0..16: tutti ABSENT
    # Chunks 17..25: Timeout operativi
    detection_failures = {
        i: AiBackendTimeoutError(f"Timeout al chunk {i}")
        for i in range(17, 26)
    }

    client = MockScriptedLlmClient(detection_failures=detection_failures)
    service = LocalTopicAnalysisService(
        default_model="qwen2.5-7b-instruct",
        profile=OperationalProfile.BALANCED,
        context_length=8192,
    )

    res = service.run_topic_detection(document=doc, topic=topic, client=client)

    assert res.decision == TopicDecision.UNCERTAIN, (
        "Non è consentito concludere ABSENT se 9 blocchi su 26 non sono stati analizzati!"
    )
    assert res.evidence_ids == ()
    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["completed_chunk_count"] == 17
    assert res.metadata["failed_chunk_count"] == 9


def test_detection_all_26_chunks_absent_concludes_absent_success():
    """Parte C2: Se e solo se tutti i 26 chunk attesi sono analizzati ed escludono il topic, il verdetto è ABSENT."""
    doc = _create_synthetic_26_chunk_document("doc_detection_26_absent")
    topic = TopicQuery(
        topic_id="query_inesistente",
        label="Argomento Assente",
        description="Nessun riscontro in tutta la conversazione.",
    )

    client = MockScriptedLlmClient()  # Tutti i 26 chunk rispondono ABSENT
    service = LocalTopicAnalysisService(
        default_model="qwen2.5-7b-instruct",
        profile=OperationalProfile.BALANCED,
        context_length=8192,
    )

    res = service.run_topic_detection(document=doc, topic=topic, client=client)

    assert res.decision == TopicDecision.ABSENT
    assert res.evidence_ids == ()
    assert res.metadata["status"] == "SUCCESS"
    assert res.metadata["completed_chunk_count"] == 26
    assert res.metadata["failed_chunk_count"] == 0


def test_detection_all_chunks_failed_raises_operational_error():
    """Parte C2: Se tutti i chunk falliscono, deve essere sollevato OperationalAnalysisFailedError."""
    doc = _create_synthetic_26_chunk_document("doc_detection_all_fail")
    topic = TopicQuery(topic_id="q", label="L", description="D")

    # Tutti i 26 chunk falliscono
    all_failures = {i: AiBackendTimeoutError(f"Fail {i}") for i in range(26)}
    client = MockScriptedLlmClient(detection_failures=all_failures)
    service = LocalTopicAnalysisService(
        default_model="qwen2.5-7b-instruct",
        profile=OperationalProfile.BALANCED,
        context_length=8192,
    )

    with pytest.raises(OperationalAnalysisFailedError):
        service.run_topic_detection(document=doc, topic=topic, client=client)


# ==============================================================================
# PARTE D: PROVA DEL NOVE INDIPENDENTE E CASO DI CONTROLLO
# ==============================================================================

def test_prova_del_nove_inferential_isolation_and_consistency():
    """
    Parte D1/D2: Prova del Nove.
    La seconda inferenza (Topic Detection):
    - Riceve ESCLUSIVAMENTE label e short_description del DiscoveredTopic.
    - NON riceve gli evidence_ids scoperti come suggerimento.
    - ConsistencyService calcola CONSISTENT a posteriori.
    """
    # Creiamo un documento con un'evidenza specifica
    bundles = [
        _create_synthetic_bundle(0, "Pianificazione logistica merci e magazzino casse sigillate."),
        _create_synthetic_bundle(1, "Comunicazione ordinaria non pertinente."),
    ]
    doc = ConversationEvidenceDocument(document_id="doc_prova_nove", bundles=tuple(bundles))

    disc_topic = DiscoveredTopic(
        label="Logistica Depositi",
        short_description="Pianificazione logistica merci e magazzino casse sigillate.",
        evidence_ids=("msg::0::ORIGINAL_TEXT",),
    )

    # 1. Discovery iniziale registrata
    disc_res = TopicDiscoveryResult(
        topics=(disc_topic,),
        provenance_document_id=doc.document_id,
        metadata={"run_id": "run_disc_100", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
    )

    service = LocalTopicAnalysisService(default_model="qwen2.5-7b-instruct")
    service.register_mock_discovery_run(doc.document_id, disc_res)

    # 2. Mock client per la seconda inferenza di Detection
    detection_call_messages = []

    class InspectionClient(FakeLocalLlmClient):
        def __init__(self):
            super().__init__(model_name="qwen2.5-7b-instruct", models=("qwen2.5-7b-instruct",))

        def chat_completion(self, messages: list[dict[str, str]], model_id: str | None = None, **kwargs: Any) -> LlmCompletionResponse:
            detection_call_messages.extend(messages)
            resp = {
                "decision": "PRESENT",
                "evidence_ids": ["msg::0::ORIGINAL_TEXT"],
                "rationale": "Rilevato tema logistica nel messaggio 0.",
            }
            return LlmCompletionResponse(
                content=json.dumps(resp),
                model=model_id or "qwen2.5-7b-instruct",
            )

    client = InspectionClient()

    det_res, consistency = service.verify_discovered_topic(
        document=doc,
        discovered_topic=disc_topic,
        discovery_run_id="run_disc_100",
        client=client,
    )

    # D1: Verifica isolamento inferenziale: la seconda inferenza NON deve contenere gli evidence IDs nel system o user prompt come target hint!
    combined_prompt = " ".join(m.get("content", "") for m in detection_call_messages)
    # L'evidenza msg::0 compare nel corpo del documento da analizzare, ma la query NON suggerisce quale ID sia quello atteso
    assert "Target evidence:" not in combined_prompt
    assert "Expected evidence:" not in combined_prompt

    # Verifica verdetto e consistenza
    assert det_res.decision == TopicDecision.PRESENT
    assert det_res.evidence_ids == ("msg::0::ORIGINAL_TEXT",)
    assert consistency.status == ConsistencyStatus.CONSISTENT
    assert consistency.discovery_run_id == "run_disc_100"
    assert len(consistency.overlap_evidence_ids) == 1
    assert consistency.overlap_evidence_ids == ("msg::0::ORIGINAL_TEXT",)


def test_control_case_vision_description_serialization():
    """
    Parte D3: Caso di controllo obbligatorio:
    Label: 'Logistica e Ispezione Depositi'
    Description: 'Ispezione visiva del magazzino merci con casse sigillate e sicurezza'
    Evidence ID di controllo: 'msg::5::VISION_DESCRIPTION'
    Verifica che VISION_DESCRIPTION venga serializzata tra le evidenze disponibili e analizzabile semanticamente.
    """
    demo_doc, _, _ = build_synthetic_demo_dataset()
    doc = demo_doc

    # 1. Verifica presenza fisica
    sec = next(s for s in doc.all_evidence_sections if s.evidence_id == "msg::5::VISION_DESCRIPTION")
    assert sec.source_type == EvidenceSourceType.VISION_DESCRIPTION
    assert "porta di sicurezza blindata" in sec.text

    # 2. Topic Detection sul caso di controllo
    topic = TopicQuery(
        topic_id="query_control_case",
        label="Logistica e Ispezione Depositi",
        description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
    )

    captured_prompt = []

    class VisionVerifyingClient(FakeLocalLlmClient):
        def __init__(self):
            super().__init__(model_name="qwen2.5-7b-instruct", models=("qwen2.5-7b-instruct",))

        def chat_completion(self, messages: list[dict[str, str]], model_id: str | None = None, **kwargs: Any) -> LlmCompletionResponse:
            captured_prompt.extend(messages)
            resp = {
                "decision": "PRESENT",
                "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
                "rationale": "Riscontro visivo confermato per ispezione magazzino merci e porta blindata.",
            }
            return LlmCompletionResponse(
                content=json.dumps(resp),
                model=model_id or "qwen2.5-7b-instruct",
            )

    client = VisionVerifyingClient()
    service = LocalTopicAnalysisService(default_model="qwen2.5-7b-instruct")
    res = service.run_topic_detection(document=doc, topic=topic, client=client)

    # Verifica serializzazione nel prompt
    all_content = " ".join(m.get("content", "") for m in captured_prompt)
    assert "msg::5::VISION_DESCRIPTION" in all_content, "L'ID VISION_DESCRIPTION deve comparire nel prompt inviato al modello!"
    assert "porta di sicurezza blindata" in all_content

    # Risultato Detection corretto
    assert res.decision == TopicDecision.PRESENT
    assert "msg::5::VISION_DESCRIPTION" in res.evidence_ids
    assert res.provenance_document_id == doc.document_id
