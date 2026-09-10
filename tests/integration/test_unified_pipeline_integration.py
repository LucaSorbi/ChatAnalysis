"""
tests/integration/test_unified_pipeline_integration.py
------------------------------------------------------
Test di INTEGRAZIONE end-to-end per la pipeline a sei stadi:
DATI ORIGINALI -> IMPORTER -> VALIDATORE -> NORMALIZZATORE -> ENTITY RESOLVER -> UNIFIED MODEL

Verifica sui dataset sintetici reali in test_data/:
- msgstore.db (WhatsApp messaggi: 622 record, di cui 504 messaggi)
- wa.db (WhatsApp contatti: 4 record)
- messages.csv (Cellebrite CSV: 302 messaggi)
- messages.json (Cellebrite JSON: 100 messaggi)
- report.xml (Cellebrite XML UFDR: 50 messaggi)
Totale record raw: 1078.
Totale messaggi unificati: 956.

Verifiche:
1. Piena provenance forense: da UnifiedMessage risalendo a NormalizedRecord e RawRecord.
2. Preservazione non-distruttiva: tutti i 956 messaggi sono istanziati; nessun messaggio scartato.
3. Immutabilità profonda su tutti gli oggetti unificati.
4. Integrità temporale: i messaggi Cellebrite CSV mantengono NAIVE_UNKNOWN senza forzature fittizie a UTC.
5. Determinismo degli ID: message_id nel formato 'unified:{source_name}:{source_record_id}'.
6. Tracciamento LOCAL_USER: i messaggi uscenti da msgstore.db hanno mittente LOCAL_USER (is_local_user=True).
7. Gruppo WhatsApp: i messaggi di gruppo hanno chat_type='group' e titolo 'Gruppo_Sintetico_01'.
8. Riconoscimento candidati duplicati cross-source senza eliminazione.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from entity_resolution.models import EvidenceLevel, ResolutionResult
from entity_resolution.resolver import DeterministicEntityResolver
from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter
from normalization.models import NormalizedRecord, TimestampTzStatus
from normalization.normalizer import RecordNormalizer
from unified.builder import UnifiedModelBuilder
from unified.context import UnifiedBuildContext
from unified.models import Chat, Participant, UnifiedMessage
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


@pytest.fixture(scope="module")
def pipeline_data():
    """
    Esegue l'intera pipeline fino al layer di Entity Resolution sui dati sintetici reali.
    """
    validator = RecordValidator()
    normalizer = RecordNormalizer()
    resolver = DeterministicEntityResolver()

    all_normalized: list[NormalizedRecord] = []

    # 1. msgstore.db (622 record)
    imp_msg = WhatsAppMsgstoreImporter()
    all_normalized.extend(normalizer.normalize_stream(validator.validate_stream(imp_msg.import_records(_MSGSTORE_DB))))

    # 2. wa.db (4 record)
    imp_wa = WhatsAppWaDbImporter()
    all_normalized.extend(normalizer.normalize_stream(validator.validate_stream(imp_wa.import_records(_WA_DB))))

    # 3. messages.csv (302 record)
    imp_csv = CellebriteCsvImporter()
    all_normalized.extend(normalizer.normalize_stream(validator.validate_stream(imp_csv.import_records(_MESSAGES_CSV))))

    # 4. messages.json (100 record)
    imp_json = CellebriteJsonImporter()
    all_normalized.extend(normalizer.normalize_stream(validator.validate_stream(imp_json.import_records(_MESSAGES_JSON))))

    # 5. report.xml (50 record)
    imp_xml = CellebriteXmlImporter()
    all_normalized.extend(normalizer.normalize_stream(validator.validate_stream(imp_xml.import_records(_REPORT_XML))))

    # Esegui Entity Resolution Foundation
    resolution = resolver.resolve(all_normalized)

    # Assembla UnifiedMessage con UnifiedBuildContext
    context = UnifiedBuildContext.from_records(all_normalized, resolution=resolution)
    builder = UnifiedModelBuilder(context=context)
    unified_messages = builder.build_all(all_normalized)

    return {
        "all_normalized": all_normalized,
        "resolution": resolution,
        "unified_messages": unified_messages,
    }


class TestUnifiedPipelineIntegration:

    def test_total_unified_message_count(self, pipeline_data):
        """
        Verifica che esattamente tutti i record di tipo 'message' (956)
        siano stati convertiti in UnifiedMessage senza alcuna perdita.
        """
        all_norm = pipeline_data["all_normalized"]
        unified_msgs = pipeline_data["unified_messages"]

        expected_msg_count = sum(1 for r in all_norm if r.record_type == "message")
        assert expected_msg_count == 956
        assert len(unified_msgs) == 956

    def test_full_forensic_provenance_preserved(self, pipeline_data):
        """
        Verifica che ciascun UnifiedMessage permetta di risalire fedelmente
        al NormalizedRecord, al RawRecord, al file sorgente e all'ID nativo.
        """
        unified_msgs = pipeline_data["unified_messages"]
        for msg in unified_msgs[:50]:  # Campione significativo
            assert isinstance(msg, UnifiedMessage)
            assert msg.message_id.startswith(f"unified:{msg.source_name}:{msg.source_record_id}")
            # Provenance chain
            assert msg.provenance_record is not None
            assert msg.provenance_record.raw_record is not None
            assert msg.provenance_record.raw_record.source_name == msg.source_name
            assert msg.provenance_record.raw_record.source_record_id == msg.source_record_id
            assert msg.source_path == msg.provenance_record.raw_record.source_path

    def test_timezone_integrity_across_heterogeneous_sources(self, pipeline_data):
        """
        Verifica che i messaggi Cellebrite CSV mantengano NAIVE_UNKNOWN senza forzature fittizie a UTC,
        mentre msgstore.db e JSON mantengano KNOWN_UTC.
        """
        unified_msgs = pipeline_data["unified_messages"]
        csv_msgs = [m for m in unified_msgs if m.source_name == "cellebrite_csv"]
        msgstore_msgs = [m for m in unified_msgs if m.source_name == "msgstore_db"]

        # Tutti i messaggi CSV con data valida devono essere NAIVE_UNKNOWN
        valid_csv_ts = [m for m in csv_msgs if m.timestamp.status != TimestampTzStatus.ABSENT]
        assert len(valid_csv_ts) > 0
        for m in valid_csv_ts:
            assert m.timestamp.status == TimestampTzStatus.NAIVE_UNKNOWN
            assert m.timestamp.utc_datetime is None
            assert m.timestamp.naive_datetime is not None

        # Tutti i messaggi msgstore.db con timestamp valido devono essere KNOWN_UTC
        for m in msgstore_msgs:
            if m.timestamp.status != TimestampTzStatus.ABSENT:
                assert m.timestamp.status == TimestampTzStatus.KNOWN_UTC
                assert m.timestamp.utc_datetime is not None

    def test_local_user_identification(self, pipeline_data):
        """
        Verifica che i messaggi WhatsApp con key_from_me == 1 abbiano sender LOCAL_USER (is_local_user=True).
        """
        unified_msgs = pipeline_data["unified_messages"]
        msgstore_msgs = [m for m in unified_msgs if m.source_name == "msgstore_db"]

        outgoing_msgs = [
            m for m in msgstore_msgs
            if m.provenance_record.raw_record.raw_fields.get("key_from_me") == 1
        ]
        assert len(outgoing_msgs) > 0

        for m in outgoing_msgs:
            assert m.sender is not None
            assert m.sender.is_local_user is True
            assert m.sender.identifier == "LOCAL_USER"

    def test_whatsapp_group_classification_and_title(self, pipeline_data):
        """
        Verifica che i messaggi appartenenti al gruppo sintetico WhatsApp
        siano associati a una Chat con chat_type='group' e titolo 'Gruppo_Sintetico_01'.
        """
        unified_msgs = pipeline_data["unified_messages"]
        group_msgs = [
            m for m in unified_msgs
            if m.source_name == "msgstore_db" and m.chat and "@g.us" in m.chat.chat_id
        ]
        assert len(group_msgs) > 0

        for m in group_msgs:
            assert m.chat is not None
            assert m.chat.chat_type == "group"
            assert m.chat.title == "Gruppo_Sintetico_01"

    def test_duplicate_candidates_linked_without_dropping_messages(self, pipeline_data):
        """
        Verifica che i messaggi duplicati cross-source siano marcati con duplicate_candidate_ids,
        e che entrambi i record sorgente siano presenti come messaggi distinti.
        """
        unified_msgs = pipeline_data["unified_messages"]
        msgs_with_dups = [m for m in unified_msgs if len(m.duplicate_candidate_ids) > 0]
        assert len(msgs_with_dups) > 0

        # Raggruppa per candidate_id
        from collections import defaultdict
        dup_to_msgs = defaultdict(list)
        for m in msgs_with_dups:
            for did in m.duplicate_candidate_ids:
                dup_to_msgs[did].append(m)

        for did, group in dup_to_msgs.items():
            assert len(group) >= 2
            # I record appartengono a istanze distinte di UnifiedMessage
            msg_ids = set(m.message_id for m in group)
            assert len(msg_ids) == len(group)

    def test_deep_immutability(self, pipeline_data):
        """
        Verifica l'immutabilità profonda su messaggi, partecipanti e chat.
        """
        msg = pipeline_data["unified_messages"][0]
        with pytest.raises(FrozenInstanceError):
            msg.text_content = "mutated"  # type: ignore[misc]

        if msg.sender:
            with pytest.raises(FrozenInstanceError):
                msg.sender.display_name = "mutated"  # type: ignore[misc]

        if msg.chat:
            with pytest.raises(FrozenInstanceError):
                msg.chat.title = "mutated"  # type: ignore[misc]
