"""
verify_bundle_audit.py
Verifies the audit archive:
- Extraction
- Manifest SHA-256 match
- Compileall
- Pytest execution
"""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ZIP_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_checkpoint_inode_reuse_ownership_audit_20261020.zip")

print(f"[*] Checking archive: {ZIP_PATH}")
assert ZIP_PATH.is_file(), f"Archive not found: {ZIP_PATH}"

with tempfile.TemporaryDirectory() as td:
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        zf.extractall(td)
    print(f"[*] Extracted into temporary directory: {td}")

    # 1. Verify manifest
    manifest_file = Path(td) / "manifest_sha256.txt"
    assert manifest_file.is_file(), "manifest_sha256.txt missing from archive!"
    lines = [l.strip() for l in manifest_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"[*] Checking {len(lines)} files against manifest_sha256.txt...")

    for line in lines:
        expected_sha, rel_path = line.split("  ", 1)
        target = Path(td) / rel_path
        assert target.is_file(), f"Missing file: {rel_path}"
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual_sha == expected_sha, f"SHA mismatch on {rel_path}: {actual_sha} != {expected_sha}"
    print(f"[PASS] Manifest SHA-256: {len(lines)}/{len(lines)} correct.")

    # 2. Compileall
    res = subprocess.run([sys.executable, "-m", "compileall", td], capture_output=True, text=True)
    assert res.returncode == 0, f"Compileall failed: {res.stderr}"
    print("[PASS] Compileall: PASS")

    # 3. Pytest critical tests (test_122 and test_136 to test_143)
    cmd = [sys.executable, "-m", "pytest", f"{td}/tests/unit/test_checkpoint_preflight_resume.py", "-k", "test_122 or test_136 or test_137 or test_138 or test_139 or test_140 or test_141 or test_142 or test_143", "-v"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout)
    assert res.returncode == 0, f"Pytest failed: {res.stderr}"
    print("[PASS] Critical tests: ALL PASSED!")
