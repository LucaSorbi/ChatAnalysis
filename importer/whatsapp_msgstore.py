"""
importer/whatsapp_msgstore.py
------------------------------
Importer concreto per WhatsApp msgstore.db — backup nativo Android.

Sorgente supportata
-------------------
File:   msgstore.db
Tipo:   SQLite 3
Tabelle utilizzate: messages (504 righe nel campione), chat_list (3), media_refs (115)

Schema osservato (da ispezione diretta — Fase 1 + Fase 2):
    messages:
        _id, key_remote_jid, key_from_me, key_id, status, needs_push,
        data, timestamp, media_url, media_mime_type, media_wa_type,
        media_size, media_name, media_caption, latitude, longitude,
        thumb_image, remote_resource, received_timestamp, send_timestamp,
        receipt_server_timestamp, receipt_device_timestamp,
        read_device_timestamp, played_device_timestamp, raw_data,
        recipient_count, participant_hash, starred, quoted_row_id,
        mentioned_jids, multicast_id, edit_version, media_enc_hash,
        payment_transaction_id, forwarded, preview_type, deleted
    chat_list:
        _id, key_remote_jid, message_table_id, subject, creation,
        last_read_message_table_id, hidden
    media_refs:
        _id, message_row_id, media_job_uuid, file_path, file_size,
        media_type

Strategia can_import
--------------------
Riconoscimento strutturale non-distruttivo, in sola lettura.
Nome del file ed estensione NON sono un hard gate: in contesti forensi
un database può essere copiato, rinominato o privo di estensione.

Algoritmo:
1. Il file deve esistere ed essere un file regolare (non directory).
2. Si tenta l'apertura in modalità read-only (?mode=ro).
3. Si verifica la presenza delle tabelle caratteristiche di msgstore.db
   ('messages', 'chat_list') e delle colonne chiave ('key_remote_jid',
   'key_from_me', 'key_id') nella tabella messages.
4. Qualsiasi errore (file non SQLite, schema diverso, permessi) →
   return False senza propagare eccezioni.

Modalità read-only
------------------
Ogni connessione usa l'URI ?mode=ro che impedisce scritture a livello
di driver SQLite. Non vengono eseguiti INSERT, UPDATE, CREATE, DROP.

Streaming
---------
import_records() usa cursor.fetchone() in loop row-by-row tramite
iterazione del cursor — nessuna fetchall() sull'intero dataset.

Gestione errori
---------------
- Errore sorgente (file non trovato, non SQLite, tabelle mancanti):
  propagato come FileNotFoundError o ValueError da validate_source().
- Errore record: il mapping SQLite row -> RawRecord è un passthrough diretto.
  Gli errori inattesi/programmatici non vengono intercettati con broad except
  né mascherati da warning con salto record: vengono lasciati propagare
  per garantire integrità e osservabilità forense.

Record prodotti
---------------
record_type "message"   → tabella messages (un RawRecord per riga)
record_type "chat"      → tabella chat_list (un RawRecord per riga)
record_type "media_ref" → tabella media_refs (un RawRecord per riga)

Cosa NON viene fatto
--------------------
- Nessuna normalizzazione di timestamp (unix_ms conservato tal quale)
- Nessuna normalizzazione di JID o numeri di telefono
- Nessuna risoluzione contatti tramite wa.db
- Nessuna verifica esistenza file media su disco
- Nessuna deduplicazione
- Nessuna entity resolution
- Nessun join cross-database
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
# Costanti strutturali — identificano inequivocabilmente msgstore.db
# ---------------------------------------------------------------------------

_SOURCE_NAME = "msgstore_db"

# Tabelle che devono essere presenti per riconoscere il file come msgstore.db
_REQUIRED_TABLES: frozenset[str] = frozenset({"messages", "chat_list"})

# Colonne che devono essere presenti nella tabella messages
_REQUIRED_MSG_COLUMNS: frozenset[str] = frozenset({"key_remote_jid", "key_from_me", "key_id"})

# Versione importer — inclusa nel metadata di ogni record
_IMPORTER_VERSION = "0.1.0"


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """
    Apre una connessione SQLite in modalità read-only (?mode=ro).
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


class WhatsAppMsgstoreImporter(BaseImporter):
    """
    Importer per WhatsApp msgstore.db (backup nativo Android).

    Emette RawRecord per ciascuna riga di:
      - tabella messages   → record_type="message"
      - tabella chat_list  → record_type="chat"
      - tabella media_refs → record_type="media_ref"

    Non tocca wa.db né alcun altro file esterno.
    Non normalizza nessun valore.
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    # ------------------------------------------------------------------
    # can_import — verifica non-distruttiva
    # ------------------------------------------------------------------

    def can_import(self, source_path: Path) -> bool:
        """
        True se source_path è un msgstore.db WhatsApp — riconoscimento strutturale.

        Nome file ed estensione non sono un hard gate: in contesti forensi
        il file può essere rinominato (es. .bak, .csv, o privo di estensione).
        La decisione definitiva dipende dal contenuto SQLite e dallo schema.

        Algoritmo:
        1. Il file deve esistere ed essere un file regolare (non directory).
        2. Apertura in read-only (?mode=ro).
        3. Verifica tabelle caratteristiche ('messages', 'chat_list').
        4. Verifica colonne chiave di WhatsApp ('key_remote_jid', 'key_from_me', 'key_id').
        5. Qualsiasi errore (file non SQLite, corrotto, schema incompatibile) → False.
        """
        if not source_path.exists() or not source_path.is_file():
            return False
        # Verifica strutturale (read-only): è questo un msgstore.db WhatsApp?
        try:
            conn = _open_readonly(source_path)
            try:
                return self._has_required_schema(conn)
            finally:
                conn.close()
        except Exception:
            # Qualsiasi errore di apertura o query (file corrotto, non SQLite, permessi, ecc.)
            # → non è una sorgente gestibile da questo importer
            return False

    def _has_required_schema(self, conn: sqlite3.Connection) -> bool:
        """Controlla tabelle e colonne caratteristiche di msgstore.db."""
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
            ("messages", "chat_list"),
        )
        found_tables = {row[0] for row in cur.fetchall()}
        if not _REQUIRED_TABLES.issubset(found_tables):
            return False
        cur.execute("PRAGMA table_info(messages)")
        found_cols = {row[1] for row in cur.fetchall()}
        return _REQUIRED_MSG_COLUMNS.issubset(found_cols)

    # ------------------------------------------------------------------
    # import_records — generatore principale
    # ------------------------------------------------------------------

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge msgstore.db in read-only e produce RawRecord uno alla volta.

        Ordine: messages → chat_list → media_refs

        Raises
        ------
        FileNotFoundError
            Se source_path non esiste.
        ValueError
            Se source_path non è riconosciuto come msgstore.db.
        sqlite3.DatabaseError
            Se il file SQLite è corrotto e non può essere aperto.
        """
        self.validate_source(source_path)
        source_str = str(source_path.resolve())
        conn = _open_readonly(source_path)
        try:
            yield from self._import_messages(conn, source_str)
            yield from self._import_chat_list(conn, source_str)
            yield from self._import_media_refs(conn, source_str)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Generatori per singola tabella
    # ------------------------------------------------------------------

    def _import_messages(
        self, conn: sqlite3.Connection, source_str: str
    ) -> Iterator[RawRecord]:
        """
        Itera sulla tabella messages riga per riga (streaming).
        Passthrough diretto riga SQLite -> RawRecord.
        Errori programmatici/inattesi propagano per garantire integrità forense.
        """
        cur = conn.cursor()
        cur.execute("SELECT * FROM messages ORDER BY _id")
        for row in cur:
            record_id = str(row["_id"])
            fields = _row_to_dict(row)
            # media_reference: usa media_url se presente, altrimenti None
            media_ref: str | None = fields.get("media_url")  # type: ignore[assignment]
            yield RawRecord(
                source_name=_SOURCE_NAME,
                source_path=source_str,
                source_record_id=record_id,
                record_type="message",
                raw_fields=fields,
                media_reference=media_ref if media_ref else None,
                metadata={
                    "table": "messages",
                    "importer": "WhatsAppMsgstoreImporter",
                    "importer_version": _IMPORTER_VERSION,
                },
            )

    def _import_chat_list(
        self, conn: sqlite3.Connection, source_str: str
    ) -> Iterator[RawRecord]:
        """Itera sulla tabella chat_list riga per riga."""
        cur = conn.cursor()
        cur.execute("SELECT * FROM chat_list ORDER BY _id")
        for row in cur:
            yield RawRecord(
                source_name=_SOURCE_NAME,
                source_path=source_str,
                source_record_id=str(row["_id"]),
                record_type="chat",
                raw_fields=_row_to_dict(row),
                media_reference=None,
                metadata={
                    "table": "chat_list",
                    "importer": "WhatsAppMsgstoreImporter",
                    "importer_version": _IMPORTER_VERSION,
                },
            )

    def _import_media_refs(
        self, conn: sqlite3.Connection, source_str: str
    ) -> Iterator[RawRecord]:
        """Itera sulla tabella media_refs riga per riga."""
        cur = conn.cursor()
        cur.execute("SELECT * FROM media_refs ORDER BY _id")
        for row in cur:
            fields = _row_to_dict(row)
            file_path: str | None = fields.get("file_path")  # type: ignore[assignment]
            yield RawRecord(
                source_name=_SOURCE_NAME,
                source_path=source_str,
                source_record_id=str(row["_id"]),
                record_type="media_ref",
                raw_fields=fields,
                media_reference=file_path if file_path else None,
                metadata={
                    "table": "media_refs",
                    "importer": "WhatsAppMsgstoreImporter",
                    "importer_version": _IMPORTER_VERSION,
                },
            )
