"""
tests/unit/test_ai_benchmark.py
-------------------------------
Test unitari per l'harness di benchmark multilingue sintetico (ai/benchmark.py),
l'audit hardware (ai/hardware.py) e la scoperta modelli (ai/discovery.py):
- Creazione e validità degli scenari multilingue
- Calcolo metriche statistiche con denominator trasparenti (Completion Rate, Decision Accuracy)
- Identità del modello derivata da ExperimentModelSpec e rilevamento mismatch
- Benchmark dedicato Open Topic Discovery
- Latenza end-to-end comprensiva di traduzione per TRANSLATE_FIRST
- Rilevamento hardware non-privilegiato e categorizzazione famiglie di modelli (UNVERIFIED se offline)
"""
import json
from pathlib import Path
import pytest

from ai.backend import (
    AiModelMismatchError,
    FakeLocalLlmClient,
)
from ai.benchmark import (
    TopicDetectionMetrics,
    create_synthetic_benchmark_documents,
    run_synthetic_benchmark,
    run_synthetic_discovery_benchmark,
    save_benchmark_report,
)
from ai.discovery import (
    discover_models_on_client,
    infer_model_family,
)
from ai.hardware import (
    HardwareProfile,
    probe_local_hardware,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ExperimentModelSpec,
    ModelFamily,
    TopicDecision,
)


@pytest.mark.unit
class TestAiBenchmarkAndDiscovery:

    def test_create_synthetic_benchmark_documents(self):
        scenarios = create_synthetic_benchmark_documents()
        assert len(scenarios) == 4

        languages = {sc["lang"] for sc in scenarios}
        assert languages == {"it", "en", "es", "mixed"}

        for sc in scenarios:
            assert sc["document"].message_count >= 2
            assert len(sc["tests"]) >= 2
            decisions = {dec for _, dec in sc["tests"]}
            assert TopicDecision.PRESENT in decisions
            assert TopicDecision.ABSENT in decisions

    def test_topic_detection_metrics_calculation(self):
        m = TopicDetectionMetrics()
        m.attempted_queries = 10
        m.completed_valid_queries = 8
        m.failed_queries = 2
        m.correct_decisions = 6
        m.true_positives = 3
        m.false_positives = 1
        m.true_negatives = 3
        m.false_negatives = 1
        m.invalid_evidence_references = 1
        m.total_end_to_end_latency_seconds = 2.0

        assert m.completion_rate == 0.8
        assert m.decision_accuracy == round(6 / 8, 4)
        assert m.precision == round(3 / 4, 4)
        assert m.recall == round(3 / 4, 4)
        assert m.f1_score == round(3 / 4, 4)
        assert m.invalid_evidence_reference_rate == 0.1
        assert m.average_end_to_end_latency == 0.25

    def test_run_synthetic_benchmark_with_model_spec(self, tmp_path: Path):
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-1.5b",
            quantization="Q4_K_M",
        )

        canned = {
            ("Viaggi e Vacanze", "Firenze"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Viaggio trovato"}',
            ("Lavoro e Riunioni", "budget"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_en_work_0::ORIGINAL_TEXT"], "rationale": "Lavoro trovato"}',
            ("Cibo e Ristorazione", "margherita"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_es_food_0::ORIGINAL_TEXT"], "rationale": "Ristorante trovato"}',
            ("Sport e Fitness", "racchette"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_mixed_sport_0::ORIGINAL_TEXT"], "rationale": "Sport trovato"}',
        }
        client = FakeLocalLlmClient(
            models=("qwen-2.5-1.5b",),
            canned_responses=canned,
            default_response='{"decision": "ABSENT", "evidence_ids": [], "rationale": "Tema assente"}',
            model_name="qwen-2.5-1.5b",
        )

        res = run_synthetic_benchmark(
            client=client,
            model_spec=spec,
            strategies=(AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,),
        )

        assert res["benchmark_mode"] == "FAKE_HARNESS_VALIDATION"
        assert res["model_spec"]["model_id"] == "qwen-2.5-1.5b"
        assert res["model_spec"]["family"] == "QWEN"

        strat_res = res["results_by_strategy"]["DIRECT_MULTILINGUAL"]
        metrics = strat_res["metrics"]
        assert metrics["attempted_queries"] == 8
        assert metrics["completed_valid_queries"] == 8
        assert metrics["completion_rate"] == 1.0
        assert metrics["accuracy"] >= 0.75

        # Verifica ripartizione per lingua
        assert "it" in res["results_by_language"]
        assert "en" in res["results_by_language"]

        # Test salvataggio file
        json_file, md_file = save_benchmark_report(res, output_dir=tmp_path)
        assert json_file.exists()
        assert md_file.exists()
        assert "Benchmark Sperimentale AI Locale" in md_file.read_text(encoding="utf-8")

    def test_run_synthetic_discovery_benchmark(self):
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-1.5b",
        )
        canned_disc = {
            ("OPEN TOPIC DISCOVERY", "scenario_it_travel"): '{"topics": [{"label": "Viaggi", "short_description": "Viaggi a Firenze", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_en_work"): '{"topics": [{"label": "Lavoro", "short_description": "Meeting e budget", "evidence_ids": ["synth:synthetic_benchmark:scenario_en_work_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_es_food"): '{"topics": [{"label": "Ristorazione", "short_description": "Cena e pizza", "evidence_ids": ["synth:synthetic_benchmark:scenario_es_food_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_mixed_sport"): '{"topics": [{"label": "Sport", "short_description": "Partita di tennis", "evidence_ids": ["synth:synthetic_benchmark:scenario_mixed_sport_0::ORIGINAL_TEXT"]}]}',
        }
        client = FakeLocalLlmClient(
            canned_responses=canned_disc,
            default_response='{"topics": []}',
            model_name="qwen-2.5-1.5b",
        )

        res = run_synthetic_discovery_benchmark(client=client, model_spec=spec)
        assert res["task"] == "OPEN_TOPIC_DISCOVERY"
        assert res["metrics"]["attempted_runs"] == 4
        assert res["metrics"]["completed_runs"] == 4
        assert res["metrics"]["structured_validity_rate"] == 1.0

    def test_infer_model_family(self):
        assert infer_model_family("qwen2.5-7b-instruct-q4_k_m.gguf") == ModelFamily.QWEN
        assert infer_model_family("Meta-Llama-3.1-8B-Instruct-GGUF") == ModelFamily.LLAMA
        assert infer_model_family("deepseek-r1-distill-qwen-1.5b") == ModelFamily.DEEPSEEK
        assert infer_model_family("mistral-7b-instruct-v0.3") == ModelFamily.OTHER

    def test_discover_models_on_fake_client_verified(self):
        client = FakeLocalLlmClient(
            models=("qwen2.5-1.5b", "llama-3.2-3b", "custom-model"),
        )
        report = discover_models_on_client(client)

        assert report.server_available is True
        assert report.status == "VERIFIED"
        assert len(report.available_models) == 3
        assert ModelFamily.QWEN in report.detected_families
        assert ModelFamily.LLAMA in report.detected_families
        assert ModelFamily.DEEPSEEK in report.missing_families

    def test_discover_models_on_unavailable_server_returns_unverified(self):
        client = FakeLocalLlmClient(available=False)
        report = discover_models_on_client(client)

        # F1: Server non disponibile -> UNVERIFIED e missing_families vuota (non falsamente marcate come assenti)
        assert report.server_available is False
        assert report.status == "UNVERIFIED"
        assert report.available_models == ()
        assert report.detected_families == ()
        assert report.missing_families == ()

    def test_probe_local_hardware(self):
        profile = probe_local_hardware()
        assert isinstance(profile, HardwareProfile)
        assert profile.total_ram_gb > 0.0
        assert profile.python_version != ""
        assert profile.architecture != ""

        tiers = profile.recommended_model_tiers()
        assert len(tiers) >= 2

    def test_classify_benchmark_error_with_ai_backend_timeout_error(self):
        from ai.backend import AiBackendTimeoutError
        from ai.benchmark import _classify_benchmark_error

        err = AiBackendTimeoutError("Richiesta scaduta per timeout")
        assert _classify_benchmark_error(err) == "BACKEND_TIMEOUT"

    def test_run_synthetic_benchmark_complete_per_run_metadata(self):
        # Sezioni I e J: Verifica metadati completi per ciascun run e assenza di prompt/chat/segreti
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-1.5b",
            quantization="Q4_K_M",
            parameter_size="1.5B",
            context_length=4096,
        )
        canned = {
            ("Viaggi e Vacanze", "Firenze"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Viaggio a Firenze"}',
        }
        client = FakeLocalLlmClient(
            canned_responses=canned,
            default_response='{"decision": "ABSENT", "evidence_ids": [], "rationale": "Non rilevato"}',
            model_name="qwen-2.5-1.5b",
        )
        res = run_synthetic_benchmark(
            client=client,
            model_spec=spec,
            strategies=(AnalysisLanguageStrategy.TRANSLATE_FIRST,),
        )
        runs = res["results_by_strategy"]["TRANSLATE_FIRST"]["runs"]
        assert len(runs) > 0
        first_run = runs[0]

        # Verifica campi obbligatori per Sezione I
        required_keys = [
            "benchmark_mode", "backend", "model_id", "family", "quantization",
            "parameter_size", "context_length", "scenario_id", "language",
            "topic_id", "language_strategy", "prompt_version", "temperature",
            "seed", "timeout_seconds", "max_tokens", "prompt_tokens",
            "completion_tokens", "total_tokens", "analysis_latency_seconds",
            "translation_latency_seconds", "cold_end_to_end_latency_seconds",
            "amortized_end_to_end_latency_seconds", "total_end_to_end_latency_seconds",
            "structured_output_valid", "status", "error_type",
        ]
        for k in required_keys:
            assert k in first_run, f"Campo obbligatorio mancante nel run: {k}"

        # Verifica divieto Sezione I: nessun prompt completo, testo chat, o translation completa
        forbidden_keys = ["prompt", "system_prompt", "chat_text", "full_text", "full_translation", "api_key", "secret"]
        for fk in forbidden_keys:
            assert fk not in first_run, f"Campo vietato presente nel run: {fk}"

        # Verifica Sezione J: distinzione cold vs amortized latency
        assert first_run["cold_end_to_end_latency_seconds"] >= first_run["amortized_end_to_end_latency_seconds"]

    def test_run_synthetic_discovery_benchmark_completeness(self):
        # Sezione K & Requisiti 8, 9, 10: metriche per-run per Open Topic Discovery e token usage completi
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-1.5b",
        )
        # scenario_it_travel produce errore INVALID_EVIDENCE_REFERENCE perché "invalid_id_999" non esiste
        canned_disc = {
            ("OPEN TOPIC DISCOVERY", "scenario_it_travel"): '{"topics": [{"label": "Viaggi", "short_description": "Viaggi", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT", "invalid_id_999"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_en_work"): '{"topics": [{"label": "Lavoro", "short_description": "Meeting", "evidence_ids": ["synth:synthetic_benchmark:scenario_en_work_0::ORIGINAL_TEXT"]}]}',
        }
        client = FakeLocalLlmClient(
            canned_responses=canned_disc,
            default_response='{"topics": []}',
            model_name="qwen-2.5-1.5b",
        )
        res = run_synthetic_discovery_benchmark(client=client, model_spec=spec, max_tokens=128)

        metrics = res["metrics"]
        assert "invalid_evidence_reference_failure_count" in metrics
        assert "evidence_reference_valid_run_rate" in metrics
        assert "structured_output_failure_count" in metrics
        assert "model_mismatch_count" in metrics
        assert "error_counts" in metrics
        assert res["manual_review_status"] == "NOT_REVIEWED"
        assert res["benchmark_mode"] == "FAKE_HARNESS_VALIDATION"

        # 1 run su 4 ha fallito per INVALID_EVIDENCE_REFERENCE ("invalid_id_999")
        assert metrics["invalid_evidence_reference_failure_count"] >= 1
        assert metrics["evidence_reference_valid_run_rate"] < 1.0

        # Verifica token metadata completi nel run riuscito (Requisito 9 & 10)
        successful_runs = [r for r in res["runs"] if r["status"] == "SUCCESS"]
        assert len(successful_runs) > 0
        first_success = successful_runs[0]
        assert first_success["max_tokens"] == 128
        assert first_success["prompt_tokens"] == 10
        assert first_success["completion_tokens"] == 20
        assert first_success["total_tokens"] == 30
        assert first_success["token_usage"]["prompt_tokens"] == 10
        assert first_success["token_usage"]["completion_tokens"] == 20
        assert first_success["token_usage"]["total_tokens"] == 30

    def test_max_tokens_and_timeout_propagated_to_fake_client_and_detailed_runs(self):
        # Requisito 10: max_tokens e timeout propagati a BaseLocalLlmClient e registrati nei detailed_runs
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-2.5-1.5b",
        )
        client = FakeLocalLlmClient(
            default_response='{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Presente"}',
            model_name="qwen-2.5-1.5b",
        )
        res = run_synthetic_benchmark(
            client=client,
            model_spec=spec,
            strategies=(AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,),
            timeout_seconds=42.5,
            max_tokens=256,
        )
        # Verifica che il client abbia ricevuto max_tokens e timeout_seconds
        assert len(client.call_history) > 0
        assert client.call_history[0]["max_tokens"] == 256
        assert client.call_history[0]["timeout_seconds"] == 42.5

        # Verifica che il detailed_run contenga max_tokens
        runs = res["results_by_strategy"]["DIRECT_MULTILINGUAL"]["detailed_runs"]
        assert runs[0]["max_tokens"] == 256
        assert runs[0]["timeout_seconds"] == 42.5
        assert runs[0]["prompt_tokens"] == 10
        assert runs[0]["completion_tokens"] == 20
        assert runs[0]["total_tokens"] == 30

    def test_analyzers_and_translator_propagate_max_tokens_and_token_usage(self):
        # Requisiti 4, 5, 6: TopicDetection, TopicDiscovery e Translation salvano prompt_tokens, completion_tokens, total_tokens
        from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer
        from ai.translation import EvidenceTranslator
        from ai.models import TopicQuery

        client = FakeLocalLlmClient(
            default_response='{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Presente"}',
            model_name="qwen-2.5-1.5b",
        )
        scenarios = create_synthetic_benchmark_documents()
        doc = scenarios[0]["document"]

        # 1. Topic Detection
        analyzer_det = TopicDetectionAnalyzer(client=client, default_model_id="qwen-2.5-1.5b")
        query = TopicQuery(topic_id="top_travel", label="Viaggi", description="Vacanze")
        res_det = analyzer_det.detect_topic(document=doc, topic=query, max_tokens=128)
        assert res_det.metadata["prompt_tokens"] == 10
        assert res_det.metadata["completion_tokens"] == 20
        assert res_det.metadata["total_tokens"] == 30
        assert res_det.metadata["max_tokens"] == 128

        # 2. Topic Discovery
        client_disc = FakeLocalLlmClient(
            default_response='{"topics": [{"label": "Viaggi", "short_description": "Vacanze", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"]}]}',
            model_name="qwen-2.5-1.5b",
        )
        analyzer_disc = TopicDiscoveryAnalyzer(client=client_disc, default_model_id="qwen-2.5-1.5b")
        res_disc = analyzer_disc.discover_topics(document=doc, max_tokens=128)
        assert res_disc.metadata["prompt_tokens"] == 10
        assert res_disc.metadata["completion_tokens"] == 20
        assert res_disc.metadata["total_tokens"] == 30
        assert res_disc.metadata["max_tokens"] == 128

        # 3. Translation
        import json
        tr_items = [{"evidence_id": s.evidence_id, "translated_text": f"Tradotto {s.evidence_id}"} for s in doc.all_evidence_sections]
        client_tr = FakeLocalLlmClient(
            default_response=json.dumps({"translations": tr_items}),
            model_name="qwen-2.5-1.5b",
        )
        translator = EvidenceTranslator(client=client_tr, default_model_id="qwen-2.5-1.5b")
        res_tr = translator.translate_document(document=doc, max_tokens=128)
        assert res_tr.metadata["prompt_tokens"] == 10
        assert res_tr.metadata["completion_tokens"] == 20
        assert res_tr.metadata["total_tokens"] == 30
        assert res_tr.metadata["max_tokens"] == 128

    def test_token_usage_absent_yields_none_metadata(self):
        # Requisito 6: se usage è assente (None), i campi nei metadati devono essere None
        from ai.topics import TopicDetectionAnalyzer
        from ai.models import TopicQuery
        from ai.backend import LlmCompletionResponse

        class ClientWithoutUsage(FakeLocalLlmClient):
            def chat_completion(self, *args, **kwargs):
                return LlmCompletionResponse(
                    content='{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Presente"}',
                    model="qwen-2.5-1.5b",
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                )

        client = ClientWithoutUsage(model_name="qwen-2.5-1.5b")
        scenarios = create_synthetic_benchmark_documents()
        doc = scenarios[0]["document"]
        analyzer = TopicDetectionAnalyzer(client=client, default_model_id="qwen-2.5-1.5b")
        res = analyzer.detect_topic(document=doc, topic=TopicQuery("top", "Label", "Desc"))

        assert res.metadata["prompt_tokens"] is None
        assert res.metadata["completion_tokens"] is None
        assert res.metadata["total_tokens"] is None



