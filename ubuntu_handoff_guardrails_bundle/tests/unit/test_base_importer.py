"""
tests/unit/test_base_importer.py
---------------------------------
Test unitari per importer.base.BaseImporter.

Copertura:
- ABC non istanziabile direttamente
- Sottoclasse concreta minima funzionante
- can_import() — comportamento base
- import_records() — tipo di ritorno (Iterator)
- validate_source() — FileNotFoundError, ValueError
- repr()
- Importer che gestisce errori riga per riga senza interrompere l'iterazione
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from importer.base import BaseImporter
from importer.models import RawRecord
from tests.fixtures.sample_records import make_sqlite_message_record


# ---------------------------------------------------------------------------
# Implementazione minimale concreta per i test
# ---------------------------------------------------------------------------

class _MinimalImporter(BaseImporter):
    """
    Importer concreto minimale per i test.
    Non fa I/O reale: produce record sintetici.
    """

    @property
    def source_name(self) -> str:
        return "test_source"

    def can_import(self, source_path: Path) -> bool:
        return source_path.suffix == ".test"

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        self.validate_source(source_path)
        yield make_sqlite_message_record(record_id="1", source_path=str(source_path))
        yield make_sqlite_message_record(record_id="2", source_path=str(source_path))


class _ResilientImporter(BaseImporter):
    """
    Importer che produce alcuni record validi e uno problematico,
    ma continua a iterare senza interrompere l'elaborazione.
    """

    @property
    def source_name(self) -> str:
        return "resilient_source"

    def can_import(self, source_path: Path) -> bool:
        return source_path.suffix == ".test"

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        self.validate_source(source_path)
        for i in range(5):
            try:
                if i == 2:
                    # Simula un errore su un singolo record
                    raise ValueError(f"Errore simulato al record {i}")
                yield make_sqlite_message_record(
                    record_id=str(i),
                    source_path=str(source_path),
                )
            except ValueError:
                # L'importer gestisce l'errore e continua
                continue


class _EmptyImporter(BaseImporter):
    """Importer che non produce nessun record."""

    @property
    def source_name(self) -> str:
        return "empty_source"

    def can_import(self, source_path: Path) -> bool:
        return True

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        self.validate_source(source_path)
        return
        yield  # rende la funzione un generatore vuoto


# ---------------------------------------------------------------------------
# Helper: crea un file temporaneo con estensione .test
# ---------------------------------------------------------------------------

@pytest.fixture
def test_file(tmp_path):
    """File temporaneo con estensione .test per i test dell'importer."""
    f = tmp_path / "synthetic_data.test"
    f.write_text("dati sintetici\n")
    return f


@pytest.fixture
def minimal_importer():
    return _MinimalImporter()


@pytest.fixture
def resilient_importer():
    return _ResilientImporter()


# ---------------------------------------------------------------------------
# ABC — non istanziabile
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBaseImporterIsABC:

    def test_cannot_instantiate_abc_directly(self):
        """BaseImporter è astratta: non può essere istanziata direttamente."""
        with pytest.raises(TypeError):
            BaseImporter()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_be_instantiated(self):
        """Una sottoclasse che non implementa tutti i metodi astratti non può essere istanziata."""
        class _Incomplete(BaseImporter):
            @property
            def source_name(self) -> str:
                return "incomplete"
            # can_import e import_records NON implementati

        with pytest.raises(TypeError):
            _Incomplete()  # type: ignore[abstract]

    def test_concrete_subclass_can_be_instantiated(self, minimal_importer):
        """Una sottoclasse completa può essere istanziata."""
        assert isinstance(minimal_importer, BaseImporter)


# ---------------------------------------------------------------------------
# source_name
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSourceName:

    def test_source_name_is_string(self, minimal_importer):
        assert isinstance(minimal_importer.source_name, str)

    def test_source_name_not_empty(self, minimal_importer):
        assert minimal_importer.source_name != ""

    def test_source_name_value(self, minimal_importer):
        assert minimal_importer.source_name == "test_source"


# ---------------------------------------------------------------------------
# can_import()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCanImport:

    def test_can_import_returns_true_for_supported_extension(self, minimal_importer, test_file):
        assert minimal_importer.can_import(test_file) is True

    def test_can_import_returns_false_for_unsupported_extension(self, minimal_importer, tmp_path):
        wrong_file = tmp_path / "data.csv"
        wrong_file.touch()
        assert minimal_importer.can_import(wrong_file) is False

    def test_can_import_is_pure_no_side_effects(self, minimal_importer, test_file):
        """can_import() non deve produrre side effects: chiamarlo più volte è sicuro."""
        result1 = minimal_importer.can_import(test_file)
        result2 = minimal_importer.can_import(test_file)
        assert result1 == result2

    def test_can_import_nonexistent_path(self, minimal_importer, tmp_path):
        """can_import() non richiede che il file esista — è solo un check sull'estensione."""
        nonexistent = tmp_path / "ghost.test"
        # Deve restituire True/False senza eccezioni
        result = minimal_importer.can_import(nonexistent)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# import_records()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImportRecords:

    def test_import_records_returns_iterator(self, minimal_importer, test_file):
        result = minimal_importer.import_records(test_file)
        # Deve essere iterabile
        assert hasattr(result, "__iter__")
        assert hasattr(result, "__next__")

    def test_import_records_yields_raw_records(self, minimal_importer, test_file):
        records = list(minimal_importer.import_records(test_file))
        assert len(records) == 2
        for r in records:
            assert isinstance(r, RawRecord)

    def test_import_records_source_name_matches(self, minimal_importer, test_file):
        """I record prodotti devono avere source_path coerente con il file."""
        records = list(minimal_importer.import_records(test_file))
        for r in records:
            assert r.source_path == str(test_file)

    def test_import_records_record_ids_are_unique(self, minimal_importer, test_file):
        records = list(minimal_importer.import_records(test_file))
        ids = [r.source_record_id for r in records]
        assert len(ids) == len(set(ids)), "I source_record_id devono essere unici"

    def test_empty_importer_yields_nothing(self, tmp_path):
        importer = _EmptyImporter()
        empty_file = tmp_path / "empty.db"
        empty_file.touch()
        records = list(importer.import_records(empty_file))
        assert records == []

    def test_resilient_importer_skips_bad_records(self, resilient_importer, test_file):
        """L'importer resiliente deve continuare anche se un record dà errore."""
        records = list(resilient_importer.import_records(test_file))
        # 5 iterazioni, 1 con errore → 4 record validi
        assert len(records) == 4
        ids = [r.source_record_id for r in records]
        assert "2" not in ids  # il record problematico non deve essere incluso


# ---------------------------------------------------------------------------
# validate_source()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidateSource:

    def test_validate_source_raises_file_not_found(self, minimal_importer, tmp_path):
        nonexistent = tmp_path / "ghost.test"
        with pytest.raises(FileNotFoundError, match="ghost.test"):
            minimal_importer.validate_source(nonexistent)

    def test_validate_source_raises_value_error_wrong_type(self, minimal_importer, tmp_path):
        wrong_file = tmp_path / "data.csv"
        wrong_file.touch()
        with pytest.raises(ValueError, match="_MinimalImporter"):
            minimal_importer.validate_source(wrong_file)

    def test_validate_source_passes_for_valid_file(self, minimal_importer, test_file):
        """validate_source() non deve sollevare eccezioni per file valido."""
        minimal_importer.validate_source(test_file)  # nessuna eccezione


# ---------------------------------------------------------------------------
# repr()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRepr:

    def test_repr_contains_class_name(self, minimal_importer):
        assert "_MinimalImporter" in repr(minimal_importer)

    def test_repr_contains_source_name(self, minimal_importer):
        assert "test_source" in repr(minimal_importer)
