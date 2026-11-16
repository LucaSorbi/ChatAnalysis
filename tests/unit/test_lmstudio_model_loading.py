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
    clear_model_bindings,
    get_model_binding,
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


@pytest.fixture(autouse=True)
def clean_bindings():
    clear_model_bindings()
    yield
    clear_model_bindings()


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


def test_loaded_with_generic_status_no_identity_returns_jit_unverified_and_no_last_loaded_model(monkeypatch):
    """Verifica che {"status": "loaded"} senza identità ritorni JIT_UNVERIFIED e non imposti _last_loaded_model."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b'{"status": "loaded"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert getattr(client, "_last_loaded_model", None) is None


def test_loaded_with_wrong_model_key_raises_model_mismatch(monkeypatch):
    """Verifica che {"status": "loaded", "model": "WRONG-MODEL"} sollevi AiModelMismatchError."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b'{"status": "loaded", "model": "WRONG-MODEL"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_loaded_with_ambiguous_conflicting_instance_id_raises_model_mismatch(monkeypatch):
    """Verifica che un instance_id con famiglia conflittuale sollevi AiModelMismatchError."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b'{"status": "loaded", "instance_id": "deepseek-instance-42"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_loaded_with_qwen3_identity_when_qwen25_requested_raises_mismatch(monkeypatch):
    """Verifica che risposta Qwen3 quando richiesto Qwen2.5 sollevi AiModelMismatchError."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b'{"status": "loaded", "model": "qwen3-7b-instruct"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_loaded_with_deepseek_identity_when_qwen25_requested_raises_mismatch(monkeypatch):
    """Verifica che risposta DeepSeek quando richiesto Qwen2.5 sollevi AiModelMismatchError."""
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(b'{"status": "loaded", "model": "deepseek-r1-distill-qwen-7b"}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_chat_completion_response_model_mismatch_raises_error(monkeypatch):
    """Verifica che una chat_completion con response.model incoerente sollevi AiModelMismatchError."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        payload = {
            "model": "meta-llama-3.1-8b-instruct",
            "choices": [{"message": {"content": "ok"}}],
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient(model_id="qwen2.5-7b-instruct")

    with pytest.raises(AiModelMismatchError):
        client.chat_completion(messages=[{"role": "user", "content": "test"}])


# ==============================================================================
# BLOCKER 1 — 10 TEST OBBLIGATORI AUDIT (LM Studio Identity Binding & Opaque ID)
# ==============================================================================

def test_audit_1_http_200_opaque_instance_id_only_is_jit_unverified(monkeypatch):
    """1. Risposta HTTP 200 contenente soltanto un instance ID opaco -> JIT_UNVERIFIED, _last_loaded_model NON aggiornato."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps({"id": "opaque-instance-001"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert res.load_mode == "jit_unverified"
    assert res.instance_id == "opaque-instance-001"
    assert getattr(client, "_last_loaded_model", None) is None
    # Binding provvisorio registrato ma non ancora verificato
    binding = client.get_binding("opaque-instance-001")
    assert binding is not None
    assert binding.is_verified is False
    assert binding.resolved_model_key == "qwen2.5-7b-instruct"


def test_audit_2_http_200_status_loaded_and_opaque_instance_id_is_jit_unverified(monkeypatch):
    """2. Risposta contenente status loaded e instance ID opaco senza model/model_key -> JIT_UNVERIFIED, _last_loaded_model NON aggiornato."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        payload = {"status": "loaded", "id": "opaque-instance-002"}
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert getattr(client, "_last_loaded_model", None) is None
    binding = client.get_binding("opaque-instance-002")
    assert binding is not None
    assert binding.is_verified is False


def test_audit_3_http_200_valid_model_key_and_opaque_instance_id_is_loaded(monkeypatch):
    """3. Risposta contenente model key valido e instance ID opaco -> ModelLifecycleStatus.LOADED con binding verificato."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        payload = {
            "status": "loaded",
            "model": "qwen2.5-7b-instruct",
            "id": "opaque-instance-003",
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.LOADED
    assert res.load_mode == "native_loaded"
    assert client._last_loaded_model == "opaque-instance-003"
    assert client.is_model_loaded("qwen2.5-7b-instruct") is True
    binding = client.get_binding("opaque-instance-003")
    assert binding is not None
    assert binding.is_verified is True


def test_audit_4_http_200_different_model_raises_mismatch_error(monkeypatch):
    """4. Risposta con modello diverso -> solleva categoricamente AiModelMismatchError."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        payload = {
            "status": "loaded",
            "model": "meta-llama-3.1-8b-instruct",
            "id": "opaque-instance-004",
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")

    assert getattr(client, "_last_loaded_model", None) is None


def test_audit_5_completion_with_opaque_target_and_bound_canonical_response_succeeds(monkeypatch):
    """5. Completion con target API identifier opaco e response.model canonico correttamente associato -> accettata e binding verificato."""
    clear_model_bindings()
    # Step 1: load_model con risposta opaca (JIT_UNVERIFIED)
    def mock_load(req, timeout):
        return DummyHTTPResponse(json.dumps({"id": "opaque-instance-005"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_load)
    client = LmStudioClient()
    load_res = client.load_model("qwen2.5-7b-instruct")
    assert load_res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert getattr(client, "_last_loaded_model", None) is None

    # Step 2: completion con target_model="opaque-instance-005" e response.model="qwen2.5-7b-instruct"
    def mock_chat(req, timeout):
        payload = {
            "model": "qwen2.5-7b-instruct",
            "choices": [{"message": {"content": "Risposta inferenza valida"}}],
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_chat)
    chat_res = client.chat_completion(
        messages=[{"role": "user", "content": "ping"}],
        model_id="opaque-instance-005",
    )

    assert chat_res.content == "Risposta inferenza valida"
    assert chat_res.model == "qwen2.5-7b-instruct"
    # Ora il binding è stato verificato dalla prima completion conforme
    binding = client.get_binding("opaque-instance-005")
    assert binding is not None
    assert binding.is_verified is True
    assert client._last_loaded_model == "qwen2.5-7b-instruct"


def test_audit_6_completion_with_opaque_target_and_foreign_response_model_raises_mismatch(monkeypatch):
    """6. Completion con target opaco ma response.model appartenente ad altro modello -> solleva AiModelMismatchError."""
    clear_model_bindings()
    # Step 1: load_model per Qwen con risposta opaca
    def mock_load(req, timeout):
        return DummyHTTPResponse(json.dumps({"id": "opaque-instance-006"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_load)
    client = LmStudioClient()
    client.load_model("qwen2.5-7b-instruct")

    # Step 2: completion con response.model da famiglia aliena (llama)
    def mock_chat(req, timeout):
        payload = {
            "model": "meta-llama-3.1-8b-instruct",
            "choices": [{"message": {"content": "Risposta aliena"}}],
        }
        return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_chat)
    with pytest.raises(AiModelMismatchError):
        client.chat_completion(
            messages=[{"role": "user", "content": "ping"}],
            model_id="opaque-instance-006",
        )


def test_audit_7_qwen25_strictly_distinct_from_qwen3():
    """7. Qwen2.5 distinto da Qwen3 sia a livello di equivalenza sia di conflitto."""
    clear_model_bindings()
    assert is_equivalent_operational_model("qwen2.5-7b-instruct", "qwen3-7b-instruct") is False
    assert is_equivalent_operational_model("qwen3-7b-instruct", "qwen2.5-7b-instruct") is False


def test_audit_8_deepseek_r1_distill_strictly_distinct_from_qwen25():
    """8. DeepSeek-R1-Distill-Qwen distinto da Qwen2.5 sia a livello di equivalenza sia di conflitto."""
    clear_model_bindings()
    assert is_equivalent_operational_model("deepseek-r1-distill-qwen-7b", "qwen2.5-7b-instruct") is False
    assert is_equivalent_operational_model("qwen2.5-7b-instruct", "deepseek-r1-distill-qwen-7b") is False


def test_audit_9_jit_unverified_never_falsely_confirms_loaded(monkeypatch):
    """9. JIT_UNVERIFIED senza falsa conferma di caricamento."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps({"id": "opaque-jit-test"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("meta-llama-3.1-8b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    assert client.is_model_loaded("meta-llama-3.1-8b-instruct") is False
    assert getattr(client, "_last_loaded_model", None) is None


def test_audit_10_no_premature_update_of_last_loaded_model(monkeypatch):
    """10. Nessun aggiornamento prematuro di _last_loaded_model prima della verifica documentata o completion."""
    clear_model_bindings()
    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps({"id": "opaque-premature-check"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    client.load_model("qwen2.5-7b-instruct")

    assert client._last_loaded_model is None
    assert getattr(client, "_last_loaded_model_key", None) is None


# ==============================================================================
# TEST UFFICIALI LM STUDIO REST API (POST /api/v1/models/load) - BLOCKER 2
# ==============================================================================

def test_official_load_response_valid_schema_verified(monkeypatch):
    """Blocker 2.1, 2.2: Payload ufficiale POST /api/v1/models/load con load_config.context_length=8192."""
    clear_model_bindings()
    # Schema esatto dalla documentazione LM Studio: https://lmstudio.ai/docs/developer/rest/load
    official_payload = {
        "type": "llm",
        "instance_id": "qwen2.5-7b-instruct",
        "status": "loaded",
        "load_config": {
            "context_length": 8192,
        },
    }

    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps(official_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct", context_length=8192)

    assert res.status == ModelLifecycleStatus.LOADED
    assert res.instance_id == "qwen2.5-7b-instruct"
    assert res.context_length == 8192
    assert res.loaded_instance_context_length == 8192
    assert res.context_evidence_source == "LOADER_API_RESPONSE"
    assert client.is_model_loaded("qwen2.5-7b-instruct") is True
    binding = client.get_active_binding()
    assert binding is not None
    assert binding.is_verified is True
    assert binding.loaded_instance_context_length == 8192
    assert binding.context_evidence_source == "LOADER_API_RESPONSE"


def test_official_load_response_context_4096(monkeypatch):
    """Blocker 2.3: Payload ufficiale POST /api/v1/models/load con load_config.context_length=4096."""
    clear_model_bindings()
    official_payload = {
        "type": "llm",
        "instance_id": "qwen2.5-7b-instruct",
        "status": "loaded",
        "load_config": {
            "context_length": 4096,
        },
    }

    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps(official_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct", context_length=8192)

    assert res.status == ModelLifecycleStatus.LOADED
    assert res.loaded_instance_context_length == 4096
    assert res.context_length == 4096
    binding = client.get_active_binding()
    assert binding is not None
    assert binding.is_verified is True
    assert binding.loaded_instance_context_length == 4096


def test_official_load_response_opaque_instance_verified_via_get(monkeypatch):
    """Blocker 2.4: Istanza opaca senza model_key nel load, verificata tramite query GET dei modelli."""
    clear_model_bindings()
    load_payload = {
        "type": "llm",
        "instance_id": "instance-qwen-001",
        "status": "loaded",
        "load_config": {
            "context_length": 8192,
        },
    }
    get_payload = {
        "models": [
            {
                "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
                "display_name": "Qwen2.5-7B-Instruct",
                "loaded_instances": [
                    {
                        "id": "instance-qwen-001",
                        "config": {"context_length": 8192},
                    }
                ],
            }
        ]
    }

    def mock_urlopen(req, timeout):
        url = req.full_url
        if "/api/v1/models/load" in url:
            return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))
        elif "/api/v1/models" in url:
            return DummyHTTPResponse(json.dumps(get_payload).encode("utf-8"))
        return DummyHTTPResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct", context_length=8192)

    assert res.status == ModelLifecycleStatus.LOADED
    assert res.instance_id == "instance-qwen-001"
    assert res.model_key == "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"
    binding = client.get_active_binding()
    assert binding is not None
    assert binding.is_verified is True
    assert binding.loaded_instance_id == "instance-qwen-001"
    assert binding.resolved_model_key == "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"


def test_official_load_response_opaque_instance_unverifiable_becomes_jit_unverified(monkeypatch):
    """Blocker 2.5: Istanza opaca non verificabile tramite GET diventa JIT_UNVERIFIED senza fingere successo."""
    clear_model_bindings()
    load_payload = {
        "type": "llm",
        "instance_id": "unknown-opaque-instance-999",
        "status": "loaded",
        "load_config": {
            "context_length": 8192,
        },
    }

    def mock_urlopen(req, timeout):
        url = req.full_url
        if "/api/v1/models/load" in url:
            return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))
        elif "/api/v1/models" in url:
            # GET non elenca questa istanza
            return DummyHTTPResponse(json.dumps({"models": []}).encode("utf-8"))
        return DummyHTTPResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
    binding = client.get_active_binding()
    assert binding is not None
    assert binding.is_verified is False
    assert res.context_evidence_source == "UNVERIFIED"


def test_official_load_response_opaque_instance_belongs_to_alien_model_raises_mismatch(monkeypatch):
    """Blocker 2.6: Instance ID appartenente a un modello alieno verificato via GET solleva AiModelMismatchError."""
    clear_model_bindings()
    load_payload = {
        "type": "llm",
        "instance_id": "instance-alien-42",
        "status": "loaded",
    }
    get_payload = {
        "models": [
            {
                "key": "meta-llama/Meta-Llama-3.1-8B-Instruct-GGUF",
                "display_name": "Llama-3.1-8B-Instruct",
                "loaded_instances": [{"id": "instance-alien-42"}],
            }
        ]
    }

    def mock_urlopen(req, timeout):
        url = req.full_url
        if "/api/v1/models/load" in url:
            return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))
        elif "/api/v1/models" in url:
            return DummyHTTPResponse(json.dumps(get_payload).encode("utf-8"))
        return DummyHTTPResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError) as exc_info:
        client.load_model("qwen2.5-7b-instruct")

    assert "mismatch" in str(exc_info.value).lower()


def test_load_response_requested_qwen25_but_instance_qwen3_raises_mismatch(monkeypatch):
    """Blocker 2.7: Modello richiesto Qwen2.5 ma instance_id con token Qwen3 solleva AiModelMismatchError."""
    clear_model_bindings()
    load_payload = {
        "status": "loaded",
        "instance_id": "qwen3-7b-instruct-instance-1",
    }

    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")


def test_load_response_requested_qwen25_but_instance_deepseek_raises_mismatch(monkeypatch):
    """Blocker 2.8: Modello richiesto Qwen2.5 ma instance_id con token DeepSeek solleva AiModelMismatchError."""
    clear_model_bindings()
    load_payload = {
        "status": "loaded",
        "instance_id": "deepseek-r1-distill-qwen-7b-inst",
    }

    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()

    with pytest.raises(AiModelMismatchError):
        client.load_model("qwen2.5-7b-instruct")


def test_official_load_response_status_loading(monkeypatch):
    """Blocker 2.10: Status loading restituito dal loader non certifica il binding."""
    clear_model_bindings()
    load_payload = {
        "status": "loading",
        "instance_id": "qwen2.5-7b-instruct",
    }

    def mock_urlopen(req, timeout):
        return DummyHTTPResponse(json.dumps(load_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    res = client.load_model("qwen2.5-7b-instruct")

    assert res.status == ModelLifecycleStatus.LOADING
    assert client.get_active_binding() is None




