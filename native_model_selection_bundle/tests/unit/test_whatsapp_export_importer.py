"""
tests/unit/test_whatsapp_export_importer.py
-------------------------------------------
Test unitari per WhatsAppExportImporter.

Principi:
- Test isolati con file sintetici creati in tmp_path.
- NESSUN dato forense reale o file utente.
- Copertura completa per tutte le 16 fixture sintetiche richieste e vincoli forensi.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import socket
from types import MappingProxyType
import zipfile

import pytest

from importer.base import BaseImporter
from importer.models import RawRecord
from importer.whatsapp_export import WhatsAppExportImporter


# ---------------------------------------------------------------------------
# Helpers: costruttori di fixture sintetiche controllate
# ---------------------------------------------------------------------------

def make_synthetic_android_txt(path: Path) -> Path:
    """Fixture 1: Android TXT semplice."""
    content = (
        "12/09/2026, 14:35 - Mario Rossi: Ciao, come stai?\n"
        "12/09/2026, 14:36 - Luca Bianchi: Ciao Mario! Tutto bene qui.\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_ios_txt(path: Path) -> Path:
    """Fixture 2: iOS TXT semplice."""
    content = (
        "[12/09/2026, 14:35:12] Mario Rossi: Ciao da iPhone\n"
        "[12/09/2026, 14:36:00] Luca Bianchi: Ricevuto da iOS\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_multiline_txt(path: Path) -> Path:
    """Fixture 3: Messaggio multilinea con newline preservati."""
    content = (
        "12/09/2026, 14:35 - Mario Rossi: Prima riga del messaggio\n"
        "seconda riga del messaggio\n"
        "terza riga del messaggio\n"
        "12/09/2026, 14:38 - Luca Bianchi: Risposta su riga singola\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_system_txt(path: Path) -> Path:
    """Fixture 4: Messaggi di sistema (crittografia, cambio oggetto)."""
    content = (
        "12/09/2026, 14:30 - I messaggi e le chiamate sono crittografati end-to-end.\n"
        "12/09/2026, 14:31 - Mario Rossi: Ciao a tutti\n"
        "12/09/2026, 14:32 - Hai cambiato l'oggetto in: Discussione Tesi\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_unicode_names_txt(path: Path) -> Path:
    """Fixture 5: Nomi con spazi, accenti e caratteri Unicode/direzionali."""
    content = (
        "\u200e12/09/2026, 14:35 - \u200eRené François Noël: Saluti da Parigi\n"
        "12/09/2026, 14:36 - José María Álvarez: Saludos desde Madrid\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_emoji_txt(path: Path) -> Path:
    """Fixture 6: Emoji sia nei nomi che nel testo."""
    content = (
        "12/09/2026, 14:35 - Mario 🍕: Ciao mondo! 🌍🚀\n"
        "12/09/2026, 14:36 - Luca ☕: Buongiorno! 😊👍\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_urls_txt(path: Path) -> Path:
    """Fixture 7: URL con due punti e porte di rete (non spezzare colons)."""
    content = (
        "12/09/2026, 14:35 - Mario Rossi: Visita https://example.com:8080/test?a=1:2\n"
        "12/09/2026, 14:36 - Luca Bianchi: Guarda anche http://localhost:3000/#section\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_seconds_txt(path: Path) -> Path:
    """Fixture 8: Timestamp con secondi inclusi."""
    content = (
        "12/09/2026, 14:35:45 - Mario Rossi: Messaggio con secondi 1\n"
        "12/09/2026, 14:36:12 - Luca Bianchi: Messaggio con secondi 2\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_ampm_txt(path: Path) -> Path:
    """Fixture 9: Timestamp formato 12h AM/PM."""
    content = (
        "12/09/2026, 2:35 PM - Mario Rossi: Pomeriggio\n"
        "12/09/2026, 09:15 AM - Luca Bianchi: Mattina\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_synthetic_zip_with_image(path: Path) -> Path:
    """Fixture 10: ZIP con transcript + immagine fittizia."""
    chat_content = (
        "12/09/2026, 14:35 - Mario Rossi: IMG-20260912-WA0001.jpg (file allegato)\n"
        "12/09/2026, 14:36 - Luca Bianchi: Bella foto!\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_chat.txt", chat_content.encode("utf-8"))
        zf.writestr("IMG-20260912-WA0001.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")
    return path


def make_synthetic_zip_with_audio(path: Path) -> Path:
    """Fixture 11: ZIP con transcript + audio fittizio."""
    chat_content = (
        "[12/09/2026, 14:35:12] Mario Rossi: <allegato: audio_nota.opus>\n"
        "[12/09/2026, 14:36:00] Luca Bianchi: Ti ascolto ora.\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("chat.txt", chat_content.encode("utf-8"))
        zf.writestr("audio_nota.opus", b"OggS\x00\x02\x00\x00OpusHead")
    return path


def make_synthetic_zip_ambiguous_txts(path: Path) -> Path:
    """Fixture 12: ZIP con più transcript compatibili (deve fallire esplicitamente)."""
    chat_1 = (
        "12/09/2026, 14:35 - Mario Rossi: Chat 1 messaggio 1\n"
        "12/09/2026, 14:36 - Luca Bianchi: Chat 1 messaggio 2\n"
    )
    chat_2 = (
        "12/09/2026, 15:10 - Anna Verdi: Chat 2 messaggio 1\n"
        "12/09/2026, 15:11 - Carlo Neri: Chat 2 messaggio 2\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("export_chat_a.txt", chat_1.encode("utf-8"))
        zf.writestr("export_chat_b.txt", chat_2.encode("utf-8"))
    return path


def make_synthetic_zip_path_traversal(path: Path) -> Path:
    """Fixture 13: ZIP con path traversal (deve essere rifiutato)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_chat.txt", "12/09/2026, 14:35 - Mario: Ciao\n12/09/2026, 14:36 - Luca: Ciao\n")
        # Inserimento forzato di un membro non sicuro con '..'
        info = zipfile.ZipInfo("../evil.txt")
        zf.writestr(info, b"evil content")
    return path


def make_synthetic_zip_malformed(path: Path) -> Path:
    """Fixture 14: ZIP corrotto / troncato."""
    path.write_bytes(b"PK\x03\x04corrupted_zip_payload_header_incomplete")
    return path


def make_synthetic_generic_txt(path: Path) -> Path:
    """Fixture 15: File TXT generico non WhatsApp."""
    path.write_text("Lista della spesa:\n- Mele\n- Pere\n- Pane\n", encoding="utf-8")
    return path


def make_synthetic_generic_zip(path: Path) -> Path:
    """Fixture 16: Archivio ZIP generico senza transcript WhatsApp."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "This is an ordinary project readme.\n")
        zf.writestr("document.pdf", b"%PDF-1.4 dummy document")
    return path


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def importer():
    return WhatsAppExportImporter()


# ---------------------------------------------------------------------------
# BaseImporter Contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppExportInheritsBaseImporter:

    def test_is_base_importer_subclass(self, importer):
        assert isinstance(importer, BaseImporter)

    def test_source_name_is_string(self, importer):
        assert isinstance(importer.source_name, str)

    def test_source_name_value(self, importer):
        assert importer.source_name == "whatsapp_export"

    def test_repr_contains_class_name(self, importer):
        assert "WhatsAppExportImporter" in repr(importer)

    def test_repr_contains_source_name(self, importer):
        assert "whatsapp_export" in repr(importer)


# ---------------------------------------------------------------------------
# can_import — Casi positivi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppExportCanImportPositive:

    def test_can_import_android_txt(self, importer, tmp_path):
        f = make_synthetic_android_txt(tmp_path / "chat_android.txt")
        assert importer.can_import(f) is True

    def test_can_import_ios_txt(self, importer, tmp_path):
        f = make_synthetic_ios_txt(tmp_path / "chat_ios.txt")
        assert importer.can_import(f) is True

    def test_can_import_multiline_txt(self, importer, tmp_path):
        f = make_synthetic_multiline_txt(tmp_path / "chat_multiline.txt")
        assert importer.can_import(f) is True

    def test_can_import_system_txt(self, importer, tmp_path):
        f = make_synthetic_system_txt(tmp_path / "chat_system.txt")
        assert importer.can_import(f) is True

    def test_can_import_zip_with_image(self, importer, tmp_path):
        f = make_synthetic_zip_with_image(tmp_path / "export_image.zip")
        assert importer.can_import(f) is True

    def test_can_import_zip_with_audio(self, importer, tmp_path):
        f = make_synthetic_zip_with_audio(tmp_path / "export_audio.zip")
        assert importer.can_import(f) is True

    def test_can_import_with_utf8_bom(self, importer, tmp_path):
        content = "12/09/2026, 14:35 - Mario: Ciao con BOM\n12/09/2026, 14:36 - Luca: Ricevuto\n"
        bom_file = tmp_path / "chat_bom.txt"
        bom_file.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
        assert importer.can_import(bom_file) is True

    def test_can_import_renamed_no_extension(self, importer, tmp_path):
        src = make_synthetic_android_txt(tmp_path / "chat.txt")
        renamed = tmp_path / "evidence_file_without_ext"
        shutil.copy2(src, renamed)
        assert importer.can_import(renamed) is True

    def test_can_import_is_idempotent(self, importer, tmp_path):
        f = make_synthetic_android_txt(tmp_path / "chat.txt")
        assert importer.can_import(f) is True
        assert importer.can_import(f) is True


# ---------------------------------------------------------------------------
# can_import — Casi negativi
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppExportCanImportNegative:

    def test_returns_false_for_nonexistent_file(self, importer, tmp_path):
        assert importer.can_import(tmp_path / "nonexistent.txt") is False

    def test_returns_false_for_directory(self, importer, tmp_path):
        assert importer.can_import(tmp_path) is False

    def test_returns_false_for_generic_txt(self, importer, tmp_path):
        f = make_synthetic_generic_txt(tmp_path / "generic.txt")
        assert importer.can_import(f) is False

    def test_returns_false_for_generic_zip(self, importer, tmp_path):
        f = make_synthetic_generic_zip(tmp_path / "generic.zip")
        assert importer.can_import(f) is False

    def test_returns_false_for_malformed_zip(self, importer, tmp_path):
        f = make_synthetic_zip_malformed(tmp_path / "malformed.zip")
        assert importer.can_import(f) is False

    def test_returns_false_for_ambiguous_zip(self, importer, tmp_path):
        f = make_synthetic_zip_ambiguous_txts(tmp_path / "ambiguous.zip")
        assert importer.can_import(f) is False

    def test_returns_false_for_zip_with_path_traversal(self, importer, tmp_path):
        f = make_synthetic_zip_path_traversal(tmp_path / "traversal.zip")
        assert importer.can_import(f) is False

    def test_returns_false_for_empty_file(self, importer, tmp_path):
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"")
        assert importer.can_import(empty) is False

    def test_returns_false_for_csv_cellebrite(self, importer, tmp_path):
        csv_file = tmp_path / "messages.csv"
        csv_file.write_text("Source,MessageType,TimeStamp,Direction,ChatId\nWhatsApp,Text,2024-01-01,In,chat_1\n")
        assert importer.can_import(csv_file) is False


# ---------------------------------------------------------------------------
# import_records — Validazione e parsing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppExportImportRecords:

    def test_raises_file_not_found(self, importer, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(importer.import_records(tmp_path / "missing.txt"))

    def test_raises_value_error_for_generic_txt(self, importer, tmp_path):
        f = make_synthetic_generic_txt(tmp_path / "other.txt")
        with pytest.raises(ValueError):
            list(importer.import_records(f))

    def test_raises_value_error_for_ambiguous_zip(self, importer, tmp_path):
        f = make_synthetic_zip_ambiguous_txts(tmp_path / "ambig.zip")
        with pytest.raises(ValueError):
            list(importer.import_records(f))

    def test_android_txt_records_attributes(self, importer, tmp_path):
        f = make_synthetic_android_txt(tmp_path / "android.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        for idx, rec in enumerate(records, start=1):
            assert isinstance(rec, RawRecord)
            assert rec.source_name == "whatsapp_export"
            assert rec.record_type == "message"
            assert rec.source_record_id == f"msg_{idx}"
            assert rec.source_path == str(f)
            assert rec.media_reference is None
            assert rec.metadata["encoding"] == "utf-8"

        rec1 = records[0]
        assert rec1.raw_fields["raw_timestamp"] == "12/09/2026, 14:35"
        assert rec1.raw_fields["sender"] == "Mario Rossi"
        assert rec1.raw_fields["text"] == "Ciao, come stai?"
        assert rec1.raw_fields["line_start"] == 1
        assert rec1.raw_fields["line_end"] == 1
        assert rec1.raw_fields["is_system_message"] is False

        rec2 = records[1]
        assert rec2.raw_fields["raw_timestamp"] == "12/09/2026, 14:36"
        assert rec2.raw_fields["sender"] == "Luca Bianchi"
        assert rec2.raw_fields["text"] == "Ciao Mario! Tutto bene qui."

    def test_ios_txt_records_attributes(self, importer, tmp_path):
        f = make_synthetic_ios_txt(tmp_path / "ios.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        assert records[0].raw_fields["raw_timestamp"] == "12/09/2026, 14:35:12"
        assert records[0].raw_fields["sender"] == "Mario Rossi"
        assert records[0].raw_fields["text"] == "Ciao da iPhone"
        assert records[1].raw_fields["raw_timestamp"] == "12/09/2026, 14:36:00"
        assert records[1].raw_fields["sender"] == "Luca Bianchi"

    def test_multiline_preserves_internal_newlines(self, importer, tmp_path):
        f = make_synthetic_multiline_txt(tmp_path / "multi.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        rec1 = records[0]
        expected_multiline = (
            "Prima riga del messaggio\n"
            "seconda riga del messaggio\n"
            "terza riga del messaggio"
        )
        assert rec1.raw_fields["text"] == expected_multiline
        assert rec1.raw_fields["line_start"] == 1
        assert rec1.raw_fields["line_end"] == 3

        rec2 = records[1]
        assert rec2.raw_fields["text"] == "Risposta su riga singola"
        assert rec2.raw_fields["line_start"] == 4
        assert rec2.raw_fields["line_end"] == 4

    def test_system_messages_classification(self, importer, tmp_path):
        f = make_synthetic_system_txt(tmp_path / "sys.txt")
        records = list(importer.import_records(f))

        assert len(records) == 3

        # Record 1: crittografia (nessun sender)
        assert records[0].raw_fields["is_system_message"] is True
        assert records[0].raw_fields["sender"] is None
        assert "crittografati end-to-end" in records[0].raw_fields["text"]

        # Record 2: normale utente
        assert records[1].raw_fields["is_system_message"] is False
        assert records[1].raw_fields["sender"] == "Mario Rossi"
        assert records[1].raw_fields["text"] == "Ciao a tutti"

        # Record 3: notifica sistema "Hai cambiato l'oggetto in"
        assert records[2].raw_fields["is_system_message"] is True
        assert records[2].raw_fields["sender"] is None

    def test_unicode_and_invisible_characters(self, importer, tmp_path):
        f = make_synthetic_unicode_names_txt(tmp_path / "uni.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        assert records[0].raw_fields["sender"] == "René François Noël"
        assert records[1].raw_fields["sender"] == "José María Álvarez"

    def test_emoji_preservation(self, importer, tmp_path):
        f = make_synthetic_emoji_txt(tmp_path / "emoji.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        assert records[0].raw_fields["sender"] == "Mario 🍕"
        assert records[0].raw_fields["text"] == "Ciao mondo! 🌍🚀"
        assert records[1].raw_fields["sender"] == "Luca ☕"
        assert records[1].raw_fields["text"] == "Buongiorno! 😊👍"

    def test_url_with_colons_not_split(self, importer, tmp_path):
        f = make_synthetic_urls_txt(tmp_path / "urls.txt")
        records = list(importer.import_records(f))

        assert len(records) == 2
        assert records[0].raw_fields["sender"] == "Mario Rossi"
        assert records[0].raw_fields["text"] == "Visita https://example.com:8080/test?a=1:2"
        assert records[1].raw_fields["sender"] == "Luca Bianchi"
        assert records[1].raw_fields["text"] == "Guarda anche http://localhost:3000/#section"

    def test_timestamp_with_seconds_and_ampm(self, importer, tmp_path):
        f_sec = make_synthetic_seconds_txt(tmp_path / "sec.txt")
        recs_sec = list(importer.import_records(f_sec))
        assert recs_sec[0].raw_fields["raw_timestamp"] == "12/09/2026, 14:35:45"

        f_ampm = make_synthetic_ampm_txt(tmp_path / "ampm.txt")
        recs_ampm = list(importer.import_records(f_ampm))
        assert recs_ampm[0].raw_fields["raw_timestamp"] == "12/09/2026, 2:35 PM"

    def test_zip_with_image_attachment_reference(self, importer, tmp_path):
        f = make_synthetic_zip_with_image(tmp_path / "chat_img.zip")
        records = list(importer.import_records(f))

        assert len(records) == 2
        rec_img = records[0]
        assert rec_img.media_reference == "IMG-20260912-WA0001.jpg"
        assert rec_img.metadata["is_archive"] is True
        assert rec_img.metadata["archive_member_name"] == "IMG-20260912-WA0001.jpg"
        assert rec_img.metadata["archive_member_size"] > 0
        assert rec_img.raw_fields["attachment_name"] == "IMG-20260912-WA0001.jpg"

        rec_text = records[1]
        assert rec_text.media_reference is None

    def test_zip_with_audio_attachment_reference(self, importer, tmp_path):
        f = make_synthetic_zip_with_audio(tmp_path / "chat_aud.zip")
        records = list(importer.import_records(f))

        assert len(records) == 2
        rec_aud = records[0]
        assert rec_aud.media_reference == "audio_nota.opus"
        assert rec_aud.metadata["is_archive"] is True
        assert rec_aud.metadata["archive_member_name"] == "audio_nota.opus"


# ---------------------------------------------------------------------------
# Sicurezza Forense & Invarianti
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppExportSecurityAndInvariants:

    def test_deep_immutability(self, importer, tmp_path):
        f = make_synthetic_android_txt(tmp_path / "immut.txt")
        records = list(importer.import_records(f))
        rec = records[0]

        # Verifica MappingProxyType per dizionari
        assert isinstance(rec.raw_fields, MappingProxyType)
        assert isinstance(rec.metadata, MappingProxyType)

        # Tentativo di mutazione attributo
        with pytest.raises((AttributeError, TypeError)):
            rec.source_name = "tampered"

        # Tentativo di mutazione raw_fields
        with pytest.raises(TypeError):
            rec.raw_fields["new_field"] = "hacked"

        # Tentativo di mutazione metadata
        with pytest.raises(TypeError):
            rec.metadata["tamper"] = True

    def test_read_only_source_file_unchanged(self, importer, tmp_path):
        f = make_synthetic_android_txt(tmp_path / "readonly.txt")
        mtime_before = os.path.getmtime(f)
        size_before = os.path.getsize(f)
        bytes_before = f.read_bytes()

        _ = list(importer.import_records(f))

        mtime_after = os.path.getmtime(f)
        size_after = os.path.getsize(f)
        bytes_after = f.read_bytes()

        assert mtime_before == mtime_after
        assert size_before == size_after
        assert bytes_before == bytes_after

    def test_no_network_socket_during_import(self, importer, tmp_path, monkeypatch):
        """Verifica che nessun socket o connessione di rete venga aperta durante l'import."""
        def guarded_connect(*args, **kwargs):
            raise RuntimeError("Tentativo di accesso di rete non consentito durante l'import!")

        monkeypatch.setattr(socket.socket, "connect", guarded_connect)

        f_txt = make_synthetic_android_txt(tmp_path / "net_test.txt")
        records_txt = list(importer.import_records(f_txt))
        assert len(records_txt) == 2

        f_zip = make_synthetic_zip_with_image(tmp_path / "net_test.zip")
        records_zip = list(importer.import_records(f_zip))
        assert len(records_zip) == 2

    def test_zip_path_traversal_rejected(self, importer, tmp_path):
        f = make_synthetic_zip_path_traversal(tmp_path / "trav.zip")
        assert importer.can_import(f) is False
        with pytest.raises(ValueError):
            list(importer.import_records(f))

    def test_zip_bomb_uncompressed_limit_rejected(self, importer, tmp_path, monkeypatch):
        """Verifica limite ZIP bomb con compression ratio anomalo o dimensione decompressa."""
        import importer.whatsapp_export as wa_mod
        monkeypatch.setattr(wa_mod, "_MAX_UNCOMPRESSED_SIZE", 100)

        f = make_synthetic_zip_with_image(tmp_path / "bomb.zip")
        assert importer.can_import(f) is False
        with pytest.raises(ValueError):
            list(importer.import_records(f))

        with zipfile.ZipFile(f, "r") as zf:
            with pytest.raises(ValueError, match="ZIP bomb"):
                wa_mod._validate_zip_archive(zf)

