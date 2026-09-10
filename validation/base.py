"""
validation/base.py
------------------
Interfaccia astratta per il layer di Validazione.

Definisce il contratto fondamentale:
- validate(record: RawRecord) -> ValidationResult
- validate_stream(records: Iterator[RawRecord]) -> Iterator[ValidationResult]

Principi:
- Strettamente non distruttivo: nessun RawRecord viene alterato o filtrato via.
- Streaming incrementale: l'elaborazione avviene record per record senza bufferizzare
  l'intero flusso in liste in memoria o convertire in DataFrame/Pandas.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from importer.models import RawRecord
from validation.models import ValidationResult


class BaseValidator(ABC):
    """
    Contratto base per validatori di RawRecord.

    Ogni validatore concreto deve implementare `validate(record)` per osservare
    un singolo RawRecord e restituire un ValidationResult.
    """

    @abstractmethod
    def validate(self, record: RawRecord) -> ValidationResult:
        """
        Osserva un singolo RawRecord e restituisce il relativo ValidationResult.

        Non deve mai:
        - modificare il RawRecord fornito;
        - normalizzare formati di dati (es. date, telefoni);
        - scartare o eliminare record;
        - accedere a risorse di rete o scrivere sul filesystem.
        """
        pass

    def validate_stream(self, records: Iterator[RawRecord]) -> Iterator[ValidationResult]:
        """
        Valida una sequenza di RawRecord in streaming incrementale.

        Produce un generatore lazy che emette un ValidationResult per ciascun
        RawRecord in ingresso, preservando l'elaborazione streaming della pipeline.
        """
        for record in records:
            yield self.validate(record)
