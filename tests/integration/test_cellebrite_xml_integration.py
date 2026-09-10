"""
tests/integration/test_cellebrite_xml_integration.py
----------------------------------------------------
Test di INTEGRAZIONE per CellebriteXmlImporter.

Usa il file sintetico reale in test_data/cellebrite_export/report.xml.
Nessun dato forense reale — tutti i dati sono fittizi e conformi.

ATTENZIONE: questi test aprono il file in sola lettura ('rb').
Il file non viene mai modificato.
"""
from __future__ import annotations

import inspect
import os
from itertools import islice
from pathlib import Path
from types import MappingProxyType

import pytest

from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter

# ---------------------------------------------------------------------------
# Path ai file sintetici
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CELLEBRITE_DIR = _PROJECT_ROOT / "test_data" / "cellebrite_export"
_REPORT_XML = _CELLEBRITE_DIR / "report.xml"
_MESSAGES_JSON = _CELLEBRITE_DIR / "messages.json"
_MESSAGES_CSV = _CELLEBRITE_DIR / "messages.csv"
_MSGSTORE_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "msgstore.db"
_WA_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "wa.db"

pytestmark = pytest.mark.integration

if not _REPORT_XML.exists():
    pytestmark = pytest.mark.skip(reason=f"File sintetico non trovato: {_REPORT_XML}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def importer():
    return CellebriteXmlImporter()


@pytest.fixture(scope="module")
def all_records(importer):
    """Carica tutti i record da report.xml una sola volta per il modulo."""
    return list(importer.import_records(_REPORT_XML))


# ---------------------------------------------------------------------------
# can_import e Riconoscimento Sorgente Reale
# ---------------------------------------------------------------------------

class TestCanImportIntegration:

    def test_can_import_real_report_xml(self, importer):
        assert importer.can_import(_REPORT_XML) is True

    def test_can_import_rejects_other_sources(self, importer):
        assert importer.can_import(_MESSAGES_JSON) is False
        assert importer.can_import(_MESSAGES_CSV) is False
        assert importer.can_import(_MSGSTORE_DB) is False
        assert importer.can_import(_WA_DB) is False


# ---------------------------------------------------------------------------
# Conteggi e Integrità RawRecord
# ---------------------------------------------------------------------------

class TestRecordIntegrity:

    def test_total_message_count_is_50(self, all_records):
        assert len(all_records) == 50

    def test_all_items_are_raw_records(self, all_records):
        for r in all_records:
            assert isinstance(r, RawRecord)

    def test_source_name_is_cellebrite_xml(self, all_records):
        for r in all_records:
            assert r.source_name == "cellebrite_xml"

    def test_record_type_is_message(self, all_records):
        for r in all_records:
            assert r.record_type == "message"

    def test_source_record_ids_are_unique_and_match_0_to_49(self, all_records):
        ids = [r.source_record_id for r in all_records]
        assert len(ids) == 50
        assert len(set(ids)) == 50
        assert ids == [str(i) for i in range(50)]

    def test_first_message_exact_values(self, all_records):
        r0 = all_records[0]
        assert r0.source_record_id == "0"
        assert r0.raw_fields["Sender"] == "+39 000 0000001"
        assert r0.raw_fields["Timestamp"] == "2024-04-23T20:30:15+00:00"
        assert r0.raw_fields["Body"] == "Messaggio di test sintetico 08"
        assert r0.raw_fields["Deleted"] == "false"
        assert r0.media_reference is None

    def test_empty_body_message_value(self, all_records):
        r1 = all_records[1]
        assert r1.source_record_id == "1"
        assert r1.raw_fields["Sender"] == "+1 000 0000004"
        assert r1.raw_fields["Body"] is None

    def test_deleted_true_message_value(self, all_records):
        # Messaggio id="35" ha Deleted == "true"
        r35 = all_records[35]
        assert r35.source_record_id == "35"
        assert r35.raw_fields["Deleted"] == "true"
        assert r35.raw_fields["Body"] == "https://example.org/synthetic-test-group"

    def test_metadata_fields(self, all_records):
        for idx, r in enumerate(all_records):
            assert r.metadata["table"] == "InstantMessages"
            assert r.metadata["format"] == "xml_ufdr"
            assert r.metadata["xml_index"] == idx
            assert r.metadata["importer"] == "CellebriteXmlImporter"
            assert "importer_version" in r.metadata


# ---------------------------------------------------------------------------
# Preservazione Tipi Nativi XML e Assenza di Normalizzazione
# ---------------------------------------------------------------------------

class TestXmlTypePreservationIntegration:

    def test_deleted_values_are_strictly_strings(self, all_records):
        for r in all_records:
            deleted_val = r.raw_fields["Deleted"]
            assert isinstance(deleted_val, str)
            assert deleted_val in ("false", "true")

    def test_timestamps_are_raw_iso_strings(self, all_records):
        for r in all_records:
            ts = r.raw_fields["Timestamp"]
            assert isinstance(ts, str)
            assert "+00:00" in ts

    def test_sender_aliases_and_numbers_preserved(self, all_records):
        senders = {r.raw_fields["Sender"] for r in all_records}
        assert "group_participant_A" in senders
        assert "+39 000 0000001" in senders
        assert "+1 000 0000004" in senders

    def test_no_unified_message_fields_injected(self, all_records):
        for r in all_records:
            assert "sender_id" not in r.raw_fields
            assert "text_content" not in r.raw_fields
            assert "utc_timestamp" not in r.raw_fields


# ---------------------------------------------------------------------------
# Deep Immutability
# ---------------------------------------------------------------------------

class TestDeepImmutabilityIntegration:

    def test_raw_fields_is_immutable(self, all_records):
        for r in all_records:
            assert isinstance(r.raw_fields, MappingProxyType)
            with pytest.raises(TypeError):
                r.raw_fields["tampered"] = 123

    def test_attributes_mapping_is_immutable(self, all_records):
        for r in all_records:
            attribs = r.raw_fields["@attributes"]
            assert isinstance(attribs, MappingProxyType)
            with pytest.raises(TypeError):
                attribs["id"] = "999"


# ---------------------------------------------------------------------------
# Streaming e Read-Only
# ---------------------------------------------------------------------------

class TestStreamingAndReadOnly:

    def test_import_records_returns_generator(self, importer):
        gen = importer.import_records(_REPORT_XML)
        assert inspect.isgenerator(gen)
        first = next(gen)
        assert isinstance(first, RawRecord)
        gen.close()

    def test_streaming_partial_consumption(self, importer):
        records = list(islice(importer.import_records(_REPORT_XML), 5))
        assert len(records) == 5
        assert [r.source_record_id for r in records] == [str(i) for i in range(5)]

    def test_mtime_unchanged_after_reading(self, importer):
        mtime_before = os.path.getmtime(_REPORT_XML)
        list(importer.import_records(_REPORT_XML))
        mtime_after = os.path.getmtime(_REPORT_XML)
        assert mtime_before == mtime_after

    def test_size_unchanged_after_reading(self, importer):
        size_before = _REPORT_XML.stat().st_size
        list(importer.import_records(_REPORT_XML))
        size_after = _REPORT_XML.stat().st_size
        assert size_before == size_after


# ---------------------------------------------------------------------------
# Matrice di Discriminazione Incrociata a 5 Vie
# ---------------------------------------------------------------------------

class TestCrossImporterDiscrimination5Way:
    """
    Verifica che ciascun importer accetti esclusivamente il proprio file
    e rifiuti categoricamente tutti gli altri 4 file sorgente.
    """

    @pytest.fixture
    def msgstore_importer(self):
        return WhatsAppMsgstoreImporter()

    @pytest.fixture
    def wa_importer(self):
        return WhatsAppWaDbImporter()

    @pytest.fixture
    def csv_importer(self):
        return CellebriteCsvImporter()

    @pytest.fixture
    def json_importer(self):
        return CellebriteJsonImporter()

    @pytest.fixture
    def xml_importer(self):
        return CellebriteXmlImporter()

    def test_msgstore_importer_5way(self, msgstore_importer):
        assert msgstore_importer.can_import(_MSGSTORE_DB) is True
        assert msgstore_importer.can_import(_WA_DB) is False
        assert msgstore_importer.can_import(_MESSAGES_CSV) is False
        assert msgstore_importer.can_import(_MESSAGES_JSON) is False
        assert msgstore_importer.can_import(_REPORT_XML) is False

    def test_wa_importer_5way(self, wa_importer):
        assert wa_importer.can_import(_WA_DB) is True
        assert wa_importer.can_import(_MSGSTORE_DB) is False
        assert wa_importer.can_import(_MESSAGES_CSV) is False
        assert wa_importer.can_import(_MESSAGES_JSON) is False
        assert wa_importer.can_import(_REPORT_XML) is False

    def test_csv_importer_5way(self, csv_importer):
        assert csv_importer.can_import(_MESSAGES_CSV) is True
        assert csv_importer.can_import(_MSGSTORE_DB) is False
        assert csv_importer.can_import(_WA_DB) is False
        assert csv_importer.can_import(_MESSAGES_JSON) is False
        assert csv_importer.can_import(_REPORT_XML) is False

    def test_json_importer_5way(self, json_importer):
        assert json_importer.can_import(_MESSAGES_JSON) is True
        assert json_importer.can_import(_MSGSTORE_DB) is False
        assert json_importer.can_import(_WA_DB) is False
        assert json_importer.can_import(_MESSAGES_CSV) is False
        assert json_importer.can_import(_REPORT_XML) is False

    def test_xml_importer_5way(self, xml_importer):
        assert xml_importer.can_import(_REPORT_XML) is True
        assert xml_importer.can_import(_MSGSTORE_DB) is False
        assert xml_importer.can_import(_WA_DB) is False
        assert xml_importer.can_import(_MESSAGES_CSV) is False
        assert xml_importer.can_import(_MESSAGES_JSON) is False
