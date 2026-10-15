"""
tests/unit/test_operational_integration_audit.py
------------------------------------------------
Suite di test completa per la verifica dei 18 requisiti obbligatori di integrazione operativa,
contabilita HTTP reale, gestione rigorosa dei checkpoint e protezione forense.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Sequence

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.chunking import canonicalize_topic_label, split_document_into_chunks
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
from ai.token_budget import DEFAULT_SAFETY_MARGIN_TOKENS
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    CheckpointSaveError,
    compute_document_fingerprint,
    compute_run_config_hash,
    validate_checkpoint_evidence_references,
)
from analysis.models import (
    OperationalAnalysisConfig,
    OperationalProfile,
    resolve_operational_profile,
)
from analysis.progress import AnalysisProgressTracker
from analysis.service import (
    LocalTopicAnalysisService,
    calculate_discovery_max_tokens,
)
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from ui.application import get_operational_analysis_service
from unified.models import UnifiedMessage
from validation.models import ValidationResult


class ControlledMockClient(BaseLlmClient):
    """Client di test controllato con listener di progresso HTTP conforme all'invariante."""

    def __init__(self, responses: list[Any] | None = None, model_id: str = "test-model") -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.request_listener: Any = None
        self.model_id = model_id

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float = 30.0,
        seed: int | None = 42,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass

        if not self.responses:
            resp_content = json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Default mock response."})
        else:
            resp_content = self.responses.pop(0)

        if isinstance(resp_content, Exception):
            if self.request_listener:
                try:
                    self.request_listener.record_http_response_received()
                    self.request_listener.record_http_error(500, str(resp_content))
                except Exception:
                    pass
            raise resp_content

        if isinstance(resp_content, dict):
            resp_str = json.dumps(resp_content)
        else:
            resp_str = str(resp_content)

        if self.request_listener:
            try:
                self.request_listener.record_http_response_received()
                self.request_listener.record_request_success()
            except Exception:
                pass

        return LlmCompletionResponse(
            content=resp_str,
            model=model or self.model_id,
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            latency_seconds=0.01,
        )


def _create_bundle(idx: str, text: str, ts_iso: str = "2026-03-01T10:00:00Z") -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="test",
        source_path="/path/test",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text, "timestamp": ts_iso},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=datetime.fromisoformat(ts_iso.replace("Z", "+00:00")),
        naive_datetime=None,
        iso_string=ts_iso,
        raw_value=ts_iso,
    )
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


def _make_doc(doc_id: str = "doc_test", count: int = 3, base_ts: str = "2026-03-01T10:00:00Z") -> ConversationEvidenceDocument:
    bundles = [_create_bundle(str(i), f"Messaggio di test numero {i}", ts_iso=base_ts) for i in range(count)]
    return ConversationEvidenceDocument(document_id=doc_id, source_name="test", bundles=tuple(bundles))


# ==============================================================================
# TEST 1: Configurazione effettiva creata dalla factory Streamlit
# ==============================================================================
def test_01_streamlit_factory_effective_configuration():
    service = get_operational_analysis_service()
    assert service.profile == OperationalProfile.BALANCED
    assert service.max_chunk_tokens == 4200
    assert service.max_chunk_characters == 18000
    assert service.enable_checkpointing is True
    assert service.checkpoint_store is not None
    assert isinstance(service.checkpoint_store, AnalysisCheckpointStore)


# ==============================================================================
# TEST 2: Profilo Bilanciato attivo nella run operativa
# ==============================================================================
def test_02_balanced_profile_active_in_operational_run():
    doc = _make_doc("doc_balanced", count=4)
    client = ControlledMockClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "No topic."}
    ])
    service = get_operational_analysis_service(profile=OperationalProfile.BALANCED)
    query = TopicQuery(topic_id="t1", label="Test", description="Test query")
    res = service.run_topic_detection(doc, query, client=client, model_name="test-model")
    assert res.metadata["chunk_count"] == 1
    assert service.profile == OperationalProfile.BALANCED
    assert service.max_chunk_tokens == 4200


# ==============================================================================
# TEST 3: Profilazione preventiva con 3, 10 e 25 topic
# ==============================================================================
def test_03_pre_analysis_profiling_coherence_3_10_25_topics():
    doc = _make_doc("doc_coherence", count=5)
    service = get_operational_analysis_service()

    plan_3 = service.create_analysis_plan(doc, "discovery", max_topics=3)
    plan_10 = service.create_analysis_plan(doc, "discovery", max_topics=10)
    plan_25 = service.create_analysis_plan(doc, "discovery", max_topics=25)

    assert plan_3.analysis_type == "discovery"
    assert plan_10.analysis_type == "discovery"
    assert plan_25.analysis_type == "discovery"

    # La riserva di output scala con max_topics
    tok_3 = calculate_discovery_max_tokens(3, context_length=service.context_length)
    tok_10 = calculate_discovery_max_tokens(10, context_length=service.context_length)
    tok_25 = calculate_discovery_max_tokens(25, context_length=service.context_length)

    assert tok_3 <= tok_10 <= tok_25
    # La profilazione documentale preventiva e il piano coincidono perfettamente
    prof_3 = service.profile_document(doc, "discovery", max_topics=3)
    prof_25 = service.profile_document(doc, "discovery", max_topics=25)
    assert prof_3.estimated_chunks == plan_3.planned_chunks_count
    assert prof_25.estimated_chunks == plan_25.planned_chunks_count


# ==============================================================================
# TEST 4: Conteggio reale delle richieste HTTP mediante adapter controllato
# ==============================================================================
def test_04_real_http_request_counting_with_controlled_adapter():
    doc = _make_doc("doc_http_count", count=2)
    client = ControlledMockClient(responses=[
        {"topics": [{"label": "Tema 1", "short_description": "Desc 1", "evidence_ids": [doc.bundles[0].text_evidence_sections[0].evidence_id]}]},
        {"topics": [{"label": "Tema 2", "short_description": "Desc 2", "evidence_ids": [doc.bundles[1].text_evidence_sections[0].evidence_id]}]},
    ])
    service = LocalTopicAnalysisService(max_chunk_bundles=1, enable_checkpointing=False)

    tracker_events = []
    def on_prog(evt):
        tracker_events.append(evt)
        # Verifica rigorosa dell'invariante ad ogni singolo evento!
        assert evt.requests_sent == evt.requests_successful + evt.requests_failed + evt.requests_in_flight

    res = service.run_topic_discovery(doc, client=client, model_name="test-model", progress_callback=on_prog)
    assert len(res.topics) == 2
    assert client.call_count == 2
    last_evt = tracker_events[-1]
    assert last_evt.requests_sent == 2
    assert last_evt.requests_successful == 2
    assert last_evt.requests_failed == 0
    assert last_evt.requests_in_flight == 0


# ==============================================================================
# TEST 5: Retry HTTP e JSON
# ==============================================================================
def test_05_retry_http_and_json():
    tracker = AnalysisProgressTracker(total_chunks=1)
    tracker.record_request_sent()
    assert tracker.requests_sent == 1
    assert tracker.requests_in_flight == 1
    assert tracker.requests_sent == tracker.requests_successful + tracker.requests_failed + tracker.requests_in_flight

    tracker.record_http_response_received()
    tracker.record_http_error(500, "Internal Server Error")
    assert tracker.requests_failed == 1
    assert tracker.requests_in_flight == 0
    assert tracker.requests_sent == tracker.requests_successful + tracker.requests_failed + tracker.requests_in_flight

    # Retry 1 via chunk_retry (che incrementa requests_sent e requests_in_flight)
    tracker.chunk_retry(1, "Tentativo retry")
    assert tracker.retries_count == 1
    assert tracker.requests_sent == 2
    assert tracker.requests_in_flight == 1
    assert tracker.requests_sent == tracker.requests_successful + tracker.requests_failed + tracker.requests_in_flight

    tracker.record_http_response_received()
    tracker.record_request_success()
    assert tracker.requests_successful == 1
    assert tracker.requests_in_flight == 0
    assert tracker.requests_sent == tracker.requests_successful + tracker.requests_failed + tracker.requests_in_flight


# ==============================================================================
# TEST 6: Context overflow e suddivisione dinamica
# ==============================================================================
def test_06_context_overflow_and_dynamic_splitting():
    tracker = AnalysisProgressTracker(total_chunks=1)
    tracker.start_chunk(1)
    tracker.record_request_sent()
    tracker.record_http_response_received()
    tracker.record_http_error(400, "Context length exceeded")

    tracker.split_chunk(1, subchunks_count=2, failed_prior_request=True, prior_error_msg="CONTEXT_OVERFLOW")
    assert tracker.total_chunks == 2
    assert tracker.split_count == 1
    assert tracker.requests_sent == tracker.requests_successful + tracker.requests_failed + tracker.requests_in_flight


# ==============================================================================
# TEST 7: Checkpoint abilitato dalla UI
# ==============================================================================
def test_07_checkpoint_enabled_by_ui(tmp_path):
    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "test_chk")
    assert chk_store.checkpoint_dir.exists()
    service = get_operational_analysis_service(checkpoint_store=chk_store, enable_checkpointing=True)
    assert service.enable_checkpointing is True
    assert service.checkpoint_store == chk_store


# ==============================================================================
# TEST 8: Interruzione e ripresa dopo ricreazione del servizio
# ==============================================================================
def test_08_interruption_and_resumption_after_service_recreation(tmp_path):
    doc = _make_doc("doc_resume", count=2)
    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "resume_chk")
    query = TopicQuery(topic_id="t1", label="Test", description="Test query")

    # Step 1: Esecuzione che si interrompe al chunk 2
    class InterruptedClient(ControlledMockClient):
        def chat_completion(self, *args, **kwargs):
            if self.call_count == 1:
                raise KeyboardInterrupt("Simulata interruzione utente!")
            return super().chat_completion(*args, **kwargs)

    client1 = InterruptedClient(responses=[
        {"decision": "PRESENT", "evidence_ids": [doc.bundles[0].text_evidence_sections[0].evidence_id], "rationale": "Chunk 1 OK"},
    ])
    svc1 = LocalTopicAnalysisService(max_chunk_bundles=1, checkpoint_store=chk_store, enable_checkpointing=True)

    with pytest.raises(KeyboardInterrupt):
        svc1.run_topic_detection(doc, query, client=client1, model_name="test-model")

    # Step 2: Ricreazione del servizio (nuova istanza indipendente)
    client2 = ControlledMockClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "Chunk 2 OK"},
    ])
    svc2 = LocalTopicAnalysisService(max_chunk_bundles=1, checkpoint_store=chk_store, enable_checkpointing=True)

    res = svc2.run_topic_detection(doc, query, client=client2, model_name="test-model")
    assert res.decision == TopicDecision.PRESENT
    # Solo 1 chiamata al client reale perche il chunk 1 e stato recuperato dal checkpoint!
    assert client2.call_count == 1


# ==============================================================================
# TEST 9: Modifica dei parametri della run e invalidazione checkpoint
# ==============================================================================
def test_09_run_parameter_alteration_invalidates_checkpoint(tmp_path):
    doc = _make_doc("doc_param", count=1)
    cfg1 = OperationalAnalysisConfig(temperature=0.0)
    cfg2 = OperationalAnalysisConfig(temperature=0.7)

    hash1 = compute_run_config_hash(model_id="qwen", prompt_version="v1", config=cfg1)
    hash2 = compute_run_config_hash(model_id="qwen", prompt_version="v1", config=cfg2)
    assert hash1 != hash2


# ==============================================================================
# TEST 10: Modifica dei timestamp e invalidazione checkpoint
# ==============================================================================
def test_10_document_timestamp_alteration_invalidates_checkpoint():
    doc1 = _make_doc("doc_ts", count=2, base_ts="2026-03-01T10:00:00Z")
    doc2 = _make_doc("doc_ts", count=2, base_ts="2026-03-01T11:00:00Z")

    fp1 = compute_document_fingerprint(doc1)
    fp2 = compute_document_fingerprint(doc2)
    assert fp1 != fp2


# ==============================================================================
# TEST 11: Corruzione o manipolazione del checkpoint
# ==============================================================================
def test_11_checkpoint_corruption_or_tampering(tmp_path):
    doc = _make_doc("doc_corrupt", count=1)
    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "corrupt_chk")
    cfg_hash = "abc123"
    doc_fp = compute_document_fingerprint(doc)

    # Scrittura file corrotto
    corrupt_file = chk_store.checkpoint_dir / f"chk_{doc_fp[:16]}_{cfg_hash[:16]}.json"
    corrupt_file.write_text("{ CORRUPTED NOT JSON }", encoding="utf-8")

    loaded = chk_store.load_and_validate_checkpoint(doc, cfg_hash)
    assert loaded is None  # Rifiuto sicuro senza crash


# ==============================================================================
# TEST 12: Errore di salvataggio checkpoint
# ==============================================================================
def test_12_checkpoint_save_error_handled_gracefully(tmp_path):
    doc = _make_doc("doc_save_err", count=1)
    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "save_err_chk")

    class FailingStore(AnalysisCheckpointStore):
        def save_chunk(self, *args, **kwargs):
            raise CheckpointSaveError("Spazio su disco esaurito!")

    failing_store = FailingStore(checkpoint_dir=tmp_path / "save_err_chk")
    svc = LocalTopicAnalysisService(checkpoint_store=failing_store, enable_checkpointing=True)
    client = ControlledMockClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "OK"}
    ])
    query = TopicQuery(topic_id="t1", label="Test", description="Test query")

    # L'analisi non deve crashare se il salvataggio del checkpoint fallisce!
    res = svc.run_topic_detection(doc, query, client=client, model_name="test-model")
    assert res.decision == TopicDecision.ABSENT


# ==============================================================================
# TEST 13: Nessuna ripetizione di chunk recuperati
# ==============================================================================
def test_13_zero_repeated_requests_for_recovered_chunks(tmp_path):
    doc = _make_doc("doc_zero_repeat", count=3)
    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "zero_repeat_chk")
    query = TopicQuery(topic_id="t1", label="Test", description="Test query")

    # Pre-popoliamo il checkpoint per chunk 0 e chunk 1
    doc_fp = compute_document_fingerprint(doc)
    svc_temp = LocalTopicAnalysisService(max_chunk_bundles=1, checkpoint_store=chk_store, enable_checkpointing=True)
    plan = svc_temp.create_analysis_plan(doc, "detection")
    chunks = list(plan.chunks)

    res_mock = TopicDetectionResult(
        topic=query,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Cached chunk",
        provenance_document_id=doc.document_id,
        metadata={"chunk_id": chunks[0].document_id},
    )
    cfg_fp = compute_run_config_hash(
        model_id="test-model",
        prompt_version="detection_operational",
        config=svc_temp.config,
        topic_query=query,
        chunks=chunks,
        extra_params={
            "strategy": "DIRECT_MULTILINGUAL",
            "operational_mode": True,
            "loaded_instance_id": None,
            "resolved_model_key": None,
            "quantization": None,
        },
    )
    chk_store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_mock, 3, "detection", "test-model", "detection_operational")
    chk_store.save_chunk(doc.document_id, doc_fp, cfg_fp, 1, res_mock, 3, "detection", "test-model", "detection_operational")

    client = ControlledMockClient(responses=[
        {"decision": "PRESENT", "evidence_ids": [doc.bundles[2].text_evidence_sections[0].evidence_id], "rationale": "Fresh chunk"}
    ])
    res = svc_temp.run_topic_detection(doc, query, client=client, model_name="test-model")

    # ESATTAMENTE 1 chiamata al client reale per il chunk rimanente!
    assert client.call_count == 1
    assert res.decision == TopicDecision.PRESENT


# ==============================================================================
# TEST 14: Analisi parziale e completa
# ==============================================================================
def test_14_partial_and_complete_analysis_reporting():
    doc = _make_doc("doc_status", count=2)
    # Uno chunk riesce, uno fallisce in modo non recuperabile
    client_partial = ControlledMockClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "OK"},
        AiStructuredOutputError("JSON invalido irreversibile"),
    ])
    svc = LocalTopicAnalysisService(max_chunk_bundles=1, enable_checkpointing=False)
    query = TopicQuery(topic_id="t1", label="Test", description="Test query")

    res = svc.run_topic_detection(doc, query, client=client_partial, model_name="test-model")
    assert res.metadata["status"] == "PARTIAL"
    assert res.decision == TopicDecision.UNCERTAIN

    # Con tutti i chunk riusciti
    client_complete = ControlledMockClient(responses=[
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "OK 1"},
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "OK 2"},
    ])
    res_comp = svc.run_topic_detection(doc, query, client=client_complete, model_name="test-model")
    assert res_comp.metadata["status"] == "SUCCESS"


# ==============================================================================
# TEST 15: Consolidamento senza fusioni semanticamente improprie
# ==============================================================================
def test_15_topic_consolidation_without_semantic_false_mergers():
    # Due temi con gli stessi stem ordinati ma relazioni lessicali opposte
    label1 = "Cessione di armi a privati"
    label2 = "Cessione di privati ad armi"

    canon1 = canonicalize_topic_label(label1)
    canon2 = canonicalize_topic_label(label2)

    # Devono rimanere DIVERSI: nessuna fusione impropria!
    assert canon1 != canon2

    # Invece duplicati lessicali effettivi devono coincidere
    dup1 = canonicalize_topic_label("Spedizione di armi")
    dup2 = canonicalize_topic_label("Spedizioni di armi")
    assert dup1 == dup2


# ==============================================================================
# TEST 16: Isolamento delle conversazioni
# ==============================================================================
def test_16_cross_document_isolation(tmp_path):
    doc_a = _make_doc("doc_A", count=2)
    doc_b = _make_doc("doc_B", count=2)

    chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "iso_chk")
    fp_a = compute_document_fingerprint(doc_a)
    fp_b = compute_document_fingerprint(doc_b)
    assert fp_a != fp_b

    # Salva un checkpoint per doc_a
    res = TopicDetectionResult(
        topic=TopicQuery(topic_id="t1", label="A", description="A"),
        decision=TopicDecision.PRESENT,
        evidence_ids=(doc_a.bundles[0].text_evidence_sections[0].evidence_id,),
        rationale="Rat A",
        provenance_document_id=doc_a.document_id,
        metadata={},
    )
    chk_store.save_chunk("doc_A", fp_a, "cfg_123", 0, res, 2, "detection", "qwen", "v1")

    # Doc B NON deve poter caricare il checkpoint di Doc A
    loaded_for_b = chk_store.load_and_validate_checkpoint(doc_b, "cfg_123")
    assert loaded_for_b is None


# ==============================================================================
# TEST 17: Integrita e completezza delle evidenze
# ==============================================================================
def test_17_evidence_id_integrity():
    doc = _make_doc("doc_ev_int", count=2)
    valid_ids = {sec.evidence_id for sec in doc.all_evidence_sections}

    # Risultato che cita un evidence_id estraneo
    alien_res = TopicDetectionResult(
        topic=TopicQuery(topic_id="t1", label="A", description="A"),
        decision=TopicDecision.PRESENT,
        evidence_ids=("alien_id::999",),
        rationale="Alien",
        provenance_document_id=doc.document_id,
        metadata={},
    )
    from analysis.checkpoint import AnalysisCheckpoint
    chk = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint="fp",
        config_fingerprint="cfg",
        analysis_type="detection",
        model_id="test-model",
        prompt_version="v1",
        total_planned_chunks=1,
        completed_chunk_indices=(0,),
        serialized_results={"0": {"decision": "PRESENT", "evidence_ids": ["alien_id::999"], "rationale": "Alien"}},
        created_at="2026-03-01T10:00:00Z",
        updated_at="2026-03-01T10:00:00Z",
    )
    assert validate_checkpoint_evidence_references(chk, doc) is False


# ==============================================================================
# TEST 18: Nessuna modifica al benchmark scientifico (SHA256 congelati)
# ==============================================================================
def test_18_frozen_scientific_benchmark_integrity():
    repo_root = Path(__file__).resolve().parents[2]

    frozen_expectations = {
        repo_root / "ai" / "experiment.py": "91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845",
        repo_root / "ai" / "benchmark.py": "DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36",
        repo_root / "run_benchmark.sh": "7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7",
    }

    for file_path, expected_hash in frozen_expectations.items():
        assert file_path.exists(), f"File scientifico mancante: {file_path}"
        content = file_path.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest().upper()
        assert actual_hash == expected_hash, (
            f"VIOLAZIONE CONGELAMENTO SCIENTIFICO su {file_path.name}: "
            f"atteso {expected_hash}, ottenuto {actual_hash}"
        )
