"""
tests/unit/test_export_attestation_provenance.py
------------------------------------------------
Test di verifica forense e fail-closed per la protezione della provenienza
degli export in ChatAnalysis (Audit 27/10/2026).
"""
import pytest
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.export import (
    build_topic_detection_export_report,
    build_topic_discovery_export_report,
)
from analysis.models import AnalysisOrigin, AttestationLevel


@pytest.fixture
def mock_document():
    return ConversationEvidenceDocument(
        document_id="test_doc_prov_123",
        bundles=(),
        chat_id="test_chat_prov",
    )


def test_reconstructed_run_strictly_provenance_not_verified(mock_document):
    """Verifica che una run ricostruita artificialmente non ottenga MAI LIVE_AUTHORITATIVE."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic A", short_description="Desc A", evidence_ids=["E001", "E002"])
        ],
        metadata={
            "run_id": "run_reconstructed_test",
            "origin": AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value,
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value
    assert report["experiment"]["attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value


def test_reconstructed_run_rejected_even_if_passed_to_authoritative_ids(mock_document):
    """Verifica che tentare di forzare una run ricostruita tramite authoritative_live_run_ids fallisca."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic B", short_description="Desc B", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_123",
            "origin": AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value,
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_123"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value
    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value


def test_provenance_verified_false_refuses_live_authoritative(mock_document):
    """Verifica che provenance_verified=False blocchi LIVE_AUTHORITATIVE anche con LIVE_LM_STUDIO."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic C", short_description="Desc C", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_live_unverified",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": False,
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_live_unverified"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value


def test_authentic_live_run_receives_live_authoritative(mock_document):
    """Verifica che una vera esecuzione LIVE_LM_STUDIO verificata con riscontri completi riceva LIVE_AUTHORITATIVE."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic D", short_description="Desc D", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_authentic_live_456",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [
                {"chunk_index": 0, "status": "SUCCESS", "completion_tokens": 145, "http_status": 200}
            ],
            "latency_seconds": 2.1,
            "total_evidence_count": 5,
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_authentic_live_456"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value


def test_provenance_verified_missing_refuses_live_authoritative(mock_document):
    """Verifica che la totale assenza della chiave provenance_verified rifiuti categoricamente LIVE_AUTHORITATIVE."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic E", short_description="Desc E", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_live_no_prov_flag",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            # provenance_verified MANCANTE
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_live_no_prov_flag"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value


def test_provenance_verified_none_refuses_live_authoritative(mock_document):
    """Verifica che provenance_verified=None rifiuti categoricamente LIVE_AUTHORITATIVE."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic F", short_description="Desc F", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_live_prov_none",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": None,  # Esplicitamente None
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_live_prov_none"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value


def test_authoritative_id_alone_insufficient_without_matching_document(mock_document):
    """Verifica che la presenza in authoritative_live_run_ids non basti se il documento non coincide."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic G", short_description="Desc G", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_doc_mismatch",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id="completely_different_doc_id",  # Mismatch documentale
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_doc_mismatch"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value


def test_authoritative_id_alone_insufficient_without_valid_model(mock_document):
    """Verifica che la presenza in authoritative_live_run_ids non basti se il modello è fittizio o mock."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic H", short_description="Desc H", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_fake_model",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "fake-model",  # Modello fake/mock
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_fake_model"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

    # Test con modello 'UNKNOWN'
    disc_res_unknown = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic H2", short_description="Desc H2", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_unknown_model",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "UNKNOWN",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id=mock_document.document_id,
    )

    report_unk = build_topic_discovery_export_report(
        discovery_result=disc_res_unknown,
        document=mock_document,
        authoritative_live_run_ids={"run_unknown_model"},
        redact_sensitive_topics=True,
    )

    assert report_unk["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report_unk["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value



def test_authoritative_id_alone_insufficient_without_configuration(mock_document):
    """Verifica che la presenza in authoritative_live_run_ids non basti se manca la configurazione verificabile."""
    disc_res = TopicDiscoveryResult(
        topics=[
            DiscoveredTopic(label="Topic I", short_description="Desc I", evidence_ids=["E001"])
        ],
        metadata={
            "run_id": "run_no_config",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            # Nessuna informazione di configurazione (nessun config_fingerprint, prompt_version, temperature, ecc.)
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{"status": "SUCCESS", "completion_tokens": 50}],
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_no_config"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value


def test_authoritative_id_alone_insufficient_without_registered_responses(mock_document):
    """Verifica che la presenza in authoritative_live_run_ids non basti se mancano risposte registrate verificate."""
    disc_res = TopicDiscoveryResult(
        topics=[],
        metadata={
            "run_id": "run_no_responses",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_discovery_operational_v2",
            "completed_chunk_count": 0,
            "chunk_diagnostics": [],  # Nessuna diagnostica registrata
        },
        provenance_document_id=mock_document.document_id,
    )

    report = build_topic_discovery_export_report(
        discovery_result=disc_res,
        document=mock_document,
        authoritative_live_run_ids={"run_no_responses"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value
    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value


def test_detection_export_respects_provenance_quarantine(mock_document):
    """Verifica che anche l'export di detection protegga la provenienza fail-closed."""
    det_res = TopicDetectionResult(
        topic=TopicQuery(topic_id="q1", label="Test Topic", description="Desc Topic"),
        decision=TopicDecision.PRESENT,
        evidence_ids=("E001",),
        rationale="Found in E001",
        provenance_document_id=mock_document.document_id,
        metadata={
            "run_id": "run_det_reconstructed",
            "origin": AnalysisOrigin.RECONSTRUCTED_UNVERIFIED.value,
        },
    )

    report = build_topic_detection_export_report(
        detection_result=det_res,
        document=mock_document,
        authoritative_live_run_ids={"run_det_reconstructed"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value


def test_detection_authentic_live_run_receives_live_authoritative(mock_document):
    """Verifica che una Topic Detection live autentica e verificata riceva LIVE_AUTHORITATIVE."""
    det_res = TopicDetectionResult(
        topic=TopicQuery(topic_id="q1", label="Test Topic", description="Desc Topic"),
        decision=TopicDecision.PRESENT,
        evidence_ids=("E001",),
        rationale="Found in E001",
        provenance_document_id=mock_document.document_id,
        metadata={
            "run_id": "run_det_authentic_live",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "prompt_version": "topic_detection_v1",
            "latency_seconds": 1.2,
            "completion_tokens": 80,
            "http_status": 200,
        },
    )

    report = build_topic_detection_export_report(
        detection_result=det_res,
        document=mock_document,
        authoritative_live_run_ids={"run_det_authentic_live"},
        redact_sensitive_topics=True,
    )

    assert report["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value


def test_checkpoint_with_missing_origin_never_becomes_live_without_proof(tmp_path):
    """
    CRITICITÀ #1 (Regola 6): Verifica fail-closed che un checkpoint con origin mancante
    non diventi MAI LIVE_LM_STUDIO o LIVE_AUTHORITATIVE né al caricamento (load_persisted_discovery_result),
    né durante una ripresa/consolidamento parziale con client LM Studio, né in export.
    """
    from datetime import datetime, timezone
    import json
    from ai.backend import LlmCompletionResponse
    from ai.lmstudio import LmStudioClient
    from ai.models import MessageEvidenceBundle
    from analysis.checkpoint import (
        AnalysisCheckpointStore,
        compute_document_fingerprint,
        compute_run_config_hash,
    )
    from analysis.models import OperationalAnalysisConfig
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

    bundles = []
    for i in range(4):
        raw = RawRecord(
            source_name="whatsapp_test",
            source_path="/test",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": f"Messaggio test {i}", "timestamp": "2026-10-27T10:00:00Z"},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string="2026-10-27T10:00:00Z",
            raw_value="2026-10-27T10:00:00Z",
        )
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_test",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio test {i} per verifica provenienza",
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg_prov_{i}",
            source_name="whatsapp_test",
            source_record_id=str(i),
            source_path="/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio test {i} per verifica provenienza",
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))

    doc = ConversationEvidenceDocument(
        document_id="doc_prov_guard_test",
        bundles=tuple(bundles),
        source_name="whatsapp_test",
    )

    chk_dir = tmp_path / "chk_prov_guard"
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    cfg = OperationalAnalysisConfig(max_chunk_bundles=2)
    service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, config=cfg)

    plan = service.create_analysis_plan(
        doc,
        analysis_type="discovery",
        max_topics=10,
        user_max_tokens=1024,
        context_length=service.context_length,
    )
    chunks = list(plan.chunks)
    assert len(chunks) == 2

    doc_fp = compute_document_fingerprint(doc)
    cfg_fp = compute_run_config_hash(
        model_id="qwen2.5-7b-instruct",
        prompt_version="discovery_operational",
        config=OperationalAnalysisConfig(
            model_spec="qwen2.5-7b-instruct",
            context_length=service.context_length,
            timeout_seconds=service.timeout_seconds,
            max_tokens=1024,
            temperature=0.0,
            seed=42,
            profile=service.profile,
            chunk_character_budget=service.max_chunk_characters,
            max_chunk_bundles=service.max_chunk_bundles,
            max_chunk_tokens=plan.chunk_token_budget,
        ),
        chunks=chunks,
        extra_params={
            "max_topics": 10,
            # Identità della run Discovery: policy di accettazione corrente (assert del test invariati).
            "acceptance_policy": "degeneration_v2_structural",
            # Regola del budget d'uscita dei microchunk (parte dell'identità della run corrente; assert invariati).
            "micro_output_budget_policy": "micro_budget_formula_v2",
            "resolved_model_key": "qwen2.5-7b-instruct",
            "quantization": None,
        },
    )

    e0 = chunks[0].all_evidence_sections[0].evidence_id
    e1 = chunks[1].all_evidence_sections[0].evidence_id

    # Salviamo il Chunk 0 SENZA origin (origin="" e metadata priva di origin)
    chunk0_no_origin = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Tema Senza Origine",
                short_description="Blocco storico privo di origin",
                evidence_ids=(e0,),
            ),
        ),
        provenance_document_id=doc.document_id,
        metadata={"model": "qwen2.5-7b-instruct"},
    )
    store.save_chunk(
        document_id=doc.document_id,
        document_fingerprint=doc_fp,
        config_fingerprint=cfg_fp,
        chunk_idx=0,
        result=chunk0_no_origin,
        total_planned_chunks=2,
        analysis_type="discovery",
        model_id="qwen2.5-7b-instruct",
        prompt_version="discovery_operational",
        origin="",
        run_id="",
    )

    # 1. Verifica che il caricamento parziale con origin mancante sia UNATTESTED e origin=None
    loaded_partial = service.load_persisted_discovery_result(doc)
    assert loaded_partial is not None
    assert loaded_partial.metadata.get("origin") is None
    assert loaded_partial.metadata.get("attestation_level") == AttestationLevel.UNATTESTED.value

    # 2. Riprendiamo la run con un client che eredita da LmStudioClient per elaborare il Chunk 1:
    # poiche' il Chunk 0 recuperato non ha origin LIVE_LM_STUDIO, l'aggregato finale
    # NON deve diventare LIVE_LM_STUDIO ne' LIVE_AUTHORITATIVE!
    class FakeLiveLmStudioSubClient(LmStudioClient):
        def __init__(self):
            super().__init__(base_url="http://127.0.0.1:1234", model_id="qwen2.5-7b-instruct")
            self._last_loaded_model_key = "qwen2.5-7b-instruct"

        def is_available(self, timeout_seconds: float = 2.0) -> bool:
            return True

        def list_models(self) -> tuple[str, ...]:
            return ("qwen2.5-7b-instruct",)

        def ensure_model_loaded(self, model_id: str, context_length: int | None = None, timeout_seconds: float | None = None) -> str:
            self._last_loaded_model_key = "qwen2.5-7b-instruct"
            return "qwen2.5-7b-instruct"

        def chat_completion(self, messages, model_id=None, schema=None, temperature=0.0, seed=42, timeout_seconds=60.0, max_tokens=None):
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Tema Blocco 1",
                            "short_description": "Secondo blocco",
                            "evidence_ids": ["E001"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop", "usage": {"prompt_tokens": 50, "completion_tokens": 20}},
            )

    live_sub_client = FakeLiveLmStudioSubClient()
    resumed_res = service.run_topic_discovery(
        document=doc,
        client=live_sub_client,
        model_name="qwen2.5-7b-instruct",
        max_topics=10,
        max_tokens=1024,
    )

    assert resumed_res.metadata.get("recovered_chunk_count") == 1
    assert resumed_res.metadata.get("origin") is None
    assert resumed_res.metadata.get("provenance_verified") is False
    assert resumed_res.metadata["run_id"] not in service._authoritative_live_run_ids

    export_rep = build_topic_discovery_export_report(
        discovery_result=resumed_res,
        document=doc,
        authoritative_live_run_ids=service._authoritative_live_run_ids,
        redact_sensitive_topics=True,
    )
    assert export_rep["experiment"]["verified_attestation_level"] == AttestationLevel.PROVENANCE_NOT_VERIFIED.value
    assert export_rep["experiment"]["verified_attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

    # 3. Anche ricaricando il checkpoint 2/2 da una nuova istanza del service, poiche' il Chunk 0
    # non aveva origin e il Chunk 1 aveva LIVE_LM_STUDIO, il caricamento fallisce chiuso (origin=None, UNATTESTED)
    fresh_service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, config=cfg)
    reloaded_full = fresh_service.load_persisted_discovery_result(doc)
    assert reloaded_full is not None
    assert reloaded_full.metadata.get("origin") is None
    assert reloaded_full.metadata.get("attestation_level") == AttestationLevel.UNATTESTED.value


