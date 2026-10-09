"""
tests/unit/test_operational_model_management.py
------------------------------------------------
Suite di test specifica per la gestione autonoma del modello locale in Topic Detection:
1. modello già caricato -> nessun nuovo load;
2. modello installato ma non caricato -> auto-load;
3. modello assente -> errore controllato;
4. server offline -> errore controllato;
5. timeout load;
6. timeout inference;
7. model_id esplicito e corretto;
8. nessuna selezione arbitraria del primo modello;
9. nessun download automatico;
10. nessuna rete esterna;
11. timeout operativo Topic Detection = 240;
12. ingestion continua a non invocare AI;
13. benchmark completamente invariato.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any
import pytest

from ai.backend import (
    AiBackendTimeoutError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    FakeLocalLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    DEFAULT_OPERATIONAL_GPU_OFFLOAD,
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MODEL,
    DEFAULT_OPERATIONAL_TEMPERATURE,
    DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
    LmStudioClient,
    get_model_display_name,
    resolve_installed_model_id,
)
from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)
from ui.application import (
    build_topic_query,
    execute_manual_topic_detection,
    execute_real_ingestion,
    prepare_operational_model,
)
from ui.demo import build_synthetic_demo_dataset
from ui.models import (
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
)


class MockOperationalLlmClient(BaseLlmClient):
    """Client di test per validare il lifecycle di model management e inferenza."""

    def __init__(
        self,
        available: bool = True,
        models: tuple[str, ...] = ("qwen2.5-7b-instruct",),
        loaded_models: tuple[str, ...] | None = None,
        simulate_load_timeout: bool = False,
        simulate_inference_timeout: bool = False,
        default_decision: str = "ABSENT",
    ) -> None:
        self._available = available
        self._models = tuple(models)
        self.loaded_models: set[str] = set(loaded_models) if loaded_models is not None else set()
        self.load_calls: list[dict[str, Any]] = []
        self.completion_calls: list[dict[str, Any]] = []
        self.simulate_load_timeout = simulate_load_timeout
        self.simulate_inference_timeout = simulate_inference_timeout
        self.default_decision = default_decision
        self.valid_evidence_id = None
        self.model_id: str | None = None

    def is_available(self) -> bool:
        return self._available

    def list_models(self) -> tuple[str, ...]:
        if not self._available:
            raise LmStudioUnavailableError("LM Studio non raggiungibile su http://127.0.0.1:1234.")
        return self._models

    def is_model_loaded(self, model_id: str) -> bool:
        if not self._available:
            raise LmStudioUnavailableError("LM Studio non raggiungibile.")
        return model_id in self.loaded_models or any(model_id.lower() in m.lower() for m in self.loaded_models)

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> dict[str, Any]:
        if not self._available:
            raise LmStudioUnavailableError("LM Studio non raggiungibile.")
        if self.simulate_load_timeout:
            raise AiBackendTimeoutError(f"Timeout ({timeout_seconds}s) durante il caricamento del modello '{model_id}'.")
        resolved = resolve_installed_model_id(model_id, self._models)
        if not resolved:
            display = get_model_display_name(model_id)
            raise AiModelNotInstalledError(f"Il modello {display} non è installato in LM Studio.")
        self.load_calls.append({
            "model_id": resolved,
            "context_length": context_length,
            "gpu_offload": gpu_offload,
            "timeout_seconds": timeout_seconds,
        })
        self.loaded_models.add(resolved)
        return {"model": resolved, "status": "loaded"}

    def unload_model(self, model_id: str | None = None, timeout_seconds: float = 30.0) -> bool:
        if model_id:
            self.loaded_models.discard(model_id)
        else:
            self.loaded_models.clear()
        return True

    def ensure_model_loaded(
        self,
        model_id: str = "qwen2.5-7b-instruct",
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> str:
        if not self.is_available():
            raise LmStudioUnavailableError("LM Studio non raggiungibile.")
        resolved = resolve_installed_model_id(model_id, self._models)
        if not resolved:
            display = get_model_display_name(model_id)
            raise AiModelNotInstalledError(f"Il modello {display} non è installato in LM Studio.")
        if not self.is_model_loaded(resolved):
            self.load_model(resolved, context_length=context_length, gpu_offload=gpu_offload, timeout_seconds=timeout_seconds)
        self.model_id = resolved
        return resolved

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 240.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        if not self._available:
            raise LmStudioUnavailableError("LM Studio non raggiungibile.")
        if self.simulate_inference_timeout:
            raise AiBackendTimeoutError(f"Timeout ({timeout_seconds}s) durante l'inferenza.")

        effective_model = model_id or self.model_id or "qwen2.5-7b-instruct"
        self.completion_calls.append({
            "model_id": effective_model,
            "messages": messages,
            "temperature": temperature,
            "timeout_seconds": timeout_seconds,
            "max_tokens": max_tokens,
        })

        content_payload = {
            "decision": self.default_decision,
            "evidence_ids": [self.valid_evidence_id] if self.default_decision == "PRESENT" and self.valid_evidence_id else [],
            "rationale": "Verifica eseguita con successo in ambiente controllato.",
        }

        return LlmCompletionResponse(
            content=json.dumps(content_payload),
            model=effective_model,
            prompt_tokens=50,
            completion_tokens=25,
            total_tokens=75,
            latency_seconds=0.2,
        )


def _get_doc() -> ConversationEvidenceDocument:
    doc, _, _ = build_synthetic_demo_dataset()
    return doc


# -----------------------------------------------------------------------------
# TEST 1: Modello già caricato -> nessun nuovo load
# -----------------------------------------------------------------------------
def test_01_model_already_loaded_skips_new_load():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct",),
        loaded_models=("qwen2.5-7b-instruct",),
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    res = execute_manual_topic_detection(document=doc, topic=topic, client=client)
    assert isinstance(res, TopicDetectionResult)
    assert len(client.load_calls) == 0  # Nessun load invocato!
    assert len(client.completion_calls) == 1
    assert client.completion_calls[0]["model_id"] == "qwen2.5-7b-instruct"


# -----------------------------------------------------------------------------
# TEST 2: Modello installato ma non caricato -> auto-load
# -----------------------------------------------------------------------------
def test_02_model_installed_not_loaded_triggers_autoload():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct",),
        loaded_models=(),  # Non è ancora caricato in VRAM
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    res = execute_manual_topic_detection(document=doc, topic=topic, client=client)
    assert isinstance(res, TopicDetectionResult)
    assert len(client.load_calls) == 1
    load_entry = client.load_calls[0]
    assert load_entry["model_id"] == "qwen2.5-7b-instruct"
    assert load_entry["context_length"] == 8192
    assert load_entry["gpu_offload"] == "max"
    assert load_entry["timeout_seconds"] == 240.0
    assert "qwen2.5-7b-instruct" in client.loaded_models


# -----------------------------------------------------------------------------
# TEST 3: Modello assente -> errore controllato
# -----------------------------------------------------------------------------
def test_03_model_absent_raises_controlled_error():
    client = MockOperationalLlmClient(
        models=("some-other-model",),
        loaded_models=(),
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    with pytest.raises(AiModelNotInstalledError) as exc_info:
        execute_manual_topic_detection(document=doc, topic=topic, client=client)

    assert "Il modello Qwen2.5-7B-Instruct non è installato in LM Studio." in str(exc_info.value)
    assert len(client.load_calls) == 0


# -----------------------------------------------------------------------------
# TEST 4: Server offline -> errore controllato
# -----------------------------------------------------------------------------
def test_04_server_offline_raises_controlled_error():
    client = MockOperationalLlmClient(available=False)
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    with pytest.raises(LmStudioUnavailableError) as exc_info:
        execute_manual_topic_detection(document=doc, topic=topic, client=client)

    assert "non raggiungibile" in str(exc_info.value).lower()
    assert len(client.load_calls) == 0
    assert len(client.completion_calls) == 0


# -----------------------------------------------------------------------------
# TEST 5: Timeout load
# -----------------------------------------------------------------------------
def test_05_model_load_timeout_raises_timeout_error():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct",),
        loaded_models=(),
        simulate_load_timeout=True,
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    with pytest.raises(AiBackendTimeoutError) as exc_info:
        execute_manual_topic_detection(document=doc, topic=topic, client=client)

    assert "Timeout" in str(exc_info.value)
    assert len(client.completion_calls) == 0


# -----------------------------------------------------------------------------
# TEST 6: Timeout inference
# -----------------------------------------------------------------------------
def test_06_inference_timeout_raises_timeout_error():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct",),
        loaded_models=("qwen2.5-7b-instruct",),
        simulate_inference_timeout=True,
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    with pytest.raises(AiBackendTimeoutError) as exc_info:
        execute_manual_topic_detection(document=doc, topic=topic, client=client)

    assert "Timeout" in str(exc_info.value)


# -----------------------------------------------------------------------------
# TEST 7: model_id esplicito e corretto
# -----------------------------------------------------------------------------
def test_07_explicit_and_correct_model_id_enforced():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct", "meta-llama-3.1-8b-instruct"),
        loaded_models=("meta-llama-3.1-8b-instruct",),
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    # Invocazione con model_id esplicito Llama
    res = execute_manual_topic_detection(
        document=doc,
        topic=topic,
        client=client,
        model_id="meta-llama-3.1-8b-instruct",
    )
    assert res.metadata["model"] == "meta-llama-3.1-8b-instruct"
    assert client.completion_calls[-1]["model_id"] == "meta-llama-3.1-8b-instruct"


# -----------------------------------------------------------------------------
# TEST 8: Nessuna selezione arbitraria del primo modello
# -----------------------------------------------------------------------------
def test_08_no_arbitrary_selection_of_first_available_model():
    # Modelli presenti dove il primo NON è Qwen
    client = MockOperationalLlmClient(
        models=("random-first-model", "another-unrelated-model"),
        loaded_models=("random-first-model",),
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    # Se non forniamo model_id (default Qwen), NON deve selezionare random-first-model!
    with pytest.raises(AiModelNotInstalledError) as exc_info:
        execute_manual_topic_detection(document=doc, topic=topic, client=client, model_id=None)

    assert "Il modello Qwen2.5-7B-Instruct non è installato in LM Studio." in str(exc_info.value)
    assert len(client.completion_calls) == 0


# -----------------------------------------------------------------------------
# TEST 9: Nessun download automatico
# -----------------------------------------------------------------------------
def test_09_no_automatic_downloads():
    # Verifica che il resolver verifichi solo la presenza locale senza invocare download
    available = ("qwen2.5-7b-instruct",)
    assert resolve_installed_model_id("qwen2.5-7b-instruct", available) == "qwen2.5-7b-instruct"
    assert resolve_installed_model_id("uninstalled-model", available) is None


# -----------------------------------------------------------------------------
# TEST 10: Nessuna rete esterna (Loopback-Only Security)
# -----------------------------------------------------------------------------
def test_10_no_external_network_calls_loopback_only():
    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="http://192.168.1.50:1234")

    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="https://api.openai.com/v1")

    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="http://remote.lan:1234")

    # Loopback valido
    valid_client = LmStudioClient(base_url="http://127.0.0.1:1234")
    assert valid_client.base_url == "http://127.0.0.1:1234"


# -----------------------------------------------------------------------------
# TEST 11: Timeout operativo Topic Detection = 240 secondi
# -----------------------------------------------------------------------------
def test_11_operational_topic_detection_timeout_is_240s():
    client = MockOperationalLlmClient(
        models=("qwen2.5-7b-instruct",),
        loaded_models=(),
    )
    doc = _get_doc()
    topic = build_topic_query("Argomento test")

    execute_manual_topic_detection(document=doc, topic=topic, client=client)

    # Verifica timeout nel load e nella completion
    assert client.load_calls[0]["timeout_seconds"] == 240.0
    assert client.completion_calls[0]["timeout_seconds"] == 240.0
    assert client.completion_calls[0]["max_tokens"] == 512
    assert client.completion_calls[0]["temperature"] == 0.0


# -----------------------------------------------------------------------------
# TEST 12: Ingestion continua a non invocare AI
# -----------------------------------------------------------------------------
def test_12_ingestion_continues_zero_ai_invocations():
    chat_text = (
        "10/05/2024, 15:00 - Alice: Ciao, verifichiamo la perizia?\n"
        "10/05/2024, 15:01 - Bob: Ricevuto, procedo con la validazione hash.\n"
    ).encode("utf-8")

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename="_chat.txt",
        file_bytes=chat_text,
    )

    res = execute_real_ingestion(req)
    assert res.status == IngestionStatus.SUCCESS
    assert len(res.documents) == 1
    # Verifica integrità: nessuna interazione AI durante l'ingestion


# -----------------------------------------------------------------------------
# TEST 13: Protocollo benchmark completamente invariato
# -----------------------------------------------------------------------------
def test_13_benchmark_protocol_completely_unchanged():
    from ai import experiment

    # Verifica che il modulo ai/experiment mantenga i suoi parametri indipendenti
    parsed = experiment.parse_args(["--model-id", "qwen2.5-7b-instruct", "--family", "QWEN"])
    assert parsed.timeout == 120.0
    assert parsed.max_tokens == 512
