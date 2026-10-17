"""
tests/unit/test_packaging_release.py
------------------------------------
Test di packaging REALE per il release gate di ChatAnalysis:
1. Costruisce una wheel reale dal repository (in una directory temporanea).
2. Apre la wheel costruita (zipfile) ed esamina il manifest.
3. Verifica la presenza tassativa del package analysis/ e dei suoi file:
   - analysis/__init__.py, models.py, consistency.py, service.py, checkpoint.py, progress.py
4. Verifica la presenza di tutti i package fondamentali:
   - ai/, core/, entity_resolution/, importer/, multimodal/, normalization/,
     search/, ui/, unified/, validation/
5. Estrae la wheel in un ambiente isolato (senza repo root in sys.path)
   e verifica che gli import fondamentali abbiano successo.
6. Verifica l'unicità del manifest SHA256 e l'assenza di duplicati anche su esecuzioni ripetute.
7. Verifica il rigetto di database non approvati (.sqlite3, .db3, WAL, SHM, journal, magic headers).
8. Verifica il rigetto di collegamenti simbolici e tentativi di path traversal.
9. Verifica l'esclusione di .env, chiavi, credenziali e file privati sotto test_data/.
10. Verifica l'inventario esplicito dei percorsi (rifiuto di file sconosciuti con nomi ordinari fuori da test_data/).
11. Verifica l'integrità delle fixture sintetiche via SHA256 (rifiuto di fixture manomesse o sostituite).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_packaging_wheel_contains_analysis_and_core_packages():
    """Costruisce una wheel reale e verifica i package inclusi e l'import isolato."""
    # Assicura ambiente di build pulito privo di residui da compilazioni precedenti
    shutil.rmtree(REPO_ROOT / "build", ignore_errors=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        dist_dir = Path(tmp_dir) / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)

        # 1. Costruzione wheel tramite pip wheel --no-deps --no-build-isolation
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(dist_dir),
            str(REPO_ROOT),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"pip wheel fallita:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

        wheels = list(dist_dir.glob("*.whl"))
        assert len(wheels) == 1, f"Attesa 1 wheel, trovate: {wheels}"
        whl_path = wheels[0]

        # 2. Ispezione archivio wheel
        with zipfile.ZipFile(whl_path, "r") as zf:
            namelist = set(zf.namelist())

            # 3. Verifica package analysis/
            required_analysis_files = [
                "analysis/__init__.py",
                "analysis/models.py",
                "analysis/consistency.py",
                "analysis/service.py",
                "analysis/checkpoint.py",
                "analysis/progress.py",
            ]
            for req_file in required_analysis_files:
                assert req_file in namelist, f"File obbligatorio mancante nella wheel: {req_file}"

            # 4. Verifica package fondamentali
            fundamental_packages = [
                "ai/__init__.py",
                "core/__init__.py",
                "entity_resolution/__init__.py",
                "importer/__init__.py",
                "multimodal/__init__.py",
                "normalization/__init__.py",
                "search/__init__.py",
                "ui/__init__.py",
                "unified/__init__.py",
                "validation/__init__.py",
            ]
            for f_pkg in fundamental_packages:
                assert f_pkg in namelist, f"Package fondamentale mancante nella wheel: {f_pkg}"

            # Verifica assenza assoluta di __pycache__ e *.pyc
            pycache_entries = [n for n in namelist if "__pycache__" in n or n.endswith(".pyc")]
            assert not pycache_entries, f"Wheel contiene residui di compilazione o cache: {pycache_entries}"

        # 5. Test di import isolato (senza repo root in PYTHONPATH)
        extract_dir = Path(tmp_dir) / "isolated_site_packages"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(whl_path, "r") as zf:
            zf.extractall(extract_dir)

        isolated_script = (
            "import sys\n"
            "import analysis\n"
            "from analysis.models import OperationalAnalysisConfig, DocumentAnalysisState\n"
            "from analysis.service import LocalTopicAnalysisService\n"
            "import ui.application\n"
            "import ai.lmstudio\n"
            "import importer.whatsapp_export\n"
            "import multimodal.asset_access\n"
            "print('ISOLATED_IMPORT_SUCCESS')\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(extract_dir)
        proc = subprocess.run(
            [sys.executable, "-c", isolated_script],
            capture_output=True,
            text=True,
            cwd=str(extract_dir),
            env=env,
            check=False,
        )
        assert proc.returncode == 0, f"Import isolato fallito:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert "ISOLATED_IMPORT_SUCCESS" in proc.stdout


def test_packaging_archive_double_run_no_duplicate_manifest(tmp_path: Path):
    """
    Verifica che eseguendo il confezionamento due volte nella stessa directory temporanea:
    - Non vi siano nomi duplicati nell'archivio.
    - Il manifest interno sia unico.
    - Tutti gli hash dei file corrispondano a quelli registrati nel manifest.
    - Il vecchio manifest non sia incluso né elencato nel manifest successivo.
    """
    from scripts.create_scalability_audit_archive import create_archive

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (mock_repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    src_dir = mock_repo / "ai"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("# init\n", encoding="utf-8")
    (src_dir / "models.py").write_text("class Doc: pass\n", encoding="utf-8")

    # Inserisci anche una fixture approvata
    fixtures_dir = mock_repo / "test_data" / "whatsapp_export"
    fixtures_dir.mkdir(parents=True)
    msgstore_content = b"SQLITE_SYNTHETIC_TEST_FIXTURE_1"
    (fixtures_dir / "msgstore.db").write_bytes(msgstore_content)

    mock_inv = {
        "pyproject.toml",
        "README.md",
        "ai/__init__.py",
        "ai/models.py",
        "test_data/whatsapp_export/msgstore.db",
    }
    mock_hashes = {
        "test_data/whatsapp_export/msgstore.db": hashlib.sha256(msgstore_content).hexdigest(),
    }

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    zip_dest = out_dir / "test_release.zip"
    sha_dest = out_dir / "test_release.zip.sha256"

    # --- PRIMA ESECUZIONE ---
    create_archive(
        repo_root=mock_repo,
        dest_zip=zip_dest,
        dest_zip_sha=sha_dest,
        trusted_inventory=mock_inv,
        fixture_baseline_hashes=mock_hashes,
    )
    assert zip_dest.exists()
    assert sha_dest.exists()

    # Simula che un MANIFEST_SHA256.txt sia stato salvato o estratto anche dentro mock_repo
    (mock_repo / "MANIFEST_SHA256.txt").write_text("OLD_MANIFEST_CONTENT_THAT_MUST_NOT_BE_INCLUDED\n", encoding="utf-8")
    (mock_repo / "stray.sha256").write_text("STRAY_HASH\n", encoding="utf-8")

    # --- SECONDA ESECUZIONE NELLA STESSA DIRECTORY ---
    dest_zip, zip_sha, count, packed_list = create_archive(
        repo_root=mock_repo,
        dest_zip=zip_dest,
        dest_zip_sha=sha_dest,
        trusted_inventory=mock_inv,
        fixture_baseline_hashes=mock_hashes,
    )

    # 1. Verifica archivio e assenza di nomi duplicati
    with zipfile.ZipFile(dest_zip, "r") as z:
        namelist = z.namelist()
        assert len(namelist) == len(set(namelist)), f"Nomi duplicati rilevati nell'archivio: {namelist}"

        # 2. Manifest unico
        assert namelist.count("MANIFEST_SHA256.txt") == 1

        # 3. Leggi il manifest e verifica che non elenchi se stesso né stray.sha256
        manifest_text = z.read("MANIFEST_SHA256.txt").decode("utf-8")
        assert "MANIFEST_SHA256.txt" not in manifest_text
        assert "stray.sha256" not in manifest_text
        assert "OLD_MANIFEST_CONTENT_THAT_MUST_NOT_BE_INCLUDED" not in manifest_text

        # 4. Verifica che tutti gli hash registrati nel manifest corrispondano esattamente
        lines = [line.strip() for line in manifest_text.splitlines() if line.strip() and not line.startswith("#")]
        for line in lines:
            parts = line.split("  ", 1)
            assert len(parts) == 2, f"Riga manifest malformata: {line}"
            expected_hash, arcname = parts
            file_data = z.read(arcname)
            actual_hash = hashlib.sha256(file_data).hexdigest()
            assert actual_hash == expected_hash, f"Hash non corrispondente per {arcname}: atteso {expected_hash}, calcolato {actual_hash}"


def test_packaging_archive_rejects_unapproved_db_under_test_data(tmp_path: Path):
    """
    Verifica che qualsiasi database o file zip non approvato sotto test_data/
    (inclusi .sqlite3, .db3, file laterali -wal, -shm e file con firma SQLite)
    venga rigorosamente escluso dall'archivio (guardrail anti-leak).
    """
    from scripts.create_scalability_audit_archive import create_archive

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    # Crea cartelle test_data
    td = mock_repo / "test_data"
    wa_exp = td / "whatsapp_export"
    wa_exp.mkdir(parents=True)
    smoke = td / "synthetic_smoke"
    smoke.mkdir(parents=True)

    # 1. Fixture approvate (DEVONO essere incluse)
    msgstore_bytes = b"APPROVED_MSGSTORE"
    wa_bytes = b"APPROVED_WA"
    zip_bytes = b"APPROVED_SYNTHETIC_ZIP"
    readme_bytes = b"# Synthetic Readme\n"

    (wa_exp / "msgstore.db").write_bytes(msgstore_bytes)
    (wa_exp / "wa.db").write_bytes(wa_bytes)
    (smoke / "synthetic_whatsapp_chat.zip").write_bytes(zip_bytes)
    (td / "README.md").write_bytes(readme_bytes)

    # 2. File NON approvati sotto test_data/ (casi limite segnalati nell'audit)
    (td / "local_messages.sqlite3").write_bytes(b"UNAPPROVED_SQLITE3")
    (td / "backup.db3").write_bytes(b"UNAPPROVED_DB3")
    (wa_exp / "msgstore.db-wal").write_bytes(b"UNAPPROVED_WAL")
    (wa_exp / "msgstore.db-shm").write_bytes(b"UNAPPROVED_SHM")
    (td / "temp.journal").write_bytes(b"UNAPPROVED_JOURNAL")

    sqlite_magic_header = b"SQLite format 3\x00" + b"\x00" * 48
    (td / "innocent_export.txt").write_bytes(sqlite_magic_header)
    (td / "disguised_chat.csv").write_bytes(sqlite_magic_header)
    (td / "unapproved_personal.db").write_bytes(b"SENSITIVE_UNAPPROVED_DB")
    (wa_exp / "unapproved_chat.sqlite").write_bytes(b"UNAPPROVED_SQLITE")
    (smoke / "unapproved_archive.zip").write_bytes(b"UNAPPROVED_ZIP")
    (mock_repo / "root_leak.sqlite").write_bytes(b"ROOT_SQLITE")

    mock_inv = {
        "pyproject.toml",
        "test_data/README.md",
        "test_data/whatsapp_export/msgstore.db",
        "test_data/whatsapp_export/wa.db",
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
        # Inserisci anche i percorsi non approvati nell'inventario per verificare
        # che i guardrail specifici di DB/SQLite li escludano in ogni caso:
        "test_data/local_messages.sqlite3",
        "test_data/backup.db3",
        "test_data/whatsapp_export/msgstore.db-wal",
        "test_data/whatsapp_export/msgstore.db-shm",
        "test_data/temp.journal",
        "test_data/innocent_export.txt",
        "test_data/disguised_chat.csv",
        "test_data/unapproved_personal.db",
        "test_data/whatsapp_export/unapproved_chat.sqlite",
        "test_data/synthetic_smoke/unapproved_archive.zip",
        "root_leak.sqlite",
    }
    mock_hashes = {
        "test_data/README.md": hashlib.sha256(readme_bytes).hexdigest(),
        "test_data/whatsapp_export/msgstore.db": hashlib.sha256(msgstore_bytes).hexdigest(),
        "test_data/whatsapp_export/wa.db": hashlib.sha256(wa_bytes).hexdigest(),
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip": hashlib.sha256(zip_bytes).hexdigest(),
    }

    out_zip = tmp_path / "test_guardrail.zip"
    create_archive(
        repo_root=mock_repo,
        dest_zip=out_zip,
        trusted_inventory=mock_inv,
        fixture_baseline_hashes=mock_hashes,
    )

    with zipfile.ZipFile(out_zip, "r") as z:
        names = set(z.namelist())

        # Le fixture approvate DEVONO essere presenti
        assert "test_data/whatsapp_export/msgstore.db" in names
        assert "test_data/whatsapp_export/wa.db" in names
        assert "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip" in names
        assert "test_data/README.md" in names

        # Tutti i file NON approvati DEVONO essere esclusi
        assert "test_data/local_messages.sqlite3" not in names
        assert "test_data/backup.db3" not in names
        assert "test_data/whatsapp_export/msgstore.db-wal" not in names
        assert "test_data/whatsapp_export/msgstore.db-shm" not in names
        assert "test_data/temp.journal" not in names
        assert "test_data/innocent_export.txt" not in names
        assert "test_data/disguised_chat.csv" not in names
        assert "test_data/unapproved_personal.db" not in names
        assert "test_data/whatsapp_export/unapproved_chat.sqlite" not in names
        assert "test_data/synthetic_smoke/unapproved_archive.zip" not in names
        assert "root_leak.sqlite" not in names


def test_packaging_archive_rejects_symlinks_and_external_traversal(tmp_path: Path):
    """
    Verifica che qualsiasi collegamento simbolico (a file o directory) o tentativo
    di path traversal all'esterno della radice venga scartato senza leggere dati esterni.
    """
    from scripts.create_scalability_audit_archive import create_archive, should_pack_file

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (mock_repo / "valid_code.py").write_text("print('ok')\n", encoding="utf-8")

    # File esterno alla root del repo contenente dati riservati
    external_secret = tmp_path / "external_private_data.txt"
    external_secret.write_text("TOP_SECRET_EXTERNAL_CONTENT\n", encoding="utf-8")

    external_dir = tmp_path / "external_private_dir"
    external_dir.mkdir()
    (external_dir / "secret_in_dir.txt").write_text("SECRET_INSIDE_DIR\n", encoding="utf-8")

    # Tenta di creare symlink se i permessi OS lo consentono
    symlink_file = mock_repo / "leak_symlink.txt"
    symlink_dir = mock_repo / "leak_dir_symlink"
    symlink_created = False
    try:
        os.symlink(external_secret, symlink_file)
        os.symlink(external_dir, symlink_dir, target_is_directory=True)
        symlink_created = True
    except (OSError, NotImplementedError):
        symlink_created = False

    mock_inv = {"pyproject.toml", "valid_code.py", "leak_symlink.txt", "leak_dir_symlink/secret_in_dir.txt"}
    out_zip = tmp_path / "test_symlink_guardrail.zip"
    create_archive(repo_root=mock_repo, dest_zip=out_zip, trusted_inventory=mock_inv)

    with zipfile.ZipFile(out_zip, "r") as z:
        names = set(z.namelist())
        assert "valid_code.py" in names

        if symlink_created:
            assert "leak_symlink.txt" not in names
            assert "leak_dir_symlink" not in names
            assert "leak_dir_symlink/secret_in_dir.txt" not in names

            for name in names:
                content = z.read(name).decode("utf-8", errors="ignore")
                assert "TOP_SECRET_EXTERNAL_CONTENT" not in content
                assert "SECRET_INSIDE_DIR" not in content

    # Test unitario deterministico della funzione should_pack_file con mock di symlink
    mock_symlink_path = MagicMock(spec=Path)
    mock_symlink_path.is_symlink.return_value = True
    assert should_pack_file(
        fpath=mock_symlink_path,
        rel_str="symlink_file.txt",
        file_name="symlink_file.txt",
        file_suffix=".txt",
        repo_root_resolved=mock_repo.resolve(),
        trusted_inventory={"symlink_file.txt"},
    ) is False, "should_pack_file deve restituire False per qualsiasi symlink!"

    # Test unitario deterministico per file che si risolve all'esterno della root
    mock_escaped_path = MagicMock(spec=Path)
    mock_escaped_path.is_symlink.return_value = False
    mock_escaped_path.resolve.return_value = Path("C:/Windows/System32/drivers/etc/hosts")
    assert should_pack_file(
        fpath=mock_escaped_path,
        rel_str="sub/../../hosts",
        file_name="hosts",
        file_suffix="",
        repo_root_resolved=mock_repo.resolve(),
        trusted_inventory={"sub/../../hosts"},
    ) is False, "should_pack_file deve restituire False per percorsi che si risolvono all'esterno della root!"


def test_packaging_archive_rejects_env_and_private_files_under_test_data(tmp_path: Path):
    """
    Verifica che file .env, credenziali, chiavi e file privati (.txt, .json, .csv)
    sotto test_data/ vengano rigorosamente esclusi dall'archivio ZIP,
    preservando solo le fixture sintetiche autorizzate e i template pubblici.
    """
    from scripts.create_scalability_audit_archive import create_archive

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (mock_repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    (mock_repo / "valid_code.py").write_text("print('safe')\n", encoding="utf-8")

    # 1. File sensibili e credenziali nella root o in sottocartelle
    (mock_repo / ".env").write_text("DUMMY_API_KEY=sk_test_fake_secret_12345\n", encoding="utf-8")
    (mock_repo / ".env.local").write_text("LOCAL_SECRET=top_secret_dummy\n", encoding="utf-8")
    (mock_repo / ".env.production").write_text("PROD_SECRET=dummy_prod_key\n", encoding="utf-8")
    (mock_repo / "credentials.json").write_text('{"token": "fake_token_123"}\n', encoding="utf-8")
    (mock_repo / "secrets.yaml").write_text("secret_key: fake_key\n", encoding="utf-8")
    (mock_repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n", encoding="utf-8")
    (mock_repo / "server.key").write_text("FAKE_PRIVATE_KEY\n", encoding="utf-8")
    (mock_repo / "server.pem").write_text("FAKE_CERT\n", encoding="utf-8")

    # 2. Template pubblico autorizzato (DEVE essere incluso)
    (mock_repo / ".env.example").write_text("API_KEY=your_key_here\n", encoding="utf-8")
    (mock_repo / ".env.template").write_text("API_KEY=template_value\n", encoding="utf-8")

    # 3. Cartella test_data con file approvati e file privati NON approvati
    td = mock_repo / "test_data"
    td_wa = td / "whatsapp_export"
    td_wa.mkdir(parents=True)
    td_smoke = td / "synthetic_smoke"
    td_smoke.mkdir(parents=True)
    td_cell = td / "cellebrite_export"
    td_cell.mkdir(parents=True)
    td_cloud = td / "cloud_e2e"
    td_cloud.mkdir(parents=True)

    # Fixture approvate (DEVONO essere incluse)
    readme_b = b"# Test Data Readme\n"
    msgstore_b = b"APPROVED_MSGSTORE"
    wa_b = b"APPROVED_WA"
    zip_b = b"APPROVED_SYNTHETIC_ZIP"
    txt_b = b"APPROVED_SYNTHETIC_TXT\n"
    csv_b = b"id,msg\n1,hello\n"
    json_b = b'{"messages": []}\n'
    xml_b = b"<report/>\n"
    gt_b = b'{"gt": 1}\n'
    cloud_json_b = b'{"cloud": []}\n'

    (td / "README.md").write_bytes(readme_b)
    (td_wa / "msgstore.db").write_bytes(msgstore_b)
    (td_wa / "wa.db").write_bytes(wa_b)
    (td_smoke / "synthetic_whatsapp_chat.zip").write_bytes(zip_b)
    (td_smoke / "synthetic_whatsapp_chat.txt").write_bytes(txt_b)
    (td_cell / "messages.csv").write_bytes(csv_b)
    (td_cell / "messages.json").write_bytes(json_b)
    (td_cell / "report.xml").write_bytes(xml_b)
    (td_cloud / "ground_truth.json").write_bytes(gt_b)
    (td_cloud / "messages.json").write_bytes(cloud_json_b)

    # File NON approvati sotto test_data/
    (td / "private_chat.txt").write_text("PRIVATE_CHAT_SENSITIVE_CONTENT\n", encoding="utf-8")
    (td / "private_evidence.json").write_text('{"private_evidence": "secret"}\n', encoding="utf-8")
    (td / "private_data.csv").write_text("confidential,salary\nAlice,100000\n", encoding="utf-8")
    (td / "unapproved_audio.opus").write_bytes(b"UNAPPROVED_OPUS_CONTENT")
    (td_wa / "unapproved_chat.txt").write_text("SENSITIVE_UNAPPROVED_WA_TXT\n", encoding="utf-8")

    mock_inv = {
        "valid_code.py",
        "pyproject.toml",
        "README.md",
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "secrets.yaml",
        "id_rsa",
        "server.key",
        "server.pem",
        ".env.example",
        ".env.template",
        "test_data/README.md",
        "test_data/whatsapp_export/msgstore.db",
        "test_data/whatsapp_export/wa.db",
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt",
        "test_data/cellebrite_export/messages.csv",
        "test_data/cellebrite_export/messages.json",
        "test_data/cellebrite_export/report.xml",
        "test_data/cloud_e2e/ground_truth.json",
        "test_data/cloud_e2e/messages.json",
    }
    mock_hashes = {
        "test_data/README.md": hashlib.sha256(readme_b).hexdigest(),
        "test_data/whatsapp_export/msgstore.db": hashlib.sha256(msgstore_b).hexdigest(),
        "test_data/whatsapp_export/wa.db": hashlib.sha256(wa_b).hexdigest(),
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip": hashlib.sha256(zip_b).hexdigest(),
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt": hashlib.sha256(txt_b).hexdigest(),
        "test_data/cellebrite_export/messages.csv": hashlib.sha256(csv_b).hexdigest(),
        "test_data/cellebrite_export/messages.json": hashlib.sha256(json_b).hexdigest(),
        "test_data/cellebrite_export/report.xml": hashlib.sha256(xml_b).hexdigest(),
        "test_data/cloud_e2e/ground_truth.json": hashlib.sha256(gt_b).hexdigest(),
        "test_data/cloud_e2e/messages.json": hashlib.sha256(cloud_json_b).hexdigest(),
    }

    out_zip = tmp_path / "test_privacy_files.zip"
    flagged: list[str] = []
    create_archive(
        repo_root=mock_repo,
        dest_zip=out_zip,
        flagged_excluded=flagged,
        trusted_inventory=mock_inv,
        fixture_baseline_hashes=mock_hashes,
    )

    with zipfile.ZipFile(out_zip, "r") as z:
        names = set(z.namelist())

        # File leciti inclusi
        assert "valid_code.py" in names
        assert "pyproject.toml" in names
        assert ".env.example" in names
        assert ".env.template" in names

        # Fixture autorizzate incluse
        assert "test_data/README.md" in names
        assert "test_data/whatsapp_export/msgstore.db" in names
        assert "test_data/whatsapp_export/wa.db" in names
        assert "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip" in names
        assert "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt" in names
        assert "test_data/cellebrite_export/messages.csv" in names
        assert "test_data/cellebrite_export/messages.json" in names
        assert "test_data/cellebrite_export/report.xml" in names
        assert "test_data/cloud_e2e/ground_truth.json" in names
        assert "test_data/cloud_e2e/messages.json" in names

        # File privati e sensibili TASSATIVAMENTE ESCLUSI
        assert ".env" not in names
        assert ".env.local" not in names
        assert ".env.production" not in names
        assert "credentials.json" not in names
        assert "secrets.yaml" not in names
        assert "id_rsa" not in names
        assert "server.key" not in names
        assert "server.pem" not in names

        # File privati sotto test_data/ TASSATIVAMENTE ESCLUSI
        assert "test_data/private_chat.txt" not in names
        assert "test_data/private_evidence.json" not in names
        assert "test_data/private_data.csv" not in names
        assert "test_data/unapproved_audio.opus" not in names
        assert "test_data/whatsapp_export/unapproved_chat.txt" not in names

        for name in names:
            content = z.read(name).decode("utf-8", errors="ignore")
            assert "sk_test_fake_secret_12345" not in content
            assert "PRIVATE_CHAT_SENSITIVE_CONTENT" not in content
            assert "fake_token_123" not in content
            assert "Alice,100000" not in content

    assert any(".env" in p for p in flagged)
    assert any("private_chat.txt" in p for p in flagged)
    assert any("private_evidence.json" in p for p in flagged)
    assert any("credentials.json" in p for p in flagged)


def test_packaging_archive_rejects_unknown_ordinary_files_outside_test_data(tmp_path: Path):
    """
    DIFETTO 1: Riproduce il caso in cui file privati con nomi ordinari (.txt, .json, .csv, .py)
    fuori da test_data/ vengono aggiunti nel progetto.
    Verifica che il guardrail dell'inventario esplicito (TRUSTED_SOURCE_INVENTORY)
    rifiuti tassativamente tali file e li segnali per approvazione manuale senza leggerne il contenuto.
    """
    from scripts.create_scalability_audit_archive import create_archive

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    src_dir = mock_repo / "ai"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("# init\n", encoding="utf-8")
    core_dir = mock_repo / "core"
    core_dir.mkdir()
    (core_dir / "config.py").write_text("# config\n", encoding="utf-8")

    # File sconosciuti / non approvati con nomi ordinari fuori da test_data/
    (mock_repo / "notes.txt").write_text("SENSITIVE_ORDINARY_NOTE_CONTENT\n", encoding="utf-8")
    (src_dir / "private_chat.csv").write_text("sender,text\nAlice,Secret\n", encoding="utf-8")
    (core_dir / "personal_info.json").write_text('{"name": "Private User"}\n', encoding="utf-8")
    scripts_dir = mock_repo / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "unauthorized_tool.py").write_text("print('unauthorized')\n", encoding="utf-8")
    tests_dir = mock_repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "secret_leak.txt").write_text("LEAK_IN_TESTS\n", encoding="utf-8")

    # Inventario approvato (contiene solo i file leciti)
    trusted_inventory = {
        "pyproject.toml",
        "ai/__init__.py",
        "core/config.py",
    }

    out_zip = tmp_path / "test_inventory_guardrail.zip"
    flagged: list[str] = []
    create_archive(
        repo_root=mock_repo,
        dest_zip=out_zip,
        flagged_excluded=flagged,
        trusted_inventory=trusted_inventory,
    )

    with zipfile.ZipFile(out_zip, "r") as z:
        names = set(z.namelist())

        # I file approvati DEVONO essere presenti
        assert "pyproject.toml" in names
        assert "ai/__init__.py" in names
        assert "core/config.py" in names

        # I file sconosciuti con nomi ordinari DEVONO essere tassativamente esclusi
        assert "notes.txt" not in names
        assert "ai/private_chat.csv" not in names
        assert "core/personal_info.json" not in names
        assert "scripts/unauthorized_tool.py" not in names
        assert "tests/secret_leak.txt" not in names

        # Nessun dato sensibile deve essere presente
        for name in names:
            content = z.read(name).decode("utf-8", errors="ignore")
            assert "SENSITIVE_ORDINARY_NOTE_CONTENT" not in content
            assert "Private User" not in content
            assert "LEAK_IN_TESTS" not in content

    # Verifica che tutti i file sconosciuti siano stati segnalati nella lista flagged
    assert "notes.txt" in flagged
    assert "ai/private_chat.csv" in flagged
    assert "core/personal_info.json" in flagged
    assert "scripts/unauthorized_tool.py" in flagged
    assert "tests/secret_leak.txt" in flagged


def test_packaging_archive_rejects_tampered_or_substituted_synthetic_fixtures(tmp_path: Path):
    """
    DIFETTO 2: Riproduce il caso in cui il contenuto di una fixture autorizzata sotto test_data/
    venga sostituito o manomesso (ad es. con chat private o dati sensibili) mantenendo
    lo stesso nome e percorso approvato.
    Verifica che il controllo crittografico SHA-256 contro la baseline attendibile
    rilevi la manomissione, escluda la fixture dallo ZIP e la segnali per indagine.
    """
    from scripts.create_scalability_audit_archive import create_archive

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()
    (mock_repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    td = mock_repo / "test_data"
    wa_exp = td / "whatsapp_export"
    wa_exp.mkdir(parents=True)
    smoke = td / "synthetic_smoke"
    smoke.mkdir(parents=True)
    cell = td / "cellebrite_export"
    cell.mkdir(parents=True)
    cloud = td / "cloud_e2e"
    cloud.mkdir(parents=True)

    # Fixture 1 e 2: AUTENTICHE (corrispondono alla baseline attendibile)
    auth_msgstore_content = b"GENUINE_AUTHENTIC_SQLITE_MSGSTORE_CONTENT"
    auth_smoke_content = b"GENUINE_SYNTHETIC_WHATSAPP_SMOKE_CHAT"
    (wa_exp / "msgstore.db").write_bytes(auth_msgstore_content)
    (smoke / "synthetic_whatsapp_chat.txt").write_bytes(auth_smoke_content)

    # Fixture 3 e 4: SOSTITUITE / MANOMESSE con dati privati riservati fittizi
    tampered_csv_content = b"caller,receiver,private_message\nBoss,Alice,TopSecretCompanyDeal2026\n"
    tampered_gt_content = b'{"confidential_leak": "secret_classified_findings"}\n'
    (cell / "messages.csv").write_bytes(tampered_csv_content)
    (cloud / "ground_truth.json").write_bytes(tampered_gt_content)

    # Baseline attendibile: registra gli hash originali legittimi (NON quelli manomessi)
    genuine_csv_baseline = b"GENUINE_OFFICIAL_CELLEBRITE_MESSAGES_CSV\n"
    genuine_gt_baseline = b'{"official_ground_truth": true}\n'

    trusted_inventory = {
        "pyproject.toml",
        "test_data/whatsapp_export/msgstore.db",
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt",
        "test_data/cellebrite_export/messages.csv",
        "test_data/cloud_e2e/ground_truth.json",
    }

    baseline_hashes = {
        "test_data/whatsapp_export/msgstore.db": hashlib.sha256(auth_msgstore_content).hexdigest(),
        "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt": hashlib.sha256(auth_smoke_content).hexdigest(),
        "test_data/cellebrite_export/messages.csv": hashlib.sha256(genuine_csv_baseline).hexdigest(),
        "test_data/cloud_e2e/ground_truth.json": hashlib.sha256(genuine_gt_baseline).hexdigest(),
    }

    out_zip = tmp_path / "test_tampered_fixtures_guardrail.zip"
    flagged: list[str] = []
    create_archive(
        repo_root=mock_repo,
        dest_zip=out_zip,
        flagged_excluded=flagged,
        trusted_inventory=trusted_inventory,
        fixture_baseline_hashes=baseline_hashes,
    )

    with zipfile.ZipFile(out_zip, "r") as z:
        names = set(z.namelist())

        # Le fixture autentiche DEVONO essere presenti
        assert "test_data/whatsapp_export/msgstore.db" in names
        assert "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt" in names
        assert "pyproject.toml" in names

        # Le fixture sostituite/alterate DEVONO essere respinte dallo ZIP
        assert "test_data/cellebrite_export/messages.csv" not in names
        assert "test_data/cloud_e2e/ground_truth.json" not in names

        # Nessun dato riservato della fixture manomessa deve comparire nello ZIP
        for name in names:
            content = z.read(name).decode("utf-8", errors="ignore")
            assert "TopSecretCompanyDeal2026" not in content
            assert "secret_classified_findings" not in content

    # Verifica che le fixture alterate siano state registrate in flagged_excluded
    assert "test_data/cellebrite_export/messages.csv" in flagged
    assert "test_data/cloud_e2e/ground_truth.json" in flagged
