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
    ModelLifecycleStatus,
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


# ==============================================================================
# TEST OBBLIGATORI AUDIT: CONTEXT LENGTH PROVENANCE E VERIFICA OPERATIVA
# ==============================================================================

def test_context_resolution_loaded_instance_at_4096():
    """1. Istanza caricata a 4096: context_length DEVE essere 4096 e NON 8192."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=({"id": "inst-4096", "context_length": 4096},),
        loaded_instance_context_length=4096,
        max_context_length=32768,
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.context_length == 4096
    assert resolved.loaded_instance_context_length == 4096
    assert resolved.configured_context_length == 8192
    assert resolved.context_evidence_source == "LOADED_INSTANCE_METADATA"
    assert resolved.server_confirmed_context_length == 4096


def test_context_resolution_loaded_instance_at_8192():
    """2. Istanza caricata a 8192: context_length confermato a 8192."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=({"id": "inst-8192", "context_length": 8192},),
        loaded_instance_context_length=8192,
        max_context_length=32768,
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.context_length == 8192
    assert resolved.loaded_instance_context_length == 8192
    assert resolved.server_confirmed_context_length == 8192
    assert resolved.context_evidence_source == "LOADED_INSTANCE_METADATA"


def test_context_resolution_max_32768_with_loaded_4096():
    """3. Max context 32768 con istanza caricata a 4096: prevale l'istanza effettiva (4096), non max_context né 8192."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        max_context_length=32768,
        loaded_instance_context_length=4096,
        loaded_instances=({"id": "inst-qwen", "context_length": 4096},),
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.model_max_context_length == 32768
    assert resolved.context_length == 4096
    assert resolved.loaded_instance_context_length == 4096
    assert resolved.context_length != 8192
    assert resolved.context_length != 32768


def test_context_resolution_instance_context_absent():
    """4. Context dell'istanza assente: context_length è None (UNKNOWN) e NON 8192."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=({"id": "inst-qwen"},),  # Nessun context_length nell'istanza
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.is_loaded is True
    assert resolved.context_length is None
    assert resolved.loaded_instance_context_length is None
    assert resolved.context_evidence_source == "UNKNOWN"
    assert resolved.server_confirmed_context_length is None


def test_context_resolution_installed_but_unloaded():
    """5. Modello installato ma non caricato: context_length è None, load_mode è 'unloaded'."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=(),
        max_context_length=32768,
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.is_loaded is False
    assert resolved.context_length is None
    assert resolved.loaded_instance_context_length is None
    assert resolved.context_evidence_source == "NOT_LOADED"
    assert resolved.model_max_context_length == 32768


def test_context_resolution_configured_8192_backend_4096():
    """6. Valore di configurazione 8192 ma backend a 4096: separazione esplicita tra configured e loaded."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=({"id": "inst-qwen", "context_length": 4096},),
    )
    resolved = resolve_operational_model_from_metadata(
        [meta],
        "qwen2.5-7b-instruct",
        configured_context_length=8192,
    )
    assert resolved.configured_context_length == 8192
    assert resolved.requested_context_length == 8192
    assert resolved.loaded_instance_context_length == 4096
    assert resolved.context_length == 4096
    assert resolved.context_length != resolved.configured_context_length


def test_context_resolution_loader_confirms_8192(monkeypatch):
    """7. Loader che conferma correttamente 8192: ModelLoadResult registra la risposta positiva del server."""
    def mock_urlopen(req, timeout):
        class LoaderResp:
            def __init__(self):
                self.status = 200
            def read(self):
                return json.dumps({
                    "status": "loaded",
                    "model": "qwen2.5-7b-instruct",
                    "instance_id": "inst-qwen2.5-8192",
                    "context_length": 8192,
                }).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return LoaderResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    load_res = client.load_model("qwen2.5-7b-instruct", context_length=8192)

    assert load_res.status in (ModelLifecycleStatus.LOADED, ModelLifecycleStatus.ALREADY_LOADED)
    assert load_res.context_length == 8192
    assert load_res.loaded_instance_context_length == 8192
    assert load_res.context_evidence_source == "LOADER_API_RESPONSE"


def test_context_resolution_incoherent_metadata():
    """8. Metadata incoerenti (valori non interi o malformati): non inventa arbitrariamente 8192."""
    dict_item = {
        "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        "display_name": "Qwen2.5 7B Instruct",
        "loaded_instances": [{"id": "inst-qwen", "context_length": "invalid_string_not_int"}],
        "max_context_length": "corrupt_data",
        "loaded_instance_context_length": None,
    }
    resolved = resolve_operational_model_from_metadata([dict_item], "qwen2.5-7b-instruct")
    assert resolved.context_length is None
    assert resolved.loaded_instance_context_length is None
    assert resolved.model_max_context_length is None
    assert resolved.context_length != 8192


def test_context_resolution_absence_of_verifiable_info():
    """9. Assenza totale di informazioni verificabili sul context: context_length rimane None."""
    meta = LocalModelMetadata(
        key="lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
        display_name="Qwen2.5 7B Instruct",
        loaded_instances=({"id": "inst-qwen"},),
        max_context_length=None,
        loaded_instance_context_length=None,
    )
    resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
    assert resolved.context_length is None
    assert resolved.server_confirmed_context_length is None
    assert resolved.context_evidence_source == "UNKNOWN"


def test_context_resolution_audit_reproduction_matrix():
    """10. Replica esatta dello scenario di audit: variazione max_context non maschera il loaded_context."""
    for max_ctx in (4096, 8192, 32768, None):
        meta = LocalModelMetadata(
            model_key="qwen2.5-7b-instruct",
            max_context_length=max_ctx,
            loaded_instance_context_length=4096,
        )
        resolved = resolve_operational_model_from_metadata([meta], "qwen2.5-7b-instruct")
        # In NESSUN caso deve restituire 8192 quando l'istanza è caricata a 4096
        assert resolved.context_length == 4096, f"Fallito per max_context={max_ctx}: atteso 4096, ottenuto {resolved.context_length}"
        assert resolved.loaded_instance_context_length == 4096
        assert resolved.model_max_context_length == max_ctx

