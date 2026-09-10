"""
tests/integration/test_cellebrite_csv_integration.py
---------------------------------------------------
Test di INTEGRAZIONE per CellebriteCsvImporter.

Usa il file sintetico reale in test_data/cellebrite_export/messages.csv.
Nessun dato forense reale — tutti i dati sono fittizi e conformi.

ATTENZIONE: questi test aprono il file in sola lettura.
Il file non viene mai modificato.
"""
from __future__ import annotations

import csv
import inspect
import os
from itertools import islice
from pathlib import Path

import pytest

from importer.cellebrite_csv import CellebriteCsvImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter

# ---------------------------------------------------------------------------
# Path al CSV sintetico Cellebrite
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CELLEBRITE_DIR = _PROJECT_ROOT / "test_data" / "cellebrite_export"
_MESSAGES_CSV = _CELLEBRITE_DIR / "messages.csv"
_MESSAGES_JSON = _CELLEBRITE_DIR / "messages.json"
_REPORT_XML = _CELLEBRITE_DIR / "report.xml"
_MSGSTORE_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "msgstore.db"
_WA_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "wa.db"

pytestmark = pytest.mark.integration

if not _MESSAGES_CSV.exists():
    pytestmark = pytest.mark.skip(reason=f"File sintetico non trovato: {_MESSAGES_CSV}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def importer():
    return CellebriteCsvImporter()


@pytest.fixture(scope="module")
def all_records(importer):
    """Carica tutti i record da messages.csv una sola volta per il modulo."""
    return list(importer.import_records(_MESSAGES_CSV))


# ---------------------------------------------------------------------------
# Riconoscimento sorgente
# ---------------------------------------------------------------------------

class TestCellebriteCsvRecognition:

    def test_can_import_returns_true(self, importer):
        assert importer.can_import(_MESSAGES_CSV) is True

    def test_csv_exists_and_is_file(self):
        assert _MESSAGES_CSV.exists()
        assert _MESSAGES_CSV.is_file()

    def test_rejects_other_cellebrite_formats(self, importer):
        if _MESSAGES_JSON.exists():
            assert importer.can_import(_MESSAGES_JSON) is False
        if _REPORT_XML.exists():
            assert importer.can_import(_REPORT_XML) is False

    def test_rejects_whatsapp_databases(self, importer):
        if _MSGSTORE_DB.exists():
            assert importer.can_import(_MSGSTORE_DB) is False
        if _WA_DB.exists():
            assert importer.can_import(_WA_DB) is False


# ---------------------------------------------------------------------------
# Discriminazione cross-importer
# ---------------------------------------------------------------------------

class TestCrossImporterDiscrimination:

    def test_msgstore_importer_rejects_cellebrite_csv(self):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(_MESSAGES_CSV) is False

    def test_wa_importer_rejects_cellebrite_csv(self):
        wa_imp = WhatsAppWaDbImporter()
        assert wa_imp.can_import(_MESSAGES_CSV) is False


# ---------------------------------------------------------------------------
# Conteggio e tipi di record
# ---------------------------------------------------------------------------

class TestCellebriteCsvRecordCountAndTypes:

    def test_total_record_count_is_302(self, all_records):
        """Nel CSV sintetico sono presenti esattamente 302 righe di messaggi."""
        assert len(all_records) == 302

    def test_all_records_are_raw_records(self, all_records):
        assert all(isinstance(r, RawRecord) for r in all_records)

    def test_all_records_are_message_type(self, all_records):
        assert all(r.record_type == "message" for r in all_records)

    def test_source_name_is_cellebrite_csv(self, all_records):
        assert all(r.source_name == "cellebrite_csv" for r in all_records)

    def test_source_record_ids_are_row_2_to_303(self, all_records):
        ids = [r.source_record_id for r in all_records]
        expected_ids = [f"row:{i}" for i in range(2, 304)]
        assert ids == expected_ids
        assert len(set(ids)) == 302

    def test_source_path_is_messages_csv_path(self, all_records):
        expected_path = str(_MESSAGES_CSV.resolve())
        assert all(r.source_path == expected_path for r in all_records)

    def test_metadata_contains_provenance_info(self, all_records):
        for idx, r in enumerate(all_records, start=2):
            assert r.metadata.get("importer") == "CellebriteCsvImporter"
            assert r.metadata.get("table") == "messages"
            assert r.metadata.get("row_number") == idx

# ---------------------------------------------------------------------------
# Fedeltà dei dati source-level (nessuna normalizzazione semantica)
# ---------------------------------------------------------------------------

class TestCellebriteCsvDataFaithfulness:

    def test_chat_ids_preserved_verbatim(self, all_records):
        chat_ids = {r.raw_fields["ChatId"] for r in all_records}
        assert chat_ids == {"chat_1", "chat_2", "chat_3"}

    def test_directions_preserved_verbatim(self, all_records):
        directions = {r.raw_fields["Direction"] for r in all_records}
        assert directions == {"Incoming", "Outgoing"}

    def test_timestamps_preserved_raw_without_timezone_conversion(self, all_records):
        """I timestamp devono essere preservati come stringhe grezze, no datetime/UTC (incluso stringa vuota)."""
        for r in all_records:
            ts = r.raw_fields["TimeStamp"]
            assert isinstance(ts, str)

    def test_media_references_correspond_to_attachments(self, all_records):
        non_empty_attachments = 0
        for r in all_records:
            att = r.raw_fields["Attachments"]
            if att != "":
                assert r.media_reference == att
                non_empty_attachments += 1
            else:
                assert r.media_reference is None
        assert non_empty_attachments == 25

    def test_deleted_flag_preserved_as_raw_string(self, all_records):
        deleted_values = {r.raw_fields["Deleted"] for r in all_records}
        assert deleted_values.issubset({"True", "False", ""})

    def test_raw_fields_match_direct_csv_reader_verbatim(self, all_records):
        """Ogni campo in raw_fields deve corrispondere esattamente alla lettura diretta con csv.DictReader."""
        with open(_MESSAGES_CSV, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            direct_rows = list(reader)

        assert len(all_records) == len(direct_rows)
        for r, d in zip(all_records, direct_rows):
            assert r.raw_fields == d


# ---------------------------------------------------------------------------
# Assenza di normalizzazione semantica ed Entity Resolution
# ---------------------------------------------------------------------------

class TestNoEntityResolutionOrSemanticNormalization:

    def test_chat_ids_not_resolved_to_jids(self, all_records):
        """Nessuna mappatura 'chat_1' -> JID WhatsApp deve avvenire nell'importer."""
        for r in all_records:
            assert not r.raw_fields["ChatId"].endswith("@s.whatsapp.net")
            assert not r.raw_fields["ChatId"].endswith("@g.us")

    def test_group_participant_aliases_not_resolved(self, all_records):
        """Nessuna risoluzione di group_participant_A verso numeri o contatti."""
        from_values = {r.raw_fields["From"] for r in all_records}
        assert any("group_participant" in v for v in from_values)

    def test_no_unified_message_fields_injected(self, all_records):
        """Nessun campo di UnifiedMessage (es. sender_id, text_content, utc_timestamp) presente."""
        for r in all_records:
            assert "sender_id" not in r.raw_fields
            assert "text_content" not in r.raw_fields
            assert "utc_timestamp" not in r.raw_fields


# ---------------------------------------------------------------------------
# Streaming e Read-Only
# ---------------------------------------------------------------------------

class TestStreamingAndReadOnly:

    def test_import_records_returns_generator(self, importer):
        gen = importer.import_records(_MESSAGES_CSV)
        assert inspect.isgenerator(gen)
        # Consuma un elemento e chiudi
        first = next(gen)
        assert isinstance(first, RawRecord)
        gen.close()

    def test_streaming_partial_consumption(self, importer):
        records = list(islice(importer.import_records(_MESSAGES_CSV), 5))
        assert len(records) == 5
        assert [r.source_record_id for r in records] == [f"row:{i}" for i in range(2, 7)]

    def test_mtime_unchanged_after_reading(self, importer):
        mtime_before = os.path.getmtime(_MESSAGES_CSV)
        list(importer.import_records(_MESSAGES_CSV))
        mtime_after = os.path.getmtime(_MESSAGES_CSV)
        assert mtime_before == mtime_after

    def test_size_unchanged_after_reading(self, importer):
        size_before = _MESSAGES_CSV.stat().st_size
        list(importer.import_records(_MESSAGES_CSV))
        size_after = _MESSAGES_CSV.stat().st_size
        assert size_before == size_after
