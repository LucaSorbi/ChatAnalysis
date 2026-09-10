"""
tests/integration/test_whatsapp_msgstore_integration.py
---------------------------------------------------------
Test di INTEGRAZIONE per WhatsAppMsgstoreImporter.

Usa il database sintetico reale presente in test_data/.
Nessun dato forense reale — il dataset è stato creato sinteticamente
per il progetto (numeri di telefono nel formato +39 333 XXXXXXX,
testi inventati, JID artificiali).

ATTENZIONE: questi test aprono test_data/whatsapp_export/msgstore.db
in modalità read-only. Il file non viene mai modificato.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from importer.models import RawRecord
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter

# ---------------------------------------------------------------------------
# Path al DB sintetico
# ---------------------------------------------------------------------------

# Calcola il path rispetto alla root del progetto (due livelli su da tests/integration/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MSGSTORE_DB = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "msgstore.db"


# ---------------------------------------------------------------------------
# Skip automatico se il DB non è disponibile
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

if not _MSGSTORE_DB.exists():
    pytestmark = pytest.mark.skip(reason=f"DB sintetico non trovato: {_MSGSTORE_DB}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def importer():
    return WhatsAppMsgstoreImporter()


@pytest.fixture(scope="module")
def all_records(importer):
    """Carica tutti i record dal DB sintetico una volta sola per il modulo."""
    return list(importer.import_records(_MSGSTORE_DB))


@pytest.fixture(scope="module")
def message_records(all_records):
    return [r for r in all_records if r.record_type == "message"]


@pytest.fixture(scope="module")
def chat_records(all_records):
    return [r for r in all_records if r.record_type == "chat"]


@pytest.fixture(scope="module")
def media_ref_records(all_records):
    return [r for r in all_records if r.record_type == "media_ref"]


# ---------------------------------------------------------------------------
# can_import sul DB reale sintetico
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCanImportReal:

    def test_can_import_real_msgstore(self, importer):
        """A. Il vero msgstore.db sintetico → True."""
        assert importer.can_import(_MSGSTORE_DB) is True

    def test_renamed_real_db_no_extension(self, importer, tmp_path):
        """
        B. Copia del DB sintetico reale rinominata senza estensione → True.
        NON si modifica il file originale: si usa una copia temporanea.
        """
        import shutil
        renamed = tmp_path / "msgstore_rinominato"
        shutil.copy2(_MSGSTORE_DB, renamed)
        assert importer.can_import(renamed) is True

    def test_renamed_real_db_bak_extension(self, importer, tmp_path):
        """
        B. Copia del DB sintetico reale con estensione .bak → True.
        """
        import shutil
        renamed = tmp_path / "evidence.bak"
        shutil.copy2(_MSGSTORE_DB, renamed)
        assert importer.can_import(renamed) is True

    def test_cannot_import_wa_db(self, importer):
        """wa.db ha uno schema diverso e non deve essere accettato da questo importer."""
        wa_db = _PROJECT_ROOT / "test_data" / "whatsapp_export" / "wa.db"
        if wa_db.exists():
            result = importer.can_import(wa_db)
            # wa.db non contiene chat_list con le colonne WhatsApp standard di messages
            assert result is False


# ---------------------------------------------------------------------------
# Conteggio record attesi
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRecordCounts:

    def test_total_records_positive(self, all_records):
        assert len(all_records) > 0

    def test_message_count_matches_db(self, message_records):
        """La Fase 1 ha rilevato 504 messaggi in msgstore.db."""
        assert len(message_records) == 504

    def test_chat_count_matches_db(self, chat_records):
        """La Fase 1 ha rilevato 3 chat in msgstore.db."""
        assert len(chat_records) == 3

    def test_media_ref_count_matches_db(self, media_ref_records):
        """La Fase 1 ha rilevato 115 media_refs in msgstore.db."""
        assert len(media_ref_records) == 115

    def test_total_count(self, all_records):
        """504 messages + 3 chats + 115 media_refs = 622 record totali."""
        assert len(all_records) == 622


# ---------------------------------------------------------------------------
# Struttura RawRecord
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRawRecordStructure:

    def test_all_records_are_raw_record(self, all_records):
        for r in all_records:
            assert isinstance(r, RawRecord)

    def test_source_name_consistent(self, all_records):
        for r in all_records:
            assert r.source_name == "msgstore_db"

    def test_source_path_consistent(self, all_records):
        expected_path = str(_MSGSTORE_DB.resolve())
        for r in all_records:
            assert r.source_path == expected_path

    def test_record_ids_unique_per_type(self, all_records):
        """I (record_type, source_record_id) devono essere unici."""
        keys = [(r.record_type, r.source_record_id) for r in all_records]
        assert len(keys) == len(set(keys))

    def test_all_source_record_ids_are_strings(self, all_records):
        for r in all_records:
            assert isinstance(r.source_record_id, str)

    def test_message_record_types(self, message_records):
        for r in message_records:
            assert r.record_type == "message"

    def test_chat_record_types(self, chat_records):
        for r in chat_records:
            assert r.record_type == "chat"

    def test_media_ref_record_types(self, media_ref_records):
        for r in media_ref_records:
            assert r.record_type == "media_ref"


# ---------------------------------------------------------------------------
# Campi messaggi — presenza e tipi
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMessageFields:

    def test_key_remote_jid_present(self, message_records):
        for msg in message_records:
            assert "key_remote_jid" in msg.raw_fields

    def test_key_from_me_is_int(self, message_records):
        for msg in message_records:
            val = msg.raw_fields["key_from_me"]
            assert isinstance(val, (int, type(None)))

    def test_timestamp_is_integer(self, message_records):
        """timestamp deve essere unix_ms (int) — mai stringa o datetime."""
        for msg in message_records:
            ts = msg.raw_fields["timestamp"]
            assert isinstance(ts, (int, type(None))), (
                f"timestamp di tipo inatteso: {type(ts)} per _id={msg.source_record_id}"
            )

    def test_deleted_is_integer_flag(self, message_records):
        """deleted deve essere 0 o 1 — valore int raw dalla sorgente."""
        for msg in message_records:
            val = msg.raw_fields["deleted"]
            assert val in (0, 1, None), f"deleted inatteso: {val}"
            assert not isinstance(val, bool)

    def test_media_wa_type_observed_values(self, message_records):
        """Verifica i media_wa_type effettivamente presenti nel dataset."""
        observed = {msg.raw_fields["media_wa_type"] for msg in message_records}
        # Dalla Fase 1: 0 (text), 1 (image), 2 (audio), 3 (video)
        assert 0 in observed
        assert 1 in observed

    def test_deleted_messages_count(self, message_records):
        """La Fase 1 ha rilevato 14 messaggi eliminati."""
        deleted = [m for m in message_records if m.raw_fields.get("deleted") == 1]
        assert len(deleted) == 14

    def test_anomalous_timestamps_pass_through(self, message_records):
        """
        Timestamp anomali (0, negativi) devono passare senza crash.
        Il DB sintetico contiene _id=502 (ts=0) e _id=504 (ts=-1000).
        """
        anomalous = [
            m for m in message_records
            if m.raw_fields.get("timestamp") is not None
            and m.raw_fields["timestamp"] <= 0
        ]
        assert len(anomalous) == 2, (
            f"Attesi 2 timestamp anomali, trovati {len(anomalous)}"
        )


# ---------------------------------------------------------------------------
# JID — preservazione senza normalizzazione
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestJIDsNotNormalized:

    def test_jids_contain_at_symbol(self, message_records):
        """I JID WhatsApp contengono '@' — non devono essere stati convertiti."""
        jids = [m.raw_fields["key_remote_jid"] for m in message_records if m.raw_fields.get("key_remote_jid")]
        assert all("@" in jid for jid in jids), "Trovato JID senza '@' — potrebbe essere normalizzato"

    def test_jid_domain_preserved(self, message_records):
        """Il dominio WhatsApp deve essere preservato."""
        jids = [m.raw_fields["key_remote_jid"] for m in message_records if m.raw_fields.get("key_remote_jid")]
        # Tutti i JID nel sintetico usano @s.whatsapp.net o @g.us
        valid_domains = {"@s.whatsapp.net", "@g.us", "@broadcast"}
        for jid in jids:
            assert any(d in jid for d in valid_domains), f"Dominio JID inatteso: {jid}"


# ---------------------------------------------------------------------------
# Read-only — il DB non viene modificato
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestReadOnlyReal:

    def test_db_unchanged_after_full_import(self, all_records):
        """
        Il fatto che all_records sia stato caricato senza errori e il DB
        sia ancora accessibile dimostra che non è stato corrotto.
        Verifica il conteggio come proxy di integrità.
        """
        import sqlite3
        conn = sqlite3.connect(_MSGSTORE_DB)
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 504  # stesso count pre-import

    def test_db_file_accessible_after_import(self, importer, all_records):
        """Dopo l'import, can_import deve ancora restituire True (file non corrotto)."""
        assert importer.can_import(_MSGSTORE_DB) is True


# ---------------------------------------------------------------------------
# media_refs
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMediaRefs:

    def test_media_refs_have_file_path(self, media_ref_records):
        """Tutti i media_ref devono avere file_path e media_reference non None."""
        for ref in media_ref_records:
            assert "file_path" in ref.raw_fields
            fp = ref.raw_fields["file_path"]
            if fp is not None:
                assert isinstance(fp, str)
                assert ref.media_reference == fp

    def test_media_refs_link_to_messages(self, media_ref_records):
        """media_refs devono avere message_row_id non None."""
        for ref in media_ref_records:
            assert "message_row_id" in ref.raw_fields

    def test_media_type_field_preserved(self, media_ref_records):
        for ref in media_ref_records:
            assert "media_type" in ref.raw_fields
