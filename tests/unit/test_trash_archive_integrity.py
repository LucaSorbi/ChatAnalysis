"""
tests/unit/test_trash_archive_integrity.py
------------------------------------------
Integrità dell'archivio storico `Trash/` (spostamenti con `git mv`, senza alterare i contenuti) e dello script
`scripts/archive_private_local.py`. Dati SINTETICI; nessun database o file reale viene letto o scritto.
"""
from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import archive_private_local as apl

ROOT = Path(__file__).resolve().parent.parent.parent
TRASH = ROOT / "Trash"
MANIFEST = TRASH / "MANIFEST.csv"

pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="Trash/MANIFEST.csv assente (checkout parziale)")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _rows() -> list[dict[str, str]]:
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_manifest_statuses_are_known_and_moved_files_are_verified():
    rows = _rows()
    assert {r["status"] for r in rows} <= {
        "MOVED", "KEPT", "PRIVATE_NOT_MOVED", "UNKNOWN_NOT_MOVED", "LOCAL_ONLY_UNTOUCHED"}
    moved = [r for r in rows if r["status"] == "MOVED"]
    assert len(moved) == 57
    for r in moved:
        new, old = ROOT / r["new_path"], ROOT / r["old_path"]
        assert r["hash_verified"] == "YES", r["old_path"]
        assert new.is_file(), f"manca {r['new_path']}"
        assert not old.exists(), f"il vecchio percorso esiste ancora: {r['old_path']}"
        assert _sha(new) == r["sha256_before"] == r["sha256_after"], f"contenuto alterato: {r['new_path']}"


def test_no_runtime_test_or_packaging_file_references_moved_paths():
    names = {Path(r["old_path"]).name for r in _rows() if r["status"] == "MOVED"}
    scan: list[Path] = [ROOT / n for n in ("pyproject.toml", "MANIFEST.in", "app.py", "run_benchmark.sh", "compute_manifest.py")]
    for d in ("ai", "analysis", "ui", "core", "importer", "validation", "multimodal", "normalization", "unified",
              "entity_resolution", "search", "profiler", "tests", "scripts"):
        for p in (ROOT / d).rglob("*"):
            if p.suffix in {".py", ".toml", ".cfg", ".sh", ".in"} and "__pycache__" not in p.parts:
                # i builder storici di release citano i vecchi percorsi per costruzione: esclusi (documentato in Trash/README.md)
                if p.parent.name == "scripts" and p.name.startswith("create_"):
                    continue
                if p.resolve() == Path(__file__).resolve():
                    continue
                scan.append(p)
    offenders = []
    for p in scan:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        offenders += [(str(p.relative_to(ROOT)), n) for n in names if n in text]
    assert offenders == []


def test_essential_and_frozen_files_were_not_moved_and_are_unchanged():
    rows = _rows()
    moved_old = {r["old_path"] for r in rows if r["status"] == "MOVED"}
    for keep in ("RUNBOOK_UBUNTU.txt", "benchmark_frozen_sha256.txt", "run_benchmark.sh", "pyproject.toml", "MANIFEST.in"):
        assert keep not in moved_old and (ROOT / keep).is_file()
    for r in rows:
        if r["status"] == "KEPT" and r["classification"] == "BENCHMARK_FROZEN":
            assert _sha(ROOT / r["old_path"]) == r["sha256_before"], f"benchmark congelato modificato: {r['old_path']}"
    for d in ("ai", "analysis", "ui", "core", "importer", "validation"):
        assert (ROOT / d).is_dir()


def test_gitignore_only_ignores_private_local_not_trash_globally():
    lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    trash_rules = [l for l in lines if "Trash" in l and not l.startswith("#")]
    assert trash_rules == ["Trash/Privato_Locale/*", "!Trash/Privato_Locale/README.md"]
    assert not any(l.rstrip("/") in {"Trash", "/Trash", "Trash/*", "Trash/**"} for l in lines)


def test_trash_contains_no_databases_archives_or_wal_files_outside_private_area():
    banned = {".db", ".sqlite", ".sqlite3", ".zip", ".db-wal", ".db-shm", ".wal", ".shm"}
    found = []
    for p in TRASH.rglob("*"):
        if p.is_file() and "Privato_Locale" not in p.relative_to(TRASH).parts:
            if p.suffix.lower() in banned or p.name.endswith(("-wal", "-shm")):
                found.append(str(p.relative_to(ROOT)))
    assert found == []


def test_suspicious_versioned_files_are_listed_as_exceptions_not_moved():
    rows = {r["old_path"]: r for r in _rows()}
    for p in ("scratch/debug_actual_output.py", "scratch_call_385_request_body.json", "scratch/fresh_live_test_store/checkpoints.db"):
        assert rows[p]["status"] == "PRIVATE_NOT_MOVED" and (ROOT / p).is_file()
    assert rows["chat-20261021T182727Z-1-001.zip"]["status"] == "UNKNOWN_NOT_MOVED"
    forensic = [r for r in rows.values() if r["old_path"].startswith("forensic_provenance_audit_backup_20261027/")]
    assert forensic and all(r["status"] == "PRIVATE_NOT_MOVED" and (ROOT / r["old_path"]).is_file() for r in forensic)


# ----------------------------------------------------------------------------
# scripts/archive_private_local.py (repo sintetico in tmp_path)
# ----------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATANALYSIS_CHECKPOINT_DIR", raising=False)
    (tmp_path / "Trash" / "Privato_Locale").mkdir(parents=True)
    out = tmp_path / "output" / "run1"
    out.mkdir(parents=True)
    (out / "risultato.json").write_text('{"x": 1}', encoding="utf-8")
    (out / "note.txt").write_text("dati sintetici", encoding="utf-8")
    return tmp_path


def _make_wal_db(path: Path) -> None:
    c = sqlite3.connect(str(path))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(a INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    c.commit()
    c.close()


def test_archive_dry_run_is_default_and_creates_nothing(repo):
    lines: list[str] = []
    rc = apl.run([repo / "output"], "lab", repo, execute=False, out=lines.append)
    assert rc == 0 and any("DRY-RUN" in l for l in lines)
    assert list((repo / "Trash" / "Privato_Locale").iterdir()) == []


def test_archive_execute_copies_with_verified_hashes_and_keeps_originals(repo):
    before = {p: _sha(p) for p in (repo / "output").rglob("*") if p.is_file()}
    assert apl.run([repo / "output"], "lab", repo, execute=True, out=lambda s: None) == 0
    dest = repo / "Trash" / "Privato_Locale" / "lab" / "output" / "run1"
    assert _sha(dest / "risultato.json") == before[repo / "output" / "run1" / "risultato.json"]
    manifest = list(csv.DictReader(open(repo / "Trash" / "Privato_Locale" / "lab" / "ARCHIVE_MANIFEST.csv", encoding="utf-8")))
    assert len(manifest) == 2 and all(r["verified"] == "YES" for r in manifest)
    assert before == {p: _sha(p) for p in (repo / "output").rglob("*") if p.is_file()}, "gli originali non devono cambiare"
    # seconda esecuzione: nessuna sovrascrittura
    lines: list[str] = []
    apl.run([repo / "output"], "lab", repo, execute=True, out=lines.append)
    assert sum("SKIP_EXISTS" in l for l in lines) == 2


def test_archive_sqlite_uses_online_backup_and_is_consistent(repo):
    db = repo / "output" / "run1" / "store.db"
    _make_wal_db(db)
    assert apl.run([repo / "output"], "lab", repo, execute=True, out=lambda s: None) == 0
    copy = repo / "Trash" / "Privato_Locale" / "lab" / "output" / "run1" / "store.db"
    c = sqlite3.connect(str(copy))
    assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    assert not any(p.name.endswith(("-wal", "-shm")) for p in (repo / "Trash" / "Privato_Locale").rglob("*"))


def test_archive_excludes_active_checkpoints_by_default(repo):
    chk = repo / ".checkpoints"
    chk.mkdir()
    _make_wal_db(chk / "checkpoints.db")
    lines: list[str] = []
    apl.run([chk], "lab", repo, execute=True, out=lines.append)
    assert any("SKIP_ACTIVE_CHECKPOINT" in l for l in lines)
    assert not (repo / "Trash" / "Privato_Locale" / "lab").exists()


def test_archive_rejects_destination_outside_private_area_and_bad_labels(repo):
    with pytest.raises(ValueError):
        apl.run([repo / "output"], "lab", repo, dest=repo / "altrove", execute=True, out=lambda s: None)
    with pytest.raises(ValueError):
        apl.run([repo / "output"], "../fuga", repo, execute=True, out=lambda s: None)
    assert apl.main(["--source", str(repo / "output"), "--label", "lab", "--dest", str(repo / "altrove"),
                     "--repo-root", str(repo)]) == 2
    assert not (repo / "altrove").exists()


def test_historical_archive_builders_are_all_documented_as_non_executable():
    doc = (ROOT / "scripts" / "HISTORICAL_ARCHIVE_BUILDERS.md").read_text(encoding="utf-8")
    assert "STORICI" in doc and "non eseguire" in doc
    missing = [p.name for p in (ROOT / "scripts").glob("create_*.py") if p.name not in doc]
    assert missing == [], f"builder non documentati: {missing}"


def test_inventory_limits_are_documented_and_after_snapshot_does_not_list_itself():
    readme = (TRASH / "inventory" / "README.md").read_text(encoding="utf-8")
    assert "Non include sé stesso" in readme.replace("non include", "Non include") or "non include sé stesso" in readme
    with open(TRASH / "inventory" / "INVENTORY_AFTER.csv", newline="", encoding="utf-8") as fh:
        paths = {r["path"] for r in csv.DictReader(fh)}
    assert "Trash/inventory/INVENTORY_AFTER.csv" not in paths
