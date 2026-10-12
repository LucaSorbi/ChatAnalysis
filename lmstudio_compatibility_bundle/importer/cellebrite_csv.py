"""
importer/cellebrite_csv.py
--------------------------
Importer concreto per esportazioni messaggi Cellebrite in formato CSV.

Sorgente supportata
-------------------
File:   messages.csv (o esportazioni CSV Cellebrite compatibili)
Tipo:   CSV delimitato da virgola, encoding UTF-8 (con o senza BOM)
Colonne osservate nel campione:
    Source, MessageType, TimeStamp, Direction, From, To, Body,
    Attachments, Status, Deleted, Forwarded, ApplicationId, ChatId

Strategia can_import
--------------------
Riconoscimento strutturale non-distruttivo in sola lettura.
Nome del file ed estensione NON sono un hard gate: in contesti forensi
un'esportazione può essere rinominata (es. evidence.bak, report.txt o senza estensione).
L'algoritmo legge esclusivamente la prima riga del file (header)
verificando la presenza delle colonne caratteristiche di Cellebrite
('Source', 'MessageType', 'TimeStamp', 'Direction', 'ChatId').
Qualsiasi errore di decodifica (file binari, SQLite, immagini)
o schema incompatibile restituisce False.

Modalità read-only
------------------
Apertura in sola lettura standard ('r', encoding='utf-8-sig').
Nessuna scrittura, creazione di file temporanei o modifica del file originale.

Streaming
---------
import_records() genera RawRecord riga per riga tramite csv.DictReader,
garantendo un'elaborazione incrementale con consumo proporzionale alla singola riga.

Questioni aperte (esplicitamente non risolte a questo layer):
------------------------------------------------------------
1. ChatId -> JID: ChatId è conservato tal quale ('chat_1', 'chat_2', 'chat_3').
2. group_participant_A: l'alias è conservato verbatim senza risoluzione.
3. Timezone CSV: il timestamp è conservato come stringa raw (nessuna assunzione UTC o Europe/Rome).
4. Deleted semantics: conservato come stringa ('False', 'True'), senza conversione boolean.
5. Validità media: Attachments è conservato come stringa senza verifica su disco.
6. Message types: nessun filtro o tassonomia chiusa; tipi non ancora osservati passano.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterator

from importer.base import BaseImporter
from importer.models import RawRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti strutturali — identificano esportazioni CSV Cellebrite
# ---------------------------------------------------------------------------

_SOURCE_NAME = "cellebrite_csv"

# Colonne caratteristiche minime richieste nell'header
_REQUIRED_COLUMNS: frozenset[str] = frozenset({
    "Source", "MessageType", "TimeStamp", "Direction", "ChatId"
})

# Versione importer — inclusa nel metadata di ogni record
_IMPORTER_VERSION = "0.1.0"


class CellebriteCsvImporter(BaseImporter):
    """
    Importer per esportazioni messaggi Cellebrite in formato CSV.

    Emette RawRecord per ciascuna riga di dati del file CSV:
      - source_name = "cellebrite_csv"
      - record_type = "message"
      - source_record_id = "row:<line_number>" (identificativo strutturale 1-based)
      - raw_fields = dizionario colonna -> valore stringa originale
      - media_reference = valore di Attachments se non vuoto, altrimenti None
      - metadata = context di provenienza (linea, versione importer)
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    # ------------------------------------------------------------------
    # can_import — verifica non-distruttiva
    # ------------------------------------------------------------------

    def can_import(self, source_path: Path) -> bool:
        """
        True se source_path è un file CSV Cellebrite compatibile — riconoscimento strutturale.

        Legge esclusivamente l'header del file verificando la presenza delle colonne
        caratteristiche di Cellebrite. Nome ed estensione non sono un hard gate.
        """
        if not source_path.exists() or not source_path.is_file():
            return False
        try:
            with open(source_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    return False
                header_cols = {col.strip() for col in header if isinstance(col, str)}
                return _REQUIRED_COLUMNS.issubset(header_cols)
        except Exception:
            # Errori di decodifica, file binari, permessi, ecc. -> False
            return False

    # ------------------------------------------------------------------
    # import_records — generatore principale
    # ------------------------------------------------------------------

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge il CSV Cellebrite riga per riga e produce RawRecord in streaming.

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è riconosciuto come CSV Cellebrite
            o se una riga è malformata.
        """
        self.validate_source(source_path)
        source_str = str(source_path.resolve())

        with open(source_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError(f"File CSV privo di header: {source_path}")

            prev_line_num = reader.line_num  # 1 dopo la lettura dell'header
            for row in reader:
                start_line = prev_line_num + 1
                end_line = reader.line_num
                prev_line_num = end_line

                # Rilevamento anomalie strutturali / righe malformate
                if None in row:
                    raise ValueError(
                        f"Riga malformata alla linea {start_line} di {source_path}: "
                        f"trovati campi in eccesso {row[None]}"
                    )
                if any(v is None for v in row.values()):
                    raise ValueError(
                        f"Riga malformata alla linea {start_line} di {source_path}: "
                        f"colonne mancanti rispetto all'header"
                    )

                record_id = f"row:{start_line}"
                attachments = row.get("Attachments", "")
                media_ref: str | None = attachments if attachments else None

                yield RawRecord(
                    source_name=_SOURCE_NAME,
                    source_path=source_str,
                    source_record_id=record_id,
                    record_type="message",
                    raw_fields=dict(row),
                    media_reference=media_ref,
                    metadata={
                        "table": "messages",
                        "row_number": start_line,
                        "importer": "CellebriteCsvImporter",
                        "importer_version": _IMPORTER_VERSION,
                    },
                )
