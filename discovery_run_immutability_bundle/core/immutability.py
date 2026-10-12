"""
core/immutability.py
--------------------
Utility condivisa neutrale per l'immutabilità profonda (Deep Immutability).

Disaccoppia i layer superiori (normalization, entity_resolution, unified)
dall'importer layer.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any


def freeze_structural(val: Any) -> Any:
    """
    Rende ricorsivamente immutabile una struttura dati (deep immutability strutturale).

    - mapping (dict, MappingProxyType) -> MappingProxyType con valori ricorsivamente congelati
    - sequence (list, tuple) -> tuple con elementi ricorsivamente congelati
    - scalari (str, int, float, bool, None, bytes) -> invariati

    Nessuna normalizzazione semantica viene effettuata: chiavi, valori, ordine e tipi
    vengono rigorosamente preservati.
    """
    if isinstance(val, (dict, MappingProxyType)):
        return MappingProxyType({k: freeze_structural(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple)):
        return tuple(freeze_structural(item) for item in val)
    return val
