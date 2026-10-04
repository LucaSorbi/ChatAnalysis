"""
tests/unit/test_ai_cloud_test.py
--------------------------------
Test offline e completamente mockati per il client cloud sperimentale
(RemoteOpenAICompatibleTestClient).

Copertura vincolante:
1. model_id obbligatorio (AiModelNotSpecifiedError).
2. API key letta esclusivamente da ambiente.
3. Segreto (API key) mai esposto in eccezioni, log o metadati.
4. Gestione timeout (AiBackendTimeoutError).
5. Gestione errori HTTP (AiBackendRequestError).
6. Risposta JSON malformata (AiBackendProtocolError).
7. Structured output (json_schema e json_object).
8. Controllo model mismatch (AiModelMismatchError).
9. Estrazione token usage (prompt, completion, total).
10. Nessun retry automatico (chiamata singola rigorosa).
11. Zero connessioni di rete reali (completamente offline).
12. Integrazione con TopicDetectionAnalyzer.
"""
from __future__ import annotations

import io
import json
import os
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiBackendUnavailableError,
    AiModelMismatchError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    BaseLocalLlmClient,
)
from ai.cloud_test import RemoteOpenAICompatibleTestClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    TopicDecision,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection


def _make_mock_response(body_dict: dict, status: int = 200) -> MagicMock:
    """Crea una risposta HTTP mockata con bytes JSON."""
    raw_bytes = json.dumps(body_dict).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = raw_bytes
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    return mock_resp


@pytest.fixture(autouse=True)
def block_external_sockets(monkeypatch):
    """Garantisce che nessun test esegua connessioni di rete reali."""
    def _fail_connect(*args, **kwargs):
        raise RuntimeError("Tentativo di connessione di rete reale non consentito nel test offline!")

    monkeypatch.setattr(socket, "create_connection", _fail_connect)


class TestRemoteOpenAICompatibleTestClient:
    """Test suite per RemoteOpenAICompatibleTestClient."""

    def test_class_hierarchy(self):
        """Il client deve implementare BaseLlmClient ma NON BaseLocalLlmClient."""
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )
        assert isinstance(client, BaseLlmClient)
        assert not isinstance(client, BaseLocalLlmClient)

    def test_url_scheme_validation(self):
        """Verifica che sia accettato SOLO https, rifiutando http, ftp, hostname mancante."""
        # HTTPS accettato
        client = RemoteOpenAICompatibleTestClient(endpoint_url="https://api.openai.com/v1")
        assert client.endpoint_url == "https://api.openai.com/v1"

        # HTTP rifiutato con ValueError chiaro
        with pytest.raises(ValueError, match="richiede obbligatoriamente 'https'"):
            RemoteOpenAICompatibleTestClient(endpoint_url="http://api.openai.com/v1")

        # FTP rifiutato
        with pytest.raises(ValueError, match="richiede obbligatoriamente 'https'"):
            RemoteOpenAICompatibleTestClient(endpoint_url="ftp://api.example.com/v1")

        # Hostname mancante rifiutato
        with pytest.raises(ValueError, match="Hostname mancante"):
            RemoteOpenAICompatibleTestClient(endpoint_url="https://")

        # Query string rifiutata senza echo dei parametri
        super_secret_query = "SUPER_SECRET_QUERY_VALUE"
        with pytest.raises(ValueError) as exc_q:
            RemoteOpenAICompatibleTestClient(endpoint_url=f"https://api.example.com/v1?api_key={super_secret_query}")
        assert "Parametri di query" in str(exc_q.value)
        assert super_secret_query not in str(exc_q.value)

        # Fragment rifiutato senza echo del fragment
        super_secret_fragment = "SUPER_SECRET_FRAGMENT_VALUE"
        with pytest.raises(ValueError) as exc_f:
            RemoteOpenAICompatibleTestClient(endpoint_url=f"https://api.example.com/v1#{super_secret_fragment}")
        assert "Fragment" in str(exc_f.value)
        assert super_secret_fragment not in str(exc_f.value)

    def test_embedded_credentials_in_url_rejected(self):
        """Rifiuta URL contenenti credenziali embedded (username@ o username:password@)."""
        # username:password@
        with pytest.raises(ValueError, match="Credenziali o userinfo embedded"):
            RemoteOpenAICompatibleTestClient(endpoint_url="https://user:secret123@api.openai.com/v1")

        # username@
        with pytest.raises(ValueError, match="Credenziali o userinfo embedded"):
            RemoteOpenAICompatibleTestClient(endpoint_url="https://admin@api.openai.com/v1")

    @pytest.mark.parametrize("header_name", [
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "Proxy-Authorization",
        "proxy-authorization",
        "X-API-Key",
        "x-api-key",
        "X-Api-Key",
        "Api-Key",
        "api-key",
        "API-KEY",
    ])
    def test_custom_headers_security_rejects_credential_headers(self, header_name):
        """I custom_headers non devono consentire l'override di header di autenticazione sensibili."""
        secret_value = "super-secret-token-never-expose"
        with pytest.raises(ValueError) as exc_info:
            RemoteOpenAICompatibleTestClient(
                endpoint_url="https://api.openai.com/v1",
                custom_headers={header_name: secret_value},
            )
        err_msg = str(exc_info.value)
        assert "Header non consentito in custom_headers" in err_msg
        # Il valore segreto NON deve apparire nel messaggio di errore
        assert secret_value not in err_msg

    def test_custom_headers_allowed_non_sensitive(self, monkeypatch):
        """Header non sensibili personalizzati devono essere consentiti e inclusi nella richiesta."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "env-key-999")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            custom_headers={"X-Organization-Id": "org-123"},
        )
        captured_headers = {}

        def _mock_urlopen(req, timeout=None):
            nonlocal captured_headers
            captured_headers = dict(req.headers)
            return _make_mock_response({
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
            })

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            client.chat_completion(messages=[{"role": "user", "content": "test"}])

        assert captured_headers.get("X-organization-id") == "org-123" or captured_headers.get("X-Organization-Id") == "org-123"
        assert captured_headers.get("Authorization") == "Bearer env-key-999"

    def test_model_id_mandatory(self, monkeypatch):
        """La chiamata deve fallire con AiModelNotSpecifiedError se model_id è assente."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-secret-key-12345")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id=None,
        )
        with pytest.raises(AiModelNotSpecifiedError, match="Nessun model_id specificato"):
            client.chat_completion(messages=[{"role": "user", "content": "ciao"}])

    def test_api_key_from_environment(self, monkeypatch):
        """L'API key deve essere letta esclusivamente dalla variabile d'ambiente."""
        monkeypatch.delenv("CLOUD_LLM_API_KEY", raising=False)
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )
        # Se la variabile d'ambiente è assente, solleva AiBackendUnavailableError
        with pytest.raises(AiBackendUnavailableError, match="API key cloud non configurata"):
            client.chat_completion(messages=[{"role": "user", "content": "ciao"}])

        # Se la variabile d'ambiente è presente, la richiesta include l'header Authorization corretto
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-real-test-secret-999")
        captured_headers = {}

        def _mock_urlopen(req, timeout=None):
            nonlocal captured_headers
            captured_headers = dict(req.headers)
            return _make_mock_response({
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
            })

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            resp = client.chat_completion(messages=[{"role": "user", "content": "ciao"}])
            assert resp.content == "ok"
            assert captured_headers.get("Authorization") == "Bearer sk-real-test-secret-999"

    def test_secret_not_exposed_in_exceptions_or_metadata(self, monkeypatch):
        """L'API key non deve mai apparire nei messaggi di errore o nei metadati."""
        secret_key = "sk-super-secret-key-to-redact-777"
        monkeypatch.setenv("CLOUD_LLM_API_KEY", secret_key)

        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        # 1. Simula errore HTTP che accidentalmente include il segreto nel corpo
        mock_http_err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401,
            msg=f"Unauthorized with key {secret_key}",
            hdrs={},
            fp=io.BytesIO(f"Invalid credentials for key {secret_key}".encode("utf-8")),
        )

        with patch("urllib.request.urlopen", side_effect=mock_http_err):
            with pytest.raises(AiBackendRequestError) as exc_info:
                client.chat_completion(messages=[{"role": "user", "content": "test"}])
            error_text = str(exc_info.value)
            assert secret_key not in error_text
            assert "[REDACTED_API_KEY]" in error_text

        # 2. Verifica che i metadati della risposta non contengano la chiave
        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "success"}}],
        })):
            resp = client.chat_completion(messages=[{"role": "user", "content": "test"}])
            meta_str = str(resp.metadata)
            assert secret_key not in meta_str
            assert resp.metadata["backend"] == "remote_openai_compatible_test"
            assert resp.metadata["synthetic_data_only"] is True

    def test_timeout_handling(self, monkeypatch):
        """Un timeout di rete deve essere convertito in AiBackendTimeoutError."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            default_timeout_seconds=5.0,
        )

        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
            with pytest.raises(AiBackendTimeoutError, match="Timeout"):
                client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_http_error_handling(self, monkeypatch):
        """Errori HTTP 4xx/5xx devono sollevare AiBackendRequestError."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        mock_err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error": "rate_limit_exceeded"}'),
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            with pytest.raises(AiBackendRequestError, match="HTTP 429"):
                client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_malformed_json_response(self, monkeypatch):
        """Risposte non JSON o malformate devono sollevare AiBackendProtocolError."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>Gateway Timeout 504</html>"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(AiBackendProtocolError, match="JSON malformato"):
                client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_structured_output_payload(self, monkeypatch):
        """Il payload deve formattare correttamente response_format per JSON Schema."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            require_json_schema=True,
        )

        captured_payload = None

        def _mock_urlopen(req, timeout=None):
            nonlocal captured_payload
            captured_payload = json.loads(req.data.decode("utf-8"))
            return _make_mock_response({
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": '{"decision": "PRESENT"}'}}],
            })

        test_schema = {
            "type": "object",
            "properties": {"decision": {"type": "string"}},
            "required": ["decision"],
        }

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            client.chat_completion(
                messages=[{"role": "user", "content": "test"}],
                schema=test_schema,
            )

        assert captured_payload is not None
        assert "response_format" in captured_payload
        rf = captured_payload["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"] == test_schema

    def test_structured_output_json_object_fallback(self, monkeypatch):
        """
        Con require_json_schema=False:
        - response_format == {"type": "json_object"}
        - nel payload inviato è presente un'istruzione di sistema esplicita con JSON e schema
        - la lista messages originale passata dal chiamante NON viene modificata
        - l'output continua a passare dai validator del layer di analisi
        """
        from ai.structured import validate_topic_detection_payload

        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            require_json_schema=False,
        )

        captured_payload = None

        def _mock_urlopen(req, timeout=None):
            nonlocal captured_payload
            captured_payload = json.loads(req.data.decode("utf-8"))
            return _make_mock_response({
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": json.dumps({
                    "decision": "PRESENT",
                    "evidence_ids": ["ev_100"],
                    "rationale": "Evidenza chiara trovata nel testo",
                })}}],
            })

        original_messages = [
            {"role": "system", "content": "Base system directive."},
            {"role": "user", "content": "Forensic evidence text."},
        ]
        original_messages_snapshot = [dict(m) for m in original_messages]
        target_schema = {
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
            },
            "required": ["decision", "evidence_ids", "rationale"],
        }

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            resp = client.chat_completion(
                messages=original_messages,
                schema=target_schema,
            )

        # 1. response_format == {"type": "json_object"}
        assert captured_payload is not None
        assert captured_payload["response_format"] == {"type": "json_object"}

        # 2. Istruzione esplicita integrata nel system message prima del primo messaggio user
        sent_messages = captured_payload["messages"]
        first_user_idx = next(i for i, m in enumerate(sent_messages) if m["role"] == "user")
        assert first_user_idx > 0, "La direttiva JSON deve trovarsi PRIMA del primo messaggio role=user"

        system_msg = sent_messages[0]
        assert system_msg["role"] == "system"
        assert "Base system directive." in system_msg["content"]
        assert "Return ONLY a valid JSON object" in system_msg["content"]
        assert "Do not return Markdown, code fences or explanatory prose" in system_msg["content"]
        assert "The JSON must satisfy the requested structured-output contract" in system_msg["content"]
        assert "Target JSON Schema" in system_msg["content"]

        # 3. La lista messages originale non viene modificata
        assert original_messages == original_messages_snapshot
        assert len(original_messages) == 2

        # 4. L'output continua a passare dai validator del layer di analisi
        decision, evidence_ids, rationale = validate_topic_detection_payload(
            raw_text=resp.content,
            valid_evidence_ids={"ev_100"},
        )
        assert decision == TopicDecision.PRESENT
        assert evidence_ids == ("ev_100",)
        assert rationale == "Evidenza chiara trovata nel testo"

    def test_structured_output_json_object_fallback_inserts_system_before_user_when_no_prior_system(self, monkeypatch):
        """Se non esiste alcun messaggio system pregresso, la direttiva viene inserita prima del primo user message."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            require_json_schema=False,
        )

        captured_payload = None

        def _mock_urlopen(req, timeout=None):
            nonlocal captured_payload
            captured_payload = json.loads(req.data.decode("utf-8"))
            return _make_mock_response({
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": '{"result": "ok"}'}}],
            })

        user_only_messages = [
            {"role": "user", "content": "Evidence text only without initial system."},
        ]
        user_only_snapshot = [dict(m) for m in user_only_messages]

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            client.chat_completion(
                messages=user_only_messages,
                schema={"type": "object"},
            )

        sent_messages = captured_payload["messages"]
        assert len(sent_messages) == 2
        assert sent_messages[0]["role"] == "system"
        assert "Return ONLY a valid JSON object" in sent_messages[0]["content"]
        assert sent_messages[1]["role"] == "user"
        # Originale non mutato
        assert user_only_messages == user_only_snapshot

    def test_model_id_exact_match_success(self, monkeypatch):
        """Model ID identico deve avere esito positivo."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            check_model_mismatch=True,
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o",
            "choices": [{"message": {"content": "ok"}}],
        })):
            resp = client.chat_completion(messages=[{"role": "user", "content": "test"}])
            assert resp.model == "gpt-4o"

    def test_model_id_dated_version_rejected_as_mismatch(self, monkeypatch):
        """Una versione datata (es. gpt-4o-2024-08-06 per gpt-4o) deve essere rifiutata con AiModelMismatchError."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            check_model_mismatch=True,
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-2024-08-06",
            "choices": [{"message": {"content": "ok"}}],
        })):
            with pytest.raises(AiModelMismatchError, match="Model mismatch nel backend cloud") as exc_info:
                client.chat_completion(messages=[{"role": "user", "content": "test"}])
            assert exc_info.value.requested_model == "gpt-4o"
            assert exc_info.value.returned_model == "gpt-4o-2024-08-06"

    def test_model_id_related_family_rejected_as_mismatch(self, monkeypatch):
        """Un modello della stessa famiglia ma con ID diverso (es. gpt-4o-mini per gpt-4o) deve essere rifiutato."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            check_model_mismatch=True,
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "ok"}}],
        })):
            with pytest.raises(AiModelMismatchError, match="Model mismatch nel backend cloud") as exc_info:
                client.chat_completion(messages=[{"role": "user", "content": "test"}])
            assert exc_info.value.requested_model == "gpt-4o"
            assert exc_info.value.returned_model == "gpt-4o-mini"

    def test_model_mismatch_unrelated_model(self, monkeypatch):
        """Se il provider restituisce un modello completamente estraneo, deve sollevare AiModelMismatchError."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o",
            check_model_mismatch=True,
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "claude-3-5-sonnet",
            "choices": [{"message": {"content": "hello"}}],
        })):
            with pytest.raises(AiModelMismatchError, match="Model mismatch nel backend cloud"):
                client.chat_completion(messages=[{"role": "user", "content": "test"}])

    def test_token_usage_extraction(self, monkeypatch):
        """I conteggi token (prompt, completion, total) devono essere estratti correttamente."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "test content"}}],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 17,
                "total_tokens": 59,
            },
        })):
            resp = client.chat_completion(messages=[{"role": "user", "content": "test"}])
            assert resp.prompt_tokens == 42
            assert resp.completion_tokens == 17
            assert resp.total_tokens == 59
            assert resp.latency_seconds >= 0.0

    def test_no_automatic_retry(self, monkeypatch):
        """In caso di errore non deve esserci alcun retry automatico (esattamente 1 tentativo)."""
        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        call_count = 0

        def _failing_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("Connection refused")

        with patch("urllib.request.urlopen", side_effect=_failing_urlopen):
            with pytest.raises(AiBackendUnavailableError):
                client.chat_completion(messages=[{"role": "user", "content": "test"}])

        assert call_count == 1, f"Attesa esattamente 1 chiamata senza retry, eseguite {call_count} chiamate"

    def test_integration_with_topic_detection_analyzer(self, monkeypatch):
        """TopicDetectionAnalyzer deve funzionare perfettamente con RemoteOpenAICompatibleTestClient."""
        from ai.benchmark import create_synthetic_benchmark_documents

        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
        )

        analyzer = TopicDetectionAnalyzer(client=client)

        docs = create_synthetic_benchmark_documents()
        doc = docs[0]["document"]
        valid_eid = doc.all_evidence_sections[0].evidence_id

        topic = TopicQuery(
            topic_id="top_cycling",
            label="Ciclismo",
            description="Attività e percorsi in bicicletta",
        )

        valid_llm_json = {
            "decision": "PRESENT",
            "evidence_ids": [valid_eid],
            "rationale": "La conversazione menziona esplicitamente il percorso ciclabile.",
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": json.dumps(valid_llm_json)}}],
        })):
            result = analyzer.detect_topic(
                document=doc,
                topic=topic,
                strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            )

            assert result.decision == TopicDecision.PRESENT
            assert result.evidence_ids == (valid_eid,)
            assert "ciclabile" in result.rationale
            assert result.provenance_document_id == doc.document_id

    def test_integration_with_topic_detection_analyzer_json_fallback(self, monkeypatch):
        """TopicDetectionAnalyzer deve funzionare perfettamente anche con il fallback json_object."""
        from ai.benchmark import create_synthetic_benchmark_documents

        monkeypatch.setenv("CLOUD_LLM_API_KEY", "dummy-key")
        client = RemoteOpenAICompatibleTestClient(
            endpoint_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            require_json_schema=False,
        )

        analyzer = TopicDetectionAnalyzer(client=client)

        docs = create_synthetic_benchmark_documents()
        doc = docs[0]["document"]
        valid_eid = doc.all_evidence_sections[0].evidence_id

        topic = TopicQuery(
            topic_id="top_cycling",
            label="Ciclismo",
            description="Attività e percorsi in bicicletta",
        )

        valid_llm_json = {
            "decision": "PRESENT",
            "evidence_ids": [valid_eid],
            "rationale": "La conversazione menziona la pista ciclabile con fallback json.",
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response({
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": json.dumps(valid_llm_json)}}],
        })):
            result = analyzer.detect_topic(
                document=doc,
                topic=topic,
                strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            )

            assert result.decision == TopicDecision.PRESENT
            assert result.evidence_ids == (valid_eid,)
            assert "ciclabile" in result.rationale
            assert result.provenance_document_id == doc.document_id
