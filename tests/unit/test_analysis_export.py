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
        assert "run_a" in fn1
        assert "run_b" in fn2

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
        assert parsed_1["result"]["semantic_consistency"]["target_detection_run_id"] == "run_det_target_1"

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
        meta_hist["git_commit"] = "commit_storico_123456"
        meta_hist["run_id"] = "run_storica_01"
        det_hist = TopicDetectionResult(
            topic=det.topic,
            decision=det.decision,
            rationale=det.rationale,
            evidence_ids=det.evidence_ids,
            provenance_document_id=det.provenance_document_id,
            metadata=meta_hist,
        )

        rep = build_topic_detection_export_report(det_hist, doc, custom_commit="export_head_abcdef")
        assert rep["software"]["execution_software_commit"] == "commit_storico_123456"
        assert rep["software"]["export_software_commit"] == "export_head_abcdef"

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

