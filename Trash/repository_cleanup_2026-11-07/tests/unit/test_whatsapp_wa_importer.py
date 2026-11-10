"""
tests/unit/test_whatsapp_wa_importer.py
---------------------------------------
Test UNITARI per WhatsAppWaDbImporter (wa.db — contatti Android WhatsApp).

Principio: test isolati con DB SQLite sintetici in tmp_path.
Nessun dato forense reale.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter


# ---------------------------------------------------------------------------
# Helpers: crea DB temporanei sintetici con schema wa.db
# ---------------------------------------------------------------------------

def _make_minimal_wa_db(path: Path, n_contacts: int = 4) -> None:
    """Crea un wa.db sintetico minimale con schema WhatsApp contacts."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE contacts (
            _id          INTEGER PRIMARY KEY,
            jid          TEXT,
            display_name TEXT,
            status       TEXT,
            phone_number TEXT
        );
    """)
    samples = [
        (1, "+390000000001@s.whatsapp.net", "Contatto_001", "Status sintetico test 01", "+39 000 0000001"),
        (2, "+390000000002@s.whatsapp.net", "Contatto_002", None, "+39 000 0000002"),
        (3, "+390000000003@s.whatsapp.net", "Contatto_003", "Status sintetico test 03", "+39 000 0000003"),
        (4, "+10000000004@s.whatsapp.net", "Contatto_004", "Status sintetico test 04", "+1 000 0000004"),
    ]
    for row in samples[:n_contacts]:
        conn.execute(
            "INSERT INTO contacts (_id, jid, display_name, status, phone_number) VALUES (?, ?, ?, ?, ?)",
            row,
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


def _make_wa_db_missing_columns(path: Path) -> None:
    """Crea un SQLite con tabella contacts ma colonne chiave mancanti."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE contacts (_id INTEGER PRIMARY KEY, notes TEXT)")
    conn.commit()
    conn.close()


def _make_minimal_msgstore(path: Path) -> None:
    """Crea un msgstore.db minimale per test di discriminazione incrociata."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chat_list (_id INTEGER PRIMARY KEY, key_remote_jid TEXT);
        CREATE TABLE messages (
            _id INTEGER PRIMARY KEY,
            key_remote_jid TEXT,
            key_from_me INTEGER,
            key_id TEXT
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return WhatsAppWaDbImporter()


@pytest.fixture
def minimal_wa_db(tmp_path):
    """DB temporaneo con schema wa.db e 4 contatti sintetici."""
    db = tmp_path / "wa.db"
    _make_minimal_wa_db(db, n_contacts=4)
    return db


@pytest.fixture
def non_whatsapp_db(tmp_path):
    """DB SQLite generico."""
    db = tmp_path / "generic.db"
    _make_non_whatsapp_sqlite(db)
    return db


@pytest.fixture
def incomplete_schema_db(tmp_path):
    """DB SQLite con tabella contacts ma colonne mancanti."""
    db = tmp_path / "partial_wa.db"
    _make_wa_db_missing_columns(db)
    return db


@pytest.fixture
def minimal_msgstore_db(tmp_path):
    """DB SQLite minimale msgstore.db per test discriminazione."""
    db = tmp_path / "msgstore.db"
    _make_minimal_msgstore(db)
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
        assert importer.source_name == "wa_db"

    def test_repr_contains_class_name(self, importer):
        assert "WhatsAppWaDbImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "wa_db" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportPositive:

    def test_returns_true_for_valid_wa_db(self, importer, minimal_wa_db):
        """DB sintetico minimale con schema wa.db → True."""
        assert importer.can_import(minimal_wa_db) is True

    def test_returns_bool(self, importer, minimal_wa_db):
        result = importer.can_import(minimal_wa_db)
        assert isinstance(result, bool)

    def test_idempotent(self, importer, minimal_wa_db):
        r1 = importer.can_import(minimal_wa_db)
        r2 = importer.can_import(minimal_wa_db)
        assert r1 == r2

    def test_renamed_db_no_extension(self, importer, minimal_wa_db, tmp_path):
        """Copia del DB rinominata senza estensione → True."""
        renamed = tmp_path / "wa_contacts_backup"
        shutil.copy2(minimal_wa_db, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_db_unusual_extension(self, importer, minimal_wa_db, tmp_path):
        """Copia del DB con estensione .bak → True."""
        renamed = tmp_path / "wa.bak"
        shutil.copy2(minimal_wa_db, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_db_csv_extension(self, importer, minimal_wa_db, tmp_path):
        """Copia dello stesso DB chiamata evidence.csv → True."""
        renamed = tmp_path / "contacts_evidence.csv"
        shutil.copy2(minimal_wa_db, renamed)
        assert importer.can_import(renamed) is True

    def test_synthetic_original_wa_db_returns_true(self, importer):
        """test_data/whatsapp_export/wa.db sintetico originale → True."""
        real_wa = Path("test_data/whatsapp_export/wa.db")
        assert real_wa.exists()
        assert importer.can_import(real_wa) is True

    def test_synthetic_wa_db_copied_as_evidence_csv(self, importer, tmp_path):
        """Copia del wa.db sintetico originale come evidence.csv → True."""
        real_wa = Path("test_data/whatsapp_export/wa.db")
        dest = tmp_path / "evidence.csv"
        shutil.copy2(real_wa, dest)
        assert importer.can_import(dest) is True


# ---------------------------------------------------------------------------
# can_import — casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        ghost = tmp_path / "ghost_wa.db"
        assert importer.can_import(ghost) is False

    def test_returns_false_for_csv_text(self, importer, tmp_path):
        csv_file = tmp_path / "contacts.csv"
        csv_file.write_text("id,name\n1,Mario")
        assert importer.can_import(csv_file) is False

    def test_returns_false_for_json_text(self, importer, tmp_path):
        jf = tmp_path / "contacts.json"
        jf.write_text('[{"id": 1, "name": "Mario"}]')
        assert importer.can_import(jf) is False

    def test_returns_false_for_xml_text(self, importer, tmp_path):
        xf = tmp_path / "contacts.xml"
        xf.write_text("<contacts/>")
        assert importer.can_import(xf) is False

    def test_returns_false_for_generic_sqlite(self, importer, non_whatsapp_db):
        assert importer.can_import(non_whatsapp_db) is False

    def test_returns_false_for_incomplete_schema(self, importer, incomplete_schema_db):
        assert importer.can_import(incomplete_schema_db) is False

    def test_returns_false_for_plain_text_with_db_extension(self, importer, tmp_path):
        fake = tmp_path / "fake_wa.db"
        fake.write_text("non sono un database sqlite")
        assert importer.can_import(fake) is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        assert importer.can_import(tmp_path) is False

    def test_returns_false_for_msgstore_db(self, importer, minimal_msgstore_db):
        """msgstore.db ha tabelle messages/chat_list ma non contacts → False."""
        assert importer.can_import(minimal_msgstore_db) is False

    def test_does_not_raise_for_any_input(self, importer, tmp_path):
        cases = [
            tmp_path / "missing.db",
            tmp_path,
            tmp_path / "empty.db",
        ]
        (tmp_path / "empty.db").touch()
        for p in cases:
            assert isinstance(importer.can_import(p), bool)

    def test_programmatic_error_propagates_in_can_import(self, importer, tmp_path, monkeypatch):
        """Un bug programmatico inatteso (es. TypeError) in can_import NON deve essere mascherato."""
        valid_file = tmp_path / "test_wa.db"
        valid_file.touch()

        def _buggy_schema(conn):
            raise TypeError("Bug programmatico inatteso wa.db")

        monkeypatch.setattr(importer, "_has_required_schema", _buggy_schema)
        with pytest.raises(TypeError, match="Bug programmatico inatteso wa.db"):
            importer.can_import(valid_file)


# ---------------------------------------------------------------------------
# Cross-importer discrimination
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrossImporterDiscrimination:

    def test_msgstore_importer_accepts_msgstore_rejects_wa(
        self, minimal_msgstore_db, minimal_wa_db
    ):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(minimal_msgstore_db) is True
        assert msgstore_imp.can_import(minimal_wa_db) is False

    def test_wa_importer_accepts_wa_rejects_msgstore(
        self, minimal_msgstore_db, minimal_wa_db
    ):
        wa_imp = WhatsAppWaDbImporter()
        assert wa_imp.can_import(minimal_wa_db) is True
        assert wa_imp.can_import(minimal_msgstore_db) is False


# ---------------------------------------------------------------------------
# import_records — errori sorgente
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsSourceErrors:

    def test_raises_file_not_found_for_nonexistent(self, importer, tmp_path):
        ghost = tmp_path / "ghost.db"
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(ghost))

    def test_raises_value_error_for_non_wa_db(self, importer, non_whatsapp_db):
        with pytest.raises(ValueError):
            list(importer.import_records(non_whatsapp_db))

    def test_raises_value_error_for_msgstore_db(self, importer, minimal_msgstore_db):
        with pytest.raises(ValueError):
            list(importer.import_records(minimal_msgstore_db))


# ---------------------------------------------------------------------------
# import_records — output e struttura RawRecord
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsOutput:

    def test_returns_iterator(self, importer, minimal_wa_db):
        gen = importer.import_records(minimal_wa_db)
        assert isinstance(gen, Iterator)

    def test_yields_raw_records(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert all(isinstance(r, RawRecord) for r in records)

    def test_total_record_count(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert len(records) == 4

    def test_source_name_is_wa_db(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert all(r.source_name == "wa_db" for r in records)

    def test_source_path_matches_file(self, importer, minimal_wa_db):
        expected_path = str(minimal_wa_db.resolve())
        records = list(importer.import_records(minimal_wa_db))
        assert all(r.source_path == expected_path for r in records)

    def test_record_ids_are_unique(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        ids = [r.source_record_id for r in records]
        assert len(ids) == len(set(ids))

    def test_record_type_is_contact(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert all(r.record_type == "contact" for r in records)

    def test_source_record_id_is_string(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert all(isinstance(r.source_record_id, str) for r in records)
        assert [r.source_record_id for r in records] == ["1", "2", "3", "4"]

    def test_raw_fields_contain_schema_columns(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        expected_cols = {"_id", "jid", "display_name", "status", "phone_number"}
        for r in records:
            assert expected_cols.issubset(r.raw_fields.keys())

    def test_media_reference_is_none(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        assert all(r.media_reference is None for r in records)

    def test_metadata_contains_table_and_importer(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        for r in records:
            assert r.metadata.get("table") == "contacts"
            assert r.metadata.get("importer") == "WhatsAppWaDbImporter"
            assert "importer_version" in r.metadata


# ---------------------------------------------------------------------------
# Preservazione fedele e assenza di normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValuePreservation:

    def test_jid_preserved_as_is(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        jids = [r.raw_fields["jid"] for r in records]
        assert "+390000000001@s.whatsapp.net" in jids
        assert "+10000000004@s.whatsapp.net" in jids

    def test_phone_number_preserved_no_e164_normalization(self, importer, minimal_wa_db):
        """I numeri devono mantenere gli spazi e la formattazione originale."""
        records = list(importer.import_records(minimal_wa_db))
        numbers = [r.raw_fields["phone_number"] for r in records]
        assert "+39 000 0000001" in numbers
        assert "+1 000 0000004" in numbers

    def test_display_name_preserved(self, importer, minimal_wa_db):
        records = list(importer.import_records(minimal_wa_db))
        names = [r.raw_fields["display_name"] for r in records]
        assert "Contatto_001" in names
        assert "Contatto_002" in names

    def test_null_status_preserved(self, importer, minimal_wa_db):
        """Il contatto 2 ha status NULL: deve restare None."""
        records = list(importer.import_records(minimal_wa_db))
        rec2 = next(r for r in records if r.source_record_id == "2")
        assert rec2.raw_fields["status"] is None

    def test_no_normalization_in_raw_fields(self, importer, minimal_wa_db):
        """I campi in raw_fields devono corrispondere esattamente alla riga SQLite."""
        records = list(importer.import_records(minimal_wa_db))
        conn = sqlite3.connect(minimal_wa_db)
        conn.row_factory = sqlite3.Row
        for rec in records:
            row = conn.execute(
                "SELECT * FROM contacts WHERE _id=?", (int(rec.source_record_id),)
            ).fetchone()
            assert row is not None
            for col in row.keys():
                assert rec.raw_fields[col] == row[col]
        conn.close()


# ---------------------------------------------------------------------------
# Read-Only
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReadOnly:

    def test_db_mtime_unchanged_after_import(self, importer, minimal_wa_db):
        mtime_before = os.path.getmtime(minimal_wa_db)
        list(importer.import_records(minimal_wa_db))
        mtime_after = os.path.getmtime(minimal_wa_db)
        assert mtime_before == mtime_after

    def test_db_size_unchanged_after_import(self, importer, minimal_wa_db):
        size_before = minimal_wa_db.stat().st_size
        list(importer.import_records(minimal_wa_db))
        size_after = minimal_wa_db.stat().st_size
        assert size_before == size_after

    def test_cannot_write_via_readonly_connection(self, minimal_wa_db):
        from importer.whatsapp_wa import _open_readonly
        conn = _open_readonly(minimal_wa_db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO contacts (_id, jid) VALUES (99, 'test@s.whatsapp.net')")
        conn.close()


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStreaming:

    def test_import_is_generator(self, importer, minimal_wa_db):
        gen = importer.import_records(minimal_wa_db)
        import inspect
        assert inspect.isgenerator(gen)

    def test_records_available_incrementally(self, importer, minimal_wa_db):
        gen = importer.import_records(minimal_wa_db)
        first = next(gen)
        assert isinstance(first, RawRecord)
        assert first.source_record_id == "1"


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestErrorHandling:

    def test_empty_contacts_table_yields_no_records(self, importer, tmp_path):
        db = tmp_path / "empty_contacts.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE contacts (
                _id INTEGER PRIMARY KEY,
                jid TEXT,
                display_name TEXT,
                status TEXT,
                phone_number TEXT
            );
        """)
        conn.commit()
        conn.close()

        records = list(importer.import_records(db))
        assert records == []
