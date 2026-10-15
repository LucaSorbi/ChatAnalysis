"""
scripts/generate_final_performance_accounting_patch_utf8.py
-----------------------------------------------------------
Genera la patch Git UTF-8 completa (git_diff_final_performance_accounting.patch)
delle modifiche per la release di audit finale e correzione contatori HTTP.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    patch_path = root / "git_diff_final_performance_accounting.patch"

    # 1. Tracked changes vs HEAD
    cmd = ["git", "diff", "HEAD"]
    res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    diff_text = res.stdout

    # 2. Add untracked new files as unified diffs
    untracked_files = [
        "analysis/checkpoint.py",
        "tests/unit/test_performance_accounting_audit.py",
        "scripts/package_final_performance_accounting_zip.py",
        "scripts/generate_final_performance_accounting_patch_utf8.py",
    ]

    for rel_path in untracked_files:
        p = root / rel_path
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            diff_text += f"\ndiff --git a/{rel_path} b/{rel_path}\n"
            diff_text += f"new file mode 100644\n"
            diff_text += f"--- /dev/null\n"
            diff_text += f"+++ b/{rel_path}\n"
            diff_text += f"@@ -0,0 +1,{len(lines)} @@\n"
            for line in lines:
                diff_text += f"+{line}"
            if lines and not lines[-1].endswith("\n"):
                diff_text += "\n\\ No newline at end of file\n"

    patch_path.write_text(diff_text, encoding="utf-8")
    print(f"Patch salvata con successo in: {patch_path} ({len(diff_text):,} caratteri)")


if __name__ == "__main__":
    main()
