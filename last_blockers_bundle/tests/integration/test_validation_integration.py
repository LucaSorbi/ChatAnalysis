"""
tests/integration/test_validation_integration.py
------------------------------------------------
Test di INTEGRAZIONE per il layer di Validazione:
DATI ORIGINALI -> IMPORTER -> RawRecord -> VALIDAZIONE -> ValidationResult

Esegue la validazione in streaming sulle 5 sorgenti sintetiche reali in test_data/:
- msgstore.db
- wa.db
- messages.csv
- messages.json
- report.xml
"""
from __future__ import annotations

from pathlib import Path

import pytest

from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter
from validation.models import ValidationResult, ValidationSeverity
from validation.validator import RecordValidator

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CELLEBRITE_DIR = _PROJECT_ROOT / "test_data" / "cellebrite_export"
_WHATSAPP_DIR = _PROJECT_ROOT / "test_data" / "whatsapp_export"

_REPORT_XML = _CELLEBRITE_DIR / "report.xml"
_MESSAGES_JSON = _CELLEBRITE_DIR / "messages.json"
_MESSAGES_CSV = _CELLEBRITE_DIR / "messages.csv"
_MSGSTORE_DB = _WHATSAPP_DIR / "msgstore.db"
_WA_DB = _WHATSAPP_DIR / "wa.db"

pytestmark = pytest.mark.integration


@pytest.fixture
def validator() -> RecordValidator:
    return RecordValidator()


class TestValidationPipelineIntegration:

    def test_whatsapp_msgstore_stream_validation(self, validator):
        importer = WhatsAppMsgstoreImporter()
        stream = validator.validate_stream(importer.import_records(_MSGSTORE_DB))

        results = list(stream)
        assert len(results) > 0
        for res in results:
            assert isinstance(res, ValidationResult)
            assert res.is_valid is True  # Nessun errore bloccante sui dati di test
            # Il RawRecord è preservato integro
            assert res.record.source_name == "msgstore_db"

    def test_whatsapp_wa_stream_validation(self, validator):
        importer = WhatsAppWaDbImporter()
        stream = validator.validate_stream(importer.import_records(_WA_DB))

        results = list(stream)
        assert len(results) > 0
        for res in results:
            assert isinstance(res, ValidationResult)
            assert res.is_valid is True
            assert res.record.source_name == "wa_db"

    def test_cellebrite_csv_stream_validation_observes_empty_timestamp(self, validator):
        """
        Nel CSV sintetico reale è presente un record con timestamp vuoto.
        La pipeline di validazione deve osservarlo, emettere il WARNING appropriato,
        senza arrestarsi e senza alterare il record.
        """
        importer = CellebriteCsvImporter()
        stream = validator.validate_stream(importer.import_records(_MESSAGES_CSV))

        results = list(stream)
        assert len(results) == 302

        # Cerca record con timestamp vuoto
        empty_ts_results = [
            r for r in results
            if any(i.code == "CSV_EMPTY_TIMESTAMP" for i in r.issues)
        ]
        assert len(empty_ts_results) >= 1
        for r in empty_ts_results:
            assert r.has_warnings is True
            assert r.is_valid is True  # Non scartato, non invalido
            assert r.record.raw_fields["TimeStamp"] == ""

    def test_cellebrite_json_stream_validation(self, validator):
        importer = CellebriteJsonImporter()
        stream = validator.validate_stream(importer.import_records(_MESSAGES_JSON))

        results = list(stream)
        assert len(results) == 100
        for res in results:
            assert isinstance(res, ValidationResult)
            assert res.is_valid is True
            assert res.record.source_name == "cellebrite_json"

    def test_cellebrite_xml_stream_validation(self, validator):
        """
        Nel report XML UFDR reale, i record sono strutturalmente validi.
        L'assenza di ChatId è una caratteristica del formato e non genera issue per-record.
        """
        importer = CellebriteXmlImporter()
        stream = validator.validate_stream(importer.import_records(_REPORT_XML))

        results = list(stream)
        assert len(results) == 50
        for res in results:
            assert isinstance(res, ValidationResult)
            assert res.is_valid is True
            assert res.has_errors is False
            assert len(res.issues) == 0
