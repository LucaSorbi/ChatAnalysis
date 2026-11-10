"""
tests/unit/test_raw_record.py
------------------------------
Test unitari per importer.models.RawRecord.

Copertura:
- Creazione con campi validi
- Campi obbligatori (source_name, source_path, source_record_id, record_type)
- Tipi errati per raw_fields e metadata
- Immutabilità (frozen=True)
- Valori opzionali (media_reference=None)
- Metodi di utilità: get_field(), has_media()
- repr() e hash()
"""
import pytest

from importer.models import RawRecord
from tests.fixtures.sample_records import (
    make_sqlite_message_record,
    make_csv_message_record,
    make_json_message_record,
    make_xml_message_record,
    make_media_record,
    make_contact_record,
)


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_record():
    return make_sqlite_message_record()


@pytest.fixture
def csv_record():
    return make_csv_message_record()


@pytest.fixture
def media_record():
    return make_media_record()


# ---------------------------------------------------------------------------
# Creazione valida
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRawRecordCreation:

    def test_create_minimal(self):
        """Creazione con i campi minimi obbligatori."""
        record = RawRecord(
            source_name="test_source",
            source_path="/some/path/file.db",
            source_record_id="1",
            record_type="message",
            raw_fields={"key": "value"},
            media_reference=None,
            metadata={},
        )
        assert record.source_name == "test_source"
        assert record.source_path == "/some/path/file.db"
        assert record.source_record_id == "1"
        assert record.record_type == "message"
        assert record.raw_fields == {"key": "value"}
        assert record.media_reference is None
        assert record.metadata == {}

    def test_create_sqlite_record(self, sqlite_record):
        """Creazione di un record SQLite sintetico."""
        assert sqlite_record.source_name == "msgstore_db"
        assert sqlite_record.record_type == "message"
        assert sqlite_record.raw_fields["_id"] == 1
        assert sqlite_record.raw_fields["media_wa_type"] == 0

    def test_create_csv_record(self, csv_record):
        """Creazione di un record CSV sintetico."""
        assert csv_record.source_name == "cellebrite_csv"
        assert csv_record.record_type == "message"
        assert csv_record.raw_fields["Source"] == "WhatsApp"

    def test_create_json_record(self):
        """Creazione di un record JSON sintetico."""
        record = make_json_message_record()
        assert record.source_name == "cellebrite_json"
        assert record.raw_fields["type"] == "text"

    def test_create_xml_record(self):
        """Creazione di un record XML sintetico."""
        record = make_xml_message_record()
        assert record.source_name == "cellebrite_xml"
        assert record.raw_fields["Deleted"] == "false"

    def test_create_media_record(self, media_record):
        """Creazione di un record media_ref sintetico."""
        assert media_record.record_type == "media_ref"
        assert media_record.has_media() is True

    def test_create_contact_record(self):
        """Creazione di un record contatto sintetico."""
        record = make_contact_record()
        assert record.record_type == "contact"
        assert record.raw_fields["display_name"] == "Alice Sintetica"

    def test_media_reference_populated(self):
        """media_reference viene valorizzato correttamente."""
        record = make_sqlite_message_record(
            media_url="WhatsApp Images/IMG_synth_001.jpg",
            media_wa_type=1,
        )
        assert record.media_reference == "WhatsApp Images/IMG_synth_001.jpg"
        assert record.has_media() is True

    def test_metadata_contains_table(self, sqlite_record):
        """Il metadata di un record SQLite contiene il nome della tabella."""
        assert sqlite_record.metadata["table"] == "messages"

    def test_raw_fields_preserves_none_values(self):
        """Valori None nei raw_fields devono essere preservati."""
        record = RawRecord(
            source_name="s",
            source_path="/p",
            source_record_id="1",
            record_type="message",
            raw_fields={"body": None, "media_url": None},
            media_reference=None,
            metadata={},
        )
        assert record.raw_fields["body"] is None
        assert record.raw_fields["media_url"] is None

    def test_raw_fields_preserves_nested_dict(self):
        """raw_fields preserva dict annidati (congelati come MappingProxyType per deep immutability)."""
        from types import MappingProxyType
        record = make_json_message_record()
        assert isinstance(record.raw_fields["content"], MappingProxyType)
        assert record.raw_fields["content"]["text"] == "Messaggio JSON sintetico"


# ---------------------------------------------------------------------------
# Validazione campi obbligatori
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRawRecordValidation:

    def test_empty_source_name_raises(self):
        with pytest.raises(ValueError, match="source_name"):
            RawRecord(
                source_name="",
                source_path="/p",
                source_record_id="1",
                record_type="message",
                raw_fields={},
                media_reference=None,
                metadata={},
            )

    def test_empty_source_path_raises(self):
        with pytest.raises(ValueError, match="source_path"):
            RawRecord(
                source_name="s",
                source_path="",
                source_record_id="1",
                record_type="message",
                raw_fields={},
                media_reference=None,
                metadata={},
            )

    def test_empty_source_record_id_raises(self):
        with pytest.raises(ValueError, match="source_record_id"):
            RawRecord(
                source_name="s",
                source_path="/p",
                source_record_id="",
                record_type="message",
                raw_fields={},
                media_reference=None,
                metadata={},
            )

    def test_empty_record_type_raises(self):
        with pytest.raises(ValueError, match="record_type"):
            RawRecord(
                source_name="s",
                source_path="/p",
                source_record_id="1",
                record_type="",
                raw_fields={},
                media_reference=None,
                metadata={},
            )

    def test_raw_fields_not_dict_raises(self):
        with pytest.raises(TypeError, match="raw_fields"):
            RawRecord(
                source_name="s",
                source_path="/p",
                source_record_id="1",
                record_type="message",
                raw_fields=["not", "a", "dict"],  # type: ignore[arg-type]
                media_reference=None,
                metadata={},
            )

    def test_metadata_not_dict_raises(self):
        with pytest.raises(TypeError, match="metadata"):
            RawRecord(
                source_name="s",
                source_path="/p",
                source_record_id="1",
                record_type="message",
                raw_fields={},
                media_reference=None,
                metadata="not a dict",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Immutabilità
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRawRecordImmutability:

    def test_cannot_reassign_source_name(self, sqlite_record):
        """frozen=True: riassegnazione attributo deve fallire."""
        with pytest.raises((AttributeError, TypeError)):
            sqlite_record.source_name = "other"  # type: ignore[misc]

    def test_cannot_reassign_source_path(self, sqlite_record):
        with pytest.raises((AttributeError, TypeError)):
            sqlite_record.source_path = "/other/path"  # type: ignore[misc]

    def test_cannot_reassign_record_type(self, sqlite_record):
        with pytest.raises((AttributeError, TypeError)):
            sqlite_record.record_type = "contact"  # type: ignore[misc]

    def test_cannot_reassign_media_reference(self, sqlite_record):
        with pytest.raises((AttributeError, TypeError)):
            sqlite_record.media_reference = "some/path.jpg"  # type: ignore[misc]

    def test_two_identical_records_are_equal(self):
        """Due record con gli stessi dati devono essere uguali (frozen dataclass)."""
        r1 = make_sqlite_message_record(record_id="42")
        r2 = make_sqlite_message_record(record_id="42")
        assert r1 == r2

    def test_different_ids_are_not_equal(self):
        r1 = make_sqlite_message_record(record_id="1")
        r2 = make_sqlite_message_record(record_id="2")
        assert r1 != r2

    def test_raw_fields_is_mapping_proxy(self, sqlite_record):
        """raw_fields deve essere MappingProxyType dopo la costruzione."""
        from types import MappingProxyType
        assert isinstance(sqlite_record.raw_fields, MappingProxyType)

    def test_raw_fields_mutation_raises(self, sqlite_record):
        """Tentativo di mutare raw_fields deve sollevare TypeError."""
        with pytest.raises(TypeError):
            sqlite_record.raw_fields["_id"] = 999  # type: ignore[index]

    def test_metadata_mutation_raises(self, sqlite_record):
        """Tentativo di mutare metadata deve sollevare TypeError."""
        with pytest.raises(TypeError):
            sqlite_record.metadata["table"] = "other"  # type: ignore[index]

    def test_nested_dict_mutation_raises(self):
        """Dizionario annidato dentro raw_fields deve essere MappingProxyType e non mutabile."""
        from types import MappingProxyType
        record = RawRecord(
            source_name="cellebrite_json",
            source_path="/fake/messages.json",
            source_record_id="msg_001",
            record_type="message",
            raw_fields={"content": {"text": "original", "media_path": None}},
            media_reference=None,
            metadata={},
        )
        assert isinstance(record.raw_fields["content"], MappingProxyType)
        assert record.raw_fields["content"]["text"] == "original"
        with pytest.raises(TypeError):
            record.raw_fields["content"]["text"] = "mutated"  # type: ignore[index]

    def test_nested_list_mutation_raises(self):
        """Lista annidata dentro raw_fields deve essere convertita in tuple immutabile."""
        record = RawRecord(
            source_name="cellebrite_json",
            source_path="/fake/messages.json",
            source_record_id="msg_001",
            record_type="message",
            raw_fields={"tags": ["tag_a", "tag_b"]},
            media_reference=None,
            metadata={},
        )
        assert isinstance(record.raw_fields["tags"], tuple)
        assert record.raw_fields["tags"] == ("tag_a", "tag_b")
        with pytest.raises(TypeError):
            record.raw_fields["tags"][0] = "mutated"  # type: ignore[index]
        with pytest.raises(AttributeError):
            record.raw_fields["tags"].append("tag_c")  # type: ignore[attr-defined]

    def test_dict_inside_list_mutation_raises(self):
        """Dizionario dentro una lista annidata deve essere MappingProxyType dentro tuple."""
        from types import MappingProxyType
        record = RawRecord(
            source_name="cellebrite_json",
            source_path="/fake/messages.json",
            source_record_id="msg_001",
            record_type="message",
            raw_fields={"attachments": [{"file_name": "foto.jpg", "size": 1024}]},
            media_reference=None,
            metadata={},
        )
        assert isinstance(record.raw_fields["attachments"], tuple)
        assert isinstance(record.raw_fields["attachments"][0], MappingProxyType)
        assert record.raw_fields["attachments"][0]["file_name"] == "foto.jpg"
        with pytest.raises(TypeError):
            record.raw_fields["attachments"][0]["file_name"] = "malicious.exe"  # type: ignore[index]
        with pytest.raises(TypeError):
            record.raw_fields["attachments"][0] = {"file_name": "other.jpg"}  # type: ignore[index]

    def test_nested_metadata_mutation_raises(self):
        """Strutture annidate dentro metadata devono essere ricorsivamente immutabili."""
        from types import MappingProxyType
        record = RawRecord(
            source_name="s",
            source_path="/p",
            source_record_id="1",
            record_type="message",
            raw_fields={},
            media_reference=None,
            metadata={"nested": {"level": 2, "items": [1, 2]}},
        )
        assert isinstance(record.metadata["nested"], MappingProxyType)
        assert isinstance(record.metadata["nested"]["items"], tuple)
        with pytest.raises(TypeError):
            record.metadata["nested"]["level"] = 99  # type: ignore[index]
        with pytest.raises(TypeError):
            record.metadata["nested"]["items"][0] = 99  # type: ignore[index]


# ---------------------------------------------------------------------------
# Metodi di utilità
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRawRecordMethods:

    def test_get_field_existing_key(self, sqlite_record):
        assert sqlite_record.get_field("_id") == 1

    def test_get_field_missing_key_returns_default(self, sqlite_record):
        assert sqlite_record.get_field("nonexistent") is None

    def test_get_field_missing_key_custom_default(self, sqlite_record):
        assert sqlite_record.get_field("nonexistent", "fallback") == "fallback"

    def test_get_field_none_value(self):
        """get_field deve restituire None se il campo esiste ma è None."""
        record = make_sqlite_message_record(data=None)
        assert record.get_field("data") is None

    def test_has_media_false_when_no_media(self, sqlite_record):
        assert sqlite_record.has_media() is False

    def test_has_media_true_when_media_present(self, media_record):
        assert media_record.has_media() is True

    def test_has_media_false_when_empty_string(self):
        record = RawRecord(
            source_name="s",
            source_path="/p",
            source_record_id="1",
            record_type="message",
            raw_fields={},
            media_reference="",
            metadata={},
        )
        assert record.has_media() is False

    def test_repr_contains_class_name(self, sqlite_record):
        r = repr(sqlite_record)
        assert "RawRecord" in r

    def test_repr_contains_source_name(self, sqlite_record):
        r = repr(sqlite_record)
        assert "msgstore_db" in r
