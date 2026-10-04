"""
ai/cloud_test.py
----------------
Adapter client remoto sperimentale per API REST compatibili con OpenAI (es. OpenAI, OpenRouter).

POLITICA DI UTILIZZO E SICUREZZA:
1. SPERIMENTALE — ESCLUSIVAMENTE PER BENCHMARK SU DATASET SINTETICO:
   Questo client è progettato unicamente per consentire test di validazione E2E preliminari
   su dataset sintetici dedicati. NON è concepito né autorizzato per l'impiego operativo.
2. DIVIETO ASSOLUTO SU DATI FORENSI REALI:
   I dati forensi reali non devono MAI essere inviati a endpoint remoti cloud.
   Il percorso forense definitivo rimane rigorosamente locale (LM Studio su loopback).
3. ISOLAMENTO TOTALE DALLA UI (STREAMLIT):
   Questo modulo NON è esposto nella UI di Streamlit (nessun pulsante, nessun selettore,
   nessun campo API key).
4. GESTIONE RIGOROSA DEI SEGRETI:
   L'API key è letta ESCLUSIVAMENTE da variabili d'ambiente (default: CLOUD_LLM_API_KEY).
   Nessun token viene salvato nel repository, loggato o esposto nelle eccezioni.
5. NESSUN RETRY AUTOMATICO E NESSUNA AUTO-SELEZIONE:
   Nessun retry silenzioso. model_id deve essere esplicito per prevenire ambiguità scientifica.
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
    AiBackendError,
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiBackendUnavailableError,
    AiModelMismatchError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    LlmCompletionResponse,
)

_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionRefusedError, OSError)

DISALLOWED_CUSTOM_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
})


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


def _validate_remote_url(endpoint_url: str) -> urllib.parse.ParseResult:
    """
    Valida la correttezza formale e la sicurezza dell'URL remoto dell'endpoint.
    
    Regole vincolanti:
    - Solo schema 'https' consentito (nessun 'http' né altri schemi non cifrati).
    - Hostname obbligatorio.
    - Nessuna credenziale o userinfo embedded (es. user:pass@ o user@).
    - Nessun parametro di query ('?...') né fragment ('#...').
    """
    parsed = urllib.parse.urlparse(endpoint_url)
    if parsed.scheme != "https":
        raise ValueError(
            f"Schema URL non consentito: '{parsed.scheme}'. "
            "Il client cloud remoto richiede obbligatoriamente 'https' (richieste http non cifrate non sono consentite)."
        )
    if not parsed.hostname:
        raise ValueError("Hostname mancante nell'URL endpoint.")
    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        raise ValueError(
            "Credenziali o userinfo embedded nell'URL non consentite. "
            "L'autenticazione deve avvenire esclusivamente tramite variabile d'ambiente."
        )
    if parsed.query:
        raise ValueError(
            "Parametri di query non consentiti nell'URL endpoint. "
            "Nessuna configurazione o credenziale deve essere passata nell'URL."
        )
    if parsed.fragment:
        raise ValueError(
            "Fragment non consentito nell'URL endpoint."
        )
    return parsed


class RemoteOpenAICompatibleTestClient(BaseLlmClient):
    """
    Client adapter sperimentale per endpoint remoti compatibili con l'API OpenAI Chat Completions.
    
    ATTENZIONE: Modulo marcato come EXPERIMENTAL / SYNTHETIC DATA ONLY.
    Implementa BaseLlmClient, NON BaseLocalLlmClient.
    """

    def __init__(
        self,
        endpoint_url: str,
        model_id: str | None = None,
        api_key_env_var: str = "CLOUD_LLM_API_KEY",
        default_timeout_seconds: float = 30.0,
        require_json_schema: bool = True,
        check_model_mismatch: bool = True,
        custom_headers: Mapping[str, str] | None = None,
        debug_preserve_raw: bool = False,
    ) -> None:
        _validate_remote_url(endpoint_url)
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model_id = model_id.strip() if model_id else None
        self.api_key_env_var = api_key_env_var
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.require_json_schema = require_json_schema
        self.check_model_mismatch = check_model_mismatch
        if custom_headers:
            for h_name in custom_headers:
                if h_name.strip().lower() in DISALLOWED_CUSTOM_HEADERS:
                    raise ValueError(
                        f"Header non consentito in custom_headers: '{h_name.strip()}'. "
                        "I parametri o header di autenticazione/credenziali non possono essere specificati via custom_headers."
                    )
            self.custom_headers: dict[str, str] | None = dict(custom_headers)
        else:
            self.custom_headers = None
        self.debug_preserve_raw = debug_preserve_raw

    @property
    def safe_endpoint(self) -> str:
        """Restituisce l'endpoint privo di credenziali o percorsi sensibili per log/errori."""
        parsed = urllib.parse.urlparse(self.endpoint_url)
        port_part = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port_part}{parsed.path}"

    def _get_api_key(self) -> str:
        """
        Recupera l'API key esclusivamente dalla variabile d'ambiente configurata.
        Solleva AiBackendUnavailableError se la chiave non è impostata o è vuota.
        """
        key = os.environ.get(self.api_key_env_var)
        if not key or not key.strip():
            raise AiBackendUnavailableError(
                f"API key cloud non configurata: variabile d'ambiente '{self.api_key_env_var}' assente o vuota. "
                "Per eseguire test sul backend cloud sperimentale è necessario impostare la variabile d'ambiente."
            )
        return key.strip()

    def _sanitize_error(self, message: str) -> str:
        """Rimuove qualsiasi potenziale occorrenza dell'API key dai messaggi di errore."""
        key = os.environ.get(self.api_key_env_var)
        if key and len(key) >= 4 and key in message:
            message = message.replace(key, "[REDACTED_API_KEY]")
        return message

    def _get_headers(self, api_key: str) -> dict[str, str]:
        """Costruisce gli header HTTP minimi senza loggare né esporre il token."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "ChatAnalysis-CloudTestClient/1.0 (Experimental-SyntheticOnly)",
        }
        if self.custom_headers:
            for k, v in self.custom_headers.items():
                if k.strip().lower() not in DISALLOWED_CUSTOM_HEADERS:
                    headers[k] = v
        return headers

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> LlmCompletionResponse:
        """
        Invia una richiesta di chat completion all'endpoint remoto compatibile OpenAI.
        
        Vincoli:
        - model_id esplicito obbligatorio (nessuna auto-selezione).
        - API key recuperata da variabile d'ambiente al momento della chiamata.
        - Nessun retry automatico in caso di errore.
        - Timeout esplicito.
        """
        target_model = model_id or self.model_id
        if not target_model or not target_model.strip():
            raise AiModelNotSpecifiedError(
                "Nessun model_id specificato per la chat completion cloud. "
                "Per preservare l'integrità scientifica dell'esperimento, l'identificativo modello deve essere esplicito."
            )

        api_key = self._get_api_key()
        effective_timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds

        # 1. Non mutare la lista messages originale del chiamante
        # 2. Crea una copia dei messaggi destinati alla richiesta
        request_messages: list[dict[str, str]] = [dict(m) for m in messages]

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": request_messages,
            "temperature": temperature,
        }
        if seed is not None:
            payload["seed"] = seed
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Supporto per Structured Output (JSON Schema nativo o fallback json_object controllato)
        if schema is not None:
            if self.require_json_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_output",
                        "strict": True,
                        "schema": schema,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}
                schema_repr = json.dumps(schema, indent=2, ensure_ascii=False)
                fallback_directive = (
                    "Return ONLY a valid JSON object. "
                    "Do not return Markdown, code fences or explanatory prose. "
                    "The JSON must satisfy the requested structured-output contract.\n\n"
                    f"Target JSON Schema:\n{schema_repr}"
                )
                system_idx = next(
                    (i for i, m in enumerate(request_messages) if m.get("role") == "system"),
                    None,
                )
                if system_idx is not None:
                    existing_content = request_messages[system_idx].get("content", "")
                    request_messages[system_idx] = {
                        "role": "system",
                        "content": f"{existing_content}\n\n{fallback_directive}",
                    }
                else:
                    first_user_idx = next(
                        (i for i, m in enumerate(request_messages) if m.get("role") == "user"),
                        len(request_messages),
                    )
                    request_messages.insert(
                        first_user_idx,
                        {"role": "system", "content": fallback_directive},
                    )

        # Risoluzione dell'URL di chat completion
        if self.endpoint_url.endswith("/chat/completions"):
            url = self.endpoint_url
        else:
            url = f"{self.endpoint_url}/chat/completions"

        req_data = json.dumps(payload).encode("utf-8")
        headers = self._get_headers(api_key)
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        start_t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                elapsed = round(time.perf_counter() - start_t, 3)
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:250]
            except Exception:
                pass
            msg = self._sanitize_error(
                f"Errore HTTP {e.code} dall'endpoint cloud ({self.safe_endpoint}): {e.reason}. Risposta: {error_body}"
            )
            raise AiBackendRequestError(msg) from None
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                msg = self._sanitize_error(
                    f"Timeout ({effective_timeout}s) durante la chiamata cloud verso {self.safe_endpoint}: {e}"
                )
                raise AiBackendTimeoutError(msg) from None
            msg = self._sanitize_error(
                f"Errore di connessione verso endpoint cloud {self.safe_endpoint}: {e}"
            )
            raise AiBackendUnavailableError(msg) from None

        try:
            raw_json = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise AiBackendProtocolError(
                f"Risposta non valida dall'endpoint cloud (JSON malformato): {e}"
            ) from e

        if not isinstance(raw_json, dict):
            raise AiBackendProtocolError(
                f"Risposta top-level dal provider non è un dizionario (ricevuto {type(raw_json).__name__})."
            )

        # Validazione e controllo Model ID (Exact String Match)
        raw_model = raw_json.get("model")
        if isinstance(raw_model, str) and raw_model.strip():
            response_model = raw_model.strip()
        else:
            response_model = target_model

        if self.check_model_mismatch and isinstance(raw_model, str) and raw_model.strip():
            if target_model.strip() != response_model:
                raise AiModelMismatchError(
                    f"Model mismatch nel backend cloud: richiesto '{target_model}', restituito '{response_model}'.",
                    requested_model=target_model,
                    returned_model=response_model,
                )

        # Validazione choices e content
        choices = raw_json.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise AiBackendProtocolError("Campo 'choices' mancante o vuoto nella risposta del provider cloud.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise AiBackendProtocolError("L'elemento choices[0] deve essere un dizionario.")

        message_obj = first_choice.get("message")
        if not isinstance(message_obj, dict):
            raise AiBackendProtocolError("Campo choices[0]['message'] deve essere un dizionario.")

        content = message_obj.get("content")
        if not isinstance(content, str):
            raise AiBackendProtocolError("Campo choices[0]['message']['content'] deve essere una stringa.")

        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

        # Estrazione usage se fornita dal provider
        usage = raw_json.get("usage")
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            tt = usage.get("total_tokens")
            if isinstance(pt, (int, float)) and not isinstance(pt, bool) and pt >= 0:
                prompt_tokens = int(pt)
            if isinstance(ct, (int, float)) and not isinstance(ct, bool) and ct >= 0:
                completion_tokens = int(ct)
            if isinstance(tt, (int, float)) and not isinstance(tt, bool) and tt >= 0:
                total_tokens = int(tt)

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
                "backend": "remote_openai_compatible_test",
                "experimental": True,
                "synthetic_data_only": True,
                "endpoint_url": self.safe_endpoint,
                "target_model": target_model,
                "temperature": temperature,
                "seed": seed,
            },
        )
