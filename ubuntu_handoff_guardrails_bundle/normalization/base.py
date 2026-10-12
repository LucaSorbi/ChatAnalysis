"""
normalization/base.py
---------------------
Interfaccia base astratta per il layer di Normalizzazione.

Definisce il contratto:
- normalize(validation_result: ValidationResult) -> NormalizedRecord
- normalize_stream(stream: Iterator[ValidationResult]) -> Iterator[NormalizedRecord]

Principi:
- Pipeline streaming incrementale (lazy generator).
- Provenance garantita: riceve ValidationResult (che contiene il RawRecord)
  e produce un NormalizedRecord senza alterare la sorgente o il RawRecord.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from normalization.models import NormalizedRecord
from validation.models import ValidationResult


class BaseNormalizer(ABC):
    """
    Contratto astratto per normalizzatori di record forensi.
    """

    @abstractmethod
    def normalize(self, validation_result: ValidationResult) -> NormalizedRecord:
        """
        Normalizza i campi di un record a partire dal suo ValidationResult.

        Non muta né il RawRecord né il ValidationResult originali.
        """
        pass

    def normalize_stream(self, stream: Iterator[ValidationResult]) -> Iterator[NormalizedRecord]:
        """
        Elabora in streaming lazy un iteratore di ValidationResult,
        emettendo un NormalizedRecord per ciascun elemento.
        """
        for validation_result in stream:
            yield self.normalize(validation_result)
