"""
tests/unit/test_lmstudio_native_integration.py
----------------------------------------------
Test di integrazione simulata end-to-end conforme ai contratti ufficiali LM Studio REST API:
Sequenza:
1. GET /api/v1/models (elenco modelli installati con Qwen2.5 Q4_K_M)
2. Risoluzione deterministica del modello Qwen2.5 Q4_K_M
3. POST /api/v1/models/load (payload ufficiale con instance_id e load_config.context_length)
4. Lettura instance_id e load_config
5. Successiva interrogazione GET /api/v1/models per verifica istanza caricata e binding
6. Topic Discovery con vero adapter LmStudioClient e validazione response.model
7. Topic Detection con vero adapter LmStudioClient e validazione response.model
8. Valutazione Consistency e integrità del binding
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from ai.backend import LlmCompletionResponse
from ai.lmstudio import (
    AiModelNotInstalledError,
    LmStudioClient,
    ModelLifecycleStatus,
    clear_model_bindings,
    is_equivalent_operational_model,
    resolve_operational_model_from_metadata,
)
from analysis.service import LocalTopicAnalysisService
from ui.demo import build_synthetic_demo_dataset


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


def _build_test_document():
    doc_demo, _, _ = build_synthetic_demo_dataset()
    return doc_demo


def test_full_simulated_native_integration_sequence():
    """Esegue l'intero ciclo GET -> RESOLVE -> LOAD -> GET -> DISCOVERY -> DETECTION -> CONSISTENCY."""
    clear_model_bindings()
    client = LmStudioClient(base_url="http://127.0.0.1:1234", model_id="qwen2.5-7b-instruct")

    # Stato del mock server LM Studio
    server_state = {
        "is_loaded": False,
        "loaded_instance_id": None,
        "loaded_context": 8192,
    }

    doc = _build_test_document()
    eid = doc.bundles[0].text_evidence_sections[0].evidence_id

    # Risposta Discovery strutturata conforme
    discovery_llm_json = json.dumps({
        "topics": [
            {
                "label": "Logistica e Ispezione Depositi",
                "short_description": "Pianificazione container e ispezione depositi merci",
                "evidence_ids": [eid],
            }
        ]
    })

    # Risposta Detection strutturata conforme
    detection_llm_json = json.dumps({
        "decision": "PRESENT",
        "evidence_ids": [eid],
        "rationale": "Il messaggio contiene espliciti riferimenti a logistica container e ispezione depositi.",
    })

    def mock_urlopen(req, timeout=30.0):
        url = req.full_url
        method = req.get_method()

        # 1. GET /api/v1/models (elenco dettagliato nativo LM Studio con schema ufficiale)
        if "/api/v1/models" in url and method == "GET":
            loaded_instances = []
            if server_state["is_loaded"]:
                loaded_instances.append({
                    "id": server_state["loaded_instance_id"],
                    "config": {
                        "context_length": server_state["loaded_context"],
                    },
                })
            payload = {
                "models": [
                    {
                        "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
                        "display_name": "Qwen2.5-7B-Instruct",
                        "quantization": {
                            "name": "Q4_K_M",
                            "bits_per_weight": 4,
                        },
                        "max_context_length": 32768,
                        "loaded_instances": loaded_instances,
                    }
                ]
            }
            return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

        # 2. GET /v1/models (liveness check)
        if "/v1/models" in url and method == "GET":
            payload = {"data": [{"id": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"}]}
            return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

        # 3. POST /api/v1/models/load (caricamento modello con schema ufficiale)
        if "/api/v1/models/load" in url and method == "POST":
            server_state["is_loaded"] = True
            server_state["loaded_instance_id"] = "qwen-instance-8192"
            payload = {
                "type": "llm",
                "instance_id": server_state["loaded_instance_id"],
                "status": "loaded",
                "load_config": {
                    "context_length": server_state["loaded_context"],
                },
            }
            return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))

        # 4. POST /v1/chat/completions (inferenza LLM per Discovery o Detection)
        if "/v1/chat/completions" in url and method == "POST":
            req_data = json.loads(req.data.decode("utf-8"))
            messages = req_data.get("messages", [])
            all_text = " ".join(m.get("content", "") for m in messages).lower()
            if "open topic discovery" in all_text or "discover the main topics" in all_text or ("topics" in all_text and "evidence_ids" in all_text and "label" in all_text):
                resp_content = discovery_llm_json
            else:
                resp_content = detection_llm_json

            chat_res = {
                "id": "chatcmpl-mock-12345",
                "object": "chat.completion",
                "model": server_state["loaded_instance_id"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": resp_content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 45,
                    "total_tokens": 165,
                },
            }
            return DummyHTTPResponse(json.dumps(chat_res).encode("utf-8"))

        raise ValueError(f"URL non gestito nel mock di integrazione: {url} ({method})")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # PASSAGGIO 1: Raggiungibilità backend
        assert client.is_available() is True

        # PASSAGGIO 2: Query GET /api/v1/models e risoluzione Qwen2.5 Q4_K_M
        native_models = client.get_native_models()
        assert len(native_models) == 1
        resolved = resolve_operational_model_from_metadata(native_models, "qwen2.5-7b-instruct")
        assert resolved.model_key == "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"
        assert resolved.quantization == "Q4_K_M"
        assert resolved.selected_quantization == "Q4_K_M"
        assert resolved.quantization_evidence_source == "SERVER_API_QUANTIZATION_OBJECT"
        assert resolved.is_loaded is False  # Non ancora caricato

        # PASSAGGIO 3: Caricamento modello via ensure_model_loaded (POST /api/v1/models/load)
        active_id = client.ensure_model_loaded(
            model_id=resolved.model_key,
            context_length=8192,
        )
        assert active_id == "qwen-instance-8192"

        # PASSAGGIO 4: Verifica binding attivo del client
        binding = client.get_active_binding()
        assert binding is not None
        assert binding.is_verified is True
        assert binding.resolved_model_key == "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"
        assert binding.loaded_instance_id == "qwen-instance-8192"
        assert binding.api_identifier == "qwen-instance-8192"
        assert binding.loaded_instance_context_length == 8192
        assert binding.quantization == "Q4_K_M"
        assert binding.context_evidence_source == "LOADER_API_RESPONSE"

        # PASSAGGIO 5: Verifica successiva interrogazione GET /api/v1/models
        updated_native = client.get_native_models()
        res_after = resolve_operational_model_from_metadata(updated_native, "qwen2.5-7b-instruct")
        assert res_after.is_loaded is True
        assert res_after.instance_id == "qwen-instance-8192"
        assert res_after.loaded_instance_context_length == 8192
        assert res_after.quantization == "Q4_K_M"

        # PASSAGGIO 6: Esecuzione Topic Discovery con vero adapter LmStudioClient
        service = LocalTopicAnalysisService()
        doc = _build_test_document()

        disc_res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
            max_topics=5,
        )
        assert len(disc_res.topics) == 1
        assert disc_res.topics[0].label == "Logistica e Ispezione Depositi"
        disc_model = disc_res.metadata.get("response_model")
        assert disc_model == "qwen-instance-8192"
        assert is_equivalent_operational_model("qwen2.5-7b-instruct", disc_model, client=client) is True

        # PASSAGGIO 7: Esecuzione Topic Detection con vero adapter LmStudioClient (prova del nove)
        first_topic = disc_res.topics[0]
        det_res, consistency = service.verify_discovered_topic(
            document=doc,
            discovered_topic=first_topic,
            discovery_result=disc_res,
            client=client,
            model_name="qwen2.5-7b-instruct",
        )
        assert det_res is not None
        assert det_res.decision.value == "PRESENT"
        det_model = det_res.metadata.get("response_model")
        assert det_model == "qwen-instance-8192"
        assert is_equivalent_operational_model("qwen2.5-7b-instruct", det_model, client=client) is True

        # PASSAGGIO 8: Verifica Consistency e allineamento formale
        assert consistency is not None
        assert consistency.status.value in ("CONFIRMED", "STRONG_CONFIRMATION", "CONSISTENT")
        assert disc_model == det_model == binding.api_identifier


def test_simulated_native_integration_negative_nonexistent_instance():
    """Caso negativo: target_instance_id inesistente solleva AiModelNotInstalledError e non crea binding."""
    clear_model_bindings()
    client = LmStudioClient(base_url="http://127.0.0.1:1234", model_id="qwen2.5-7b-instruct")

    payload = {
        "models": [
            {
                "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
                "display_name": "Qwen2.5-7B-Instruct",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "max_context_length": 32768,
                "loaded_instances": [
                    {"id": "qwen-instance-8192", "config": {"context_length": 8192}}
                ],
            }
        ]
    }

    def mock_urlopen(req, timeout=30.0):
        url = req.full_url
        if "/v1/models" in url:
            return DummyHTTPResponse(json.dumps({"data": [{"id": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"}]}).encode("utf-8"))
        if "/api/v1/models" in url:
            return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))
        raise ValueError(f"URL non atteso: {url}")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        from ai.backend import AiModelNotInstalledError
        with pytest.raises(AiModelNotInstalledError) as exc_info:
            client.ensure_model_loaded(
                model_id="qwen2.5-7b-instruct",
                target_instance_id="not-an-instance-id",
            )
        assert "not-an-instance-id" in str(exc_info.value)
        assert client.get_active_binding() is None


def test_simulated_native_integration_negative_wrong_quantization():
    """Caso negativo: quantizzazione esposta Q8_0 quando è richiesta Q4_K_M."""
    clear_model_bindings()
    client = LmStudioClient(base_url="http://127.0.0.1:1234", model_id="qwen2.5-7b-instruct")

    payload = {
        "models": [
            {
                "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF/q8_0",
                "display_name": "Qwen2.5-7B-Instruct",
                "quantization": {"name": "Q8_0", "bits_per_weight": 8},
                "max_context_length": 32768,
                "loaded_instances": [
                    {"id": "qwen-instance-q8", "config": {"context_length": 8192}}
                ],
            }
        ]
    }

    def mock_urlopen(req, timeout=30.0):
        url = req.full_url
        if "/api/v1/models" in url:
            return DummyHTTPResponse(json.dumps(payload).encode("utf-8"))
        raise ValueError(f"URL non atteso: {url}")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        native_models = client.get_native_models()
        # 1. Strict mode (fallback disabilitato): REJECTED con AiModelNotInstalledError
        with pytest.raises(AiModelNotInstalledError):
            resolve_operational_model_from_metadata(
                native_models,
                "qwen2.5-7b-instruct",
                required_quantization="Q4_K_M",
                allow_fallback_quantization=False,
            )

        # 2. UI / Explicit fallback autorizzato: ACCEPTED con fallback tracciato
        res = resolve_operational_model_from_metadata(
            native_models,
            "qwen2.5-7b-instruct",
            required_quantization="Q4_K_M",
            allow_fallback_quantization=True,
        )
        assert res.selected_quantization == "Q8_0"
        assert res.requested_quantization == "Q4_K_M"
        assert res.selected_quantization != res.requested_quantization

