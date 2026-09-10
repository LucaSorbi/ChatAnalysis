"""
tests/integration/test_entity_resolution_integration.py
-------------------------------------------------------
Test di INTEGRAZIONE per l'intera pipeline forense a cinque stadi:
DATI ORIGINALI -> IMPORTER -> VALIDATORE -> NORMALIZZATORE -> ENTITY RESOLVER

Esegue la risoluzione deterministica sui dataset sintetici reali in test_data/:
- msgstore.db (WhatsApp messaggi)
- wa.db (WhatsApp rubrica contatti)
- messages.csv (Cellebrite CSV)
- messages.json (Cellebrite JSON)
- report.xml (Cellebrite XML UFDR)
"""
from __future__ import annotations

from pathlib import Path
import pytest

from entity_resolution.models import EvidenceLevel, EvidenceType, ResolutionResult
from entity_resolution.resolver import DeterministicEntityResolver
from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter
from normalization.models import NormalizedRecord
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


@pytest.fixture(scope="module")
def all_normalized_records() -> list[NormalizedRecord]:
    """
    Esegue streaming completo di import, validazione e normalizzazione
    su tutte le 5 sorgenti sintetiche reali.
    """
    validator = RecordValidator()
    normalizer = RecordNormalizer()
    records: list[NormalizedRecord] = []

    # 1. msgstore.db (622 record)
    imp_msg = WhatsAppMsgstoreImporter()
    records.extend(normalizer.normalize_stream(validator.validate_stream(imp_msg.import_records(_MSGSTORE_DB))))

    # 2. wa.db (4 record)
    imp_wa = WhatsAppWaDbImporter()
    records.extend(normalizer.normalize_stream(validator.validate_stream(imp_wa.import_records(_WA_DB))))

    # 3. messages.csv (302 record)
    imp_csv = CellebriteCsvImporter()
    records.extend(normalizer.normalize_stream(validator.validate_stream(imp_csv.import_records(_MESSAGES_CSV))))

    # 4. messages.json (100 record)
    imp_json = CellebriteJsonImporter()
    records.extend(normalizer.normalize_stream(validator.validate_stream(imp_json.import_records(_MESSAGES_JSON))))

    # 5. report.xml (50 record)
    imp_xml = CellebriteXmlImporter()
    records.extend(normalizer.normalize_stream(validator.validate_stream(imp_xml.import_records(_REPORT_XML))))

    return records


@pytest.fixture(scope="module")
def resolution_result(all_normalized_records) -> ResolutionResult:
    resolver = DeterministicEntityResolver()
    return resolver.resolve(all_normalized_records)


class TestEntityResolutionIntegration:

    def test_total_records_processed(self, resolution_result, all_normalized_records):
        assert resolution_result.total_records_processed == len(all_normalized_records)
        # 622 + 4 + 302 + 100 + 50 = 1078
        assert resolution_result.total_records_processed == 1078

    def test_whatsapp_jid_and_wa_db_exact_link(self, resolution_result):
        """
        Requisito B3: i JID '+390000000001@s.whatsapp.net' e '+390000000002@s.whatsapp.net'
        presenti in msgstore.db devono corrispondere esattamente ai contatti in wa.db.
        """
        entities_by_id = {e.canonical_identifier: e for e in resolution_result.candidate_entities}

        # Verifica entità per +390000000001@s.whatsapp.net
        assert "+390000000001@s.whatsapp.net" in entities_by_id
        ent1 = entities_by_id["+390000000001@s.whatsapp.net"]
        assert "Contatto_001" in ent1.display_names

        sources1 = {r.source_name for r in ent1.references}
        assert "msgstore_db" in sources1
        assert "wa_db" in sources1

        # Verifica evidenza EXACT
        assert any(
            ev.evidence_type == EvidenceType.JID_EXACT and ev.evidence_level == EvidenceLevel.EXACT
            for ev in ent1.evidence_chain
        )

        # Verifica entità per +390000000002@s.whatsapp.net
        assert "+390000000002@s.whatsapp.net" in entities_by_id
        ent2 = entities_by_id["+390000000002@s.whatsapp.net"]
        assert "Contatto_002" in ent2.display_names
        sources2 = {r.source_name for r in ent2.references}
        assert "msgstore_db" in sources2
        assert "wa_db" in sources2

    def test_group_participant_a_remains_unresolved(self, resolution_result):
        """
        Requisito B5: 'group_participant_A' deve rimanere esplicitamente UNRESOLVED.
        Nessuna entità candidata deve avere 'group_participant_A' come identificatore.
        """
        candidate_ids = {e.canonical_identifier for e in resolution_result.candidate_entities}
        assert "group_participant_A" not in candidate_ids

        unres_vals = {u.raw_value for u in resolution_result.unresolved_references}
        assert "group_participant_A" in unres_vals

    def test_duplicate_candidates_detected_without_deleting_records(self, resolution_result):
        """
        Requisito B8: duplicati candidati identificati su messaggi sintetici identici
        condivisi tra le esportazioni Cellebrite / WhatsApp, senza eliminare alcun record.
        """
        dups = resolution_result.duplicate_candidates
        assert len(dups) > 0
        # Tutti i record sorgente coinvolti sono accessibili
        for d in dups:
            assert len(d.records) >= 2
            assert d.confidence in (EvidenceLevel.STRONG, EvidenceLevel.WEAK)
            assert d.confidence != EvidenceLevel.EXACT


    def test_group_jid_isolated_and_local_user_present(self, resolution_result):
        """
        Verifica che il gruppo JID non sia trattato come partecipante persona,
        e che LOCAL_USER sia tracciato come CandidateEntity con entity_type='local_user'.
        """
        entities_by_id = {e.candidate_identifier: e for e in resolution_result.candidate_entities}
        assert "LOCAL_USER" in entities_by_id
        local_ent = entities_by_id["LOCAL_USER"]
        assert local_ent.entity_type == "local_user"

        # Il JID del gruppo non deve essere unito a persone
        for ent in resolution_result.candidate_entities:
            if ent.candidate_identifier.endswith("@g.us"):
                assert ent.entity_type == "group"

    def test_no_unified_message_created(self, resolution_result):
        """
        Vincolo architetturale: nessun oggetto UnifiedMessage deve essere istanziato.
        """
        assert isinstance(resolution_result, ResolutionResult)
        assert not hasattr(resolution_result, "unified_messages")
