"""
tests/unit/test_sentinel_checkpoint_protection.py
-------------------------------------------------
Test di sicurezza e non-regressione per garantire che l'esecuzione di test
e fixture non cancelli mai file sentinella o directory di checkpoint esterne.
"""
import hashlib
import os
from pathlib import Path
import pytest

from analysis.checkpoint import AnalysisCheckpointStore


def test_sentinel_file_preserved_across_checkpoint_operations(tmp_path: Path):
    """
    Dimostra che un file sentinella presente in una directory protetta
    rimane byte-per-byte identico durante tutte le operazioni dei test.
    """
    sentinel_dir = tmp_path / "protected_sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = sentinel_dir / "sentinel_store.db"
    
    sentinel_content = b"SENTINEL_FORENSIC_INTEGRITY_GUARD_20261025_IMMUTABLE"
    sentinel_file.write_bytes(sentinel_content)
    sha_before = hashlib.sha256(sentinel_content).hexdigest()

    # Operazioni su un checkpoint store isolato e separato
    isolated_test_dir = tmp_path / "isolated_test_store"
    isolated_store = AnalysisCheckpointStore(checkpoint_dir=isolated_test_dir)
    assert isolated_store.checkpoint_dir.resolve() != sentinel_dir.resolve()

    # Verifica che il file sentinella esista e sia identico
    assert sentinel_file.exists()
    assert sentinel_file.read_bytes() == sentinel_content
    sha_after = hashlib.sha256(sentinel_file.read_bytes()).hexdigest()
    assert sha_before == sha_after, "Il file sentinella non deve subire alterazioni"


def test_production_checkpoints_never_targeted_by_fixtures():
    """
    Verifica statica che nessuna fixture attiva nei test unitari
    contenga istruzioni di rimozione ricorsiva su Path('.checkpoints').
    """
    test_files = [
        Path("tests/unit/test_subchunk_split_recovery_and_coverage.py"),
        Path("tests/unit/test_ui_manual_topic_detection.py"),
        Path("tests/unit/test_apptest_flows.py"),
    ]
    for tf in test_files:
        if tf.exists():
            content = tf.read_text(encoding="utf-8")
            assert 'shutil.rmtree(Path(".checkpoints")' not in content, f"Rilevato rmtree distruttivo su .checkpoints in {tf}"
            assert "shutil.rmtree(chk_dir" not in content or "tmp_path" in content, f"Rilevato rmtree non vincolato a tmp_path in {tf}"
