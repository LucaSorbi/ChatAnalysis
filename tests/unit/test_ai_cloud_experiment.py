"""
tests/unit/test_ai_cloud_experiment.py
---------------------------------------
Test unitari e di integrazione offline per il runner sperimentale cloud E2E
(ai/cloud_experiment.py).

Garantisce:
1. Hash congelati corretti del dataset (messages.json) e ground truth (ground_truth.json).
2. Hash errato/manomesso causa abort immediato (CloudExperimentIntegrityError).
3. Assenza RUN_CLOUD_E2E=1 causa abort (CloudExperimentOptInError).
4. Assenza --force-run causa abort (CloudExperimentOptInError).
5. Assenza API key causa abort prima di qualsiasi attività HTTP (AiBackendUnavailableError).
6. Endpoint non HTTPS o con query/fragment causa abort.
7. Nessuna fuga di ground truth nei prompt (expected_decision, expected_evidence_ids, human_rationale, keywords).
8. Topic Discovery non riceve expected_discovery_topics nel prompt.
9. Citazione di evidence_id inventati/invalidi viene respinta e registrata come errore.
10. Valutazione decisionale accurata (PRESENT, ABSENT, UNCERTAIN).
11. Corretta esecuzione separata di DIRECT_MULTILINGUAL e TRANSLATE_FIRST per Caso 5.
12. Nessun token segreto nei file di output generati.
13. Nessun retry automatico (chiamata singola).
14. Rifiuto di argomenti CLI arbitrari di input (--dataset, --ground-truth, --input-file).
15. Zero connessioni reali (offline al 100%).
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from ai.backend import (
    AiBackendError,
    AiBackendRequestError,
    AiBackendUnavailableError,
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiStructuredOutputError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.cloud_experiment import (
    FROZEN_DATASET_REL_PATH,
    FROZEN_DATASET_SHA256,
    FROZEN_GROUND_TRUTH_REL_PATH,
    FROZEN_GROUND_TRUTH_SHA256,
    CloudExperimentError,
    CloudExperimentIntegrityError,
    CloudExperimentOptInError,
    _ExperimentClientWrapper,
    _parse_min_request_interval_arg,
    main,
    parse_args,
    run_cloud_benchmark,
    run_synthetic_pipeline,
    verify_file_hash,
    verify_opt_in,
)
from ai.models import (
    AnalysisLanguageStrategy,
    TopicDecision,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer
from ai.translation import EvidenceTranslator

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def block_external_sockets(monkeypatch):
    """Garantisce che nessun test esegua connessioni di rete reali."""
    def _fail_connect(*args, **kwargs):
        raise RuntimeError("Tentativo di connessione esterna non consentito nei test del runner!")

    monkeypatch.setattr(socket, "create_connection", _fail_connect)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Evita attese reali di pacing durante i test del runner."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


class MockLlmClient(BaseLlmClient):
    """Client LLM in-memory deterministico per testare il runner offline."""

    def __init__(
        self,
        canned_responses: dict[str, str] | None = None,
        default_model: str = "gpt-4o",
        simulate_mismatch: str | None = None,
        simulate_error: Exception | None = None,
    ) -> None:
        self.canned_responses = canned_responses or {}
        self.default_model = default_model
        self.simulate_mismatch = simulate_mismatch
        self.simulate_error = simulate_error
        self.recorded_calls: list[dict] = []

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> LlmCompletionResponse:
        self.recorded_calls.append({
            "messages": [dict(m) for m in messages],
            "model_id": model_id,
            "schema": schema,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
        })

        if self.simulate_error is not None:
            raise self.simulate_error

        returned_model = self.simulate_mismatch or model_id or self.default_model
        if model_id and returned_model != model_id:
            raise AiModelMismatchError(
                f"Model mismatch nel backend cloud: richiesto '{model_id}', restituito '{returned_model}'.",
                requested_model=model_id,
                returned_model=returned_model,
            )

        full_prompt = " ".join(m.get("content", "") for m in messages)

        # Cerca corrispondenza in canned_responses
        resp_content = None
        for key, val in self.canned_responses.items():
            if key in full_prompt:
                resp_content = val
                break

        if resp_content is None:
            # Fallback generico per topic detection
            resp_content = json.dumps({
                "decision": "PRESENT",
                "evidence_ids": ["unified:cellebrite_json:ce2e_c1_001::ORIGINAL_TEXT"],
                "rationale": "Evidenza rilevata dal mock.",
            })

        return LlmCompletionResponse(
            content=resp_content,
            model=returned_model,
            prompt_tokens=50,
            completion_tokens=25,
            total_tokens=75,
            latency_seconds=0.123,
            metadata={
                "backend": "mock_client",
                "temperature": temperature,
                "seed": seed,
            },
        )


class TestCloudExperimentOptInAndIntegrity:
    """Verifica del doppio opt-in e dell'integrità degli hash congelati."""

    def test_frozen_files_exist_and_hashes_match(self):
        """I file congelati devono esistere e i loro hash SHA-256 devono coincidere esattamente."""
        ds_path = _REPO_ROOT / FROZEN_DATASET_REL_PATH
        gt_path = _REPO_ROOT / FROZEN_GROUND_TRUTH_REL_PATH

        assert ds_path.is_file(), f"Dataset file non trovato: {ds_path}"
        assert gt_path.is_file(), f"Ground truth file non trovato: {gt_path}"

        verify_file_hash(ds_path, FROZEN_DATASET_SHA256, "dataset")
        verify_file_hash(gt_path, FROZEN_GROUND_TRUTH_SHA256, "ground_truth")

    def test_hash_mismatch_raises_integrity_error(self, tmp_path):
        """Se il file del dataset viene alterato, verify_file_hash deve sollevare CloudExperimentIntegrityError."""
        fake_file = tmp_path / "tampered.json"
        fake_file.write_text('{"tampered": true}', encoding="utf-8")

        with pytest.raises(CloudExperimentIntegrityError, match="Violazione integrità"):
            verify_file_hash(fake_file, FROZEN_DATASET_SHA256, "dataset manomesso")

    def test_missing_file_raises_file_not_found_error(self, tmp_path):
        """Se il file congelato non esiste, deve sollevare FileNotFoundError."""
        missing = tmp_path / "non_existent.json"
        with pytest.raises(FileNotFoundError, match="non trovato"):
            verify_file_hash(missing, FROZEN_DATASET_SHA256, "file mancante")

    def test_double_opt_in_both_required(self, monkeypatch):
        """Devono essere presenti sia RUN_CLOUD_E2E=1 sia force_run=True."""
        # 1. Nessun opt-in
        monkeypatch.delenv("RUN_CLOUD_E2E", raising=False)
        with pytest.raises(CloudExperimentOptInError, match="doppio opt-in obbligatorio non soddisfatto"):
            verify_opt_in(force_run=False)

        # 2. Solo flag CLI ma no env
        with pytest.raises(CloudExperimentOptInError, match="variabile d'ambiente RUN_CLOUD_E2E=1"):
            verify_opt_in(force_run=True)

        # 3. Solo env ma no flag CLI
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        with pytest.raises(CloudExperimentOptInError, match="flag CLI --force-run"):
            verify_opt_in(force_run=False)

        # 4. Entrambi soddisfatti -> nessun errore
        verify_opt_in(force_run=True)

    def test_missing_api_key_aborts_before_network(self, monkeypatch):
        """Se la variabile d'ambiente dell'API key è vuota o assente, abortisce prima di qualsiasi chiamata."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.delenv("CLOUD_LLM_API_KEY", raising=False)

        with pytest.raises(AiBackendUnavailableError, match="API key cloud non configurata"):
            run_cloud_benchmark(
                endpoint_url="https://api.openai.com/v1",
                model_id="gpt-4o",
                force_run=True,
            )

    def test_invalid_endpoint_url_aborts(self, monkeypatch):
        """Endpoint non HTTPS o contenenti query/fragment devono causare l'abort con ValueError."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-secret-key")

        # HTTP non cifrato
        with pytest.raises(ValueError, match="richiede obbligatoriamente 'https'"):
            run_cloud_benchmark(
                endpoint_url="http://api.openai.com/v1",
                model_id="gpt-4o",
                force_run=True,
            )

        # Parametri di query non consentiti né echoati
        super_secret_query = "SUPER_SECRET_QUERY_VALUE"
        with pytest.raises(ValueError) as exc_q:
            run_cloud_benchmark(
                endpoint_url=f"https://api.openai.com/v1?key={super_secret_query}",
                model_id="gpt-4o",
                force_run=True,
            )
        assert "Parametri di query" in str(exc_q.value)
        assert super_secret_query not in str(exc_q.value)

        # Fragment non consentito né echoato
        super_secret_fragment = "SUPER_SECRET_FRAGMENT_VALUE"
        with pytest.raises(ValueError) as exc_f:
            run_cloud_benchmark(
                endpoint_url=f"https://api.openai.com/v1#{super_secret_fragment}",
                model_id="gpt-4o",
                force_run=True,
            )
        assert "Fragment" in str(exc_f.value)
        assert super_secret_fragment not in str(exc_f.value)

    def test_cli_forbids_arbitrary_input_paths(self):
        """La CLI non deve permettere parametri di override del dataset o ground truth."""
        # Se l'utente tenta di passare --dataset o --ground-truth, argparse deve rifiutarli
        with pytest.raises(SystemExit):
            parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--dataset", "other.json"])

        with pytest.raises(SystemExit):
            parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--ground-truth", "other.json"])

        with pytest.raises(SystemExit):
            parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--input-file", "other.json"])


@pytest.fixture
def mock_responses():
    """Risposte controllate conformi allo schema per i 6 casi."""
    return {
        # Case 1 (Ciclismo)
        "Ciclismo": json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [
                "unified:cellebrite_json:ce2e_c1_001::ORIGINAL_TEXT",
                "unified:cellebrite_json:ce2e_c1_002::ORIGINAL_TEXT",
            ],
            "rationale": "Conversazione chiaramente incentrata sul ciclismo.",
        }),
        # Case 2 (Compravendita Illecita implicita)
        "ce2e_c2": json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [
                "unified:cellebrite_json:ce2e_c2_001::ORIGINAL_TEXT",
                "unified:cellebrite_json:ce2e_c2_003::ORIGINAL_TEXT",
            ],
            "rationale": "Merci non tracciabili e pagamento contanti clandestino.",
        }),
        # Case 3 (Compravendita Illecita ambigua)
        "ce2e_c3": json.dumps({
            "decision": "UNCERTAIN",
            "evidence_ids": [
                "unified:cellebrite_json:ce2e_c3_001::ORIGINAL_TEXT",
            ],
            "rationale": "Evidenze parziali e ambigue non sufficienti per concludere.",
        }),
        # Case 4 (Compravendita Illecita assente)
        "ce2e_c4": json.dumps({
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "La conversazione verte su risotto e chitarra.",
        }),
        # Case 5 Traduzione (per TRANSLATE_FIRST)
        "TRANSLATION": json.dumps({
            "translations": [
                {"evidence_id": "unified:cellebrite_json:ce2e_c5_001::ORIGINAL_TEXT", "translated_text": "Prenotazione volo Barcellona"},
                {"evidence_id": "unified:cellebrite_json:ce2e_c5_002::ORIGINAL_TEXT", "translated_text": "Conferenza accademica confermata"},
                {"evidence_id": "unified:cellebrite_json:ce2e_c5_003::ORIGINAL_TEXT", "translated_text": "Camera hotel prenotata"},
                {"evidence_id": "unified:cellebrite_json:ce2e_c5_004::ORIGINAL_TEXT", "translated_text": "Keynote speaker annunciato"},
                {"evidence_id": "unified:cellebrite_json:ce2e_c5_005::ORIGINAL_TEXT", "translated_text": "Incontro all'aeroporto per il taxi"},
            ]
        }),
        # Case 5 Detection (per DIRECT e TRANSLATE)
        "ce2e_c5": json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [
                "unified:cellebrite_json:ce2e_c5_001::ORIGINAL_TEXT",
                "unified:cellebrite_json:ce2e_c5_002::ORIGINAL_TEXT",
            ],
            "rationale": "Viaggio per conferenza accademica a Barcellona confermato.",
        }),
        # Case 6 Topic Discovery
        "OPEN TOPIC DISCOVERY": json.dumps({
            "topics": [
                {
                    "label": "Condominio",
                    "short_description": "Discussione su spese e assemblea",
                    "evidence_ids": ["unified:cellebrite_json:ce2e_c6_001::ORIGINAL_TEXT"],
                },
                {
                    "label": "Calcetto",
                    "short_description": "Partita di calcetto settimanale",
                    "evidence_ids": ["unified:cellebrite_json:ce2e_c6_002::ORIGINAL_TEXT"],
                },
            ]
        }),
    }


class TestCloudExperimentExecutionMocked:
    """Verifica dell'esecuzione logica dell'esperimento tramite mock client."""

    def test_ground_truth_never_leaked_into_prompts(self, monkeypatch, tmp_path, mock_responses):
        """Verifica che nessun dato sensibile della ground truth appaia mai nei messaggi inviati al modello."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-test-secret-never-expose-12345")

        mock_client = MockLlmClient(canned_responses=mock_responses, default_model="gpt-4o")

        out_dir = tmp_path / "cloud_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        # Carica la ground truth reale per confrontare i testi
        gt_path = _REPO_ROOT / FROZEN_GROUND_TRUTH_REL_PATH
        gt_data = json.loads(gt_path.read_text(encoding="utf-8"))

        for call in mock_client.recorded_calls:
            all_content = " ".join(m["content"] for m in call["messages"])

            # 1. Nessun human_rationale deve mai essere presente nel prompt
            for c in gt_data["cases"]:
                rat = c.get("human_rationale")
                if rat:
                    assert rat not in all_content, f"human_rationale trapelato nel prompt: {rat[:30]}..."

            # 2. Nessun expected_discovery_topics deve apparire nelle chiamate di discovery
            if "OPEN TOPIC DISCOVERY" in all_content:
                c6 = next(c for c in gt_data["cases"] if c["case_id"] == "CASE_6_TOPIC_DISCOVERY")
                for exp_top in c6["expected_discovery_topics"]:
                    assert exp_top["label"] not in all_content, f"Discovery topic label trapelato: {exp_top['label']}"
                    for kw in exp_top["keywords"]:
                        # Le keyword composite della ground truth non devono essere passate
                        assert f"'{kw}'" not in all_content

    def test_full_synthetic_benchmark_metrics_and_artifacts(self, monkeypatch, tmp_path, mock_responses):
        """Esecuzione completa del benchmark: verifica metriche, decision accuracy e artefatti generati."""
        secret_key = "sk-test-secret-never-expose-12345"
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", secret_key)

        mock_client = MockLlmClient(canned_responses=mock_responses, default_model="gpt-4o")
        out_dir = tmp_path / "cloud_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        # 1. Metriche Topic Detection
        metrics = results["metrics"]
        assert metrics["benchmark_type"] == "TECHNICAL VALIDATION BENCHMARK"
        assert metrics["total_detection_cases"] == 6  # 1, 2, 3, 4, 5-DIRECT, 5-TRANSLATE
        assert metrics["completed_detection_cases"] == 6
        assert metrics["failed_detection_cases"] == 0
        assert metrics["correct_decisions"] == 6
        assert metrics["decision_accuracy"] == 1.0
        assert metrics["invalid_evidence_citation_count"] == 0
        assert metrics["structured_output_failure_count"] == 0
        assert metrics["model_mismatch_count"] == 0

        # 2. Verifica breakdown decisioni
        assert metrics["present_breakdown"]["total"] == 4  # Case 1, 2, 5A, 5B
        assert metrics["present_breakdown"]["correct"] == 4
        assert metrics["absent_breakdown"]["total"] == 1   # Case 4
        assert metrics["absent_breakdown"]["correct"] == 1
        assert metrics["uncertain_breakdown"]["total"] == 1 # Case 3
        assert metrics["uncertain_breakdown"]["correct"] == 1

        # 3. Verifica caso multilingue TRANSLATE_FIRST (5B)
        case_5b = next(c for c in results["cases"] if c["case_id"] == "CASE_5_MULTILINGUAL_TRANSLATE_FIRST")
        assert case_5b["strategy"] == AnalysisLanguageStrategy.TRANSLATE_FIRST.value
        assert case_5b["translation_prompt_version"] == "evidence_translation_v1"
        assert case_5b["translation_latency_seconds"] is not None
        assert case_5b["translation_total_tokens"] is not None

        # 4. Verifica Caso 6 (Topic Discovery)
        case_6 = next(c for c in results["cases"] if c["case_id"] == "CASE_6_TOPIC_DISCOVERY")
        assert case_6["evaluation_mode"] == "QUALITATIVE"
        assert len(case_6["topics"]) == 2
        assert case_6["evidence_ids_valid"] is True

        # 5. Verifica file di output generati e assenza di segreti
        json_file = out_dir / "cloud-e2e-result.json"
        md_file = out_dir / "cloud-e2e-report.md"

        assert json_file.is_file()
        assert md_file.is_file()

        json_text = json_file.read_text(encoding="utf-8")
        md_text = md_file.read_text(encoding="utf-8")

        assert secret_key not in json_text
        assert secret_key not in md_text
        assert "Authorization" not in json_text
        assert "Bearer" not in json_text

    def test_invalid_evidence_id_causes_case_failure(self, monkeypatch, tmp_path):
        """Se il modello inventa un evidence_id, il validator deve fallire e il caso viene registrato come FAILED."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        # Mock che restituisce un ID inventato non presente in doc
        invalid_evidence_resp = {
            "Ciclismo": json.dumps({
                "decision": "PRESENT",
                "evidence_ids": ["unified:cellebrite_json:INVENTED_ID_999::ORIGINAL_TEXT"],
                "rationale": "Evidenza inventata.",
            })
        }
        mock_client = MockLlmClient(canned_responses=invalid_evidence_resp, default_model="gpt-4o")
        out_dir = tmp_path / "cloud_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        case_1 = next(c for c in results["cases"] if c["case_id"] == "CASE_1_EXPLICIT_PRESENT")
        assert case_1["status"] == "FAILED"
        assert case_1["error_type"] == "AiInvalidEvidenceCitationError"
        assert results["metrics"]["invalid_evidence_citation_count"] >= 1
        assert results["metrics"]["structured_output_failure_count"] >= 1

    def test_model_mismatch_causes_case_failure(self, monkeypatch, tmp_path):
        """Se il client rileva un model mismatch, il caso viene registrato come FAILED con errore AiModelMismatchError."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        mock_client = MockLlmClient(
            default_model="gpt-4o",
            simulate_mismatch="gpt-4o-different-version",
        )
        out_dir = tmp_path / "cloud_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        case_1 = next(c for c in results["cases"] if c["case_id"] == "CASE_1_EXPLICIT_PRESENT")
        assert case_1["status"] == "FAILED"
        assert case_1["error_type"] == "AiModelMismatchError"
        assert case_1["model_id_requested"] == "gpt-4o"
        assert case_1["model_id_returned"] == "gpt-4o-different-version"
        assert case_1["model_id_returned"] != "MISMATCH"
        assert results["metrics"]["model_mismatch_count"] >= 1

    def test_no_automatic_retry_on_backend_error(self, monkeypatch, tmp_path):
        """In caso di errore del backend (es. timeout o errore HTTP), viene effettuato esattamente 1 tentativo per caso."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        mock_client = MockLlmClient(
            default_model="gpt-4o",
            simulate_error=AiBackendRequestError("Simulated 500 error"),
        )
        out_dir = tmp_path / "cloud_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        # 6 chiamate totali: Case 1, 2, 3, 4, 5A, 5B (fallisce al primo step di traduzione), 6
        # Totale tentativi registrati deve essere esattamente 7 (o 6, una per caso/sotto-caso)
        assert len(mock_client.recorded_calls) == 7
        assert results["metrics"]["failed_detection_cases"] == 6

    def test_ground_truth_accessed_strictly_post_ingestion(self, monkeypatch, tmp_path, mock_responses):
        """Dimostra che nessun accesso o verifica hash di ground_truth avviene prima della pipeline sintetica."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        mock_client = MockLlmClient(canned_responses=mock_responses, default_model="gpt-4o")
        out_dir = tmp_path / "cloud_output"

        call_order = []

        import ai.cloud_experiment as ce
        orig_run_pipeline = ce.run_synthetic_pipeline
        orig_verify_hash = ce.verify_file_hash
        orig_load_gt = ce.load_ground_truth_post_ingestion

        def spy_run_pipeline(dataset_path):
            call_order.append("run_synthetic_pipeline")
            return orig_run_pipeline(dataset_path)

        def spy_verify_hash(path, expected_hash, label):
            call_order.append(f"verify_hash:{label}")
            return orig_verify_hash(path, expected_hash, label)

        def spy_load_gt(gt_path):
            call_order.append("load_ground_truth")
            return orig_load_gt(gt_path)

        monkeypatch.setattr(ce, "run_synthetic_pipeline", spy_run_pipeline)
        monkeypatch.setattr(ce, "verify_file_hash", spy_verify_hash)
        monkeypatch.setattr(ce, "load_ground_truth_post_ingestion", spy_load_gt)

        run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
        )

        assert "run_synthetic_pipeline" in call_order
        assert "verify_hash:ground truth (ground_truth.json)" in call_order
        assert "load_ground_truth" in call_order

        pipe_idx = call_order.index("run_synthetic_pipeline")
        gt_hash_idx = call_order.index("verify_hash:ground truth (ground_truth.json)")
        gt_load_idx = call_order.index("load_ground_truth")
        ds_hash_idx = call_order.index("verify_hash:dataset sintetico (messages.json)")

        # Dataset hash verificato prima della pipeline
        assert ds_hash_idx < pipe_idx
        # Ground truth verificata e letta SOLO DOPO il completamento della pipeline
        assert pipe_idx < gt_hash_idx
        assert gt_hash_idx < gt_load_idx

    def test_synthetic_pipeline_failure_raises_cloud_experiment_error(self, monkeypatch):
        """Se l'ingestion fallisce, run_synthetic_pipeline solleva CloudExperimentError e non procede all'AI."""
        from ui.models import IngestionResult, IngestionStatus, IngestionSummary, SourceFormat
        import ai.cloud_experiment as ce

        fake_summary = IngestionSummary(
            source_format=SourceFormat.CELLEBRITE_JSON,
            original_filename="messages.json",
            sha256="fake",
            file_size_bytes=100,
            raw_record_count=0,
            validation_issue_count=1,
            normalized_record_count=0,
            unified_message_count=0,
            conversation_count=0,
            auxiliary_record_count=0,
            status=IngestionStatus.FAILED,
        )
        fake_result = IngestionResult(summary=fake_summary, documents={})

        monkeypatch.setattr("ai.cloud_experiment.ingest_file_payload", lambda **kwargs: fake_result)

        ds_path = _REPO_ROOT / FROZEN_DATASET_REL_PATH
        with pytest.raises(CloudExperimentError, match="pipeline sintetica non riuscita"):
            ce.run_synthetic_pipeline(ds_path)

    def test_cli_main_exception_sanitizes_api_key(self, monkeypatch, capsys):
        """main() deve redigere la API key dall'output anche in caso di eccezione generica imprevista."""
        secret_key = "sk-super-secret-api-key-99999"
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", secret_key)

        def _boom(*args, **kwargs):
            raise RuntimeError(f"Unexpected crash exposing {secret_key}")

        monkeypatch.setattr("ai.cloud_experiment.run_cloud_benchmark", _boom)

        with pytest.raises(SystemExit) as exc_info:
            main(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--force-run"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert secret_key not in captured.err
        assert secret_key not in captured.out
        assert "[REDACTED_API_KEY]" in captured.err

    def test_cli_main_output_distinguishes_success_from_failure(self, monkeypatch, tmp_path, mock_responses, capsys):
        """Se ci sono failure, il banner CLI non deve riportare 'CON SUCCESSO' ma 'CON FAILURE REGISTRATE'."""
        secret_key = "dummy-key"
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", secret_key)

        out_dir = tmp_path / "cli_output"

        # 1. Caso con fallimenti: Mock che simula mismatch
        mismatch_client = MockLlmClient(default_model="gpt-4o", simulate_mismatch="wrong-model")
        with patch("ai.cloud_experiment.RemoteOpenAICompatibleTestClient", return_value=mismatch_client):
            exit_code = main([
                "--endpoint-url", "https://api.openai.com/v1",
                "--model-id", "gpt-4o",
                "--force-run",
                "--output-dir", str(out_dir),
            ])
            assert exit_code == 0
            captured = capsys.readouterr()
            assert "PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON FAILURE REGISTRATE" in captured.out
            assert "CON SUCCESSO" not in captured.out

        # 2. Caso con tutti i compiti riusciti
        success_client = MockLlmClient(canned_responses=mock_responses, default_model="gpt-4o")
        with patch("ai.cloud_experiment.RemoteOpenAICompatibleTestClient", return_value=success_client):
            exit_code = main([
                "--endpoint-url", "https://api.openai.com/v1",
                "--model-id", "gpt-4o",
                "--force-run",
                "--output-dir", str(out_dir),
            ])
            assert exit_code == 0
            captured = capsys.readouterr()
            assert "PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON SUCCESSO" in captured.out
            assert "CON FAILURE REGISTRATE" not in captured.out


class TestProtocolV2Enhancements:
    """Test mirati per il Protocol V2 (supporto seed facoltativo, metriche raffinate e tracciamento)."""

    def test_cli_seed_parsing_integer(self):
        """--seed 42 produce l'intero 42."""
        args = parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--seed", "42"])
        assert args.seed == 42
        assert isinstance(args.seed, int)

    def test_cli_seed_parsing_none_variations(self):
        """--seed con variazioni di 'none' o 'null' produce None (omesso)."""
        for val in ["none", "None", "NONE", "null", "NULL", "omitted"]:
            args = parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--seed", val])
            assert args.seed is None

    def test_cli_seed_parsing_invalid_raises_error(self):
        """--seed con stringa non convertibile genera errore di parsing CLI."""
        with pytest.raises(SystemExit):
            parse_args(["--endpoint-url", "https://api.openai.com/v1", "--model-id", "gpt-4o", "--seed", "invalid_seed"])

    def test_cloud_client_payload_omits_seed_when_none(self, monkeypatch):
        """seed=None non genera il campo 'seed' nel payload HTTP cloud."""
        from ai.cloud_test import RemoteOpenAICompatibleTestClient
        import urllib.request

        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
        )

        captured_payloads = []

        def mock_urlopen(req, *args, **kwargs):
            body_dict = json.loads(req.data.decode("utf-8"))
            captured_payloads.append(body_dict)
            raw_bytes = json.dumps({
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1234567,
                "model": "gpt-4o",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"decision": "PRESENT", "evidence_ids": [], "rationale": "test"}'},
                    "finish_reason": "stop"
                }]
            }).encode("utf-8")
            resp_mock = MagicMock()
            resp_mock.status = 200
            resp_mock.read.return_value = raw_bytes
            resp_mock.__enter__.return_value = resp_mock
            resp_mock.__exit__.return_value = None
            return resp_mock

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model_id="gpt-4o",
            seed=None,
        )

        assert len(captured_payloads) == 1
        assert "seed" not in captured_payloads[0]

    def test_cloud_client_payload_includes_seed_when_integer(self, monkeypatch):
        """seed=42 include il campo 'seed' nel payload HTTP cloud con valore 42."""
        from ai.cloud_test import RemoteOpenAICompatibleTestClient
        import urllib.request

        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
        )

        captured_payloads = []

        def mock_urlopen(req, *args, **kwargs):
            body_dict = json.loads(req.data.decode("utf-8"))
            captured_payloads.append(body_dict)
            raw_bytes = json.dumps({
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1234567,
                "model": "gpt-4o",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"decision": "PRESENT", "evidence_ids": [], "rationale": "test"}'},
                    "finish_reason": "stop"
                }]
            }).encode("utf-8")
            resp_mock = MagicMock()
            resp_mock.status = 200
            resp_mock.read.return_value = raw_bytes
            resp_mock.__enter__.return_value = resp_mock
            resp_mock.__exit__.return_value = None
            return resp_mock

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            model_id="gpt-4o",
            seed=42,
        )

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["seed"] == 42

    def test_decision_accuracy_is_none_and_markdown_na_when_zero_completed(self, monkeypatch, tmp_path):
        """Quando nessun caso di detection è completato con successo, decision_accuracy è None e il report Markdown mostra N/A."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        mock_client = MockLlmClient(
            default_model="gpt-4o",
            simulate_error=AiBackendRequestError("Simulated 400 Bad Request"),
        )
        out_dir = tmp_path / "zero_completed_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
            seed=None,
        )

        m = results["metrics"]
        assert m["total_detection_cases"] == 6
        assert m["completed_detection_cases"] == 0
        assert m["failed_detection_cases"] == 6
        assert m["detection_completion_rate"] == 0.0
        assert m["decision_accuracy"] is None
        assert m["cloud_api_requests_sent"] == 7
        assert m["llm_inferences_completed"] == 0
        assert m["model_outputs_received"] == 0

        # Verifica Markdown
        md_path = out_dir / "cloud-e2e-report.md"
        assert md_path.is_file()
        md_text = md_path.read_text(encoding="utf-8")
        assert "Accaccuratezza Decisionale (Casi Completati) | N/A" in md_text or "Accuratezza Decisionale (Casi Completati) | N/A" in md_text
        assert "Detection Completion Rate | 0.0%" in md_text
        assert "Cloud API Requests Sent:** 7" in md_text
        assert "LLM Inferences Completed:** 0" in md_text
        assert "Seed:** `null` (UNSUPPORTED_OMITTED)" in md_text

    def test_decision_accuracy_computed_only_on_completed_cases(self, monkeypatch, tmp_path, mock_responses):
        """decision_accuracy deve essere calcolata esclusivamente sui casi completati con successo."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        # Client speciale che fallisce per Case 1 e Case 2, ma ha successo su 3, 4, 5A, 5B e 6.
        # Su 4 casi completati: supponiamo 3 corretti e 1 errato (modificando la risposta per Case 3 da UNCERTAIN a PRESENT).
        tampered_responses = dict(mock_responses)
        tampered_responses["ce2e_c3"] = json.dumps({
            "decision": "PRESENT",  # Errato rispetto a ground truth UNCERTAIN
            "evidence_ids": ["unified:cellebrite_json:ce2e_c3_001::ORIGINAL_TEXT"],
            "rationale": "decisione errata simulata per test metriche"
        })

        class SelectiveFailMockClient(MockLlmClient):
            def __init__(self, responses, default_model):
                super().__init__(canned_responses=responses, default_model=default_model)
                self.call_count = 0

            def chat_completion(self, messages, **kwargs):
                self.call_count += 1
                # Fallisce sui primi 2 tentativi (Case 1 e Case 2)
                if self.call_count in (1, 2):
                    raise AiBackendRequestError("Simulated backend failure on case 1 and 2")
                return super().chat_completion(messages, **kwargs)

        mock_client = SelectiveFailMockClient(tampered_responses, default_model="gpt-4o")
        out_dir = tmp_path / "partial_completed_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
            seed=None,
        )

        m = results["metrics"]
        assert m["total_detection_cases"] == 6
        assert m["completed_detection_cases"] == 4  # 4 completati (Case 3, 4, 5A, 5B)
        assert m["failed_detection_cases"] == 2     # 2 falliti (Case 1, 2)
        assert m["detection_completion_rate"] == round(4 / 6, 4)
        # Dei 4 completati: Case 4 (ABSENT corretto), Case 5A (PRESENT corretto), Case 5B (PRESENT corretto), Case 3 (PRESENT anziché UNCERTAIN -> errato)
        # Corretti = 3 / 4 completati -> decision_accuracy = 0.75 (NON 3/6 = 0.5)
        assert m["correct_decisions"] == 3
        assert m["decision_accuracy"] == 0.75

        assert m["cloud_api_requests_sent"] == 8  # 1+1+1+1+1+(1 trans + 1 det)+1 = 8
        assert m["llm_inferences_completed"] == 6  # 8 - 2 falliti = 6
        assert m["model_outputs_received"] == 6


class TestProtocolV3Pacing:
    """Test unitari approfonditi e offline per il rate-limit-aware pacing (Protocol V3)."""

    class MockClock:
        """Simulatore deterministico di orologio per test di pacing senza sleep reali."""
        def __init__(self, start_time: float = 100.0) -> None:
            self.current_time = start_time
            self.sleep_calls: list[float] = []

        def monotonic(self) -> float:
            return self.current_time

        def sleep(self, seconds: float) -> None:
            self.sleep_calls.append(seconds)
            self.current_time += seconds

    def _setup_clock(self, monkeypatch, start_time: float = 100.0) -> MockClock:
        clock = self.MockClock(start_time)
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        return clock

    def test_first_request_no_sleep(self, monkeypatch):
        """1. Prima richiesta: parte immediatamente senza alcuna chiamata a sleep."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        wrapper.chat_completion(messages=[{"role": "user", "content": "hi"}], model_id="gemini-3.8-flash")

        assert clock.sleep_calls == []
        assert wrapper.requests_sent == 1
        assert wrapper.inferences_completed == 1
        assert wrapper.last_request_start_monotonic == 100.0
        assert wrapper.request_start_deltas == []
        assert len(wrapper.request_telemetry) == 1
        assert wrapper.request_telemetry[0] == {
            "request_sequence_number": 1,
            "request_start_offset_seconds": 0.0,
        }

    def test_second_request_after_5s_sleeps_10s(self, monkeypatch):
        """2. Seconda richiesta dopo 5 secondi: attende preventivamente 10 secondi."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        wrapper.chat_completion(messages=[{"role": "user", "content": "r1"}], model_id="gemini-3.8-flash")
        assert clock.sleep_calls == []

        # Trascorrono 5 secondi
        clock.current_time += 5.0

        wrapper.chat_completion(messages=[{"role": "user", "content": "r2"}], model_id="gemini-3.8-flash")

        assert clock.sleep_calls == [10.0]
        assert wrapper.requests_sent == 2
        assert wrapper.request_start_deltas == [15.0]
        assert len(wrapper.request_telemetry) == 2
        assert wrapper.request_telemetry[1] == {
            "request_sequence_number": 2,
            "request_start_offset_seconds": 15.0,
        }

    def test_second_request_after_15s_no_sleep(self, monkeypatch):
        """3. Seconda richiesta dopo esattamente 15 secondi: nessuna sleep necessaria."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        wrapper.chat_completion(messages=[{"role": "user", "content": "r1"}], model_id="gemini-3.8-flash")

        clock.current_time += 15.0

        wrapper.chat_completion(messages=[{"role": "user", "content": "r2"}], model_id="gemini-3.8-flash")

        assert clock.sleep_calls == []
        assert wrapper.requests_sent == 2
        assert wrapper.request_start_deltas == [15.0]
        assert wrapper.request_telemetry[1]["request_start_offset_seconds"] == 15.0

    def test_second_request_after_20s_no_sleep(self, monkeypatch):
        """4. Seconda richiesta dopo 20 secondi (>15s): nessuna sleep necessaria."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        wrapper.chat_completion(messages=[{"role": "user", "content": "r1"}], model_id="gemini-3.8-flash")

        clock.current_time += 20.0

        wrapper.chat_completion(messages=[{"role": "user", "content": "r2"}], model_id="gemini-3.8-flash")

        assert clock.sleep_calls == []
        assert wrapper.requests_sent == 2
        assert wrapper.request_start_deltas == [20.0]
        assert wrapper.request_telemetry[1]["request_start_offset_seconds"] == 20.0

    def test_multiple_consecutive_requests_respect_interval(self, monkeypatch):
        """5. Richieste multiple consecutive: rispettano sempre l'intervallo minimo di 15s."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        simulated_elapsed_before_call = [0.0, 2.0, 5.0, 22.0, 1.0]
        for delay in simulated_elapsed_before_call:
            clock.current_time += delay
            wrapper.chat_completion(messages=[{"role": "user", "content": "req"}], model_id="m")

        assert wrapper.requests_sent == 5
        assert len(wrapper.request_start_deltas) == 4
        for delta in wrapper.request_start_deltas:
            assert delta >= 15.0

    def test_evidence_translator_respects_pacing(self, monkeypatch):
        """6. EvidenceTranslator è sottoposto allo stesso vincolo di pacing del wrapper."""
        clock = self._setup_clock(monkeypatch, 100.0)
        docs = run_synthetic_pipeline(_REPO_ROOT / FROZEN_DATASET_REL_PATH)
        doc = next(iter(docs.values()))

        canned = {
            "FORENSIC EVIDENCE FAITHFUL TRANSLATION": json.dumps({
                "translations": [
                    {"evidence_id": s.evidence_id, "translated_text": "Tradotto"}
                    for s in doc.all_evidence_sections
                ]
            })
        }
        inner = MockLlmClient(canned_responses=canned)
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)
        translator = EvidenceTranslator(client=wrapper, default_target_language="it", default_model_id="m")

        # Prima richiesta (traduzione) -> no sleep
        translator.translate_document(document=doc, model_id="m")
        assert clock.sleep_calls == []

        # Seconda richiesta dopo 4 secondi -> sleep 11s
        clock.current_time += 4.0
        translator.translate_document(document=doc, model_id="m")
        assert clock.sleep_calls == [11.0]

    def test_topic_detection_analyzer_respects_pacing(self, monkeypatch):
        """7. TopicDetectionAnalyzer passa attraverso il medesimo pacing."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)
        analyzer = TopicDetectionAnalyzer(client=wrapper, default_model_id="m")

        docs = run_synthetic_pipeline(_REPO_ROOT / FROZEN_DATASET_REL_PATH)
        doc = next(iter(docs.values()))
        topic = TopicQuery(topic_id="t1", label="Label", description="Desc")

        analyzer.detect_topic(document=doc, topic=topic)
        assert clock.sleep_calls == []

        clock.current_time += 7.0
        analyzer.detect_topic(document=doc, topic=topic)
        assert clock.sleep_calls == [8.0]

    def test_topic_discovery_analyzer_respects_pacing(self, monkeypatch):
        """8. TopicDiscoveryAnalyzer passa attraverso il medesimo pacing."""
        clock = self._setup_clock(monkeypatch, 100.0)
        canned = {
            "OPEN TOPIC DISCOVERY": json.dumps({"topics": [{"label": "T1", "short_description": "Desc", "evidence_ids": ["unified:cellebrite_json:ce2e_c1_001::ORIGINAL_TEXT"]}]})
        }
        inner = MockLlmClient(canned_responses=canned)
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)
        analyzer = TopicDiscoveryAnalyzer(client=wrapper, default_model_id="m")

        docs = run_synthetic_pipeline(_REPO_ROOT / FROZEN_DATASET_REL_PATH)
        doc = next(iter(docs.values()))

        analyzer.discover_topics(document=doc, model_id="m")
        assert clock.sleep_calls == []

        clock.current_time += 3.0
        analyzer.discover_topics(document=doc, model_id="m")
        assert clock.sleep_calls == [12.0]

    def test_pacing_does_not_cause_retry_on_429(self, monkeypatch):
        """9 & 10. HTTP 429 causa fallimento immediato senza ALCUN retry o recovery."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient(simulate_error=AiBackendRequestError("HTTP 429 Too Many Requests"))
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        with pytest.raises(AiBackendRequestError) as exc_info:
            wrapper.chat_completion(messages=[{"role": "user", "content": "call"}], model_id="m")

        assert "429" in str(exc_info.value)
        assert wrapper.requests_sent == 1
        assert wrapper.inferences_completed == 0
        assert wrapper.outputs_received == 0

    def test_pacing_does_not_cause_retry_on_503(self, monkeypatch):
        """11. HTTP 503 causa fallimento immediato senza ALCUN retry o recovery."""
        clock = self._setup_clock(monkeypatch, 100.0)
        inner = MockLlmClient(simulate_error=AiBackendUnavailableError("HTTP 503 Service Unavailable"))
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=15.0)

        with pytest.raises(AiBackendUnavailableError) as exc_info:
            wrapper.chat_completion(messages=[{"role": "user", "content": "call"}], model_id="m")

        assert "503" in str(exc_info.value)
        assert wrapper.requests_sent == 1
        assert wrapper.inferences_completed == 0
        assert wrapper.outputs_received == 0

    def test_seed_none_remains_omitted(self, monkeypatch):
        """12. seed=None viene passato correttamente come None al client interno."""
        inner = MockLlmClient()
        wrapper = _ExperimentClientWrapper(inner, min_request_interval_seconds=0.0)

        wrapper.chat_completion(messages=[{"role": "user", "content": "call"}], model_id="m", seed=None)

        assert len(inner.recorded_calls) == 1
        assert inner.recorded_calls[0]["seed"] is None

    def test_cli_min_request_interval_parsing(self):
        """15 & 16. Validazione CLI per --min-request-interval-seconds (valori validi e rifiuto negativi)."""
        args = parse_args([
            "--endpoint-url", "https://api.openai.com/v1",
            "--model-id", "gemini-3.8-flash",
            "--min-request-interval-seconds", "15",
        ])
        assert args.min_request_interval_seconds == 15.0

        args_zero = parse_args([
            "--endpoint-url", "https://api.openai.com/v1",
            "--model-id", "gemini-3.8-flash",
            "--min-request-interval-seconds", "0.0",
        ])
        assert args_zero.min_request_interval_seconds == 0.0

        with pytest.raises(SystemExit):
            parse_args([
                "--endpoint-url", "https://api.openai.com/v1",
                "--model-id", "gemini-3.8-flash",
                "--min-request-interval-seconds", "-10",
            ])

    def test_full_benchmark_pacing_telemetry_and_metrics(self, monkeypatch, tmp_path, mock_responses):
        """13, 14 & 17. Esecuzione benchmark offline con Protocol V3: metriche, telemetria e report coerenti."""
        monkeypatch.setenv("RUN_CLOUD_E2E", "1")
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")

        # Mock clock che avanza deterministico
        clock = self._setup_clock(monkeypatch, 100.0)

        mock_client = MockLlmClient(canned_responses=mock_responses, default_model="gemini-3.8-flash")
        out_dir = tmp_path / "v3_offline_output"

        results = run_cloud_benchmark(
            endpoint_url="https://api.openai.com/v1",
            model_id="gemini-3.8-flash",
            force_run=True,
            output_dir=out_dir,
            client_override=mock_client,
            seed=None,
            min_request_interval_seconds=15.0,
        )

        assert results["protocol_version"] == 3
        assert results["phase"] == "PRELIMINARY CLOUD E2E VALIDATION"
        cfg = results["configuration"]
        assert cfg["protocol_version"] == 3
        assert cfg["minimum_request_interval_seconds"] == 15.0
        assert cfg["provider_rpm_limit_observed"] == 5
        assert cfg["provider_tpm_limit_observed"] == 250000
        assert cfg["provider_rpd_limit_observed"] == 20
        assert cfg["automatic_retry"] is False

        m = results["metrics"]
        assert m["protocol_version"] == 3
        assert m["minimum_request_interval_seconds"] == 15.0
        assert m["requests_with_pacing"] == 8
        assert m["cloud_api_requests_sent"] == 8
        assert m["llm_inferences_completed"] == 8
        assert m["model_outputs_received"] == 8
        assert m["completed_detection_cases"] == 6
        assert m["decision_accuracy"] == 1.0
        assert m["automatic_retry"] is False

        # Verifica deltas osservati
        assert len(m["request_start_deltas"]) == 7
        assert all(d >= 15.0 for d in m["request_start_deltas"])
        assert m["minimum_observed_request_start_delta_seconds"] >= 15.0

        # Verifica telemetria per-request
        telemetry = results["request_telemetry"]
        assert len(telemetry) == 8
        assert telemetry[0]["request_sequence_number"] == 1
        assert telemetry[0]["request_start_offset_seconds"] == 0.0
        assert telemetry[-1]["request_sequence_number"] == 8
        assert telemetry[-1]["request_start_offset_seconds"] >= 105.0

        # Verifica file generati
        json_file = out_dir / "cloud-e2e-result.json"
        md_file = out_dir / "cloud-e2e-report.md"
        assert json_file.is_file()
        assert md_file.is_file()

        md_text = md_file.read_text(encoding="utf-8")
        assert "Protocol V3" in md_text
        assert "Pacing Telemetria Preventivo (Protocol V3)" in md_text
        assert "Intervallo Minimo Configurato" in md_text
        assert "Limiti Provider Osservati" in md_text
        assert "HTTP 429 (Too Many Requests) / HTTP 503 (Service Unavailable)" in md_text


