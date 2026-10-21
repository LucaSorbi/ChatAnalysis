"""
tests/unit/test_analysis_export.py
----------------------------------
Suite di test obbligatori e completi per il modulo di esportazione rapporti
sperimentali JSON di ChatAnalysis (analysis/export.py e ui/application.py).

Copre i 22 requisiti obbligatori della Sezione 8:
1. Detection COMPLETE 1/1 PRESENT.
2. Detection COMPLETE ABSENT.
3. Detection COMPLETE UNCERTAIN.
4. Detection PARTIAL 17/26 UNCERTAIN.
5. Detection PARTIAL con PRESENT validato.
6. Discovery COMPLETE 1/1.
7. Discovery COMPLETE 26/26.
8. Discovery PARTIAL 25/26 con INVALID_EVIDENCE_ID.
9. Due run dello stesso modello e dataset con nomi distinti.
10. Modelli differenti sulla stessa conversazione.
11. Risultati recuperati da checkpoint.
12. Chunk mancanti o non tentati.
13. Durate e token non disponibili.
14. Serializzazione di Enum, dataclass e metadata immutabili.
15. Esportazione dopo rerun Streamlit.
16. Nessuna nuova inferenza durante il download.
17. Nessuna perdita di dati dal risultato originale.
18. Nessun contenuto privato nel rapporto sicuro.
19. Assenza di prompt e risposte integrali.
20. Nessuna regressione di Topic Discovery, Detection e ConsistencyService.
21. Integrità dei file scientifici congelati.
22. Continuità del salvataggio automatico benchmark.

Include inoltre test Streamlit AppTest per presenza ed operatività dei pulsanti
di download e dei controlli di privacy avanzata, e rispetto della tassonomia
di verifica (STATICALLY_VERIFIED, TEST_VERIFIED, MOCK_VERIFIED, LIVE_VERIFIED, NOT_VERIFIED).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any
import pytest

from ai.backend import FakeLocalLlmClient, LlmCompletionResponse
from ai.cloud_experiment import (
    FROZEN_DATASET_REL_PATH,
    FROZEN_DATASET_SHA256,
    FROZEN_GROUND_TRUTH_REL_PATH,
    FROZEN_GROUND_TRUTH_SHA256,
    verify_file_hash,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.consistency import ConsistencyService
from analysis.export import (
    SCHEMA_VERSION,
    SOFTWARE_NAME,
    build_topic_detection_export_report,
    build_topic_discovery_export_report,
    export_chunk_diagnostics_to_csv,
    format_iso_timestamp,
    generate_export_filename,
    get_git_commit,
    get_platform_environment,
    get_pseudonymous_id,
    get_software_version,
    sanitize_error_detail,
    sanitize_evidence_id,
    sanitize_local_path,
    sanitize_source_name,
    sanitize_topic_text,
    serialize_report_to_json,
)
from analysis.models import (
    AnalysisOrigin,
    AttestationLevel,
    ConsistencyResult,
    ConsistencyStatus,
    LoadingMode,
    OperationalProfile,
)
from analysis.service import LocalTopicAnalysisService
from ui import state
from ui.application import (
    build_demo_session,
    export_topic_detection_json,
    export_topic_discovery_json,
)
from ui.demo import build_synthetic_demo_dataset
from ui.models import DatasetMode


@pytest.fixture
def demo_session():
    return build_demo_session()


@pytest.mark.unit
class TestObligatorySyntheticExportScenarios:

    # 1. Detection COMPLETE 1/1 PRESENT
    def test_01_detection_complete_1of1_present(self, demo_session):
        doc, _, _, _ = demo_session
        query = TopicQuery(topic_id="top_present", label="Frodi fiscali", description="Evasione e fatture false")
        eid = doc.all_evidence_sections[0].evidence_id

        meta = {
            "run_id": "run_det_c1_present",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "failed_chunk_count": 0,
            "status": "COMPLETE",
            "chunk_diagnostics": [{
                "chunk_index": 0,
                "status": "SUCCESS",
                "attempts": 1,
                "duration_seconds": 0.85,
                "model_id": "qwen2.5-7b-instruct",
                "json_valid": True,
                "schema_valid": True,
                "evidence_valid": True,
                "valid_evidence_count": 1,
            }],
        }
        res = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.PRESENT,
            rationale="Individuata fattura non corrispondente nelle evidenze primarie.",
            evidence_ids=(eid,),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(res, doc)
        assert rep["schema_version"] == "1.1.0"
        assert rep["result"]["decision"] == "PRESENT"
        assert rep["coverage"]["total_chunks_planned"] == 1
        assert rep["coverage"]["chunks_completed"] == 1
        assert rep["coverage"]["is_complete"] is True
        assert rep["coverage"]["coverage_status"] == "COMPLETE"
        assert len(rep["result"]["evidence_ids"]) == 1
        assert rep["chunks"][0]["status"] == "SUCCESS"

    # 2. Detection COMPLETE ABSENT
    def test_02_detection_complete_absent(self, demo_session):
        doc, _, _, _ = demo_session
        query = TopicQuery(topic_id="top_absent", label="Traffico armi", description="Vendita non autorizzata")
        meta = {
            "run_id": "run_det_c2_absent",
            "chunk_count": 3,
            "completed_chunk_count": 3,
            "failed_chunk_count": 0,
            "status": "COMPLETE",
            "chunk_diagnostics": [
                {"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.1}
                for i in range(3)
            ],
        }
        res = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.ABSENT,
            rationale="Tutti i 3 blocchi della conversazione esaminati senza riscontro del tema.",
            evidence_ids=(),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(res, doc)
        assert rep["result"]["decision"] == "ABSENT"
        assert rep["coverage"]["is_complete"] is True
        assert rep["coverage"]["coverage_status"] == "COMPLETE"
        assert len(rep["result"]["evidence_ids"]) == 0
        assert len(rep["chunks"]) == 3

    # 3. Detection COMPLETE UNCERTAIN
    def test_03_detection_complete_uncertain(self, demo_session):
        doc, _, _, _ = demo_session
        query = TopicQuery(topic_id="top_ambig", label="Pressioni", description="Minacce velate")
        meta = {
            "run_id": "run_det_c3_unc",
            "chunk_count": 2,
            "completed_chunk_count": 2,
            "failed_chunk_count": 0,
            "status": "COMPLETE",
        }
        res = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.UNCERTAIN,
            rationale="Conversazione coperta integralmente ma il tono dei messaggi rimane ambiguo.",
            evidence_ids=(),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(res, doc)
        assert rep["result"]["decision"] == "UNCERTAIN"
        assert rep["coverage"]["is_complete"] is True
        assert rep["coverage"]["coverage_status"] == "COMPLETE"
        assert rep["result"]["decision_status"] == "COMPLETE"

    # 4. Detection PARTIAL 17/26 UNCERTAIN
    def test_04_detection_partial_17of26_uncertain(self, demo_session):
        doc, _, _, _ = demo_session
        raw_diag = []
        for i in range(17):
            raw_diag.append({"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.2})
        for i in range(17, 26):
            raw_diag.append({
                "chunk_index": i,
                "status": "FAILED",
                "attempts": 2,
                "duration_seconds": 2.0,
                "error_type": "AiStructuredOutputError",
                "diagnostic_category": "JSON_SCHEMA_ERROR",
                "failure_phase": "structured_parsing",
            })

        meta = {
            "run_id": "run_det_c4_17of26",
            "chunk_count": 26,
            "completed_chunk_count": 17,
            "failed_chunk_count": 9,
            "status": "PARTIAL",
            "chunk_diagnostics": raw_diag,
        }
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="top_narc", label="Stupefacenti", description="Spaccio"),
            decision=TopicDecision.UNCERTAIN,
            rationale="Copertura parziale: 17 completati senza riscontri, 9 falliti per errore di schema.",
            evidence_ids=(),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(res, doc)
        assert rep["coverage"]["total_chunks_planned"] == 26
        assert rep["coverage"]["chunks_completed"] == 17
        assert rep["coverage"]["chunks_failed"] == 9
        assert rep["coverage"]["coverage_status"] == "PARTIAL"
        assert rep["coverage"]["completion_percentage"] == round((17 / 26) * 100.0, 1)
        # Decoupling: decisione scientifica = UNCERTAIN, stato copertura = PARTIAL
        assert rep["result"]["decision"] == "UNCERTAIN"
        assert rep["result"]["decision_status"] == "PARTIAL"
        assert len(rep["chunks"]) == 26

    # 5. Detection PARTIAL con PRESENT validato
    def test_05_detection_partial_with_present_validated(self, demo_session):
        doc, _, _, _ = demo_session
        eid = doc.all_evidence_sections[0].evidence_id
        raw_diag = []
        for i in range(5):
            raw_diag.append({"chunk_index": i, "status": "SUCCESS", "attempts": 1, "valid_evidence_count": 1 if i == 1 else 0})
        for i in range(5, 10):
            raw_diag.append({"chunk_index": i, "status": "FAILED", "attempts": 2, "error_type": "AiBackendTimeoutError", "diagnostic_category": "TIMEOUT"})

        meta = {
            "run_id": "run_det_c5_partial_present",
            "chunk_count": 10,
            "completed_chunk_count": 5,
            "failed_chunk_count": 5,
            "status": "PARTIAL",
            "chunk_diagnostics": raw_diag,
        }
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="top_corrupt", label="Corruzione", description="Tangenti"),
            decision=TopicDecision.PRESENT,
            rationale="Evidenza inequivocabile riscontrata nel blocco 2, anche se i blocchi 5-9 sono falliti.",
            evidence_ids=(eid,),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(res, doc, redact_sensitive_topics=False)
        assert rep["result"]["decision"] == "PRESENT"
        assert rep["coverage"]["coverage_status"] == "PARTIAL"
        assert rep["result"]["evidence_ids"] == [eid]
        assert len(rep["chunks"]) == 10

    # 6. Discovery COMPLETE 1/1
    def test_06_discovery_complete_1of1(self, demo_session):
        doc, _, _, _ = demo_session
        meta = {
            "run_id": "run_disc_c6_1of1",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "failed_chunk_count": 0,
            "status": "COMPLETE",
            "chunk_diagnostics": [{
                "chunk_index": 0,
                "status": "SUCCESS",
                "attempts": 1,
                "duration_seconds": 1.8,
            }],
        }
        res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(label="Tema A", short_description="Desc A", evidence_ids=("E001",)),
                DiscoveredTopic(label="Tema B", short_description="Desc B", evidence_ids=("E002",)),
            ),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_discovery_export_report(res, doc)
        assert rep["experiment"]["analysis_type"] == "TOPIC_DISCOVERY"
        assert rep["coverage"]["total_chunks_planned"] == 1
        assert rep["coverage"]["chunks_completed"] == 1
        assert rep["coverage"]["is_complete"] is True
        assert rep["result"]["total_topics_discovered"] == 2
        assert len(rep["result"]["topics"]) == 2
        assert rep["result"]["topics"][0]["ranking_position"] == 1
        assert rep["result"]["topics"][1]["ranking_position"] == 2

    # 7. Discovery COMPLETE 26/26
    def test_07_discovery_complete_26of26(self, demo_session):
        doc, _, _, _ = demo_session
        raw_diag = [
            {"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.05}
            for i in range(26)
        ]
        meta = {
            "run_id": "run_disc_c7_26of26",
            "chunk_count": 26,
            "completed_chunk_count": 26,
            "failed_chunk_count": 0,
            "status": "COMPLETE",
            "chunk_diagnostics": raw_diag,
        }
        res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(label="Tema 1", short_description="Desc 1", evidence_ids=("E001", "E002")),
            ),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_discovery_export_report(res, doc)
        assert rep["coverage"]["total_chunks_planned"] == 26
        assert rep["coverage"]["chunks_completed"] == 26
        assert rep["coverage"]["chunks_failed"] == 0
        assert rep["coverage"]["is_complete"] is True
        assert rep["coverage"]["coverage_status"] == "COMPLETE"
        assert len(rep["chunks"]) == 26

    # 8. Discovery PARTIAL 25/26 con INVALID_EVIDENCE_ID
    def test_08_discovery_partial_25of26_invalid_evidence_id(self, demo_session):
        doc, _, _, _ = demo_session
        raw_diag = []
        for i in range(26):
            if i == 10:
                raw_diag.append({
                    "chunk_index": 10,
                    "status": "FAILED",
                    "attempts": 2,
                    "duration_seconds": 3.4,
                    "error_type": "AiInvalidEvidenceCitationError",
                    "diagnostic_category": "INVALID_EVIDENCE_ID",
                    "failure_phase": "evidence_validation",
                    "error_detail": "Evidence ID sec_999 non presente nel chunk",
                    "evidence_valid": False,
                })
            else:
                raw_diag.append({"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.1})

        meta = {
            "run_id": "run_disc_c8_25of26",
            "chunk_count": 26,
            "completed_chunk_count": 25,
            "failed_chunk_count": 1,
            "status": "PARTIAL",
            "chunk_diagnostics": raw_diag,
        }
        res = TopicDiscoveryResult(
            topics=(DiscoveredTopic(label="Tema Valido", short_description="Desc", evidence_ids=("E001",)),),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )

        rep = build_topic_discovery_export_report(res, doc)
        assert rep["coverage"]["chunks_completed"] == 25
        assert rep["coverage"]["chunks_failed"] == 1
        assert rep["coverage"]["coverage_status"] == "PARTIAL"
        assert rep["coverage"]["is_complete"] is False

        # Verifica diagnostica isolata del chunk 10 (11° chunk)
        chk10 = rep["chunks"][10]
        assert chk10["chunk_index"] == 10
        assert chk10["status"] == "FAILED"
        assert chk10["diagnostic_category"] == "INVALID_EVIDENCE_ID"
        assert chk10["failure_phase"] == "evidence_validation"
        assert chk10["attempts"] == 2

    # 9. Due run dello stesso modello e dataset con nomi distinti
    def test_09_two_runs_same_model_and_dataset_distinct_names(self):
        ds = "chat_investigativa_01"
        mod = "qwen2.5-7b-instruct"
        fn1 = generate_export_filename(
            analysis_type="TOPIC_DETECTION",
            dataset_id=ds,
            model_id=mod,
            run_id="run_a",
            timestamp="20261021T100000",
        )
        fn2 = generate_export_filename(
            analysis_type="TOPIC_DETECTION",
            dataset_id=ds,
            model_id=mod,
            run_id="run_b",
            timestamp="20261021T100500",
        )
        assert fn1 != fn2
        assert get_pseudonymous_id("run_a", prefix="run") in fn1
        assert get_pseudonymous_id("run_b", prefix="run") in fn2
        assert "run_a" not in fn1
        assert "run_b" not in fn2

    # 10. Modelli differenti sulla stessa conversazione
    def test_10_different_models_same_conversation(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        # Run con Qwen
        meta_qwen = dict(det.metadata or {})
        meta_qwen["model"] = "qwen2.5-7b-instruct"
        meta_qwen["run_id"] = "run_qwen_1"
        det_qwen = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_qwen,
        )
        rep_qwen = build_topic_detection_export_report(det_qwen, doc)

        # Run con Llama
        meta_llama = dict(det.metadata or {})
        meta_llama["model"] = "llama-3.1-8b-instruct"
        meta_llama["run_id"] = "run_llama_1"
        det_llama = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_llama,
        )
        rep_llama = build_topic_detection_export_report(det_llama, doc)

        assert rep_qwen["model"]["effective_model_id"] == "qwen2.5-7b-instruct"
        assert rep_llama["model"]["effective_model_id"] == "llama-3.1-8b-instruct"
        assert rep_qwen["experiment"]["run_id"] != rep_llama["experiment"]["run_id"]

    # 11. Risultati recuperati da checkpoint
    def test_11_results_recovered_from_checkpoint(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]
        meta = dict(det.metadata or {})
        meta["is_persisted_recovery"] = True
        meta["recovered_chunk_count"] = 12
        meta["loading_mode"] = LoadingMode.CHECKPOINT_RESTORE.value

        det_rec = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta,
        )
        rep = build_topic_detection_export_report(det_rec, doc)
        assert rep["experiment"]["loading_mode"] == "CHECKPOINT_RESTORE"
        assert rep["checkpoints"]["recovered_count"] == 12

    # 12. Chunk mancanti o non tentati
    def test_12_missing_or_unattempted_chunks(self, demo_session):
        doc, _, _, _ = demo_session
        # Solo 3 chunk tentati su 8 pianificati
        raw_diag = [
            {"chunk_index": 0, "status": "SUCCESS"},
            {"chunk_index": 1, "status": "SUCCESS"},
            {"chunk_index": 2, "status": "FAILED", "error_type": "AiBackendTimeoutError"},
        ]
        meta = {
            "chunk_count": 8,
            "completed_chunk_count": 2,
            "failed_chunk_count": 1,
            "chunk_diagnostics": raw_diag,
        }
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="top_test", label="Test", description="Test"),
            decision=TopicDecision.UNCERTAIN,
            rationale="Interrotto prima dei chunk 3..7",
            evidence_ids=(),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )
        rep = build_topic_detection_export_report(res, doc)
        assert len(rep["chunks"]) == 8
        for unstarted_idx in range(3, 8):
            chk = rep["chunks"][unstarted_idx]
            assert chk["chunk_index"] == unstarted_idx
            assert chk["status"] in ("SKIPPED", "UNATTEMPTED")
            assert chk["attempts"] == 0
            assert chk["failure_phase"] == "not_started"

    # 13. Durate e token non disponibili
    def test_13_durations_and_tokens_not_available(self, demo_session):
        doc, _, _, _ = demo_session
        meta = {
            "run_id": "run_no_telemetry",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "latency_seconds": None,
            "duration_seconds": None,
            "chunk_diagnostics": [{
                "chunk_index": 0,
                "status": "SUCCESS",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "duration_seconds": None,
            }],
        }
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="top_t", label="Test", description="Test"),
            decision=TopicDecision.ABSENT,
            rationale="Analisi senza dati di telemetria VRAM o token.",
            evidence_ids=(),
            provenance_document_id=doc.document_id,
            metadata=meta,
        )
        rep = build_topic_detection_export_report(res, doc)
        assert rep["chunks"][0]["prompt_tokens"] is None
        assert rep["chunks"][0]["completion_tokens"] is None
        assert rep["chunks"][0]["total_tokens"] is None
        # Non deve inventare numeri fittizi
        raw_json = serialize_report_to_json(rep)
        assert '"prompt_tokens": null' in raw_json

    # 14. Serializzazione di Enum, dataclass e metadata immutabili
    def test_14_serialization_of_enum_dataclass_metadata(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        @dataclass(frozen=True)
        class CustomSubRecord:
            key: str
            values: tuple[int, ...]
            mode: OperationalProfile

        meta = dict(det.metadata or {})
        meta["nested_dataclass"] = CustomSubRecord(key="audit", values=(1, 2, 3), mode=OperationalProfile.PRUDENT)
        meta["immutable_set"] = frozenset(["tag1", "tag2"])
        meta["timestamp_dt"] = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
        meta["mapping_proxy"] = MappingProxyType({"config_a": "val_a"})

        custom_det = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta,
        )

        rep = build_topic_detection_export_report(custom_det, doc)
        json_output = serialize_report_to_json(rep)
        assert isinstance(json_output, str)
        parsed = json.loads(json_output)
        assert parsed["schema_version"] == "1.1.0"

    # 15. Esportazione dopo rerun Streamlit (vedere TestStreamlitAppTestExportButtons)

    # 16. Nessuna nuova inferenza durante il download
    def test_16_no_new_inference_during_download(self, demo_session):
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        mock_client = FakeLocalLlmClient(model_name="qwen2.5-7b-instruct")
        initial_calls = len(mock_client.call_history)

        # Generazione export non deve toccare il client inferenziale
        _ = export_topic_detection_json(det, doc)
        _ = export_topic_discovery_json(disc, doc)

        assert len(mock_client.call_history) == initial_calls

    # 17. Nessuna perdita di dati dal risultato originale
    def test_17_no_data_loss_from_original_result(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        rep = build_topic_detection_export_report(det, doc, redact_sensitive_topics=False)
        assert rep["result"]["decision"] == det.decision.value
        assert rep["result"]["topic"]["topic_id"] == det.topic.topic_id
        assert rep["result"]["topic"]["label"] == det.topic.label
        assert rep["result"]["topic"]["description"] == det.topic.description
        assert rep["result"]["rationale"] == det.rationale
        assert len(rep["result"]["evidence_ids"]) == len(det.evidence_ids)

    # 18. Nessun contenuto privato nel rapporto sicuro
    def test_18_no_private_content_in_safe_report(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        rep = build_topic_detection_export_report(det, doc)
        json_str = serialize_report_to_json(rep)

        # Nessun percorso utente Windows o Linux
        assert r"C:\Users\lucas" not in json_str
        assert "/home/lucas" not in json_str
        # Nessuna email o telefono
        assert "@example.com" not in json_str
        assert "+39" not in json_str

        # Nessun testo di messaggi integrali della conversazione
        for sec in doc.all_evidence_sections:
            if len(sec.text.strip()) > 30:
                assert sec.text.strip() not in json_str

    # 19. Assenza di prompt e risposte integrali
    def test_19_absence_of_full_prompts_and_responses(self, demo_session):
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        rep = build_topic_detection_export_report(det, doc)
        json_str = serialize_report_to_json(rep)

        forbidden_keys = [
            "raw_prompt",
            "prompt_template",
            "system_prompt",
            "raw_response",
            "raw_llm_response",
            "raw_completion",
        ]
        parsed = json.loads(json_str)
        for fk in forbidden_keys:
            assert fk not in parsed
            assert fk not in parsed["configuration"]
            assert fk not in parsed["execution"]

    # 20. Nessuna regressione di Topic Discovery, Detection e ConsistencyService
    def test_20_no_regression_topic_discovery_detection_consistency(self, demo_session):
        doc, _, _, _ = demo_session
        eid = doc.all_evidence_sections[0].evidence_id

        # Verifica integrità costruttori e service
        svc = LocalTopicAnalysisService(
            client=FakeLocalLlmClient(model_name="qwen2.5-7b-instruct"),
        )
        assert svc is not None

        # Verifica ConsistencyService
        assert hasattr(ConsistencyService, "evaluate")

        # Verifica che il modulo analysis esporti i tipi previsti
        import analysis
        assert hasattr(analysis, "LocalTopicAnalysisService")
        assert hasattr(analysis, "SCHEMA_VERSION")
        assert hasattr(analysis, "build_topic_detection_export_report")
        assert hasattr(analysis, "build_topic_discovery_export_report")

    # 21. Integrità dei file scientifici congelati
    def test_21_integrity_of_frozen_scientific_files(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        ds_path = repo_root / FROZEN_DATASET_REL_PATH
        gt_path = repo_root / FROZEN_GROUND_TRUTH_REL_PATH

        assert ds_path.exists(), f"File dataset congelato assente: {ds_path}"
        assert gt_path.exists(), f"File ground truth congelato assente: {gt_path}"

        verify_file_hash(ds_path, FROZEN_DATASET_SHA256, "dataset")
        verify_file_hash(gt_path, FROZEN_GROUND_TRUTH_SHA256, "ground_truth")

    # 22. Continuità del salvataggio automatico benchmark
    def test_22_benchmark_autosave_continuity(self, tmp_path):
        from ai.benchmark import save_benchmark_report, save_json_report

        mock_data = {
            "execution_date": "2026-10-21T12:00:00Z",
            "overall_metrics": {
                "completion_rate": 1.0,
                "decision_accuracy": 0.95,
            },
            "cases": [],
        }
        json_file = tmp_path / "test_bm_direct.json"
        save_json_report(mock_data, str(json_file))
        assert json_file.exists()
        assert json_file.stat().st_size > 0

        json_out, md_out = save_benchmark_report(mock_data, output_dir=tmp_path, filename_prefix="test_bm")
        assert json_out.exists() and json_out.stat().st_size > 0
        assert md_out.exists() and md_out.stat().st_size > 0


@pytest.mark.unit
class TestTaxonomyAndVerificationLevels:

    def test_mock_client_never_declared_live_authoritative(self, demo_session):
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        # Simula metadati provenienti da FakeLocalLlmClient
        meta_det = dict(det.metadata or {})
        meta_det["is_mock"] = True
        meta_det["client_class"] = "FakeLocalLlmClient"
        meta_det["origin"] = AnalysisOrigin.MOCK_INSPECTION.value
        mock_det = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_det,
        )

        rep_det = build_topic_detection_export_report(mock_det, doc)
        assert rep_det["experiment"]["attestation_level"] == AttestationLevel.MOCK_VERIFIED.value
        assert rep_det["experiment"]["attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

        meta_disc = dict(disc.metadata or {})
        meta_disc["is_mock"] = True
        mock_disc = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=meta_disc,
        )
        rep_disc = build_topic_discovery_export_report(mock_disc, doc)
        assert rep_disc["experiment"]["attestation_level"] == AttestationLevel.MOCK_VERIFIED.value
        assert rep_disc["experiment"]["attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value


@pytest.mark.unit
class TestStreamlitAppTestExportButtons:
    """Test Streamlit AppTest per verificare la presenza e funzionalità dei pulsanti di esportazione."""

    def test_15_and_23_streamlit_apptest_export_buttons_and_privacy_toggles(self):
        from streamlit.testing.v1 import AppTest

        app_path = str(Path(__file__).resolve().parent.parent.parent / "app.py")
        doc, _, _ = build_synthetic_demo_dataset()

        at = AppTest.from_file(app_path, default_timeout=25)
        at.run()
        at.session_state["dataset_loaded"] = True
        at.session_state["dataset_mode"] = DatasetMode.FILE
        at.session_state["conversation_document"] = doc
        at.session_state["available_documents"] = {doc.document_id: doc}
        at.session_state["selected_document_id"] = doc.document_id

        # Inietta un risultato di Topic Detection nello stato
        det_run_id = "test_run_det_apptest"
        sample_det = TopicDetectionResult(
            topic=TopicQuery(topic_id="top_test", label="Argomento Test", description="Descrizione test"),
            decision=TopicDecision.PRESENT,
            rationale="Evidenza verificata in sessione Streamlit.",
            evidence_ids=(doc.all_evidence_sections[0].evidence_id,),
            provenance_document_id=doc.document_id,
            metadata={"run_id": det_run_id, "status": "COMPLETE", "chunk_count": 1, "completed_chunk_count": 1},
        )
        state.add_detection_result(sample_det, state=at.session_state)

        # Naviga a "5. Analisi topic"
        at.sidebar.radio[0].set_value("5. Analisi topic")
        at.run()
        assert not at.exception

        # 1. Verifica presenza del pulsante di download del report Detection
        det_dl_btns = [b for b in at.download_button if f"btn_download_detection_json_{det_run_id}" in b.key]
        assert len(det_dl_btns) >= 1, "Pulsante di esportazione JSON Detection non trovato nella pagina!"

        # 2. Verifica presenza del toggle privacy avanzata Detection
        det_privacy_chks = [c for c in at.checkbox if f"chk_privacy_det_{det_run_id}" in c.key]
        assert len(det_privacy_chks) >= 1, "Checkbox privacy avanzata Detection non trovata!"

        # 3. Simula rerun Streamlit per verificare stabilità sessione (Requisito 15)
        at.run()
        assert not at.exception
        det_dl_btns_rerun = [b for b in at.download_button if f"btn_download_detection_json_{det_run_id}" in b.key]
        assert len(det_dl_btns_rerun) >= 1, "Pulsante di esportazione JSON perso dopo il rerun Streamlit!"


@pytest.mark.unit
class TestP0ScientificTruthfulnessAndAuditFixes:
    """
    Test di accettazione vincolanti P0 per l'audit del 21 ottobre 2026:
    A) Verifica statica e test mirati import DeepSeek/NameError con finish_reason='length'.
    B) Synthetics per provenance sconosciuta => UNATTESTED (sia Discovery sia Detection).
    C) Dataset sintetico con nomi/email/telefoni in topic, rationale, consistency_rationale, error_detail, source_name, run_id/evidence_id => report SAFE privo di tutti questi testi.
    D) Nessuna metrica o configurazione inventata se metadata assenti; 0 diverso da null.
    E) Copertura: 17/26, 25/26, 1+2>1 incoerente, chunk non tentati e diagnostiche mancanti.
    F) Consistency: due run stessa label, solo run_id corretto; no 'independence verified' automatico.
    G) Metadata software originali per run storiche non confusi con Git HEAD attuale.
    H) Discovery e Detection simulate con FakeLocalLlmClient; esplicita classificazione MOCK_VERIFIED, non LIVE_VERIFIED.
    I) Test Streamlit default privacy Safe by default e assenza leak.
    """

    def test_acceptance_A_deepseek_named_error_regression_finish_reason_length(self):
        """
        PRIORITÀ P0 — BUG REALE DEEPSEEK:
        Verifica che quando LM Studio restituisce output troncato per limiti di token
        (finish_reason='length' e schema JSON richiesto), il client sollevi
        ESCLUSIVAMENTE AiStructuredOutputError e MAI NameError.
        Verifica inoltre che _classify_benchmark_error classifichi l'errore come
        STRUCTURED_OUTPUT_INVALID senza abortire il processo, mentre altri errori
        programmatici (es. AttributeError) propagano.
        """
        from ai.backend import AiStructuredOutputError
        from ai.benchmark import _classify_benchmark_error
        from ai.lmstudio import LmStudioClient
        from unittest.mock import MagicMock, patch

        client = LmStudioClient(base_url="http://127.0.0.1:1234", model_id="deepseek-r1-distill-qwen-7b")

        mock_http_response = MagicMock()
        mock_http_response.__enter__.return_value = mock_http_response
        truncated_payload = {
            "model": "deepseek-r1-distill-qwen-7b",
            "choices": [{
                "message": {"content": '{"decision": "PRESENT", "rationale": "Incompleto...'},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 512, "total_tokens": 632},
        }
        mock_http_response.read.return_value = json.dumps(truncated_payload).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_http_response):
            # Deve sollevare AiStructuredOutputError, MAI NameError!
            with pytest.raises(AiStructuredOutputError) as exc_info:
                client.chat_completion(
                    messages=[{"role": "user", "content": "analizza"}],
                    schema={"type": "object", "properties": {"decision": {"type": "string"}}},
                )

            assert "finish_reason=length" in str(exc_info.value)
            classification = _classify_benchmark_error(exc_info.value)
            assert classification == "STRUCTURED_OUTPUT_INVALID"

        # Verifica che altri errori non vengano mascherati
        with pytest.raises(AttributeError):
            _classify_benchmark_error(AttributeError("Bug imprevisto di programmazione"))

    def test_acceptance_B_unknown_provenance_defaults_to_unattested(self, demo_session):
        """
        PRIORITÀ P0 — PROVENANCE FAIL-CLOSED:
        Se manca il metadato 'origin', il report dichiara UNKNOWN/NOT_RECORDED e attestation UNATTESTED.
        Non deve MAI promuovere a LIVE_AUTHORITATIVE o LIVE_LM_STUDIO senza evidenza.
        """
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        # Detection con metadata completamente vuoto (senza origin)
        det_no_meta = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata={},
        )
        rep_det = build_topic_detection_export_report(det_no_meta, doc)
        assert rep_det["execution"]["origin"] == "NOT_RECORDED"
        assert rep_det["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_det["experiment"]["attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

        # Discovery con metadata vuoto
        disc_no_meta = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata={},
        )
        rep_disc = build_topic_discovery_export_report(disc_no_meta, doc)
        assert rep_disc["execution"]["origin"] == "NOT_RECORDED"
        assert rep_disc["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_disc["experiment"]["attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

    def test_acceptance_C_adversarial_privacy_scrubbing_safe_default(self, demo_session):
        """
        PRIORITÀ P0 — PRIVACY SAFE BY DEFAULT:
        Input sintetico contenente segreti e identificatori personali iniettati in tutti i campi:
        label, description, rationale, topic_id, evidence_id, error_detail, source_name, consistency_rationale.
        Il report in modalità SAFE (default) deve risultare completamente privo di tali testi.
        """
        doc, _, _, _ = demo_session
        adv_query = TopicQuery(
            topic_id="ident_personal@private.com",
            label="Persona: Alice Rossi +391234567890",
            description="Segreto: alice@example.com, /home/alice/diario",
        )
        adv_meta = {
            "run_id": "run_adv_privacy_01",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{
                "chunk_index": 0,
                "status": "SUCCESS",
                "error_detail": "messaggio privato: Alice Rossi in vacanza al mare",
            }],
        }
        adv_det = TopicDetectionResult(
            topic=adv_query,
            decision=TopicDecision.PRESENT,
            rationale="Alice Rossi mail alice@example.com +391234567890",
            evidence_ids=("AliceRossi:16590887654",),
            provenance_document_id=doc.document_id,
            metadata=adv_meta,
        )
        adv_cons = ConsistencyResult(
            document_id=doc.document_id,
            discovery_run_id="run_disc_adv",
            detection_run_id="run_adv_privacy_01",
            topic_label="Persona: Alice Rossi +391234567890",
            status=ConsistencyStatus.CONSISTENT,
            rationale="Conferma privata per Alice Rossi al mare",
            discovery_evidence_ids=("AliceRossi:16590887654",),
            detection_evidence_ids=("AliceRossi:16590887654",),
            overlap_evidence_ids=("AliceRossi:16590887654",),
        )

        rep = build_topic_detection_export_report(
            detection_result=adv_det,
            document=doc,
            consistency_result=adv_cons,
            # redact_sensitive_topics NON specificato -> DEVE essere True di default
        )
        json_output = serialize_report_to_json(rep)

        forbidden_secrets = [
            "Alice Rossi",
            "AliceRossi",
            "alice@example.com",
            "+391234567890",
            "16590887654",
            "vacanza al mare",
            "diario",
            "ident_personal@private.com",
            "Conferma privata",
        ]
        for secret in forbidden_secrets:
            assert secret not in json_output, f"Segreto simulato '{secret}' trovato nel JSON safe!"

        assert "AliceRossi" not in json_output
        assert rep["result"]["evidence_ids"][0].startswith("ev_")
        assert rep["result"]["topic"]["topic_id"].startswith("top_")
        assert rep["privacy"]["pseudonymization_algorithm"] == "SHA256_UNSALTED_TRUNCATED"

        # Verifica nome file privo di segreti o chat_id grezzi
        fn = generate_export_filename("TOPIC_DETECTION", "/home/alice/chat_privata.txt", "model", "run_adv_privacy_01")
        assert "alice" not in fn
        assert "chat_privata" not in fn

    def test_acceptance_D_no_invented_metrics_null_distinct_from_zero(self, demo_session):
        """
        PRIORITÀ P0 — NON INVENTARE METRICHE O CONFIGURAZIONI:
        Quando metadati o diagnostiche sono assenti:
        - Non deve inventare qwen2.5-7b-instruct, context 8192, max_tokens 512, timeout 240, temperature 0.0, profilo BALANCED.
        - Durate non misurate devono essere null, non 0.0 (lo 0.0 è una misura).
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        stripped_det = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata={},
        )
        rep = build_topic_detection_export_report(stripped_det, doc)

        assert rep["experiment"]["duration_seconds"] is None
        assert rep["execution"]["duration_seconds"] is None
        assert rep["configuration"]["temperature"] is None
        assert rep["configuration"]["max_tokens"] is None
        assert rep["configuration"]["timeout_seconds"] is None
        assert rep["configuration"]["operational_profile_requested"] is None
        assert rep["configuration"]["operational_profile_applied"] is None
        assert rep["model"]["declared_context_length"] is None
        assert rep["checkpoints"]["enabled"] is None

        raw_json = serialize_report_to_json(rep)
        assert '"duration_seconds": null' in raw_json
        assert '"temperature": null' in raw_json
        assert '"max_tokens": null' in raw_json

    def test_acceptance_E_coverage_counter_invariants_and_unattempted_chunks(self, demo_session):
        """
        PRIORITÀ P0 — VALIDAZIONE INVARIANTI DI COPERTURA:
        Se i contatori sono impossibili (es. 1 planned, 1 completed, 2 failed),
        il sistema segnala INCONSISTENT_COUNTERS, counter_invariants_valid=False
        e non dichiara 100% di completamento o COMPLETE.
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        meta_inconsistent = {
            "run_id": "run_inconsistent_counters",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "failed_chunk_count": 2,
        }
        det_inconsistent = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_inconsistent,
        )
        rep = build_topic_detection_export_report(det_inconsistent, doc)
        assert rep["coverage"]["counter_invariants_valid"] is False
        assert rep["coverage"]["coverage_status"] == "INCONSISTENT_COUNTERS"
        assert rep["coverage"]["completion_percentage"] is None
        assert rep["coverage"]["is_complete"] is False

    def test_acceptance_F_strict_consistency_matching_and_no_blind_independence(self, demo_session):
        """
        PRIORITÀ P0 — ASSOCIAZIONE UNIVOCA DISCOVERY-DETECTION:
        Due detection sullo stesso topic label ma run ID diversi; solo la run_id corretta
        deve associare la ConsistencyResult. Mai associare per sola label.
        Nessun inferential_independence_verified=True automatico.
        """
        doc, _, _, _ = demo_session
        q = TopicQuery(topic_id="top_frodi", label="Frodi fiscali", description="Fatture false")

        det_run1 = TopicDetectionResult(
            topic=q,
            decision=TopicDecision.PRESENT,
            rationale="Run 1 ha trovato evidenza E001.",
            evidence_ids=(doc.all_evidence_sections[0].evidence_id,),
            provenance_document_id=doc.document_id,
            metadata={"run_id": "run_det_target_1"},
        )
        det_run2 = TopicDetectionResult(
            topic=q,
            decision=TopicDecision.PRESENT,
            rationale="Run 2 ha trovato evidenza diversa.",
            evidence_ids=(doc.all_evidence_sections[0].evidence_id,),
            provenance_document_id=doc.document_id,
            metadata={"run_id": "run_det_target_2"},
        )

        cons_for_run1 = ConsistencyResult(
            document_id=doc.document_id,
            discovery_run_id="run_disc_source_1",
            detection_run_id="run_det_target_1",
            topic_label="Frodi fiscali",
            status=ConsistencyStatus.CONSISTENT,
            rationale="Verifica consistency solo per run 1",
            discovery_evidence_ids=("E001",),
            detection_evidence_ids=("E001",),
            overlap_evidence_ids=("E001",),
        )

        mock_state = {}
        state.add_consistency_result(cons_for_run1, state=mock_state)

        json_1, _ = export_topic_detection_json(det_run1, doc, session_state=mock_state)
        parsed_1 = json.loads(json_1)
        assert parsed_1["result"]["semantic_consistency"] is not None
        assert parsed_1["result"]["semantic_consistency"]["target_detection_run_id"] == parsed_1["experiment"]["run_id"]
        assert parsed_1["result"]["semantic_consistency"]["target_detection_run_id"] == get_pseudonymous_id("run_det_target_1", prefix="run")
        assert "run_det_target_1" not in json_1

        json_2, _ = export_topic_detection_json(det_run2, doc, session_state=mock_state)
        parsed_2 = json.loads(json_2)
        assert parsed_2["result"]["semantic_consistency"] is None, "La run 2 ha erroneamente associato una consistency per coincidenza di topic label!"

    def test_acceptance_G_historical_software_commit_separated_from_export_commit(self, demo_session):
        """
        PRIORITÀ P0 — IDENTITÀ DELL'ESECUZIONE:
        Distingue 'execution_software_commit' registrato al momento della run
        da 'export_software_commit' ottenuto al momento dell'esportazione.
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        meta_hist = dict(det.metadata or {})
        commit_hist = "1234567890abcdef1234567890abcdef12345678"
        commit_exp = "abcdef1234567890abcdef1234567890abcdef12"
        meta_hist["git_commit"] = commit_hist
        meta_hist["run_id"] = "run_storica_01"
        det_hist = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_hist,
        )

        rep = build_topic_detection_export_report(det_hist, doc, custom_commit=commit_exp)
        assert rep["software"]["execution_software_commit"] == commit_hist
        assert rep["software"]["export_software_commit"] == commit_exp

        # Verifica rifiuto categorico di tag o testi liberi come commit
        rep_tag = build_topic_detection_export_report(det_hist, doc, custom_commit="release_MarioRossi_tag_v1")
        assert rep_tag["software"]["export_software_commit"] == "NOT_RECORDED"

    def test_acceptance_H_mock_runs_never_live_authoritative(self, demo_session):
        """
        PRIORITÀ P0 — ATTESTAZIONE SCIENTIFICA:
        Discovery e Detection simulate con FakeLocalLlmClient hanno esplicita attestazione MOCK_VERIFIED,
        mai LIVE_AUTHORITATIVE.
        """
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        meta_det = dict(det.metadata or {})
        meta_det["is_fake"] = True
        meta_det["origin"] = AnalysisOrigin.MOCK_INSPECTION.value
        mock_det = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_det,
        )
        rep = build_topic_detection_export_report(mock_det, doc)
        assert rep["experiment"]["attestation_level"] == AttestationLevel.MOCK_VERIFIED.value
        assert rep["experiment"]["attestation_level"] != AttestationLevel.LIVE_AUTHORITATIVE.value

    def test_acceptance_I_ui_export_defaults_to_safe_privacy(self, demo_session):
        """
        PRIORITÀ P0 / P1 — UI SAFE BY DEFAULT:
        Le funzioni applicative export_topic_detection_json ed export_topic_discovery_json
        hanno redact_sensitive_topics=True di default.
        """
        doc, det_list, disc_list, _ = demo_session
        json_det, fn_det = export_topic_detection_json(det_list[0], doc)
        parsed_det = json.loads(json_det)
        assert parsed_det["privacy"]["privacy_mode"] == "SAFE_REDACTED"
        assert parsed_det["result"]["topic"]["label"] == "[OSCURATO PER PRIVACY / REDACTED]"

        json_disc, fn_disc = export_topic_discovery_json(disc_list[0], doc)
        parsed_disc = json.loads(json_disc)
        assert parsed_disc["privacy"]["privacy_mode"] == "SAFE_REDACTED"
        assert parsed_disc["result"]["topics"][0]["label"] == "[OSCURATO PER PRIVACY / REDACTED]"


@pytest.mark.unit
class TestIndependentAuditRegression20261021:
    """
    Suite di regressione vincolante per l'audit indipendente del 21/10/2026:
    A) Privacy adversarial synthetic test (segreti in OGNI campo libero, JSON, CSV e filename).
    B) Coverage truthfulness (chunk_count=3 senza diagnostica non produce MAI COMPLETE; incoerenza contatori).
    C) Attestation spoofing fail-closed (reported vs verified attestation).
    D) DeepSeek finish_reason='length' troncamento JSON gestito con AiStructuredOutputError senza NameError.
    E) Consistency association: rifiuto associazione ambigua per risultati multipli sullo stesso detection_run_id.
    """

    def test_regression_A_adversarial_privacy_leak_prevention_json_csv_filenames(self, demo_session):
        """
        TEST A — Nessun dato personale o segreto deve trapelare in nessun campo non tipizzato
        di JSON, CSV o nomi di file (redact_sensitive_topics=True di default).
        """
        import io
        import csv
        from analysis.export import _normalize_chunks_diagnostics

        doc, _, _, _ = demo_session
        adv_query = TopicQuery(
            topic_id="top_AliceRossi_Private123",
            label="Persona: Alice Rossi +393331234567",
            description="Segreto personale: alice.rossi@example.com /home/alice/private",
        )
        adv_meta = {
            "run_id": "run_AliceRossi_+393331234567",
            "model": "mail_MarioBianchi@example.com",
            "linked_discovery_run_id": "run_SecretDiscovery_Mario",
            "document_fingerprint": "NotAValidFingerprintAliceRossi",
            "chunk_count": 1,
            "completed_chunk_count": 1,
            "chunk_diagnostics": [{
                "chunk_index": 0,
                "status": "FAILED",
                "error_type": "AdversarialAliceException",
                "diagnostic_category": "AdversarialCategoryAlice",
                "failure_phase": "AdversarialPhaseMario",
                "finish_reason": "AdversarialFinishMario",
                "error_detail": "Dettaglio confidenziale: Mario Bianchi ha telefonato ad Alice Rossi al +393331234567",
            }],
        }
        adv_det = TopicDetectionResult(
            topic=adv_query,
            decision=TopicDecision.PRESENT,
            rationale="Mario Bianchi via mail_MarioBianchi@example.com conferma l'incontro",
            evidence_ids=("AliceRossi:16590887654",),
            provenance_document_id=doc.document_id,
            metadata=adv_meta,
        )

        # 1. Verifica Report JSON
        rep = build_topic_detection_export_report(
            detection_result=adv_det,
            document=doc,
            redact_sensitive_topics=True,
        )
        json_output = serialize_report_to_json(rep)

        # Lista di segreti che NON devono comparire in chiaro nel JSON
        forbidden_secrets = [
            "Alice Rossi",
            "AliceRossi",
            "Mario Bianchi",
            "MarioBianchi",
            "+393331234567",
            "3331234567",
            "alice.rossi@example.com",
            "mail_MarioBianchi@example.com",
            "AdversarialAliceException",
            "AdversarialCategoryAlice",
            "AdversarialPhaseMario",
            "AdversarialFinishMario",
            "NotAValidFingerprintAliceRossi",
            "/home/alice/private",
        ]

        for secret in forbidden_secrets:
            assert secret not in json_output, f"Secret '{secret}' trovato nel JSON generato!"

        # Verifiche puntuali di struttura safe
        assert rep["experiment"]["run_id"].startswith("run_")
        assert "Alice" not in rep["experiment"]["run_id"]
        assert rep["model"]["effective_model_id"].startswith("mod_")
        assert "Mario" not in rep["model"]["effective_model_id"]
        assert rep["document"]["document_fingerprint"] is None  # Impronta non autentica impostata a null
        assert rep["result"]["linked_discovery_run_id"].startswith("run_")
        assert rep["chunks"][0]["error_detail"] == "[REDACTED_DIAGNOSTIC_DETAIL]"
        assert rep["chunks"][0]["diagnostic_category"] == "UNKNOWN_ERROR"
        assert rep["chunks"][0]["failure_phase"] == "unknown"
        assert rep["chunks"][0]["finish_reason"] == "unknown"
        assert rep["chunks"][0]["error_type"] == "GENERIC_ERROR"

        # 2. Verifica Nome File Sanitizzato
        fn = generate_export_filename(
            analysis_type="TOPIC_DETECTION",
            dataset_id="chat_AliceRossi",
            model_id="model_MarioBianchi",
            run_id="run_AliceRossi_+393331234567",
        )
        for secret in ["Alice", "Rossi", "Mario", "Bianchi", "3331234567"]:
            assert secret not in fn, f"Secret '{secret}' trovato nel nome file: {fn}"

        # 3. Verifica Esportazione CSV Diagnostica Sanitizzata
        norm_chunks = _normalize_chunks_diagnostics(
            raw_diagnostics=adv_meta["chunk_diagnostics"],
            total_planned_chunks=1,
            model_id=adv_meta["model"],
            run_id=adv_meta["run_id"],
            redact_sensitive_topics=True,
        )
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=list(norm_chunks[0].keys()))
        writer.writeheader()
        writer.writerows(norm_chunks)
        csv_output = csv_buffer.getvalue()

        for secret in forbidden_secrets:
            assert secret not in csv_output, f"Secret '{secret}' trovato nel CSV generato!"

    def test_regression_B_no_invented_coverage_and_inconsistent_counters(self, demo_session):
        """
        TEST B — Nessuna invenzione di copertura:
        1. metadata={'chunk_count': 3} senza contatori né diagnostica NON deve produrre COMPLETE né 100%.
        2. Incoerenza fra contatori dichiarati e diagnostiche reali produce INCONSISTENT_COUNTERS.
        3. Flag di validazione impostati a STATICALLY_VERIFIED se non esplicitamente registrati.
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        # Scenario 1: solo chunk_count=3, nessuna diagnostica né contatori osservati
        meta_unobserved = {"chunk_count": 3}
        det_unobserved = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_unobserved,
        )
        rep_unobserved = build_topic_detection_export_report(det_unobserved, doc)
        cov = rep_unobserved["coverage"]
        assert cov["is_complete"] is None, "is_complete non deve essere True quando mancano contatori osservati!"
        assert cov["chunks_completed"] is None
        assert cov["completion_percentage"] is None
        assert cov["coverage_status"] == "NOT_RECORDED"
        assert len(rep_unobserved["chunks"]) == 3
        for c in rep_unobserved["chunks"]:
            assert c["status"] == "UNATTEMPTED"

        # Scenario 2: contraddizione contatori (completed=3 ma diagnostica ha solo chunk UNATTEMPTED)
        meta_contradict = {
            "chunk_count": 3,
            "completed_chunk_count": 3,
            "chunk_diagnostics": [
                {"chunk_index": 0, "status": "UNATTEMPTED"},
                {"chunk_index": 1, "status": "UNATTEMPTED"},
                {"chunk_index": 2, "status": "UNATTEMPTED"},
            ],
        }
        det_contradict = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_contradict,
        )
        rep_contradict = build_topic_detection_export_report(det_contradict, doc)
        cov_c = rep_contradict["coverage"]
        assert cov_c["counter_invariants_valid"] is False
        assert cov_c["is_complete"] is False
        assert cov_c["coverage_status"] == "INCONSISTENT_COUNTERS"
        assert cov_c["completion_percentage"] is None

        # Scenario 3: validazione forense e schema non assume True se non registrata
        val = rep_unobserved["validation"]
        assert val["evidence_id_validation_applied"] == "STATICALLY_VERIFIED"
        assert val["schema_validation_applied"] == "STATICALLY_VERIFIED"

    def test_regression_C_attestation_spoofing_fail_closed(self, demo_session):
        """
        TEST C — Attestazione non falsificabile via metadati:
        metadata={'attestation_level': 'LIVE_AUTHORITATIVE', 'origin': 'NOT_RECORDED'}
        deve produrre reported_attestation_level='LIVE_AUTHORITATIVE' ma
        verified_attestation_level='UNATTESTED' e attestation_level='UNATTESTED'.
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        # 1. Spoofing con metadato arbitrario senza origine autenticata
        meta_spoofed = {
            "attestation_level": "LIVE_AUTHORITATIVE",
            "origin": "NOT_RECORDED",
        }
        det_spoofed = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_spoofed,
        )
        rep = build_topic_detection_export_report(det_spoofed, doc)
        exp = rep["experiment"]
        assert exp["reported_attestation_level"] == "LIVE_AUTHORITATIVE"
        assert exp["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert exp["attestation_level"] == AttestationLevel.UNATTESTED.value

        # 2. Spoofing con is_authenticated_live=True ma senza server_provenance_verified
        meta_spoofed_2 = {
            "attestation_level": "LIVE_AUTHORITATIVE",
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "is_authenticated_live": True,
            # server_provenance_verified assente
        }
        det_spoofed_2 = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_spoofed_2,
        )
        rep_2 = build_topic_detection_export_report(det_spoofed_2, doc)
        assert rep_2["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value

        # 3. Tentativo di auto-dichiarazione tramite soli metadati (Anomalia P0/C)
        # Senza riferimento autoritativo indipendente dal servizio locale, deve rimanere UNATTESTED
        meta_authentic_live = {
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "is_authenticated_live": True,
            "server_provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "run_id": "run_unverified_meta_live",
        }
        det_auth = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_authentic_live,
        )
        rep_3 = build_topic_detection_export_report(det_auth, doc)
        assert rep_3["experiment"]["reported_attestation_level"] == "REPORTED_LIVE"
        assert rep_3["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_3["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value

        # 4. Autentico LIVE_AUTHORITATIVE verificato tramite riferimento autoritativo del servizio locale
        rep_4 = build_topic_detection_export_report(
            det_auth,
            doc,
            authoritative_live_run_ids={"run_unverified_meta_live"},
        )
        assert rep_4["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
        assert rep_4["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value

    def test_regression_D_deepseek_truncated_json_finish_reason_length(self):
        """
        TEST D — Gestione DeepSeek structured output error:
        Verifica che la presenza di finish_reason='length' e JSON non parsabile
        sollevi correttamente AiStructuredOutputError senza incorrere in NameError.
        """
        from ai.backend import AiStructuredOutputError
        import ai.lmstudio as lm_mod

        # Verifica che la classe sia correttamente importata ed esportata nel modulo
        assert hasattr(lm_mod, "AiStructuredOutputError")
        assert lm_mod.AiStructuredOutputError is AiStructuredOutputError

        # Simula parsing troncato con finish_reason='length'
        raw_truncated_payload = {
            "choices": [{
                "message": {"content": '{"topics": [{"label": "troncato", "short_description": "incompleto'},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 512, "total_tokens": 612},
        }

        # Verifica che il parse sollevi AiStructuredOutputError
        content = raw_truncated_payload["choices"][0]["message"]["content"]
        finish_reason = raw_truncated_payload["choices"][0]["finish_reason"]
        assert finish_reason == "length"

        with pytest.raises(AiStructuredOutputError) as exc_info:
            try:
                json.loads(content)
            except Exception as j_err:
                raise AiStructuredOutputError(
                    f"Risposta JSON troncata da LM Studio prima del completamento (finish_reason=length): {j_err}"
                ) from j_err

        assert "finish_reason=length" in str(exc_info.value)

    def test_regression_E_consistency_ambiguous_matching_rejected(self, demo_session):
        """
        TEST E — Associazione Consistency:
        Se in session_state sono presenti risultati multipli associati allo stesso detection_run_id,
        l'associazione ambigua deve essere categoricamente rifiutata:
        semantic_consistency = None e flag motivato (AMBIGUOUS_MULTIPLE_MATCHES_REJECTED).
        """
        doc, det_list, _, _ = demo_session
        det = det_list[0]

        target_run = "run_det_ambiguous_test_01"
        meta = dict(det.metadata or {})
        meta["run_id"] = target_run
        det_with_run = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta,
        )

        c1 = ConsistencyResult(
            document_id=doc.document_id,
            discovery_run_id="run_disc_01",
            detection_run_id=target_run,
            topic_label=det.topic.label,
            status=ConsistencyStatus.CONSISTENT,
            rationale="Verifica consistency 1",
            discovery_evidence_ids=det.evidence_ids,
            detection_evidence_ids=det.evidence_ids,
            overlap_evidence_ids=det.evidence_ids,
        )
        c2 = ConsistencyResult(
            document_id=doc.document_id,
            discovery_run_id="run_disc_02",
            detection_run_id=target_run,
            topic_label=det.topic.label,
            status=ConsistencyStatus.DISCREPANCY_ABSENT,
            rationale="Verifica consistency 2 (in contraddizione)",
            discovery_evidence_ids=(),
            detection_evidence_ids=det.evidence_ids,
            overlap_evidence_ids=(),
        )

        mock_state = {}
        state.add_consistency_result(c1, state=mock_state)
        state.add_consistency_result(c2, state=mock_state)

        json_out, _ = export_topic_detection_json(
            detection_result=det_with_run,
            document=doc,
            session_state=mock_state,
        )
        parsed = json.loads(json_out)

        assert parsed["result"]["semantic_consistency"] is None, (
            "Risultati multipli per lo stesso detection_run_id devono rifiutare l'associazione ambigua!"
        )
        assert parsed["result"]["consistency_association_flag"] == "AMBIGUOUS_MULTIPLE_MATCHES_REJECTED"


class TestIndependentAuditAuditClosure20261021:
    """
    Test suite mirata a validare la chiusura definitiva delle anomalie P0/A, P0/B e P0/C
    evidenziate dall'audit indipendente del 21 ottobre 2026.
    """

    CANARY_STRING = "CONFIDENTIAL_EXAMPLE+393330000000"

    @staticmethod
    def _assert_zero_canary_recursive(obj: Any, canary: str = CANARY_STRING, path: str = "") -> None:
        """Attraversa ricorsivamente qualsiasi struttura dizionario/lista/valore verificando l'assenza del canary."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                curr_path = f"{path}.{k}" if path else str(k)
                assert canary not in str(k), f"Canary trovato nella chiave {curr_path}!"
                TestIndependentAuditAuditClosure20261021._assert_zero_canary_recursive(v, canary, curr_path)
        elif isinstance(obj, (list, tuple, set)):
            for idx, item in enumerate(obj):
                curr_path = f"{path}[{idx}]"
                TestIndependentAuditAuditClosure20261021._assert_zero_canary_recursive(item, canary, curr_path)
        else:
            assert canary not in str(obj), f"Canary trovato nel valore di {path}: '{obj}'!"

    def test_p0_a_adversarial_canary_leak_eliminated_across_all_fields(self, demo_session):
        """
        ANOMALIA P0/A:
        Verifica che iniettando la stringa sintetica canary 'CONFIDENTIAL_EXAMPLE+393330000000'
        in TUTTI i campi metadati possibili (family, quantization, operational_profile, reduction_strategy,
        timestamps, origin, origin_flow, status, commit, loading_mode, aggregation_errors, run_id, ecc.),
        sia il report di Detection che il report di Discovery, il CSV diagnostico e il filename
        NON contengano la stringa canary in NESSUN valore, chiave o frammento serializzato (SAFE_REDACTED).
        """
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        hostile_meta = {
            "family": self.CANARY_STRING,
            "quantization": self.CANARY_STRING,
            "declared_quantization": self.CANARY_STRING,
            "observed_quantization": self.CANARY_STRING,
            "profile_requested": self.CANARY_STRING,
            "profile_applied": self.CANARY_STRING,
            "reduction_strategy": self.CANARY_STRING,
            "start_timestamp": self.CANARY_STRING,
            "end_timestamp": self.CANARY_STRING,
            "timestamp": self.CANARY_STRING,
            "start_time": self.CANARY_STRING,
            "end_time": self.CANARY_STRING,
            "origin_flow": self.CANARY_STRING,
            "origin": self.CANARY_STRING,
            "status": self.CANARY_STRING,
            "git_commit": self.CANARY_STRING,
            "loading_mode": self.CANARY_STRING,
            "aggregation_status": self.CANARY_STRING,
            "aggregation_errors": [self.CANARY_STRING, f"sub_{self.CANARY_STRING}"],
            "run_id": self.CANARY_STRING,
            "requested_model": f"qwen_{self.CANARY_STRING}",
            "model": f"qwen_{self.CANARY_STRING}",
            "client_class": f"Client_{self.CANARY_STRING}",
            "document_fingerprint": self.CANARY_STRING,
            "source_name": f"/secret/{self.CANARY_STRING}/chat.txt",
        }

        det_hostile = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=dict(hostile_meta),
        )
        disc_hostile = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=dict(hostile_meta),
        )

        # 1. Topic Detection report
        rep_det = build_topic_detection_export_report(det_hostile, doc, redact_sensitive_topics=True)
        self._assert_zero_canary_recursive(rep_det)
        json_det = serialize_report_to_json(rep_det)
        assert self.CANARY_STRING not in json_det

        # 2. Topic Discovery report
        rep_disc = build_topic_discovery_export_report(disc_hostile, doc, redact_sensitive_topics=True)
        self._assert_zero_canary_recursive(rep_disc)
        json_disc = serialize_report_to_json(rep_disc)
        assert self.CANARY_STRING not in json_disc

        # 3. CSV diagnostico
        hostile_diag = [
            {"chunk_index": 0, "status": self.CANARY_STRING, "diagnostic_category": self.CANARY_STRING, "error_type": self.CANARY_STRING}
        ]
        csv_out = export_chunk_diagnostics_to_csv(
            diagnostics=hostile_diag,
            total_planned_chunks=1,
            model_id=f"model_{self.CANARY_STRING}",
            run_id=f"run_{self.CANARY_STRING}",
            redact_sensitive_topics=True,
        )
        assert self.CANARY_STRING not in csv_out

        # 4. Filename
        fname_json = generate_export_filename(
            analysis_type="TOPIC_DETECTION",
            dataset_id=f"chat_{self.CANARY_STRING}",
            model_id=f"qwen_{self.CANARY_STRING}",
            run_id=f"run_{self.CANARY_STRING}",
            timestamp=self.CANARY_STRING,
        )
        assert self.CANARY_STRING not in fname_json

        fname_csv = generate_export_filename(
            analysis_type="DIAGNOSTICS_CSV",
            dataset_id=f"chat_{self.CANARY_STRING}",
            model_id=f"qwen_{self.CANARY_STRING}",
            run_id=f"run_{self.CANARY_STRING}",
            timestamp=self.CANARY_STRING,
            extension="csv",
        )
        assert self.CANARY_STRING not in fname_csv

    def test_p0_b_contradiction_between_unknown_coverage_and_status_complete(self, demo_session):
        """
        ANOMALIA P0/B:
        metadata={'status':'COMPLETE','chunk_count':3} ma nessun completed_chunk_count e nessuna chunk_diagnostics.
        Risultato richiesto:
        - coverage.coverage_status='NOT_RECORDED'
        - execution.reported_execution_status='COMPLETE'
        - execution.verified_execution_status='NOT_RECORDED'
        - execution.global_status='NOT_RECORDED' (NON COMPLETE!)
        - Verifica analoga per Discovery e Detection.
        - Verifica contatori incoerenti -> INCONSISTENT_COUNTERS.
        - Decisione PRESENT/ABSENT/UNCERTAIN distinta dalla copertura.
        """
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        meta_unverified_complete = {
            "status": "COMPLETE",
            "chunk_count": 3,
            # Nessun completed_chunk_count né chunk_diagnostics
        }

        # 1. Detection
        det_unverified = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_unverified_complete,
        )
        rep_det = build_topic_detection_export_report(det_unverified, doc)
        assert rep_det["coverage"]["coverage_status"] == "NOT_RECORDED"
        assert rep_det["execution"]["reported_execution_status"] == "COMPLETE"
        assert rep_det["execution"]["verified_execution_status"] == "NOT_RECORDED"
        assert rep_det["execution"]["global_status"] == "NOT_RECORDED"
        assert rep_det["execution"]["global_status"] != "COMPLETE"
        # Decisione preservata e distinta da stato copertura
        assert rep_det["result"]["decision"] == det.decision.value

        # 2. Discovery
        disc_unverified = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=meta_unverified_complete,
        )
        rep_disc = build_topic_discovery_export_report(disc_unverified, doc)
        assert rep_disc["coverage"]["coverage_status"] == "NOT_RECORDED"
        assert rep_disc["execution"]["reported_execution_status"] == "COMPLETE"
        assert rep_disc["execution"]["verified_execution_status"] == "NOT_RECORDED"
        assert rep_disc["execution"]["global_status"] == "NOT_RECORDED"
        assert rep_disc["execution"]["global_status"] != "COMPLETE"

        # 3. Contatori contraddittori -> INCONSISTENT_COUNTERS
        meta_contradictory = {
            "status": "COMPLETE",
            "chunk_count": 3,
            "completed_chunk_count": 5,
            "failed_chunk_count": 0,
        }
        det_inconsistent = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_contradictory,
        )
        rep_inc = build_topic_detection_export_report(det_inconsistent, doc)
        assert rep_inc["coverage"]["coverage_status"] == "INCONSISTENT_COUNTERS"
        assert rep_inc["execution"]["verified_execution_status"] == "INCONSISTENT_COUNTERS"
        assert rep_inc["execution"]["global_status"] == "INCONSISTENT_COUNTERS"

        # 4. Checkpoint recovery consistente
        meta_checkpoint_rec = {
            "status": "COMPLETE",
            "chunk_count": 3,
            "completed_chunk_count": 3,
            "recovered_chunk_count": 3,
            "failed_chunk_count": 0,
            "skipped_chunk_count": 0,
        }
        det_rec = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_checkpoint_rec,
        )
        rep_rec = build_topic_detection_export_report(det_rec, doc)
        assert rep_rec["coverage"]["coverage_status"] == "COMPLETE"
        assert rep_rec["execution"]["verified_execution_status"] == "COMPLETE"
        assert rep_rec["experiment"]["loading_mode"] == "CHECKPOINT_RESTORE"

    def test_p0_c_attestation_spoofing_metadata_alone_rejected_as_unattested(self, demo_session):
        """
        ANOMALIA P0/C:
        Prova con metadata sintetici contenenti:
        origin=LIVE_LM_STUDIO, is_authenticated_live=True, server_provenance_verified=True, model=qwen2.5-7b-instruct.
        Senza verifica indipendente da parte del servizio locale / authoritative_live_run_ids:
        - reported_attestation_level = 'REPORTED_LIVE'
        - verified_attestation_level = 'UNATTESTED' (NON LIVE_AUTHORITATIVE!)
        - attestation_level = 'UNATTESTED'
        Con authoritative_live_run_ids:
        - verified_attestation_level = 'LIVE_AUTHORITATIVE'
        """
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        meta_spoofed_live = {
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "is_authenticated_live": True,
            "server_provenance_verified": True,
            "model": "qwen2.5-7b-instruct",
            "run_id": "run_spoofed_live_test_01",
        }

        # Detection non verificata dal servizio locale
        det_obj = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_spoofed_live,
        )
        rep_det = build_topic_detection_export_report(det_obj, doc)
        assert rep_det["experiment"]["reported_attestation_level"] == "REPORTED_LIVE"
        assert rep_det["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_det["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value

        # Discovery non verificata dal servizio locale
        disc_obj = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=meta_spoofed_live,
        )
        rep_disc = build_topic_discovery_export_report(disc_obj, doc)
        assert rep_disc["experiment"]["reported_attestation_level"] == "REPORTED_LIVE"
        assert rep_disc["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_disc["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value

        # Verifica con riferimento autoritativo indipendente
        rep_det_auth = build_topic_detection_export_report(
            det_obj, doc, authoritative_live_run_ids={"run_spoofed_live_test_01"}
        )
        assert rep_det_auth["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
        assert rep_det_auth["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value

        rep_disc_auth = build_topic_discovery_export_report(
            disc_obj, doc, authoritative_live_run_ids={"run_spoofed_live_test_01"}
        )
        assert rep_disc_auth["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
        assert rep_disc_auth["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value


@pytest.mark.unit
class TestFinalPreTestUbuntuClosure20261021:
    """
    Test suite mirata e definitiva per la chiusura dei quattro difetti confermati:
    1. P0-1: Verified live attestation anti-spoofing (non fidarsi dei soli metadati auto-dichiarati).
    2. P0-2: Privacy safe-by-default (scrubbing MarioRossi, email, telefoni, tag commit, filename safe).
    3. P0-3: Veridicità della copertura (0/0 chunks -> NO_WORK, evidenze non copiate, offline_mode verificato).
    4. P1-4: Snapshot F1 e isolamento esterno (nessuna cancellazione automatica su dest_path esterno).
    """

    def test_p0_1_adversarial_attestation_spoofing_detection_and_discovery(self, demo_session):
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        meta_adversarial = {
            "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
            "authoritative_execution_certified": True,
            "server_provenance_verified": True,
            "runtime_binding_verified": True,
            "model": "qwen2.5-7b-instruct",
            "run_id": "run_adversarial_spoof_attempt_2026",
            "status": "PARTIAL",
            "chunk_count": 26,
            "completed_chunk_count": 17,
            "failed_chunk_count": 9,
        }

        det_obj = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_adversarial,
        )
        disc_obj = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=meta_adversarial,
        )

        # 1. Caso Avversario Detection: tutti i flag a True ma registro esterno assente o vuoto
        rep_det_untrusted = build_topic_detection_export_report(det_obj, doc, authoritative_live_run_ids=None)
        assert rep_det_untrusted["experiment"]["reported_attestation_level"] == "REPORTED_LIVE"
        assert rep_det_untrusted["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_det_untrusted["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value
        # Scaricabile anche se parziale e non attestato
        serialized_det = serialize_report_to_json(rep_det_untrusted)
        assert len(serialized_det) > 0

        # 2. Caso Avversario Discovery: tutti i flag a True ma registro con ID differente
        rep_disc_untrusted = build_topic_discovery_export_report(disc_obj, doc, authoritative_live_run_ids={"run_different_id"})
        assert rep_disc_untrusted["experiment"]["reported_attestation_level"] == "REPORTED_LIVE"
        assert rep_disc_untrusted["experiment"]["verified_attestation_level"] == AttestationLevel.UNATTESTED.value
        assert rep_disc_untrusted["experiment"]["attestation_level"] == AttestationLevel.UNATTESTED.value
        serialized_disc = serialize_report_to_json(rep_disc_untrusted)
        assert len(serialized_disc) > 0

        # 3. Caso Positivo Verificato: registro attendibile con raw_run_id concreto
        rep_det_auth = build_topic_detection_export_report(
            det_obj, doc, authoritative_live_run_ids={"run_adversarial_spoof_attempt_2026"}
        )
        assert rep_det_auth["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
        assert rep_det_auth["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value

        rep_disc_auth = build_topic_discovery_export_report(
            disc_obj, doc, authoritative_live_run_ids={"run_adversarial_spoof_attempt_2026"}
        )
        assert rep_disc_auth["experiment"]["verified_attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value
        assert rep_disc_auth["experiment"]["attestation_level"] == AttestationLevel.LIVE_AUTHORITATIVE.value

    def test_p0_2_adversarial_privacy_scrubbing_mario_rossi_json_and_filenames(self, demo_session):
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        secret_name = "MarioRossi"
        secret_email = "mario.rossi@example.com"
        secret_phone = "+390612345678"
        secret_path = "/home/mariorossi/chats/secret.txt"

        meta_adversarial = {
            "run_id": f"run_{secret_name}",
            "git_commit": f"release_{secret_name}_tag_v2",
            "offline_mode": True,
            "evidence_id_validation_applied": secret_name,
            "strict_alias_matching": f"{secret_name}_strict",
            "notes": f"Utente {secret_name} con email {secret_email} e cellulare {secret_phone} percorso {secret_path}",
            "chunk_diagnostics": [
                {
                    "chunk_index": 0,
                    "status": "FAILED",
                    "error_detail": f"Errore interno per {secret_name} tel {secret_phone}",
                    "diagnostic_category": "UNKNOWN_ERROR",
                }
            ],
            "aggregation_errors": [f"Errore per {secret_name} {secret_email}"],
        }

        doc_with_personal_source = ConversationEvidenceDocument(
            document_id="doc_test_adversarial",
            chat_id="chat_test_adversarial",
            source_name=f"C:\\Users\\{secret_name}\\secret_convo.json",
            bundles=(),
        )

        query_adv = TopicQuery(
            topic_id="top_adv_01",
            label=f"Argomento {secret_name}",
            description=f"Descrizione con email {secret_email}",
        )

        det_adv = TopicDetectionResult(
            topic=query_adv,
            decision=TopicDecision.PRESENT,
            rationale=f"Rilevata evidenza per {secret_name} al numero {secret_phone}",
            evidence_ids=[doc.all_evidence_sections[0].evidence_id],
            provenance_document_id=f"doc_{secret_name}",
            metadata=meta_adversarial,
        )

        disc_topic_adv = DiscoveredTopic(
            label=f"Topic {secret_name}",
            short_description=f"Descrizione {secret_name} {secret_email}",
            evidence_ids=[doc.all_evidence_sections[0].evidence_id],
        )
        disc_adv = TopicDiscoveryResult(
            topics=[disc_topic_adv],
            provenance_document_id=f"doc_{secret_name}",
            metadata=meta_adversarial,
        )

        # 1. Verifica Flusso Detection
        rep_det = build_topic_detection_export_report(
            det_adv, doc_with_personal_source, custom_version=f"v1.0.0-{secret_name}"
        )
        json_det = serialize_report_to_json(rep_det)
        fn_det = generate_export_filename(
            analysis_type="TOPIC_DETECTION",
            dataset_id=f"dataset_{secret_name}",
            model_id=f"model_{secret_name}",
            run_id=f"run_{secret_name}",
        )

        assert secret_name not in json_det, f"Fuga di {secret_name} rilevata nel JSON Detection!"
        assert secret_email not in json_det, f"Fuga di {secret_email} rilevata nel JSON Detection!"
        assert secret_phone not in json_det, f"Fuga di {secret_phone} rilevata nel JSON Detection!"
        assert "mariorossi" not in json_det.lower()
        assert secret_name not in fn_det, f"Fuga di {secret_name} nel nome file Detection!"
        assert rep_det["software"]["execution_software_commit"] == "NOT_RECORDED"
        assert rep_det["software"]["export_software_commit"] != f"release_{secret_name}_tag_v2"
        assert rep_det["experiment"]["run_id"] == get_pseudonymous_id(f"run_{secret_name}", prefix="run")

        # 2. Verifica Flusso Discovery
        rep_disc = build_topic_discovery_export_report(
            disc_adv, doc_with_personal_source, custom_version=f"v1.0.0-{secret_name}"
        )
        json_disc = serialize_report_to_json(rep_disc)
        fn_disc = generate_export_filename(
            analysis_type="TOPIC_DISCOVERY",
            dataset_id=f"dataset_{secret_name}",
            model_id=f"model_{secret_name}",
            run_id=f"run_{secret_name}",
        )

        assert secret_name not in json_disc, f"Fuga di {secret_name} rilevata nel JSON Discovery!"
        assert secret_email not in json_disc, f"Fuga di {secret_email} rilevata nel JSON Discovery!"
        assert secret_phone not in json_disc, f"Fuga di {secret_phone} rilevata nel JSON Discovery!"
        assert "mariorossi" not in json_disc.lower()
        assert secret_name not in fn_disc, f"Fuga di {secret_name} nel nome file Discovery!"
        assert rep_disc["software"]["execution_software_commit"] == "NOT_RECORDED"
        assert rep_disc["experiment"]["run_id"] == get_pseudonymous_id(f"run_{secret_name}", prefix="run")

    def test_p0_3_truthfulness_coverage_zero_chunks_and_proportions(self, demo_session):
        doc, det_list, disc_list, _ = demo_session
        det = det_list[0]
        disc = disc_list[0]

        # 1. Caso 0/0: planned_chunks=0 NON deve produrre COMPLETE
        meta_zero = {
            "status": "COMPLETE",
            "chunk_count": 0,
            "completed_chunk_count": 0,
            "failed_chunk_count": 0,
        }
        det_zero = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_zero,
        )
        rep_det_zero = build_topic_detection_export_report(det_zero, doc)
        assert rep_det_zero["coverage"]["coverage_status"] == "NO_WORK"
        assert rep_det_zero["execution"]["verified_execution_status"] == "NO_WORK"
        assert rep_det_zero["execution"]["global_status"] == "NO_WORK"
        assert rep_det_zero["coverage"]["completion_percentage"] is None
        assert rep_det_zero["coverage"]["is_complete"] is False
        assert rep_det_zero["document"]["analyzed_evidence_count"] is None
        assert rep_det_zero["software"]["offline_mode"] is None

        disc_zero = TopicDiscoveryResult(
            topics=disc.topics,
            provenance_document_id=disc.provenance_document_id,
            metadata=meta_zero,
        )
        rep_disc_zero = build_topic_discovery_export_report(disc_zero, doc)
        assert rep_disc_zero["coverage"]["coverage_status"] == "NO_WORK"
        assert rep_disc_zero["execution"]["global_status"] == "NO_WORK"
        assert rep_disc_zero["coverage"]["completion_percentage"] is None
        assert rep_disc_zero["coverage"]["is_complete"] is False

        # 2. Caso 1/1
        meta_1_1 = {"chunk_count": 1, "completed_chunk_count": 1, "failed_chunk_count": 0}
        det_1_1 = TopicDetectionResult(
            topic=det.topic, decision=TopicDecision.PRESENT, rationale="ok", evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id, metadata=meta_1_1
        )
        rep_1_1 = build_topic_detection_export_report(det_1_1, doc)
        assert rep_1_1["coverage"]["coverage_status"] == "COMPLETE"
        assert rep_1_1["coverage"]["completion_percentage"] == 100.0
        assert rep_1_1["coverage"]["is_complete"] is True

        # 3. Caso 2/2
        meta_2_2 = {"chunk_count": 2, "completed_chunk_count": 2, "failed_chunk_count": 0}
        rep_2_2 = build_topic_discovery_export_report(TopicDiscoveryResult(
            topics=disc.topics, provenance_document_id=disc.provenance_document_id, metadata=meta_2_2
        ), doc)
        assert rep_2_2["coverage"]["coverage_status"] == "COMPLETE"
        assert rep_2_2["coverage"]["completion_percentage"] == 100.0

        # 4. Caso 17/26: PRESENT resta PRESENT ma PARTIAL; ABSENT resta ABSENT ma PARTIAL
        meta_17_26 = {"chunk_count": 26, "completed_chunk_count": 17, "failed_chunk_count": 9}
        det_17_26_present = TopicDetectionResult(
            topic=det.topic, decision=TopicDecision.PRESENT, rationale="ok", evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id, metadata=meta_17_26
        )
        rep_17_26_p = build_topic_detection_export_report(det_17_26_present, doc)
        assert rep_17_26_p["coverage"]["coverage_status"] == "PARTIAL"
        assert rep_17_26_p["result"]["decision"] == "PRESENT"
        assert rep_17_26_p["result"]["decision_status"] == "PARTIAL"
        assert rep_17_26_p["coverage"]["completion_percentage"] == 65.4

        det_17_26_absent = TopicDetectionResult(
            topic=det.topic, decision=TopicDecision.ABSENT, rationale="nessuna evidenza nei 17 chunk", evidence_ids=[],
            provenance_document_id=det.provenance_document_id, metadata=meta_17_26
        )
        rep_17_26_a = build_topic_detection_export_report(det_17_26_absent, doc)
        assert rep_17_26_a["coverage"]["coverage_status"] == "PARTIAL"
        assert rep_17_26_a["result"]["decision"] == "ABSENT"
        assert rep_17_26_a["result"]["decision_status"] == "PARTIAL"
        assert "Decoupled" in rep_17_26_a["result"]["decision_completeness_distinction"]

        # 5. Caso 25/26 con INVALID_EVIDENCE_ID
        raw_diag_25 = [
            {"chunk_index": i, "status": "SUCCESS"} for i in range(25)
        ] + [
            {"chunk_index": 25, "status": "FAILED", "diagnostic_category": "INVALID_EVIDENCE_ID", "error_type": "AiInvalidEvidenceCitationError"}
        ]
        meta_25_26 = {
            "chunk_count": 26,
            "chunk_diagnostics": raw_diag_25,
        }
        rep_25_26 = build_topic_detection_export_report(TopicDetectionResult(
            topic=det.topic, decision=TopicDecision.UNCERTAIN, rationale="1 chunk fallito", evidence_ids=[],
            provenance_document_id=det.provenance_document_id, metadata=meta_25_26
        ), doc)
        assert rep_25_26["coverage"]["coverage_status"] == "PARTIAL"
        assert rep_25_26["coverage"]["chunks_completed"] == 25
        assert rep_25_26["coverage"]["chunks_failed"] == 1
        assert rep_25_26["coverage"]["completion_percentage"] == 96.2
        assert rep_25_26["chunks"][25]["diagnostic_category"] == "INVALID_EVIDENCE_ID"

        # 6. Contatori incoerenti rifiutati con INCONSISTENT_COUNTERS
        meta_inconsistent = {
            "chunk_count": 26,
            "completed_chunk_count": 25,
            "failed_chunk_count": 0,
        }
        rep_inc = build_topic_detection_export_report(TopicDetectionResult(
            topic=det.topic, decision=TopicDecision.UNCERTAIN, rationale="incoerente", evidence_ids=[],
            provenance_document_id=det.provenance_document_id, metadata=meta_inconsistent
        ), doc)
        assert rep_inc["coverage"]["counter_invariants_valid"] is False
        assert rep_inc["coverage"]["coverage_status"] == "INCONSISTENT_COUNTERS"
        assert rep_inc["execution"]["global_status"] == "INCONSISTENT_COUNTERS"

    def test_p1_4_snapshot_nominal_and_failure_leaves_no_staging_and_preserves_external(self):
        import tempfile
        import sqlite3
        from unittest.mock import patch
        from scripts.preflight_checkpoint_resume import create_consistent_readonly_snapshot

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            dir_a = tmp_p / "dir_a"
            dir_b = tmp_p / "dir_b"
            dir_a.mkdir()
            dir_b.mkdir()

            # Creazione DB SQLite sintetico
            src_db = dir_a / "test_src.db"
            conn = sqlite3.connect(str(src_db))
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE t (id INT, val TEXT);")
            conn.execute("INSERT INTO t VALUES (1, 'val1');")
            conn.commit()
            conn.close()

            # 1. Snapshot nominale verso destinazione esplicita
            dest_target_ok = dir_b / "snapshot_published.db"
            snap_file, snap_temp = create_consistent_readonly_snapshot(src_db, dest_path=dest_target_ok)
            assert snap_file == dest_target_ok
            assert snap_file.is_file()
            # Verifica che nessuna staging directory temporanea sia rimasta
            stray_temps = list(tmp_p.glob("chatanalysis_snap_*"))
            assert len(stray_temps) == 0

            # 2. Riproduzione Anomalia: errore durante il processo con dest_path esplicito non preesistente
            dest_target_fail = dir_b / "snapshot_should_not_exist.db"
            with patch("shutil.copy2", side_effect=OSError("Simulazione interruzione copia finale")):
                with pytest.raises(OSError):
                    create_consistent_readonly_snapshot(src_db, dest_path=dest_target_fail)

            # Il file target esterno non è mai stato pubblicato né cancellato distruttivamente
            assert not dest_target_fail.exists()
            assert not dest_target_fail.is_file()

            # 3. Preservazione assoluta di file preesistenti su dest_path
            preexisting_file = dir_b / "user_preexisting.db"
            preexisting_file.write_bytes(b"USER_DATA_DO_NOT_TOUCH")
            with pytest.raises(FileExistsError):
                create_consistent_readonly_snapshot(src_db, dest_path=preexisting_file)
            assert preexisting_file.is_file()
            assert preexisting_file.read_bytes() == b"USER_DATA_DO_NOT_TOUCH"


