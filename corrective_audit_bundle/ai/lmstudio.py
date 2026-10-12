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


@dataclass(frozen=True)
class LocalModelMetadata:
    """Metadati di un modello installato localmente in LM Studio."""
    key: str
    display_name: str
    quantization: str | None = None
    loaded_instances: tuple[dict[str, Any], ...] = ()
    max_context_length: int | None = None


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
    context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH
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
        if key == "error":
            return self.error_message
        return self.raw_response.get(key, default)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in ("instance_id", "id", "error"):
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return key in ("status", "instance_id", "id", "model", "load_mode", "context_length") or key in self.raw_response


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


_ACTIVE_MODEL_BINDINGS: dict[str, ModelIdentityBinding] = {}


def register_model_binding(binding: ModelIdentityBinding) -> None:
    """Registra una relazione esplicita tra identificatori di modello e istanza nel registro globale."""
    if binding.loaded_instance_id:
        _ACTIVE_MODEL_BINDINGS[binding.loaded_instance_id.strip().lower()] = binding
    if binding.api_identifier:
        _ACTIVE_MODEL_BINDINGS[binding.api_identifier.strip().lower()] = binding
    _ACTIVE_MODEL_BINDINGS[binding.resolved_model_key.strip().lower()] = binding


def get_model_binding(identifier: str) -> ModelIdentityBinding | None:
    """Recupera il binding registrato per un identificatore (instance ID, api identifier o model key)."""
    if not identifier:
        return None
    return _ACTIVE_MODEL_BINDINGS.get(identifier.strip().lower())


def remove_model_binding(identifier: str) -> None:
    """Rimuove un binding specifico e tutti i suoi alias dal registro globale."""
    if not identifier:
        return
    ident_clean = identifier.strip().lower()
    binding = _ACTIVE_MODEL_BINDINGS.pop(ident_clean, None)
    if binding is not None:
        for k in list(_ACTIVE_MODEL_BINDINGS.keys()):
            if _ACTIVE_MODEL_BINDINGS[k] is binding:
                del _ACTIVE_MODEL_BINDINGS[k]


def clear_model_bindings() -> None:
    """Azzera il registro dei model bindings (utile nei test e reset)."""
    _ACTIVE_MODEL_BINDINGS.clear()


@dataclass(frozen=True)
class ResolvedOperationalModel:
    """
    Rappresentazione tipizzata dell'identità del modello operativo risolto.
    Distingue: canonical spec, model key installato, instance id caricato e api identifier.
    """
    requested_canonical_id: str
    model_key: str
    is_loaded: bool
    instance_id: str | None
    api_identifier: str
    display_name: str
    load_mode: str = "unloaded"
    quantization: str | None = None
    context_length: int = DEFAULT_OPERATIONAL_CONTEXT_LENGTH
    canonical_spec: OperationalModelSpec | None = None

    def __iter__(self):
        yield self.model_key
        yield self.is_loaded
        yield self.instance_id

    def is_equivalent_response_model(self, response_model: str) -> bool:
        return is_equivalent_operational_model(self.model_key, response_model)


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


def is_equivalent_operational_model(target_model: str, response_model: str) -> bool:
    """
    Verifica se response_model è equivalente a target_model nel contesto operativo LM Studio.
    Risolve esplicitamente binding registrati tra instance ID opachi e model key.
    Rifiuta categoricamente mismatch cross-family (es. qwen vs llama o qwen vs deepseek).
    """
    if not target_model or not response_model:
        return False
    t_clean = target_model.strip().lower()
    r_clean = response_model.strip().lower()
    if t_clean == r_clean:
        return True

    # Risoluzione binding esplicito per target_model
    b_target = get_model_binding(target_model)
    if b_target is not None:
        bound_key = b_target.resolved_model_key
        if bound_key.strip().lower() != t_clean:
            if is_equivalent_operational_model(bound_key, response_model):
                return True
        bound_req = b_target.requested_canonical_id
        if bound_req.strip().lower() != t_clean:
            if is_equivalent_operational_model(bound_req, response_model):
                return True

    # Risoluzione binding esplicito per response_model
    b_resp = get_model_binding(response_model)
    if b_resp is not None:
        bound_key = b_resp.resolved_model_key
        if bound_key.strip().lower() != r_clean:
            if is_equivalent_operational_model(target_model, bound_key):
                return True
        bound_req = b_resp.requested_canonical_id
        if bound_req.strip().lower() != r_clean:
            if is_equivalent_operational_model(target_model, bound_req):
                return True

    fam_t = get_model_family(t_clean)
    fam_r = get_model_family(r_clean)
    if fam_t and fam_r and fam_t != fam_r:
        return False
    if (fam_t is None) != (fam_r is None):
        return False

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
    if fam_req and fam_id and fam_req != fam_id:
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
    quant = str(item.get("quantization") or "") or None
    raw_loaded = item.get("loaded_instances") or []
    state_val = str(item.get("state") or item.get("status") or "").lower()
    is_loaded_state = state_val in ("loaded", "active", "ready") or item.get("loaded") is True or item.get("is_loaded") is True
    if is_loaded_state and not raw_loaded:
        raw_loaded = [{"id": key}]
    loaded = tuple(raw_loaded) if isinstance(raw_loaded, (list, tuple)) else ()
    max_ctx = item.get("max_context_length")
    return LocalModelMetadata(
        key=key,
        display_name=disp,
        quantization=quant,
        loaded_instances=loaded,
        max_context_length=int(max_ctx) if isinstance(max_ctx, int) else None,
    )


def resolve_qwen_operational_model(
    models: Sequence[LocalModelMetadata | dict[str, Any] | str],
    target_spec: str = "qwen2.5-7b-instruct",
) -> tuple[str, bool, str | None]:
    """
    Risolve deterministicamente il modello Qwen2.5-7B-Instruct.
    Verifica '2.5' esplicitamente e rifiuta modelli cross-family (DeepSeek, Qwen3).
    """
    spec = OPERATIONAL_SPECS["qwen2.5-7b-instruct"]
    resolved = resolve_operational_model_from_metadata(models, requested_model=spec.canonical_id)
    return resolved.model_key, resolved.is_loaded, resolved.instance_id


def resolve_operational_model_from_metadata(
    models: Sequence[LocalModelMetadata | dict[str, Any] | str],
    requested_model: str = "qwen2.5-7b-instruct",
) -> ResolvedOperationalModel:
    """
    Risolve il modello operativo richiesto identificando la specifica canonica (OperationalModelSpec).
    Garantisce assenza totale di cross-family routing (es. DeepSeek vs Qwen vs Llama).
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
            preferred_quantization="",
        )

    norm_models = [_normalize_model_item(m) for m in models]

    def matches_spec(m: LocalModelMetadata) -> bool:
        txt = f"{m.key} {m.display_name}".lower().replace("_", "-").replace(".", "")
        # Verifica token obbligatori
        for tok in target_spec.required_tokens:
            tok_norm = tok.replace(".", "")
            if tok_norm not in txt:
                return False
        # Verifica token vietati
        for tok in target_spec.forbidden_tokens:
            tok_norm = tok.replace(".", "")
            if tok_norm in txt:
                return False
        # Verifica dimensione parametrica se specificata
        if target_spec.parameter_size and target_spec.parameter_size != "custom":
            for other_size in ("14b", "32b", "72b", "05b", "15b", "3b"):
                if other_size != target_spec.parameter_size and other_size in txt:
                    return False
        return True

    candidates = [m for m in norm_models if matches_spec(m)]

    # 1. Verifica se un'istanza è già caricata
    for c in candidates:
        if c.loaded_instances:
            inst = c.loaded_instances[0]
            inst_id = str(inst.get("id") or c.key)
            register_model_binding(ModelIdentityBinding(
                requested_canonical_id=target_spec.canonical_id,
                resolved_model_key=c.key,
                loaded_instance_id=inst_id if inst_id != c.key else None,
                api_identifier=inst_id,
                load_mode="already_loaded",
                status=ModelLifecycleStatus.ALREADY_LOADED,
                is_verified=True,
            ))
            return ResolvedOperationalModel(
                requested_canonical_id=target_spec.canonical_id,
                model_key=c.key,
                is_loaded=True,
                instance_id=inst_id,
                api_identifier=inst_id,
                display_name=target_spec.display_name,
                load_mode="already_loaded",
                quantization=c.quantization,
                canonical_spec=target_spec,
            )

    # 2. Nessun candidato
    if not candidates:
        raise AiModelNotInstalledError(f"Il modello {target_spec.display_name} non è installato localmente in LM Studio.")

    # 3. Candidato singolo
    if len(candidates) == 1:
        c = candidates[0]
        return ResolvedOperationalModel(
            requested_canonical_id=target_spec.canonical_id,
            model_key=c.key,
            is_loaded=False,
            instance_id=None,
            api_identifier=c.key,
            display_name=target_spec.display_name,
            load_mode="unloaded",
            quantization=c.quantization,
            canonical_spec=target_spec,
        )

    # 4. Preferenza per quantizzazione preferita (es. Q4_K_M)
    pref_q = target_spec.preferred_quantization.lower()
    q_candidates = [
        c for c in candidates
        if (c.quantization and pref_q in c.quantization.lower())
        or pref_q in c.key.lower().replace("-", "_")
    ]
    if len(q_candidates) == 1:
        c = q_candidates[0]
        return ResolvedOperationalModel(
            requested_canonical_id=target_spec.canonical_id,
            model_key=c.key,
            is_loaded=False,
            instance_id=None,
            api_identifier=c.key,
            display_name=target_spec.display_name,
            load_mode="unloaded",
            quantization=c.quantization,
            canonical_spec=target_spec,
        )

    if len(q_candidates) > 1:
        raise AiModelAmbiguousError(
            f"Rilevateplici versioni compatibili di {target_spec.display_name} con quantizzazione {target_spec.preferred_quantization.upper()} "
            f"({', '.join(c.key for c in q_candidates)}). Selezionare esplicitamente il modello da utilizzare."
        )

    raise AiModelAmbiguousError(
        f"Rilevateplici versioni compatibili di {target_spec.display_name} "
        f"({', '.join(c.key for c in candidates)}). Selezionare esplicitamente il modello da utilizzare."
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
    ) -> None:
        _validate_loopback_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        # Token letto opzionalmente da parametro o da ambiente
        self._api_token = api_token or os.environ.get("LM_STUDIO_API_TOKEN")
        self.default_timeout_seconds = default_timeout_seconds
        self.debug_preserve_raw = debug_preserve_raw
        self._last_loaded_model: str | None = None
        self._last_loaded_model_key: str | None = None
        self._loaded_instance_id: str | None = None
        self.load_mode: str = "unloaded"
        # Registry istanziabile di proprietà del client per isolamento lifecycle
        self._model_bindings: dict[str, ModelIdentityBinding] = {}
        self._active_binding: ModelIdentityBinding | None = None

    def register_binding(self, binding: ModelIdentityBinding) -> None:
        """Registra una relazione esplicita tra identificatori di modello e istanza per questo client."""
        if binding.loaded_instance_id:
            self._model_bindings[binding.loaded_instance_id.strip().lower()] = binding
        if binding.api_identifier:
            self._model_bindings[binding.api_identifier.strip().lower()] = binding
        self._model_bindings[binding.resolved_model_key.strip().lower()] = binding
        self._active_binding = binding
        register_model_binding(binding)

    def get_binding(self, identifier: str | None) -> ModelIdentityBinding | None:
        """Recupera il binding registrato di proprietà di questo client (o globale come fallback)."""
        if not identifier:
            return None
        return self._model_bindings.get(identifier.strip().lower()) or get_model_binding(identifier)

    def invalidate_binding(self, identifier: str | None = None) -> None:
        """Invalida i binding del modello specificato senza cancellare arbitrariamente binding di altri modelli o client."""
        if identifier:
            ident_clean = identifier.strip().lower()
            binding = self._model_bindings.pop(ident_clean, None)
            if binding is not None:
                for k in list(self._model_bindings.keys()):
                    if self._model_bindings[k] is binding:
                        del self._model_bindings[k]
                if self._active_binding is binding:
                    self._active_binding = None
            remove_model_binding(identifier)
        else:
            self._model_bindings.clear()
            self._active_binding = None

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

                # 4. Validazione positiva dell'identità del modello
                # A. Se il server dichiara esplicitamente un modello (campo 'model' o 'model_key')
                if model_returned is not None:
                    model_returned_str = str(model_returned).strip()
                    if not is_equivalent_operational_model(model_id, model_returned_str):
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

                # C. Se la risposta non contiene un model/model_key documentato che certifichi l'identità:
                # Sia che contenga solo un instance ID opaco (es. {"id": "opaque-instance-001"}),
                # sia uno status generico (es. {"status": "loaded"}),
                # sia entrambi senza un modello esplicitamente dichiarato:
                # NON inventiamo l'identità del modello! Lo stato è JIT_UNVERIFIED e _last_loaded_model NON viene aggiornato prematuramente.
                if model_returned is None:
                    provisional_binding = ModelIdentityBinding(
                        requested_canonical_id=model_id,
                        resolved_model_key=model_id,
                        loaded_instance_id=str(instance_id) if instance_id else None,
                        api_identifier=str(instance_id or model_id),
                        load_mode="jit_unverified",
                        status=ModelLifecycleStatus.JIT_UNVERIFIED,
                        is_verified=False,
                    )
                    self.register_binding(provisional_binding)
                    self._active_binding = provisional_binding
                    self._loaded_instance_id = str(instance_id) if instance_id else None
                    self.load_mode = "jit_unverified"
                    # NOTA: _last_loaded_model NON viene impostato qui
                    return ModelLoadResult(
                        status=ModelLifecycleStatus.JIT_UNVERIFIED,
                        model_key=model_id,
                        instance_id=str(instance_id) if instance_id else None,
                        api_identifier=str(instance_id or model_id),
                        load_mode="jit_unverified",
                        context_length=context_length,
                        raw_response=res,
                        error_message="Risposta priva di conferma documentata del modello; binding da verificare al primo completamento.",
                    )

                # 5. Caricamento positivamente confermato con identità certificata da model/model_key
                eff_status = ModelLifecycleStatus.ALREADY_LOADED if resp_status == "already_loaded" else ModelLifecycleStatus.LOADED
                verified_binding = ModelIdentityBinding(
                    requested_canonical_id=model_id,
                    resolved_model_key=str(model_returned),
                    loaded_instance_id=str(instance_id) if instance_id else None,
                    api_identifier=str(instance_id or model_id),
                    load_mode="already_loaded" if eff_status == ModelLifecycleStatus.ALREADY_LOADED else "native_loaded",
                    status=eff_status,
                    is_verified=True,
                )
                self.register_binding(verified_binding)
                self._active_binding = verified_binding
                self.load_mode = verified_binding.load_mode
                self._last_loaded_model = str(instance_id or model_id)
                self._last_loaded_model_key = model_id
                self._loaded_instance_id = str(instance_id) if instance_id else None
                return ModelLoadResult(
                    status=eff_status,
                    model_key=model_id,
                    instance_id=str(instance_id) if instance_id else None,
                    api_identifier=str(instance_id or model_id),
                    load_mode=verified_binding.load_mode,
                    context_length=context_length,
                    raw_response=res,
                )
        except urllib.error.HTTPError as e:
            err_body = ""
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
                    context_length=context_length,
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

        native_models = self.get_native_models()
        model_key, is_loaded, instance_id = resolve_operational_model_from_metadata(
            native_models, model_id
        )

        if is_loaded:
            active_id = instance_id or model_key
            self.model_id = active_id
            self._loaded_instance_id = instance_id
            return active_id

        load_result = self.load_model(
            model_id=model_key,
            context_length=context_length,
            timeout_seconds=timeout_seconds,
        )

        if load_result.status in (ModelLifecycleStatus.LOADED, ModelLifecycleStatus.ALREADY_LOADED):
            active_id = load_result.instance_id or load_result.api_identifier or model_key
            self.model_id = active_id
            self._loaded_instance_id = load_result.instance_id
            return active_id
        elif load_result.status == ModelLifecycleStatus.JIT_UNVERIFIED:
            active_id = load_result.instance_id or load_result.api_identifier or model_key
            self.model_id = active_id
            self._loaded_instance_id = load_result.instance_id
            self.load_mode = "jit_unverified"
            return active_id
        elif load_result.status == ModelLifecycleStatus.LOADING:
            raise AiModelLoadError(
                f"Il modello '{model_key}' è ancora in fase di caricamento in LM Studio (status=LOADING): {load_result.error_message}"
            )
        else:
            raise AiModelLoadError(
                f"Impossibile verificare il caricamento del modello '{model_key}': stato {load_result.status}"
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

        # Validazione positiva identità response.model contro target_model per tutte le completion
        if not is_equivalent_operational_model(target_model, response_model):
            raise AiModelMismatchError(
                f"Response model mismatch: modello richiesto '{target_model}', "
                f"ma la risposta proviene dal modello non equivalente '{response_model}'.",
                requested_model=target_model,
                returned_model=response_model,
            )

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

