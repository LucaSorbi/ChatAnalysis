"""
tests/unit/test_verify_package_manifest.py
------------------------------------------
Test avversari e di sicurezza per lo script scripts/verify_package_manifest.py.
Verifica:
1. Rilevazione e rigetto di nomi file duplicati grezzi all'interno dell'archivio ZIP.
2. Rilevazione e rigetto di collisioni di percorsi normalizzati (es. backslash vs slash).
3. Rigetto di percorsi assoluti (Unix '/' o Windows 'C:').
4. Rigetto di tentativi di Directory Traversal ('..').
5. Rigetto di separatori ambigui o doppi ('//' o '\\\\').
6. Rigetto di digest SHA-256 malformati o con sintassi non esadecimale a 64 caratteri.
7. Rigetto di manifest che referenziano se stessi (auto-referenzialità).
8. Rigetto di file registrati nel manifest ma mancanti nello ZIP.
9. Rigetto di file presenti nello ZIP ma omessi dal manifest.
10. Rigetto di mismatch di hash tra file reale e registrazione manifest.
11. Successo (VERIFIED_PASS) su archivio sintetico valido, conforme e coerente.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import zipfile

import pytest

from scripts.verify_package_manifest import verify_zip_manifest, compute_sha256_bytes


def _create_minimal_manifest(records: dict[str, str]) -> bytes:
    lines = [
        "# ChatAnalysis Test Manifest",
        f"# Total Files: {len(records)}",
    ]
    for p, h in sorted(records.items()):
        lines.append(f"{h}  {p}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_verify_zip_duplicate_raw_filename_rejected(tmp_path: Path):
    """Verifica che un archivio ZIP contenente due file con lo stesso nome esatto venga rigettato."""
    zip_p = tmp_path / "duplicate_raw.zip"

    data1 = b"CONTENT_1"
    data2 = b"CONTENT_2"
    h1 = hashlib.sha256(data1).hexdigest()

    manifest_bytes = _create_minimal_manifest({"file1.txt": h1})

    # Creazione ZIP con due voci 'file1.txt' (duplicato a livello Central Directory)
    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("file1.txt", data1)
        zf.writestr("file1.txt", data2)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert metrics["status"] == "FAILED"
    assert metrics["duplicate_entries"] >= 1
    assert any("duplicato" in err.lower() for err in metrics["errors"])


def test_verify_zip_normalized_collision_rejected(tmp_path: Path):
    """Verifica che percorsi differenti grezzi che normalizzano allo stesso percorso POSIX vengano rigettati."""
    zip_p = tmp_path / "normalized_collision.zip"

    data1 = b"CONTENT_A"
    data2 = b"CONTENT_B"
    h1 = hashlib.sha256(data1).hexdigest()

    manifest_bytes = _create_minimal_manifest({"folder/sub.txt": h1})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("folder/sub.txt", data1)
        zf.writestr("folder\\sub.txt", data2)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert metrics["status"] == "FAILED"
    assert metrics["duplicate_entries"] >= 1
    assert any("collisione" in err.lower() for err in metrics["errors"])


def test_verify_zip_absolute_path_unix_rejected(tmp_path: Path):
    """Verifica che percorsi assoluti Unix vengano rigettati."""
    zip_p = tmp_path / "abs_unix.zip"
    data = b"EVIL_UNIX"
    h = hashlib.sha256(data).hexdigest()

    manifest_bytes = _create_minimal_manifest({"/etc/shadow": h})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("/etc/shadow", data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("assoluto" in err.lower() for err in metrics["errors"])


def test_verify_zip_absolute_path_windows_rejected(tmp_path: Path):
    """Verifica che percorsi assoluti Windows vengano rigettati."""
    zip_p = tmp_path / "abs_win.zip"
    data = b"EVIL_WINDOWS"
    h = hashlib.sha256(data).hexdigest()

    manifest_bytes = _create_minimal_manifest({"C:/Windows/system.ini": h})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("C:/Windows/system.ini", data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("assoluto" in err.lower() for err in metrics["errors"])


def test_verify_zip_directory_traversal_rejected(tmp_path: Path):
    """Verifica che tentativi di directory traversal '..' vengano rigettati."""
    zip_p = tmp_path / "traversal.zip"
    data = b"TRAVERSAL_PAYLOAD"
    h = hashlib.sha256(data).hexdigest()

    manifest_bytes = _create_minimal_manifest({"../../secret.txt": h})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("../../secret.txt", data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("traversal" in err.lower() for err in metrics["errors"])


def test_verify_zip_ambiguous_separators_rejected(tmp_path: Path):
    """Verifica che separatori ambigui o doppi '//' vengano rigettati."""
    zip_p = tmp_path / "ambiguous_sep.zip"
    data = b"AMBIGUOUS_DATA"
    h = hashlib.sha256(data).hexdigest()

    manifest_bytes = _create_minimal_manifest({"sub//file.txt": h})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("sub//file.txt", data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("ambigui" in err.lower() for err in metrics["errors"])


def test_verify_zip_invalid_sha256_syntax_rejected(tmp_path: Path):
    """Verifica che hash non validi sintatticamente (es. lunghezza errata o caratteri non-hex) vengano rigettati."""
    zip_p = tmp_path / "invalid_sha.zip"
    data = b"VALID_DATA"

    # Hash lungo solo 63 caratteri (non 64)
    short_hash = "a" * 63
    manifest_bytes = _create_minimal_manifest({"valid.txt": short_hash})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("valid.txt", data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("sintatticamente" in err.lower() for err in metrics["errors"])


def test_verify_zip_self_referencing_manifest_rejected(tmp_path: Path):
    """Verifica che un manifest che elenca se stesso venga rigettato."""
    zip_p = tmp_path / "self_ref.zip"
    dummy_sha = "0" * 64
    manifest_bytes = _create_minimal_manifest({"manifest_sha256.txt": dummy_sha})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert any("se stesso" in err.lower() for err in metrics["errors"])


def test_verify_zip_missing_and_unlisted_files_rejected(tmp_path: Path):
    """Verifica che file mancanti nello ZIP o file non registrati nel manifest vengano rigettati."""
    zip_p = tmp_path / "completeness.zip"
    data1 = b"FILE_1_PAYLOAD"
    h1 = hashlib.sha256(data1).hexdigest()
    fake_sha = "f" * 64

    # Manifest include file1.txt e missing.txt, ma nello zip c'è solo file1.txt e un unlisted.txt
    manifest_bytes = _create_minimal_manifest({
        "file1.txt": h1,
        "missing.txt": fake_sha,
    })

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("file1.txt", data1)
        zf.writestr("unlisted.txt", b"EXTRA_FILE")

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert metrics["missing_files_in_zip"] == 1
    assert metrics["unlisted_files_in_manifest"] == 1


def test_verify_zip_hash_mismatch_rejected(tmp_path: Path):
    """Verifica che alterazioni di byte anche di un solo bit vengano rilevate come mismatch."""
    zip_p = tmp_path / "mismatch.zip"
    actual_data = b"REAL_AUTHENTIC_BYTES"
    tampered_sha = hashlib.sha256(b"CORRUPTED_BYTES").hexdigest()

    manifest_bytes = _create_minimal_manifest({"file.txt": tampered_sha})

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("file.txt", actual_data)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is False
    assert metrics["mismatched_hashes"] == 1


def test_verify_zip_valid_archive_passes(tmp_path: Path):
    """Verifica che un archivio valido e conforme superi la verifica al 100%."""
    zip_p = tmp_path / "valid.zip"
    f1 = b"DATA_1"
    f2 = b"DATA_2"
    h1 = hashlib.sha256(f1).hexdigest()
    h2 = hashlib.sha256(f2).hexdigest()

    manifest_bytes = _create_minimal_manifest({
        "pkg/a.py": h1,
        "pkg/b.py": h2,
    })

    with zipfile.ZipFile(zip_p, "w") as zf:
        zf.writestr("manifest_sha256.txt", manifest_bytes)
        zf.writestr("pkg/a.py", f1)
        zf.writestr("pkg/b.py", f2)

    success, metrics = verify_zip_manifest(zip_p, run_tests=False)
    assert success is True
    assert metrics["status"] == "VERIFIED_PASS"
    assert metrics["correct_hashes"] == 2
    assert metrics["manifest_entries"] == 2
    assert len(metrics["errors"]) == 0
