"""
ai/backend.py
-------------
Contratti astratti, risposte ed eccezioni per i client LLM (BaseLlmClient, BaseLocalLlmClient, FakeLocalLlmClient).

Architettura delle interfacce:
- BaseLlmClient:
  Contratto fondamentale generico per l'operazione di chat_completion() con model_id esplicito,
  schema JSON e parametri di riproducibilità (temperature, seed, max_tokens, timeout_seconds).
  Implementato sia da runtime locali sia da adapter remoti sperimentali (es. RemoteOpenAICompatibleTestClient).
- BaseLocalLlmClient:
  Specializzazione locale del contratto base che estende BaseLlmClient richiedendo metodi di verifica
  presenza locale (is_available()) e introspezione dei modelli residenti (list_models()).
  Implementato da LmStudioClient e FakeLocalLlmClient.

Principi architetturali:
1. PERCORSO FORENSE LOCALE PRIORITARIO:
   L'architettura definitiva della tesi si fonda su runtime locali (LM Studio su loopback)
   e modelli Open Weight (Qwen, Llama, DeepSeek). Nessun dato reale transita su canali esterni.
2. CONTRATTO UNIFORME CON MODEL ID ESPLICITO:
   Ogni invocazione richiede model_id esplicito per garantire tracciabilità e riproducibilità scientifica.
3. ISOLAMENTO TEST DETERMINISTICI E OFFLINE:
   FakeLocalLlmClient garantisce l'esecuzione deterministica, veloce e offline della suite pytest
   senza richiedere server attivi o modelli residenti in memoria.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import time
from typing import Any, Mapping

from core.immutability import freeze_structural


class AiBackendError(Exception):
    """Eccezione base per errori nel layer AI."""
    pass


class AiBackendUnavailableError(AiBackendError):
    """Sollevata quando il server o backend AI locale non è raggiungibile o non risponde."""
    pass


class LmStudioUnavailableError(AiBackendUnavailableError):
    """Sollevata specificamente quando il server locale LM Studio è spento o irraggiungibile."""
    pass


class AiBackendTimeoutError(AiBackendError):
    """Sollevata quando una richiesta verso il backend AI scade per timeout."""
    pass


class AiStructuredOutputError(AiBackendError):
    """Sollevata quando l'output restituito dall'LLM non rispetta il formato JSON o lo schema atteso."""
    pass


class AiInvalidEvidenceCitationError(AiStructuredOutputError):
    """Sollevata quando il modello cita evidence_id inesistenti, allucinati o non consentiti."""

    def __init__(
        self,
        message: str,
        invalid_evidence_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.invalid_evidence_ids = invalid_evidence_ids


class AnalysisInputTooLargeError(AiBackendError):
    """Sollevata quando l'input supera la dimensione massima gestibile dal contesto o dal chunker."""
    pass


class AiModelNotSpecifiedError(AiBackendError):
    """Sollevata quando una richiesta di completamento o benchmark non specifica un model_id esplicito."""
    pass


class AiModelNotInstalledError(AiModelNotSpecifiedError):
    """Sollevata quando il modello richiesto non è presente tra quelli installati/disponibili localmente."""
    pass


class AiModelMismatchError(AiBackendError):
    """Sollevata quando il modello restituito/utilizzato dal backend differisce da quello atteso."""

    def __init__(
        self,
        message: str,
        requested_model: str | None = None,
        returned_model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.requested_model = requested_model
        self.returned_model = returned_model


class AiBackendProtocolError(AiBackendError):
    """Sollevata quando la risposta del backend viola il protocollo atteso (es. JSON malformato, campi mancanti)."""
    pass


class AiBackendRequestError(AiBackendError):
    """Sollevata per errori di richiesta HTTP (es. codici di stato 4xx o 5xx restituiti dal backend)."""
    pass


class AiModelLoadError(AiBackendRequestError):
    """Sollevata quando si verifica un errore durante il caricamento di un modello in LM Studio."""
    pass


@dataclass(frozen=True)
class LlmCompletionResponse:
    """
    Risposta immutabile da una richiesta di chat completion locale.
    """
    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_seconds: float = 0.0
    raw_response: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_response", freeze_structural(self.raw_response))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


class BaseLlmClient(ABC):
    """
    Contratto astratto fondamentale per client LLM (locali o sperimentali remoti).
    Definisce l'operazione essenziale di chat completion.
    """

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> LlmCompletionResponse:
        """
        Esegue una chiamata di chat completion con configurazione orientata alla riproducibilità.
        """
        raise NotImplementedError


class BaseLocalLlmClient(BaseLlmClient):
    """
    Contratto astratto unificato per client LLM locali.
    Estende BaseLlmClient richiedendo controlli di presenza locale e introspezione dei modelli residenti.
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Verifica se il server locale è attivo e pronto a ricevere inferenze."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> tuple[str, ...]:
        """Restituisce la lista degli identificativi modello disponibili nel backend locale."""
        raise NotImplementedError

    def is_model_loaded(self, model_id: str) -> bool:
        """Verifica se il modello specificato è attualmente caricato in memoria."""
        return False

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> dict[str, Any]:
        """Carica il modello nel runtime locale."""
        return {"model": model_id, "status": "loaded"}

    def unload_model(self, model_id: str | None = None, timeout_seconds: float = 30.0) -> bool:
        """Scarica il modello dal runtime locale."""
        return True

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> LlmCompletionResponse:
        """
        Esegue una chiamata di chat completion con configurazione orientata alla riproducibilità.
        """
        raise NotImplementedError


class FakeLocalLlmClient(BaseLocalLlmClient):
    """
    Client deterministico in-memory per test unitari e simulazioni offline senza server.
    """

    def __init__(
        self,
        available: bool = True,
        models: tuple[str, ...] = ("qwen-2.5-1.5b", "llama-3.2-3b", "deepseek-r1-1.5b"),
        loaded_models: tuple[str, ...] | None = None,
        canned_responses: dict[Any, str] | None = None,
        default_response: str = "",
        simulate_error: Exception | None = None,
        simulate_load_error: Exception | None = None,
        model_name: str = "fake-local-model",
        simulate_model_mismatch: str | None = None,
        require_explicit_model: bool = False,
    ) -> None:
        self._available = available
        self._models = tuple(models)
        self._loaded_models = set(loaded_models) if loaded_models is not None else set()
        self.load_history: list[dict[str, Any]] = []
        self._simulate_load_error = simulate_load_error
        self._canned_responses = canned_responses or {}
        self._default_response = default_response
        self._simulate_error = simulate_error
        self.model_name = model_name
        self.simulate_model_mismatch = simulate_model_mismatch
        self.require_explicit_model = require_explicit_model
        self.call_history: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self._available

    def list_models(self) -> tuple[str, ...]:
        if not self._available:
            raise LmStudioUnavailableError("Fake client simulato come non disponibile.")
        return self._models

    def is_model_loaded(self, model_id: str) -> bool:
        if not self._available:
            raise LmStudioUnavailableError("Fake client simulato come non disponibile.")
        return model_id in self._loaded_models or any(
            model_id.lower() in m.lower() for m in self._loaded_models
        )

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> dict[str, Any]:
        if not self._available:
            raise LmStudioUnavailableError("Fake client simulato come non disponibile.")
        if self._simulate_load_error is not None:
            raise self._simulate_load_error
        # Verifica che il modello sia tra quelli installati
        found = any(model_id.lower() in m.lower() or m.lower() in model_id.lower() for m in self._models)
        if not found and model_id not in self._models:
            raise AiModelNotInstalledError(f"Il modello {model_id} non è installato in LM Studio.")
        self.load_history.append({
            "model_id": model_id,
            "context_length": context_length,
            "gpu_offload": gpu_offload,
            "timeout_seconds": timeout_seconds,
        })
        self._loaded_models.add(model_id)
        return {"model": model_id, "status": "loaded"}

    def unload_model(self, model_id: str | None = None, timeout_seconds: float = 30.0) -> bool:
        if model_id:
            self._loaded_models.discard(model_id)
        else:
            self._loaded_models.clear()
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> LlmCompletionResponse:
        if not self._available:
            raise LmStudioUnavailableError("Server locale LM Studio non raggiungibile.")

        if self._simulate_error is not None:
            raise self._simulate_error

        if self.require_explicit_model and model_id is None:
            raise AiModelNotSpecifiedError("model_id esplicito obbligatorio ma non specificato.")

        effective_model = self.simulate_model_mismatch or model_id or self.model_name

        # Registra la chiamata per audit nei test
        self.call_history.append({
            "model_id": model_id,
            "effective_model": effective_model,
            "messages": messages,
            "schema": schema,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
        })

        # Ricerca risposta programmata sui messaggi inviati
        full_text = " ".join(m.get("content", "") for m in messages)

        content = self._default_response
        for trigger, resp in self._canned_responses.items():
            if isinstance(trigger, tuple):
                if all(t in full_text for t in trigger):
                    content = resp
                    break
            elif isinstance(trigger, str) and trigger in full_text:
                content = resp
                break

        return LlmCompletionResponse(
            content=content,
            model=effective_model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_seconds=0.001,
            raw_response={"model": effective_model, "finish_reason": "stop"},
            metadata={
                "backend": "fake_local",
                "temperature": temperature,
                "seed": seed,
            },
        )
