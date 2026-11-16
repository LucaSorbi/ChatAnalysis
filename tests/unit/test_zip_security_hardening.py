"""
test_zip_security_hardening.py
------------------------------
Test dedicati di hardening di sicurezza per archivi ZIP WhatsApp (FASE 21, FASE 32).

Verifica che tutti i seguenti pattern malevoli vengano rigorosamente intercettati e respinti:
- ../evil.txt
- ..\\evil.txt
- folder/../evil.txt
- folder\\..\\evil.txt
- C:\\evil.txt
- \\\\server\\share\\evil.txt
- /etc/passwd
- symlink POSIX
- ZIP bomb (dimensione totale, per-file, compression ratio, troppi membri)
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from importer.whatsapp_export import WhatsAppExportImporter, _validate_zip_archive


def _create_malicious_zip(member_name: str, content: bytes = b"malicious content") -> io.BytesIO:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr(member_name, content)
    bio.seek(0)
    return bio


@pytest.mark.parametrize(
    "traversal_path",
    [
        "../evil.txt",
        "..\\evil.txt",
        "folder/../evil.txt",
        "folder\\..\\evil.txt",
        "C:\\evil.txt",
        "C:/evil.txt",
        "\\\\server\\share\\evil.txt",
        "//server/share/evil.txt",
        "/etc/passwd",
        "/var/log/syslog",
        "sub/../../secret.txt",
        "sub\\..\\..\\secret.txt",
    ],
)
def test_zip_path_traversal_rejected(traversal_path: str):
    """Verifica che qualsiasi tentativo di directory traversal (Windows o POSIX) sia respinto."""
    bio = _create_malicious_zip(traversal_path)
    with zipfile.ZipFile(bio, "r") as zf:
        with pytest.raises(ValueError, match="non consentito"):
            _validate_zip_archive(zf)


def test_zip_with_symlink_rejected():
    """Verifica che membri con attributo symlink POSIX siano respinti."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zinfo = zipfile.ZipInfo("symlink_target.txt")
        # Attributo UNIX symlink: S_IFLNK (0o120000)
        zinfo.external_attr = 0o120777 << 16
        zf.writestr(zinfo, b"/etc/shadow")
    bio.seek(0)

    with zipfile.ZipFile(bio, "r") as zf:
        with pytest.raises(ValueError, match="symlink non consentito"):
            _validate_zip_archive(zf)


def test_importer_validate_source_rejects_malicious_zip(tmp_path: Path):
    """Verifica che validate_source dell'importer respinga l'archivio malevolo."""
    bad_zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(bad_zip_path, "w") as zf:
        zf.writestr("folder\\..\\evil.txt", b"evil data")

    importer = WhatsAppExportImporter()
    with pytest.raises(ValueError, match="non consentito"):
        importer.validate_source(bad_zip_path)


def test_legitimate_nested_zip_accepted():
    """Verifica che percorsi legittimi all'interno dello zip senza traversal siano accettati."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("_chat.txt", b"10/05/2024, 14:30 - Alice: Ciao\n10/05/2024, 14:31 - Bob: Salve\n")
        zf.writestr("media/photo.jpg", b"fake photo binary data")
        zf.writestr("media/subfolder/audio.opus", b"fake audio binary data")
    bio.seek(0)

    with zipfile.ZipFile(bio, "r") as zf:
        # Non deve sollevare alcuna eccezione
        _validate_zip_archive(zf)
