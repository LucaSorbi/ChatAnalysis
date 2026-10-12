"""
importer/whatsapp_wa.py
-----------------------
Importer concreto per WhatsApp wa.db — database contatti nativo Android.

Sorgente supportata
-------------------
File:   wa.db
Tipo:   SQLite 3
Tabelle utilizzate: contacts (4 righe nel campione sintetico)

Schema osservato (da ispezione diretta):
    contacts:
        _id (INTEGER PRIMARY KEY)
        jid (TEXT)
        display_name (TEXT)
        status (TEXT)
        phone_number (TEXT)

Strategia can_import
--------------------
Riconoscimento strutturale non-distruttivo in sola lettura.
Nome del file ed estensione NON sono un hard gate: in contesti forensi
un database può essere rinominato (es. wa.bak, wa.csv o privo di estensione).
La decisione definitiva dipende dal contenuto SQLite e dalla presenza
della tabella 'contacts' e delle colonne chiave ('jid', 'display_name', 'phone_number').

Modalità read-only
------------------
Connessione SQLite con URI ?mode=ro garantito a livello di driver.
Nessuna scrittura, creazione di tabelle, migrazioni, viste, indici,
file WAL (.db-wal) o journal (.db-journal).

Streaming
---------
import_records() genera RawRecord uno alla volta tramite iterazione del cursor
(row-by-row), senza fetchall() sull'intero dataset.

Gestione errori
---------------
- Errore sorgente (file non trovato, non SQLite, tabelle mancanti):
  propagato come FileNotFoundError o ValueError da validate_source().
- Errore record: il mapping SQLite row -> RawRecord è un passthrough diretto.
  Gli errori inattesi/programmatici non vengono intercettati con broad except
  né mascherati da warning con salto record: vengono lasciati propagare
  per garantire integrità e osservabilità forense.

Separazione assoluta
--------------------
wa.db è una sorgente separata e indipendente da msgstore.db.
Nessun join cross-database, nessun arricchimento di messaggi con nomi contatto,
nessuna risoluzione di JID, nessuna creazione di entità Participant o Chat unificate.

Cosa NON viene fatto
--------------------
- Nessuna normalizzazione del numero telefonico in E.164
- Nessuna normalizzazione del JID
- Nessuna risoluzione di contatti verso messaggi di msgstore.db
- Nessuna deduplicazione
- Nessuna entity resolution
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from importer.base import BaseImporter
from importer.models import RawRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti strutturali — identificano wa.db
# ---------------------------------------------------------------------------

_SOURCE_NAME = "wa_db"

# Tabelle richieste
_REQUIRED_TABLES: frozenset[str] = frozenset({"contacts"})

# Colonne caratteristiche della tabella contacts
_REQUIRED_CONTACTS_COLUMNS: frozenset[str] = frozenset({"jid", "display_name", "phone_number"})

# Versione importer — inclusa nel metadata di ogni record
_IMPORTER_VERSION = "0.1.0"


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """
    Apre una connessione SQLite in modalità read-only (?mode=ro) con path assoluto.
    L'URI mode=ro è garantito a livello di driver: qualsiasi tentativo
    di scrittura genera OperationalError.
    """
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converte sqlite3.Row in dict Python ordinario, preservando None."""
    return {k: row[k] for k in row.keys()}


class WhatsAppWaDbImporter(BaseImporter):
    """
    Importer per WhatsApp wa.db (database contatti nativo Android).

    Emette RawRecord per ciascuna riga della tabella 'contacts':
      - record_type = "contact"
      - raw_fields = tutti i campi originali (_id, jid, display_name, status, phone_number)
      - media_reference = None
      - metadata = context tecnico di lettura

    Nessuna normalizzazione dei valori (nessun E.164, nessun alias).
    Nessun cross-linkage con msgstore.db.
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    # ------------------------------------------------------------------
    # can_import — verifica non-distruttiva
    # ------------------------------------------------------------------

    def can_import(self, source_path: Path) -> bool:
        """
        True se source_path è un wa.db WhatsApp — riconoscimento strutturale.

        Nome file ed estensione non sono un hard gate: in contesti forensi
        il file può essere rinominato (es. .bak, .csv, o privo di estensione).
        La decisione definitiva dipende dal contenuto SQLite e dallo schema.

        Algoritmo:
        1. Il file deve esistere ed essere un file regolare (non directory).
        2. Apertura in read-only (?mode=ro).
        3. Verifica presenza della tabella 'contacts'.
        4. Verifica presenza colonne chiave ('jid', 'display_name', 'phone_number').
        5. Qualsiasi errore (file non SQLite, corrotto, schema incompatibile) → False.
        """
        if not source_path.exists() or not source_path.is_file():
            return False
        try:
            conn = _open_readonly(source_path)
            try:
                return self._has_required_schema(conn)
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            # Errori I/O, permessi, file non SQLite, o DB corrotto/non apribile
            # → non è una sorgente gestibile da questo importer. Bug programmatici inattesi propagano.
            return False

    def _has_required_schema(self, conn: sqlite3.Connection) -> bool:
        """Controlla tabella contacts e colonne caratteristiche di wa.db."""
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
        )
        found_tables = {row[0] for row in cur.fetchall()}
        if not _REQUIRED_TABLES.issubset(found_tables):
            return False
        cur.execute("PRAGMA table_info(contacts)")
        found_cols = {row[1] for row in cur.fetchall()}
        return _REQUIRED_CONTACTS_COLUMNS.issubset(found_cols)

    # ------------------------------------------------------------------
    # import_records — generatore principale
    # ------------------------------------------------------------------

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge wa.db in read-only e produce RawRecord uno alla volta.

        Itera sulla tabella contacts riga per riga (streaming).
        Passthrough diretto riga SQLite -> RawRecord.
        Errori programmatici/inattesi propagano per garantire integrità forense.

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è riconosciuto come wa.db.
        sqlite3.DatabaseError
            Se il file SQLite è corrotto e non può essere aperto.
        """
        self.validate_source(source_path)
        source_str = str(source_path.resolve())
        conn = _open_readonly(source_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM contacts ORDER BY _id")
            for row in cur:
                record_id = str(row["_id"])
                fields = _row_to_dict(row)
                yield RawRecord(
                    source_name=_SOURCE_NAME,
                    source_path=source_str,
                    source_record_id=record_id,
                    record_type="contact",
                    raw_fields=fields,
                    media_reference=None,
                    metadata={
                        "table": "contacts",
                        "importer": "WhatsAppWaDbImporter",
                        "importer_version": _IMPORTER_VERSION,
                    },
                )
        finally:
            conn.close()
