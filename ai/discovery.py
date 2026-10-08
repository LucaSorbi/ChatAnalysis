"""
ai/discovery.py
---------------
Rilevamento e catalogazione dei modelli LLM disponibili in LM Studio e mappatura delle famiglie.

Principi architetturali (Fase F):
1. NESSUN DOWNLOAD AUTOMATICO:
   Il modulo ispeziona esclusivamente i modelli già scaricati e visibili in LM Studio.
2. STATO RIGOROSO DELLE FAMIGLIE (FASE F1):
   Se il server è irraggiungibile, lo stato delle famiglie è UNVERIFIED.
   missing_families è vuoto e NON include falsi allarmi sulle famiglie mancanti.
3. CATTURA ECCEZIONI TYPED (FASE F2):
   discover_models_on_client cattura solo eccezioni backend note (AiBackendUnavailableError, LmStudioUnavailableError,
   AiBackendProtocolError, OSError). Errori imprevisti propagano.
4. METADATI RICCHI E JIT SEMANTICS (FASI F3, F4):
   Interroga get_models_detailed() se disponibile per estrarre quantizzazione, parametri e contesto senza inventare dati.
   Documenta che /v1/models elenca i modelli visibili al server, che possono essere caricati Just-In-Time in RAM.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Sequence

from ai.backend import (
    AiBackendProtocolError,
    AiBackendUnavailableError,
    BaseLocalLlmClient,
    LmStudioUnavailableError,
)
from ai.models import ExperimentModelSpec, ModelFamily

_BACKEND_DISCOVERY_ERRORS = (
    AiBackendUnavailableError,
    LmStudioUnavailableError,
    AiBackendProtocolError,
    OSError,
)


def infer_model_family(model_id: str) -> ModelFamily:
    """
    Deduce la famiglia architetturale del modello basandosi sul nome/ID.
    Regola di precedenza: verifica 'deepseek' PRIMA di 'qwen' per classificare
    correttamente i modelli distillati (es. DeepSeek-R1-Distill-Qwen-1.5B).
    """
    mid = model_id.lower()
    if "deepseek" in mid:
        return ModelFamily.DEEPSEEK
    if "llama" in mid or "alpaca" in mid or "vicuna" in mid:
        return ModelFamily.LLAMA
    if "qwen" in mid:
        return ModelFamily.QWEN
    return ModelFamily.OTHER


@dataclass(frozen=True)
class ModelDiscoveryReport:
    """
    Rapporto di scoperta dei modelli disponibili sul backend locale.
    """
    server_available: bool
    status: str  # "VERIFIED" se il server ha risposto, "UNVERIFIED" se non raggiungibile
    available_models: tuple[str, ...]
    detected_families: tuple[ModelFamily, ...]
    missing_families: tuple[ModelFamily, ...]
    specs: tuple[ExperimentModelSpec, ...]


def discover_models_on_client(client: BaseLocalLlmClient) -> ModelDiscoveryReport:
    """
    Interroga il client per scoprire i modelli disponibili e catalogarli nelle famiglie sperimentali.
    """
    target_families = (ModelFamily.LLAMA, ModelFamily.QWEN, ModelFamily.DEEPSEEK)

    try:
        if not client.is_available():
            return ModelDiscoveryReport(
                server_available=False,
                status="UNVERIFIED",
                available_models=(),
                detected_families=(),
                missing_families=(),
                specs=(),
            )
    except _BACKEND_DISCOVERY_ERRORS:
        return ModelDiscoveryReport(
            server_available=False,
            status="UNVERIFIED",
            available_models=(),
            detected_families=(),
            missing_families=(),
            specs=(),
        )

    try:
        model_ids = client.list_models()
    except _BACKEND_DISCOVERY_ERRORS:
        return ModelDiscoveryReport(
            server_available=False,
            status="UNVERIFIED",
            available_models=(),
            detected_families=(),
            missing_families=(),
            specs=(),
        )

    detailed_map: dict[str, dict[str, Any]] = {}
    if hasattr(client, "get_models_detailed"):
        try:
            raw_details = client.get_models_detailed()
            for d in raw_details:
                if isinstance(d, dict):
                    mid = d.get("id") or d.get("key")
                    if mid:
                        detailed_map[str(mid)] = d
        except _BACKEND_DISCOVERY_ERRORS:
            pass

    specs: list[ExperimentModelSpec] = []
    detected: set[ModelFamily] = set()

    for mid in model_ids:
        fam = infer_model_family(mid)
        detected.add(fam)
        d_meta = detailed_map.get(mid, {})

        quant = d_meta.get("quantization")
        param_size = d_meta.get("params_string") or d_meta.get("parameter_size")
        max_ctx = d_meta.get("max_context_length")
        runtime_ctx = d_meta.get("runtime_context_length") or d_meta.get("loaded_context_length")
        parsed_max = int(max_ctx) if max_ctx and str(max_ctx).isdigit() else None
        parsed_run = int(runtime_ctx) if runtime_ctx and str(runtime_ctx).isdigit() else None

        specs.append(
            ExperimentModelSpec(
                family=fam,
                model_id=mid,
                quantization=str(quant) if quant else None,
                parameter_size=str(param_size) if param_size else None,
                context_length=parsed_run or parsed_max,
                runtime_context_length=parsed_run,
                max_context_length=parsed_max,
                metadata={k: v for k, v in d_meta.items() if k not in ("id", "key")},
            )
        )

    missing = [f for f in target_families if f not in detected]

    return ModelDiscoveryReport(
        server_available=True,
        status="VERIFIED",
        available_models=tuple(model_ids),
        detected_families=tuple(sorted(detected, key=lambda x: x.value)),
        missing_families=tuple(sorted(missing, key=lambda x: x.value)),
        specs=tuple(specs),
    )
