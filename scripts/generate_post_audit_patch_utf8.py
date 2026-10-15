"""
scripts/generate_post_audit_patch_utf8.py
-----------------------------------------
Genera la patch Git UTF-8 completa delle modifiche rispetto a HEAD (6c1dc46).
"""
import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parents[1]
    patch_path = root / "git_diff_post_audit_corrective.patch"
    
    # 1. Tracked changes vs HEAD
    cmd = ["git", "diff", "HEAD"]
    res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    
    diff_text = res.stdout
    
    # 2. Add untracked new files as diffs
    untracked_files = [
        "ai/token_budget.py",
        "analysis/progress.py",
        "tests/unit/test_integrated_audit_fixes.py",
        "tests/unit/test_realworld_topic_improvements.py",
        "scripts/measure_scalability_and_profiling.py",
        "scripts/run_and_log_tests_utf8.py",
        "test_data/synthetic_whatsapp_large_75kb.txt",
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
    print(f"Patch salvata con successo in: {patch_path} ({len(diff_text)} caratteri)")

if __name__ == "__main__":
    main()
