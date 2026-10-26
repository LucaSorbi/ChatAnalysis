import os
from pathlib import Path
import json

brain_dir = Path(r"C:\Users\Luca\.gemini\antigravity-ide\brain")
print(f"Scanning {brain_dir}...")

target_run = "run_disc_3f3731eb04"
findings = []

for root, dirs, files in os.walk(brain_dir):
    for fname in files:
        fpath = Path(root) / fname
        # Skip video or huge binaries if any
        if fpath.suffix in [".webp", ".png", ".jpg", ".bin"]:
            continue
        try:
            sz = fpath.stat().st_size
            if sz > 200 * 1024 * 1024:
                continue
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if target_run in content:
                    print(f"Found {target_run} in: {fpath} (size {sz} bytes)")
                    # Check if it has serialized topics
                    has_topics = '"topics"' in content
                    has_ser = "serialized_result" in content
                    print(f"   Contains '\"topics\"': {has_topics}")
                    print(f"   Contains 'serialized_result': {has_ser}")
                    findings.append((fpath, has_topics, has_ser))
        except Exception as e:
            pass

print(f"\nTotal files referencing {target_run}: {len(findings)}")
