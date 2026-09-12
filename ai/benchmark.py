"""
ai/benchmark.py
---------------
Harness di benchmark sperimentale multilingue non sensibile e calcolo delle metriche di analisi.

Principi architetturali (Fasi J, K):
1. INTEGRITÀ SCIENTIFICA DEL MODELLO (FASE J1, A3):
   Il benchmark accetta una ExperimentModelSpec formale e valida che il backend risponda con il medesimo model_id.
2. DIMENSIONI SPERIMENTALI RIGOROSE (FASE J2):
   Metriche aggregate per: MODEL x LANGUAGE x STRATEGY (overall, by_language, by_strategy).
3. CONFRONTO EQUO DI LATENZA (FASE J3):
   Per TRANSLATE_FIRST misura separatamente translation_latency e analysis_latency;
   il confronto principale impiega la total_end_to_end_latency.
4. TASSONOMIA DEGLI ERRORI E DENOMINATORI TRASPARENTI (FASI J4, J5, J6):
   Distingue BACKEND_UNAVAILABLE, TIMEOUT, PROTOCOL, STRUCTURED_OUTPUT_INVALID,
   INVALID_EVIDENCE_REFERENCE, TRANSLATION_FAILED, MODEL_MISMATCH.
   Riporta attempted_queries, completed_valid_queries, failed_queries e completion_rate.
   Bug imprevisti propagano durante l'esecuzione.
5. BENCHMARK OPEN TOPIC DISCOVERY SEPARATO (FASE J7):
   Valutazione dedicata della validità strutturale e delle evidenze per Topic Discovery.
6. METADATI COMPLETI E DISTINZIONE MODALITÀ (FASI J8, J9, K):
   Timestamp ISO runtime, etichettatura FAKE_HARNESS_VALIDATION vs REAL_MODEL_BENCHMARK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Mapping

from ai.backend import (
    AiBackendError,
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiBackendUnavailableError,
    AiModelMismatchError,
    AiModelNotSpecifiedError,
    AiStructuredOutputError,
    BaseLocalLlmClient,
    FakeLocalLlmClient,
    LmStudioUnavailableError,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    EvidenceTranslationResult,
    ExperimentModelSpec,
    ModelFamily,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer
from ai.translation import EvidenceTranslator
from importer.models import RawRecord
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_synthetic_message(
    msg_id: str,
    text: str,
    source_name: str = "synthetic_benchmark",
) -> UnifiedMessage:
    raw = RawRecord(
        source_name=source_name,
        source_path="benchmark/synthetic",
        source_record_id=msg_id,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name=source_name,
        source_record_id=msg_id,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    return UnifiedMessage(
        message_id=f"synth:{source_name}:{msg_id}",
        source_name=source_name,
        source_record_id=msg_id,
        source_path="benchmark/synthetic",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )


def create_synthetic_benchmark_documents() -> list[dict[str, Any]]:
    """
    Costruisce una suite di scenari sintetici multilingue per il benchmark sperimentale.
    Include campioni in IT, EN, ES e misto con ground truth per topic positivi e negativi.
    """
    topic_viaggio = TopicQuery(
        topic_id="top_travel",
        label="Viaggi e Vacanze",
        description="Pianificazione di viaggi, voli, vacanze, hotel o spostamenti turistici.",
    )
    topic_lavoro = TopicQuery(
        topic_id="top_work",
        label="Lavoro e Riunioni",
        description="Incontri di lavoro, deadline di progetti aziendali o riunioni professionali.",
    )
    topic_ristorante = TopicQuery(
        topic_id="top_food",
        label="Cibo e Ristorazione",
        description="Prenotazioni al ristorante, cene, pranzi o pietanze alimentari.",
    )
    topic_sport = TopicQuery(
        topic_id="top_sport",
        label="Sport e Fitness",
        description="Partite di calcio, tennis, allenamenti in palestra o gare sportive.",
    )

    scenarios = [
        # 1. Italiano - Viaggio (Positivo per viaggio, negativo per sport)
        {
            "id": "scenario_it_travel",
            "lang": "it",
            "messages": [
                "Ciao Marco, hai prenotato l'hotel per il weekend a Firenze?",
                "Sì, ho trovato una camera vicino alla stazione di Santa Maria Novella.",
                "Perfetto! Prendiamo il treno ad alta velocità delle 8:30 di sabato mattina.",
            ],
            "tests": [
                (topic_viaggio, TopicDecision.PRESENT),
                (topic_sport, TopicDecision.ABSENT),
            ],
        },
        # 2. English - Work & Meetings (Positive work, negative food)
        {
            "id": "scenario_en_work",
            "lang": "en",
            "messages": [
                "Good morning team, please remember our quarterly review meeting at 2 PM.",
                "I have prepared the presentation slides and updated the budget spreadsheet.",
                "Great! Let's ensure the client deliverables are finalized before tomorrow's deadline.",
            ],
            "tests": [
                (topic_lavoro, TopicDecision.PRESENT),
                (topic_ristorante, TopicDecision.ABSENT),
            ],
        },
        # 3. Spanish - Restaurant & Food (Positive food, negative travel)
        {
            "id": "scenario_es_food",
            "lang": "es",
            "messages": [
                "¡Hola Carlos! ¿Reservaste la mesa para cenar esta noche?",
                "Sí, reservé en el restaurante italiano del centro a las nueve y media.",
                "Estupendo, quiero probar la pizza margherita y la pasta fresca.",
            ],
            "tests": [
                (topic_ristorante, TopicDecision.PRESENT),
                (topic_viaggio, TopicDecision.ABSENT),
            ],
        },
        # 4. Mixed Language - Sport (Positive sport, negative work)
        {
            "id": "scenario_mixed_sport",
            "lang": "mixed",
            "messages": [
                "Are you ready for the tennis match tomorrow afternoon?",
                "Certo! Porto io le racchette nuove e le palline.",
                "¡Perfecto! Nos vemos en la pista a las cinco.",
            ],
            "tests": [
                (topic_sport, TopicDecision.PRESENT),
                (topic_lavoro, TopicDecision.ABSENT),
            ],
        },
    ]

    benchmark_data = []
    for sc in scenarios:
        bundles = []
        for idx, text in enumerate(sc["messages"]):
            msg = _create_synthetic_message(f"{sc['id']}_{idx}", text)
            bundles.append(MessageEvidenceBundle(message=msg))

        doc = ConversationEvidenceDocument(
            document_id=f"doc::{sc['id']}",
            bundles=tuple(bundles),
            chat_id=sc["id"],
            source_name="synthetic_benchmark",
        )
        benchmark_data.append({
            "id": sc["id"],
            "lang": sc["lang"],
            "document": doc,
            "tests": sc["tests"],
        })

    return benchmark_data


@dataclass
class TopicDetectionMetrics:
    attempted_queries: int = 0
    completed_valid_queries: int = 0
    failed_queries: int = 0
    correct_decisions: int = 0
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0
    uncertain_count: int = 0
    structured_output_failures: int = 0
    invalid_evidence_references: int = 0
    error_counts: dict[str, int] = field(default_factory=dict)
    total_analysis_latency_seconds: float = 0.0
    total_translation_latency_seconds: float = 0.0
    scenario_translation_stage_latency_seconds: float = 0.0
    total_cold_end_to_end_latency_seconds: float = 0.0
    total_amortized_end_to_end_latency_seconds: float = 0.0
    total_end_to_end_latency_seconds: float = 0.0

    @property
    def completion_rate(self) -> float:
        return round(self.completed_valid_queries / self.attempted_queries, 4) if self.attempted_queries else 0.0

    @property
    def decision_accuracy(self) -> float:
        return round(self.correct_decisions / self.completed_valid_queries, 4) if self.completed_valid_queries else 0.0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return round(self.true_positives / denom, 4) if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return round(self.true_positives / denom, 4) if denom else 0.0

    @property
    def f1_score(self) -> float:
        p = self.precision
        r = self.recall
        return round(2 * (p * r) / (p + r), 4) if (p + r) else 0.0

    @property
    def invalid_evidence_reference_rate(self) -> float:
        return round(self.invalid_evidence_references / self.attempted_queries, 4) if self.attempted_queries else 0.0

    @property
    def average_cold_end_to_end_latency_seconds(self) -> float:
        return round(self.total_cold_end_to_end_latency_seconds / self.completed_valid_queries, 3) if self.completed_valid_queries else 0.0

    @property
    def average_amortized_end_to_end_latency_seconds(self) -> float:
        return round(self.total_amortized_end_to_end_latency_seconds / self.completed_valid_queries, 3) if self.completed_valid_queries else 0.0

    @property
    def average_end_to_end_latency(self) -> float:
        if self.total_amortized_end_to_end_latency_seconds > 0:
            return round(self.total_amortized_end_to_end_latency_seconds / self.completed_valid_queries, 3) if self.completed_valid_queries else 0.0
        return round(self.total_end_to_end_latency_seconds / self.completed_valid_queries, 3) if self.completed_valid_queries else 0.0

    def record_error(self, err_type: str) -> None:
        self.failed_queries += 1
        self.error_counts[err_type] = self.error_counts.get(err_type, 0) + 1


def _classify_benchmark_error(exc: Exception) -> str:
    """Classifica l'eccezione nella tassonomia formale del benchmark."""
    if isinstance(exc, AiBackendTimeoutError):
        return "BACKEND_TIMEOUT"
    if isinstance(exc, LmStudioUnavailableError):
        return "BACKEND_UNAVAILABLE"
    if isinstance(exc, TimeoutError):
        return "BACKEND_TIMEOUT"
    if isinstance(exc, AiBackendProtocolError):
        return "BACKEND_PROTOCOL"
    if isinstance(exc, AiBackendRequestError):
        return "BACKEND_REQUEST_ERROR"
    if isinstance(exc, AiModelMismatchError):
        return "MODEL_MISMATCH"
    if isinstance(exc, AiModelNotSpecifiedError):
        return "MODEL_NOT_SPECIFIED"
    if isinstance(exc, AiStructuredOutputError):
        msg = str(exc).lower()
        if "allucinat" in msg or "inesistent" in msg:
            return "INVALID_EVIDENCE_REFERENCE"
        return "STRUCTURED_OUTPUT_INVALID"
    # Bug o eccezioni non previste propagano (non mascherate)
    raise exc


def run_synthetic_benchmark(
    client: BaseLocalLlmClient,
    model_spec: ExperimentModelSpec,
    strategies: tuple[AnalysisLanguageStrategy, ...] = (
        AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        AnalysisLanguageStrategy.TRANSLATE_FIRST,
    ),
    temperature: float = 0.0,
    seed: int | None = 42,
    timeout_seconds: float = 30.0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """
    Esegue il benchmark sintetico controllato su una o più strategie linguistiche.
    Valida l'identità del modello contro ExperimentModelSpec e categorizza fedelmente errori e latenze.
    """
    is_fake = isinstance(client, FakeLocalLlmClient)
    benchmark_mode = "FAKE_HARNESS_VALIDATION" if is_fake else "REAL_MODEL_BENCHMARK"
    runtime_timestamp = datetime.now(timezone.utc).isoformat()

    benchmark_scenarios = create_synthetic_benchmark_documents()
    analyzer = TopicDetectionAnalyzer(client=client, default_model_id=model_spec.model_id)
    translator = EvidenceTranslator(client=client, default_model_id=model_spec.model_id)

    results_by_strategy: dict[str, Any] = {}
    by_language_data: dict[str, dict[str, TopicDetectionMetrics]] = {}

    for strat in strategies:
        strat_metrics = TopicDetectionMetrics()
        detailed_runs: list[dict[str, Any]] = []

        for sc in benchmark_scenarios:
            lang = sc["lang"]
            if lang not in by_language_data:
                by_language_data[lang] = {}
            if strat.value not in by_language_data[lang]:
                by_language_data[lang][strat.value] = TopicDetectionMetrics()
            lang_metrics = by_language_data[lang][strat.value]

            doc: ConversationEvidenceDocument = sc["document"]
            translation_res: EvidenceTranslationResult | None = None
            trans_latency_sec = 0.0

            # 1. Fase di traduzione preliminare se TRANSLATE_FIRST
            if strat == AnalysisLanguageStrategy.TRANSLATE_FIRST:
                tr_start = time.perf_counter()
                try:
                    translation_res = translator.translate_document(
                        doc,
                        model_id=model_spec.model_id,
                        target_language="it",
                        temperature=temperature,
                        seed=seed,
                        timeout_seconds=timeout_seconds,
                        max_tokens=max_tokens,
                    )
                    trans_latency_sec = round(time.perf_counter() - tr_start, 3)
                    strat_metrics.scenario_translation_stage_latency_seconds += trans_latency_sec
                    lang_metrics.scenario_translation_stage_latency_seconds += trans_latency_sec
                except Exception as e:
                    err_cls = _classify_benchmark_error(e)
                    for query, _ in sc["tests"]:
                        strat_metrics.attempted_queries += 1
                        strat_metrics.record_error("TRANSLATION_FAILED")
                        lang_metrics.attempted_queries += 1
                        lang_metrics.record_error("TRANSLATION_FAILED")
                        detailed_runs.append({
                            "benchmark_mode": benchmark_mode,
                            "backend": "fake_local" if is_fake else "lm_studio",
                            "model_id": model_spec.model_id,
                            "family": model_spec.family.value,
                            "quantization": model_spec.quantization,
                            "parameter_size": model_spec.parameter_size,
                            "context_length": model_spec.context_length,
                            "scenario_id": sc["id"],
                            "language": sc["lang"],
                            "topic_id": query.topic_id,
                            "language_strategy": strat.value,
                            "prompt_version": "topic_detection_v1",
                            "temperature": temperature,
                            "seed": seed,
                            "timeout_seconds": timeout_seconds,
                            "max_tokens": max_tokens,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "analysis_latency_seconds": 0.0,
                            "translation_latency_seconds": 0.0,
                            "cold_end_to_end_latency_seconds": 0.0,
                            "amortized_end_to_end_latency_seconds": 0.0,
                            "total_end_to_end_latency_seconds": 0.0,
                            "structured_output_valid": False,
                            "status": "FAILED",
                            "error_type": "TRANSLATION_FAILED",
                            "error": f"TRANSLATION_FAILED: {err_cls} ({e})",
                        })
                    continue

            # Calcolo quota traduzione amortizzata per query nello scenario
            num_queries_in_sc = len(sc["tests"])
            amortized_trans_sec = (
                round(trans_latency_sec / num_queries_in_sc, 3)
                if num_queries_in_sc > 0
                else 0.0
            )

            # 2. Test su ciascuna query tematica
            for query, expected_decision in sc["tests"]:
                strat_metrics.attempted_queries += 1
                lang_metrics.attempted_queries += 1
                query_start = time.perf_counter()

                try:
                    res: TopicDetectionResult = analyzer.detect_topic(
                        document=doc,
                        topic=query,
                        strategy=strat,
                        translation=translation_res,
                        model_id=model_spec.model_id,
                        temperature=temperature,
                        seed=seed,
                        timeout_seconds=timeout_seconds,
                        max_tokens=max_tokens,
                    )
                    analysis_lat = round(time.perf_counter() - query_start, 3)
                    cold_e2e_lat = round(analysis_lat + trans_latency_sec, 3)
                    amortized_e2e_lat = round(analysis_lat + amortized_trans_sec, 3)

                    strat_metrics.completed_valid_queries += 1
                    strat_metrics.total_analysis_latency_seconds += analysis_lat
                    strat_metrics.total_translation_latency_seconds += trans_latency_sec
                    strat_metrics.total_cold_end_to_end_latency_seconds += cold_e2e_lat
                    strat_metrics.total_amortized_end_to_end_latency_seconds += amortized_e2e_lat
                    strat_metrics.total_end_to_end_latency_seconds += amortized_e2e_lat

                    lang_metrics.completed_valid_queries += 1
                    lang_metrics.total_analysis_latency_seconds += analysis_lat
                    lang_metrics.total_translation_latency_seconds += trans_latency_sec
                    lang_metrics.total_cold_end_to_end_latency_seconds += cold_e2e_lat
                    lang_metrics.total_amortized_end_to_end_latency_seconds += amortized_e2e_lat
                    lang_metrics.total_end_to_end_latency_seconds += amortized_e2e_lat

                    is_correct = (res.decision == expected_decision)
                    if is_correct:
                        strat_metrics.correct_decisions += 1
                        lang_metrics.correct_decisions += 1

                    if expected_decision == TopicDecision.PRESENT:
                        if res.decision == TopicDecision.PRESENT:
                            strat_metrics.true_positives += 1
                            lang_metrics.true_positives += 1
                        elif res.decision == TopicDecision.UNCERTAIN:
                            strat_metrics.uncertain_count += 1
                            strat_metrics.false_negatives += 1
                            lang_metrics.uncertain_count += 1
                            lang_metrics.false_negatives += 1
                        else:
                            strat_metrics.false_negatives += 1
                            lang_metrics.false_negatives += 1
                    else:  # ABSENT expected
                        if res.decision == TopicDecision.ABSENT:
                            strat_metrics.true_negatives += 1
                            lang_metrics.true_negatives += 1
                        elif res.decision == TopicDecision.UNCERTAIN:
                            strat_metrics.uncertain_count += 1
                            strat_metrics.false_positives += 1
                            lang_metrics.uncertain_count += 1
                            lang_metrics.false_positives += 1
                        else:
                            strat_metrics.false_positives += 1
                            lang_metrics.false_positives += 1

                    detailed_runs.append({
                        "benchmark_mode": benchmark_mode,
                        "backend": "fake_local" if is_fake else "lm_studio",
                        "model_id": model_spec.model_id,
                        "family": model_spec.family.value,
                        "quantization": model_spec.quantization,
                        "parameter_size": model_spec.parameter_size,
                        "context_length": model_spec.context_length,
                        "scenario_id": sc["id"],
                        "language": sc["lang"],
                        "topic_id": query.topic_id,
                        "language_strategy": strat.value,
                        "prompt_version": res.metadata.get("prompt_version", "topic_detection_v1"),
                        "temperature": temperature,
                        "seed": seed,
                        "timeout_seconds": timeout_seconds,
                        "max_tokens": max_tokens,
                        "prompt_tokens": res.metadata.get("prompt_tokens"),
                        "completion_tokens": res.metadata.get("completion_tokens"),
                        "total_tokens": res.metadata.get("total_tokens"),
                        "analysis_latency_seconds": analysis_lat,
                        "translation_latency_seconds": trans_latency_sec,
                        "cold_end_to_end_latency_seconds": cold_e2e_lat,
                        "amortized_end_to_end_latency_seconds": amortized_e2e_lat,
                        "total_end_to_end_latency_seconds": amortized_e2e_lat,
                        "structured_output_valid": True,
                        "status": "SUCCESS",
                        "error_type": None,
                        "expected": expected_decision.value,
                        "actual": res.decision.value,
                        "correct": is_correct,
                        "evidence_ids": list(res.evidence_ids),
                        "rationale": res.rationale,
                    })

                except Exception as e:
                    err_cls = _classify_benchmark_error(e)
                    strat_metrics.record_error(err_cls)
                    lang_metrics.record_error(err_cls)
                    if err_cls == "INVALID_EVIDENCE_REFERENCE":
                        strat_metrics.invalid_evidence_references += 1
                        lang_metrics.invalid_evidence_references += 1
                    elif err_cls == "STRUCTURED_OUTPUT_INVALID":
                        strat_metrics.structured_output_failures += 1
                        lang_metrics.structured_output_failures += 1

                    detailed_runs.append({
                        "benchmark_mode": benchmark_mode,
                        "backend": "fake_local" if is_fake else "lm_studio",
                        "model_id": model_spec.model_id,
                        "family": model_spec.family.value,
                        "quantization": model_spec.quantization,
                        "parameter_size": model_spec.parameter_size,
                        "context_length": model_spec.context_length,
                        "scenario_id": sc["id"],
                        "language": sc["lang"],
                        "topic_id": query.topic_id,
                        "language_strategy": strat.value,
                        "prompt_version": "topic_detection_v1",
                        "temperature": temperature,
                        "seed": seed,
                        "timeout_seconds": timeout_seconds,
                        "max_tokens": max_tokens,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "analysis_latency_seconds": 0.0,
                        "translation_latency_seconds": trans_latency_sec,
                        "cold_end_to_end_latency_seconds": trans_latency_sec,
                        "amortized_end_to_end_latency_seconds": amortized_trans_sec,
                        "total_end_to_end_latency_seconds": amortized_trans_sec,
                        "structured_output_valid": False,
                        "status": "FAILED",
                        "error_type": err_cls,
                        "error": f"{err_cls}: {e}",
                    })

        results_by_strategy[strat.value] = {
            "metrics": {
                "attempted_queries": strat_metrics.attempted_queries,
                "completed_valid_queries": strat_metrics.completed_valid_queries,
                "failed_queries": strat_metrics.failed_queries,
                "completion_rate": strat_metrics.completion_rate,
                "accuracy": strat_metrics.decision_accuracy,
                "precision": strat_metrics.precision,
                "recall": strat_metrics.recall,
                "f1_score": strat_metrics.f1_score,
                "uncertain_count": strat_metrics.uncertain_count,
                "structured_output_failures": strat_metrics.structured_output_failures,
                "invalid_evidence_references": strat_metrics.invalid_evidence_references,
                "invalid_evidence_reference_rate": strat_metrics.invalid_evidence_reference_rate,
                "average_analysis_latency_seconds": round(strat_metrics.total_analysis_latency_seconds / strat_metrics.completed_valid_queries, 3) if strat_metrics.completed_valid_queries else 0.0,
                "average_translation_latency_seconds": round(strat_metrics.total_translation_latency_seconds / strat_metrics.completed_valid_queries, 3) if strat_metrics.completed_valid_queries else 0.0,
                "scenario_translation_stage_latency_seconds": round(strat_metrics.scenario_translation_stage_latency_seconds, 3),
                "average_cold_end_to_end_latency_seconds": strat_metrics.average_cold_end_to_end_latency_seconds,
                "average_amortized_end_to_end_latency_seconds": strat_metrics.average_amortized_end_to_end_latency_seconds,
                "average_end_to_end_latency_seconds": strat_metrics.average_amortized_end_to_end_latency_seconds,
                "primary_comparison_metric": "average_amortized_end_to_end_latency_seconds",
                "error_counts": strat_metrics.error_counts,
            },
            "runs": detailed_runs,
            "detailed_runs": detailed_runs,
        }


    # Calcolo ripartizione per lingua
    by_language_summary: dict[str, Any] = {}
    for lang, strats in by_language_data.items():
        by_language_summary[lang] = {}
        for s_name, m in strats.items():
            by_language_summary[lang][s_name] = {
                "attempted_queries": m.attempted_queries,
                "completed_valid_queries": m.completed_valid_queries,
                "accuracy": m.decision_accuracy,
                "precision": m.precision,
                "recall": m.recall,
                "f1_score": m.f1_score,
                "completion_rate": m.completion_rate,
                "average_cold_end_to_end_latency_seconds": m.average_cold_end_to_end_latency_seconds,
                "average_amortized_end_to_end_latency_seconds": m.average_amortized_end_to_end_latency_seconds,
                "average_end_to_end_latency_seconds": m.average_amortized_end_to_end_latency_seconds,
            }

    declared_meta = model_spec.metadata.get("declared_model_metadata", {
        "quantization": model_spec.quantization,
        "parameter_size": model_spec.parameter_size,
        "context_length": model_spec.context_length,
    })
    observed_meta = model_spec.metadata.get("observed_model_metadata", {
        "quantization": None,
        "parameter_size": None,
        "context_length": None,
    })

    return {
        "benchmark_mode": benchmark_mode,
        "model_spec": {
            "family": model_spec.family.value,
            "model_id": model_spec.model_id,
            "quantization": model_spec.quantization,
            "parameter_size": model_spec.parameter_size,
            "context_length": model_spec.context_length,
            "declared_model_metadata": declared_meta,
            "observed_model_metadata": observed_meta,
            "metadata_discrepancies": model_spec.metadata.get("metadata_discrepancies", []),
        },
        "timestamp": runtime_timestamp,
        "sample_type": "pilot_synthetic_non_sensitive",
        "notice": "Campione sperimentale pilota non generalizzabile statisticamente. I risultati ottenuti con Fake client non costituiscono inferenza reale sui pesi.",
        "results_by_strategy": results_by_strategy,
        "results_by_language": by_language_summary,
    }


def run_synthetic_discovery_benchmark(
    client: BaseLocalLlmClient,
    model_spec: ExperimentModelSpec,
    max_topics: int = 10,
    temperature: float = 0.0,
    seed: int | None = 42,
    timeout_seconds: float = 30.0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """
    Esegue il benchmark specifico per Open Topic Discovery.
    Registra validità strutturale, rispetto del vincolo evidence_id, error taxonomy e metadati completi.
    """
    is_fake = isinstance(client, FakeLocalLlmClient)
    benchmark_mode = "FAKE_HARNESS_VALIDATION" if is_fake else "REAL_MODEL_BENCHMARK"
    runtime_timestamp = datetime.now(timezone.utc).isoformat()

    scenarios = create_synthetic_benchmark_documents()
    analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id=model_spec.model_id)

    attempted_runs = 0
    completed_runs = 0
    failed_runs = 0
    structured_output_failure_count = 0
    model_mismatch_count = 0
    invalid_evidence_reference_failure_count = 0
    valid_evidence_reference_run_count = 0
    error_counts: dict[str, int] = {}
    total_topics_discovered = 0
    total_latency_seconds = 0.0
    runs_log: list[dict[str, Any]] = []

    for sc in scenarios:
        doc = sc["document"]
        valid_evidence_ids = {s.evidence_id for s in doc.all_evidence_sections}
        attempted_runs += 1
        start_t = time.perf_counter()

        try:
            res: TopicDiscoveryResult = analyzer.discover_topics(
                document=doc,
                model_id=model_spec.model_id,
                max_topics=max_topics,
                temperature=temperature,
                seed=seed,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
            )
            lat = round(time.perf_counter() - start_t, 3)
            completed_runs += 1
            valid_evidence_reference_run_count += 1
            topic_cnt = len(res.topics)
            total_topics_discovered += topic_cnt
            total_latency_seconds += lat

            scenario_topics_summary = []
            for t in res.topics:
                scenario_topics_summary.append({
                    "label": t.label,
                    "short_description": t.short_description,
                    "evidence_count": len(t.evidence_ids),
                    "evidence_ids": list(t.evidence_ids),
                })

            prompt_toks = res.metadata.get("prompt_tokens")
            compl_toks = res.metadata.get("completion_tokens")
            tot_toks = res.metadata.get("total_tokens")

            runs_log.append({
                "benchmark_mode": benchmark_mode,
                "backend": "fake_local" if is_fake else "lm_studio",
                "scenario_id": sc["id"],
                "language": sc["lang"],
                "prompt_version": res.metadata.get("prompt_version", "topic_discovery_v1"),
                "status": "SUCCESS",
                "topics_count": topic_cnt,
                "evidence_reference_valid": True,
                "latency_seconds": lat,
                "max_tokens": max_tokens,
                "prompt_tokens": prompt_toks,
                "completion_tokens": compl_toks,
                "total_tokens": tot_toks,
                "token_usage": {
                    "prompt_tokens": prompt_toks,
                    "completion_tokens": compl_toks,
                    "total_tokens": tot_toks,
                },
                "topics": scenario_topics_summary,
            })
        except Exception as e:
            err_cls = _classify_benchmark_error(e)
            failed_runs += 1
            error_counts[err_cls] = error_counts.get(err_cls, 0) + 1
            if err_cls == "STRUCTURED_OUTPUT_INVALID":
                structured_output_failure_count += 1
            elif err_cls == "INVALID_EVIDENCE_REFERENCE":
                invalid_evidence_reference_failure_count += 1
            elif err_cls == "MODEL_MISMATCH":
                model_mismatch_count += 1

            runs_log.append({
                "benchmark_mode": benchmark_mode,
                "backend": "fake_local" if is_fake else "lm_studio",
                "scenario_id": sc["id"],
                "language": sc["lang"],
                "prompt_version": "topic_discovery_v1",
                "status": "FAILED",
                "evidence_reference_valid": False if err_cls == "INVALID_EVIDENCE_REFERENCE" else None,
                "max_tokens": max_tokens,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "error_type": err_cls,
                "error": f"{err_cls}: {e}",
            })

    evidence_reference_valid_run_rate = (
        round(valid_evidence_reference_run_count / attempted_runs, 4)
        if attempted_runs > 0
        else 0.0
    )

    declared_meta = model_spec.metadata.get("declared_model_metadata", {
        "quantization": model_spec.quantization,
        "parameter_size": model_spec.parameter_size,
        "context_length": model_spec.context_length,
    })
    observed_meta = model_spec.metadata.get("observed_model_metadata", {
        "quantization": None,
        "parameter_size": None,
        "context_length": None,
    })

    return {
        "benchmark_mode": benchmark_mode,
        "task": "OPEN_TOPIC_DISCOVERY",
        "model_spec": {
            "family": model_spec.family.value,
            "model_id": model_spec.model_id,
            "quantization": model_spec.quantization,
            "parameter_size": model_spec.parameter_size,
            "context_length": model_spec.context_length,
            "declared_model_metadata": declared_meta,
            "observed_model_metadata": observed_meta,
            "metadata_discrepancies": model_spec.metadata.get("metadata_discrepancies", []),
        },
        "timestamp": runtime_timestamp,
        "manual_review_status": "NOT_REVIEWED",
        "metrics": {
            "attempted_runs": attempted_runs,
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "completion_rate": round(completed_runs / attempted_runs, 4) if attempted_runs else 0.0,
            "structured_validity_rate": round(completed_runs / attempted_runs, 4) if attempted_runs else 0.0,
            "structured_output_failure_count": structured_output_failure_count,
            "model_mismatch_count": model_mismatch_count,
            "invalid_evidence_reference_failure_count": invalid_evidence_reference_failure_count,
            "invalid_evidence_reference_count": invalid_evidence_reference_failure_count,
            "evidence_reference_valid_run_rate": evidence_reference_valid_run_rate,
            "evidence_reference_validity_rate": evidence_reference_valid_run_rate,
            "error_counts": error_counts,
            "average_topics_per_conversation": round(total_topics_discovered / completed_runs, 2) if completed_runs else 0.0,
            "average_latency_seconds": round(total_latency_seconds / completed_runs, 3) if completed_runs else 0.0,
        },
        "runs": runs_log,
    }


def save_benchmark_report(
    benchmark_data: dict[str, Any],
    output_dir: Path | str = "output",
    filename_prefix: str = "ai_benchmark",
) -> tuple[Path, Path]:
    """
    Salva i risultati del benchmark in formato JSON e Markdown all'interno di output_dir.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    json_file = out_path / f"{filename_prefix}.json"
    md_file = out_path / f"{filename_prefix}.md"

    # 1. Scrittura JSON completo
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_data, f, indent=2, ensure_ascii=False)

    # 2. Scrittura Markdown sintetico
    spec = benchmark_data.get("model_spec", {})
    md_lines = [
        "# Benchmark Sperimentale AI Locale — Topic Detection Multilingue",
        "",
        f"**Modalità**: `{benchmark_data.get('benchmark_mode')}`  ",
        f"**Modello**: `{spec.get('model_id')}` (Famiglia: `{spec.get('family')}`, Quant: `{spec.get('quantization') or 'N/D'}`)  ",
        f"**Data e Ora (UTC)**: {benchmark_data.get('timestamp')}  ",
        f"**Avviso**: {benchmark_data.get('notice')}  ",
        "",
        "## 1. Metriche per Strategia Linguistica (Confronto Primario su Latenza End-to-End)",
        "",
        "| Strategia | Query Totali | Completate | Completion Rate | Accuracy | Precision | Recall | F1 Score | Incerti | Fallimenti Output | Latenza E2E Amortizzata (s) | Latenza E2E Cold (s) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    results_by_strat = benchmark_data.get("results_by_strategy", {})
    for strat, data in results_by_strat.items():
        m = data.get("metrics", {})
        md_lines.append(
            f"| **{strat}** | {m.get('attempted_queries')} | {m.get('completed_valid_queries')} | {m.get('completion_rate')} | "
            f"{m.get('accuracy')} | {m.get('precision')} | {m.get('recall')} | {m.get('f1_score')} | {m.get('uncertain_count')} | "
            f"{m.get('structured_output_failures')} | {m.get('average_amortized_end_to_end_latency_seconds')} | {m.get('average_cold_end_to_end_latency_seconds')} |"
        )

    md_lines.append("")
    md_lines.append("## 2. Note Metodologiche ed Error Taxonomy")
    md_lines.append("- Le metriche decisionali (Accuracy, Precision, Recall, F1) sono formalmente calcolate sulle query valide completate affiancate dal Completion Rate.")
    md_lines.append("- **Metodologia di Latenza**: Per la strategia TRANSLATE_FIRST, la traduzione dello scenario è eseguita una sola volta e riutilizzata per le query dello scenario. Il confronto primario ('Latenza E2E Amortizzata') ripartisce equamente la latenza di traduzione tra le query. La 'Latenza E2E Cold' riflette invece il caso peggiore di singola query con traduzione dedicata.")

    with open(md_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    return json_file, md_file

