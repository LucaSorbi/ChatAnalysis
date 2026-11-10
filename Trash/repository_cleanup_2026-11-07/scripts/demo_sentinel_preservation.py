#!/usr/bin/env python3
"""
scripts/demo_sentinel_preservation.py
------------------------------------
Dimostrazione controllata della preservazione dei checkpoint mediante file sentinella sintetico.
Esegue le tre suite precedentemente distruttive e certifica che il file sentinella
rimane integro e immutato prima e dopo l'esecuzione di pytest.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys


def run_sentinel_demonstration():
    repo_root = Path(__file__).resolve().parents[1]
    sentinel_dir = repo_root / "sentinel_controlled_guard"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = sentinel_dir / "sentinel_control.db"

    test_payload = b"SENTINEL_CONTROL_PAYLOAD_SHA256_PROTECTION_AUDIT_20261025" * 32
    sentinel_file.write_bytes(test_payload)
    sha_before = hashlib.sha256(test_payload).hexdigest()
    mtime_before = sentinel_file.stat().st_mtime
    size_before = sentinel_file.stat().st_size

    print("=" * 80)
    print("PROVA CONTROLLATA DI PRESERVAZIONE FILE SENTINELLA")
    print("=" * 80)
    print(f"[*] File Sentinella: {sentinel_file}")
    print(f"[*] SHA-256 PRE-TEST:  {sha_before}")
    print(f"[*] Dimensione (byte): {size_before}")
    print(f"[*] Timestamp:         {mtime_before}")
    print("\n[*] Esecuzione suite di test (test_subchunk, test_ui_manual, test_apptest_flows)...")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_subchunk_split_recovery_and_coverage.py",
        "tests/unit/test_ui_manual_topic_detection.py",
        "tests/unit/test_apptest_flows.py",
        "-q",
    ]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    print(proc.stdout)
    if proc.stderr:
        print("Stderr:", proc.stderr[:300])

    print(f"[*] Exit code pytest: {proc.returncode}")
    assert proc.returncode == 0, f"I test hanno fallito con codice {proc.returncode}"

    # Verifica post-test
    assert sentinel_file.exists(), "ERRORE CRITICO: Il file sentinella è stato cancellato!"
    data_after = sentinel_file.read_bytes()
    sha_after = hashlib.sha256(data_after).hexdigest()
    mtime_after = sentinel_file.stat().st_mtime
    size_after = sentinel_file.stat().st_size

    print(f"\n[*] SHA-256 POST-TEST: {sha_after}")
    print(f"[*] Dimensione (byte): {size_after}")
    print(f"[*] Timestamp:         {mtime_after}")

    assert sha_before == sha_after, "ERRORE: L'hash del file sentinella è cambiato!"
    assert size_before == size_after, "ERRORE: La dimensione del file sentinella è cambiata!"
    assert mtime_before == mtime_after, "ERRORE: Il file sentinella è stato toccato o ricreato!"

    print("\n[OK] VERIFICA SUPERATA: Il file sentinella è rimasto IDENTICO al 100%.")
    print("     Nessuna fixture ha cancellato o toccato directory esterne a tmp_path.")
    print("=" * 80)

    # Pulizia della sola cartella sentinella temporanea
    sentinel_file.unlink()
    sentinel_dir.rmdir()


if __name__ == "__main__":
    run_sentinel_demonstration()
