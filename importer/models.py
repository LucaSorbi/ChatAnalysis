"""
importer/models.py
------------------
Modello intermedio RawRecord.

Principio:
    SOURCE  →  RawRecord  →  (futuro) UnifiedMessage

RawRecord è un contenitore immutabile e fedele alla sorgente:
- non applica trasformazioni semantiche
- non normalizza timestamp, numeri di telefono o identità
- non risolve entità cross-source
- conserva i campi originali esattamente come letti

Immutabilità — decisione architetturale:
    frozen=True impedisce la riassegnazione di qualsiasi attributo dell'oggetto.
    I campi raw_fields e metadata sono congelati ricorsivamente in __post_init__
    tramite _freeze_structural() e object.__setattr__:
    - dizionari (top-level e annidati) avvolti in MappingProxyType
    - sequenze (liste e tuple annidate) convertite in tuple immutabili
    - scalari (str, int, float, bool, None, bytes) invariati
    Qualsiasi tentativo di mutazione top-level o annidata solleva TypeError / AttributeError a runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


from core.immutability import freeze_structural

_freeze_structural = freeze_structural



@dataclass(frozen=True)
class RawRecord:
    """
    Record grezzo letto da una sorgente forense, senza trasformazioni.

    Attributes
    ----------
    source_name : str
        Identificatore della sorgente.
        Valori attesi: "msgstore_db", "cellebrite_csv",
        "cellebrite_json", "cellebrite_xml".

    source_path : str
        Path assoluto (stringa) del file sorgente da cui il record è stato letto.
        Usare str e non Path per serializzabilità e compatibilità con frozen.

    source_record_id : str
        Identificativo nativo della sorgente.
        Esempi: "42" (_id SQLite), "msg_00123" (id JSON), "15" (id attributo XML).
        Sempre stringa per uniformità tra sorgenti eterogenee.

    record_type : str
        Tipo logico del record nella sorgente originale.
        Valori comuni: "message", "contact", "chat", "media_ref".
        Non è il tipo semantico unificato (quello sarà in UnifiedMessage).

    raw_fields : dict[str, Any]
        Tutti i campi originali del record, non modificati.
        Chiavi: nomi dei campi così come appaiono nella sorgente.
        Valori: tipi nativi Python (str, int, float, None, bool, dict, list).

    media_reference : str | None
        Riferimento grezzo al file media, se presente nel record.
        Può essere un path relativo, un URL, un path Cellebrite, ecc.
        Non normalizzato — la normalizzazione avverrà in fasi successive.
        None se il record non ha media associati.

    metadata : dict[str, Any]
        Informazioni ausiliarie sulla provenienza del record.
        Esempi di chiavi: "table" (nome tabella SQLite), "row_number",
        "encoding" (CSV), "xml_tag", "importer_version".
        Non contiene dati del messaggio, solo contesto di lettura.
    """

    source_name: str
    source_path: str
    source_record_id: str
    record_type: str
    raw_fields: dict[str, Any]
    media_reference: str | None
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        """
        Validazione e hardening post-costruzione.

        Dopo la validazione, raw_fields e metadata vengono avvolti in
        MappingProxyType via object.__setattr__ (necessario su frozen dataclass).
        Questo rende il dict top-level realmente read-only a runtime.
        """
        if not self.source_name:
            raise ValueError("source_name non può essere vuoto")
        if not self.source_path:
            raise ValueError("source_path non può essere vuoto")
        if not self.source_record_id:
            raise ValueError("source_record_id non può essere vuoto")
        if not self.record_type:
            raise ValueError("record_type non può essere vuoto")
        if not isinstance(self.raw_fields, (dict, MappingProxyType)):
            raise TypeError(f"raw_fields deve essere dict, ricevuto {type(self.raw_fields)}")
        if not isinstance(self.metadata, (dict, MappingProxyType)):
            raise TypeError(f"metadata deve essere dict, ricevuto {type(self.metadata)}")
        # Congelamento ricorsivo: rende raw_fields e metadata profondamente immutabili.
        # Usare object.__setattr__ perché frozen=True blocca l'assegnazione diretta.
        object.__setattr__(self, "raw_fields", _freeze_structural(self.raw_fields))
        object.__setattr__(self, "metadata", _freeze_structural(self.metadata))

    def get_field(self, key: str, default: Any = None) -> Any:
        """Accesso conveniente a raw_fields con default."""
        return self.raw_fields.get(key, default)

    def has_media(self) -> bool:
        """True se il record ha un riferimento media non nullo e non vuoto."""
        return bool(self.media_reference)
