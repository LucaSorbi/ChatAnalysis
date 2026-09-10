"""
importer/base.py
----------------
Interfaccia astratta comune a tutti gli importer forensi.

Scelta architetturale: ABC (Abstract Base Class)
    Rispetto a Protocol, ABC garantisce:
    - Errore a runtime al momento dell'istanziazione se un metodo astratto
      non è implementato (fail-fast)
    - Ereditarietà esplicita e nominale (issubclass() funziona correttamente)
    - Più adatto a contesti forensi dove la correttezza dell'interfaccia
      è un requisito di affidabilità, non solo di typing statico

    Protocol sarebbe preferibile solo se volessimo duck-typing (classi di terze
    parti che non ereditano da BaseImporter ma ne rispettano la firma).
    In questo progetto tutti gli importer sono interni e controllati: ABC è la
    scelta corretta.

Contratto:
    - can_import() è NON-DISTRUTTIVO: può aprire la sorgente in read-only
      per verifiche strutturali (es. schema DB), ma non deve scrivere,
      creare o modificare nulla. Non deve sollevare eccezioni per sorgenti
      non compatibili: restituisce False.
    - import_records() è un generatore (Iterator) per evitare di caricare
      interi dataset in memoria; particolarmente importante per DB SQLite
      con decine di migliaia di righe
    - Gli errori non bloccano l'intera elaborazione: ogni importer deve
      gestire eccezioni riga per riga con logging, non silenziare con
      `except Exception: pass` (gli errori devono essere osservabili)
    - Gli importer sono READ-ONLY sulla sorgente (stesso contratto del profiler)
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from importer.models import RawRecord

logger = logging.getLogger(__name__)


class BaseImporter(ABC):
    """
    Interfaccia astratta per tutti gli importer forensi.

    Un importer concreto deve:
    1. Dichiarare il proprio source_name (proprietà)
    2. Implementare can_import() per auto-selezione
    3. Implementare import_records() come generatore di RawRecord

    Esempio di utilizzo futuro:
        importer = SQLiteImporter()
        if importer.can_import(path):
            for record in importer.import_records(path):
                process(record)
    """

    # ------------------------------------------------------------------
    # Interfaccia astratta — DEVE essere implementata dalle sottoclassi
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def source_name(self) -> str:
        """
        Identificatore della sorgente gestita da questo importer.
        Deve corrispondere a RawRecord.source_name nei record prodotti.
        Esempi: "msgstore_db", "cellebrite_csv", "cellebrite_json", "cellebrite_xml"
        """
        ...

    @abstractmethod
    def can_import(self, source_path: Path) -> bool:
        """
        Verifica se questo importer è in grado di gestire la sorgente.

        Contratto: NON-DISTRUTTIVO.
        Può aprire la sorgente in modalità read-only per verifiche strutturali
        (es. controllare tabelle caratteristiche di un DB SQLite), ma:
        - non deve scrivere, creare o modificare nulla
        - non deve sollevare eccezioni per sorgenti non compatibili
        - deve restituire False se la sorgente non è gestibile
        - deve restituire False se la sorgente non esiste

        Parameters
        ----------
        source_path : Path
            Path del file o directory da verificare.

        Returns
        -------
        bool
            True se l'importer può gestire la sorgente, False altrimenti.
        """
        ...

    @abstractmethod
    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge la sorgente e produce RawRecord uno alla volta (generatore).

        Contratto:
        - READ-ONLY sulla sorgente: nessuna modifica al file originale
        - Lazy evaluation: i record vengono letti e prodotti uno per uno
        - Resilienza: gli errori su singoli record non devono interrompere
          l'iterazione; loggare e continuare con il record successivo
        - Nessuna normalizzazione: i valori sono copiati dalla sorgente
          senza modifiche semantiche

        Parameters
        ----------
        source_path : Path
            Path del file o directory da leggere.

        Yields
        ------
        RawRecord
            Record grezzo letto dalla sorgente.

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è gestibile da questo importer
            (verificare prima con can_import()).
        """
        ...

    # ------------------------------------------------------------------
    # Metodi concreti — comportamento di default condiviso
    # ------------------------------------------------------------------

    def validate_source(self, source_path: Path) -> None:
        """
        Verifica che la sorgente esista e sia gestibile.
        Chiamato tipicamente all'inizio di import_records().

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se can_import() restituisce False.
        """
        if not source_path.exists():
            raise FileNotFoundError(f"Sorgente non trovata: {source_path}")
        if not self.can_import(source_path):
            raise ValueError(
                f"{self.__class__.__name__} non può gestire: {source_path} "
                f"(usa can_import() per verificare prima)"
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source_name={self.source_name!r})"
