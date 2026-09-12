"""
ai/backend.py
-------------
Contratto astratto, risposte ed eccezioni per i client LLM locali (BaseLocalLlmClient, FakeLocalLlmClient).

Principi architetturali:
1. NESSUNA DIPENDENZA CLOUD:
   Interfaccia progettata esclusivamente per runtime locali (LM Studio, Ollama locale, CTranslate2).
2. CONTRATTO UNIFORME CON MODEL ID ESPLICITO:
   Supporto per is_available(), list_models() e chat_completion() con model_id esplicito
   e parametri di riproducibilità (temperature, seed).
3. ISOLAMENTO TEST OFFLINE:
   FakeLocalLlmClient garantisce l'esecuzione deterministica, veloce e offline della suite pytest senza richiedere server accesi o modelli scaricati.
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


class AnalysisInputTooLargeError(AiBackendError):
    """Sollevata quando l'input supera la dimensione massima gestibile dal contesto o dal chunker."""
    pass


class AiModelNotSpecifiedError(AiBackendError):
    """Sollevata quando una richiesta di completamento o benchmark non specifica un model_id esplicito."""
    pass


class AiModelMismatchError(AiBackendError):
    """Sollevata quando il modello restituito/utilizzato dal backend differisce da quello atteso."""
    pass


class AiBackendProtocolError(AiBackendError):
    """Sollevata quando la risposta del backend viola il protocollo atteso (es. JSON malformato, campi mancanti)."""
    pass


class AiBackendRequestError(AiBackendError):
    """Sollevata per errori di richiesta HTTP (es. codici di stato 4xx o 5xx restituiti dal backend)."""
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


class BaseLocalLlmClient(ABC):
    """
    Contratto astratto unificato per client LLM locali.
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Verifica se il server locale è attivo e pronto a ricevere inferenze."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> tuple[str, ...]:
        """Restituisce la lista degli identificativi modello disponibili nel backend locale."""
        raise NotImplementedError

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
        canned_responses: dict[Any, str] | None = None,
        default_response: str = "",
        simulate_error: Exception | None = None,
        model_name: str = "fake-local-model",
        simulate_model_mismatch: str | None = None,
        require_explicit_model: bool = False,
    ) -> None:
        self._available = available
        self._models = tuple(models)
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
