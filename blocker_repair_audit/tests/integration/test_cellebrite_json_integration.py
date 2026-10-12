"""
tests/integration/test_cellebrite_json_integration.py
-----------------------------------------------------
Test di INTEGRAZIONE per CellebriteJsonImporter.

Usa il file sintetico reale in test_data/cellebrite_export/messages.json.
Nessun dato forense reale — tutti i dati sono fittizi e conformi.

ATTENZIONE: questi test aprono il file in sola lettura ('rb').
Il file non viene mai modificato.
"""
from __future__ import annotations

import inspect
import json
import os
from itertools import islice
from pathlib import Path
from types import MappingProxyType

import pytest

from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter

# ---------------------------------------------------------------------------
# Path ai file sintetici
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CELLEBRITE_DIR = _PROJECT_ROOT / "test_data" / "cellebrite_export"
_MESSAGES_JSON = _CELLEBRITE_DIR / "messages.json"
_MESSAGES_CSV = _CELLEBRITE_DIR / "messages.csv"
_REPORT_XML = _CELLEBRITE_DIR / "report.xml"
_MSGSTORE_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "msgstore.db"
_WA_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "wa.db"

pytestmark = pytest.mark.integration

if not _MESSAGES_JSON.exists():
    pytestmark = pytest.mark.skip(reason=f"File sintetico non trovato: {_MESSAGES_JSON}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def importer():
    return CellebriteJsonImporter()


@pytest.fixture(scope="module")
def all_records(importer):
    """Carica tutti i record da messages.json una sola volta per il modulo."""
    return list(importer.import_records(_MESSAGES_JSON))


# ---------------------------------------------------------------------------
# Riconoscimento sorgente
# ---------------------------------------------------------------------------

class TestCellebriteJsonRecognition:

    def test_can_import_returns_true(self, importer):
        assert importer.can_import(_MESSAGES_JSON) is True

    def test_json_exists_and_is_file(self):
        assert _MESSAGES_JSON.exists()
        assert _MESSAGES_JSON.is_file()

    def test_rejects_other_cellebrite_formats(self, importer):
        if _MESSAGES_CSV.exists():
            assert importer.can_import(_MESSAGES_CSV) is False
        if _REPORT_XML.exists():
            assert importer.can_import(_REPORT_XML) is False

    def test_rejects_whatsapp_databases(self, importer):
        if _MSGSTORE_DB.exists():
            assert importer.can_import(_MSGSTORE_DB) is False
        if _WA_DB.exists():
            assert importer.can_import(_WA_DB) is False


# ---------------------------------------------------------------------------
# Discriminazione cross-importer (4 vie)
# ---------------------------------------------------------------------------

class TestCrossImporterDiscrimination:

    def test_msgstore_importer_rejects_cellebrite_json(self):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(_MESSAGES_JSON) is False

    def test_wa_importer_rejects_cellebrite_json(self):
        wa_imp = WhatsAppWaDbImporter()
        assert wa_imp.can_import(_MESSAGES_JSON) is False

    def test_cellebrite_csv_importer_rejects_cellebrite_json(self):
        csv_imp = CellebriteCsvImporter()
        assert csv_imp.can_import(_MESSAGES_JSON) is False

    def test_cellebrite_json_importer_accepts_json_only(self, importer):
        assert importer.can_import(_MESSAGES_JSON) is True
        if _MESSAGES_CSV.exists():
            assert importer.can_import(_MESSAGES_CSV) is False


# ---------------------------------------------------------------------------
# Conteggio e tipi di record
# ---------------------------------------------------------------------------

class TestCellebriteJsonRecordCountAndTypes:

    def test_total_record_count_is_100(self, all_records):
        """Nel dataset messages.json sintetico sono presenti esattamente 100 messaggi."""
        assert len(all_records) == 100

    def test_all_records_are_raw_records(self, all_records):
        assert all(isinstance(r, RawRecord) for r in all_records)

    def test_all_records_are_message_type(self, all_records):
        assert all(r.record_type == "message" for r in all_records)

    def test_source_name_is_cellebrite_json(self, all_records):
        assert all(r.source_name == "cellebrite_json" for r in all_records)

    def test_source_record_ids_are_unique_native_ids(self, all_records):
        ids = [r.source_record_id for r in all_records]
        assert len(ids) == 100
        assert len(set(ids)) == 100
        assert all(id_.startswith("msg_") for id_ in ids)
        assert ids[0] == "msg_00000"
        assert ids[-1] == "msg_00099"

    def test_source_path_is_messages_json_path(self, all_records):
        expected_path = str(_MESSAGES_JSON.resolve())
        assert all(r.source_path == expected_path for r in all_records)

    def test_metadata_contains_provenance_info(self, all_records):
        for idx, r in enumerate(all_records):
            assert r.metadata.get("importer") == "CellebriteJsonImporter"
            assert r.metadata.get("format") == "json_array"
            assert r.metadata.get("array_index") == idx
            assert r.metadata.get("table") == "messages"


# ---------------------------------------------------------------------------
# Fedeltà dei dati: tipi nativi JSON, immutabilità profonda e nessun cast
# ---------------------------------------------------------------------------

class TestCellebriteJsonDataFaithfulness:

    def test_boolean_types_preserved_as_native_bool(self, all_records):
        """deleted, forwarded e starred devono essere bool nativi Python (True/False), NON stringhe."""
        for r in all_records:
            meta = r.raw_fields["metadata"]
            assert isinstance(meta["deleted"], bool)
            assert isinstance(meta["forwarded"], bool)
            assert isinstance(meta["starred"], bool)
            assert meta["deleted"] is not str
            assert meta["deleted"] in (True, False)

    def test_null_types_preserved_as_none(self, all_records):
        for r in all_records:
            content = r.raw_fields["content"]
            assert content["media_path"] is None
            assert r.media_reference is None

    def test_nested_structures_are_deeply_immutable(self, all_records):
        """Oggetti annidati (content, metadata) devono essere MappingProxyType e non mutabili."""
        for r in all_records:
            assert isinstance(r.raw_fields["content"], MappingProxyType)
            assert isinstance(r.raw_fields["metadata"], MappingProxyType)
            with pytest.raises(TypeError):
                r.raw_fields["content"]["text"] = "hacked"  # type: ignore[index]
            with pytest.raises(TypeError):
                r.raw_fields["metadata"]["deleted"] = not r.raw_fields["metadata"]["deleted"]  # type: ignore[index]

    def test_chat_ids_preserved_verbatim(self, all_records):
        chat_ids = {r.raw_fields["chat_id"] for r in all_records}
        assert chat_ids == {"chat_1", "chat_2", "chat_3"}

    def test_timestamps_preserved_raw_without_timezone_conversion(self, all_records):
        """I timestamp ISO-8601 devono rimanere stringhe grezze, senza parsing a datetime UTC."""
        for r in all_records:
            ts = r.raw_fields["timestamp"]
            assert isinstance(ts, str)
            assert "T" in ts

    def test_raw_fields_match_direct_json_load_content(self, all_records):
        """Ogni record emesso deve corrispondere esattamente all'oggetto JSON originale."""
        with open(_MESSAGES_JSON, "r", encoding="utf-8") as f:
            direct_items = json.load(f)

        assert len(all_records) == len(direct_items)
        for r, d in zip(all_records, direct_items):
            assert r.source_record_id == d["id"]
            assert r.raw_fields["chat_id"] == d["chat_id"]
            assert r.raw_fields["sender"] == d["sender"]
            assert r.raw_fields["timestamp"] == d["timestamp"]
            assert r.raw_fields["type"] == d["type"]
            assert r.raw_fields["content"]["text"] == d["content"]["text"]
            assert r.raw_fields["content"]["media_path"] == d["content"]["media_path"]
            assert r.raw_fields["metadata"]["deleted"] == d["metadata"]["deleted"]
            assert r.raw_fields["metadata"]["forwarded"] == d["metadata"]["forwarded"]
            assert r.raw_fields["metadata"]["starred"] == d["metadata"]["starred"]


# ---------------------------------------------------------------------------
# Assenza di normalizzazione semantica ed Entity Resolution
# ---------------------------------------------------------------------------

class TestNoEntityResolutionOrSemanticNormalization:

    def test_chat_ids_not_resolved_to_jids(self, all_records):
        for r in all_records:
            assert not r.raw_fields["chat_id"].endswith("@s.whatsapp.net")
            assert not r.raw_fields["chat_id"].endswith("@g.us")

    def test_group_participant_aliases_not_resolved(self, all_records):
        senders = {r.raw_fields["sender"] for r in all_records}
        assert "group_participant_A" in senders

    def test_no_unified_message_fields_injected(self, all_records):
        for r in all_records:
            assert "sender_id" not in r.raw_fields
            assert "text_content" not in r.raw_fields
            assert "utc_timestamp" not in r.raw_fields


# ---------------------------------------------------------------------------
# Streaming e Read-Only
# ---------------------------------------------------------------------------

class TestStreamingAndReadOnly:

    def test_import_records_returns_generator(self, importer):
        gen = importer.import_records(_MESSAGES_JSON)
        assert inspect.isgenerator(gen)
        first = next(gen)
        assert isinstance(first, RawRecord)
        gen.close()

    def test_streaming_partial_consumption(self, importer):
        records = list(islice(importer.import_records(_MESSAGES_JSON), 5))
        assert len(records) == 5
        assert [r.source_record_id for r in records] == [f"msg_0000{i}" for i in range(5)]

    def test_mtime_unchanged_after_reading(self, importer):
        mtime_before = os.path.getmtime(_MESSAGES_JSON)
        list(importer.import_records(_MESSAGES_JSON))
        mtime_after = os.path.getmtime(_MESSAGES_JSON)
        assert mtime_before == mtime_after

    def test_size_unchanged_after_reading(self, importer):
        size_before = _MESSAGES_JSON.stat().st_size
        list(importer.import_records(_MESSAGES_JSON))
        size_after = _MESSAGES_JSON.stat().st_size
        assert size_before == size_after
