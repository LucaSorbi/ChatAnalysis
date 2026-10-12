"""
tests/unit/test_ai_lmstudio.py
------------------------------
Test unitari per l'adapter LmStudioClient:
- Politica di sicurezza loopback-only (rifiuto host remoti o non locali)
- Obbligatorietà del model_id esplicito (nessuna auto-selezione)
- Tassonomia delle eccezioni (Unavailable, Protocol, Request)
- Gestione server non disponibile ed esclusioni da is_available
- Parsing della risposta chat_completion, metadati e privacy raw_response
"""
import io
import json
import unittest.mock as mock
import urllib.error
import urllib.request
import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    LmStudioUnavailableError,
)
from ai.lmstudio import LmStudioClient, _validate_loopback_url


@pytest.mark.unit
class TestLmStudioClient:

    def test_loopback_security_accepts_valid_hosts(self):
        valid_urls = [
            "http://127.0.0.1:1234",
            "http://localhost:1234",
            "http://localhost:8080",
            "http://[::1]:1234",
        ]
        for url in valid_urls:
            client = LmStudioClient(base_url=url)
            assert client.base_url == url

    def test_loopback_security_rejects_remote_hosts(self):
        invalid_urls = [
            "http://192.168.1.100:1234",
            "http://10.0.0.1:1234",
            "http://api.openai.com",
            "http://lmstudio.remote.lan:1234",
            "https://evil.server.com",
        ]
        for url in invalid_urls:
            with pytest.raises(ValueError, match="esclusivamente host loopback"):
                LmStudioClient(base_url=url)

    def test_loopback_security_rejects_invalid_scheme(self):
        with pytest.raises(ValueError, match="Schema URL non valido"):
            LmStudioClient(base_url="ftp://127.0.0.1:1234")

    def test_model_id_mandatory_no_auto_selection(self):
        client = LmStudioClient()
        with pytest.raises(AiModelNotSpecifiedError, match="Nessun model_id specificato"):
            client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_server_unavailable_graceful_handling(self):
        client = LmStudioClient(base_url="http://127.0.0.1:59999", model_id="some-model")
        assert client.is_available() is False

        with pytest.raises(LmStudioUnavailableError):
            client.list_models()

        with pytest.raises(LmStudioUnavailableError):
            client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_is_available_propagates_programming_error(self, monkeypatch):
        def buggy_urlopen(*args, **kwargs):
            raise TypeError("Errore programmatico interno")

        monkeypatch.setattr(urllib.request, "urlopen", buggy_urlopen)
        client = LmStudioClient()
        with pytest.raises(TypeError, match="Errore programmatico interno"):
            client.is_available()

    def test_list_models_protocol_error_on_invalid_json(self, monkeypatch):
        class BadJsonResponse:
            def __init__(self):
                self.status = 200

            def read(self):
                return b"Non e un JSON"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: BadJsonResponse())
        client = LmStudioClient()
        with pytest.raises(AiBackendProtocolError, match="JSON malformato in list_models"):
            client.list_models()

    def test_chat_completion_http_error_mapped_to_request_error(self, monkeypatch):
        def http_err(*args, **kwargs):
            fp = io.BytesIO(b"Bad Request")
            raise urllib.error.HTTPError("http://127.0.0.1:1234/v1/chat/completions", 400, "Bad Request", {}, fp)

        monkeypatch.setattr(urllib.request, "urlopen", http_err)
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendRequestError, match="Errore HTTP 400"):
            client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_chat_completion_protocol_error_on_empty_choices(self, monkeypatch):
        class EmptyChoicesResponse:
            def __init__(self):
                self.status = 200

            def read(self):
                return json.dumps({"model": "qwen-2.5-1.5b", "choices": []}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: EmptyChoicesResponse())
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="Campo 'choices' mancante, vuoto o non lista"):
            client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_auth_headers_with_and_without_token(self, monkeypatch):
        monkeypatch.delenv("LM_STUDIO_API_TOKEN", raising=False)

        # Senza token
        client_no_auth = LmStudioClient()
        headers = client_no_auth._get_headers()
        assert "Authorization" not in headers

        # Con token esplicito
        client_with_auth = LmStudioClient(api_token="secret_token_123")
        headers = client_with_auth._get_headers()
        assert headers.get("Authorization") == "Bearer secret_token_123"

        # Con token da variabile d'ambiente
        monkeypatch.setenv("LM_STUDIO_API_TOKEN", "env_secret_456")
        client_env = LmStudioClient()
        headers = client_env._get_headers()
        assert headers.get("Authorization") == "Bearer env_secret_456"

    def test_chat_completion_mocked_success_and_raw_privacy(self, monkeypatch):
        fake_response = {
            "id": "chatcmpl-123",
            "model": "qwen-2.5-1.5b",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"decision": "PRESENT", "evidence_ids": ["e1"], "rationale": "Test"}',
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 40,
                "completion_tokens": 15,
                "total_tokens": 55,
            },
        }

        class MockResponse:
            def __init__(self):
                self.status = 200

            def read(self):
                return json.dumps(fake_response).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: MockResponse())

        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        res = client.chat_completion(messages=[{"role": "user", "content": "Analizza"}])

        assert res.model == "qwen-2.5-1.5b"
        assert res.content == fake_response["choices"][0]["message"]["content"]
        assert res.total_tokens == 55
        assert res.metadata["backend"] == "lm_studio"

        # Privacy raw_response: non conserva tutto il body ma solo metadati di protocollo
        assert "choices" not in res.raw_response
        assert res.raw_response["finish_reason"] == "stop"
        assert res.raw_response["model"] == "qwen-2.5-1.5b"

    # =========================================================================
    # Test SEZIONE A: MODEL ID MUST COME FROM THE BACKEND RESPONSE
    # =========================================================================
    def _mock_chat_response(self, monkeypatch, payload_dict):
        class MockResp:
            def __init__(self):
                self.status = 200
            def read(self):
                return json.dumps(payload_dict).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: MockResp())

    def test_chat_completion_protocol_error_on_missing_model(self, monkeypatch):
        bad_resp = {
            "choices": [{"message": {"content": "ok"}}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="priva del campo obbligatorio 'model'"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_null_model(self, monkeypatch):
        bad_resp = {
            "model": None,
            "choices": [{"message": {"content": "ok"}}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="non valido o vuoto"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_non_string_model(self, monkeypatch):
        bad_resp = {
            "model": 12345,
            "choices": [{"message": {"content": "ok"}}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="non valido o vuoto"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_empty_model(self, monkeypatch):
        bad_resp = {
            "model": "   ",
            "choices": [{"message": {"content": "ok"}}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="non valido o vuoto"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_model_strictly_from_backend(self, monkeypatch):
        resp = {
            "model": "qwen2.5-1.5b-instruct@q4_k_m",
            "choices": [{"message": {"content": "ok"}}],
        }
        self._mock_chat_response(monkeypatch, resp)
        client = LmStudioClient(model_id="requested-model-id")
        res = client.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert res.model == "qwen2.5-1.5b-instruct@q4_k_m"

    # =========================================================================
    # Test SEZIONE B: STRICT CHAT COMPLETION RESPONSE SHAPE
    # =========================================================================
    def test_chat_completion_protocol_error_on_non_dict_top_level(self, monkeypatch):
        class NonDictResp:
            def __init__(self):
                self.status = 200
            def read(self):
                return b'["not", "a", "dict"]'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: NonDictResp())
        client = LmStudioClient(model_id="qwen-2.5-1.5b")
        with pytest.raises(AiBackendProtocolError, match="non è un dizionario"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_non_dict_choice(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": ["not_a_dict"],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="choices\\[0\\] deve essere un dizionario"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_non_dict_message(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": [{"message": "just_a_string"}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="message'\\] deve essere un dizionario"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_non_string_content(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": [{"message": {"content": 123}}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="content'\\] deve essere una stringa"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_invalid_finish_reason(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": [{"message": {"content": "ok"}, "finish_reason": 42}],
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="finish_reason"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_invalid_usage_type(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": [{"message": {"content": "ok"}}],
            "usage": "not_a_dict",
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="usage' deve essere un dizionario"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_protocol_error_on_negative_or_boolean_tokens(self, monkeypatch):
        bad_resp = {
            "model": "m1",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": True},
        }
        self._mock_chat_response(monkeypatch, bad_resp)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendProtocolError, match="Conteggio token 'prompt_tokens' in 'usage' non numerico"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    # =========================================================================
    # Test SEZIONE C: DETAILED MODELS PROTOCOL ERROR
    # =========================================================================
    def test_get_models_detailed_malformed_json_raises_protocol_error_no_fallback(self, monkeypatch):
        class MalformedApiJsonResponse:
            def __init__(self):
                self.status = 200
            def read(self):
                return b"<html><body>Not a JSON response</body></html>"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: MalformedApiJsonResponse())
        client = LmStudioClient()
        with pytest.raises(AiBackendProtocolError, match="JSON malformato ricevuto da"):
            client.get_models_detailed()

    def test_get_models_detailed_fallback_on_404_only(self, monkeypatch):
        calls = []

        def mock_urlopen(req, timeout):
            url = req.full_url
            calls.append(url)
            if "/api/v1/models" in url:
                fp = io.BytesIO(b"Not Found")
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, fp)
            if "/v1/models" in url:
                class V1Resp:
                    def __init__(self):
                        self.status = 200
                    def read(self):
                        return json.dumps({"data": [{"id": "v1-model"}]}).encode("utf-8")
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return V1Resp()
            raise ValueError(f"URL imprevisto: {url}")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = LmStudioClient()
        res = client.get_models_detailed()
        assert len(res) == 1
        assert res[0]["id"] == "v1-model"
        assert any("/api/v1/models" in c for c in calls)
        assert any("/v1/models" in c for c in calls)

    # =========================================================================
    # Test SEZIONE D: TIMEOUT TAXONOMY REALE
    # =========================================================================
    def test_chat_completion_timeout_raises_ai_backend_timeout_error(self, monkeypatch):
        from ai.backend import AiBackendTimeoutError
        import socket

        def timeout_urlopen(*args, **kwargs):
            raise urllib.error.URLError(socket.timeout("The read operation timed out"))

        monkeypatch.setattr(urllib.request, "urlopen", timeout_urlopen)
        client = LmStudioClient(model_id="m1")
        with pytest.raises(AiBackendTimeoutError, match="Timeout"):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    # =========================================================================
    # Test SEZIONE E: MODEL MANAGEMENT, AUTO-LOAD E JIT
    # =========================================================================
    def test_load_model_post_endpoint_success(self, monkeypatch):
        calls = []

        class LoadResp:
            def __init__(self):
                self.status = 200
            def read(self):
                return json.dumps({"model": "qwen2.5-7b-instruct", "status": "loaded"}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def mock_urlopen(req, timeout):
            calls.append(req.full_url)
            assert "/api/v1/models/load" in req.full_url
            assert req.get_method() == "POST"
            body = json.loads(req.data.decode("utf-8"))
            assert body["model"] == "qwen2.5-7b-instruct"
            assert body["context_length"] == 8192
            assert body["echo_load_config"] is True
            assert "identifier" not in body
            assert "contextLength" not in body
            assert "gpu_offload" not in body
            assert "gpuOffload" not in body
            return LoadResp()

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = LmStudioClient()
        res = client.load_model("qwen2.5-7b-instruct", context_length=8192)
        assert res["status"] == "loaded"
        assert client.is_model_loaded("qwen2.5-7b-instruct") is True

    def test_load_model_fallback_on_404_jit(self, monkeypatch):
        def mock_urlopen(req, timeout):
            url = req.full_url
            if "/api/v1/models/load" in url:
                fp = io.BytesIO(b"Not Found")
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, fp)
            if "/v1/models" in url:
                class ModelsResp:
                    def __init__(self):
                        self.status = 200
                    def read(self):
                        return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return ModelsResp()
            raise ValueError(f"URL imprevisto: {url}")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = LmStudioClient()
        res = client.load_model("qwen2.5-7b-instruct")
        assert res["status"] == "jit_ready"

    def test_load_model_timeout_mapped_to_ai_backend_timeout_error(self, monkeypatch):
        import socket
        def timeout_urlopen(*args, **kwargs):
            raise urllib.error.URLError(socket.timeout("The read operation timed out"))

        monkeypatch.setattr(urllib.request, "urlopen", timeout_urlopen)
        client = LmStudioClient()
        with pytest.raises(AiBackendTimeoutError, match="Timeout"):
            client.load_model("qwen2.5-7b-instruct")

    def test_ensure_model_loaded_skips_when_already_loaded(self, monkeypatch):
        calls = []

        def mock_urlopen(req, timeout):
            calls.append(req.full_url)
            if "/api/v1/models" in req.full_url:
                class DetailedResp:
                    def __init__(self):
                        self.status = 200
                    def read(self):
                        return json.dumps({
                            "models": [{"id": "qwen2.5-7b-instruct", "state": "loaded"}]
                        }).encode("utf-8")
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return DetailedResp()
            if "/v1/models" in req.full_url:
                class V1Resp:
                    def __init__(self):
                        self.status = 200
                    def read(self):
                        return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return V1Resp()
            raise ValueError(f"URL non consentito: {req.full_url}")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = LmStudioClient()
        resolved = client.ensure_model_loaded("qwen2.5-7b-instruct")
        assert resolved == "qwen2.5-7b-instruct"
        # Non deve invocare /api/v1/models/load
        assert not any("/load" in c for c in calls)

    def test_ensure_model_loaded_raises_when_not_installed(self, monkeypatch):
        def mock_urlopen(req, timeout):
            class V1Resp:
                def __init__(self):
                    self.status = 200
                def read(self):
                    return json.dumps({"data": [{"id": "other-model"}]}).encode("utf-8")
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return V1Resp()

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = LmStudioClient()
        with pytest.raises(AiModelNotInstalledError, match="Qwen2.5-7B-Instruct non è installato"):
            client.ensure_model_loaded("qwen2.5-7b-instruct")

