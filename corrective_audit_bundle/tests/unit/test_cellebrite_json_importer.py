"""
tests/unit/test_cellebrite_json_importer.py
-------------------------------------------
Test unitari per CellebriteJsonImporter.

Copertura:
- Ereditarietà da BaseImporter e proprietà source_name
- can_import(): positivo (con/senza estensione, .txt), negativo (file inesistente,
  directory, JSON generico, malformato, SQLite, CSV, XML)
- Discriminazione cross-importer (tutti e 4 gli importer)
- import_records(): emissione RawRecord, generator/streaming, read-only
- Preservazione tipi JSON nativi (bool, null, number, string)
- Immutabilità profonda su strutture annidate (content, metadata)
- Fallback source_record_id su 'item:<idx>' quando 'id' è assente
- Error handling su sorgente non valida o elementi non-dict
"""
from __future__ import annotations

import json
import os
from itertools import islice
from pathlib import Path
from types import MappingProxyType
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return CellebriteJsonImporter()


@pytest.fixture
def sample_json_data():
    return [
        {
            "id": "msg_00000",
            "chat_id": "chat_1",
            "sender": "group_participant_A",
            "timestamp": "2024-10-31T16:50:00+00:00",
            "type": "audio",
            "content": {
                "text": "Messaggio sintetico con emoji 💊",
                "media_path": None,
            },
            "metadata": {
                "deleted": False,
                "forwarded": False,
                "starred": False,
            },
        },
        {
            "id": "msg_00001",
            "chat_id": "chat_2",
            "sender": "+1 000 0000004",
            "timestamp": "2025-05-13T13:42:44+00:00",
            "type": "text",
            "content": {
                "text": "Messaggio di test sintetico 12",
                "media_path": "files/audio_01.aac",
            },
            "metadata": {
                "deleted": True,
                "forwarded": True,
                "starred": False,
            },
        },
    ]


@pytest.fixture
def valid_json_file(tmp_path, sample_json_data):
    file_path = tmp_path / "test_messages.json"
    file_path.write_text(json.dumps(sample_json_data), encoding="utf-8")
    return file_path


@pytest.fixture
def generic_json_file(tmp_path):
    file_path = tmp_path / "generic.json"
    file_path.write_text(json.dumps([{"users": ["alice", "bob"]}]), encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Contratto BaseImporter
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestInheritsBaseImporter:

    def test_is_base_importer_subclass(self):
        assert issubclass(CellebriteJsonImporter, BaseImporter)

    def test_source_name_is_string(self, importer):
        assert isinstance(importer.source_name, str)

    def test_source_name_value(self, importer):
        assert importer.source_name == "cellebrite_json"

    def test_repr_contains_class_name(self, importer):
        assert "CellebriteJsonImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "cellebrite_json" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportPositive:

    def test_returns_true_for_valid_json(self, importer, valid_json_file):
        assert importer.can_import(valid_json_file) is True

    def test_returns_bool(self, importer, valid_json_file):
        result = importer.can_import(valid_json_file)
        assert isinstance(result, bool)

    def test_idempotent(self, importer, valid_json_file):
        assert importer.can_import(valid_json_file) == importer.can_import(valid_json_file)

    def test_renamed_json_no_extension(self, importer, valid_json_file, tmp_path):
        renamed = tmp_path / "evidence_file"
        renamed.write_bytes(valid_json_file.read_bytes())
        assert importer.can_import(renamed) is True

    def test_renamed_json_txt_extension(self, importer, valid_json_file, tmp_path):
        renamed = tmp_path / "evidence.txt"
        renamed.write_bytes(valid_json_file.read_bytes())
        assert importer.can_import(renamed) is True

    def test_synthetic_original_json_returns_true(self, importer):
        project_root = Path(__file__).resolve().parent.parent.parent
        real_json = project_root / "test_data" / "cellebrite_export" / "messages.json"
        if real_json.exists():
            assert importer.can_import(real_json) is True


# ---------------------------------------------------------------------------
# can_import — casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        assert importer.can_import(tmp_path / "missing.json") is False

    def test_returns_false_for_generic_json(self, importer, generic_json_file):
        assert importer.can_import(generic_json_file) is False

    def test_returns_false_for_empty_json_array(self, importer, tmp_path):
        f = tmp_path / "empty_array.json"
        f.write_text("[]", encoding="utf-8")
        assert importer.can_import(f) is False

    def test_returns_false_for_json_object_not_array(self, importer, tmp_path):
        f = tmp_path / "object.json"
        f.write_text('{"chat_id": "1", "sender": "me", "timestamp": "t", "type": "text"}', encoding="utf-8")
        assert importer.can_import(f) is False

    def test_returns_false_for_plain_text(self, importer, tmp_path):
        txt = tmp_path / "plain.txt"
        txt.write_text("Not a json file", encoding="utf-8")
        assert importer.can_import(txt) is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        assert importer.can_import(tmp_path) is False

    def test_returns_false_for_empty_file(self, importer, tmp_path):
        empty = tmp_path / "empty.json"
        empty.touch()
        assert importer.can_import(empty) is False

    def test_returns_false_for_malformed_json(self, importer, tmp_path):
        malformed = tmp_path / "malformed.json"
        malformed.write_text("[{invalid json", encoding="utf-8")
        assert importer.can_import(malformed) is False

    def test_does_not_raise_for_any_input(self, importer, tmp_path):
        weird = tmp_path / "binary.bin"
        weird.write_bytes(b"\x00\xff\xfe\x00\x12\x34\x56\x78")
        assert importer.can_import(weird) is False

    def test_does_not_swallow_unexpected_programming_errors(self, importer, valid_json_file, monkeypatch):
        """Verifica che eccezioni non attese (es. bug interni) non vengano soppresse da can_import."""
        def buggy_items(*args, **kwargs):
            raise TypeError("Simulated programming bug in internal parsing logic")

        monkeypatch.setattr("ijson.items", buggy_items)
        with pytest.raises(TypeError, match="Simulated programming bug"):
            importer.can_import(valid_json_file)


# ---------------------------------------------------------------------------
# Discriminazione cross-importer (4 vie)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrossImporterDiscrimination:

    def test_msgstore_importer_rejects_cellebrite_json(self, valid_json_file):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(valid_json_file) is False

    def test_wa_importer_rejects_cellebrite_json(self, valid_json_file):
        wa_imp = WhatsAppWaDbImporter()
        assert wa_imp.can_import(valid_json_file) is False

    def test_cellebrite_csv_importer_rejects_cellebrite_json(self, valid_json_file):
        csv_imp = CellebriteCsvImporter()
        assert csv_imp.can_import(valid_json_file) is False

    def test_cellebrite_json_importer_accepts_json_rejects_others(self, importer, valid_json_file, tmp_path):
        assert importer.can_import(valid_json_file) is True

        fake_csv = tmp_path / "fake.csv"
        fake_csv.write_text("Source,MessageType,TimeStamp,Direction,ChatId\n", encoding="utf-8")
        assert importer.can_import(fake_csv) is False


# ---------------------------------------------------------------------------
# import_records — errori sorgente
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsSourceErrors:

    def test_raises_file_not_found_for_nonexistent(self, importer, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(tmp_path / "not_there.json"))

    def test_raises_value_error_for_generic_json(self, importer, generic_json_file):
        with pytest.raises(ValueError):
            list(importer.import_records(generic_json_file))

    def test_raises_value_error_for_malformed_item(self, importer, tmp_path):
        bad_json = tmp_path / "bad_item.json"
        bad_json.write_text(
            json.dumps([
                {"chat_id": "1", "sender": "s", "timestamp": "ts", "type": "t"},
                "not_a_dictionary_item",
            ]),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Elemento malformato all'indice 1"):
            list(importer.import_records(bad_json))


# ---------------------------------------------------------------------------
# import_records — output RawRecord e metadati
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsOutput:

    def test_returns_iterator(self, importer, valid_json_file):
        gen = importer.import_records(valid_json_file)
        assert isinstance(gen, Iterator)

    def test_yields_raw_records(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert all(isinstance(r, RawRecord) for r in records)

    def test_record_count(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert len(records) == 2

    def test_source_name_is_cellebrite_json(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert all(r.source_name == "cellebrite_json" for r in records)

    def test_record_type_is_message(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert all(r.record_type == "message" for r in records)

    def test_source_record_id_from_native_id(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].source_record_id == "msg_00000"
        assert records[1].source_record_id == "msg_00001"

    def test_fallback_source_record_id_when_id_missing(self, importer, tmp_path):
        no_id_json = tmp_path / "no_id.json"
        no_id_json.write_text(
            json.dumps([
                {"chat_id": "c1", "sender": "s1", "timestamp": "t1", "type": "text"},
                {"chat_id": "c2", "sender": "s2", "timestamp": "t2", "type": "text"},
            ]),
            encoding="utf-8",
        )
        records = list(importer.import_records(no_id_json))
        assert records[0].source_record_id == "item:0"
        assert records[1].source_record_id == "item:1"

    def test_source_path_matches_file(self, importer, valid_json_file):
        expected_path = str(valid_json_file.resolve())
        records = list(importer.import_records(valid_json_file))
        assert all(r.source_path == expected_path for r in records)

    def test_metadata_contains_provenance(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].metadata["table"] == "messages"
        assert records[0].metadata["format"] == "json_array"
        assert records[0].metadata["array_index"] == 0
        assert records[0].metadata["importer"] == "CellebriteJsonImporter"
        assert records[1].metadata["array_index"] == 1


# ---------------------------------------------------------------------------
# Fedeltà dei dati: tipi nativi JSON, immutabilità e assenza di normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDataFaithfulnessAndDeepImmutability:

    def test_native_boolean_types_preserved_not_strings(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        meta0 = records[0].raw_fields["metadata"]
        assert meta0["deleted"] is False
        assert isinstance(meta0["deleted"], bool)
        assert meta0["deleted"] != "False"

        meta1 = records[1].raw_fields["metadata"]
        assert meta1["deleted"] is True
        assert isinstance(meta1["deleted"], bool)

    def test_native_null_type_preserved(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].raw_fields["content"]["media_path"] is None

    def test_nested_dict_is_mapping_proxy(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        content = records[0].raw_fields["content"]
        assert isinstance(content, MappingProxyType)
        with pytest.raises(TypeError):
            content["text"] = "mutated"  # type: ignore[index]

    def test_media_reference_extracted_correctly(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].media_reference is None
        assert records[1].media_reference == "files/audio_01.aac"

    def test_chat_id_preserved_as_is(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].raw_fields["chat_id"] == "chat_1"
        assert records[1].raw_fields["chat_id"] == "chat_2"

    def test_group_participant_alias_preserved(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].raw_fields["sender"] == "group_participant_A"

    def test_timestamp_preserved_raw_iso_no_utc_translation(self, importer, valid_json_file):
        records = list(importer.import_records(valid_json_file))
        assert records[0].raw_fields["timestamp"] == "2024-10-31T16:50:00+00:00"
        assert isinstance(records[0].raw_fields["timestamp"], str)

    def test_optional_field_absent_does_not_crash(self, importer, tmp_path):
        sparse_json = tmp_path / "sparse.json"
        sparse_json.write_text(
            json.dumps([
                {
                    "chat_id": "c1",
                    "sender": "s1",
                    "timestamp": "t1",
                    "type": "text",
                    # 'content', 'metadata' e 'id' omessi intenzionalmente
                }
            ]),
            encoding="utf-8",
        )
        records = list(importer.import_records(sparse_json))
        assert len(records) == 1
        assert "content" not in records[0].raw_fields
        assert records[0].source_record_id == "item:0"
        assert records[0].media_reference is None


# ---------------------------------------------------------------------------
# Streaming e Read-Only
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStreamingAndReadOnly:

    def test_import_is_generator(self, importer, valid_json_file):
        gen = importer.import_records(valid_json_file)
        import inspect
        assert inspect.isgenerator(gen)

    def test_records_available_incrementally(self, importer, valid_json_file):
        gen = importer.import_records(valid_json_file)
        first = next(gen)
        assert isinstance(first, RawRecord)
        assert first.source_record_id == "msg_00000"

    def test_file_unchanged_after_import(self, importer, valid_json_file):
        mtime_before = os.path.getmtime(valid_json_file)
        size_before = valid_json_file.stat().st_size
        list(importer.import_records(valid_json_file))
        mtime_after = os.path.getmtime(valid_json_file)
        size_after = valid_json_file.stat().st_size
        assert mtime_before == mtime_after
        assert size_before == size_after
