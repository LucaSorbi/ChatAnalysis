"""
tests/unit/test_model_resolution_audit.py
-----------------------------------------
Test esaustivi per il Model Resolution e Model Identity Lifecycle:
- Criterio 16, 20: DeepSeek non viene instradato a Qwen resolver.
- Criterio 17, 21: Qwen3 non viene accettato come Qwen2.5.
- Criterio 18, 22: Model specs separate per Qwen, Llama, DeepSeek.
- Criterio 19, 23, 42: Model equivalence (canonical ID, model key, instance ID, response model).
- Criterio 20, 24, 43: JIT fallback non finge LOADED su 404 (status: jit_unverified).
- Criterio 21, 25, 44: is_model_loaded propaga errori reali (protocollo, HTTP, timeout, network).
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelAmbiguousError,
    AiModelMismatchError,
    AiModelNotInstalledError,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    LmStudioClient,
    LocalModelMetadata,
    OperationalModelSpec,
    ResolvedOperationalModel,
    is_equivalent_operational_model,
    resolve_operational_model_from_metadata,
)


def _make_meta(
    key: str,
    display_name: str | None = None,
    quant: str | None = "Q4_K_M",
    loaded_instances: list[dict[str, Any]] | None = None,
) -> LocalModelMetadata:
    return LocalModelMetadata(
        key=key,
        display_name=display_name or key,
        quantization=quant,
        loaded_instances=tuple(loaded_instances or []),
    )


def test_model_resolution_strict_family_isolation():
    """Verifica che una lista con Qwen2.5, Qwen3, DeepSeek e Llama risolva esclusivamente la famiglia richiesta."""
    installed = [
        _make_meta("lmstudio-community/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M", "Q4_K_M"),
        _make_meta("lmstudio-community/Qwen3-7B-Instruct-GGUF", "Qwen3-7B-Instruct-Q4_K_M", "Q4_K_M"),
        _make_meta("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B-GGUF", "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M", "Q4_K_M"),
        _make_meta("meta-llama/Meta-Llama-3.1-8B-Instruct-GGUF", "Meta-Llama-3.1-8B-Instruct-Q4_K_M", "Q4_K_M"),
    ]

    # 1. Richiesta Qwen2.5 -> solo Qwen2.5
    resolved_qwen = resolve_operational_model_from_metadata(installed, "qwen2.5-7b-instruct")
    assert "qwen2.5" in resolved_qwen.model_key.lower()
    assert "deepseek" not in resolved_qwen.model_key.lower()
    assert "qwen3" not in resolved_qwen.model_key.lower()
    assert resolved_qwen.canonical_spec.family == "QWEN2.5"

    # 2. Richiesta DeepSeek -> solo DeepSeek (non cross-routed a Qwen)
    resolved_deepseek = resolve_operational_model_from_metadata(installed, "deepseek-r1-distill-qwen-7b")
    assert "deepseek" in resolved_deepseek.model_key.lower()
    assert resolved_deepseek.canonical_spec.family == "DEEPSEEK"

    # 3. Richiesta Llama -> solo Llama 3.1
    resolved_llama = resolve_operational_model_from_metadata(installed, "meta-llama-3.1-8b-instruct")
    assert "llama-3.1" in resolved_llama.model_key.lower()
    assert resolved_llama.canonical_spec.family == "LLAMA"


def test_qwen3_never_resolved_as_qwen25():
    """Se è installato solo Qwen3, richiedere Qwen2.5 DEVE sollevare AiModelNotInstalledError."""
    installed = [
        _make_meta("lmstudio-community/Qwen3-7B-Instruct-GGUF", "Qwen3-7B-Instruct", "Q4_K_M"),
    ]
    with pytest.raises(AiModelNotInstalledError):
        resolve_operational_model_from_metadata(installed, "qwen2.5-7b-instruct")


def test_deepseek_never_resolved_as_qwen25():
    """Se è installato solo DeepSeek-R1-Distill-Qwen-7B, richiedere Qwen2.5 DEVE sollevare AiModelNotInstalledError."""
    installed = [
        _make_meta("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B-GGUF", "DeepSeek-R1-Distill-Qwen-7B", "Q4_K_M"),
    ]
    with pytest.raises(AiModelNotInstalledError):
        resolve_operational_model_from_metadata(installed, "qwen2.5-7b-instruct")


def test_preferred_quantization_q4_k_m():
    """Se sono presenti quantizzazioni multiple, Q4_K_M viene preferita."""
    installed = [
        _make_meta("lmstudio-community/Qwen2.5-7B-Instruct-GGUF/q2_k", "Qwen2.5 7B Q2_K", "Q2_K"),
        _make_meta("lmstudio-community/Qwen2.5-7B-Instruct-GGUF/q4_k_m", "Qwen2.5 7B Q4_K_M", "Q4_K_M"),
        _make_meta("lmstudio-community/Qwen2.5-7B-Instruct-GGUF/q8_0", "Qwen2.5 7B Q8_0", "Q8_0"),
    ]
    resolved = resolve_operational_model_from_metadata(installed, "qwen2.5-7b-instruct")
    assert resolved.quantization == "Q4_K_M"
    assert "q4_k_m" in resolved.model_key.lower()


def test_ambiguity_detection_without_preferred_quant():
    """Due modelli della stessa specifica senza differenziazione preferita sollevano AiModelAmbiguousError."""
    installed = [
        _make_meta("custom-org/Qwen2.5-7B-Instruct-GGUF-v1", "Qwen2.5 7B Instruct v1", "Q5_K_M"),
        _make_meta("custom-org/Qwen2.5-7B-Instruct-GGUF-v2", "Qwen2.5 7B Instruct v2", "Q5_K_S"),
    ]
    with pytest.raises(AiModelAmbiguousError):
        resolve_operational_model_from_metadata(installed, "qwen2.5-7b-instruct")


def test_model_equivalence_and_mismatch():
    """Verifica l'equivalenza autorizzata tra model key, instance id e canonical id, e il rigetto di modelli estranei."""
    # Equivalenza autorizzata per Qwen2.5
    assert is_equivalent_operational_model(
        target_model="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        response_model="qwen2.5-7b-instruct-abc123",
    ) is True

    # Rifiuto modello di altra famiglia (Llama spacciato per Qwen)
    assert is_equivalent_operational_model(
        target_model="qwen2.5-7b-instruct",
        response_model="meta-llama-3.1-8b-instruct",
    ) is False

    # Rifiuto DeepSeek spacciato per Qwen2.5
    assert is_equivalent_operational_model(
        target_model="qwen2.5-7b-instruct",
        response_model="deepseek-r1-distill-qwen-7b",
    ) is False

    # ResolvedOperationalModel instance method
    from ai.lmstudio import OPERATIONAL_SPECS
    resolved = ResolvedOperationalModel(
        canonical_spec=OPERATIONAL_SPECS["qwen2.5-7b-instruct"],
        requested_canonical_id="qwen2.5-7b-instruct",
        model_key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        is_loaded=True,
        instance_id="qwen2.5-7b-instruct-abc123",
        api_identifier="qwen2.5-7b-instruct-abc123",
        display_name="Qwen2.5-7B-Instruct",
    )
    assert resolved.is_equivalent_response_model("qwen2.5-7b-instruct-abc123") is True
    assert resolved.is_equivalent_response_model("meta-llama-3.1-8b-instruct") is False


def test_jit_fallback_404_returns_jit_unverified_and_not_loaded(monkeypatch):
    """Su endpoint load 404, il client restituisce jit_unverified e NON dichiara il modello loaded."""
    def mock_urlopen(req, timeout):
        url = req.full_url
        if "/api/v1/models/load" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"Not Found"))
        if "/api/v1/models" in url:
            class NativeResp:
                def __init__(self):
                    self.status = 200
                def read(self):
                    return json.dumps({
                        "data": [{"key": "qwen2.5-7b-instruct", "loaded_instances": []}]
                    }).encode("utf-8")
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return NativeResp()
        raise ValueError(f"URL non atteso: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    load_res = client.load_model("qwen2.5-7b-instruct")

    assert load_res["status"] == "jit_unverified"
    assert load_res["load_mode"] == "jit_unverified"
    assert client.is_model_loaded("qwen2.5-7b-instruct") is False


def test_is_model_loaded_propagates_real_errors(monkeypatch):
    """is_model_loaded non ingoia errori reali: HTTP 500, timeout, protocollo malformato, indisponibilità."""
    client = LmStudioClient()

    # 1. HTTP 500
    def mock_http_500(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"error"))
    monkeypatch.setattr(urllib.request, "urlopen", mock_http_500)
    with pytest.raises(AiBackendRequestError):
        client.is_model_loaded("qwen2.5-7b-instruct")

    # 2. Malformed JSON
    def mock_malformed_json(req, timeout):
        class MalformedResp:
            def __init__(self):
                self.status = 200
            def read(self):
                return b"THIS IS NOT JSON"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return MalformedResp()
    monkeypatch.setattr(urllib.request, "urlopen", mock_malformed_json)
    with pytest.raises(AiBackendProtocolError):
        client.is_model_loaded("qwen2.5-7b-instruct")

    # 3. Server offline (Connection Refused)
    import socket
    def mock_conn_refused(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))
    monkeypatch.setattr(urllib.request, "urlopen", mock_conn_refused)
    with pytest.raises(LmStudioUnavailableError):
        client.is_model_loaded("qwen2.5-7b-instruct")

    # 4. Timeout
    def mock_timeout(req, timeout):
        raise urllib.error.URLError(socket.timeout("Read timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", mock_timeout)
    with pytest.raises(AiBackendTimeoutError):
        client.is_model_loaded("qwen2.5-7b-instruct")
