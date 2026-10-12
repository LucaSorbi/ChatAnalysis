"""
tests/unit/test_lmstudio_model_loading.py
-----------------------------------------
Test esaustivi per il Model Loading Lifecycle e la validazione rigorosa dei contratti LM Studio:
- HTTP 200 con payload loaded valido -> ModelLoadResult con status LOADED.
- HTTP 200 con payload failed (es. "Not enough VRAM") -> solleva AiModelLoadError, mai LOADED.
- HTTP 200 con JSON malformato -> solleva AiBackendProtocolError.
- HTTP 200 con body vuoto -> solleva AiBackendProtocolError.
- HTTP 400 -> solleva AiModelLoadError con status_code=400.
- HTTP 404 / 405 -> restituisce status JIT_UNVERIFIED e NON memorizza _last_loaded_model.
- HTTP 500 -> solleva AiModelLoadError con status_code=500.
- Timeout -> solleva AiBackendTimeoutError.
- Server offline -> solleva LmStudioUnavailableError.
- Model Mismatch in modalità JIT -> solleva AiModelMismatchError se response.model non corrisponde.
- JIT verificato con completion conforme -> valida il binding.
- Rigorosa distinzione DeepSeek vs Qwen e Qwen2.5 vs Qwen3.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request
from typing import Any

import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelLoadError,
    AiModelMismatchError,
    AiModelNotInstalledError,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    LmStudioClient,
    ModelLifecycleStatus,
    ModelLoadResult,
    is_equivalent_operational_model,
)


class DummyHTTPResponse:
    def __init__(self, data: bytes, status: int = 200):
        self.data = data
        self.status = status

    def read(self) -> bytes:
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_load_model_http_200_success(monkeypatch):
    """Verifica caricamento riuscito con status loaded e instance_id valido."""
    def mock_urlopen(req, timeout):
        payload = {
            "status": "loaded",
            "instance_id": "qwen2.5-7b-instruct-inst-42",
            "model": "qwen2.5-7b-instruct",
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert isinstance(res, ModelLoadResult)
    assert res.status == ModelLifecycleStatus.LOADED
    assert res.instance_id == "qwen2.5-7b-instruct-inst-42"
    assert res.model_key == "qwen2.5-7b-instruct"
    assert client.is_model_loaded("qwen2.5-7b-instruct") is True


def test_load_model_http_200_failure_payload_raises_error(monkeypatch):
    """Verifica che HTTP 200 con status 'failed' o messaggio di errore sollevi AiModelLoadError e NON diventi loaded."""
    def mock_urlopen(req, timeout):
        payload = {
            "status": "failed",
            "error": "Not enough VRAM available to load model",
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelLoadError) as exc_info:
        client.load_model("qwen2.5-7b-instruct")

    assert "Not enough VRAM" in str(exc_info.value)
    assert exc_info.value.status_code == 200
    assert getattr(client, "_last_loaded_model", None) is None


def test_load_model_http_200_malformed_json_raises_protocol_error(monkeypatch):
    """Verifica che HTTP 200 con corpo non-JSON sollevi AiBackendProtocolError e NON finga loaded."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b"<html><body>502 Bad Gateway or invalid text</body></html>")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiBackendProtocolError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_load_model_http_200_empty_body_raises_protocol_error(monkeypatch):
    """Verifica che risposta vuota (0 byte) sollevi AiBackendProtocolError."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b"")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiBackendProtocolError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_load_model_http_400_raises_model_load_error(monkeypatch):
    """Verifica che HTTP 400 sollevi AiModelLoadError con dettagli tecnici."""
    def mock_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error": "Invalid params"}'))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelLoadError) as exc_info:
        client.load_model("qwen2.5-7b-instruct")

    assert exc_info.value.status_code == 400


def test_load_model_http_404_returns_jit_unverified_without_loaded_state(monkeypatch):
    """Verifica che HTTP 404 ritorni status JIT_UNVERIFIED senza aggiornare _last_loaded_model."""
    def mock_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'Not Found'))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert res.load_mode == "jit_unverified"
    assert getattr(client, "_last_loaded_model", None) is None


def test_load_model_http_405_returns_jit_unverified(monkeypatch):
    """Verifica che HTTP 405 (Method Not Allowed) ritorni status JIT_UNVERIFIED."""
    def mock_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 405, "Method Not Allowed", {}, io.BytesIO(b'Method Not Allowed'))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED


def test_load_model_http_500_raises_model_load_error(monkeypatch):
    """Verifica che HTTP 500 sollevi AiModelLoadError con status_code=500."""
    def mock_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b'Server crash'))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelLoadError) as exc_info:
        client.load_model("qwen2.5-7b-instruct")

    assert exc_info.value.status_code == 500


def test_load_model_timeout_raises_timeout_error(monkeypatch):
    """Verifica che timeout sollevi AiBackendTimeoutError."""
    def mock_urlopen(req, timeout):
        raise urllib.error.URLError(socket.timeout("Operation timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiBackendTimeoutError):
        client.load_model("qwen2.5-7b-instruct")


def test_load_model_connection_refused_raises_unavailable_error(monkeypatch):
    """Verifica che server down sollevi LmStudioUnavailableError."""
    def mock_urlopen(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(LmStudioUnavailableError):
        client.load_model("qwen2.5-7b-instruct")


def test_jit_completion_verifies_model_binding(monkeypatch):
    """Verifica che in modalità JIT_UNVERIFIED la prima completion verifichi il binding del modello."""
    client = LmStudioClient(model_id="qwen2.5-7b-instruct")
    client.load_mode = "jit_unverified"

    # Caso 1: response.model conforme
    def mock_chat_success(req, timeout):
        payload = {
            "model": "qwen2.5-7b-instruct",
            "choices": [{"message": {"content": "Risposta"}}],
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_chat_success)
    res = client.chat_completion([{"role": "user", "content": "test"}])
    assert res.content == "Risposta"
    assert client.load_mode == "jit_verified"
    assert client._last_loaded_model == "qwen2.5-7b-instruct"


def test_jit_completion_wrong_model_raises_model_mismatch(monkeypatch):
    """Verifica che se la completion restituisce un modello differente in JIT, viene sollevato AiModelMismatchError."""
    client = LmStudioClient(model_id="qwen2.5-7b-instruct")
    client.load_mode = "jit_unverified"

    def mock_chat_wrong(req, timeout):
        payload = {
            "model": "meta-llama-3.1-8b-instruct",
            "choices": [{"message": {"content": "Risposta"}}],
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_chat_wrong)
    with pytest.raises(AiModelMismatchError):
        client.chat_completion([{"role": "user", "content": "test"}])


def test_deepseek_and_qwen_strict_separation():
    """Verifica che DeepSeek-R1-Distill-Qwen-7B e Qwen2.5 non siano mai considerati equivalenti."""
    assert is_equivalent_operational_model("deepseek-r1-distill-qwen-7b", "qwen2.5-7b-instruct") is False
    assert is_equivalent_operational_model("qwen2.5-7b-instruct", "deepseek-r1-distill-qwen-7b") is False


def test_qwen3_and_qwen25_strict_separation():
    """Verifica che Qwen3 e Qwen2.5 non siano mai considerati equivalenti."""
    assert is_equivalent_operational_model("qwen3-7b-instruct", "qwen2.5-7b-instruct") is False
    assert is_equivalent_operational_model("qwen2.5-7b-instruct", "qwen3-7b-instruct") is False


def test_load_model_http_200_arbitrary_payload_raises_protocol_error(monkeypatch):
    """Verifica che un payload HTTP 200 arbitrario (es. {"arbitrary": true}) sollevi AiBackendProtocolError e NON diventi LOADED."""
    def mock_urlopen(req, timeout):
        payload = {"arbitrary": True}
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiBackendProtocolError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_load_model_http_200_still_loading_status_returns_loading_lifecycle(monkeypatch):
    """Verifica che status 'still_loading' o 'loading' ritorni ModelLifecycleStatus.LOADING e NON aggiorni _last_loaded_model."""
    def mock_urlopen(req, timeout):
        payload = {"status": "still_loading"}
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.LOADING
    assert res.load_mode == "loading"
    assert getattr(client, "_last_loaded_model", None) is None
    assert client.is_model_loaded("qwen2.5-7b-instruct") is False


def test_load_model_http_200_empty_dict_raises_protocol_error(monkeypatch):
    """Verifica che un dizionario vuoto sollevi AiBackendProtocolError e non confermi LOADED."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiBackendProtocolError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_ensure_model_loaded_still_loading_raises_error(monkeypatch):
    """Verifica che ensure_model_loaded sollevi AiModelLoadError se il modello è ancora in loading."""
    def mock_urlopen(req, timeout):
        payload = {"status": "loading"}
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    from ai.lmstudio import LocalModelMetadata
    client = LmStudioClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(
        client,
        "get_native_models",
        lambda: [LocalModelMetadata(key="qwen2.5-7b-instruct", display_name="Qwen2.5 7B Instruct", loaded_instances=[])],
    )
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    with pytest.raises(AiModelLoadError) as exc_info:
        client.ensure_model_loaded("qwen2.5-7b-instruct")

    assert "ancora in fase di caricamento" in str(exc_info.value)

