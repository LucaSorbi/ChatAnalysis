"""
tests/unit/test_whatsapp_msgstore_importer.py
----------------------------------------------
Test UNITARI per WhatsAppMsgstoreImporter.

Principio: nessun I/O sul vero msgstore.db sintetico.
I casi che richiedono un DB reale vanno nei test di integrazione.

Per i casi che richiedono un DB SQLite controllato, si crea un
database temporaneo in tmp_path con lo schema minimo di msgstore.db.

Nessun dato forense reale — tutti i valori sono sintetici.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter

# ---------------------------------------------------------------------------
# Helper: crea DB temporanei con schema controllato
# ---------------------------------------------------------------------------

def _make_minimal_msgstore(path: Path, n_messages: int = 2) -> None:
    """
    Crea un msgstore.db sintetico minimale con schema WhatsApp.
    Tutti i valori sono inventati.
    """
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chat_list (
            _id               INTEGER PRIMARY KEY,
            key_remote_jid    TEXT,
            message_table_id  INTEGER,
            subject           TEXT,
            creation          INTEGER,
            last_read_message_table_id INTEGER,
            hidden            INTEGER
        );
        CREATE TABLE messages (
            _id                        INTEGER PRIMARY KEY,
            key_remote_jid             TEXT,
            key_from_me                INTEGER,
            key_id                     TEXT,
            status                     INTEGER,
            needs_push                 INTEGER,
            data                       TEXT,
            timestamp                  INTEGER,
            media_url                  TEXT,
            media_mime_type            TEXT,
            media_wa_type              INTEGER,
            media_size                 INTEGER,
            media_name                 TEXT,
            media_caption              TEXT,
            latitude                   REAL,
            longitude                  REAL,
            thumb_image                TEXT,
            remote_resource            TEXT,
            received_timestamp         INTEGER,
            send_timestamp             INTEGER,
            receipt_server_timestamp   INTEGER,
            receipt_device_timestamp   INTEGER,
            read_device_timestamp      INTEGER,
            played_device_timestamp    INTEGER,
            raw_data                   BLOB,
            recipient_count            INTEGER,
            participant_hash           TEXT,
            starred                    INTEGER,
            quoted_row_id              INTEGER,
            mentioned_jids             TEXT,
            multicast_id               TEXT,
            edit_version               INTEGER,
            media_enc_hash             TEXT,
            payment_transaction_id     TEXT,
            forwarded                  INTEGER,
            preview_type               INTEGER,
            deleted                    INTEGER
        );
        CREATE TABLE media_refs (
            _id              INTEGER PRIMARY KEY,
            message_row_id   INTEGER,
            media_job_uuid   TEXT,
            file_path        TEXT,
            file_size        INTEGER,
            media_type       INTEGER
        );
    """)
    # chat_list: 1 chat sintetica
    conn.execute(
        "INSERT INTO chat_list VALUES (1, 'synth_001@s.whatsapp.net', 1, NULL, 1700000000, 1, 0)"
    )
    # messages
    for i in range(1, n_messages + 1):
        conn.execute(
            "INSERT INTO messages "
            "(_id, key_remote_jid, key_from_me, key_id, status, needs_push, "
            " data, timestamp, media_url, media_mime_type, media_wa_type, "
            " media_size, media_name, media_caption, latitude, longitude, "
            " thumb_image, remote_resource, received_timestamp, send_timestamp, "
            " receipt_server_timestamp, receipt_device_timestamp, "
            " read_device_timestamp, played_device_timestamp, raw_data, "
            " recipient_count, participant_hash, starred, quoted_row_id, "
            " mentioned_jids, multicast_id, edit_version, media_enc_hash, "
            " payment_transaction_id, forwarded, preview_type, deleted) "
            "VALUES (?, 'synth_001@s.whatsapp.net', ?, 'KEY_' || ?, "
            " 5, 0, 'testo sintetico ' || ?, 1700000000000, "
            " NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, "
            " NULL, NULL, 1700000000500, NULL, "
            " NULL, NULL, NULL, NULL, NULL, "
            " NULL, NULL, 0, NULL, "
            " NULL, NULL, 0, NULL, "
            " NULL, 0, 0, 0)",
            (i, i % 2, str(i).zfill(6), i),
        )
    conn.commit()
    conn.close()


def _make_non_whatsapp_sqlite(path: Path) -> None:
    """Crea un SQLite generico senza schema WhatsApp."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.commit()
    conn.close()


def _make_msgstore_missing_columns(path: Path) -> None:
    """Crea un SQLite con tabelle WhatsApp ma colonne mancanti."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chat_list (_id INTEGER PRIMARY KEY, subject TEXT);
        CREATE TABLE messages (_id INTEGER PRIMARY KEY, data TEXT);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return WhatsAppMsgstoreImporter()


@pytest.fixture
def minimal_db(tmp_path):
    """DB temporaneo con schema WhatsApp completo e 3 messaggi sintetici."""
    db = tmp_path / "msgstore.db"
    _make_minimal_msgstore(db, n_messages=3)
    return db


@pytest.fixture
def non_whatsapp_db(tmp_path):
    """DB SQLite generico, non WhatsApp."""
    db = tmp_path / "other.db"
    _make_non_whatsapp_sqlite(db)
    return db


@pytest.fixture
def incomplete_schema_db(tmp_path):
    """DB SQLite con tabelle WhatsApp ma colonne chiave mancanti."""
    db = tmp_path / "partial.db"
    _make_msgstore_missing_columns(db)
    return db


# ---------------------------------------------------------------------------
# BaseImporter contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestInheritsBaseImporter:

    def test_is_base_importer_subclass(self, importer):
        assert isinstance(importer, BaseImporter)

    def test_source_name_is_string(self, importer):
        assert isinstance(importer.source_name, str)

    def test_source_name_value(self, importer):
        assert importer.source_name == "msgstore_db"

    def test_repr_contains_class_name(self, importer):
        assert "WhatsAppMsgstoreImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "msgstore_db" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportPositive:

    def test_returns_true_for_valid_msgstore(self, importer, minimal_db):
        """A. DB sintetico minimale con schema WhatsApp → True."""
        assert importer.can_import(minimal_db) is True

    def test_returns_bool(self, importer, minimal_db):
        result = importer.can_import(minimal_db)
        assert isinstance(result, bool)

    def test_idempotent(self, importer, minimal_db):
        """Chiamate multiple devono restituire lo stesso risultato."""
        r1 = importer.can_import(minimal_db)
        r2 = importer.can_import(minimal_db)
        assert r1 == r2

    def test_renamed_db_no_extension(self, importer, minimal_db, tmp_path):
        """
        B. Copia del DB rinominata senza estensione → True.
        Scenario forensico: il file può essere copiato senza suffisso.
        """
        import shutil
        renamed = tmp_path / "msgstore_backup_renamed"
        shutil.copy2(minimal_db, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_db_unusual_extension(self, importer, minimal_db, tmp_path):
        """
        B. Copia del DB con estensione insolita (.bak) → True.
        Il riconoscimento strutturale non dipende dall'estensione.
        """
        import shutil
        renamed = tmp_path / "msgstore.bak"
        shutil.copy2(minimal_db, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_db_wrong_name_right_schema(self, importer, minimal_db, tmp_path):
        """
        B. DB con nome completamente diverso ma schema WhatsApp corretto → True.
        """
        import shutil
        renamed = tmp_path / "evidence_database_01.db"
        shutil.copy2(minimal_db, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_db_csv_extension(self, importer, minimal_db, tmp_path):
        """
        4. Copia dello stesso DB chiamata evidence.csv → True.
        L'estensione non è un hard gate se la struttura SQLite è compatibile.
        """
        import shutil
        renamed = tmp_path / "evidence.csv"
        shutil.copy2(minimal_db, renamed)
        assert importer.can_import(renamed) is True

    def test_synthetic_original_msgstore_returns_true(self, importer):
        """1. msgstore.db sintetico originale → True."""
        msgstore_path = Path("test_data/whatsapp_export/msgstore.db")
        assert msgstore_path.exists()
        assert importer.can_import(msgstore_path) is True

    def test_synthetic_msgstore_copied_as_evidence_csv(self, importer, tmp_path):
        """4b. Copia del msgstore.db sintetico originale come evidence.csv → True."""
        import shutil
        orig = Path("test_data/whatsapp_export/msgstore.db")
        dest = tmp_path / "evidence.csv"
        shutil.copy2(orig, dest)
        assert importer.can_import(dest) is True


# ---------------------------------------------------------------------------
# can_import — casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        """7. Path inesistente → False."""
        ghost = tmp_path / "ghost.db"
        assert importer.can_import(ghost) is False

    def test_returns_false_for_csv_extension(self, importer, tmp_path):
        """5. Vero CSV/testo (non SQLite) → False."""
        csv = tmp_path / "messages.csv"
        csv.write_text("id,text\n1,hello")
        assert importer.can_import(csv) is False

    def test_returns_false_for_json_extension(self, importer, tmp_path):
        """D. Vero file JSON (testo non SQLite) → False."""
        jsf = tmp_path / "data.json"
        jsf.write_text('{"key": "value"}')
        assert importer.can_import(jsf) is False

    def test_returns_false_for_xml_extension(self, importer, tmp_path):
        """D. Vero file XML (testo non SQLite) → False."""
        xf = tmp_path / "report.xml"
        xf.write_text("<root/>")
        assert importer.can_import(xf) is False

    def test_returns_false_for_generic_sqlite(self, importer, non_whatsapp_db):
        """C. SQLite generico senza schema WhatsApp → False."""
        assert importer.can_import(non_whatsapp_db) is False

    def test_returns_false_for_incomplete_schema(self, importer, incomplete_schema_db):
        """C. SQLite con tabelle WhatsApp ma colonne mancanti → False."""
        assert importer.can_import(incomplete_schema_db) is False

    def test_returns_false_for_plain_text_with_db_extension(self, importer, tmp_path):
        """D. File .db che non è SQLite → False (nessuna eccezione)."""
        fake = tmp_path / "fake.db"
        fake.write_text("questo non è un database SQLite")
        assert importer.can_import(fake) is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        """E. Directory invece di file → False."""
        assert importer.can_import(tmp_path) is False

    def test_does_not_raise_for_any_input(self, importer, tmp_path):
        """can_import() non deve mai sollevare eccezioni per qualsiasi input."""
        cases = [
            tmp_path / "nonexistent.db",
            tmp_path / "empty.db",
            tmp_path,              # directory invece di file
            tmp_path / "no_extension",
        ]
        (tmp_path / "empty.db").touch()
        (tmp_path / "no_extension").write_text("not sqlite")
        for path in cases:
            result = importer.can_import(path)
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# import_records — source errors
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsSourceErrors:

    def test_raises_file_not_found_for_nonexistent(self, importer, tmp_path):
        ghost = tmp_path / "ghost.db"
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(ghost))

    def test_raises_value_error_for_non_whatsapp_db(self, importer, non_whatsapp_db):
        with pytest.raises(ValueError, match="WhatsAppMsgstoreImporter"):
            list(importer.import_records(non_whatsapp_db))


# ---------------------------------------------------------------------------
# import_records — comportamento sul DB sintetico
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsOutput:

    def test_returns_iterator(self, importer, minimal_db):
        result = importer.import_records(minimal_db)
        assert hasattr(result, "__iter__")
        assert hasattr(result, "__next__")

    def test_yields_raw_records(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert isinstance(r, RawRecord)

    def test_total_record_count(self, importer, minimal_db):
        """3 messaggi + 1 chat + 0 media_refs (non inseriti nel fixture) = 4 record."""
        records = list(importer.import_records(minimal_db))
        assert len(records) == 4

    def test_source_name_is_msgstore_db(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert r.source_name == "msgstore_db"

    def test_source_path_matches_file(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert str(minimal_db.resolve()) in r.source_path

    def test_record_ids_are_unique(self, importer, minimal_db):
        """Ogni (record_type, source_record_id) deve essere unico."""
        records = list(importer.import_records(minimal_db))
        keys = [(r.record_type, r.source_record_id) for r in records]
        assert len(keys) == len(set(keys))

    def test_record_types_present(self, importer, minimal_db):
        types = {r.record_type for r in importer.import_records(minimal_db)}
        assert "message" in types
        assert "chat" in types

    def test_message_records_have_expected_fields(self, importer, minimal_db):
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        assert len(messages) == 3
        for msg in messages:
            assert "_id" in msg.raw_fields
            assert "key_remote_jid" in msg.raw_fields
            assert "key_from_me" in msg.raw_fields
            assert "timestamp" in msg.raw_fields
            assert "data" in msg.raw_fields
            assert "deleted" in msg.raw_fields

    def test_chat_records_have_expected_fields(self, importer, minimal_db):
        chats = [r for r in importer.import_records(minimal_db) if r.record_type == "chat"]
        assert len(chats) == 1
        chat = chats[0]
        assert "key_remote_jid" in chat.raw_fields
        assert chat.metadata["table"] == "chat_list"

    def test_source_record_id_is_string(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert isinstance(r.source_record_id, str)

    def test_metadata_contains_table(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert "table" in r.metadata
            assert r.metadata["table"] in ("messages", "chat_list", "media_refs")

    def test_metadata_contains_importer_name(self, importer, minimal_db):
        for r in importer.import_records(minimal_db):
            assert r.metadata["importer"] == "WhatsAppMsgstoreImporter"


# ---------------------------------------------------------------------------
# Timestamp — preservazione senza normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTimestampPreservation:

    def test_timestamp_is_raw_integer(self, importer, minimal_db):
        """timestamp deve essere il valore unix_ms raw dalla sorgente, non convertito."""
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            ts = msg.raw_fields["timestamp"]
            # Deve essere un intero (unix_ms) oppure None: mai una stringa o datetime
            assert isinstance(ts, (int, type(None)))

    def test_timestamp_not_converted_to_string(self, importer, minimal_db):
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            ts = msg.raw_fields["timestamp"]
            assert not isinstance(ts, str), "timestamp non deve essere convertito in stringa"

    def test_anomalous_timestamps_are_preserved(self, tmp_path):
        """Timestamp zero e negativi devono passare senza crash."""
        db = tmp_path / "msgstore.db"
        _make_minimal_msgstore(db, n_messages=0)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO messages (_id, key_remote_jid, key_from_me, key_id, "
            "status, needs_push, data, timestamp, media_url, media_mime_type, "
            "media_wa_type, media_size, media_name, media_caption, latitude, "
            "longitude, thumb_image, remote_resource, received_timestamp, "
            "send_timestamp, receipt_server_timestamp, receipt_device_timestamp, "
            "read_device_timestamp, played_device_timestamp, raw_data, "
            "recipient_count, participant_hash, starred, quoted_row_id, "
            "mentioned_jids, multicast_id, edit_version, media_enc_hash, "
            "payment_transaction_id, forwarded, preview_type, deleted) "
            "VALUES (999, 'synth@s.whatsapp.net', 0, 'KEY_ANOM', "
            "5, 0, 'msg anomalo', 0, NULL, NULL, 0, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, 0, NULL, NULL, NULL, 0, NULL, NULL, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO messages (_id, key_remote_jid, key_from_me, key_id, "
            "status, needs_push, data, timestamp, media_url, media_mime_type, "
            "media_wa_type, media_size, media_name, media_caption, latitude, "
            "longitude, thumb_image, remote_resource, received_timestamp, "
            "send_timestamp, receipt_server_timestamp, receipt_device_timestamp, "
            "read_device_timestamp, played_device_timestamp, raw_data, "
            "recipient_count, participant_hash, starred, quoted_row_id, "
            "mentioned_jids, multicast_id, edit_version, media_enc_hash, "
            "payment_transaction_id, forwarded, preview_type, deleted) "
            "VALUES (998, 'synth@s.whatsapp.net', 0, 'KEY_NEG', "
            "5, 0, 'msg negativo', -1000, NULL, NULL, 0, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL, -1000, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, 0, NULL, NULL, NULL, 0, NULL, NULL, 0, 0, 0)"
        )
        conn.commit()
        conn.close()

        imp = WhatsAppMsgstoreImporter()
        messages = [r for r in imp.import_records(db) if r.record_type == "message"]
        ts_values = {msg.raw_fields["_id"]: msg.raw_fields["timestamp"] for msg in messages}
        assert ts_values[999] == 0
        assert ts_values[998] == -1000


# ---------------------------------------------------------------------------
# JID — preservazione senza normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestJIDPreservation:

    def test_jid_preserved_as_is(self, importer, minimal_db):
        """key_remote_jid deve essere il valore raw della sorgente."""
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            jid = msg.raw_fields["key_remote_jid"]
            if jid is not None:
                assert isinstance(jid, str)
                # Non deve essere normalizzato: deve contenere @ o essere il JID raw
                # Il nostro sintetico usa @s.whatsapp.net
                assert "@" in jid or jid == ""

    def test_jid_not_resolved_to_phone(self, importer, minimal_db):
        """key_remote_jid non deve essere convertito in E.164."""
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            jid = msg.raw_fields.get("key_remote_jid", "")
            if jid:
                # E.164 puro non contiene "@"
                assert "@" in jid, f"JID sembra normalizzato: {jid}"


# ---------------------------------------------------------------------------
# Messaggi eliminati — preservazione flag raw
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDeletedPreservation:

    def test_deleted_field_preserved(self, importer, minimal_db):
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            assert "deleted" in msg.raw_fields
            assert isinstance(msg.raw_fields["deleted"], (int, type(None)))

    def test_deleted_not_converted_to_bool(self, importer, minimal_db):
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        for msg in messages:
            val = msg.raw_fields["deleted"]
            assert not isinstance(val, bool), "deleted non deve essere bool (deve restare int 0/1)"


# ---------------------------------------------------------------------------
# Tipi di messaggio — passthrough senza crash
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMessageTypePassthrough:

    def test_known_media_types_pass_through(self, tmp_path):
        """media_wa_type 0,1,2,3 e tipi sconosciuti devono tutti produrre RawRecord senza crash."""
        db = tmp_path / "msgstore.db"
        _make_minimal_msgstore(db, n_messages=0)
        conn = sqlite3.connect(db)
        for media_type in [0, 1, 2, 3, 99]:  # 99 = tipo sconosciuto
            conn.execute(
                "INSERT INTO messages "
                "(_id, key_remote_jid, key_from_me, key_id, status, needs_push, "
                "data, timestamp, media_url, media_mime_type, media_wa_type, "
                "media_size, media_name, media_caption, latitude, longitude, "
                "thumb_image, remote_resource, received_timestamp, send_timestamp, "
                "receipt_server_timestamp, receipt_device_timestamp, "
                "read_device_timestamp, played_device_timestamp, raw_data, "
                "recipient_count, participant_hash, starred, quoted_row_id, "
                "mentioned_jids, multicast_id, edit_version, media_enc_hash, "
                "payment_transaction_id, forwarded, preview_type, deleted) "
                "VALUES (?, 'synth@s.whatsapp.net', 0, ?, "
                "5, 0, 'testo sintetico', 1700000000000, NULL, NULL, ?, "
                "NULL, NULL, NULL, NULL, NULL, "
                "NULL, NULL, 1700000000500, NULL, "
                "NULL, NULL, NULL, NULL, NULL, "
                "NULL, NULL, 0, NULL, "
                "NULL, NULL, 0, NULL, "
                "NULL, 0, 0, 0)",
                (media_type, f"KEY_{media_type:06d}", media_type),
            )
        conn.commit()
        conn.close()

        imp = WhatsAppMsgstoreImporter()
        messages = [r for r in imp.import_records(db) if r.record_type == "message"]
        observed_types = {msg.raw_fields["media_wa_type"] for msg in messages}
        assert 0 in observed_types
        assert 1 in observed_types
        assert 99 in observed_types  # tipo sconosciuto non causa crash
        assert len(messages) == 5


# ---------------------------------------------------------------------------
# Media reference
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMediaReference:

    def test_media_reference_none_for_text_messages(self, importer, minimal_db):
        """I messaggi di testo (media_wa_type=0, media_url=None) hanno media_reference=None."""
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        text_messages = [m for m in messages if m.raw_fields.get("media_url") is None]
        for msg in text_messages:
            assert msg.media_reference is None

    def test_media_reference_populated_when_url_present(self, tmp_path):
        """media_reference deve essere popolato quando media_url è presente."""
        db = tmp_path / "msgstore.db"
        _make_minimal_msgstore(db, n_messages=0)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO messages "
            "(_id, key_remote_jid, key_from_me, key_id, status, needs_push, "
            "data, timestamp, media_url, media_mime_type, media_wa_type, "
            "media_size, media_name, media_caption, latitude, longitude, "
            "thumb_image, remote_resource, received_timestamp, send_timestamp, "
            "receipt_server_timestamp, receipt_device_timestamp, "
            "read_device_timestamp, played_device_timestamp, raw_data, "
            "recipient_count, participant_hash, starred, quoted_row_id, "
            "mentioned_jids, multicast_id, edit_version, media_enc_hash, "
            "payment_transaction_id, forwarded, preview_type, deleted) "
            "VALUES (10, 'synth@s.whatsapp.net', 0, 'KEY_MEDIA', 5, 0, "
            "NULL, 1700000000000, 'WhatsApp Images/IMG_synth.jpg', 'image/jpeg', "
            "1, 12345, 'IMG_synth.jpg', NULL, NULL, NULL, NULL, NULL, "
            "1700000000500, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "0, NULL, NULL, NULL, 0, NULL, NULL, 0, 0, 0)"
        )
        conn.commit()
        conn.close()

        imp = WhatsAppMsgstoreImporter()
        messages = [r for r in imp.import_records(db) if r.record_type == "message"]
        assert len(messages) == 1
        assert messages[0].media_reference == "WhatsApp Images/IMG_synth.jpg"
        assert messages[0].has_media() is True


# ---------------------------------------------------------------------------
# Read-only — il DB non viene modificato
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReadOnly:

    def test_db_mtime_unchanged_after_import(self, importer, minimal_db):
        """Il file DB non deve essere modificato durante l'import."""
        import os
        mtime_before = os.path.getmtime(minimal_db)
        list(importer.import_records(minimal_db))
        mtime_after = os.path.getmtime(minimal_db)
        assert mtime_before == mtime_after

    def test_db_size_unchanged_after_import(self, importer, minimal_db):
        """La dimensione del file non deve cambiare."""
        size_before = minimal_db.stat().st_size
        list(importer.import_records(minimal_db))
        size_after = minimal_db.stat().st_size
        assert size_before == size_after

    def test_cannot_write_via_readonly_connection(self, minimal_db):
        """
        La connessione ?mode=ro deve impedire scritture a livello SQLite.
        Questo testa il meccanismo di protezione, non il comportamento dell'importer.
        """
        from importer.whatsapp_msgstore import _open_readonly
        conn = _open_readonly(minimal_db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO messages (_id) VALUES (99999)")
        conn.close()


# ---------------------------------------------------------------------------
# Streaming — nessun fetchall sull'intero dataset
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStreaming:

    def test_import_is_generator(self, importer, minimal_db):
        """import_records() deve restituire un generatore, non una lista."""
        import types
        result = importer.import_records(minimal_db)
        assert isinstance(result, types.GeneratorType)

    def test_records_available_incrementally(self, importer, minimal_db):
        """
        Il generatore deve produrre record senza richiedere il completamento dell'intera iterazione.
        Verifica che il primo record sia disponibile senza consumare il generatore.
        """
        gen = importer.import_records(minimal_db)
        first = next(gen)  # deve essere disponibile immediatamente
        assert isinstance(first, RawRecord)
        # Non consumiamo il resto — il generatore deve restare aperto
        del gen  # cleanup


# ---------------------------------------------------------------------------
# Gestione errori — resilienza per-record
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestErrorHandling:

    def test_empty_db_yields_only_chat_records(self, tmp_path):
        """Un msgstore.db con 0 messaggi deve produrre solo i record di chat (se presenti)."""
        db = tmp_path / "empty_messages.db"
        _make_minimal_msgstore(db, n_messages=0)
        imp = WhatsAppMsgstoreImporter()
        records = list(imp.import_records(db))
        types_found = {r.record_type for r in records}
        # Solo "chat" (1 record nel fixture), nessun messaggio
        assert "message" not in types_found
        assert "chat" in types_found

    def test_null_fields_preserved_without_crash(self, tmp_path):
        """Record con molti campi NULL non deve causare eccezioni."""
        db = tmp_path / "nulls.db"
        _make_minimal_msgstore(db, n_messages=0)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO messages "
            "(_id, key_remote_jid, key_from_me, key_id, "
            "status, needs_push, data, timestamp, "
            "media_url, media_mime_type, media_wa_type, "
            "media_size, media_name, media_caption, latitude, longitude, "
            "thumb_image, remote_resource, received_timestamp, send_timestamp, "
            "receipt_server_timestamp, receipt_device_timestamp, "
            "read_device_timestamp, played_device_timestamp, raw_data, "
            "recipient_count, participant_hash, starred, quoted_row_id, "
            "mentioned_jids, multicast_id, edit_version, media_enc_hash, "
            "payment_transaction_id, forwarded, preview_type, deleted) "
            "VALUES (77, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
            "NULL, NULL, NULL, NULL)"
        )
        conn.commit()
        conn.close()

        imp = WhatsAppMsgstoreImporter()
        messages = [r for r in imp.import_records(db) if r.record_type == "message"]
        assert len(messages) == 1
        msg = messages[0]
        assert msg.raw_fields["_id"] == 77
        assert msg.raw_fields["key_remote_jid"] is None
        assert msg.raw_fields["data"] is None
        assert msg.raw_fields["timestamp"] is None
        assert msg.media_reference is None

    def test_no_normalization_in_raw_fields(self, importer, minimal_db):
        """
        I valori in raw_fields devono essere identici a quelli nel DB.
        Nessuna conversione di tipo, nessuna normalizzazione.
        """
        messages = [r for r in importer.import_records(minimal_db) if r.record_type == "message"]
        conn = sqlite3.connect(minimal_db)
        conn.row_factory = sqlite3.Row
        for msg in messages:
            db_row = conn.execute(
                "SELECT * FROM messages WHERE _id=?",
                (int(msg.source_record_id),),
            ).fetchone()
            assert db_row is not None
            for col in db_row.keys():
                assert msg.raw_fields[col] == db_row[col], (
                    f"Campo {col}: atteso {db_row[col]!r}, trovato {msg.raw_fields[col]!r}"
                )
        conn.close()
