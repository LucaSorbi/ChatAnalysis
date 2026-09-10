"""
entity_resolution/base.py
-------------------------
Interfaccia base astratta per il layer di Entity Resolution Foundation.

Definisce il contratto:
- resolve(records: Iterable[NormalizedRecord]) -> ResolutionResult

Principi:
- Non altera né i NormalizedRecord né i RawRecord originali.
- Preserva la provenance di ogni entità collegata.
- Non costruisce UnifiedMessage né Participant definitivi.
- Non elimina record duplicati (li segnala in DuplicateCandidate).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from entity_resolution.models import ResolutionResult
from normalization.models import NormalizedRecord


class BaseEntityResolver(ABC):
    """
    Contratto astratto per resolver di entità forensi.
    """

    @abstractmethod
    def resolve(self, records: Iterable[NormalizedRecord]) -> ResolutionResult:
        """
        Elabora un insieme di NormalizedRecord e produce un ResolutionResult.

        Parameters
        ----------
        records : Iterable[NormalizedRecord]
            Sequenza di record normalizzati provenienti da una o più sorgenti.

        Returns
        -------
        ResolutionResult
            Risultato contenente le entità candidate, i riferimenti non risolti
            e i gruppi di record duplicati candidati.
        """
        pass
