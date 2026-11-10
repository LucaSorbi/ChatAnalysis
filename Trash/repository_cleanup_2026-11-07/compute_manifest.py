#!/usr/bin/env python3
"""Compute SHA256 manifest for source files and compare with existing one."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(".")
manifest_path = Path("MANIFEST_SHA256.json")

existing = {}
if manifest_path.exists():
    with open(manifest_path, encoding="utf-8") as f:
        existing = json.load(f)

extensions = {".py", ".toml", ".cfg", ".md", ".sh", ".txt", ".json"}
skip_fragments = [".git", "__pycache__", "dist", "build", ".eggs", "egg-info"]

results = {}
errors = []

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    path_str = str(path)
    if any(d in path_str for d in skip_fragments):
        continue
    if path.suffix not in extensions:
        continue
    rel = str(path.relative_to(root)).replace("\\", "/")
    try:
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        results[rel] = h
    except Exception as e:
        errors.append(f"{rel}: {e}")

total = len(results)
print(f"Files hashed: {total}")

if existing:
    added = set(results) - set(existing)
    removed = set(existing) - set(results)
    changed = {k for k in results if k in existing and results[k] != existing[k]}
    print(f"Added: {len(added)}, Removed: {len(removed)}, Changed: {len(changed)}")
    for k in sorted(changed):
        print(f"  CHANGED: {k}")
    for k in sorted(added):
        print(f"  ADDED: {k}")
    for k in sorted(removed):
        print(f"  REMOVED: {k}")
    if not added and not removed and not changed:
        print("Manifest VERIFIED: all files match.")
else:
    print("No existing manifest — generating new one.")

with open("MANIFEST_SHA256.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print(f"Manifest saved: {total} entries.")

if errors:
    print("Errors:", errors)
    sys.exit(1)
