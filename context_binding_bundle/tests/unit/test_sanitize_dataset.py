"""
tests/unit/test_sanitize_dataset.py
-----------------------------------
Test di sicurezza per lo script scripts/sanitize_synthetic_dataset.py.

Verifica che le protezioni di sicurezza (guardia _verify_safety) impediscano
categoricamente qualsiasi alterazione al di fuori della directory test_data/.
I test operano esclusivamente su fixture temporanee isolate (tmp_path)
senza mai toccare o alterare il dataset sintetico reale.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.sanitize_synthetic_dataset import (
    _verify_safety,
    sanitize_csv,
    sanitize_json,
    sanitize_msgstore_db,
    sanitize_wa_db,
    sanitize_xml,
)


@pytest.mark.unit
class TestSanitizerSafetyGuard:
    """Verifica la logica di guardia _verify_safety."""

    def test_path_inside_test_data_allowed(self, tmp_path: Path):
        """Un percorso all'interno di una cartella test_data è consentito."""
        safe_dir = tmp_path / "project" / "test_data" / "cellebrite_export"
        safe_dir.mkdir(parents=True)
        safe_file = safe_dir / "messages.csv"
        safe_file.touch()

        # Non deve sollevare alcuna eccezione
        _verify_safety(safe_file)

    def test_path_outside_test_data_rejected(self, tmp_path: Path):
        """Un percorso al di fuori di test_data deve essere tassativamente rifiutato."""
        unsafe_dir = tmp_path / "documents" / "production_data"
        unsafe_dir.mkdir(parents=True)
        unsafe_file = unsafe_dir / "real_evidence.csv"
        unsafe_file.touch()

        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            _verify_safety(unsafe_file)

    def test_parent_traversal_resolving_outside_rejected(self, tmp_path: Path):
        """Percorsi con '..' che simulano l'uscita da test_data vengono rifiutati."""
        base_dir = tmp_path / "test_data"
        base_dir.mkdir()
        outside_dir = tmp_path / "system_sensitive"
        outside_dir.mkdir()
        outside_file = outside_dir / "passwords.txt"
        outside_file.touch()

        # Path che include test_data ma risolve fuori
        traversal_path = base_dir / ".." / "system_sensitive" / "passwords.txt"

        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            _verify_safety(traversal_path)

    def test_verification_uses_resolved_parts_not_substring(self, tmp_path: Path):
        """Directory con nomi simili (es. test_data_fake o my_test_data) sono rifiutate."""
        fake_dir = tmp_path / "test_data_fake"
        fake_dir.mkdir()
        fake_file = fake_dir / "file.csv"
        fake_file.touch()

        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            _verify_safety(fake_file)

        fake_dir2 = tmp_path / "my_test_data"
        fake_dir2.mkdir()
        fake_file2 = fake_dir2 / "file.csv"
        fake_file2.touch()

        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            _verify_safety(fake_file2)

    def test_symlink_pointing_outside_rejected(self, tmp_path: Path):
        """Un symlink situato in test_data che punta a un file esterno deve essere rifiutato."""
        safe_dir = tmp_path / "test_data"
        safe_dir.mkdir()
        outside_dir = tmp_path / "external_data"
        outside_dir.mkdir()
        outside_file = outside_dir / "confidential.csv"
        outside_file.touch()

        symlink_path = safe_dir / "link_to_external.csv"
        try:
            symlink_path.symlink_to(outside_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Creazione symlink non supportata o permessi insufficienti sul sistema: {exc}")

        # Poiché resolve() segue il symlink verso outside_file, la guardia deve bloccarlo
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            _verify_safety(symlink_path)


@pytest.mark.unit
class TestSanitizerFunctionsEnforceSafety:
    """Verifica che tutte le funzioni pubbliche di sanitizzazione invochino la guardia."""

    def test_sanitize_csv_rejects_outside_path(self, tmp_path: Path):
        outside_file = tmp_path / "outside.csv"
        outside_file.touch()
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            sanitize_csv(outside_file)

    def test_sanitize_json_rejects_outside_path(self, tmp_path: Path):
        outside_file = tmp_path / "outside.json"
        outside_file.touch()
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            sanitize_json(outside_file)

    def test_sanitize_wa_db_rejects_outside_path(self, tmp_path: Path):
        outside_file = tmp_path / "outside.db"
        outside_file.touch()
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            sanitize_wa_db(outside_file)

    def test_sanitize_msgstore_db_rejects_outside_path(self, tmp_path: Path):
        outside_file = tmp_path / "outside.db"
        outside_file.touch()
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            sanitize_msgstore_db(outside_file)

    def test_sanitize_xml_rejects_outside_path(self, tmp_path: Path):
        outside_file = tmp_path / "outside.xml"
        outside_file.touch()
        with pytest.raises(ValueError, match="SICUREZZA VIOLATA"):
            sanitize_xml(outside_file)
