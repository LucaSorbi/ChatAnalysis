"""
ai/lmstudio.py
--------------
Adapter client per server locale LM Studio (OpenAI-compatible REST API).

Caratteristiche di sicurezza e architetturali:
1. LOOPBACK-ONLY SECURITY (G1):
   Accetta esclusivamente indirizzi locali di loopback (127.0.0.1, localhost, [::1]).
   Rifiuta categoricamente qualsiasi host remoto o non locale per prevenire leak forensi.
2. ZERO EXTRA DEPENDENCIES (G2):
   Implementato su standard library Python (urllib.request, json).
3. AUTENTICAZIONE OPZIONALE (G3):
   Supporta token di sicurezza opzionale letto da parametro o da variabile d'ambiente (LM_STUDIO_API_TOKEN).
4. TASSONOMIA DEGLI ERRORI E DIVIETO DI AUTO-SELEZIONE (Fasi A, E):
   - Richiede un model_id esplicito; solleva AiModelNotSpecifiedError se non fornito.
   - Non seleziona mai silentemente il primo modello disponibile.
   - Distingue errori di connessione (LmStudioUnavailableError), protocollo (AiBackendProtocolError)
     e richieste HTTP (AiBackendRequestError).
   - is_available() cattura solo errori di rete/socket noti; i bug programmatici propagano.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelNotSpecifiedError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)

_ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionRefusedError, OSError)


def _is_timeout_error(exc: BaseException) -> bool:
    """Determina se un'eccezione di rete o socket è dovuta a un timeout effettivo."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc.reason, str) and "timed out" in exc.reason.lower():
            return True
    return False


def _validate_loopback_url(base_url: str) -> urllib.parse.ParseResult:
    """Valida che l'URL di base punti rigorosamente all'interfaccia di loopback locale."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Schema URL non valido: {parsed.scheme} (richiesto http)")

    hostname = parsed.hostname or ""
    if hostname.lower() not in _ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            f"Per motivi di sicurezza forense, LmStudioClient accetta esclusivamente host loopback "
            f"(127.0.0.1, localhost, ::1), ricevuto '{hostname}'."
        )
    return parsed


class LmStudioClient(BaseLocalLlmClient):
    """
    Client per interagire con l'istanza locale di LM Studio.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        model_id: str | None = None,
        api_token: str | None = None,
        default_timeout_seconds: float = 30.0,
        debug_preserve_raw: bool = False,
    ) -> None:
        _validate_loopback_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        # Token letto opzionalmente da parametro o da ambiente
        self._api_token = api_token or os.environ.get("LM_STUDIO_API_TOKEN")
        self.default_timeout_seconds = default_timeout_seconds
        self.debug_preserve_raw = debug_preserve_raw

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ForensicTopicAnalyzer/1.0",
        }
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    def is_available(self) -> bool:
        """
        Verifica la raggiungibilità del server locale LM Studio interrogando /v1/models.
        Intercetta esclusivamente errori di rete/socket noti; errori programmatici propagano.
        """
        url = f"{self.base_url}/v1/models"
        req = urllib.request.Request(url, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except _NETWORK_ERRORS:
            return False

    def list_models(self) -> tuple[str, ...]:
        """
        Recupera la lista degli identificativi modello disponibili interrogando /v1/models.
        Distingue tra indisponibilità del server, timeout ed errore di protocollo.
        """
        url = f"{self.base_url}/v1/models"
        req = urllib.request.Request(url, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.default_timeout_seconds) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            raise AiBackendRequestError(f"Errore HTTP {e.code} durante list_models da {self.base_url}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(
                    f"Timeout durante list_models verso LM Studio ({self.base_url}): {e}"
                ) from e
            raise LmStudioUnavailableError(
                f"Impossibile contattare il server LM Studio su {self.base_url}: {e}"
            ) from e

        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise AiBackendProtocolError(f"Risposta non valida da LM Studio (JSON malformato in list_models): {e}") from e

        if not isinstance(data, dict) or "data" not in data or not isinstance(data["data"], list):
            raise AiBackendProtocolError("Risposta non conforme al protocollo /v1/models (campo 'data' mancante o non lista).")

        models: list[str] = []
        for item in data["data"]:
            if isinstance(item, dict) and "id" in item:
                models.append(str(item["id"]))
        return tuple(models)

    def get_models_detailed(self) -> list[dict[str, Any]]:
        """
        Interroga /api/v1/models (se supportato da LM Studio) o ricade su /v1/models per recuperare metadati ricchi.
        Esegue fallback solo per codici di stato attesi (es. 404, 405, 501). Se /api/v1/models risponde HTTP 200
        ma restituisce JSON malformato o schema non conforme, solleva AiBackendProtocolError senza silent fallback.
        """
        url_api = f"{self.base_url}/api/v1/models"
        req_api = urllib.request.Request(url_api, headers=self._get_headers(), method="GET")
        endpoint_available = True
        try:
            with urllib.request.urlopen(req_api, timeout=self.default_timeout_seconds) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 405, 501):
                endpoint_available = False
            else:
                raise AiBackendRequestError(f"Errore HTTP {e.code} interrogando {url_api}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(f"Timeout durante richiesta a {url_api}: {e}") from e
            # Se la connessione fallisce per connection refused o simile, il server intero è giù
            raise LmStudioUnavailableError(f"Impossibile contattare {url_api}: {e}") from e

        if endpoint_available:
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise AiBackendProtocolError(f"JSON malformato ricevuto da {url_api}: {e}") from e

            if isinstance(data, dict):
                if "models" in data and isinstance(data["models"], list):
                    return data["models"]
                if "data" in data and isinstance(data["data"], list):
                    return data["data"]
            raise AiBackendProtocolError(f"Risposta da {url_api} non conforme al protocollo atteso ('models' o 'data' mancante o non lista).")

        # 2. Ricaduta su standard /v1/models (solo se /api/v1/models ha restituito 404/405/501)
        url_v1 = f"{self.base_url}/v1/models"
        req_v1 = urllib.request.Request(url_v1, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req_v1, timeout=self.default_timeout_seconds) as resp:
                raw_bytes_v1 = resp.read()
        except urllib.error.HTTPError as e:
            raise AiBackendRequestError(f"Errore HTTP {e.code} interrogando {url_v1}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(f"Timeout durante richiesta a {url_v1}: {e}") from e
            raise LmStudioUnavailableError(
                f"Impossibile recuperare metadati modelli da LM Studio ({self.base_url}): {e}"
            ) from e

        try:
            data = json.loads(raw_bytes_v1.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise AiBackendProtocolError(f"JSON non valido restituito da {url_v1}: {e}") from e

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        raise AiBackendProtocolError("Risposta da /v1/models priva di lista 'data' valida.")

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
        Invia una richiesta OpenAI-compatible a POST /v1/chat/completions.
        Richiede un model_id esplicito (passato a chiamata o preimpostato nel client).
        Non seleziona mai modelli in modo automatico.
        Valida rigorosamente che il campo 'model' provenga dalla risposta del server
        e che la forma della risposta rispetti il protocollo atteso.
        """
        target_model = model_id or self.model_id
        if not target_model:
            raise AiModelNotSpecifiedError(
                "Nessun model_id specificato per la chat completion. "
                "Per garantire l'integrità scientifica dell'esperimento, l'identificativo modello deve essere esplicito."
            )

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if seed is not None:
            payload["seed"] = seed
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Supporto per Structured Output (JSON Schema)
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema,
                },
            }

        url = f"{self.base_url}/v1/chat/completions"
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=self._get_headers(), method="POST")

        start_t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                elapsed = round(time.perf_counter() - start_t, 3)
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            raise AiBackendRequestError(f"Errore HTTP {e.code} da LM Studio ({self.base_url}): {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(
                    f"Timeout ({timeout_seconds}s) durante la chat completion su LM Studio ({self.base_url}): {e}"
                ) from e
            raise LmStudioUnavailableError(
                f"Errore di connessione verso LM Studio ({self.base_url}): {e}"
            ) from e

        try:
            raw_json = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise AiBackendProtocolError(f"Risposta non valida da LM Studio (JSON malformato): {e}") from e

        # Validazione Sezione B: top-level response deve essere dict
        if not isinstance(raw_json, dict):
            raise AiBackendProtocolError(
                f"Risposta top-level da LM Studio non è un dizionario (ricevuto {type(raw_json).__name__})."
            )

        # Validazione Sezione A: model ID deve venire RIGOROSAMENTE dalla risposta del backend
        if "model" not in raw_json:
            raise AiBackendProtocolError("Risposta da LM Studio priva del campo obbligatorio 'model'.")
        raw_model = raw_json["model"]
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise AiBackendProtocolError(
                f"Campo 'model' nella risposta da LM Studio non valido o vuoto (ricevuto {raw_model!r})."
            )
        response_model = raw_model

        # Validazione Sezione B: choices, message, content, finish_reason, usage
        choices = raw_json.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise AiBackendProtocolError("Campo 'choices' mancante, vuoto o non lista nella risposta di LM Studio.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise AiBackendProtocolError(
                f"L'elemento choices[0] deve essere un dizionario, ricevuto {type(first_choice).__name__}."
            )

        message_obj = first_choice.get("message")
        if not isinstance(message_obj, dict):
            raise AiBackendProtocolError(
                f"Campo choices[0]['message'] deve essere un dizionario, ricevuto {type(message_obj).__name__}."
            )

        content = message_obj.get("content")
        if not isinstance(content, str):
            raise AiBackendProtocolError(
                f"Campo choices[0]['message']['content'] deve essere una stringa, ricevuto {type(content).__name__}."
            )

        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise AiBackendProtocolError(
                f"Campo 'finish_reason' deve essere una stringa o None, ricevuto {type(finish_reason).__name__}."
            )

        usage = raw_json.get("usage")
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if usage is not None:
            if not isinstance(usage, dict):
                raise AiBackendProtocolError(
                    f"Campo 'usage' deve essere un dizionario, ricevuto {type(usage).__name__}."
                )
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = usage.get(k)
                if val is not None:
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                        raise AiBackendProtocolError(
                            f"Conteggio token '{k}' in 'usage' non numerico valido >= 0: {val!r}."
                        )
            prompt_tokens = int(usage["prompt_tokens"]) if usage.get("prompt_tokens") is not None else None
            completion_tokens = int(usage["completion_tokens"]) if usage.get("completion_tokens") is not None else None
            total_tokens = int(usage["total_tokens"]) if usage.get("total_tokens") is not None else None

        # Privacy raw_response: conserva solo metadati di protocollo se non in debug esplicito
        if self.debug_preserve_raw:
            clean_raw = raw_json
        else:
            clean_raw = {
                "model": response_model,
                "usage": usage if isinstance(usage, dict) else {},
                "finish_reason": finish_reason,
            }

        return LlmCompletionResponse(
            content=content,
            model=response_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_seconds=elapsed,
            raw_response=clean_raw,
            metadata={
                "backend": "lm_studio",
                "base_url": self.base_url,
                "target_model": target_model,
                "temperature": temperature,
                "seed": seed,
            },
        )

