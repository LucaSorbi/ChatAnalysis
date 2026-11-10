"""
tests/integration/test_whatsapp_wa_integration.py
-------------------------------------------------
Test di INTEGRAZIONE per WhatsAppWaDbImporter.

Usa il database sintetico reale presente in test_data/whatsapp_export/wa.db.
Nessun dato forense reale — tutti i contatti sono fittizi.

ATTENZIONE: questi test aprono test_data/whatsapp_export/wa.db in modalità
read-only (?mode=ro). Il database non viene mai modificato.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter

# ---------------------------------------------------------------------------
# Path al DB sintetico wa.db
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WA_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "wa.db"
_MSGSTORE_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "msgstore.db"

pytestmark = pytest.mark.integration

if not _WA_DB.exists():
    pytestmark = pytest.mark.skip(reason=f"DB sintetico non trovato: {_WA_DB}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def importer():
    return WhatsAppWaDbImporter()


@pytest.fixture(scope="module")
def all_records(importer):
    """Carica tutti i record da wa.db una sola volta per il modulo."""
    return list(importer.import_records(_WA_DB))


# ---------------------------------------------------------------------------
# Riconoscimento sorgente
# ---------------------------------------------------------------------------

class TestWaDbRecognition:

    def test_can_import_returns_true(self, importer):
        assert importer.can_import(_WA_DB) is True

    def test_db_exists_and_is_file(self):
        assert _WA_DB.exists()
        assert _WA_DB.is_file()


# ---------------------------------------------------------------------------
# Conteggio e tipi di record
# ---------------------------------------------------------------------------

class TestWaDbRecordCountAndTypes:

    def test_total_record_count_is_four(self, all_records):
        """Nel database wa.db sintetico sono presenti esattamente 4 contatti."""
        assert len(all_records) == 4

    def test_all_records_are_raw_records(self, all_records):
        assert all(isinstance(r, RawRecord) for r in all_records)

    def test_all_records_are_contact_type(self, all_records):
        assert all(r.record_type == "contact" for r in all_records)

    def test_source_record_ids_are_one_to_four(self, all_records):
        ids = [r.source_record_id for r in all_records]
        assert ids == ["1", "2", "3", "4"]

    def test_source_name_is_wa_db(self, all_records):
        assert all(r.source_name == "wa_db" for r in all_records)

    def test_source_path_is_wa_db_path(self, all_records):
        expected_path = str(_WA_DB.resolve())
        assert all(r.source_path == expected_path for r in all_records)

    def test_media_reference_is_none(self, all_records):
        assert all(r.media_reference is None for r in all_records)

    def test_metadata_table_is_contacts(self, all_records):
        for r in all_records:
            assert r.metadata.get("table") == "contacts"
            assert r.metadata.get("importer") == "WhatsAppWaDbImporter"


# ---------------------------------------------------------------------------
# Fedeltà dei dati source-level (nessuna normalizzazione)
# ---------------------------------------------------------------------------

class TestWaDbDataFaithfulness:

    def test_contact_names_match_database(self, all_records):
        names = {r.raw_fields["display_name"] for r in all_records}
        expected = {"Contatto_001", "Contatto_002", "Contatto_003", "Contatto_004"}
        assert names == expected

    def test_jids_match_database(self, all_records):
        jids = {r.raw_fields["jid"] for r in all_records}
        expected = {
            "+390000000001@s.whatsapp.net",
            "+390000000002@s.whatsapp.net",
            "+390000000003@s.whatsapp.net",
            "+10000000004@s.whatsapp.net",
        }
        assert jids == expected

    def test_phone_numbers_match_database_verbatim(self, all_records):
        """I numeri conservano la formattazione originale con spazi (no E.164)."""
        numbers = {r.raw_fields["phone_number"] for r in all_records}
        expected = {
            "+39 000 0000001",
            "+39 000 0000002",
            "+39 000 0000003",
            "+1 000 0000004",
        }
        assert numbers == expected

    def test_contact_2_status_is_none(self, all_records):
        rec2 = next(r for r in all_records if r.source_record_id == "2")
        assert rec2.raw_fields["display_name"] == "Contatto_002"
        assert rec2.raw_fields["status"] is None

    def test_all_raw_fields_match_direct_sqlite_query(self, all_records):
        """Ogni campo in raw_fields deve coincidere con la query diretta in read-only."""
        conn = sqlite3.connect(f"file:{_WA_DB.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        for r in all_records:
            row = cur.execute(
                "SELECT * FROM contacts WHERE _id=?", (int(r.source_record_id),)
            ).fetchone()
            assert row is not None
            for col in row.keys():
                assert r.raw_fields[col] == row[col]
        conn.close()


# ---------------------------------------------------------------------------
# Separazione assoluta da msgstore.db
# ---------------------------------------------------------------------------

class TestAbsoluteSeparationFromMsgstore:

    def test_no_msgstore_linkage_or_enrichment(self, all_records):
        """I record emessi da wa.db non devono contenere dati o riferimenti a messaggi."""
        for r in all_records:
            assert "messages" not in r.metadata.get("table", "")
            assert "key_id" not in r.raw_fields
            assert "data" not in r.raw_fields
            assert "timestamp" not in r.raw_fields

    def test_msgstore_importer_rejects_wa_db(self):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(_WA_DB) is False

    def test_wa_importer_rejects_msgstore_db(self, importer):
        assert importer.can_import(_MSGSTORE_DB) is False


# ---------------------------------------------------------------------------
# Read-Only e integrità del database
# ---------------------------------------------------------------------------

class TestWaDbIntegrityAndReadOnly:

    def test_mtime_unchanged_after_reading(self, importer):
        mtime_before = os.path.getmtime(_WA_DB)
        list(importer.import_records(_WA_DB))
        mtime_after = os.path.getmtime(_WA_DB)
        assert mtime_before == mtime_after

    def test_size_unchanged_after_reading(self, importer):
        size_before = _WA_DB.stat().st_size
        list(importer.import_records(_WA_DB))
        size_after = _WA_DB.stat().st_size
        assert size_before == size_after

    def test_no_journal_or_wal_files_created(self):
        parent = _WA_DB.parent
        wal = parent / "wa.db-wal"
        shm = parent / "wa.db-shm"
        journal = parent / "wa.db-journal"
        assert not wal.exists()
        assert not shm.exists()
        assert not journal.exists()
