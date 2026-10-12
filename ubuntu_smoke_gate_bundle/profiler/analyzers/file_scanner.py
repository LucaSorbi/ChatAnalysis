"""
profiler/analyzers/file_scanner.py
------------------------------------
Recursively walks source_path (READ-ONLY) and classifies every file.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from profiler.config import ProfilerConfig


@dataclass
class FileEntry:
    path: Path
    relative_path: str
    extension: str
    file_type: str
    size_bytes: int
    sha256: str = ""

    @property
    def size_kb(self): return round(self.size_bytes / 1024, 2)
    @property
    def size_mb(self): return round(self.size_bytes / (1024 * 1024), 4)


def _classify(ext: str, cfg: ProfilerConfig) -> str:
    e = ext.lower()
    if e in (".db", ".sqlite", ".sqlite3"): return "sqlite"
    if e == ".csv":                          return "csv"
    if e in (".json", ".jsonl", ".ndjson"): return "json"
    if e in (".xml", ".ufdr"):              return "xml"
    if e in cfg.audio_extensions:          return "audio"
    if e in cfg.image_extensions:          return "image"
    if e in cfg.video_extensions:          return "video"
    if e in cfg.document_extensions:       return "document"
    return "other"


def _sha256(path: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return "ERROR"


def scan(cfg: ProfilerConfig, hash_files: bool = False) -> list:
    root = Path(cfg.source_path).resolve()
    entries = []
    pattern = "**/*" if cfg.recursive else "*"
    for p in root.glob(pattern):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in cfg.skip_extensions:
            continue
        rel = str(p.relative_to(root))
        ftype = _classify(ext, cfg)
        size = p.stat().st_size
        sha = _sha256(p) if hash_files else ""
        entries.append(FileEntry(path=p, relative_path=rel, extension=ext,
                                  file_type=ftype, size_bytes=size, sha256=sha))
    return entries
