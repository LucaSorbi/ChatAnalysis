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
from types import MappingProxyType
import json
from pathlib import Path
import pytest

from ai.backend import (
    AiModelMismatchError,
    FakeLocalLlmClient,
)
from ai.benchmark import (
    TopicDetectionMetrics,
    check_metadata_discrepancies,
    create_synthetic_benchmark_documents,
    metadata_values_match,
    run_synthetic_benchmark,
    run_synthetic_discovery_benchmark,
    save_benchmark_report,
    save_json_report,
    to_json_native,
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


@pytest.mark.unit
class TestBenchmarkSerializationAndMetadata:
    """Test per la serializzazione JSON robusta e la normalizzazione dei metadati."""

    def test_to_json_native_primitives_and_containers(self):
        # Primitivi e None
        assert to_json_native(None) is None
        assert to_json_native("string") == "string"
        assert to_json_native(42) == 42
        assert to_json_native(3.14) == 3.14
        assert to_json_native(True) is True

        # Enum e Path
        assert to_json_native(ModelFamily.QWEN) == "QWEN"
        assert to_json_native(Path("my/path")) == str(Path("my/path"))

        # Tuple e List ricorsive
        assert to_json_native((1, 2, "a")) == [1, 2, "a"]
        assert to_json_native([1, (2, 3), [4, 5]]) == [1, [2, 3], [4, 5]]

        # Set e Frozenset deterministici ordinati
        assert to_json_native(set([3, 1, 2])) == [1, 2, 3]
        assert to_json_native(frozenset(["c", "a", "b"])) == ["a", "b", "c"]

        # Mapping e MappingProxyType
        nested_mp = MappingProxyType({
            "num": 1,
            "sub": MappingProxyType({"tup": (10, 20)}),
            "tags": frozenset(["b", "a"]),
        })
        converted = to_json_native(nested_mp)
        assert converted == {
            "num": 1,
            "sub": {"tup": [10, 20]},
            "tags": ["a", "b"],
        }
        assert isinstance(converted, dict)
        assert isinstance(converted["sub"], dict)
        assert isinstance(converted["sub"]["tup"], list)
        assert isinstance(converted["tags"], list)

    def test_to_json_native_unsupported_type_raises_type_error(self):
        class NonSerializable:
            pass

        with pytest.raises(TypeError, match="is not JSON serializable"):
            to_json_native(NonSerializable())

    def test_to_json_native_source_remains_unmutated(self):
        inner_tuple = (1, 2, 3)
        inner_set = frozenset(["x", "y"])
        inner_mp = MappingProxyType({"tuple": inner_tuple, "set": inner_set})
        outer_mp = MappingProxyType({"inner": inner_mp})

        res = to_json_native(outer_mp)

        # Verifica che il risultato sia convertito in dict/list
        assert res == {"inner": {"tuple": [1, 2, 3], "set": ["x", "y"]}}
        # Verifica immutabilità della sorgente
        assert isinstance(outer_mp, MappingProxyType)
        assert isinstance(outer_mp["inner"], MappingProxyType)
        assert isinstance(outer_mp["inner"]["tuple"], tuple)
        assert isinstance(outer_mp["inner"]["set"], frozenset)

    def test_save_benchmark_report_with_frozen_metadata(self, tmp_path):
        # Costruisce spec con metadata profondamente congelati da freeze_structural
        declared_meta = {
            "quantization": "Q4_K_M",
            "parameter_size": "7B",
            "context_length": 8192,
        }
        observed_meta = {
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "parameter_size": "7B",
            "context_length": 8192,
        }
        discrepancies = []

        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen2.5-7b-instruct",
            quantization="Q4_K_M",
            parameter_size="7B",
            context_length=8192,
            metadata={
                "declared_model_metadata": declared_meta,
                "observed_model_metadata": observed_meta,
                "metadata_discrepancies": discrepancies,
            },
        )

        # Verifica che spec.metadata sia effettivamente MappingProxyType
        assert isinstance(spec.metadata, MappingProxyType)
        assert isinstance(spec.metadata["declared_model_metadata"], MappingProxyType)
        assert isinstance(spec.metadata["observed_model_metadata"], MappingProxyType)
        assert isinstance(spec.metadata["observed_model_metadata"]["quantization"], MappingProxyType)
        assert isinstance(spec.metadata["metadata_discrepancies"], tuple)

        client = FakeLocalLlmClient(
            models=("qwen2.5-7b-instruct",),
            canned_responses={
                ("Viaggi e Vacanze", "Firenze"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"], "rationale": "Viaggi"}',
                ("Lavoro e Riunioni", "budget"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_en_work_0::ORIGINAL_TEXT"], "rationale": "Lavoro"}',
                ("Cibo e Ristorazione", "margherita"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_es_food_0::ORIGINAL_TEXT"], "rationale": "Cibo"}',
                ("Sport e Fitness", "racchette"): '{"decision": "PRESENT", "evidence_ids": ["synth:synthetic_benchmark:scenario_mixed_sport_0::ORIGINAL_TEXT"], "rationale": "Sport"}',
            },
            default_response='{"decision": "ABSENT", "evidence_ids": [], "rationale": "No"}',
            model_name="qwen2.5-7b-instruct",
        )

        res = run_synthetic_benchmark(
            client=client,
            model_spec=spec,
            strategies=[AnalysisLanguageStrategy.DIRECT_MULTILINGUAL],
        )

        # Verifica che il benchmark result contenga MappingProxyType nei metadati
        assert isinstance(res["model_spec"]["declared_model_metadata"], MappingProxyType)
        assert isinstance(res["model_spec"]["observed_model_metadata"], MappingProxyType)

        # Salvataggio report
        json_file, md_file = save_benchmark_report(res, output_dir=tmp_path, filename_prefix="bench_frozen")
        assert json_file.exists()
        assert md_file.exists()

        # Verifica lettura JSON con json.load
        with open(json_file, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)

        assert loaded_data["model_spec"]["model_id"] == "qwen2.5-7b-instruct"
        assert loaded_data["model_spec"]["declared_model_metadata"] == {
            "quantization": "Q4_K_M",
            "parameter_size": "7B",
            "context_length": 8192,
        }
        assert loaded_data["model_spec"]["observed_model_metadata"] == {
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "parameter_size": "7B",
            "context_length": 8192,
        }
        assert loaded_data["model_spec"]["metadata_discrepancies"] == []

        # L'oggetto sorgente deve rimanere MappingProxyType
        assert isinstance(res["model_spec"]["declared_model_metadata"], MappingProxyType)

    def test_discovery_result_with_frozen_metadata_serialization(self, tmp_path):
        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen2.5-7b-instruct",
            quantization="Q4_K_M",
            metadata={
                "declared_model_metadata": {"quantization": "Q4_K_M"},
                "observed_model_metadata": {"quantization": {"name": "Q4_K_M", "bits_per_weight": 4}},
                "metadata_discrepancies": (),
            },
        )
        canned_disc = {
            ("OPEN TOPIC DISCOVERY", "scenario_it_travel"): '{"topics": [{"label": "Viaggi", "short_description": "Viaggi", "evidence_ids": ["synth:synthetic_benchmark:scenario_it_travel_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_en_work"): '{"topics": [{"label": "Lavoro", "short_description": "Lavoro", "evidence_ids": ["synth:synthetic_benchmark:scenario_en_work_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_es_food"): '{"topics": [{"label": "Cibo", "short_description": "Cibo", "evidence_ids": ["synth:synthetic_benchmark:scenario_es_food_0::ORIGINAL_TEXT"]}]}',
            ("OPEN TOPIC DISCOVERY", "scenario_mixed_sport"): '{"topics": [{"label": "Sport", "short_description": "Sport", "evidence_ids": ["synth:synthetic_benchmark:scenario_mixed_sport_0::ORIGINAL_TEXT"]}]}',
        }
        client = FakeLocalLlmClient(
            models=("qwen2.5-7b-instruct",),
            canned_responses=canned_disc,
            default_response='{"topics": []}',
            model_name="qwen2.5-7b-instruct",
        )

        disc_res = run_synthetic_discovery_benchmark(client=client, model_spec=spec)
        disc_file = tmp_path / "discovery.json"

        # Serializzazione con save_json_report
        saved_path = save_json_report(disc_res, disc_file)
        assert saved_path == disc_file
        assert disc_file.exists()

        with open(disc_file, "r", encoding="utf-8") as f:
            loaded_disc = json.load(f)

        assert loaded_disc["task"] == "OPEN_TOPIC_DISCOVERY"
        assert loaded_disc["model_spec"]["observed_model_metadata"]["quantization"] == {
            "name": "Q4_K_M",
            "bits_per_weight": 4,
        }

    def test_metadata_discrepancy_quantization_dict_match(self):
        # Valore dichiarato stringa, valore osservato dict con 'name'
        dec = {"quantization": "Q4_K_M"}
        obs = {"quantization": {"name": "Q4_K_M", "bits_per_weight": 4}}
        assert metadata_values_match("quantization", dec["quantization"], obs["quantization"]) is True
        assert check_metadata_discrepancies(dec, obs) == []

        # Anche con MappingProxyType
        obs_frozen = {"quantization": MappingProxyType({"name": "q4_k_m", "bits_per_weight": 4})}
        assert check_metadata_discrepancies(dec, obs_frozen) == []

    def test_metadata_discrepancy_quantization_mismatch(self):
        dec = {"quantization": "Q4_K_M"}
        obs = {"quantization": {"name": "Q8_0", "bits_per_weight": 8}}
        assert metadata_values_match("quantization", dec["quantization"], obs["quantization"]) is False
        disc = check_metadata_discrepancies(dec, obs)
        assert len(disc) == 1
        assert "quantization: dichiarato='Q4_K_M'" in disc[0]
        assert "osservato={'name': 'Q8_0', 'bits_per_weight': 8}" in disc[0]

    def test_metadata_discrepancy_parameter_size_equivalences(self):
        # Varianti equivalenti di 7B
        dec = {"parameter_size": "7B"}
        for equiv in ["7b", " 7B ", "7 B", "7.0B", "7.0b", {"params_string": "7B"}, {"name": "7.0B"}]:
            obs = {"parameter_size": equiv}
            assert metadata_values_match("parameter_size", dec["parameter_size"], equiv) is True
            assert check_metadata_discrepancies(dec, obs) == []

        # Discrepanze reali
        for mismatch in ["14B", "8B", "7M", "1.5B", {"params_string": "14B"}]:
            obs = {"parameter_size": mismatch}
            assert metadata_values_match("parameter_size", dec["parameter_size"], mismatch) is False
            disc = check_metadata_discrepancies(dec, obs)
            assert len(disc) == 1

    def test_metadata_discrepancy_context_length_equivalences(self):
        # Equivalenze numeriche di 8192
        dec = {"context_length": 8192}
        for equiv in [8192, "8192", 8192.0, " 8192 ", "8192.0", {"max_context_length": 8192}]:
            obs = {"context_length": equiv}
            assert metadata_values_match("context_length", dec["context_length"], equiv) is True
            assert check_metadata_discrepancies(dec, obs) == []

        # Discrepanze reali
        for mismatch in [4096, "4096", 32768, "32768"]:
            obs = {"context_length": mismatch}
            assert metadata_values_match("context_length", dec["context_length"], mismatch) is False
            disc = check_metadata_discrepancies(dec, obs)
            assert len(disc) == 1

    def test_direct_benchmark_data_with_frozen_metadata_roundtrip(self, tmp_path):
        from core.immutability import freeze_structural
        raw_meta = {
            "declared_model_metadata": {
                "quantization": "Q4_K_M",
                "parameter_size": "7B",
                "context_length": 8192,
            },
            "observed_model_metadata": {
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "parameter_size": "7B",
                "context_length": 8192,
            },
            "metadata_discrepancies": ["discrepancy_1"],
        }
        frozen_meta = freeze_structural(raw_meta)
        assert isinstance(frozen_meta, MappingProxyType)

        benchmark_data = {
            "benchmark_mode": "REAL_MODEL_BENCHMARK",
            "model_spec": {
                "family": "QWEN",
                "model_id": "qwen2.5-7b-instruct",
                "quantization": "Q4_K_M",
                "parameter_size": "7B",
                "context_length": 8192,
                "declared_model_metadata": frozen_meta["declared_model_metadata"],
                "observed_model_metadata": frozen_meta["observed_model_metadata"],
                "metadata_discrepancies": frozen_meta["metadata_discrepancies"],
            },
            "timestamp": "2026-10-08T12:00:00Z",
            "notice": "Test notice",
            "results_by_strategy": {},
        }

        json_path, md_path = save_benchmark_report(benchmark_data, output_dir=tmp_path, filename_prefix="direct_frozen")
        assert json_path.exists()
        assert md_path.exists()

        with open(json_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["model_spec"]["declared_model_metadata"] == {
            "quantization": "Q4_K_M",
            "parameter_size": "7B",
            "context_length": 8192,
        }
        assert loaded["model_spec"]["observed_model_metadata"] == {
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "parameter_size": "7B",
            "context_length": 8192,
        }
        assert loaded["model_spec"]["metadata_discrepancies"] == ["discrepancy_1"]

    def test_notice_fake_vs_real_benchmark(self, tmp_path):
        # 1. Benchmark con Fake client produce avviso sul fake client
        fake_spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen-fake",
            context_length=8192,
        )
        fake_client = FakeLocalLlmClient(model_name="qwen-fake")
        fake_bench_res = run_synthetic_benchmark(client=fake_client, model_spec=fake_spec)
        assert "Fake client" in fake_bench_res["notice"]
        assert "Campione sperimentale pilota non generalizzabile statisticamente." in fake_bench_res["notice"]

        _, fake_md = save_benchmark_report(fake_bench_res, output_dir=tmp_path, filename_prefix="fake_report")
        fake_md_text = fake_md.read_text(encoding="utf-8")
        assert "Fake client" in fake_md_text
        assert "Campione sperimentale pilota non generalizzabile statisticamente." in fake_md_text

        # 2. Benchmark con client reale / REAL_MODEL_BENCHMARK non contiene avviso fake ma LM Studio reale
        real_bench_data = {
            "benchmark_mode": "REAL_MODEL_BENCHMARK",
            "model_spec": {
                "family": "QWEN",
                "model_id": "qwen2.5-7b-instruct",
                "quantization": "Q4_K_M",
                "parameter_size": "7B",
                "runtime_context_length": 8192,
                "max_context_length": 32768,
                "declared_model_metadata": {
                    "quantization": "Q4_K_M",
                    "parameter_size": "7B",
                    "runtime_context_length": 8192,
                },
                "observed_model_metadata": {
                    "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                    "parameter_size": "7B",
                    "max_context_length": 32768,
                },
                "metadata_discrepancies": [],
            },
            "timestamp": "2026-10-08T15:00:00Z",
            "notice": "Campione sperimentale pilota non generalizzabile statisticamente. Benchmark eseguito tramite modello locale reale su LM Studio.",
            "results_by_strategy": {},
        }
        json_real, md_real = save_benchmark_report(real_bench_data, output_dir=tmp_path, filename_prefix="real_report")
        md_real_text = md_real.read_text(encoding="utf-8")
        assert "Fake client" not in md_real_text
        assert "Benchmark eseguito tramite modello locale reale su LM Studio." in md_real_text
        assert "Campione sperimentale pilota non generalizzabile statisticamente." in md_real_text

        # 3. Discovery con fake vs real notice
        fake_disc_res = run_synthetic_discovery_benchmark(client=fake_client, model_spec=fake_spec)
        assert "Fake client" in fake_disc_res["notice"]

    def test_context_distinction_runtime_vs_max_no_discrepancy(self):
        # runtime_context_length = 8192 e max_context_length = 32768 rappresentano grandezze diverse
        # Non devono essere considerate equivalenti e NON devono generare discrepancy
        declared = {
            "quantization": "Q4_K_M",
            "parameter_size": "7B",
            "runtime_context_length": 8192,
        }
        observed = {
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "parameter_size": "7B",
            "max_context_length": 32768,
        }
        discrepancies = check_metadata_discrepancies(declared, observed)
        assert discrepancies == []

    def test_runtime_context_real_discrepancy_detected(self):
        # Se invece LM Studio espone un runtime context diverso da quello dichiarato, deve rilevare discrepancy
        declared = {
            "runtime_context_length": 8192,
        }
        observed = {
            "observed_runtime_context_length": 4096,
            "max_context_length": 32768,
        }
        discrepancies = check_metadata_discrepancies(declared, observed)
        assert len(discrepancies) == 1
        assert "runtime_context_length" in discrepancies[0]
        assert "8192" in discrepancies[0]
        assert "4096" in discrepancies[0]

    def test_markdown_report_displays_runtime_and_max_context_separately(self, tmp_path):
        from core.immutability import freeze_structural
        spec_meta = freeze_structural({
            "declared_model_metadata": {
                "quantization": "Q4_K_M",
                "parameter_size": "7B",
                "runtime_context_length": 8192,
            },
            "observed_model_metadata": {
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "parameter_size": "7B",
                "max_context_length": 32768,
            },
            "metadata_discrepancies": [],
        })

        spec = ExperimentModelSpec(
            family=ModelFamily.QWEN,
            model_id="qwen2.5-7b-instruct",
            quantization="Q4_K_M",
            parameter_size="7B",
            runtime_context_length=8192,
            max_context_length=32768,
            metadata=spec_meta,
        )

        assert spec.runtime_context_length == 8192
        assert spec.context_length == 8192
        assert spec.max_context_length == 32768

        bench_data = {
            "benchmark_mode": "REAL_MODEL_BENCHMARK",
            "model_spec": {
                "family": spec.family.value,
                "model_id": spec.model_id,
                "quantization": spec.quantization,
                "parameter_size": spec.parameter_size,
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

        json_path, md_path = save_benchmark_report(bench_data, output_dir=tmp_path, filename_prefix="ctx_report")
        assert json_path.exists()
        assert md_path.exists()

        md_content = md_path.read_text(encoding="utf-8")
        assert "**Contesto Runtime Benchmark**: 8192 token" in md_content
        assert "**Capacità Massima Modello (Max Context)**: 32768 token" in md_content
        assert "Quant: `Q4_K_M` (4-bit)" in md_content
        assert "Fake client" not in md_content
        assert "Benchmark eseguito tramite modello locale reale su LM Studio." in md_content

        with open(json_path, "r", encoding="utf-8") as f:
            loaded_json = json.load(f)
        assert loaded_json["model_spec"]["runtime_context_length"] == 8192
        assert loaded_json["model_spec"]["max_context_length"] == 32768
        assert loaded_json["model_spec"]["declared_model_metadata"]["runtime_context_length"] == 8192
        assert loaded_json["model_spec"]["observed_model_metadata"]["max_context_length"] == 32768
        assert loaded_json["model_spec"]["observed_model_metadata"]["quantization"] == {
            "name": "Q4_K_M",
            "bits_per_weight": 4,
        }





