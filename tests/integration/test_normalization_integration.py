"""
tests/integration/test_normalization_integration.py
---------------------------------------------------
Test di INTEGRAZIONE per l'intera pipeline a tre stadi:
DATI ORIGINALI -> IMPORTER -> VALIDATORE -> NORMALIZZATORE -> NormalizedRecord

Esegue la pipeline streaming sui file reali sintetici in test_data/:
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
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    TimestampTzStatus,
)
from normalization.normalizer import RecordNormalizer
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


@pytest.fixture
def normalizer() -> RecordNormalizer:
    return RecordNormalizer()


class TestNormalizationPipelineIntegration:

    def test_whatsapp_msgstore_pipeline(self, validator, normalizer):
        importer = WhatsAppMsgstoreImporter()
        raw_stream = importer.import_records(_MSGSTORE_DB)
        val_stream = validator.validate_stream(raw_stream)
        norm_stream = normalizer.normalize_stream(val_stream)

        records = list(norm_stream)
        assert len(records) == 622
        for r in records:
            assert isinstance(r, NormalizedRecord)
            assert r.source_name == "msgstore_db"
            if r.record_type == "message":
                assert r.message_type in (
                    CanonicalMessageType.TEXT,
                    CanonicalMessageType.IMAGE,
                    CanonicalMessageType.AUDIO,
                    CanonicalMessageType.VIDEO,
                    CanonicalMessageType.UNKNOWN,
                )

    def test_whatsapp_wa_pipeline(self, validator, normalizer):
        importer = WhatsAppWaDbImporter()
        raw_stream = importer.import_records(_WA_DB)
        val_stream = validator.validate_stream(raw_stream)
        norm_stream = normalizer.normalize_stream(val_stream)

        records = list(norm_stream)
        assert len(records) == 4
        for r in records:
            assert isinstance(r, NormalizedRecord)
            assert r.source_name == "wa_db"
            assert r.record_type == "contact"

    def test_cellebrite_csv_pipeline_and_empty_timestamp(self, validator, normalizer):
        """
        Nel CSV sintetico reale, la riga con timestamp vuoto deve essere
        normalizzata come ABSENT, mentre le altre devono essere NAIVE_UNKNOWN.
        """
        importer = CellebriteCsvImporter()
        raw_stream = importer.import_records(_MESSAGES_CSV)
        val_stream = validator.validate_stream(raw_stream)
        norm_stream = normalizer.normalize_stream(val_stream)

        records = list(norm_stream)
        assert len(records) == 302

        absent_ts = [r for r in records if r.timestamp.status == TimestampTzStatus.ABSENT]
        assert len(absent_ts) >= 1
        assert absent_ts[0].timestamp.utc_datetime is None
        assert absent_ts[0].timestamp.naive_datetime is None

        naive_ts = [r for r in records if r.timestamp.status == TimestampTzStatus.NAIVE_UNKNOWN]
        assert len(naive_ts) == 302 - len(absent_ts)
        assert all(r.timestamp.naive_datetime is not None for r in naive_ts)

    def test_cellebrite_json_pipeline(self, validator, normalizer):
        importer = CellebriteJsonImporter()
        raw_stream = importer.import_records(_MESSAGES_JSON)
        val_stream = validator.validate_stream(raw_stream)
        norm_stream = normalizer.normalize_stream(val_stream)

        records = list(norm_stream)
        assert len(records) == 100
        for r in records:
            assert isinstance(r, NormalizedRecord)
            assert r.source_name == "cellebrite_json"
            assert r.timestamp.status == TimestampTzStatus.KNOWN_UTC
            assert r.timestamp.utc_datetime is not None

    def test_cellebrite_xml_pipeline(self, validator, normalizer):
        importer = CellebriteXmlImporter()
        raw_stream = importer.import_records(_REPORT_XML)
        val_stream = validator.validate_stream(raw_stream)
        norm_stream = normalizer.normalize_stream(val_stream)

        records = list(norm_stream)
        assert len(records) == 50
        for r in records:
            assert isinstance(r, NormalizedRecord)
            assert r.source_name == "cellebrite_xml"
            assert r.timestamp.status == TimestampTzStatus.KNOWN_UTC
            assert r.timestamp.utc_datetime is not None
            assert r.message_type == CanonicalMessageType.UNKNOWN

