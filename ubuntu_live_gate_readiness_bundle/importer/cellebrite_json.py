"""
importer/cellebrite_json.py
---------------------------
Importer concreto per esportazioni messaggi Cellebrite in formato JSON.

Sorgente supportata
-------------------
File:   messages.json (o esportazioni JSON Cellebrite compatibili)
Tipo:   File JSON contenente un array top-level di oggetti messaggio.
Chiavi caratteristiche osservate nel campione:
    id, chat_id, sender, timestamp, type, content, metadata

Struttura interna:
    content  -> {"text": str | None, "media_path": str | None}
    metadata -> {"deleted": bool, "forwarded": bool, "starred": bool}

Strategia can_import
--------------------
Riconoscimento strutturale non-distruttivo in sola lettura.
Nome del file ed estensione NON sono un hard gate: in contesti forensi
un'esportazione può essere rinominata (es. evidence.bak, report.txt o senza estensione).
L'algoritmo legge in streaming esclusivamente il primo elemento dell'array
senza caricare l'intero file in memoria, verificando la presenza delle chiavi
caratteristiche di Cellebrite ('chat_id', 'sender', 'timestamp', 'type').
Qualsiasi errore di decodifica (file binari, SQLite, testo non JSON, XML, CSV)
o schema incompatibile restituisce False.

Modalità read-only
------------------
Apertura in sola lettura binaria ('rb').
Nessuna operazione di scrittura o modifica dell'evidenza originale.

Streaming
---------
Utilizza la libreria `ijson` per iterare in streaming gli elementi dell'array
top-level ('item') uno per uno, garantendo un'elaborazione incrementale con consumo
proporzionale alla dimensione del singolo record anche su file di grandi dimensioni.

Questioni aperte (esplicitamente non risolte a questo layer):
------------------------------------------------------------
1. ChatId -> JID: chat_id è conservato tal quale ('chat_1', 'chat_2', 'chat_3').
2. group_participant_A: l'alias è conservato verbatim senza risoluzione a contatti.
3. Timezone JSON: il timestamp è conservato nel formato ISO-8601 originale senza conversione UTC forzata.
4. Deleted semantics: conservato come tipo booleano JSON nativo (True/False).
5. Validità media: media_path è conservato come stringa senza verifica di esistenza fisica su disco.
6. Message types: nessun filtro o tassonomia di dominio chiusa.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import ijson

from importer.base import BaseImporter
from importer.models import RawRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti strutturali — identificano esportazioni JSON Cellebrite
# ---------------------------------------------------------------------------

_SOURCE_NAME = "cellebrite_json"

# Chiavi caratteristiche minime richieste nel primo oggetto JSON
_REQUIRED_KEYS: frozenset[str] = frozenset({
    "chat_id", "sender", "timestamp", "type"
})

# Versione importer — inclusa nel metadata di ogni record
_IMPORTER_VERSION = "0.1.0"


class CellebriteJsonImporter(BaseImporter):
    """
    Importer per esportazioni messaggi Cellebrite in formato JSON.

    Emette RawRecord per ciascun elemento dell'array JSON:
      - source_name = "cellebrite_json"
      - record_type = "message"
      - source_record_id = ID nativo del record (es. "msg_00000") o fallback "item:<idx>"
      - raw_fields = mapping completo dei campi con tipi nativi e strutture annidate congelate
      - media_reference = content.media_path se presente e non nullo, altrimenti None
      - metadata = contesto di provenienza (array_index, formato, versione importer)
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    # ------------------------------------------------------------------
    # can_import — verifica non-distruttiva
    # ------------------------------------------------------------------

    def can_import(self, source_path: Path) -> bool:
        """
        True se source_path è un file JSON Cellebrite compatibile — riconoscimento strutturale.

        Legge in streaming il primo elemento dell'array via ijson per verificare
        le chiavi caratteristiche. Nome ed estensione non costituiscono hard gate.
        """
        if not source_path.exists() or not source_path.is_file():
            return False

        try:
            with open(source_path, "rb") as f:
                items = ijson.items(f, "item")
                first_item = next(items, None)
                if not isinstance(first_item, dict):
                    return False
                return _REQUIRED_KEYS.issubset(first_item.keys())
        except (OSError, ijson.JSONError, UnicodeDecodeError):
            # Errori I/O, permessi, JSON malformato o troncato -> False
            return False

    # ------------------------------------------------------------------
    # import_records — generatore principale streaming
    # ------------------------------------------------------------------

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge il file JSON Cellebrite in streaming e produce RawRecord elemento per elemento.

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è riconosciuto come JSON Cellebrite
            o se un elemento dell'array è malformato.
        """
        self.validate_source(source_path)
        source_str = str(source_path.resolve())

        with open(source_path, "rb") as f:
            items = ijson.items(f, "item")
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Elemento malformato all'indice {idx} di {source_path}: "
                        f"atteso dict JSON, ricevuto {type(item).__name__}"
                    )

                # Priorità source_record_id: ID nativo > fallback strutturale item:<idx>
                native_id = item.get("id")
                if native_id is not None and str(native_id).strip():
                    record_id = str(native_id)
                else:
                    record_id = f"item:{idx}"

                # Estrazione del riferimento media (content.media_path)
                content = item.get("content")
                media_ref: str | None = None
                if isinstance(content, dict):
                    raw_media = content.get("media_path")
                    if raw_media is not None and str(raw_media).strip():
                        media_ref = str(raw_media)

                yield RawRecord(
                    source_name=_SOURCE_NAME,
                    source_path=source_str,
                    source_record_id=record_id,
                    record_type="message",
                    raw_fields=dict(item),
                    media_reference=media_ref,
                    metadata={
                        "table": "messages",
                        "format": "json_array",
                        "array_index": idx,
                        "importer": "CellebriteJsonImporter",
                        "importer_version": _IMPORTER_VERSION,
                    },
                )
