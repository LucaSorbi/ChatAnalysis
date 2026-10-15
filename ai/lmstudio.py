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

from dataclasses import dataclass, field
from enum import Enum
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
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
DEFAULT_OPERATIONAL_LOAD_TIMEOUT_SECONDS: float = 240.0
DEFAULT_OPERATIONAL_INFERENCE_TIMEOUT_SECONDS: float = 240.0
DEFAULT_OPERATIONAL_TEMPERATURE: float = 0.0
DEFAULT_OPERATIONAL_MAX_TOKENS: int = 512

MODEL_DISPLAY_NAMES: dict[str, str] = {
    "qwen2.5-7b-instruct": "Qwen2.5-7B-Instruct",
    "meta-llama-3.1-8b-instruct": "Llama 3.1-8B-Instruct",
    "deepseek-r1-distill-qwen-7b": "DeepSeek-R1-Distill-Qwen-7B",
}


def extract_instance_context_length(inst: Any) -> int | None:
    """
    Estrae il context length da una loaded instance o risposta di caricamento LM Studio:
    1. Struttura ufficiale annidata: inst["config"]["context_length"] (o contextLength)
    2. Struttura annidata load_config: inst["load_config"]["context_length"] (o contextLength)
    3. Struttura flat di compatibilità: inst["context_length"] o inst["loaded_context_length"]
    Non inventa valori e restituisce None se non presente, non positivo o non convertibile in int.
    """
    if not isinstance(inst, dict):
        return None
    # 1. Config annidato ufficiale (GET /api/v1/models)
    cfg = inst.get("config")
    if isinstance(cfg, dict):
        for k in ("context_length", "contextLength", "loaded_context_length", "context_window"):
            val = cfg.get(k)
            if val is not None:
                try:
                    c = int(val)
                    if c > 0:
                        return c
                except (ValueError, TypeError):
                    pass
    # 2. Load_config annidato (POST /api/v1/models/load)
    load_cfg = inst.get("load_config")
    if isinstance(load_cfg, dict):
        for k in ("context_length", "contextLength", "loaded_context_length", "context_window"):
            val = load_cfg.get(k)
            if val is not None:
                try:
                    c = int(val)
                    if c > 0:
                        return c
                except (ValueError, TypeError):
                    pass
    # 3. Campi flat di compatibilità
    for k in ("context_length", "loaded_context_length", "contextLength", "context_window"):
        val = inst.get(k)
        if val is not None:
            try:
                c = int(val)
                if c > 0:
                    return c
            except (ValueError, TypeError):
                pass
    return None


def extract_quantization_name(raw_quant: Any) -> str | None:
    """
    Estrae il nome canonico della quantizzazione (es. 'Q4_K_M').
    Supporta:
    1. Oggetto/dizionario ufficiale REST LM Studio: {"name": "Q4_K_M", "bits_per_weight": 4} -> 'Q4_K_M'
    2. Stringa (retrocompatibilità): 'Q4_K_M' -> 'Q4_K_M'
    3. Oggetto privo di 'name' o null -> None
    """
    if raw_quant is None:
        return None
    if isinstance(raw_quant, dict):
        name = raw_quant.get("name")
        if name and isinstance(name, str) and name.strip():
            return name.strip().upper()
        return None
    if isinstance(raw_quant, str):
        s = raw_quant.strip()
        if not s:
            return None
        if s.startswith("{") and ("'name'" in s or '"name"' in s):
            try:
                parsed = json.loads(s.replace("'", '"'))
                if isinstance(parsed, dict) and parsed.get("name"):
                    return str(parsed["name"]).strip().upper()
            except Exception:
                pass
            m = re.search(r'["\']name["\']\s*:\s*["\']([^"\']+)["\']', s)
            if m:
                return m.group(1).strip().upper()
            return None
        return s.upper()
    return None


@dataclass(frozen=True)
class LocalModelMetadata:
    """Metadati di un modello installato localmente in LM Studio."""
    key: str
    display_name: str
    quantization: str | None = None
    loaded_instances: tuple[dict[str, Any], ...] = ()
    max_context_length: int | None = None
    loaded_instance_context_length: int | None = None
    quantization_evidence_source: str = "UNKNOWN"

    def __init__(
        self,
        key: str = "",
        display_name: str = "",
        quantization: Any = None,
        loaded_instances: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        max_context_length: int | None = None,
        loaded_instance_context_length: int | None = None,
        model_key: str | None = None,
        quantization_evidence_source: str | None = None,
    ):
        actual_key = str(model_key or key or "")
        actual_disp = str(display_name or actual_key)
        norm_quant = extract_quantization_name(quantization)
        object.__setattr__(self, "key", actual_key)
        object.__setattr__(self, "display_name", actual_disp)
        object.__setattr__(self, "quantization", norm_quant)

        quant_src = quantization_evidence_source
        if not quant_src:
            if isinstance(quantization, dict) and norm_quant:
                quant_src = "SERVER_API_QUANTIZATION_OBJECT"
            elif isinstance(quantization, str) and norm_quant:
                quant_src = "SERVER_API_QUANTIZATION_STRING"
            elif norm_quant:
                quant_src = "INFERRED"
            else:
                quant_src = "UNKNOWN"
        object.__setattr__(self, "quantization_evidence_source", quant_src)

        if isinstance(loaded_instances, list):
            loaded_tuple = tuple(loaded_instances)
        else:
            loaded_tuple = tuple(loaded_instances or ())

        ctx_from_inst = None
        for inst in loaded_tuple:
            if isinstance(inst, dict):
                c = extract_instance_context_length(inst)
                if c is not None:
                    ctx_from_inst = c
                    break

        actual_loaded_ctx = int(loaded_instance_context_length) if loaded_instance_context_length is not None else ctx_from_inst
        if actual_loaded_ctx is not None and not loaded_tuple:
            loaded_tuple = ({"id": actual_key, "config": {"context_length": actual_loaded_ctx}, "context_length": actual_loaded_ctx},)

        object.__setattr__(self, "loaded_instances", loaded_tuple)
        object.__setattr__(self, "max_context_length", int(max_context_length) if max_context_length is not None else None)
        object.__setattr__(self, "loaded_instance_context_length", actual_loaded_ctx)

    @property
    def model_key(self) -> str:
        return self.key


@dataclass(frozen=True)
class OperationalModelSpec:
    """Specifica canonica e vincoli di matching per una famiglia di modelli operativi."""
    canonical_id: str
    family: str
    display_name: str
    required_tokens: tuple[str, ...]
    forbidden_tokens: tuple[str, ...] = ()
    parameter_size: str = "7b"
    preferred_quantization: str = "q4_k_m"


OPERATIONAL_SPECS: dict[str, OperationalModelSpec] = {
    "qwen2.5-7b-instruct": OperationalModelSpec(
        canonical_id="qwen2.5-7b-instruct",
        family="QWEN2.5",
        display_name="Qwen2.5-7B-Instruct",
        required_tokens=("qwen", "2.5", "7b", "instruct"),
        forbidden_tokens=("deepseek", "qwen3"),
        parameter_size="7b",
        preferred_quantization="q4_k_m",
    ),
    "meta-llama-3.1-8b-instruct": OperationalModelSpec(
        canonical_id="meta-llama-3.1-8b-instruct",
        family="LLAMA",
        display_name="Llama 3.1-8B-Instruct",
        required_tokens=("llama", "3.1", "8b", "instruct"),
        forbidden_tokens=("qwen", "deepseek"),
        parameter_size="8b",
        preferred_quantization="q4_k_m",
    ),
    "deepseek-r1-distill-qwen-7b": OperationalModelSpec(
        canonical_id="deepseek-r1-distill-qwen-7b",
        family="DEEPSEEK",
        display_name="DeepSeek-R1-Distill-Qwen-7B",
        required_tokens=("deepseek", "7b"),
        forbidden_tokens=("llama",),
        parameter_size="7b",
        preferred_quantization="q4_k_m",
    ),
}


class ModelLifecycleStatus(str, Enum):
    NOT_LOADED = "NOT_LOADED"
    LOADING = "LOADING"
    LOADED = "LOADED"
    ALREADY_LOADED = "ALREADY_LOADED"
    JIT_UNVERIFIED = "JIT_UNVERIFIED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ModelLoadResult:
    """
    Esito tipizzato del caricamento di un modello in memoria tramite LM Studio REST API.
    Fornisce accesso tipizzato e compatibilità dizionario (.get, __getitem__) per i consumatori esistenti.
    """
    status: ModelLifecycleStatus
    model_key: str
    instance_id: str | None = None
    api_identifier: str | None = None
    load_mode: str = "native_loaded"
    context_length: int | None = None
    loaded_instance_context_length: int | None = None
    context_evidence_source: str = "UNKNOWN"
    error_message: str | None = None
    technical_details: str | None = None
    raw_response: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "status":
            return self.status.value.lower()
        if key == "instance_id":
            return self.instance_id
        if key == "id":
            return self.instance_id or self.model_key
        if key == "model":
            return self.model_key
        if key == "load_mode":
            return self.load_mode
        if key == "context_length":
            return self.context_length
        if key == "loaded_instance_context_length":
            return self.loaded_instance_context_length
        if key == "context_evidence_source":
            return self.context_evidence_source
        if key == "error":
            return self.error_message
        return self.raw_response.get(key, default)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in ("instance_id", "id", "error", "context_length", "loaded_instance_context_length"):
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return key in ("status", "instance_id", "id", "model", "load_mode", "context_length", "loaded_instance_context_length", "context_evidence_source") or key in self.raw_response


@dataclass
class ModelIdentityBinding:
    """
    Rappresenta il binding esplicito e verificato tra:
    requested_canonical_id <-> resolved_model_key <-> loaded_instance_id <-> api_identifier.
    Garantisce che un instance ID opaco non venga trattato arbitrariamente come equivalente
    a un modello senza relazione verificata.
    """
    requested_canonical_id: str
    resolved_model_key: str
    loaded_instance_id: str | None = None
    api_identifier: str = ""
    load_mode: str = "unloaded"
    status: ModelLifecycleStatus = ModelLifecycleStatus.NOT_LOADED
    is_verified: bool = False
    configured_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH
    loaded_instance_context_length: int | None = None
    context_evidence_source: str = "UNKNOWN"
    quantization: str | None = None
    quantization_evidence_source: str = "UNKNOWN"


# Il registro globale mutabile _ACTIVE_MODEL_BINDINGS è stato eliminato alla radice.
# L'identità e il lifecycle dei binding appartengono rigorosamente all'istanza LmStudioClient.

def register_model_binding(binding: ModelIdentityBinding) -> None:
    """Funzione legacy: il registro globale mutabile è stato rimosso. Utilizzare client.register_binding()."""
    pass


def get_model_binding(identifier: str) -> ModelIdentityBinding | None:
    """Funzione legacy: il registro globale mutabile è stato rimosso. Utilizzare client.get_binding()."""
    return None


def remove_model_binding(identifier: str) -> None:
    """Funzione legacy: il registro globale mutabile è stato rimosso. Utilizzare client.invalidate_binding()."""
    pass


def clear_model_bindings() -> None:
    """Funzione legacy: il registro globale mutabile è stato rimosso per garantire isolamento per-client."""
    pass


@dataclass(frozen=True)
class ResolvedOperationalModel:
    """
    Rappresentazione tipizzata dell'identità del modello operativo risolto.
    Distingue: canonical spec, model key installato, instance id caricato e api identifier.
    Distingue esplicitamente il contesto richiesto, configurato e confermato dal backend.
    Separa rigorosamente quantizzazione richiesta, disponibile e selezionata.
    """
    requested_canonical_id: str
    model_key: str
    is_loaded: bool
    instance_id: str | None
    api_identifier: str
    display_name: str
    load_mode: str = "unloaded"
    quantization: str | None = None
    context_length: int | None = None
    canonical_spec: OperationalModelSpec | None = None
    requested_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH
    configured_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH
    loaded_instance_context_length: int | None = None
    model_max_context_length: int | None = None
    context_evidence_source: str = "UNKNOWN"
    requested_quantization: str = "Q4_K_M"
    available_quantization: str | None = None
    selected_quantization: str | None = None
    quantization_evidence_source: str = "UNKNOWN"

    def __init__(
        self,
        requested_canonical_id: str,
        model_key: str,
        is_loaded: bool,
        instance_id: str | None,
        api_identifier: str,
        display_name: str,
        load_mode: str = "unloaded",
        quantization: str | None = None,
        context_length: int | None = None,
        canonical_spec: OperationalModelSpec | None = None,
        requested_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
        configured_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
        loaded_instance_context_length: int | None = None,
        model_max_context_length: int | None = None,
        context_evidence_source: str = "UNKNOWN",
        requested_quantization: str = "Q4_K_M",
        available_quantization: str | None = None,
        selected_quantization: str | None = None,
        quantization_evidence_source: str = "UNKNOWN",
    ):
        object.__setattr__(self, "requested_canonical_id", requested_canonical_id)
        object.__setattr__(self, "model_key", model_key)
        object.__setattr__(self, "is_loaded", is_loaded)
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "api_identifier", api_identifier)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "load_mode", load_mode)

        eff_quant = selected_quantization or extract_quantization_name(quantization)
        object.__setattr__(self, "quantization", eff_quant)
        object.__setattr__(self, "selected_quantization", eff_quant)
        object.__setattr__(self, "available_quantization", available_quantization or eff_quant)
        object.__setattr__(self, "requested_quantization", (requested_quantization or "Q4_K_M").upper())
        object.__setattr__(self, "quantization_evidence_source", quantization_evidence_source or "UNKNOWN")

        eff_loaded_ctx = loaded_instance_context_length if loaded_instance_context_length is not None else context_length
        object.__setattr__(self, "context_length", eff_loaded_ctx)
        object.__setattr__(self, "loaded_instance_context_length", eff_loaded_ctx)
        object.__setattr__(self, "canonical_spec", canonical_spec)
        object.__setattr__(self, "requested_context_length", requested_context_length)
        object.__setattr__(self, "configured_context_length", configured_context_length)
        object.__setattr__(self, "model_max_context_length", model_max_context_length)
        object.__setattr__(self, "context_evidence_source", context_evidence_source)

    @property
    def server_confirmed_context_length(self) -> int | None:
        return self.loaded_instance_context_length

    def __iter__(self):
        yield self.model_key
        yield self.is_loaded
        yield self.instance_id

    def matches_response(self, response_model: str) -> bool:
        """Verifica se il response_model coincide con l'identità del modello risolto."""
        if not response_model:
            return False
        r_clean = response_model.strip().lower()
        if r_clean in (self.model_key.lower(), self.api_identifier.lower()):
            return True
        if self.instance_id and r_clean == self.instance_id.lower():
            return True
        return is_equivalent_operational_model(self.model_key, response_model)

    def is_equivalent_response_model(self, response_model: str) -> bool:
        return self.matches_response(response_model)


def get_model_family(name: str | None) -> str | None:
    """
    Restituisce la famiglia canonica di appartenenza del modello.
    Distingue categoricamente QWEN2.5, QWEN3, LLAMA e DEEPSEEK.
    """
    if not name:
        return None
    n = name.strip().lower()
    if "deepseek" in n:
        return "DEEPSEEK"
    if "llama" in n:
        return "LLAMA"
    if "qwen" in n:
        if "2.5" in n:
            return "QWEN2.5"
        if "3" in n or "qwen3" in n:
            return "QWEN3"
        return "QWEN_OTHER"
    return None


def is_equivalent_operational_model(
    target_model: str,
    response_model: str,
    client: Any = None,
    binding: ModelIdentityBinding | None = None,
) -> bool:
    """
    Verifica se response_model è equivalente a target_model nel contesto operativo LM Studio.
    Risolve esplicitamente binding registrati tra instance ID opachi e model key
    ESCLUSIVAMENTE se fornito un binding locale o un client esplicito.
    Nessun registro globale o fallback tra client differenti.
    Rifiuta categoricamente mismatch cross-family (es. qwen vs llama o qwen vs deepseek).
    """
    if not target_model or not response_model:
        return False
    t_clean = target_model.strip().lower()
    r_clean = response_model.strip().lower()
    if t_clean == r_clean:
        return True

    # Risoluzione binding esplicito per target_model
    b_target: ModelIdentityBinding | None = None
    if client is not None and hasattr(client, "get_binding"):
        b_target = client.get_binding(target_model)
    elif binding is not None:
        if (
            (binding.loaded_instance_id and binding.loaded_instance_id.strip().lower() == t_clean)
            or (binding.api_identifier and binding.api_identifier.strip().lower() == t_clean)
            or (binding.resolved_model_key and binding.resolved_model_key.strip().lower() == t_clean)
            or (binding.requested_canonical_id and binding.requested_canonical_id.strip().lower() == t_clean)
        ):
            b_target = binding

    # Risoluzione binding esplicito per response_model
    b_resp: ModelIdentityBinding | None = None
    if client is not None and hasattr(client, "get_binding"):
        b_resp = client.get_binding(response_model)
    elif binding is not None:
        if (
            (binding.loaded_instance_id and binding.loaded_instance_id.strip().lower() == r_clean)
            or (binding.api_identifier and binding.api_identifier.strip().lower() == r_clean)
            or (binding.resolved_model_key and binding.resolved_model_key.strip().lower() == r_clean)
            or (binding.requested_canonical_id and binding.requested_canonical_id.strip().lower() == r_clean)
        ):
            b_resp = binding

    eff_t = b_target.resolved_model_key if b_target is not None else target_model
    eff_r = b_resp.resolved_model_key if b_resp is not None else response_model
    fam_t = get_model_family(eff_t.strip().lower())
    fam_r = get_model_family(eff_r.strip().lower())
    if fam_t and fam_r and fam_t != fam_r:
        return False
    if (fam_t is None) != (fam_r is None):
        return False

    if b_target is not None:
        bound_key = b_target.resolved_model_key
        if bound_key.strip().lower() != t_clean:
            if is_equivalent_operational_model(bound_key, response_model, client=client):
                return True
        bound_req = b_target.requested_canonical_id
        if bound_req.strip().lower() != t_clean:
            if is_equivalent_operational_model(bound_req, response_model, client=client):
                return True

    if b_resp is not None:
        bound_key = b_resp.resolved_model_key
        if bound_key.strip().lower() != r_clean:
            if is_equivalent_operational_model(target_model, bound_key, client=client):
                return True
        bound_req = b_resp.requested_canonical_id
        if bound_req.strip().lower() != r_clean:
            if is_equivalent_operational_model(target_model, bound_req, client=client):
                return True

    for spec_id, spec in OPERATIONAL_SPECS.items():
        t_match = all(tok in t_clean for tok in spec.required_tokens) and not any(tok in t_clean for tok in spec.forbidden_tokens)
        r_match = all(tok in r_clean for tok in spec.required_tokens) and not any(tok in r_clean for tok in spec.forbidden_tokens)
        if t_match and r_match:
            return True

    if (r_clean.startswith(t_clean) or t_clean.startswith(r_clean)) and fam_t == fam_r and fam_t is not None:
        return True

    return False


def is_conflicting_model_or_instance(requested_model: str, identifier: str) -> bool:
    """
    Verifica se un identificatore restituito (model key o instance ID) è in conflitto o ambiguo
    rispetto al modello richiesto. Rifiuta esplicitamente se appartiene a una famiglia aliena
    (es. deepseek, llama, qwen3 quando richiesto qwen2.5).
    """
    if not requested_model or not identifier:
        return False
    r_clean = requested_model.strip().lower()
    i_clean = identifier.strip().lower()
    if r_clean == i_clean:
        return False

    fam_req = get_model_family(r_clean)
    fam_id = get_model_family(i_clean)

    # Se l'identificatore dichiara una famiglia nota e diversa -> conflitto categorico
    if fam_req and fam_id:
        if (fam_req == "QWEN2.5" and fam_id == "QWEN_OTHER") or (fam_req == "QWEN_OTHER" and fam_id == "QWEN2.5"):
            # Un instance ID generico con token "qwen" (es. instance-qwen-001) non è intrinsecamente in conflitto con Qwen2.5
            pass
        elif fam_req != fam_id:
            return True

    # Se sono definite specifiche operative, controlla i forbidden_tokens
    for spec_id, spec in OPERATIONAL_SPECS.items():
        if spec_id in r_clean or all(tok in r_clean for tok in spec.required_tokens):
            if any(tok in i_clean for tok in spec.forbidden_tokens):
                return True

    return False


def get_model_display_name(model_id: str) -> str:
    """
    Restituisce il nome formale del modello per messaggi UI e log
    usando OperationalModelSpec come unica fonte di verità.
    """
    m_clean = model_id.lower().replace("_", "-").replace(":", "-").replace("@", "-")
    # Ordine rigoroso: DeepSeek verificato PRIMA di controlli su qwen
    if "deepseek" in m_clean:
        return "DeepSeek-R1-Distill-Qwen-7B"
    if "llama" in m_clean:
        return "Llama 3.1-8B-Instruct"
    if "qwen" in m_clean:
        if "2.5" in m_clean:
            return "Qwen2.5-7B-Instruct"
    for key, spec in OPERATIONAL_SPECS.items():
        key_clean = key.lower().replace("_", "-")
        if key_clean in m_clean or m_clean in key_clean:
            return spec.display_name
    return model_id


def resolve_installed_model_id(
    requested_model: str,
    available_models: tuple[str, ...] | list[str],
) -> str | None:
    """
    Risolve un identificatore modello richiesto rispetto alla lista dei modelli
    effettivamente installati/disponibili in LM Studio.
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

    # 3. Match canonico
    req_norm = req_lower.replace("_", "-").replace(".", "")
    for m in available_models:
        m_norm = m.lower().replace("_", "-").replace(".", "")
        if req_norm == m_norm:
            return m

    # 4. Sottostringa canonica non ambigua
    req_norm_dash = req_lower.replace("_", "-")
    for m in available_models:
        m_norm = m.lower().replace("_", "-")
        if req_norm_dash in m_norm:
            return m

    return None


def _normalize_model_item(item: LocalModelMetadata | dict[str, Any] | str) -> LocalModelMetadata:
    if isinstance(item, LocalModelMetadata):
        return item
    if isinstance(item, str):
        return LocalModelMetadata(key=item, display_name=item)
    key = str(item.get("key") or item.get("id") or item.get("name") or "").strip()
    disp = str(item.get("display_name") or item.get("name") or key)
    raw_quant = item.get("quantization")
    quant = extract_quantization_name(raw_quant)
    quant_src = "UNKNOWN"
    if isinstance(raw_quant, dict):
        quant_src = "SERVER_API_QUANTIZATION_OBJECT" if quant else "INCOMPLETE_METADATA"
    elif isinstance(raw_quant, str):
        quant_src = "SERVER_API_QUANTIZATION_STRING" if quant else "INCOMPLETE_METADATA"

    if quant is None:
        key_lower = f"{key} {disp}".lower().replace("-", "_")
        for q_token in ("q4_k_m", "q4_k_s", "q8_0", "q5_k_m", "q5_k_s", "q6_k", "q2_k", "q3_k_m"):
            if q_token in key_lower:
                quant = q_token.upper()
                quant_src = "MODEL_KEY_INFERRED"
                break

    raw_loaded = item.get("loaded_instances") or []
    state_val = str(item.get("state") or item.get("status") or "").lower()
    is_loaded_state = state_val in ("loaded", "active", "ready") or item.get("loaded") is True or item.get("is_loaded") is True
    if is_loaded_state and not raw_loaded:
        raw_loaded = [{"id": key}]
    loaded = tuple(raw_loaded) if isinstance(raw_loaded, (list, tuple)) else ()
    max_ctx = item.get("max_context_length")
    loaded_ctx = item.get("loaded_instance_context_length")
    if loaded_ctx is None and loaded:
        for inst in loaded:
            if isinstance(inst, dict):
                c_val = extract_instance_context_length(inst)
                if c_val is not None:
                    loaded_ctx = c_val
                    break
    return LocalModelMetadata(
        key=key,
        display_name=disp,
        quantization=quant,
        loaded_instances=loaded,
        max_context_length=int(max_ctx) if isinstance(max_ctx, int) else None,
        loaded_instance_context_length=int(loaded_ctx) if isinstance(loaded_ctx, int) else None,
        quantization_evidence_source=quant_src,
    )


def resolve_qwen_operational_model(
    models: Sequence[LocalModelMetadata | dict[str, Any] | str],
    target_spec: str = "qwen2.5-7b-instruct",
    target_instance_id: str | None = None,
    required_quantization: str | None = "Q4_K_M",
    allow_fallback_quantization: bool = False,
) -> tuple[str, bool, str | None]:
    """
    Risolve deterministicamente il modello Qwen2.5-7B-Instruct.
    Verifica '2.5' esplicitamente e rifiuta modelli cross-family (DeepSeek, Qwen3).
    """
    spec = OPERATIONAL_SPECS["qwen2.5-7b-instruct"]
    resolved = resolve_operational_model_from_metadata(
        models,
        requested_model=spec.canonical_id,
        target_instance_id=target_instance_id,
        required_quantization=required_quantization,
        allow_fallback_quantization=allow_fallback_quantization,
    )
    return resolved.model_key, resolved.is_loaded, resolved.instance_id


def resolve_operational_model_from_metadata(
    models: Sequence[LocalModelMetadata | dict[str, Any] | str],
    requested_model: str = "qwen2.5-7b-instruct",
    target_instance_id: str | None = None,
    configured_context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    required_quantization: str | None = None,
    allow_fallback_quantization: bool = False,
) -> ResolvedOperationalModel:
    """
    Risolve il modello operativo richiesto identificando la specifica canonica (OperationalModelSpec).
    Garantisce assenza totale di cross-family routing (es. DeepSeek vs Qwen vs Llama).
    Traccia esplicitamente il contesto richiesto, configurato e confermato dal backend.
    Se target_instance_id è specificato, seleziona esclusivamente quell'istanza (senza fallback silente).
    Se target_instance_id è omesso, applica selezione deterministica e segnala ambiguità se più istanze sono compatibili.
    Separa rigorosamente quantizzazione richiesta, disponibile e selezionata.
    Quando required_quantization è specificata e allow_fallback_quantization=False, soltanto la quantizzazione
    richiesta è ammissibile (nessun fallback non autorizzato a Q8_0 o altre quantizzazioni).
    """
    req_clean = (requested_model or "").strip().lower()

    # Identificazione della specifica canonica corretta
    target_spec: OperationalModelSpec
    if not req_clean or req_clean == DEFAULT_OPERATIONAL_MODEL:
        target_spec = OPERATIONAL_SPECS["qwen2.5-7b-instruct"]
    elif "deepseek" in req_clean:
        target_spec = OPERATIONAL_SPECS["deepseek-r1-distill-qwen-7b"]
    elif "llama" in req_clean:
        target_spec = OPERATIONAL_SPECS["meta-llama-3.1-8b-instruct"]
    elif "qwen" in req_clean:
        if "3" in req_clean and "2.5" not in req_clean:
            # Qwen3 non è Qwen2.5: specifica ad-hoc per prevenire falsi match
            target_spec = OperationalModelSpec(
                canonical_id="qwen3-7b-instruct",
                family="QWEN3",
                display_name="Qwen3-7B-Instruct",
                required_tokens=("qwen", "3", "7b"),
                forbidden_tokens=("deepseek", "2.5"),
                parameter_size="7b",
                preferred_quantization="q4_k_m",
            )
        else:
            target_spec = OPERATIONAL_SPECS["qwen2.5-7b-instruct"]
    elif req_clean in OPERATIONAL_SPECS:
        target_spec = OPERATIONAL_SPECS[req_clean]
    else:
        # Modello generico esplicito
        target_spec = OperationalModelSpec(
            canonical_id=req_clean,
            family="CUSTOM",
            display_name=get_model_display_name(req_clean),
            required_tokens=tuple(req_clean.replace("_", "-").split("-")),
            forbidden_tokens=(),
            parameter_size="",
            preferred_quantization="q4_k_m",
        )

    norm_models = [_normalize_model_item(m) for m in models]

    def matches_spec(m: LocalModelMetadata) -> bool:
        txt = f"{m.key} {m.display_name}".lower().replace("_", "-").replace(".", "")
        for tok in target_spec.required_tokens:
            tok_norm = tok.replace(".", "")
            if tok_norm not in txt:
                return False
        for tok in target_spec.forbidden_tokens:
            tok_norm = tok.replace(".", "")
            if tok_norm in txt:
                return False
        if target_spec.parameter_size and target_spec.parameter_size != "custom":
            for other_size in ("14b", "32b", "72b", "05b", "15b", "3b"):
                if other_size != target_spec.parameter_size and other_size in txt:
                    return False
        return True

    candidates = [m for m in norm_models if matches_spec(m)]

    # CASO A: target_instance_id ESPLICITO (nessun fallback silenzioso consentito)
    if target_instance_id and target_instance_id.strip():
        req_inst_clean = target_instance_id.strip().lower()
        found_model: LocalModelMetadata | None = None
        found_inst: dict[str, Any] | None = None

        # Ricerca in tutti i modelli noti per verificare se appartiene a un altro modello
        for m in norm_models:
            for inst in m.loaded_instances:
                iid = str(inst.get("id") or "").strip().lower() if isinstance(inst, dict) else str(inst).strip().lower()
                if iid == req_inst_clean:
                    found_model = m
                    found_inst = inst if isinstance(inst, dict) else {"id": str(inst)}
                    break
            if found_model:
                break

        if found_model is not None and found_inst is not None:
            # 1. Verifica se il modello che ospita l'istanza soddisfa target_spec
            if not matches_spec(found_model):
                raise AiModelMismatchError(
                    f"L'istanza richiesta '{target_instance_id}' appartiene al modello '{found_model.key}' "
                    f"({found_model.display_name}), non compatibile con il modello richiesto '{target_spec.display_name}'.",
                    requested_model=target_spec.canonical_id,
                    returned_model=found_model.key,
                )

            # 2. Verifica vincolo rigoroso sulla quantizzazione dell'istanza target
            req_q_clean = required_quantization.strip().upper() if required_quantization else None
            eff_req_q = req_q_clean or (target_spec.preferred_quantization or "Q4_K_M").strip().upper()
            found_q = (found_model.quantization or "").strip().upper()
            if req_q_clean and not allow_fallback_quantization:
                if found_q != req_q_clean:
                    raise AiModelMismatchError(
                        f"L'istanza richiesta '{target_instance_id}' appartiene al modello '{found_model.key}' "
                        f"con quantizzazione '{found_model.quantization or 'UNKNOWN'}', non compatibile con la "
                        f"quantizzazione obbligatoria '{req_q_clean}'.",
                        requested_model=f"{target_spec.canonical_id}:{req_q_clean}",
                        returned_model=f"{found_model.key}:{found_model.quantization or 'UNKNOWN'}",
                    )

            # L'istanza appartiene a un modello conforme
            inst_ctx = extract_instance_context_length(found_inst)
            if inst_ctx is None:
                inst_ctx = found_model.loaded_instance_context_length
            ctx_source = "LOADED_INSTANCE_METADATA" if inst_ctx is not None else "UNKNOWN"
            return ResolvedOperationalModel(
                requested_canonical_id=target_spec.canonical_id,
                model_key=found_model.key,
                is_loaded=True,
                instance_id=str(found_inst.get("id") or target_instance_id),
                api_identifier=str(found_inst.get("id") or target_instance_id),
                display_name=target_spec.display_name,
                load_mode="already_loaded",
                quantization=found_model.quantization,
                selected_quantization=found_model.quantization,
                available_quantization=found_model.quantization,
                requested_quantization=eff_req_q,
                quantization_evidence_source=found_model.quantization_evidence_source,
                context_length=inst_ctx,
                canonical_spec=target_spec,
                requested_context_length=configured_context_length,
                configured_context_length=configured_context_length,
                loaded_instance_context_length=inst_ctx,
                model_max_context_length=found_model.max_context_length,
                context_evidence_source=ctx_source,
            )
        else:
            # L'istanza target_instance_id non esiste in nessun modello caricato
            raise AiModelNotInstalledError(
                f"L'istanza richiesta '{target_instance_id}' non è stata trovata tra le istanze caricate per il modello {target_spec.display_name}."
            )

    # CASO B: NESSUN target_instance_id SPECIFICATO
    if not candidates:
        raise AiModelNotInstalledError(f"Il modello {target_spec.display_name} non è installato localmente in LM Studio.")

    # Valutazione quantizzazione preferita/richiesta
    req_q_clean = required_quantization.strip().upper() if required_quantization else None
    pref_q = req_q_clean or (target_spec.preferred_quantization or "Q4_K_M").strip().upper()

    def _matches_q(c: LocalModelMetadata, q_name: str) -> bool:
        if c.quantization:
            return c.quantization.strip().upper() == q_name.strip().upper()
        if q_name.lower() in c.key.lower().replace("-", "_"):
            return True
        return False

    q_candidates = [c for c in candidates if _matches_q(c, pref_q)]
    if q_candidates:
        active_candidates = q_candidates
    elif req_q_clean and not allow_fallback_quantization:
        # Quando required_quantization è specificata e allow_fallback_quantization=False:
        # soltanto la quantizzazione richiesta è ammissibile. Nessun fallback a Q8_0 o altro.
        available_quants = [c.quantization or "UNKNOWN" for c in candidates]
        raise AiModelNotInstalledError(
            f"Nessuna variante di {target_spec.display_name} trovata con la quantizzazione richiesta '{req_q_clean}'. "
            f"Quantizzazioni disponibili: {', '.join(available_quants)}. "
            f"Fallback disabilitato (allow_fallback_quantization=False)."
        )
    elif allow_fallback_quantization or not req_q_clean:
        # Fallback esplicito autorizzato oppure selezione ordinaria senza vincolo rigido
        active_candidates = candidates
    else:
        raise AiModelNotInstalledError(
            f"Nessuna variante di {target_spec.display_name} trovata con la quantizzazione richiesta '{pref_q}'."
        )

    # Raccolta istanze caricate nei candidati attivi
    loaded_pairs: list[tuple[LocalModelMetadata, dict[str, Any]]] = []
    for c in active_candidates:
        if c.loaded_instances:
            for inst in c.loaded_instances:
                if isinstance(inst, dict):
                    loaded_pairs.append((c, inst))
                else:
                    loaded_pairs.append((c, {"id": str(inst)}))
        elif c.loaded_instance_context_length is not None:
            loaded_pairs.append((c, {"id": c.key, "config": {"context_length": c.loaded_instance_context_length}}))

    if len(loaded_pairs) == 1:
        sel_c, sel_inst = loaded_pairs[0]
        inst_id = str(sel_inst.get("id") or sel_c.key)
        inst_ctx = extract_instance_context_length(sel_inst)
        if inst_ctx is None:
            inst_ctx = sel_c.loaded_instance_context_length
        ctx_src = "LOADED_INSTANCE_METADATA" if inst_ctx is not None else "UNKNOWN"
        return ResolvedOperationalModel(
            requested_canonical_id=target_spec.canonical_id,
            model_key=sel_c.key,
            is_loaded=True,
            instance_id=inst_id,
            api_identifier=inst_id,
            display_name=target_spec.display_name,
            load_mode="already_loaded",
            quantization=sel_c.quantization,
            selected_quantization=sel_c.quantization,
            available_quantization=sel_c.quantization,
            requested_quantization=pref_q,
            quantization_evidence_source=sel_c.quantization_evidence_source,
            context_length=inst_ctx,
            canonical_spec=target_spec,
            requested_context_length=configured_context_length,
            configured_context_length=configured_context_length,
            loaded_instance_context_length=inst_ctx,
            model_max_context_length=sel_c.max_context_length,
            context_evidence_source=ctx_src,
        )
    elif len(loaded_pairs) > 1:
        # Più istanze caricate: verifichiamo se una sola soddisfa configured_context_length
        ctx_matches = [
            (c, inst) for (c, inst) in loaded_pairs
            if extract_instance_context_length(inst) == configured_context_length
        ]
        if len(ctx_matches) == 1:
            sel_c, sel_inst = ctx_matches[0]
            inst_id = str(sel_inst.get("id") or sel_c.key)
            inst_ctx = extract_instance_context_length(sel_inst)
            ctx_src = "LOADED_INSTANCE_METADATA" if inst_ctx is not None else "UNKNOWN"
            return ResolvedOperationalModel(
                requested_canonical_id=target_spec.canonical_id,
                model_key=sel_c.key,
                is_loaded=True,
                instance_id=inst_id,
                api_identifier=inst_id,
                display_name=target_spec.display_name,
                load_mode="already_loaded",
                quantization=sel_c.quantization,
                selected_quantization=sel_c.quantization,
                available_quantization=sel_c.quantization,
                requested_quantization=pref_q,
                quantization_evidence_source=sel_c.quantization_evidence_source,
                context_length=inst_ctx,
                canonical_spec=target_spec,
                requested_context_length=configured_context_length,
                configured_context_length=configured_context_length,
                loaded_instance_context_length=inst_ctx,
                model_max_context_length=sel_c.max_context_length,
                context_evidence_source=ctx_src,
            )
        elif len(ctx_matches) > 1:
            raise AiModelAmbiguousError(
                f"Rilevate istanze multiple caricate compatibili per {target_spec.display_name} "
                f"({', '.join(str(inst.get('id', '')) for _, inst in ctx_matches)}). "
                f"Specificare 'target_instance_id' per selezionare l'istanza in modo univoco."
            )
        else:
            raise AiModelAmbiguousError(
                f"Rilevate istanze multiple caricate per {target_spec.display_name} "
                f"({', '.join(str(inst.get('id', '')) for _, inst in loaded_pairs)}), "
                f"ma nessuna con context length {configured_context_length}. Specificare 'target_instance_id'."
            )

    # Nessuna istanza caricata in active_candidates
    if len(active_candidates) == 1:
        c = active_candidates[0]
        return ResolvedOperationalModel(
            requested_canonical_id=target_spec.canonical_id,
            model_key=c.key,
            is_loaded=False,
            instance_id=None,
            api_identifier=c.key,
            display_name=target_spec.display_name,
            load_mode="unloaded",
            quantization=c.quantization,
            selected_quantization=c.quantization,
            available_quantization=c.quantization,
            requested_quantization=pref_q,
            quantization_evidence_source=c.quantization_evidence_source,
            context_length=None,
            canonical_spec=target_spec,
            requested_context_length=configured_context_length,
            configured_context_length=configured_context_length,
            loaded_instance_context_length=None,
            model_max_context_length=c.max_context_length,
            context_evidence_source="NOT_LOADED",
        )

    # Disambiguazione per nome canonico esatto se vi sono più modelli unloaded
    exact_c = [c for c in active_candidates if c.key.lower() == target_spec.canonical_id.lower()]
    if len(exact_c) == 1:
        c = exact_c[0]
        return ResolvedOperationalModel(
            requested_canonical_id=target_spec.canonical_id,
            model_key=c.key,
            is_loaded=False,
            instance_id=None,
            api_identifier=c.key,
            display_name=target_spec.display_name,
            load_mode="unloaded",
            quantization=c.quantization,
            selected_quantization=c.quantization,
            available_quantization=c.quantization,
            requested_quantization=pref_q,
            quantization_evidence_source=c.quantization_evidence_source,
            context_length=None,
            canonical_spec=target_spec,
            requested_context_length=configured_context_length,
            configured_context_length=configured_context_length,
            loaded_instance_context_length=None,
            model_max_context_length=c.max_context_length,
            context_evidence_source="NOT_LOADED",
        )

    raise AiModelAmbiguousError(
        f"Rilevateplici versioni compatibili di {target_spec.display_name} con quantizzazione {pref_q} "
        f"({', '.join(c.key for c in active_candidates)}). Selezionare esplicitamente il modello da utilizzare."
    )


def classify_lmstudio_http_error(
    http_code: int,
    reason: Any,
    raw_body_bytes: bytes,
    base_url: str = "http://127.0.0.1:1234",
    model_id: str | None = None,
    max_tokens: int | None = None,
    prompt_tokens: int | None = None,
) -> AiBackendRequestError:
    """
    Classifica in modo strutturato e sicuro per la privacy forense gli errori HTTP di LM Studio.
    Non espone mai nei messaggi di errore o nei log estratti di conversazione o dati personali.
    """
    body_text = ""
    error_msg = ""
    request_id = None
    try:
        body_text = raw_body_bytes.decode("utf-8", errors="replace")
        parsed = json.loads(body_text)
        if isinstance(parsed, dict):
            err_obj = parsed.get("error")
            if isinstance(err_obj, dict):
                error_msg = str(err_obj.get("message") or "")
                request_id = err_obj.get("request_id") or err_obj.get("id")
            elif isinstance(err_obj, str):
                error_msg = err_obj
            elif "message" in parsed:
                error_msg = str(parsed["message"])
            if not request_id and "id" in parsed:
                request_id = str(parsed["id"])
    except Exception:
        error_msg = body_text[:200]

    lower_msg = (error_msg or body_text).lower()

    if http_code == 400:
        if any(k in lower_msg for k in ("context", "n_ctx", "maximum context length", "too many tokens", "exceed", "window", "context length")):
            cat = "CONTEXT_OVERFLOW"
            safe_reason = "LM Studio ha rifiutato la richiesta perché il prompt e il budget di risposta superano la finestra di contesto disponibile."
        elif any(k in lower_msg for k in ("response_format", "json_schema", "schema", "grammar", "structured output")):
            cat = "UNSUPPORTED_STRUCTURED_OUTPUT"
            safe_reason = "Il backend non accetta il formato JSON strutturato richiesto."
        elif any(k in lower_msg for k in ("model not loaded", "no model loaded", "model not found", "not loaded")):
            cat = "MODEL_NOT_LOADED"
            safe_reason = "Il modello specificato non risulta attualmente caricato in LM Studio."
        elif any(k in lower_msg for k in ("invalid parameter", "unknown field", "parameter", "temperature", "max_tokens", "seed", "out of range")):
            cat = "INVALID_PARAMETER"
            safe_reason = "LM Studio ha rifiutato un parametro della richiesta."
        elif any(k in lower_msg for k in ("cuda", "vram", "out of memory", "llama_decode", "memory", "metal")):
            cat = "INFERENCE_BACKEND_ERROR"
            safe_reason = "Errore del backend di inferenza locale o memoria GPU insufficiente."
        else:
            cat = "UNCLASSIFIED_HTTP_ERROR"
            safe_reason = "LM Studio ha restituito HTTP 400. Non è stato possibile determinare la causa specifica."
    elif http_code == 429:
        cat = "RATE_LIMIT_EXCEEDED"
        safe_reason = "LM Studio ha restituito HTTP 429: troppe richieste simultanee o server occupato."
    elif http_code in (500, 502, 503, 504):
        cat = "SERVER_ERROR"
        safe_reason = f"Errore interno del server LM Studio (HTTP {http_code})."
    else:
        cat = "HTTP_ERROR"
        safe_reason = f"Errore HTTP {http_code} restituito da LM Studio: {reason or 'Unknown'}"

    clean_technical = f"HTTP {http_code} [{cat}]"
    if request_id:
        clean_technical += f" (req_id: {request_id})"

    full_message = f"Errore HTTP {http_code} da LM Studio: {safe_reason} (Dettaglio: {clean_technical})"

    return AiBackendRequestError(
        message=full_message,
        status_code=http_code,
        error_category=cat,
        safe_reason=safe_reason,
        technical_details=clean_technical,
        model_id=model_id,
        request_id=str(request_id) if request_id else None,
        prompt_tokens_estimate=prompt_tokens,
        max_tokens_requested=max_tokens,
    )


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
        request_listener: Any = None,
    ) -> None:
        _validate_loopback_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        # Token letto opzionalmente da parametro o da ambiente
        self._api_token = api_token or os.environ.get("LM_STUDIO_API_TOKEN")
        self.default_timeout_seconds = default_timeout_seconds
        self.debug_preserve_raw = debug_preserve_raw
        self.request_listener = request_listener
        self._last_loaded_model: str | None = None
        self._last_loaded_model_key: str | None = None
        self._loaded_instance_id: str | None = None
        self.load_mode: str = "unloaded"
        # Registry istanziabile di proprietà del client per isolamento lifecycle
        self._model_bindings: dict[str, ModelIdentityBinding] = {}
        self._active_binding: ModelIdentityBinding | None = None

    def register_binding(self, binding: ModelIdentityBinding) -> None:
        """Registra una relazione esplicita tra identificatori di modello e istanza esclusivamente per questo client."""
        if binding.loaded_instance_id:
            self._model_bindings[binding.loaded_instance_id.strip().lower()] = binding
        if binding.api_identifier:
            self._model_bindings[binding.api_identifier.strip().lower()] = binding
        self._model_bindings[binding.resolved_model_key.strip().lower()] = binding
        self._active_binding = binding

    def get_active_binding(self) -> ModelIdentityBinding | None:
        """
        Restituisce il binding attivo di proprietà esclusiva di questa istanza client.
        Ritorna None se nessun modello è attualmente associato o caricato su questo client.
        Nessun accesso a registri globali condivisi.
        """
        return self._active_binding

    def get_binding(self, identifier: str | None = None) -> ModelIdentityBinding | None:
        """Recupera il binding registrato di proprietà esclusiva di questo client. Nessun fallback globale."""
        if not identifier:
            return self._active_binding
        return self._model_bindings.get(identifier.strip().lower())

    def invalidate_binding(self, identifier: str | None = None) -> None:
        """Invalida i binding del modello specificato per questo client senza influenzare altri client."""
        if identifier:
            ident_clean = identifier.strip().lower()
            binding = self._model_bindings.pop(ident_clean, None)
            if binding is not None:
                for k in list(self._model_bindings.keys()):
                    if self._model_bindings[k] is binding:
                        del self._model_bindings[k]
                if self._active_binding is binding:
                    self._active_binding = None
        else:
            self._model_bindings.clear()
            self._active_binding = None

    def is_equivalent_model(self, target_model: str, response_model: str) -> bool:
        """Verifica se response_model è equivalente a target_model nel contesto locale di questo client."""
        return is_equivalent_operational_model(target_model, response_model, client=self)

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

        # Fallback su /v1/models per compatibilità OpenAI
        url_v1 = f"{self.base_url}/v1/models"
        req_v1 = urllib.request.Request(url_v1, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req_v1, timeout=self.default_timeout_seconds) as resp_v1:
                raw_bytes_v1 = resp_v1.read()
        except urllib.error.HTTPError as e:
            raise AiBackendRequestError(f"Errore HTTP {e.code} interrogando {url_v1}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(f"Timeout durante richiesta a {url_v1}: {e}") from e
            raise LmStudioUnavailableError(f"Impossibile contattare {url_v1}: {e}") from e

        try:
            data_v1 = json.loads(raw_bytes_v1.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise AiBackendProtocolError(f"JSON malformato ricevuto da {url_v1}: {e}") from e

        if isinstance(data_v1, dict) and "data" in data_v1 and isinstance(data_v1["data"], list):
            return data_v1["data"]
        raise AiBackendProtocolError(f"Risposta da {url_v1} non conforme al protocollo atteso ('data' mancante o non lista).")

    def get_native_models(self) -> list[LocalModelMetadata]:
        """
        Recupera i modelli locali tramite l'API REST nativa di LM Studio GET /api/v1/models.
        Se l'endpoint non è esposto (404/405/501), esegue fallback su GET /v1/models.
        """
        _validate_loopback_url(self.base_url)
        url_native = f"{self.base_url}/api/v1/models"
        req = urllib.request.Request(url_native, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode("utf-8"))
            raw_list = data.get("data") or data.get("models") or (data if isinstance(data, list) else [])
            models: list[LocalModelMetadata] = []
            for item in raw_list:
                if isinstance(item, str):
                    models.append(LocalModelMetadata(key=item, display_name=item))
                elif isinstance(item, dict):
                    models.append(_normalize_model_item(item))
            return models
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise AiBackendProtocolError(f"Risposta non valida (JSON malformato) da {url_native}: {e}") from e
        except urllib.error.HTTPError as e:
            if e.code in (404, 405, 501):
                return self._fallback_openai_models()
            raise AiBackendRequestError(f"Errore HTTP {e.code} da {url_native}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(f"Timeout durante richiesta a {url_native}: {e}") from e
            raise LmStudioUnavailableError(f"LM Studio non raggiungibile su {self.base_url}: {e}") from e

    def _fallback_openai_models(self) -> list[LocalModelMetadata]:
        """Recupera la lista modelli tramite GET /v1/models come fallback."""
        url_v1 = f"{self.base_url}/v1/models"
        req = urllib.request.Request(url_v1, headers=self._get_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.default_timeout_seconds) as resp:
                raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode("utf-8"))
            raw_list = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            models: list[LocalModelMetadata] = []
            for item in raw_list:
                if isinstance(item, dict):
                    mid = str(item.get("id") or item.get("name") or "")
                    if mid:
                        models.append(_normalize_model_item(item))
            return models
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise AiBackendProtocolError(f"Risposta non valida (JSON malformato) da {url_v1}: {e}") from e
        except urllib.error.HTTPError as e:
            raise AiBackendRequestError(f"Errore HTTP {e.code} da {url_v1}: {e.reason}") from e
        except _NETWORK_ERRORS as e:
            if _is_timeout_error(e):
                raise AiBackendTimeoutError(f"Timeout durante richiesta a {url_v1}: {e}") from e
            raise LmStudioUnavailableError(f"LM Studio non raggiungibile su {self.base_url}: {e}") from e

    def is_model_loaded(self, model_id: str) -> bool:
        """
        Verifica se il modello specificato è attualmente caricato in memoria in LM Studio.
        Non ingoia errori di rete, protocollo o indisponibilità (propagano secondo semantica).
        """
        native = self.get_native_models()

        m_clean = model_id.strip().lower()
        for nm in native:
            if nm.key.lower() == m_clean or nm.display_name.lower() == m_clean:
                if len(nm.loaded_instances) > 0:
                    return True
            for inst in nm.loaded_instances:
                inst_id = str(inst.get("id") or "").lower()
                if inst_id and (inst_id == m_clean or inst_id == m_clean.replace("_", "-")):
                    return True

        if getattr(self, "_last_loaded_model", None) is not None:
            last = self._last_loaded_model.lower()
            last_key = getattr(self, "_last_loaded_model_key", "").lower()
            if m_clean == last or m_clean == last_key:
                return True

        active_binding = getattr(self, "_active_binding", None)
        if active_binding is not None and active_binding.is_verified:
            if m_clean == active_binding.resolved_model_key.lower():
                return True
            if active_binding.loaded_instance_id and m_clean == active_binding.loaded_instance_id.lower():
                return True

        return False

    def load_model(
        self,
        model_id: str,
        context_length: int = 8192,
        timeout_seconds: float = 240.0,
        required_quantization: str | None = None,
        allow_fallback_quantization: bool = False,
        **kwargs: Any,
    ) -> ModelLoadResult:
        """
        Carica un modello in memoria tramite l'API REST nativa di LM Studio (POST /api/v1/models/load).
        Invia ESCLUSIVAMENTE il payload minimo supportato dallo schema REST di LM Studio:
        {
            "model": "<MODEL_KEY>",
            "context_length": 8192,
            "echo_load_config": true
        }
        Verifica rigorosamente il payload di risposta HTTP 200:
        - HTTP 200 con status "failed" o campo "error" solleva AiModelLoadError (mai spacciato per loaded).
        - HTTP 200 con JSON malformato o risposta vuota solleva AiBackendProtocolError.
        - HTTP 404/405/501 restituisce ModelLoadResult con status=JIT_UNVERIFIED e non imposta _last_loaded_model.
        - _last_loaded_model viene aggiornato solo ed esclusivamente a fronte di un caricamento LOADED convalidato.
        """
        _validate_loopback_url(self.base_url)
        url = f"{self.base_url}/api/v1/models/load"
        payload = {
            "model": model_id,
            "context_length": context_length,
            "echo_load_config": True,
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=self._get_headers(), method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                raw_bytes = resp.read()
                if not raw_bytes or not raw_bytes.strip():
                    raise AiBackendProtocolError(f"Risposta vuota da {url} (HTTP 200 ma 0 byte).")
                try:
                    res = json.loads(raw_bytes.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as err:
                    raise AiBackendProtocolError(f"Risposta non valida (JSON malformato) da {url}: {err}") from err

                if not isinstance(res, dict):
                    raise AiBackendProtocolError(f"Risposta da {url} non conforme: atteso oggetto JSON, ricevuto {type(res).__name__}.")

                # 1. Controllo esplicito su fallimento dichiarato nel body HTTP 200 (es. {"status": "failed", "error": "Not enough VRAM"})
                resp_status = str(res.get("status") or "").strip().lower()
                resp_err = res.get("error") or res.get("error_message") or res.get("message")
                if resp_status in ("failed", "error") or resp_err:
                    err_msg = str(resp_err or "Stato caricamento fallito ('failed') riportato da LM Studio.")
                    raise AiModelLoadError(
                        f"Caricamento del modello '{model_id}' fallito: {err_msg}",
                        status_code=200,
                        technical_details=json.dumps(res),
                    )

                # 2. Controllo stati di caricamento in corso (in-progress / still_loading)
                if resp_status in ("loading", "still_loading", "pending", "in_progress"):
                    return ModelLoadResult(
                        status=ModelLifecycleStatus.LOADING,
                        model_key=model_id,
                        instance_id=None,
                        api_identifier=model_id,
                        load_mode="loading",
                        context_length=context_length,
                        raw_response=res,
                        error_message=f"Modello '{model_id}' ancora in fase di caricamento (status='{resp_status}').",
                    )

                # 3. Controllo conformità base del payload (rifiuta schemi sconosciuti / arbitrari)
                known_success_statuses = ("loaded", "already_loaded", "success", "ok")
                known_loading_statuses = ("loading", "still_loading", "pending", "in_progress")
                known_failure_statuses = ("failed", "error")

                if (
                    resp_status
                    and resp_status not in known_success_statuses
                    and resp_status not in known_loading_statuses
                    and resp_status not in known_failure_statuses
                ):
                    raise AiBackendProtocolError(
                        f"Risposta da {url} non conforme al contratto di LM Studio: "
                        f"status sconosciuto o non riconosciuto '{resp_status}' (payload={res})."
                    )

                is_known_status = resp_status in known_success_statuses
                instance_id_raw = res.get("instance_id") or res.get("id") or None
                instance_id = str(instance_id_raw).strip() if instance_id_raw and str(instance_id_raw).strip() else None
                model_returned = res.get("model") or res.get("model_key") or None

                if not is_known_status and not instance_id:
                    # Risposte ambigue, incomplete o sconosciute (es. {"arbitrary": true}, {"status": "unknown"}, {})
                    raise AiBackendProtocolError(
                        f"Risposta da {url} non conforme al contratto di LM Studio: "
                        f"schema privo di status valido o instance_id riconosciuto (status='{resp_status}', payload={res})."
                    )

                # 4. Estrazione context length dalla risposta del loader
                resp_ctx = extract_instance_context_length(res)

                # 5. Validazione positiva dell'identità del modello
                # A. Se il server dichiara esplicitamente un modello (campo 'model' o 'model_key')
                if model_returned is not None:
                    model_returned_str = str(model_returned).strip()
                    if not self.is_equivalent_model(model_id, model_returned_str):
                        raise AiModelMismatchError(
                            f"Caricamento fallito per mismatch di modello: richiesto '{model_id}', "
                            f"ma la risposta di LM Studio dichiara '{model_returned_str}'.",
                            requested_model=model_id,
                            returned_model=model_returned_str,
                        )

                # B. Se è presente un instance_id, verifica che non sia ambiguo o in conflitto con la famiglia richiesta
                if instance_id is not None:
                    inst_str = str(instance_id).strip()
                    if is_conflicting_model_or_instance(model_id, inst_str):
                        raise AiModelMismatchError(
                            f"Instance ID ambiguo o in conflitto '{inst_str}' per il modello richiesto '{model_id}'.",
                            requested_model=model_id,
                            returned_model=inst_str,
                        )

                # C. Risoluzione dell'identità secondo il contratto ufficiale LM Studio:
                # La risposta ufficiale di POST /api/v1/models/load contiene: {status, instance_id, load_config}.
                # Non contiene obbligatoriamente model/model_key.
                is_identity_verified = False
                effective_model_key = str(model_returned or model_id)
                found_owner: LocalModelMetadata | None = None
                found_inst_item: dict[str, Any] | None = None

                if model_returned is not None:
                    # Model key esplicitamente confermato dal payload
                    is_identity_verified = True
                elif instance_id is not None:
                    # Caso 1: L'instance_id coincide o è direttamente equivalente al modello richiesto
                    if self.is_equivalent_model(model_id, instance_id):
                        is_identity_verified = True
                        effective_model_key = model_id
                    else:
                        # Caso 2: Instance ID opaco (es. "instance-qwen-001" o uuid).
                        # Verifichiamo interrogando GET /api/v1/models per associare l'instance_id al modello installato
                        try:
                            native_models = self.get_native_models()

                            for nm in native_models:
                                for inst_obj in nm.loaded_instances:
                                    iid = str(inst_obj.get("id") or "").strip().lower()
                                    if iid and iid == instance_id.lower():
                                        found_owner = nm
                                        found_inst_item = inst_obj
                                        break
                                if found_owner is not None:
                                    break

                            if found_owner is not None:
                                eff_req_q = required_quantization or kwargs.get("required_quantization")
                                eff_allow_fb = allow_fallback_quantization or kwargs.get("allow_fallback_quantization", False)
                                if eff_req_q and not eff_allow_fb:
                                    owner_q = (found_owner.quantization or "").strip().upper()
                                    if owner_q != eff_req_q.strip().upper():
                                        raise AiModelMismatchError(
                                            f"Caricamento fallito per mismatch: l'instance_id '{instance_id}' "
                                            f"appartiene al modello '{found_owner.key}' con quantizzazione "
                                            f"'{found_owner.quantization or 'UNKNOWN'}', non compatibile con "
                                            f"la quantizzazione richiesta '{eff_req_q}'.",
                                            requested_model=f"{model_id}:{eff_req_q}",
                                            returned_model=f"{found_owner.key}:{found_owner.quantization or 'UNKNOWN'}",
                                        )
                                if self.is_equivalent_model(model_id, found_owner.key):
                                    is_identity_verified = True
                                    effective_model_key = found_owner.key
                                    if resp_ctx is None and found_inst_item is not None:
                                        resp_ctx = extract_instance_context_length(found_inst_item)
                                elif is_conflicting_model_or_instance(model_id, found_owner.key):
                                    raise AiModelMismatchError(
                                        f"Caricamento fallito per mismatch: l'instance_id '{instance_id}' "
                                        f"appartiene al modello '{found_owner.key}', incompatibile con '{model_id}'.",
                                        requested_model=model_id,
                                        returned_model=found_owner.key,
                                    )
                                else:
                                    raise AiModelMismatchError(
                                        f"Caricamento fallito per mismatch: l'instance_id '{instance_id}' "
                                        f"appartiene al modello '{found_owner.key}', non equivalente a '{model_id}'.",
                                        requested_model=model_id,
                                        returned_model=found_owner.key,
                                    )
                        except (AiModelMismatchError, AiModelNotInstalledError):
                            raise
                        except Exception:
                            # Se la query GET fallisce o l'endpoint non è raggiungibile,
                            # non possiamo certificare l'identità dell'instance_id opaco
                            pass

                # D. Se l'identità non può essere certificata:
                # Rimane JIT_UNVERIFIED e _last_loaded_model NON viene aggiornato prematuramente.
                if not is_identity_verified:
                    provisional_binding = ModelIdentityBinding(
                        requested_canonical_id=model_id,
                        resolved_model_key=model_id,
                        loaded_instance_id=str(instance_id) if instance_id else None,
                        api_identifier=str(instance_id or model_id),
                        load_mode="jit_unverified",
                        status=ModelLifecycleStatus.JIT_UNVERIFIED,
                        is_verified=False,
                        configured_context_length=context_length,
                        loaded_instance_context_length=resp_ctx,
                        context_evidence_source="UNVERIFIED",
                    )
                    self.register_binding(provisional_binding)
                    self._active_binding = provisional_binding
                    self._loaded_instance_id = str(instance_id) if instance_id else None
                    self.load_mode = "jit_unverified"
                    return ModelLoadResult(
                        status=ModelLifecycleStatus.JIT_UNVERIFIED,
                        model_key=model_id,
                        instance_id=str(instance_id) if instance_id else None,
                        api_identifier=str(instance_id or model_id),
                        load_mode="jit_unverified",
                        context_length=resp_ctx,
                        loaded_instance_context_length=resp_ctx,
                        context_evidence_source="UNVERIFIED",
                        raw_response=res,
                        error_message="Risposta priva di conferma documentata del modello; binding da verificare al primo completamento.",
                    )

                # 6. Caricamento positivamente confermato con identità certificata
                eff_status = ModelLifecycleStatus.ALREADY_LOADED if resp_status == "already_loaded" else ModelLifecycleStatus.LOADED
                eff_ctx_source = "LOADER_API_RESPONSE" if resp_ctx is not None else "UNCONFIRMED"

                quant_val = kwargs.get("quantization")
                quant_src = kwargs.get("quantization_evidence_source", "UNKNOWN")
                if found_owner is not None and found_owner.quantization:
                    quant_val = found_owner.quantization
                    quant_src = found_owner.quantization_evidence_source

                verified_binding = ModelIdentityBinding(
                    requested_canonical_id=model_id,
                    resolved_model_key=effective_model_key,
                    loaded_instance_id=str(instance_id) if instance_id else None,
                    api_identifier=str(instance_id or effective_model_key),
                    load_mode="already_loaded" if eff_status == ModelLifecycleStatus.ALREADY_LOADED else "native_loaded",
                    status=eff_status,
                    is_verified=True,
                    configured_context_length=context_length,
                    loaded_instance_context_length=resp_ctx,
                    context_evidence_source=eff_ctx_source,
                    quantization=quant_val,
                    quantization_evidence_source=quant_src,
                )
                self.register_binding(verified_binding)
                self._active_binding = verified_binding
                self.load_mode = verified_binding.load_mode
                self._last_loaded_model = str(instance_id or effective_model_key)
                self._last_loaded_model_key = effective_model_key
                self._loaded_instance_id = str(instance_id) if instance_id else None
                return ModelLoadResult(
                    status=eff_status,
                    model_key=effective_model_key,
                    instance_id=str(instance_id) if instance_id else None,
                    api_identifier=str(instance_id or effective_model_key),
                    load_mode=verified_binding.load_mode,
                    context_length=resp_ctx,
                    loaded_instance_context_length=resp_ctx,
                    context_evidence_source=eff_ctx_source,
                    raw_response=res,
                )
        except urllib.error.HTTPError as e:
            err_body = None
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass

            if e.code == 400:
                raise AiModelLoadError(
                    f"Errore HTTP 400 da LM Studio durante il caricamento di '{model_id}': {err_body or e.reason}",
                    status_code=400,
                    technical_details=err_body,
                ) from e

            if e.code in (404, 405, 501):
                # Fallback controllato JIT se l'endpoint nativo load non è esposto.
                # NON impostiamo _last_loaded_model come caricato: lo stato è JIT_UNVERIFIED.
                return ModelLoadResult(
                    status=ModelLifecycleStatus.JIT_UNVERIFIED,
                    model_key=model_id,
                    instance_id=None,
                    api_identifier=model_id,
                    load_mode="jit_unverified",
                    context_length=None,
                    loaded_instance_context_length=None,
                    context_evidence_source="NOT_EXPOSED",
                    raw_response={"status": "jit_unverified"},
                )

            raise AiModelLoadError(
                f"Errore HTTP {e.code} da LM Studio durante il caricamento di '{model_id}': {e.reason}. {err_body}".strip(),
                status_code=e.code,
                technical_details=err_body,
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
        Invalida rigorosamente i binding del modello scaricato solo a fronte di conferma positiva.
        """
        _validate_loopback_url(self.base_url)
        url = f"{self.base_url}/api/v1/models/unload"
        payload: dict[str, Any] = {}
        target_to_unload = model_id or self.model_id or (self._active_binding.resolved_model_key if self._active_binding else None)
        if target_to_unload:
            payload["model"] = target_to_unload
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=self._get_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                if resp.status in (200, 204):
                    self.invalidate_binding(target_to_unload)
                    self._last_loaded_model = None
                    self._last_loaded_model_key = None
                    self._loaded_instance_id = None
                    self.load_mode = "unloaded"
                    return True
                return False
        except (urllib.error.HTTPError, *_NETWORK_ERRORS):
            # Fallimento unload: il modello rimane potenzialmente caricato, i binding non vengono invalidati
            return False

    def ensure_model_loaded(
        self,
        model_id: str = "qwen2.5-7b-instruct",
        context_length: int = 8192,
        timeout_seconds: float = 240.0,
        target_instance_id: str | None = None,
        required_quantization: str | None = None,
        allow_fallback_quantization: bool = False,
        **kwargs: Any,
    ) -> str:
        """
        Esegue il pre-flight completo e garantisce la disponibilità operativa del modello locale.
        1. Verifica che LM Studio sia attivo su loopback.
        2. Recupera i modelli tramite GET /api/v1/models (o fallback GET /v1/models).
        3. Risolve deterministicamente il model_key e lo stato di caricamento.
        4. Se già caricato, restituisce l'identificatore attivo.
        5. Se installato ma non caricato, invia POST /api/v1/models/load con schema conforme.
        6. Restituisce il model_id reale da passare all'inferenza.
        """
        if not self.is_available():
            raise LmStudioUnavailableError(
                f"LM Studio non raggiungibile su {self.base_url}. "
                "Verificare che il server locale sia attivo."
            )

        eff_target_inst = target_instance_id or kwargs.get("instance_id")
        req_quant = required_quantization or kwargs.get("required_quantization")
        allow_fb = allow_fallback_quantization or kwargs.get("allow_fallback_quantization", False)
        native_models = self.get_native_models()
        res_meta = resolve_operational_model_from_metadata(
            native_models,
            model_id,
            target_instance_id=eff_target_inst,
            configured_context_length=context_length,
            required_quantization=req_quant,
            allow_fallback_quantization=allow_fb,
        )

        if res_meta.is_loaded:
            active_id = res_meta.instance_id or res_meta.model_key
            self.model_id = active_id
            self._loaded_instance_id = res_meta.instance_id
            self._last_loaded_model = active_id
            self._last_loaded_model_key = res_meta.model_key
            self.load_mode = "already_loaded"
            binding = ModelIdentityBinding(
                requested_canonical_id=model_id,
                resolved_model_key=res_meta.model_key,
                loaded_instance_id=res_meta.instance_id if res_meta.instance_id != res_meta.model_key else None,
                api_identifier=active_id,
                load_mode="already_loaded",
                status=ModelLifecycleStatus.ALREADY_LOADED,
                is_verified=True,
                configured_context_length=context_length,
                loaded_instance_context_length=res_meta.loaded_instance_context_length,
                context_evidence_source=res_meta.context_evidence_source,
                quantization=res_meta.quantization,
                quantization_evidence_source=res_meta.quantization_evidence_source,
            )
            self.register_binding(binding)
            return active_id

        load_result = self.load_model(
            model_id=res_meta.model_key,
            context_length=context_length,
            timeout_seconds=timeout_seconds,
            required_quantization=req_quant,
            allow_fallback_quantization=allow_fb,
            quantization=res_meta.quantization,
            quantization_evidence_source=res_meta.quantization_evidence_source,
        )

        if load_result.status in (ModelLifecycleStatus.LOADED, ModelLifecycleStatus.ALREADY_LOADED):
            active_id = load_result.instance_id or load_result.api_identifier or res_meta.model_key
            self.model_id = active_id
            self._loaded_instance_id = load_result.instance_id
            curr_b = self.get_active_binding()
            if curr_b:
                if not curr_b.quantization and res_meta.quantization:
                    curr_b.quantization = res_meta.quantization
                    curr_b.quantization_evidence_source = res_meta.quantization_evidence_source
            return active_id

        elif load_result.status == ModelLifecycleStatus.JIT_UNVERIFIED:
            active_id = load_result.instance_id or load_result.api_identifier or res_meta.model_key
            self.model_id = active_id
            self._loaded_instance_id = load_result.instance_id
            self.load_mode = "jit_unverified"
            return active_id
        elif load_result.status == ModelLifecycleStatus.LOADING:
            raise AiModelLoadError(
                f"Il modello '{res_meta.model_key}' è ancora in fase di caricamento in LM Studio (status=LOADING): {load_result.error_message}"
            )
        else:
            raise AiModelLoadError(
                f"Impossibile verificare il caricamento del modello '{res_meta.model_key}': stato {load_result.status}"
            )

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
        request_listener: Any = None,
    ) -> LlmCompletionResponse:
        """
        Invia una richiesta OpenAI-compatible a POST /v1/chat/completions.
        Richiede un model_id esplicito (passato a chiamata o preimpostato nel client).
        Collega gli eventi di avanzamento al ciclo di vita reale di ogni richiesta HTTP.
        Valida rigorosamente che il campo 'model' provenga dalla risposta del server
        e che la forma della risposta rispetti il protocollo atteso.
        """
        target_model = model_id or self.model_id
        if not target_model:
            raise AiModelNotSpecifiedError(
                "Nessun model_id specificato per la chat completion. "
                "Per garantire l'integrità scientifica dell'esperimento, l'identificativo modello deve essere esplicito."
            )

        listener = request_listener if request_listener is not None else getattr(self, "request_listener", None)

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

        if listener is not None and hasattr(listener, "record_request_sent"):
            try:
                listener.record_request_sent(phase="Esecuzione inferenza HTTP")
            except Exception:
                pass

        start_t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                elapsed = round(time.perf_counter() - start_t, 3)
                raw_bytes = resp.read()
            if listener is not None and hasattr(listener, "record_http_response_received"):
                try:
                    listener.record_http_response_received()
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            raw_err_bytes = b""
            try:
                raw_err_bytes = e.read()
            except Exception:
                pass
            if listener is not None and hasattr(listener, "record_http_error"):
                try:
                    listener.record_http_error(status_code=e.code, message=f"HTTP {e.code}")
                except Exception:
                    pass
            raise classify_lmstudio_http_error(
                http_code=e.code,
                reason=getattr(e, "reason", None),
                raw_body_bytes=raw_err_bytes,
                base_url=self.base_url,
                model_id=target_model,
                max_tokens=max_tokens,
            ) from e
        except _NETWORK_ERRORS as e:
            if listener is not None:
                try:
                    if _is_timeout_error(e) and hasattr(listener, "record_timeout"):
                        listener.record_timeout(message=str(e))
                    elif hasattr(listener, "record_http_error"):
                        listener.record_http_error(status_code=None, message=str(e))
                except Exception:
                    pass
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
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error(f"JSONDecodeError: {e}")
                except Exception:
                    pass
            raise AiBackendProtocolError(f"Risposta non valida da LM Studio (JSON malformato): {e}") from e

        # Validazione Sezione B: top-level response deve essere dict
        if not isinstance(raw_json, dict):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Risposta top-level non dict")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"Risposta top-level da LM Studio non è un dizionario (ricevuto {type(raw_json).__name__})."
            )

        # Validazione Sezione A: model ID deve venire RIGOROSAMENTE dalla risposta del backend
        if "model" not in raw_json:
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo 'model' mancante")
                except Exception:
                    pass
            raise AiBackendProtocolError("Risposta da LM Studio priva del campo obbligatorio 'model'.")
        raw_model = raw_json["model"]
        if not isinstance(raw_model, str) or not raw_model.strip():
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo 'model' non valido o vuoto")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"Campo 'model' nella risposta da LM Studio non valido o vuoto (ricevuto {raw_model!r})."
            )
        response_model = raw_model

        # Validazione positiva identità response.model contro target_model per tutte le completion
        if not self.is_equivalent_model(target_model, response_model):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Response model mismatch")
                except Exception:
                    pass
            raise AiModelMismatchError(
                f"Response model mismatch: modello richiesto '{target_model}', "
                f"ma la risposta proviene dal modello non equivalente '{response_model}'.",
                requested_model=target_model,
                returned_model=response_model,
            )

        # Validazione Sezione B: choices, message, content, finish_reason, usage
        choices = raw_json.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo 'choices' mancante o vuoto")
                except Exception:
                    pass
            raise AiBackendProtocolError("Campo 'choices' mancante, vuoto o non lista nella risposta di LM Studio.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Elemento choices[0] non dict")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"L'elemento choices[0] deve essere un dizionario, ricevuto {type(first_choice).__name__}."
            )

        message_obj = first_choice.get("message")
        if not isinstance(message_obj, dict):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo choices[0]['message'] non dict")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"Campo choices[0]['message'] deve essere un dizionario, ricevuto {type(message_obj).__name__}."
            )

        content = message_obj.get("content")
        if not isinstance(content, str):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo content non stringa")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"Campo choices[0]['message']['content'] deve essere una stringa, ricevuto {type(content).__name__}."
            )

        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            if listener is not None and hasattr(listener, "record_protocol_error"):
                try:
                    listener.record_protocol_error("Campo finish_reason non stringa")
                except Exception:
                    pass
            raise AiBackendProtocolError(
                f"Campo 'finish_reason' deve essere una stringa o None, ricevuto {type(finish_reason).__name__}."
            )

        # Troncamento JSON se schema richiesto e finish_reason='length'
        if schema is not None and finish_reason == "length":
            try:
                json.loads(content)
            except Exception as j_trunc_err:
                if listener is not None and hasattr(listener, "record_protocol_error"):
                    try:
                        listener.record_protocol_error("Risposta JSON troncata (finish_reason=length)")
                    except Exception:
                        pass
                raise AiStructuredOutputError(
                    f"Risposta JSON troncata da LM Studio prima del completamento (finish_reason=length): {j_trunc_err}"
                ) from j_trunc_err

        usage = raw_json.get("usage")
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if usage is not None:
            if not isinstance(usage, dict):
                if listener is not None and hasattr(listener, "record_protocol_error"):
                    try:
                        listener.record_protocol_error("Campo usage non dict")
                    except Exception:
                        pass
                raise AiBackendProtocolError(
                    f"Campo 'usage' deve essere un dizionario, ricevuto {type(usage).__name__}."
                )
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = usage.get(k)
                if val is not None:
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                        if listener is not None and hasattr(listener, "record_protocol_error"):
                            try:
                                listener.record_protocol_error(f"Valore token usage non valido per {k}")
                            except Exception:
                                pass
                        raise AiBackendProtocolError(
                            f"Conteggio token '{k}' in 'usage' non numerico valido >= 0: {val!r}."
                        )
            prompt_tokens = int(usage["prompt_tokens"]) if usage.get("prompt_tokens") is not None else None
            completion_tokens = int(usage["completion_tokens"]) if usage.get("completion_tokens") is not None else None
            total_tokens = int(usage["total_tokens"]) if usage.get("total_tokens") is not None else None

        # TUTTE le validazioni di protocollo e struttura sono state superate con successo!
        # SOLO ADESSO certifichiamo il binding provvisorio promuovendolo a verificato.
        binding = self.get_binding(target_model) or getattr(self, "_active_binding", None)
        if binding is not None:
            if not binding.is_verified:
                binding.is_verified = True
                binding.status = ModelLifecycleStatus.LOADED
            self._active_binding = binding
            self._last_loaded_model = response_model
            self._last_loaded_model_key = binding.resolved_model_key
            self.load_mode = "jit_verified"
        elif getattr(self, "load_mode", None) == "jit_unverified":
            self.load_mode = "jit_verified"
            self._last_loaded_model = response_model

        # Privacy raw_response: conserva solo metadati di protocollo se non in debug esplicito
        if self.debug_preserve_raw:
            clean_raw = raw_json
        else:
            clean_raw = {
                "model": response_model,
                "usage": usage if isinstance(usage, dict) else {},
                "finish_reason": finish_reason,
            }

        if listener is not None and hasattr(listener, "record_request_success"):
            try:
                listener.record_request_success()
            except Exception:
                pass

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

