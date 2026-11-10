"""
tests/unit/test_ai_experiment.py
--------------------------------
Test unitari per il CLI Pilot Runner ai/experiment.py:
- Opt-in guard di sicurezza (RUN_LM_STUDIO_BENCHMARK o --force-run)
- Validazione della famiglia di modelli contro infer_model_family (Sezione G)
- Distinzione tra metadati dichiarati e osservati da get_models_detailed (Sezione H)
- Propagazione dei bug di programmazione senza mascheramento da except Exception (Sezione F)
"""
import json
from types import MappingProxyType
import pytest
from unittest.mock import MagicMock, patch

from ai.backend import (
    AiBackendError,
    AnalysisInputTooLargeError,
    FakeLocalLlmClient,
)
from ai.experiment import main, parse_args
from ai.models import ModelFamily


@pytest.mark.unit
class TestAiExperimentCliRunner:

    def test_parse_args_defaults(self):
        args = parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN"])
        assert args.model_id == "qwen2.5-1.5b"
        assert args.family == "QWEN"
        assert args.base_url == "http://127.0.0.1:1234"
        assert args.force_run is False

    def test_opt_in_guard_blocks_without_env_or_flag(self, monkeypatch):
        monkeypatch.delenv("RUN_LM_STUDIO_BENCHMARK", raising=False)
        ret = main(["--model-id", "qwen2.5-1.5b", "--family", "QWEN"])
        assert ret == 2

    def test_opt_in_guard_passes_with_force_run(self, monkeypatch):
        monkeypatch.delenv("RUN_LM_STUDIO_BENCHMARK", raising=False)
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = False
            mock_client_cls.return_value = mock_client

            ret = main(["--model-id", "qwen2.5-1.5b", "--family", "QWEN", "--force-run"])
            # Fallisce su is_available (server non disponibile = codice 1), non sull'opt-in (codice 2)
            assert ret == 1

    def test_model_family_mismatch_blocks_execution(self, monkeypatch):
        # Sezione G: qwen non può essere eseguito dichiarando family LLAMA
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-1.5b-instruct",)
            mock_client_cls.return_value = mock_client

            ret = main([
                "--model-id", "qwen2.5-1.5b-instruct",
                "--family", "LLAMA",  # Discrepanza evidente
            ])
            assert ret == 1

    def test_model_family_match_proceeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-1.5b-instruct",)
            mock_client.get_models_detailed.return_value = [{
                "id": "qwen2.5-1.5b-instruct",
                "quantization": "Q4_K_M",
                "parameter_size": "1.5B",
                "max_context_length": "4096",
            }]
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                mock_bench.return_value = {
                    "benchmark_mode": "REAL_MODEL_BENCHMARK",
                    "model_spec": {"model_id": "qwen2.5-1.5b-instruct"},
                    "results_by_strategy": {},
                }
                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    mock_disc.return_value = {"task": "OPEN_TOPIC_DISCOVERY"}

                    ret = main([
                        "--model-id", "qwen2.5-1.5b-instruct",
                        "--family", "QWEN",
                        "--quantization", "Q4_K_M",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0

                    # Verifica che spec contenga metadati dichiarati e osservati distinti (Sezione H)
                    called_spec = mock_bench.call_args[1]["model_spec"]
                    assert called_spec.metadata["declared_model_metadata"]["quantization"] == "Q4_K_M"
                    assert called_spec.metadata["observed_model_metadata"]["quantization"] == "Q4_K_M"
                    assert called_spec.metadata["observed_model_metadata"]["parameter_size"] == "1.5B"
                    assert list(called_spec.metadata["metadata_discrepancies"]) == []

    def test_cli_contains_timeout_and_max_tokens_defaults(self):
        args = parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN"])
        assert args.timeout == 120.0
        assert args.max_tokens == 512

    def test_cli_timeout_le_zero_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN", "--timeout", "0"])
        with pytest.raises(SystemExit):
            parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN", "--timeout", "-10.5"])

    def test_cli_max_tokens_le_zero_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN", "--max-tokens", "0"])
        with pytest.raises(SystemExit):
            parse_args(["--model-id", "qwen2.5-1.5b", "--family", "QWEN", "--max-tokens", "-5"])

    def test_timeout_and_max_tokens_propagated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-1.5b-instruct",)
            mock_client.get_models_detailed.return_value = []
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                mock_bench.return_value = {
                    "benchmark_mode": "REAL_MODEL_BENCHMARK",
                    "model_spec": {"model_id": "qwen2.5-1.5b-instruct"},
                    "results_by_strategy": {},
                }
                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    mock_disc.return_value = {"task": "OPEN_TOPIC_DISCOVERY"}

                    ret = main([
                        "--model-id", "qwen2.5-1.5b-instruct",
                        "--family", "QWEN",
                        "--timeout", "45.5",
                        "--max-tokens", "256",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0
                    assert mock_bench.call_args[1]["timeout_seconds"] == 45.5
                    assert mock_bench.call_args[1]["max_tokens"] == 256
                    assert mock_disc.call_args[1]["timeout_seconds"] == 45.5
                    assert mock_disc.call_args[1]["max_tokens"] == 256

    def test_other_inferred_family_blocks_non_other_declared(self, monkeypatch):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("custom-unknown-model",)
            mock_client_cls.return_value = mock_client

            # OTHER + LLAMA bloccato
            assert main(["--model-id", "custom-unknown-model", "--family", "LLAMA"]) == 1
            # OTHER + QWEN bloccato
            assert main(["--model-id", "custom-unknown-model", "--family", "QWEN"]) == 1
            # OTHER + DEEPSEEK bloccato
            assert main(["--model-id", "custom-unknown-model", "--family", "DEEPSEEK"]) == 1

    def test_other_inferred_family_with_other_declared_allowed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("custom-unknown-model",)
            mock_client.get_models_detailed.return_value = []
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                mock_bench.return_value = {
                    "benchmark_mode": "REAL_MODEL_BENCHMARK",
                    "model_spec": {"model_id": "custom-unknown-model"},
                    "results_by_strategy": {},
                }
                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    mock_disc.return_value = {"task": "OPEN_TOPIC_DISCOVERY"}

                    # OTHER + OTHER consentito
                    ret = main([
                        "--model-id", "custom-unknown-model",
                        "--family", "OTHER",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0

    def test_programming_errors_propagate_without_silent_masking(self, monkeypatch):
        # Sezione F: Un AttributeError o TypeError non deve essere intercettato da except Exception
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-1.5b-instruct",)
            mock_client.get_models_detailed.return_value = []
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                mock_bench.side_effect = AttributeError("Attributo mancante per bug interno")

                with pytest.raises(AttributeError, match="Attributo mancante"):
                    main(["--model-id", "qwen2.5-1.5b-instruct", "--family", "QWEN"])

    def test_lmstudio_dict_quantization_preserves_structure_no_false_discrepancy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-7b-instruct",)
            # LM Studio restituisce quantization come dizionario
            mock_client.get_models_detailed.return_value = [{
                "id": "qwen2.5-7b-instruct",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "params_string": "7B",
                "max_context_length": "8192",
            }]
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                # Simula risultato benchmark con model_spec frozen
                def fake_bench(*args, **kwargs):
                    spec = kwargs["model_spec"]
                    return {
                        "benchmark_mode": "REAL_MODEL_BENCHMARK",
                        "model_spec": {
                            "model_id": spec.model_id,
                            "family": spec.family.value,
                            "quantization": spec.quantization,
                            "parameter_size": spec.parameter_size,
                            "context_length": spec.context_length,
                            "declared_model_metadata": spec.metadata["declared_model_metadata"],
                            "observed_model_metadata": spec.metadata["observed_model_metadata"],
                            "metadata_discrepancies": spec.metadata["metadata_discrepancies"],
                        },
                        "results_by_strategy": {},
                        "timestamp": "2026-10-08T12:00:00Z",
                    }
                mock_bench.side_effect = fake_bench

                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    def fake_disc(*args, **kwargs):
                        spec = kwargs["model_spec"]
                        return {
                            "task": "OPEN_TOPIC_DISCOVERY",
                            "model_spec": {
                                "model_id": spec.model_id,
                                "declared_model_metadata": spec.metadata["declared_model_metadata"],
                                "observed_model_metadata": spec.metadata["observed_model_metadata"],
                            },
                        }
                    mock_disc.side_effect = fake_disc

                    ret = main([
                        "--model-id", "qwen2.5-7b-instruct",
                        "--family", "QWEN",
                        "--quantization", "Q4_K_M",
                        "--parameter-size", "7B",
                        "--context-length", "8192",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0

                    called_spec = mock_bench.call_args[1]["model_spec"]
                    # Verifica che quantization osservata NON sia stata convertita in stringa
                    obs_q = called_spec.metadata["observed_model_metadata"]["quantization"]
                    assert isinstance(obs_q, MappingProxyType)
                    assert dict(obs_q) == {"name": "Q4_K_M", "bits_per_weight": 4}
                    # Nessuna discrepanza
                    assert list(called_spec.metadata["metadata_discrepancies"]) == []

                    # Verifica che entrambi i file JSON siano stati scritti e leggibili
                    bench_json = tmp_path / "real_benchmark_qwen2.5-7b-instruct.json"
                    disc_json = tmp_path / "real_discovery_qwen2.5-7b-instruct.json"
                    assert bench_json.exists()
                    assert disc_json.exists()

                    with open(bench_json, "r", encoding="utf-8") as f:
                        bench_data = json.load(f)
                    assert bench_data["model_spec"]["observed_model_metadata"]["quantization"] == {
                        "name": "Q4_K_M",
                        "bits_per_weight": 4,
                    }

                    with open(disc_json, "r", encoding="utf-8") as f:
                        disc_data = json.load(f)
                    assert disc_data["model_spec"]["observed_model_metadata"]["quantization"] == {
                        "name": "Q4_K_M",
                        "bits_per_weight": 4,
                    }

    def test_lmstudio_dict_quantization_detects_real_mismatch(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-7b-instruct",)
            # Restituisce Q8_0 quando dichiarato è Q4_K_M
            mock_client.get_models_detailed.return_value = [{
                "id": "qwen2.5-7b-instruct",
                "quantization": {"name": "Q8_0", "bits_per_weight": 8},
                "params_string": "7B",
                "max_context_length": "8192",
            }]
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                mock_bench.return_value = {
                    "benchmark_mode": "REAL_MODEL_BENCHMARK",
                    "model_spec": {"model_id": "qwen2.5-7b-instruct"},
                    "results_by_strategy": {},
                }
                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    mock_disc.return_value = {"task": "OPEN_TOPIC_DISCOVERY"}

                    ret = main([
                        "--model-id", "qwen2.5-7b-instruct",
                        "--family", "QWEN",
                        "--quantization", "Q4_K_M",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0

                    called_spec = mock_bench.call_args[1]["model_spec"]
                    discrepancies = list(called_spec.metadata["metadata_discrepancies"])
                    assert len(discrepancies) == 1
                    assert "quantization: dichiarato='Q4_K_M'" in discrepancies[0]
                    assert "Q8_0" in discrepancies[0]

    def test_lmstudio_real_benchmark_context_separation_and_notices(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_LM_STUDIO_BENCHMARK", "1")
        with patch("ai.experiment.LmStudioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_client.list_models.return_value = ("qwen2.5-7b-instruct",)
            # Modello reale Qwen con max_context_length = 32768
            mock_client.get_models_detailed.return_value = [{
                "id": "qwen2.5-7b-instruct",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "params_string": "7B",
                "max_context_length": "32768",
            }]
            mock_client_cls.return_value = mock_client

            with patch("ai.experiment.run_synthetic_benchmark") as mock_bench:
                def fake_bench(*args, **kwargs):
                    spec = kwargs["model_spec"]
                    return {
                        "benchmark_mode": "REAL_MODEL_BENCHMARK",
                        "model_spec": {
                            "model_id": spec.model_id,
                            "family": spec.family.value,
                            "quantization": spec.quantization,
                            "parameter_size": spec.parameter_size,
                            "context_length": spec.context_length,
                            "runtime_context_length": spec.runtime_context_length,
                            "max_context_length": spec.max_context_length,
                            "declared_model_metadata": spec.metadata["declared_model_metadata"],
                            "observed_model_metadata": spec.metadata["observed_model_metadata"],
                            "metadata_discrepancies": spec.metadata["metadata_discrepancies"],
                        },
                        "timestamp": "2026-10-08T15:00:00Z",
                        "notice": "Campione sperimentale pilota non generalizzabile statisticamente. Benchmark eseguito tramite modello locale reale su LM Studio.",
                        "results_by_strategy": {},
                    }
                mock_bench.side_effect = fake_bench

                with patch("ai.experiment.run_synthetic_discovery_benchmark") as mock_disc:
                    def fake_disc(*args, **kwargs):
                        spec = kwargs["model_spec"]
                        return {
                            "benchmark_mode": "REAL_MODEL_BENCHMARK",
                            "task": "OPEN_TOPIC_DISCOVERY",
                            "model_spec": {
                                "model_id": spec.model_id,
                                "family": spec.family.value,
                                "quantization": spec.quantization,
                                "parameter_size": spec.parameter_size,
                                "context_length": spec.context_length,
                                "runtime_context_length": spec.runtime_context_length,
                                "max_context_length": spec.max_context_length,
                                "declared_model_metadata": spec.metadata["declared_model_metadata"],
                                "observed_model_metadata": spec.metadata["observed_model_metadata"],
                                "metadata_discrepancies": spec.metadata["metadata_discrepancies"],
                            },
                            "timestamp": "2026-10-08T15:00:00Z",
                            "notice": "Campione sperimentale pilota non generalizzabile statisticamente. Benchmark eseguito tramite modello locale reale su LM Studio.",
                            "metrics": {"attempted_runs": 4, "completed_runs": 4},
                            "runs": [],
                        }
                    mock_disc.side_effect = fake_disc

                    ret = main([
                        "--model-id", "qwen2.5-7b-instruct",
                        "--family", "QWEN",
                        "--quantization", "Q4_K_M",
                        "--parameter-size", "7B",
                        "--context-length", "8192",
                        "--output-dir", str(tmp_path),
                    ])
                    assert ret == 0

                    called_spec = mock_bench.call_args[1]["model_spec"]

                    # 1. Distinzione runtime_context_length (8192) vs max_context_length (32768)
                    assert called_spec.runtime_context_length == 8192
                    assert called_spec.context_length == 8192
                    assert called_spec.max_context_length == 32768
                    assert called_spec.metadata["declared_model_metadata"]["runtime_context_length"] == 8192
                    assert called_spec.metadata["observed_model_metadata"]["max_context_length"] == 32768

                    # 2. Nessuna discrepancy generata tra 8192 e 32768
                    assert list(called_spec.metadata["metadata_discrepancies"]) == []

                    # 3. Oggetti immutabili originali preservati
                    from types import MappingProxyType
                    assert isinstance(called_spec.metadata, MappingProxyType)
                    assert isinstance(called_spec.metadata["observed_model_metadata"]["quantization"], MappingProxyType)

                    # 4. JSON files scritti e leggibili con json.load
                    bench_json = tmp_path / "real_benchmark_qwen2.5-7b-instruct.json"
                    bench_md = tmp_path / "real_benchmark_qwen2.5-7b-instruct.md"
                    disc_json = tmp_path / "real_discovery_qwen2.5-7b-instruct.json"

                    assert bench_json.exists()
                    assert bench_md.exists()
                    assert disc_json.exists()

                    with open(bench_json, "r", encoding="utf-8") as f:
                        b_data = json.load(f)
                    assert b_data["model_spec"]["runtime_context_length"] == 8192
                    assert b_data["model_spec"]["max_context_length"] == 32768
                    assert b_data["model_spec"]["observed_model_metadata"]["quantization"] == {
                        "name": "Q4_K_M",
                        "bits_per_weight": 4,
                    }

                    with open(disc_json, "r", encoding="utf-8") as f:
                        d_data = json.load(f)
                    assert d_data["model_spec"]["runtime_context_length"] == 8192
                    assert d_data["model_spec"]["max_context_length"] == 32768

                    # 5. Markdown report mostra chiaramente runtime e max context separati e nota corretta
                    md_text = bench_md.read_text(encoding="utf-8")
                    assert "**Contesto Runtime Benchmark**: 8192 token" in md_text
                    assert "**Capacità Massima Modello (Max Context)**: 32768 token" in md_text
                    assert "Fake client" not in md_text
                    assert "Benchmark eseguito tramite modello locale reale su LM Studio." in md_text
                    assert "Campione sperimentale pilota non generalizzabile statisticamente." in md_text


