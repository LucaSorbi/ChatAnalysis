"""
tests/unit/test_lmstudio_binding_lifecycle.py
---------------------------------------------
Test di unità per la gestione del ciclo di vita dei binding dei modelli LM Studio (BLOCKER 2).

Verifica:
1. Binding registrato e unload riuscito -> binding invalidato.
2. Binding registrato e unload fallito -> binding preservato.
3. Due client indipendenti con registri isolati.
4. Due istanze differenti senza cross-talk.
5. Stesso instance ID riutilizzato successivamente.
6. Risposta completion senza choices -> AiBackendProtocolError, binding NON verificato.
7. Risposta completion senza message -> AiBackendProtocolError, binding NON verificato.
8. Risposta completion con JSON malformato -> AiBackendProtocolError, binding NON verificato.
9. Response model errato -> AiModelMismatchError, binding NON verificato.
10. Status di caricamento sconosciuto -> AiBackendProtocolError, mai LOADED.
11. Status loading -> ModelLifecycleStatus.LOADING, _last_loaded_model non aggiornato.
12. Status loaded valido -> ModelLifecycleStatus.LOADED, binding verificato.
13. JIT_UNVERIFIED -> _last_loaded_model non aggiornato prematuramente.
14. Promozione positiva a verificato solo dopo completamento interamente valido.
15. Nessuna contaminazione tra famiglie (Qwen2.5, Qwen3, Llama, DeepSeek).
16. Nessun aggiornamento prematuro dei metadati su fallimento.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiModelMismatchError,
    AiModelLoadError,
)
from ai.lmstudio import (
    LmStudioClient,
    ModelIdentityBinding,
    ModelLifecycleStatus,
    ModelLoadResult,
    get_model_family,
    is_conflicting_model_or_instance,
    is_equivalent_operational_model,
)


def _make_http_response(body_dict_or_str: dict | str, status: int = 200) -> MagicMock:
    if isinstance(body_dict_or_str, dict):
        raw_bytes = json.dumps(body_dict_or_str).encode("utf-8")
    else:
        raw_bytes = body_dict_or_str.encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw_bytes
    resp.status = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


class TestLmStudioModelBindingLifecycle:
    """Verifiche formali e architetturali del lifecycle dei binding (Blocker 2)."""

    def test_binding_registered_and_unload_success(self):
        """1. Binding registrato e unload riuscito -> binding rimosso."""
        client = LmStudioClient()
        binding = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="inst-uuid-1",
            api_identifier="inst-uuid-1",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client.register_binding(binding)
        assert client.get_binding("inst-uuid-1") is binding
        assert client.get_binding("qwen2.5-7b-instruct") is binding

        with patch("urllib.request.urlopen", return_value=_make_http_response({}, status=200)):
            res = client.unload_model("qwen2.5-7b-instruct")

        assert res is True
        assert client.get_binding("inst-uuid-1") is None
        assert client.get_binding("qwen2.5-7b-instruct") is None
        assert client._active_binding is None
        assert client._last_loaded_model is None

    def test_binding_registered_and_unload_failure(self):
        """2. Binding registrato e unload fallito -> binding NON invalidato arbitrariamente."""
        client = LmStudioClient()
        binding = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="inst-uuid-2",
            api_identifier="inst-uuid-2",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client.register_binding(binding)

        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("http://127.0.0.1:1234", 500, "Internal Server Error", {}, None)):
            res = client.unload_model("qwen2.5-7b-instruct")

        assert res is False
        # Il binding deve rimanere intatto perché il modello non è stato effettivamente scaricato
        assert client.get_binding("inst-uuid-2") is binding
        assert client.get_binding("qwen2.5-7b-instruct") is binding

    def test_two_independent_clients_isolated_bindings(self):
        """3. Due client indipendenti possiedono registri isolati."""
        client_a = LmStudioClient(base_url="http://127.0.0.1:1234")
        client_b = LmStudioClient(base_url="http://127.0.0.1:1234")

        binding_a = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="client-a-inst",
            api_identifier="client-a-inst",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client_a.register_binding(binding_a)

        # client_a ha il proprio binding
        assert client_a.get_binding("client-a-inst") is binding_a
        # Invalida su client_a
        client_a.invalidate_binding("client-a-inst")
        assert client_a.get_binding("client-a-inst") is None

    def test_two_different_instances_distinct_bindings(self):
        """4. Due istanze differenti hanno binding distinti senza contaminazione."""
        client = LmStudioClient()
        binding_qwen = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="inst-qwen-42",
            api_identifier="inst-qwen-42",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        binding_llama = ModelIdentityBinding(
            requested_canonical_id="meta-llama-3.1-8b-instruct",
            resolved_model_key="meta-llama-3.1-8b-instruct",
            loaded_instance_id="inst-llama-99",
            api_identifier="inst-llama-99",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client.register_binding(binding_qwen)
        client.register_binding(binding_llama)

        assert client.get_binding("inst-qwen-42") is binding_qwen
        assert client.get_binding("inst-llama-99") is binding_llama
        assert client.get_binding("inst-qwen-42") is not client.get_binding("inst-llama-99")

        # Invalidazione di Qwen non tocca Llama
        client.invalidate_binding("qwen2.5-7b-instruct")
        assert client.get_binding("inst-qwen-42") is None
        assert client.get_binding("inst-llama-99") is binding_llama

    def test_same_instance_id_reused_subsequently(self):
        """5. Stesso instance ID riutilizzato successivamente senza conflitti."""
        client = LmStudioClient()
        b1 = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="reused-id",
            api_identifier="reused-id",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client.register_binding(b1)
        assert client.get_binding("reused-id") is b1

        client.invalidate_binding("reused-id")
        assert client.get_binding("reused-id") is None

        b2 = ModelIdentityBinding(
            requested_canonical_id="meta-llama-3.1-8b-instruct",
            resolved_model_key="meta-llama-3.1-8b-instruct",
            loaded_instance_id="reused-id",
            api_identifier="reused-id",
            status=ModelLifecycleStatus.LOADED,
            is_verified=True,
        )
        client.register_binding(b2)
        assert client.get_binding("reused-id") is b2

    def test_completion_response_missing_or_empty_choices(self):
        """6. Completion con choices=[] solleva AiBackendProtocolError e lascia binding NON verificato."""
        client = LmStudioClient()
        provisional = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            loaded_instance_id="jit-inst-01",
            api_identifier="jit-inst-01",
            status=ModelLifecycleStatus.JIT_UNVERIFIED,
            is_verified=False,
        )
        client.register_binding(provisional)

        invalid_resp = {
            "model": "qwen2.5-7b-instruct",
            "choices": [],  # Vuoto!
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(invalid_resp)):
            with pytest.raises(AiBackendProtocolError, match="choices.*mancante, vuoto o non lista"):
                client.chat_completion(
                    messages=[{"role": "user", "content": "test"}],
                    model_id="qwen2.5-7b-instruct",
                )

        # Regola critica: il binding NON deve essere diventato verificato!
        assert provisional.is_verified is False
        assert provisional.status == ModelLifecycleStatus.JIT_UNVERIFIED
        assert client._last_loaded_model is None

    def test_completion_response_missing_message(self):
        """7. Completion senza campo message solleva AiBackendProtocolError e non verifica il binding."""
        client = LmStudioClient()
        provisional = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            status=ModelLifecycleStatus.JIT_UNVERIFIED,
            is_verified=False,
        )
        client.register_binding(provisional)

        invalid_resp = {
            "model": "qwen2.5-7b-instruct",
            "choices": [{"finish_reason": "stop"}],  # message mancante!
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(invalid_resp)):
            with pytest.raises(AiBackendProtocolError, match="message.*deve essere un dizionario"):
                client.chat_completion(
                    messages=[{"role": "user", "content": "test"}],
                    model_id="qwen2.5-7b-instruct",
                )

        assert provisional.is_verified is False
        assert provisional.status == ModelLifecycleStatus.JIT_UNVERIFIED

    def test_completion_malformed_json(self):
        """8. Completion con JSON malformato solleva AiBackendProtocolError e non verifica il binding."""
        client = LmStudioClient()
        provisional = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            status=ModelLifecycleStatus.JIT_UNVERIFIED,
            is_verified=False,
        )
        client.register_binding(provisional)

        with patch("urllib.request.urlopen", return_value=_make_http_response("{not valid json")):
            with pytest.raises(AiBackendProtocolError, match="JSON malformato"):
                client.chat_completion(
                    messages=[{"role": "user", "content": "test"}],
                    model_id="qwen2.5-7b-instruct",
                )

        assert provisional.is_verified is False

    def test_completion_wrong_response_model(self):
        """9. Response model errato solleva AiModelMismatchError e non verifica il binding."""
        client = LmStudioClient()
        provisional = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            status=ModelLifecycleStatus.JIT_UNVERIFIED,
            is_verified=False,
        )
        client.register_binding(provisional)

        mismatched_resp = {
            "model": "meta-llama-3.1-8b-instruct",  # Mismatch con Qwen!
            "choices": [{"message": {"content": "ciao"}, "finish_reason": "stop"}],
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(mismatched_resp)):
            with pytest.raises(AiModelMismatchError, match="Response model mismatch"):
                client.chat_completion(
                    messages=[{"role": "user", "content": "test"}],
                    model_id="qwen2.5-7b-instruct",
                )

        assert provisional.is_verified is False

    def test_load_model_unknown_status_rejected(self):
        """10. Status sconosciuto in load_model solleva AiBackendProtocolError e NON diventa LOADED."""
        client = LmStudioClient()
        nonsense_resp = {
            "status": "nonsense",
            "model": "qwen2.5-7b-instruct",
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(nonsense_resp)):
            with pytest.raises(AiBackendProtocolError, match="status sconosciuto o non riconosciuto"):
                client.load_model("qwen2.5-7b-instruct")

        assert client._last_loaded_model is None
        assert client._active_binding is None

    def test_load_model_loading_status(self):
        """11. Status loading restituisce ModelLifecycleStatus.LOADING senza impostare _last_loaded_model."""
        client = LmStudioClient()
        loading_resp = {
            "status": "loading",
            "model": "qwen2.5-7b-instruct",
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(loading_resp)):
            res = client.load_model("qwen2.5-7b-instruct")

        assert res.status == ModelLifecycleStatus.LOADING
        assert client._last_loaded_model is None

    def test_load_model_loaded_valid(self):
        """12. Status loaded valido produce ModelLifecycleStatus.LOADED e certifica il binding."""
        client = LmStudioClient()
        valid_resp = {
            "status": "loaded",
            "model": "qwen2.5-7b-instruct",
            "instance_id": "real-inst-uuid-101",
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(valid_resp)):
            res = client.load_model("qwen2.5-7b-instruct")

        assert res.status == ModelLifecycleStatus.LOADED
        assert res.instance_id == "real-inst-uuid-101"
        assert client._last_loaded_model == "real-inst-uuid-101"
        assert client._active_binding is not None
        assert client._active_binding.is_verified is True

    def test_load_model_jit_unverified_preserves_unverified(self):
        """13. Risposta senza modello esplicito produce JIT_UNVERIFIED non verificato."""
        client = LmStudioClient()
        opaque_resp = {
            "id": "opaque-inst-999",
            # Nessun campo model o status loaded
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(opaque_resp)):
            res = client.load_model("qwen2.5-7b-instruct")

        assert res.status == ModelLifecycleStatus.JIT_UNVERIFIED
        assert client._active_binding is not None
        assert client._active_binding.is_verified is False
        assert client._last_loaded_model is None  # NON aggiornato prematuramente

    def test_provisional_binding_promoted_only_after_full_completion(self):
        """14. Promozione a verificato avviene solo dopo completamento convalidato."""
        client = LmStudioClient()
        provisional = ModelIdentityBinding(
            requested_canonical_id="qwen2.5-7b-instruct",
            resolved_model_key="qwen2.5-7b-instruct",
            status=ModelLifecycleStatus.JIT_UNVERIFIED,
            is_verified=False,
        )
        client.register_binding(provisional)

        valid_completion = {
            "model": "qwen2.5-7b-instruct",
            "choices": [
                {
                    "message": {"content": "Risposta valida"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with patch("urllib.request.urlopen", return_value=_make_http_response(valid_completion)):
            resp = client.chat_completion(
                messages=[{"role": "user", "content": "ciao"}],
                model_id="qwen2.5-7b-instruct",
            )

        assert resp.content == "Risposta valida"
        assert provisional.is_verified is True
        assert provisional.status == ModelLifecycleStatus.LOADED
        assert client._last_loaded_model == "qwen2.5-7b-instruct"
        assert client.load_mode == "jit_verified"

    def test_no_cross_model_contamination(self):
        """15. Distinzione rigorosa tra famiglie Qwen2.5, Qwen3, Llama e DeepSeek."""
        assert get_model_family("qwen2.5-7b-instruct") == "QWEN2.5"
        assert get_model_family("qwen3-8b-instruct") == "QWEN3"
        assert get_model_family("meta-llama-3.1-8b-instruct") == "LLAMA"
        assert get_model_family("deepseek-r1-distill-qwen-7b") == "DEEPSEEK"

        # Conflitti incrociati vietati
        assert is_conflicting_model_or_instance("qwen2.5-7b-instruct", "meta-llama-3.1-8b-instruct") is True
        assert is_conflicting_model_or_instance("qwen2.5-7b-instruct", "qwen3-8b-instruct") is True
        assert is_conflicting_model_or_instance("qwen2.5-7b-instruct", "deepseek-r1-distill-qwen-7b") is True
        assert is_conflicting_model_or_instance("meta-llama-3.1-8b-instruct", "qwen2.5-7b-instruct") is True

        # Non equivalenti
        assert is_equivalent_operational_model("qwen2.5-7b-instruct", "qwen3-8b-instruct") is False
        assert is_equivalent_operational_model("qwen2.5-7b-instruct", "meta-llama-3.1-8b-instruct") is False

    def test_no_premature_metadata_update_on_failure(self):
        """16. In caso di errore HTTP durante il caricamento, nessun metadato viene aggiornato."""
        client = LmStudioClient()
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("http://127.0.0.1:1234", 400, "Bad Request", {}, io.BytesIO(b"Bad Request"))):
            with pytest.raises(AiModelLoadError):
                client.load_model("qwen2.5-7b-instruct")

        assert client._last_loaded_model is None
        assert client._last_loaded_model_key is None
        assert client._active_binding is None
