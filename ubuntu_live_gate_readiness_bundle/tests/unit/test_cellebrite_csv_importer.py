"""
tests/unit/test_cellebrite_csv_importer.py
------------------------------------------
Test UNITARI per CellebriteCsvImporter.

Principio: test isolati con file CSV sintetici in tmp_path.
Nessun dato forense reale.
"""
from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.cellebrite_csv import CellebriteCsvImporter
from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter


# ---------------------------------------------------------------------------
# Helpers: crea file CSV controllati per i test unitari
# ---------------------------------------------------------------------------

_SAMPLE_CSV_HEADER = (
    "Source,MessageType,TimeStamp,Direction,From,To,Body,Attachments,Status,Deleted,Forwarded,ApplicationId,ChatId\n"
)

_SAMPLE_ROWS = [
    "WhatsApp,Text,2024-11-12 03:09:27,Outgoing,+39 000 0000003,+39 000 0000001,Messaggio di test 01,,Delivered,False,False,com.whatsapp,chat_3\n",
    "WhatsApp,Text,2025-05-30 21:54:17,Incoming,group_participant_A,group_participant_A,Messaggio di test 02,,Received,False,False,com.whatsapp,chat_2\n",
    "WhatsApp,Image,2025-01-06 13:22:43,Incoming,+39 000 0000002,+39 000 0000003,,Files\\IMG_0012.jpg,Sent,False,False,com.whatsapp,chat_1\n",
]


def _make_minimal_cellebrite_csv(path: Path, n_rows: int = 3, with_bom: bool = False) -> None:
    """Crea un file CSV Cellebrite sintetico minimale."""
    content = _SAMPLE_CSV_HEADER + "".join(_SAMPLE_ROWS[:n_rows])
    if with_bom:
        path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    else:
        path.write_text(content, encoding="utf-8")


def _make_generic_csv(path: Path) -> None:
    """Crea un CSV generico senza header Cellebrite."""
    path.write_text("id,name,value\n1,Alice,100\n2,Bob,200\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return CellebriteCsvImporter()


@pytest.fixture
def minimal_csv(tmp_path):
    csv_file = tmp_path / "messages.csv"
    _make_minimal_cellebrite_csv(csv_file, n_rows=3)
    return csv_file


@pytest.fixture
def minimal_csv_bom(tmp_path):
    csv_file = tmp_path / "messages_bom.csv"
    _make_minimal_cellebrite_csv(csv_file, n_rows=3, with_bom=True)
    return csv_file


@pytest.fixture
def generic_csv(tmp_path):
    csv_file = tmp_path / "other.csv"
    _make_generic_csv(csv_file)
    return csv_file


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
        assert importer.source_name == "cellebrite_csv"

    def test_repr_contains_class_name(self, importer):
        assert "CellebriteCsvImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "cellebrite_csv" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportPositive:

    def test_returns_true_for_valid_csv(self, importer, minimal_csv):
        assert importer.can_import(minimal_csv) is True

    def test_returns_true_for_csv_with_bom(self, importer, minimal_csv_bom):
        assert importer.can_import(minimal_csv_bom) is True

    def test_returns_bool(self, importer, minimal_csv):
        assert isinstance(importer.can_import(minimal_csv), bool)

    def test_idempotent(self, importer, minimal_csv):
        r1 = importer.can_import(minimal_csv)
        r2 = importer.can_import(minimal_csv)
        assert r1 == r2

    def test_renamed_csv_no_extension(self, importer, minimal_csv, tmp_path):
        renamed = tmp_path / "messages_no_ext"
        shutil.copy2(minimal_csv, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_csv_unusual_extension(self, importer, minimal_csv, tmp_path):
        renamed = tmp_path / "evidence.bak"
        shutil.copy2(minimal_csv, renamed)
        assert importer.can_import(renamed) is True

    def test_synthetic_original_csv_returns_true(self, importer):
        real_csv = Path("test_data/cellebrite_export/messages.csv")
        assert real_csv.exists()
        assert importer.can_import(real_csv) is True


# ---------------------------------------------------------------------------
# can_import — casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        ghost = tmp_path / "ghost.csv"
        assert importer.can_import(ghost) is False

    def test_returns_false_for_generic_csv(self, importer, generic_csv):
        assert importer.can_import(generic_csv) is False

    def test_returns_false_for_plain_text(self, importer, tmp_path):
        txt = tmp_path / "sample.txt"
        txt.write_text("just plain text without header", encoding="utf-8")
        assert importer.can_import(txt) is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        assert importer.can_import(tmp_path) is False

    def test_returns_false_for_empty_file(self, importer, tmp_path):
        empty = tmp_path / "empty.csv"
        empty.touch()
        assert importer.can_import(empty) is False

    def test_returns_false_for_msgstore_db(self, importer):
        msgstore = Path("test_data/whatsapp_export/msgstore.db")
        if msgstore.exists():
            assert importer.can_import(msgstore) is False

    def test_returns_false_for_wa_db(self, importer):
        wa_db = Path("test_data/whatsapp_export/wa.db")
        if wa_db.exists():
            assert importer.can_import(wa_db) is False

    def test_returns_false_for_json(self, importer, tmp_path):
        jf = tmp_path / "data.json"
        jf.write_text('{"Source": "WhatsApp"}', encoding="utf-8")
        assert importer.can_import(jf) is False

    def test_returns_false_for_xml(self, importer, tmp_path):
        xf = tmp_path / "data.xml"
        xf.write_text("<DumpFile><Source>WhatsApp</Source></DumpFile>", encoding="utf-8")
        assert importer.can_import(xf) is False

    def test_does_not_raise_for_any_input(self, importer, tmp_path):
        cases = [
            tmp_path / "missing.csv",
            tmp_path,
            tmp_path / "empty.csv",
        ]
        (tmp_path / "empty.csv").touch()
        for p in cases:
            assert isinstance(importer.can_import(p), bool)


# ---------------------------------------------------------------------------
# Cross-importer discrimination
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrossImporterDiscrimination:

    def test_msgstore_importer_rejects_cellebrite_csv(self, minimal_csv):
        msgstore_imp = WhatsAppMsgstoreImporter()
        assert msgstore_imp.can_import(minimal_csv) is False

    def test_wa_importer_rejects_cellebrite_csv(self, minimal_csv):
        wa_imp = WhatsAppWaDbImporter()
        assert wa_imp.can_import(minimal_csv) is False

    def test_cellebrite_csv_importer_accepts_csv_rejects_dbs(self, minimal_csv):
        csv_imp = CellebriteCsvImporter()
        assert csv_imp.can_import(minimal_csv) is True
        msgstore = Path("test_data/whatsapp_export/msgstore.db")
        wa_db = Path("test_data/whatsapp_export/wa.db")
        if msgstore.exists():
            assert csv_imp.can_import(msgstore) is False
        if wa_db.exists():
            assert csv_imp.can_import(wa_db) is False


# ---------------------------------------------------------------------------
# import_records — errori sorgente
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsSourceErrors:

    def test_raises_file_not_found_for_nonexistent(self, importer, tmp_path):
        ghost = tmp_path / "ghost.csv"
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(ghost))

    def test_raises_value_error_for_generic_csv(self, importer, generic_csv):
        with pytest.raises(ValueError):
            list(importer.import_records(generic_csv))


# ---------------------------------------------------------------------------
# import_records — struttura RawRecord e identificatori
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecordsOutput:

    def test_returns_iterator(self, importer, minimal_csv):
        gen = importer.import_records(minimal_csv)
        assert isinstance(gen, Iterator)

    def test_yields_raw_records(self, importer, minimal_csv):
        records = list(importer.import_records(minimal_csv))
        assert all(isinstance(r, RawRecord) for r in records)

    def test_record_count(self, importer, minimal_csv):
        records = list(importer.import_records(minimal_csv))
        assert len(records) == 3

    def test_source_name_is_cellebrite_csv(self, importer, minimal_csv):
        records = list(importer.import_records(minimal_csv))
        assert all(r.source_name == "cellebrite_csv" for r in records)

    def test_record_type_is_message(self, importer, minimal_csv):
        records = list(importer.import_records(minimal_csv))
        assert all(r.record_type == "message" for r in records)

    def test_source_record_id_format(self, importer, minimal_csv):
        """source_record_id deve seguire il formato row:<line_number>."""
        records = list(importer.import_records(minimal_csv))
        ids = [r.source_record_id for r in records]
        assert ids == ["row:2", "row:3", "row:4"]

    def test_source_path_matches_file(self, importer, minimal_csv):
        expected_path = str(minimal_csv.resolve())
        records = list(importer.import_records(minimal_csv))
        assert all(r.source_path == expected_path for r in records)

    def test_metadata_contains_row_number(self, importer, minimal_csv):
        records = list(importer.import_records(minimal_csv))
        assert records[0].metadata["row_number"] == 2
        assert records[1].metadata["row_number"] == 3
        assert records[2].metadata["row_number"] == 4

    def test_bom_stripped_from_fieldnames(self, importer, minimal_csv_bom):
        """L'header non deve contenere il carattere BOM \\ufeff."""
        records = list(importer.import_records(minimal_csv_bom))
        for r in records:
            assert "Source" in r.raw_fields
            assert "\ufeffSource" not in r.raw_fields


# ---------------------------------------------------------------------------
# Fedeltà dei dati e questioni aperte
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDataFaithfulness:

    def test_chat_id_preserved_as_is(self, importer, minimal_csv):
        """ChatId non deve essere risolto in JID."""
        records = list(importer.import_records(minimal_csv))
        chat_ids = [r.raw_fields["ChatId"] for r in records]
        assert chat_ids == ["chat_3", "chat_2", "chat_1"]

    def test_group_participant_alias_preserved(self, importer, minimal_csv):
        """L'alias group_participant_A deve rimanere inalterato."""
        records = list(importer.import_records(minimal_csv))
        rec2 = records[1]
        assert rec2.raw_fields["From"] == "group_participant_A"
        assert rec2.raw_fields["To"] == "group_participant_A"

    def test_timestamp_preserved_raw_no_timezone_conversion(self, importer, minimal_csv):
        """Il timestamp rimane una stringa raw senza timezone aggiunta o UTC conversion."""
        records = list(importer.import_records(minimal_csv))
        ts = records[0].raw_fields["TimeStamp"]
        assert ts == "2024-11-12 03:09:27"
        assert isinstance(ts, str)

    def test_deleted_flag_preserved_as_string(self, importer, minimal_csv):
        """Deleted deve rimanere stringa ('False', 'True'), senza conversione bool."""
        records = list(importer.import_records(minimal_csv))
        assert records[0].raw_fields["Deleted"] == "False"
        assert isinstance(records[0].raw_fields["Deleted"], str)

    def test_empty_strings_preserved(self, importer, minimal_csv):
        """Campi vuoti (es. Attachments nel primo record) rimangono stringhe vuote."""
        records = list(importer.import_records(minimal_csv))
        assert records[0].raw_fields["Attachments"] == ""
        assert records[0].raw_fields["Attachments"] is not None

    def test_media_reference_handling(self, importer, minimal_csv):
        """media_reference è popolato solo quando Attachments non è vuoto."""
        records = list(importer.import_records(minimal_csv))
        # Record 0 e 1 non hanno allegati
        assert records[0].media_reference is None
        assert records[1].media_reference is None
        # Record 2 ha allegato
        assert records[2].media_reference == "Files\\IMG_0012.jpg"

    def test_unknown_message_type_passthrough(self, importer, tmp_path):
        """Tipi di messaggio non convenzionali (es. Sticker, Voice) passano senza errori."""
        custom_csv = tmp_path / "custom.csv"
        content = (
            _SAMPLE_CSV_HEADER
            + "WhatsApp,Sticker,2024-11-12 03:09:27,Outgoing,+39 000 0000001,+39 000 0000002,Test,,Delivered,False,False,com.whatsapp,chat_1\n"
        )
        custom_csv.write_text(content, encoding="utf-8")
        records = list(importer.import_records(custom_csv))
        assert len(records) == 1
        assert records[0].raw_fields["MessageType"] == "Sticker"


# ---------------------------------------------------------------------------
# Streaming e Read-Only
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStreamingAndReadOnly:

    def test_import_is_generator(self, importer, minimal_csv):
        gen = importer.import_records(minimal_csv)
        assert inspect.isgenerator(gen)

    def test_records_available_incrementally(self, importer, minimal_csv):
        gen = importer.import_records(minimal_csv)
        first = next(gen)
        assert isinstance(first, RawRecord)
        assert first.source_record_id == "row:2"

    def test_file_unchanged_after_import(self, importer, minimal_csv):
        import os
        mtime_before = os.path.getmtime(minimal_csv)
        size_before = minimal_csv.stat().st_size
        list(importer.import_records(minimal_csv))
        mtime_after = os.path.getmtime(minimal_csv)
        size_after = minimal_csv.stat().st_size
        assert mtime_before == mtime_after
        assert size_before == size_after


# ---------------------------------------------------------------------------
# Righe malformate ed error handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMalformedRows:

    def test_extra_columns_raise_value_error(self, importer, tmp_path):
        bad_csv = tmp_path / "extra_cols.csv"
        # Riga 2 ha un valore in più rispetto all'header (14 campi invece di 13)
        content = (
            _SAMPLE_CSV_HEADER
            + "WhatsApp,Text,2024-11-12 03:09:27,Outgoing,+39 000 0000003,+39 000 0000001,Body,,Delivered,False,False,com.whatsapp,chat_3,EXTRA_FIELD\n"
        )
        bad_csv.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="campi in eccesso"):
            list(importer.import_records(bad_csv))

    def test_missing_columns_raise_value_error(self, importer, tmp_path):
        bad_csv = tmp_path / "missing_cols.csv"
        # Riga 2 ha campi mancanti (solo 3 campi invece di 13)
        content = _SAMPLE_CSV_HEADER + "WhatsApp,Text,2024-11-12 03:09:27\n"
        bad_csv.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="colonne mancanti"):
            list(importer.import_records(bad_csv))


# ---------------------------------------------------------------------------
# Gestione record multiline con newline interne quotate
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMultilineCsvRecords:

    @pytest.fixture
    def multiline_csv(self, tmp_path):
        """
        Crea un CSV con una riga multiline (linee 2-4) seguita da una riga singola (linea 5).
        """
        csv_file = tmp_path / "multiline_messages.csv"
        content = (
            _SAMPLE_CSV_HEADER
            + 'WhatsApp,Text,2024-11-12 03:09:27,Outgoing,+39 000 0000003,+39 000 0000001,"Prima riga del testo\nSeconda riga del testo\nTerza riga",,Delivered,False,False,com.whatsapp,chat_3\n'
            + 'WhatsApp,Text,2024-11-12 03:10:00,Incoming,+39 000 0000001,+39 000 0000003,Messaggio successivo su riga singola,,Delivered,False,False,com.whatsapp,chat_3\n'
        )
        csv_file.write_text(content, encoding="utf-8-sig", newline="")
        return csv_file

    def test_multiline_produces_single_logical_record_without_duplicates(self, importer, multiline_csv):
        """csv.DictReader deve produrre esattamente 2 record logici senza duplicazioni."""
        records = list(importer.import_records(multiline_csv))
        assert len(records) == 2

    def test_multiline_body_preserved_verbatim(self, importer, multiline_csv):
        """Il campo Body multiline deve contenere fedelmente le newline interne originali."""
        records = list(importer.import_records(multiline_csv))
        expected_body = "Prima riga del testo\nSeconda riga del testo\nTerza riga"
        assert records[0].raw_fields["Body"] == expected_body

    def test_multiline_source_record_ids_reflect_physical_start_lines(self, importer, multiline_csv):
        """
        source_record_id deve tracciare la linea fisica di inizio del record nel file:
        - Record 1 inizia alla linea 2 (spanna le linee 2-4) -> 'row:2'
        - Record 2 inizia alla linea 5 -> 'row:5'
        Gli ID sono univoci e la provenance è accurata.
        """
        records = list(importer.import_records(multiline_csv))
        assert records[0].source_record_id == "row:2"
        assert records[1].source_record_id == "row:5"
        assert records[0].source_record_id != records[1].source_record_id

    def test_multiline_metadata_row_number_matches_start_line(self, importer, multiline_csv):
        records = list(importer.import_records(multiline_csv))
        assert records[0].metadata["row_number"] == 2
        assert records[1].metadata["row_number"] == 5
