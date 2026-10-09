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
    AiModelLoadError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)

_ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionRefusedError, OSError)

SUPPORTED_OPERATIONAL_MODELS: tuple[str, ...] = (
    "qwen2.5-7b-instruct",
    "meta-llama-3.1-8b-instruct",
    "deepseek-r1-distill-qwen-7b",
)

DEFAULT_OPERATIONAL_MODEL: str = "qwen2.5-7b-instruct"
DEFAULT_OPERATIONAL_CONTEXT_LENGTH: int = 8192
DEFAULT_OPERATIONAL_GPU_OFFLOAD: str = "max"
DEFAULT_OPERATIONAL_TIMEOUT_SECONDS: float = 240.0
DEFAULT_OPERATIONAL_TEMPERATURE: float = 0.0
DEFAULT_OPERATIONAL_MAX_TOKENS: int = 512

MODEL_DISPLAY_NAMES: dict[str, str] = {
    "qwen2.5-7b-instruct": "Qwen2.5-7B-Instruct",
    "meta-llama-3.1-8b-instruct": "Llama 3.1-8B-Instruct",
    "deepseek-r1-distill-qwen-7b": "DeepSeek-R1-Distill-Qwen-7B",
}


def get_model_display_name(model_id: str) -> str:
    """Restituisce il nome formale del modello per messaggi UI e log."""
    m_clean = model_id.lower().replace("_", "-").replace(":", "-").replace("@", "-")
    for key, display in MODEL_DISPLAY_NAMES.items():
        key_clean = key.lower().replace("_", "-")
        if key_clean in m_clean or m_clean in key_clean:
            return display
    if "qwen" in m_clean and "7b" in m_clean:
        return "Qwen2.5-7B-Instruct"
    if "llama" in m_clean and "8b" in m_clean:
        return "Llama 3.1-8B-Instruct"
    if "deepseek" in m_clean:
        return "DeepSeek-R1-Distill-Qwen-7B"
    return model_id


def resolve_installed_model_id(
    requested_model: str,
    available_models: tuple[str, ...] | list[str],
) -> str | None:
    """
    Risolve un identificatore modello richiesto rispetto alla lista dei modelli
    effettivamente installati/disponibili in LM Studio.

    Regole:
    1. Match esatto (case-sensitive o case-insensitive).
    2. Match per prefisso o sottostringa canonica (es. 'qwen2.5-7b-instruct' corrisponde
       a 'qwen2.5-7b-instruct-gguf' o 'Qwen/Qwen2.5-7B-Instruct-GGUF').
    3. Non esegue MAI fallback arbitrario su available_models[0].
    4. Se nessun modello corrisponde, restituisce None.
    """
    if not available_models:
        return None

    req_raw = requested_model.strip()
    if not req_raw:
        return None

    # 1. Match esatto
    for m in available_models:
        if m == req_raw:
            return m

    # 2. Case-insensitive esatto
    req_lower = req_raw.lower()
    for m in available_models:
        if m.lower() == req_lower:
            return m

    # 3. Match canonico (normalizzando delimitatori _ e -)
    req_norm = req_lower.replace("_", "-").replace(".", "")
    for m in available_models:
        m_norm = m.lower().replace("_", "-").replace(".", "")
        if req_norm == m_norm:
            return m

    # 4. Sottostringa canonica (es. Qwen/Qwen2.5-7B-Instruct-GGUF o qwen2.5-7b-instruct@q4_k_m)
    for m in available_models:
        m_norm = m.lower().replace("_", "-")
        req_norm_dash = req_lower.replace("_", "-")
        if req_norm_dash in m_norm:
            return m

    return None


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
        self._last_loaded_model: str | None = None

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

    def is_model_loaded(self, model_id: str) -> bool:
        """
        Verifica se il modello specificato è attualmente caricato in memoria in LM Studio.
        Interroga get_models_detailed() e controlla gli indicatori di stato del runtime.
        """
        try:
            detailed = self.get_models_detailed()
        except Exception:
            detailed = []

        for item in detailed:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("name") or "")
            if not item_id:
                continue

            # Verifica corrispondenza ID
            is_match = (
                item_id == model_id
                or item_id.lower() == model_id.lower()
                or model_id.lower() in item_id.lower()
                or item_id.lower() in model_id.lower()
            )
            if is_match:
                state_val = str(item.get("state") or item.get("status") or "").lower()
                loaded_bool = item.get("loaded") or item.get("is_loaded")
                if state_val in ("loaded", "active", "ready") or loaded_bool is True:
                    return True
                if state_val in ("not_loaded", "unloaded", "inactive") or loaded_bool is False:
                    return False

        # Se la risposta da detailed non contiene campi di stato espliciti,
        # controlla se è stato caricato dal client durante la sessione
        if getattr(self, "_last_loaded_model", None) is not None:
            last = self._last_loaded_model.lower()
            if model_id.lower() in last or last in model_id.lower():
                return True

        return False

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> dict[str, Any]:
        """
        Carica un modello in memoria tramite l'API REST nativa di LM Studio (POST /api/v1/models/load).
        Se l'endpoint non è esposto (404/405/501), valida la presenza del modello e attiva il supporto JIT controllato.
        """
        _validate_loopback_url(self.base_url)
        url = f"{self.base_url}/api/v1/models/load"
        payload = {
            "model": model_id,
            "identifier": model_id,
            "context_length": context_length,
            "contextLength": context_length,
            "gpu_offload": gpu_offload,
            "gpuOffload": gpu_offload,
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=self._get_headers(), method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                raw_bytes = resp.read()
                self._last_loaded_model = model_id
                try:
                    return json.loads(raw_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    return {"model": model_id, "status": "loaded"}
        except urllib.error.HTTPError as e:
            if e.code in (404, 405, 501):
                # Fallback controllato su JIT di LM Studio:
                # Verifica che il modello sia effettivamente tra quelli disponibili localmente
                available = self.list_models()
                resolved = resolve_installed_model_id(model_id, available)
                if not resolved:
                    display = get_model_display_name(model_id)
                    raise AiModelNotInstalledError(
                        f"Il modello {display} non è installato in LM Studio."
                    ) from e
                self._last_loaded_model = resolved
                return {"model": resolved, "status": "jit_ready", "endpoint": "jit"}

            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise AiModelLoadError(
                f"Errore HTTP {e.code} da LM Studio durante il caricamento di '{model_id}': {e.reason}. {err_body}".strip()
            ) from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(
                    f"Timeout ({timeout_seconds}s) durante il caricamento del modello '{model_id}' in LM Studio."
                ) from e
            raise LmStudioUnavailableError(
                f"Impossibile contattare LM Studio su {self.base_url} per il caricamento del modello: {e}"
            ) from e

    def unload_model(self, model_id: str | None = None, timeout_seconds: float = 30.0) -> bool:
        """
        Scarica un modello dalla memoria tramite POST /api/v1/models/unload se supportato.
        """
        _validate_loopback_url(self.base_url)
        url = f"{self.base_url}/api/v1/models/unload"
        payload: dict[str, Any] = {}
        if model_id:
            payload["model"] = model_id
            payload["identifier"] = model_id
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=self._get_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                self._last_loaded_model = None
                return resp.status in (200, 204)
        except (urllib.error.HTTPError, _NETWORK_ERRORS):
            self._last_loaded_model = None
            return False

    def ensure_model_loaded(
        self,
        model_id: str = "qwen2.5-7b-instruct",
        context_length: int = 8192,
        gpu_offload: str = "max",
        timeout_seconds: float = 240.0,
    ) -> str:
        """
        Esegue il pre-flight completo e garantisce la disponibilità operativa del modello locale.
        1. Verifica che LM Studio sia attivo su loopback.
        2. Verifica che il modello richiesto sia installato localmente (nessun download da Internet).
        3. Se il modello è già caricato, lo riutilizza immediatamente.
        4. Se il modello non è caricato, lo carica tramite API REST nativa.
        Restituisce il model_id risolto per l'inferenza.
        """
        if not self.is_available():
            raise LmStudioUnavailableError(
                f"LM Studio non raggiungibile su {self.base_url}. "
                "Verificare che il server locale sia attivo."
            )

        available = self.list_models()
        resolved_id = resolve_installed_model_id(model_id, available)
        if not resolved_id:
            display_name = get_model_display_name(model_id)
            raise AiModelNotInstalledError(
                f"Il modello {display_name} non è installato in LM Studio."
            )

        if self.is_model_loaded(resolved_id):
            self.model_id = resolved_id
            return resolved_id

        self.load_model(
            model_id=resolved_id,
            context_length=context_length,
            gpu_offload=gpu_offload,
            timeout_seconds=timeout_seconds,
        )
        self.model_id = resolved_id
        return resolved_id

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

