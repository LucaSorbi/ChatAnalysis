"""
tests/unit/test_ai_experiment.py
--------------------------------
Test unitari per il CLI Pilot Runner ai/experiment.py:
- Opt-in guard di sicurezza (RUN_LM_STUDIO_BENCHMARK o --force-run)
- Validazione della famiglia di modelli contro infer_model_family (Sezione G)
- Distinzione tra metadati dichiarati e osservati da get_models_detailed (Sezione H)
- Propagazione dei bug di programmazione senza mascheramento da except Exception (Sezione F)
"""
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

