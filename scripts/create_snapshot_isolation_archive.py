"""
scripts/create_snapshot_isolation_archive.py
--------------------------------------------
Packaging script for the Snapshot Isolation & Preflight Closure Audit bundle.
Generates the exact ZIP archive directly OUTSIDE the 'Tesi' repository:
c:\\Users\\lucas\\Desktop\\CartellaTesi\\chatAnalysis_checkpoint_resume_snapshot_isolation_final_audit_20261020.zip

CARDINAL CONSTRAINTS:
1. NEVER create or save intermediate ZIP files inside 'Tesi' or Git repo.
2. Strict exclusion of real SQLite databases, live checkpoints, WhatsApp exports, or PII.
3. Inclusion of updated scripts, real application sources, tests, requirements, runbook, technical report, and SHA-256 manifest.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
PARENT_DIR = REPO_ROOT.parent

ARCHIVE_NAME = "chatAnalysis_checkpoint_resume_snapshot_isolation_final_audit_20261020.zip"
TARGET_ZIP_PATH = PARENT_DIR / ARCHIVE_NAME

FORBIDDEN_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".log_real", ".raw"}

FILES_TO_PACK = [
    # Technical Reports and Ubuntu Runbook
    "CHATANALYSIS_SNAPSHOT_ISOLATION_FINAL_REPORT.md",
    "CHATANALYSIS_CHECKPOINT_OUTPUT_SAFETY_REPORT.md",
    "CHATANALYSIS_CHECKPOINT_RESUME_PREFLIGHT_REPORT.md",
    "CHATANALYSIS_CHECKPOINT_SECURITY_TRIPWIRE_REPORT.md",
    "UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md",
    "test_results.log",
    "requirements_audit.txt",
    "pyproject.toml",

    # Preflight and Diagnostics Scripts
    "scripts/preflight_checkpoint_resume.py",
    "scripts/diagnose_checkpoints_safe.py",
    "scripts/diagnose_ubuntu_live_24of26.py",

    # Unit & Regression Test Suites
    "tests/unit/__init__.py",
    "tests/unit/test_checkpoint_preflight_resume.py",
    "tests/unit/test_checkpoint_safe_diagnostics.py",
    "tests/unit/test_checkpoint_integrity_scalability.py",

    # Real Application Sources
    "ai/__init__.py",
    "ai/backend.py",
    "ai/benchmark.py",
    "ai/chunking.py",
    "ai/cloud_experiment.py",
    "ai/cloud_test.py",
    "ai/discovery.py",
    "ai/experiment.py",
    "ai/hardware.py",
    "ai/lmstudio.py",
    "ai/models.py",
    "ai/serializer.py",
    "ai/structured.py",
    "ai/token_budget.py",
    "ai/topics.py",
    "ai/translation.py",

    "analysis/__init__.py",
    "analysis/checkpoint.py",
    "analysis/consistency.py",
    "analysis/models.py",
    "analysis/progress.py",
    "analysis/service.py",

    "core/__init__.py",
    "core/config.py",
    "core/immutability.py",

    "entity_resolution/__init__.py",
    "entity_resolution/base.py",
    "entity_resolution/models.py",
    "entity_resolution/resolver.py",

    "importer/__init__.py",
    "importer/base.py",
    "importer/cellebrite_csv.py",
    "importer/cellebrite_json.py",
    "importer/cellebrite_xml.py",
    "importer/models.py",
    "importer/whatsapp_export.py",
    "importer/whatsapp_msgstore.py",
    "importer/whatsapp_wa.py",

    "multimodal/__init__.py",
    "multimodal/asset_access.py",
    "multimodal/evidence.py",
    "multimodal/models.py",
    "multimodal/ocr.py",
    "multimodal/pipeline.py",
    "multimodal/resolver.py",
    "multimodal/transcriber.py",
    "multimodal/vision.py",

    "normalization/__init__.py",
    "normalization/base.py",
    "normalization/models.py",
    "normalization/normalizer.py",
    "normalization/phone.py",

    "search/__init__.py",
    "search/engine.py",
    "search/index.py",
    "search/models.py",
    "search/normalization.py",
    "search/service.py",
    "search/topics.py",
    "search/views.py",

    "ui/__init__.py",
    "ui/application.py",
    "ui/demo.py",
    "ui/ingestion.py",
    "ui/models.py",
    "ui/presentation.py",
    "ui/staging.py",
    "ui/state.py",

    "unified/__init__.py",
    "unified/builder.py",
    "unified/context.py",
    "unified/models.py",

    "validation/__init__.py",
    "validation/base.py",
    "validation/models.py",
    "validation/validator.py",
]

# Offline wheels
WHEELS_DIR = REPO_ROOT / "wheels"


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_archive() -> None:
    print("=" * 78)
    print(" CHATANALYSIS — PACKAGING AUDIT ARCHIVE (SNAPSHOT ISOLATION FINAL GATE)")
    print("=" * 78)
    print(f"Repo Root (Tesi)   : {REPO_ROOT}")
    print(f"Target Directory   : {PARENT_DIR}")
    print(f"Archive Target Path: {TARGET_ZIP_PATH}")
    print("-" * 78)

    # 1. Enforce target is outside REPO_ROOT
    try:
        TARGET_ZIP_PATH.relative_to(REPO_ROOT)
        raise RuntimeError(f"CRITICAL VIOLATION: Target path {TARGET_ZIP_PATH} is inside {REPO_ROOT}!")
    except ValueError:
        pass

    if TARGET_ZIP_PATH.exists():
        print(f"[*] Removing existing archive: {TARGET_ZIP_PATH}")
        TARGET_ZIP_PATH.unlink()

    # 2. Gather all files
    all_files = list(FILES_TO_PACK)

    # Add wheels
    if WHEELS_DIR.exists():
        for whl in sorted(WHEELS_DIR.glob("*.whl")):
            all_files.append(f"wheels/{whl.name}")

    print(f"[*] Total files to package: {len(all_files)}")

    manifest_entries: list[tuple[str, str]] = []

    # 3. Create ZIP directly outside repository
    with zipfile.ZipFile(TARGET_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for rel_path in sorted(all_files):
            src_file = REPO_ROOT / rel_path
            if not src_file.is_file():
                raise FileNotFoundError(f"Source file not found: {src_file}")

            # Check privacy anti-leak
            lower_name = src_file.name.lower()
            if any(lower_name.endswith(ext) for ext in FORBIDDEN_EXTENSIONS):
                raise ValueError(f"PRIVACY VIOLATION: Attempting to pack forbidden extension: {src_file}")

            # Check SQLite magic signature
            with open(src_file, "rb") as f:
                header = f.read(16)
                if header.startswith(b"SQLite format 3\x00"):
                    raise ValueError(f"SECURITY VIOLATION: SQLite database file detected: {src_file}")

            sha = compute_sha256(src_file)
            manifest_entries.append((sha, rel_path.replace("\\", "/")))

            # Write file to zip
            zipf.write(src_file, arcname=rel_path.replace("\\", "/"))

        # Sort manifest entries alphabetically by path
        manifest_entries.sort(key=lambda x: x[1])

        # Write audit_manifest_sha256.txt inside the ZIP
        manifest_content = "\n".join(f"{sha}  {path}" for sha, path in manifest_entries) + "\n"
        zipf.writestr("audit_manifest_sha256.txt", manifest_content.encode("utf-8"))

    archive_size = TARGET_ZIP_PATH.stat().st_size
    archive_sha = compute_sha256(TARGET_ZIP_PATH)

    print("-" * 78)
    print("[SUCCESS] ARCHIVE CREATED DIRECTLY OUTSIDE REPOSITORY!")
    print(f"Destination : {TARGET_ZIP_PATH}")
    print(f"Total Files : {len(manifest_entries)} files + audit_manifest_sha256.txt = {len(manifest_entries) + 1}")
    print(f"Size        : {archive_size:,} bytes ({archive_size / 1024:.2f} KB)")
    print(f"SHA-256     : {archive_sha}")
    print("=" * 78)

    # 4. Independent Verification: Read back and verify all hashes from ZIP
    print("\n[*] Running self-verification of the created archive...")
    with zipfile.ZipFile(TARGET_ZIP_PATH, "r") as zipf:
        read_manifest = zipf.read("audit_manifest_sha256.txt").decode("utf-8")
        manifest_lines = [l.strip() for l in read_manifest.strip().splitlines() if l.strip()]

        verified = 0
        for line in manifest_lines:
            expected_sha, expected_path = line.split("  ", 1)
            file_data = zipf.read(expected_path)
            actual_sha = hashlib.sha256(file_data).hexdigest()
            if actual_sha != expected_sha:
                raise ValueError(f"INTEGRITY ERROR: SHA mismatch for {expected_path}: {actual_sha} != {expected_sha}")
            verified += 1

        print(f"[+] Verified {verified}/{len(manifest_lines)} SHA-256 checksums from audit_manifest_sha256.txt: ALL MATCH!")


if __name__ == "__main__":
    build_archive()
