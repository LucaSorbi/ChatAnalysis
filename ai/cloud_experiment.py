"""
ai/cloud_experiment.py
-----------------------
Runner sperimentale OPT-IN per la validazione end-to-end preliminare su backend cloud compatibile OpenAI.

VINCOLI ARCHITETTURALI E DI SICUREZZA:
1. DATASET SINTETICO CONGELATO:
   Accetta ESCLUSIVAMENTE i file congelati del repository (test_data/cloud_e2e/messages.json
   e test_data/cloud_e2e/ground_truth.json) con verifica preventiva degli hash SHA-256 approvati.
   Nessun percorso arbitrario, file esterno, upload o override è consentito.
2. DOPPIO OPT-IN OBBLIGATORIO:
   Richiede contemporaneamente la variabile d'ambiente RUN_CLOUD_E2E=1 e il flag CLI --force-run.
   In assenza di entrambi, l'esecuzione viene abortita prima di qualsiasi tentativo di rete.
3. SEGREGAZIONE GROUND TRUTH / PROMPT:
   La ground truth viene caricata SOLO DOPO la costruzione dei documenti e NON viene mai
   inserita nei prompt destinati al modello.
4. NESSUN RETRY AUTOMATICO:
   Una richiesta = un tentativo. Timeout, parsing failure e mismatch vengono registrati come FAILED.
5. ISOLAMENTO TOTALE DA STREAMLIT E DATI REALI:
   Nessuna UI Streamlit per il cloud. I dati forensi reali restano esclusivo dominio del percorso
   locale (LM Studio su loopback).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping

# Garantisce che la root del repository sia in sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai.backend import (
    AiBackendError,
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiBackendUnavailableError,
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiModelNotSpecifiedError,
    AiStructuredOutputError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.cloud_test import RemoteOpenAICompatibleTestClient, _validate_remote_url
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    TopicDecision,
    TopicQuery,
)
from ai.topics import (
    PROMPT_VERSION_DETECTION,
    PROMPT_VERSION_DISCOVERY,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from ai.translation import PROMPT_VERSION_TRANSLATION, EvidenceTranslator
from ui.ingestion import ingest_file_payload
from ui.models import IngestionStatus, SourceFormat

# ---------------------------------------------------------------------------
# Costanti congelate del dataset sintetico approvato dal supervisore
# ---------------------------------------------------------------------------
FROZEN_DATASET_REL_PATH = Path("test_data") / "cloud_e2e" / "messages.json"
FROZEN_DATASET_SHA256 = "2E49A9C62D89125D225726953FF0E879FB49E07D7DB803DB5503C345B051B781"

FROZEN_GROUND_TRUTH_REL_PATH = Path("test_data") / "cloud_e2e" / "ground_truth.json"
FROZEN_GROUND_TRUTH_SHA256 = "377FB95B7690BA507481F357449B1A56D1F89CB60636DA9DEBD66FE603E5AD6D"


class CloudExperimentError(Exception):
    """Eccezione base per errori di configurazione o validazione dell'esperimento cloud."""
    pass


class CloudExperimentOptInError(CloudExperimentError):
    """Sollevata quando il doppio opt-in obbligatorio (RUN_CLOUD_E2E=1 e --force-run) non è soddisfatto."""
    pass


class CloudExperimentIntegrityError(CloudExperimentError):
    """Sollevata quando gli hash SHA-256 dei file congelati non corrispondono a quelli approvati."""
    pass


def verify_opt_in(force_run: bool) -> None:
    """
    Verifica il doppio opt-in obbligatorio.
    Richiede sia la variabile d'ambiente RUN_CLOUD_E2E=1 sia il parametro force_run=True.
    """
    env_opt_in = os.environ.get("RUN_CLOUD_E2E") == "1"
    if not env_opt_in or not force_run:
        missing = []
        if not env_opt_in:
            missing.append("variabile d'ambiente RUN_CLOUD_E2E=1")
        if not force_run:
            missing.append("flag CLI --force-run")
        raise CloudExperimentOptInError(
            f"Esecuzione benchmark cloud abortita: doppio opt-in obbligatorio non soddisfatto (mancante: {', '.join(missing)}). "
            "Per autorizzare l'esecuzione sperimentale è indispensabile impostare sia RUN_CLOUD_E2E=1 sia --force-run."
        )


def verify_file_hash(path: Path, expected_hash_upper: str, file_label: str) -> None:
    """Verifica l'integrità crittografica di un file rispetto all'hash SHA-256 congelato."""
    if not path.is_file():
        raise FileNotFoundError(f"File congelato {file_label} non trovato nel repository: '{path}'")
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    if actual_hash != expected_hash_upper:
        raise CloudExperimentIntegrityError(
            f"Violazione integrità per {file_label}: atteso SHA-256 '{expected_hash_upper}', "
            f"rilevato '{actual_hash}'. Esecuzione abortita per prevenire contaminazioni del benchmark."
        )


def run_synthetic_pipeline(dataset_path: Path) -> dict[str, ConversationEvidenceDocument]:
    """
    Esegue la pipeline forense reale sul file messages.json:
    CellebriteJsonImporter -> RawRecord -> Validation -> Normalization ->
    Entity Resolution -> UnifiedMessage -> MessageEvidenceBundle -> ConversationEvidenceDocument.
    """
    file_bytes = dataset_path.read_bytes()
    ingestion_res = ingest_file_payload(
        file_bytes_or_io=file_bytes,
        original_filename="messages.json",
        source_format=SourceFormat.CELLEBRITE_JSON,
    )
    if ingestion_res.status != IngestionStatus.SUCCESS:
        raise CloudExperimentError(
            f"Esecuzione della pipeline sintetica non riuscita (stato '{ingestion_res.status.value}'). "
            "Impossibile procedere con l'esperimento cloud."
        )
    if not ingestion_res.documents:
        raise CloudExperimentError(
            "Nessun ConversationEvidenceDocument prodotto dalla pipeline sintetica."
        )
    return ingestion_res.documents


def load_ground_truth_post_ingestion(ground_truth_path: Path) -> dict[str, Any]:
    """Carica la ground truth esclusivamente DOPO la costruzione dei documenti."""
    with open(ground_truth_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sanitize_output_text(text: str, api_key: str | None) -> str:
    """Rimuove qualsiasi potenziale occorrenza dell'API key dai testi serializzati."""
    if api_key and len(api_key) >= 4 and api_key in text:
        text = text.replace(api_key, "[REDACTED_API_KEY]")
    default_key = os.environ.get("CLOUD_LLM_API_KEY")
    if default_key and len(default_key) >= 4 and default_key in text:
        text = text.replace(default_key, "[REDACTED_API_KEY]")
    return text


class _ExperimentClientWrapper(BaseLlmClient):
    """
    Wrapper di monitoraggio trasparente che traccia chiamate HTTP, inferenze completate,
    output ricevuti e applica una policy di pacing preventivo (Protocol V3).
    """

    def __init__(self, inner: BaseLlmClient, min_request_interval_seconds: float = 15.0) -> None:
        self.inner = inner
        self.min_request_interval_seconds = max(0.0, float(min_request_interval_seconds))
        self.requests_sent = 0
        self.inferences_completed = 0
        self.outputs_received = 0
        self.last_request_start_monotonic: float | None = None
        self.first_request_start_monotonic: float | None = None
        self.request_start_deltas: list[float] = []
        self.request_telemetry: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> LlmCompletionResponse:
        now = time.monotonic()
        if self.last_request_start_monotonic is not None:
            elapsed = now - self.last_request_start_monotonic
            if elapsed < self.min_request_interval_seconds:
                sleep_duration = self.min_request_interval_seconds - elapsed
                time.sleep(sleep_duration)
                now = time.monotonic()
            delta = now - self.last_request_start_monotonic
            self.request_start_deltas.append(round(delta, 3))
        else:
            self.first_request_start_monotonic = now

        self.last_request_start_monotonic = now
        self.requests_sent += 1
        seq = self.requests_sent
        offset = round(now - self.first_request_start_monotonic, 3)
        self.request_telemetry.append({
            "request_sequence_number": seq,
            "request_start_offset_seconds": offset,
        })

        try:
            resp = self.inner.chat_completion(
                messages=messages,
                model_id=model_id,
                schema=schema,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            self.inferences_completed += 1
            self.outputs_received += 1
            return resp
        except AiModelMismatchError:
            self.inferences_completed += 1
            raise
        except (AiBackendRequestError, AiBackendTimeoutError, AiBackendUnavailableError, AiBackendProtocolError):
            raise


def _parse_seed_arg(val: Any) -> int | None:
    """Valida e converte l'argomento CLI --seed supportando interi o 'none' per omissione."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if s.lower() in ("none", "null", "omitted", ""):
        return None
    try:
        return int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Valore seed non valido: '{val}'. Specificare un intero (es. 42) oppure 'none' per omettere il parametro."
        )


def _parse_min_request_interval_arg(val: Any) -> float:
    """Valida l'intervallo minimo tra richieste in secondi (deve essere >= 0)."""
    try:
        f = float(val)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(
            f"Valore intervallo non valido: '{val}'. Specificare un numero reale >= 0."
        )
    if f < 0.0:
        raise argparse.ArgumentTypeError(
            f"L'intervallo minimo tra richieste non può essere negativo: {f}."
        )
    return f


def run_cloud_benchmark(
    endpoint_url: str,
    model_id: str,
    api_key_env_var: str = "CLOUD_LLM_API_KEY",
    timeout_seconds: float = 60.0,
    max_tokens: int = 1024,
    force_run: bool = False,
    json_mode: str = "json_schema",
    output_dir: str | Path = "output/cloud_e2e_v3",
    temperature: float = 0.0,
    seed: int | None = 42,
    min_request_interval_seconds: float = 15.0,
    client_override: BaseLlmClient | None = None,
) -> dict[str, Any]:
    """
    Funzione principale del runner sperimentale.
    Esegue l'intero benchmark E2E sui 6 casi congelati garantendo isolamento,
    verifica degli hash, policy di secret management e calcolo metriche.
    """
    # 1. verify opt-in
    verify_opt_in(force_run)

    # 2. resolve frozen paths (rigorosamente non configurabili da CLI)
    dataset_path = _REPO_ROOT / FROZEN_DATASET_REL_PATH
    ground_truth_path = _REPO_ROOT / FROZEN_GROUND_TRUTH_REL_PATH

    # 3. verify DATASET hash
    verify_file_hash(dataset_path, FROZEN_DATASET_SHA256, "dataset sintetico (messages.json)")

    # 4. validate endpoint
    _validate_remote_url(endpoint_url)

    # 5. verify API key
    api_key_raw = os.environ.get(api_key_env_var)
    if not api_key_raw or not api_key_raw.strip():
        raise AiBackendUnavailableError(
            f"API key cloud non configurata: variabile d'ambiente '{api_key_env_var}' assente o vuota. "
            "Impossibile avviare il benchmark cloud sperimentale senza chiave autorizzata."
        )
    api_key = api_key_raw.strip()

    # 6. run_synthetic_pipeline(messages.json)
    pipeline_documents = run_synthetic_pipeline(dataset_path)

    # 7. ONLY NOW verify GROUND TRUTH hash
    verify_file_hash(ground_truth_path, FROZEN_GROUND_TRUTH_SHA256, "ground truth (ground_truth.json)")

    # 8. load and parse ground_truth.json
    ground_truth_data = load_ground_truth_post_ingestion(ground_truth_path)
    cases_meta = {c["case_id"]: c for c in ground_truth_data.get("cases", [])}

    # 8. Mappatura documenti per chat_id / document_id
    doc_by_chat_id: dict[str, ConversationEvidenceDocument] = {}
    doc_by_doc_id: dict[str, ConversationEvidenceDocument] = {}
    for doc in pipeline_documents.values():
        if doc.chat_id:
            doc_by_chat_id[doc.chat_id] = doc
        doc_by_doc_id[doc.document_id] = doc

    # 9. Inizializzazione del client remoto sperimentale con wrapper di tracciamento
    require_json_schema = (json_mode == "json_schema")
    if client_override is not None:
        base_client = client_override
    else:
        base_client = RemoteOpenAICompatibleTestClient(
            endpoint_url=endpoint_url,
            model_id=model_id,
            api_key_env_var=api_key_env_var,
            default_timeout_seconds=timeout_seconds,
            require_json_schema=require_json_schema,
            check_model_mismatch=True,
            debug_preserve_raw=False,
        )

    client = _ExperimentClientWrapper(
        base_client,
        min_request_interval_seconds=min_request_interval_seconds,
    )

    detection_analyzer = TopicDetectionAnalyzer(client=client, default_model_id=model_id)
    discovery_analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id=model_id)
    translator = EvidenceTranslator(client=client, default_target_language="it", default_model_id=model_id)

    timestamp_utc = datetime.now(timezone.utc).isoformat()

    # 10. Esecuzione dei casi di test
    case_results: list[dict[str, Any]] = []

    # CASI 1, 2, 3, 4 (Detection standard DIRECT_MULTILINGUAL)
    standard_detection_case_ids = [
        "CASE_1_EXPLICIT_PRESENT",
        "CASE_2_IMPLICIT_PRESENT",
        "CASE_3_AMBIGUOUS_UNCERTAIN",
        "CASE_4_ABSENT",
    ]

    for cid in standard_detection_case_ids:
        c_def = cases_meta.get(cid)
        if not c_def:
            continue

        doc = doc_by_chat_id.get(c_def["conversation_chat_id"]) or doc_by_doc_id.get(c_def["document_id"])
        if not doc:
            raise CloudExperimentError(f"Documento non generato dalla pipeline per {cid}")

        topic = TopicQuery(
            topic_id=c_def["target_topic_id"],
            label=c_def["target_topic_label"],
            description=c_def["target_topic_description"],
        )

        res_dict: dict[str, Any] = {
            "case_id": cid,
            "document_id": doc.document_id,
            "strategy": AnalysisLanguageStrategy.DIRECT_MULTILINGUAL.value,
            "topic_id": topic.topic_id,
            "expected_decision": c_def["expected_decision"],
            "actual_decision": None,
            "decision_correct": False,
            "returned_evidence_ids": [],
            "expected_evidence_ids": c_def.get("expected_evidence_ids", []),
            "evidence_ids_valid": False,
            "rationale": None,
            "model_id_requested": model_id,
            "model_id_returned": None,
            "prompt_version": PROMPT_VERSION_DETECTION,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "latency_seconds": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "error_type": None,
            "error_message_sanitized": None,
            "status": "PENDING",
        }

        try:
            det_res = detection_analyzer.detect_topic(
                document=doc,
                topic=topic,
                strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
                model_id=model_id,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            res_dict["status"] = "SUCCESS"
            res_dict["actual_decision"] = det_res.decision.value
            res_dict["decision_correct"] = (det_res.decision.value == c_def["expected_decision"])
            res_dict["returned_evidence_ids"] = list(det_res.evidence_ids)
            res_dict["rationale"] = det_res.rationale
            res_dict["model_id_returned"] = det_res.metadata.get("model")
            res_dict["latency_seconds"] = det_res.metadata.get("latency_seconds")
            res_dict["prompt_tokens"] = det_res.metadata.get("prompt_tokens")
            res_dict["completion_tokens"] = det_res.metadata.get("completion_tokens")
            res_dict["total_tokens"] = det_res.metadata.get("total_tokens")

            # Verifica validità evidence_ids citati
            valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
            res_dict["evidence_ids_valid"] = all(eid in valid_eids for eid in det_res.evidence_ids)

        except (AiBackendError, ValueError) as exc:
            res_dict["status"] = "FAILED"
            res_dict["error_type"] = type(exc).__name__
            res_dict["error_message_sanitized"] = _sanitize_output_text(str(exc), api_key)
            if isinstance(exc, AiModelMismatchError):
                res_dict["model_id_returned"] = exc.returned_model

        case_results.append(res_dict)

    # CASO 5: MULTILINGUAL_PRESENT (Entrambi i percorsi A: DIRECT e B: TRANSLATE_FIRST)
    c5_def = cases_meta.get("CASE_5_MULTILINGUAL_PRESENT")
    if c5_def:
        doc5 = doc_by_chat_id.get(c5_def["conversation_chat_id"]) or doc_by_doc_id.get(c5_def["document_id"])
        if not doc5:
            raise CloudExperimentError("Documento non generato per CASE_5_MULTILINGUAL_PRESENT")

        topic5 = TopicQuery(
            topic_id=c5_def["target_topic_id"],
            label=c5_def["target_topic_label"],
            description=c5_def["target_topic_description"],
        )

        # 5.A — DIRECT_MULTILINGUAL
        res_5a: dict[str, Any] = {
            "case_id": "CASE_5_MULTILINGUAL_DIRECT",
            "document_id": doc5.document_id,
            "strategy": AnalysisLanguageStrategy.DIRECT_MULTILINGUAL.value,
            "topic_id": topic5.topic_id,
            "expected_decision": c5_def["expected_decision"],
            "actual_decision": None,
            "decision_correct": False,
            "returned_evidence_ids": [],
            "expected_evidence_ids": c5_def.get("expected_evidence_ids", []),
            "evidence_ids_valid": False,
            "rationale": None,
            "model_id_requested": model_id,
            "model_id_returned": None,
            "prompt_version": PROMPT_VERSION_DETECTION,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "latency_seconds": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "error_type": None,
            "error_message_sanitized": None,
            "status": "PENDING",
        }
        try:
            det_res_5a = detection_analyzer.detect_topic(
                document=doc5,
                topic=topic5,
                strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
                model_id=model_id,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            res_5a["status"] = "SUCCESS"
            res_5a["actual_decision"] = det_res_5a.decision.value
            res_5a["decision_correct"] = (det_res_5a.decision.value == c5_def["expected_decision"])
            res_5a["returned_evidence_ids"] = list(det_res_5a.evidence_ids)
            res_5a["rationale"] = det_res_5a.rationale
            res_5a["model_id_returned"] = det_res_5a.metadata.get("model")
            res_5a["latency_seconds"] = det_res_5a.metadata.get("latency_seconds")
            res_5a["prompt_tokens"] = det_res_5a.metadata.get("prompt_tokens")
            res_5a["completion_tokens"] = det_res_5a.metadata.get("completion_tokens")
            res_5a["total_tokens"] = det_res_5a.metadata.get("total_tokens")
            valid_eids_5 = {s.evidence_id for s in doc5.all_evidence_sections}
            res_5a["evidence_ids_valid"] = all(eid in valid_eids_5 for eid in det_res_5a.evidence_ids)
        except (AiBackendError, ValueError) as exc:
            res_5a["status"] = "FAILED"
            res_5a["error_type"] = type(exc).__name__
            res_5a["error_message_sanitized"] = _sanitize_output_text(str(exc), api_key)
            if isinstance(exc, AiModelMismatchError):
                res_5a["model_id_returned"] = exc.returned_model
        case_results.append(res_5a)

        # 5.B — TRANSLATE_FIRST
        res_5b: dict[str, Any] = {
            "case_id": "CASE_5_MULTILINGUAL_TRANSLATE_FIRST",
            "document_id": doc5.document_id,
            "strategy": AnalysisLanguageStrategy.TRANSLATE_FIRST.value,
            "topic_id": topic5.topic_id,
            "expected_decision": c5_def["expected_decision"],
            "actual_decision": None,
            "decision_correct": False,
            "returned_evidence_ids": [],
            "expected_evidence_ids": c5_def.get("expected_evidence_ids", []),
            "evidence_ids_valid": False,
            "rationale": None,
            "model_id_requested": model_id,
            "model_id_returned": None,
            "prompt_version": PROMPT_VERSION_DETECTION,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "latency_seconds": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "translation_prompt_version": PROMPT_VERSION_TRANSLATION,
            "translation_latency_seconds": None,
            "translation_prompt_tokens": None,
            "translation_completion_tokens": None,
            "translation_total_tokens": None,
            "error_type": None,
            "error_message_sanitized": None,
            "status": "PENDING",
        }
        try:
            # Step 1: Traduzione evidenze
            trans_res = translator.translate_document(
                document=doc5,
                target_language="it",
                model_id=model_id,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            res_5b["translation_latency_seconds"] = trans_res.metadata.get("latency_seconds")
            res_5b["translation_prompt_tokens"] = trans_res.metadata.get("prompt_tokens")
            res_5b["translation_completion_tokens"] = trans_res.metadata.get("completion_tokens")
            res_5b["translation_total_tokens"] = trans_res.metadata.get("total_tokens")

            # Step 2: Topic Detection sul documento con evidenza tradotta derivata
            det_res_5b = detection_analyzer.detect_topic(
                document=doc5,
                topic=topic5,
                strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST,
                translation=trans_res,
                model_id=model_id,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            res_5b["status"] = "SUCCESS"
            res_5b["actual_decision"] = det_res_5b.decision.value
            res_5b["decision_correct"] = (det_res_5b.decision.value == c5_def["expected_decision"])
            res_5b["returned_evidence_ids"] = list(det_res_5b.evidence_ids)
            res_5b["rationale"] = det_res_5b.rationale
            res_5b["model_id_returned"] = det_res_5b.metadata.get("model")
            res_5b["latency_seconds"] = det_res_5b.metadata.get("latency_seconds")
            res_5b["prompt_tokens"] = det_res_5b.metadata.get("prompt_tokens")
            res_5b["completion_tokens"] = det_res_5b.metadata.get("completion_tokens")
            res_5b["total_tokens"] = det_res_5b.metadata.get("total_tokens")
            valid_eids_5b = {s.evidence_id for s in doc5.all_evidence_sections}
            res_5b["evidence_ids_valid"] = all(eid in valid_eids_5b for eid in det_res_5b.evidence_ids)

        except (AiBackendError, ValueError) as exc:
            res_5b["status"] = "FAILED"
            res_5b["error_type"] = type(exc).__name__
            res_5b["error_message_sanitized"] = _sanitize_output_text(str(exc), api_key)
            if isinstance(exc, AiModelMismatchError):
                res_5b["model_id_returned"] = exc.returned_model

        case_results.append(res_5b)

    # CASO 6: TOPIC DISCOVERY (Open Discovery qualitativo post-hoc)
    c6_def = cases_meta.get("CASE_6_TOPIC_DISCOVERY")
    res_6: dict[str, Any] = {
        "case_id": "CASE_6_TOPIC_DISCOVERY",
        "document_id": None,
        "strategy": AnalysisLanguageStrategy.DIRECT_MULTILINGUAL.value,
        "evaluation_mode": "QUALITATIVE",
        "topics": [],
        "evidence_ids": [],
        "post_hoc_expected_topics": c6_def.get("expected_discovery_topics", []) if c6_def else [],
        "model_id_requested": model_id,
        "model_id_returned": None,
        "prompt_version": PROMPT_VERSION_DISCOVERY,
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_tokens,
        "latency_seconds": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "error_type": None,
        "error_message_sanitized": None,
        "status": "PENDING",
    }
    if c6_def:
        doc6 = doc_by_chat_id.get(c6_def["conversation_chat_id"]) or doc_by_doc_id.get(c6_def["document_id"])
        if doc6:
            res_6["document_id"] = doc6.document_id
            try:
                disc_res = discovery_analyzer.discover_topics(
                    document=doc6,
                    strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
                    model_id=model_id,
                    max_topics=10,
                    temperature=temperature,
                    seed=seed,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
                res_6["status"] = "SUCCESS"
                res_6["model_id_returned"] = disc_res.metadata.get("model")
                res_6["latency_seconds"] = disc_res.metadata.get("latency_seconds")
                res_6["prompt_tokens"] = disc_res.metadata.get("prompt_tokens")
                res_6["completion_tokens"] = disc_res.metadata.get("completion_tokens")
                res_6["total_tokens"] = disc_res.metadata.get("total_tokens")

                disc_topics = []
                all_disc_eids = set()
                valid_eids_6 = {s.evidence_id for s in doc6.all_evidence_sections}
                for dt in disc_res.topics:
                    disc_topics.append({
                        "label": dt.label,
                        "description": dt.short_description,
                        "evidence_ids": list(dt.evidence_ids),
                    })
                    all_disc_eids.update(dt.evidence_ids)

                res_6["topics"] = disc_topics
                res_6["evidence_ids"] = sorted(list(all_disc_eids))
                res_6["evidence_ids_valid"] = all(eid in valid_eids_6 for eid in all_disc_eids)

            except (AiBackendError, ValueError) as exc:
                res_6["status"] = "FAILED"
                res_6["error_type"] = type(exc).__name__
                res_6["error_message_sanitized"] = _sanitize_output_text(str(exc), api_key)
                if isinstance(exc, AiModelMismatchError):
                    res_6["model_id_returned"] = exc.returned_model

    case_results.append(res_6)

    # 11. Calcolo metriche aggregate di Topic Detection
    detection_runs = [r for r in case_results if r.get("evaluation_mode") != "QUALITATIVE"]
    total_det = len(detection_runs)
    successful_det = [r for r in detection_runs if r["status"] == "SUCCESS"]
    completed_det_count = len(successful_det)
    failed_det_count = total_det - completed_det_count
    correct_det = [r for r in successful_det if r["decision_correct"]]
    correct_decisions = len(correct_det)

    detection_completion_rate = round(completed_det_count / total_det, 4) if total_det > 0 else 0.0
    decision_accuracy = (
        round(correct_decisions / completed_det_count, 4)
        if completed_det_count > 0
        else None
    )

    def _calc_subset(expected_val: str) -> dict[str, int]:
        subset = [r for r in detection_runs if r["expected_decision"] == expected_val]
        completed = [r for r in subset if r.get("status") == "SUCCESS"]
        cor = [r for r in completed if r.get("decision_correct")]
        return {"total": len(subset), "completed": len(completed), "correct": len(cor)}

    pres_stats = _calc_subset("PRESENT")
    abs_stats = _calc_subset("ABSENT")
    unc_stats = _calc_subset("UNCERTAIN")

    successful_latencies = [
        r["latency_seconds"] for r in successful_det if r["latency_seconds"] is not None
    ]
    avg_latency = (
        round(sum(successful_latencies) / len(successful_latencies), 3)
        if successful_latencies
        else 0.0
    )

    min_delta = min(client.request_start_deltas) if client.request_start_deltas else None
    avg_delta = (
        round(sum(client.request_start_deltas) / len(client.request_start_deltas), 3)
        if client.request_start_deltas
        else None
    )

    metrics = {
        "benchmark_type": "TECHNICAL VALIDATION BENCHMARK",
        "scientific_disclaimer": (
            "Questo benchmark sintetico è uno strumento di validazione tecnica E2E preliminare "
            "su 6 casi controllati. Non costituisce una valutazione statistica generale del modello."
        ),
        "protocol_version": 3,
        "cloud_api_requests_sent": client.requests_sent,
        "llm_inferences_completed": client.inferences_completed,
        "model_outputs_received": client.outputs_received,
        "total_detection_cases": total_det,
        "completed_detection_cases": completed_det_count,
        "failed_detection_cases": failed_det_count,
        "detection_completion_rate": detection_completion_rate,
        "correct_decisions": correct_decisions,
        "decision_accuracy": decision_accuracy,
        "present_breakdown": pres_stats,
        "absent_breakdown": abs_stats,
        "uncertain_breakdown": unc_stats,
        "invalid_evidence_citation_count": sum(
            1 for r in detection_runs
            if r.get("error_type") == "AiInvalidEvidenceCitationError"
            or (r.get("status") == "SUCCESS" and not r.get("evidence_ids_valid"))
        ),
        "structured_output_failure_count": sum(
            1 for r in detection_runs
            if r.get("error_type") in ("AiStructuredOutputError", "AiInvalidEvidenceCitationError")
        ),
        "backend_failure_count": sum(
            1 for r in detection_runs
            if r.get("error_type") in ("AiBackendUnavailableError", "AiBackendRequestError", "AiBackendTimeoutError", "AiBackendProtocolError")
        ),
        "model_mismatch_count": sum(1 for r in detection_runs if r.get("error_type") == "AiModelMismatchError"),
        "average_successful_inference_latency": avg_latency,
        "provider_rpm_limit_observed": 5,
        "provider_tpm_limit_observed": 250000,
        "provider_rpd_limit_observed": 20,
        "minimum_request_interval_seconds": float(min_request_interval_seconds),
        "automatic_retry": False,
        "requests_with_pacing": len(client.request_telemetry),
        "minimum_observed_request_start_delta_seconds": min_delta,
        "average_observed_request_start_delta_seconds": avg_delta,
        "request_start_deltas": list(client.request_start_deltas),
    }

    # 12. Assemblaggio risultato finale
    returned_models = sorted(list({c.get("model_id_returned") for c in case_results if c.get("model_id_returned")}))
    model_id_returned_summary = returned_models[0] if len(returned_models) == 1 else (returned_models if returned_models else None)

    final_output = {
        "benchmark": "PRELIMINARY CLOUD E2E VALIDATION",
        "phase": "PRELIMINARY CLOUD E2E VALIDATION",
        "protocol_version": 3,
        "timestamp_utc": timestamp_utc,
        "provider": "Google Gemini Developer API" if "googleapis.com" in endpoint_url else "OpenAI-compatible",
        "configuration": {
            "endpoint_url": client.safe_endpoint if hasattr(client, "safe_endpoint") else endpoint_url,
            "model_id_requested": model_id,
            "model_id_returned": model_id_returned_summary,
            "json_mode": json_mode,
            "timeout_seconds": timeout_seconds,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
            "seed_requested": seed,
            "seed_provider_status": "UNSUPPORTED_OMITTED" if seed is None else "PROVIDED",
            "synthetic_dataset_sha256": FROZEN_DATASET_SHA256,
            "ground_truth_sha256": FROZEN_GROUND_TRUTH_SHA256,
            "protocol_version": 3,
            "minimum_request_interval_seconds": float(min_request_interval_seconds),
            "provider_rpm_limit_observed": 5,
            "provider_tpm_limit_observed": 250000,
            "provider_rpd_limit_observed": 20,
            "automatic_retry": False,
        },
        "metrics": metrics,
        "cases": case_results,
        "request_telemetry": client.request_telemetry,
    }

    # 13. Salvataggio artefatti (JSON e Markdown)
    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    json_path = out_dir_path / "cloud-e2e-result.json"
    report_path = out_dir_path / "cloud-e2e-report.md"

    json_str = json.dumps(final_output, indent=2, ensure_ascii=False)
    json_sanitized = _sanitize_output_text(json_str, api_key)
    json_path.write_text(json_sanitized, encoding="utf-8")

    # Generazione report Markdown
    report_md = _build_markdown_report(final_output)
    report_md_sanitized = _sanitize_output_text(report_md, api_key)
    report_path.write_text(report_md_sanitized, encoding="utf-8")

    return final_output


def _build_markdown_report(result_data: dict[str, Any]) -> str:
    """Genera il report tabulare e descrittivo in formato Markdown."""
    cfg = result_data["configuration"]
    m = result_data["metrics"]
    cases = result_data["cases"]
    telemetry = result_data.get("request_telemetry", [])

    dec_acc_str = f"{m['decision_accuracy'] * 100:.1f}%" if m.get("decision_accuracy") is not None else "N/A"
    comp_rate_str = f"{m.get('detection_completion_rate', 0.0) * 100:.1f}%"
    seed_disp = f"`{cfg['seed']}`" if cfg.get("seed") is not None else "`null` (UNSUPPORTED_OMITTED)"
    min_delta_disp = f"{m.get('minimum_observed_request_start_delta_seconds')}s" if m.get("minimum_observed_request_start_delta_seconds") is not None else "N/A"
    avg_delta_disp = f"{m.get('average_observed_request_start_delta_seconds')}s" if m.get("average_observed_request_start_delta_seconds") is not None else "N/A"

    lines = [
        "# Preliminary Cloud E2E Benchmark Report — Synthetic Dataset (Protocol V3)",
        "",
        f"> **Tipo Benchmark:** {m['benchmark_type']}",
        f"> **Protocol Version:** {m.get('protocol_version', 3)}",
        f"> **Disclaimer Scientifico:** {m['scientific_disclaimer']}",
        "",
        "## 1. Configurazione dell'Esperimento",
        "",
        f"- **Protocol Version:** `{cfg.get('protocol_version', 3)}`",
        f"- **Timestamp UTC:** `{result_data['timestamp_utc']}`",
        f"- **Provider:** `{result_data.get('provider', 'Google Gemini Developer API')}`",
        f"- **Endpoint:** `{cfg['endpoint_url']}`",
        f"- **Modello Richiesto:** `{cfg['model_id_requested']}`",
        f"- **Modello Restituito:** `{cfg.get('model_id_returned') or 'N/A'}`",
        f"- **Modalità JSON:** `{cfg['json_mode']}`",
        f"- **Timeout:** `{cfg['timeout_seconds']}s` | **Max Tokens:** `{cfg['max_tokens']}`",
        f"- **Temperature:** `{cfg['temperature']}` | **Seed:** {seed_disp}",
        f"- **Pacing Preventivo (Interval):** `{cfg.get('minimum_request_interval_seconds', 15.0)}s`",
        f"- **Limiti Provider Osservati:** `{cfg.get('provider_rpm_limit_observed', 5)} RPM` | `{cfg.get('provider_tpm_limit_observed', 250000)} TPM` | `{cfg.get('provider_rpd_limit_observed', 20)} RPD`",
        "- **Automatic Retry:** `NO (One request = One attempt)`",
        f"- **Dataset SHA-256:** `{cfg['synthetic_dataset_sha256'][:16]}...` (VERIFICATO)",
        f"- **Ground Truth SHA-256:** `{cfg['ground_truth_sha256'][:16]}...` (VERIFICATO)",
        "",
        "## 2. Metriche di Esecuzione e Topic Detection",
        "",
        "### 2.1 Esecuzione Chiamate e Inferenze",
        f"- **Cloud API Requests Sent:** {m.get('cloud_api_requests_sent', 'N/A')}",
        f"- **LLM Inferences Completed:** {m.get('llm_inferences_completed', 'N/A')}",
        f"- **Model Outputs Received:** {m.get('model_outputs_received', 'N/A')}",
        f"- **Detection Completion Rate:** {comp_rate_str}",
        "",
        "### 2.2 Pacing Telemetria Preventivo (Protocol V3)",
        f"- **Intervallo Minimo Configurato:** `{m.get('minimum_request_interval_seconds', 15.0)}s`",
        f"- **Richieste con Pacing:** {m.get('requests_with_pacing', len(telemetry))}",
        f"- **Delta Minimo Osservato Inizio Richieste:** {min_delta_disp}",
        f"- **Delta Medio Osservato Inizio Richieste:** {avg_delta_disp}",
        "- **Policy di Retry:** `NESSUNA (Automatic Retry: False, Selective Retry: False)`",
        "",
        "| Seq | Offset Inizio (s) | Delta da Richiesta Precedente (s) |",
        "|---|---|---|",
    ]

    deltas = m.get("request_start_deltas", [])
    for idx, item in enumerate(telemetry):
        seq = item.get("request_sequence_number", idx + 1)
        offset = item.get("request_start_offset_seconds", 0.0)
        delta_str = f"{deltas[idx - 1]:.3f}s" if (idx > 0 and idx - 1 < len(deltas)) else "N/A (prima richiesta)"
        lines.append(f"| {seq} | {offset:.3f}s | {delta_str} |")

    lines.extend([
        "",
        "### 2.3 Metriche Decisionali",
        "",
        "| Metrica | Valore |",
        "|---|---|",
        f"| Casi Totali Valutati | {m['total_detection_cases']} |",
        f"| Casi Completati con Successo | {m['completed_detection_cases']} |",
        f"| Casi Falliti (Backend/Execution) | {m['failed_detection_cases']} |",
        f"| Detection Completion Rate | {comp_rate_str} |",
        f"| Decisioni Corrette (Casi Completati) | {m['correct_decisions']} / {m['completed_detection_cases']} |",
        f"| Accuratezza Decisionale (Casi Completati) | {dec_acc_str} |",
        f"| PRESENT (Corretti/Completati/Totale) | {m['present_breakdown']['correct']} / {m['present_breakdown'].get('completed', m['present_breakdown']['total'])} ({m['present_breakdown']['total']} totali) |",
        f"| ABSENT (Corretti/Completati/Totale) | {m['absent_breakdown']['correct']} / {m['absent_breakdown'].get('completed', m['absent_breakdown']['total'])} ({m['absent_breakdown']['total']} totali) |",
        f"| UNCERTAIN (Corretti/Completati/Totale) | {m['uncertain_breakdown']['correct']} / {m['uncertain_breakdown'].get('completed', m['uncertain_breakdown']['total'])} ({m['uncertain_breakdown']['total']} totali) |",
        f"| Citazioni Evidenze Invalide | {m['invalid_evidence_citation_count']} |",
        f"| Errori Structured Output | {m['structured_output_failure_count']} |",
        f"| Errori Backend / Rete | {m['backend_failure_count']} |",
        f"| Errori Model Mismatch | {m['model_mismatch_count']} |",
        f"| Latenza Media Inferenza Riuscita | {m['average_successful_inference_latency']}s |",
        "",
        "## 3. Dettaglio Risultati per Caso",
        "",
        "| Caso | Strategia | Atteso | Ottenuto | Esito | Latenza | Token Totali | Status |",
        "|------|-----------|--------|----------|-------|---------|--------------|--------|",
    ])

    for c in cases:
        if c.get("evaluation_mode") == "QUALITATIVE":
            num_topics = len(c.get("topics", []))
            lat = f"{c['latency_seconds']}s" if c.get("latency_seconds") is not None else "N/A"
            tok = str(c.get("total_tokens")) if c.get("total_tokens") is not None else "N/A"
            lines.append(
                f"| {c['case_id']} | {c['strategy']} | Discovery | {num_topics} topics scoperti | QUALITATIVE | {lat} | {tok} | {c['status']} |"
            )
        else:
            exp = c.get("expected_decision") or "-"
            act = c.get("actual_decision") or "-"
            correct_badge = "CORRETTO" if c.get("decision_correct") else ("ERRATO" if c.get("status") == "SUCCESS" else "FAILED")
            lat = f"{c['latency_seconds']}s" if c.get("latency_seconds") is not None else "N/A"
            tok = str(c.get("total_tokens")) if c.get("total_tokens") is not None else "N/A"
            lines.append(
                f"| {c['case_id']} | {c['strategy']} | {exp} | {act} | {correct_badge} | {lat} | {tok} | {c['status']} |"
            )

    lines.extend([
        "",
        "## 4. Dettaglio Topic Discovery (Caso 6 — Valutazione Qualitativa Post-Hoc)",
        "",
    ])

    c6_list = [c for c in cases if c.get("case_id") == "CASE_6_TOPIC_DISCOVERY"]
    if c6_list:
        c6 = c6_list[0]
        if c6["status"] == "SUCCESS":
            lines.append("### Topic Estratti Autonomamente dal Modello:")
            for idx, t in enumerate(c6.get("topics", []), 1):
                lines.append(f"{idx}. **{t['label']}**: {t['description']} (Evidenze: `{t['evidence_ids']}`)")
            lines.append("")
            lines.append("### Topic Attesi Post-Hoc (per confronto qualitativo umano):")
            for idx, exp_t in enumerate(c6.get("post_hoc_expected_topics", []), 1):
                lines.append(f"{idx}. **{exp_t['label']}** (Keywords: {', '.join(exp_t.get('keywords', []))})")
        else:
            lines.append(f"Topic Discovery non riuscito. Errore: `{c6.get('error_type')}` — `{c6.get('error_message_sanitized')}`")

    lines.extend([
        "",
        "## 5. Criteri di Interpretazione e Distinzione Errori",
        "",
        "- **HTTP 429 (Too Many Requests) / HTTP 503 (Service Unavailable):** Classificati come **Execution Failure** dovuti ai vincoli di quota (5 RPM) o disponibilità del provider, **NON** come errori decisionali del modello.",
        "- **Citazioni Evidenze Invalide / Structured Output Error:** Classificati come **Model Format / Compliance Failure**.",
        "- **Decisione Dissonante da Ground Truth:** Classificata come **Model Decision Error**.",
        "- **Nessun Retry:** Ogni richiesta viene eseguita esattamente una volta; fallimenti 429 o 503 non vengono ripetuti.",
        "",
        "---",
        "**Dichiarazioni di Sicurezza:**",
        "- Nessun dato reale o personale è stato elaborato (SYNTHETIC DATA ONLY).",
        "- Nessuna API key o credenziale è stata salvata in chiaro nei report.",
        "- Il percorso finale della tesi rimane su hardware locale (LM Studio + Qwen/Llama/DeepSeek).",
    ])

    return "\n".join(lines)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Configura e valida gli argomenti della CLI per il runner sperimentale."""
    parser = argparse.ArgumentParser(
        description="Runner sperimentale OPT-IN per la validazione E2E preliminare con LLM cloud su dataset sintetico."
    )
    parser.add_argument(
        "--endpoint-url",
        required=True,
        help="URL HTTPS dell'endpoint compatibile OpenAI (es. 'https://api.openai.com/v1').",
    )
    parser.add_argument(
        "--model-id",
        required=True,
        help="Identificativo esatto del modello remoto (es. 'gpt-4o', 'gpt-4o-mini').",
    )
    parser.add_argument(
        "--api-key-env-var",
        default="CLOUD_LLM_API_KEY",
        help="Nome della variabile d'ambiente contenente l'API key (default: CLOUD_LLM_API_KEY).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout in secondi per le chiamate HTTP (default: 60.0).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Numero massimo di token di output (default: 1024).",
    )
    parser.add_argument(
        "--force-run",
        action="store_true",
        help="Flag CLI obbligatorio per l'opt-in esplicito (deve essere accompagnato da RUN_CLOUD_E2E=1).",
    )
    parser.add_argument(
        "--json-mode",
        choices=["json_schema", "json_object"],
        default="json_schema",
        help="Modalità di enforcement JSON del provider (default: json_schema).",
    )
    parser.add_argument(
        "--output-dir",
        default="output/cloud_e2e_v3",
        help="Cartella di destinazione per i file di report (default: 'output/cloud_e2e_v3').",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperatura di campionamento (default: 0.0 per massima riproducibilità).",
    )
    parser.add_argument(
        "--seed",
        type=_parse_seed_arg,
        default=42,
        help="Seed deterministico per il completamento (default: 42, 'none' per omettere).",
    )
    parser.add_argument(
        "--min-request-interval-seconds",
        type=_parse_min_request_interval_arg,
        default=15.0,
        help="Intervallo minimo preventivo in secondi tra l'inizio di due richieste consecutive (default: 15.0).",
    )

    return parser.parse_args(args)


def main(cli_args: list[str] | None = None) -> int:
    """Punto di ingresso CLI del runner."""
    args = None
    configured_key: str | None = None
    try:
        args = parse_args(cli_args)
        configured_key = os.environ.get(args.api_key_env_var)
        results = run_cloud_benchmark(
            endpoint_url=args.endpoint_url,
            model_id=args.model_id,
            api_key_env_var=args.api_key_env_var,
            timeout_seconds=args.timeout,
            max_tokens=args.max_tokens,
            force_run=args.force_run,
            json_mode=args.json_mode,
            output_dir=args.output_dir,
            temperature=args.temperature,
            seed=args.seed,
            min_request_interval_seconds=args.min_request_interval_seconds,
        )
        failed_cases = results["metrics"].get("failed_detection_cases", 0)
        discovery_case = next((c for c in results.get("cases", []) if c.get("case_id") == "CASE_6_TOPIC_DISCOVERY"), None)
        discovery_failed = (discovery_case is not None and discovery_case.get("status") == "FAILED")
        has_failures = failed_cases > 0 or discovery_failed

        print("\n" + "=" * 60)
        if has_failures:
            print("PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON FAILURE REGISTRATE")
        else:
            print("PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON SUCCESSO")
        print("=" * 60)
        acc_val = results["metrics"]["decision_accuracy"]
        acc_str = f"{acc_val * 100:.1f}%" if acc_val is not None else "N/A"
        print(f"Protocol Version:  {results.get('protocol_version', 3)}")
        print(f"Pacing Interval:   {results['configuration'].get('minimum_request_interval_seconds', 15.0)}s (Automatic Retry: NO)")
        if results['metrics'].get('minimum_observed_request_start_delta_seconds') is not None:
            print(f"Min Start Delta:   {results['metrics']['minimum_observed_request_start_delta_seconds']}s")
            print(f"Avg Start Delta:   {results['metrics']['average_observed_request_start_delta_seconds']}s")
        print(f"Decision Accuracy: {acc_str}")
        print(f"Completion Rate:   {results['metrics']['detection_completion_rate'] * 100:.1f}%")
        print(f"Casi Riusciti:     {results['metrics']['completed_detection_cases']} / {results['metrics']['total_detection_cases']}")
        print(f"API Requests Sent: {results['metrics']['cloud_api_requests_sent']}")
        print(f"Inferences Done:   {results['metrics']['llm_inferences_completed']}")
        print(f"Outputs Received:  {results['metrics']['model_outputs_received']}")
        print(f"Report salvati in: {args.output_dir}")
        print("=" * 60 + "\n")
        return 0
    except CloudExperimentOptInError as e:
        env_var = getattr(args, "api_key_env_var", "CLOUD_LLM_API_KEY") if args else "CLOUD_LLM_API_KEY"
        key_to_sanitize = configured_key or os.environ.get(env_var)
        print(f"\n[ERRORE OPT-IN] {_sanitize_output_text(str(e), key_to_sanitize)}\n", file=sys.stderr)
        sys.exit(2)
    except CloudExperimentIntegrityError as e:
        env_var = getattr(args, "api_key_env_var", "CLOUD_LLM_API_KEY") if args else "CLOUD_LLM_API_KEY"
        key_to_sanitize = configured_key or os.environ.get(env_var)
        print(f"\n[ERRORE INTEGRITÀ DATASET] {_sanitize_output_text(str(e), key_to_sanitize)}\n", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        env_var = getattr(args, "api_key_env_var", "CLOUD_LLM_API_KEY") if args else "CLOUD_LLM_API_KEY"
        key_to_sanitize = configured_key or os.environ.get(env_var)
        print(f"\n[ERRORE ESECUZIONE RUNNER] {_sanitize_output_text(str(e), key_to_sanitize)}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

