"""
profiler/analyzers/csv_analyzer.py
Analyses CSV files for schema, types, null rates, duplicates.
"""
from __future__ import annotations
import csv, hashlib, re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from profiler.config import ProfilerConfig

TS_RE     = re.compile(r"(timestamp|time|date|created|sent|received|_at)$", re.I)
SENDER_RE = re.compile(r"(sender|from|author|source|participant|jid|phone)$", re.I)
BODY_RE   = re.compile(r"(body|text|message|content|msg|chat|caption)$", re.I)
MEDIA_RE  = re.compile(r"(media|attach|file|path|url|thumb)$", re.I)

def _role(name: str) -> str:
    n = name.lower()
    if TS_RE.search(n):     return "timestamp"
    if SENDER_RE.search(n): return "sender"
    if BODY_RE.search(n):   return "body"
    if MEDIA_RE.search(n):  return "media"
    if n in ("id", "_id", "uuid"): return "id"
    return "unknown"

def _detect_encoding(path: Path) -> str:
    try:
        from charset_normalizer import from_path
        results = from_path(str(path), cp_isolation=["utf-8","utf-16","latin-1","cp1252"])
        best = results.best()
        return best.first().encoding if best else "utf-8"
    except Exception:
        return "utf-8"

@dataclass
class CSVColumnProfile:
    name: str
    inferred_role: str
    null_rate: float
    unique_rate: float
    avg_length: float

@dataclass
class CSVProfile:
    file_path: str
    size_bytes: int
    encoding: str
    delimiter: str
    total_rows_estimated: int
    sampled_rows: int
    columns: list
    duplicate_fingerprints: int
    duplicate_groups: int
    errors: list = field(default_factory=list)

def analyze(csv_path: Path, cfg: ProfilerConfig) -> CSVProfile:
    errors = []
    root = Path(cfg.source_path).resolve()
    rel = str(csv_path.relative_to(root)) if csv_path.is_absolute() else str(csv_path)
    size = csv_path.stat().st_size
    encoding = _detect_encoding(csv_path)
    try:
        with open(csv_path, "r", encoding=encoding, errors="replace") as f:
            sample = f.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ","
    rows, total = [], 0
    try:
        with open(csv_path, "r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                total += 1
                if total <= cfg.max_sample_rows:
                    rows.append(row)
    except Exception as e:
        errors.append(str(e))
    cols = []
    dup_fps = dup_groups = 0
    if rows:
        headers = list(rows[0].keys())
        fp_fields = [h for h in headers if h.lower() in [f.lower() for f in cfg.duplicate_fingerprint_fields]]
        if not fp_fields: fp_fields = headers
        fps = [hashlib.md5(str(tuple(str(r.get(f,"")).lower() for f in fp_fields)).encode()).hexdigest() for r in rows]
        cnt = Counter(fps)
        dup_groups = sum(1 for v in cnt.values() if v > 1)
        dup_fps = sum(v-1 for v in cnt.values() if v > 1)
        for h in headers:
            vals = [r.get(h,"") for r in rows]
            non_null = [v for v in vals if v and v.strip()]
            null_rate = round(1 - len(non_null)/len(vals), 4) if vals else 0.0
            unique_rate = round(len(set(non_null))/len(non_null), 4) if non_null else 0.0
            avg_len = round(sum(len(v) for v in non_null)/len(non_null), 1) if non_null else 0.0
            cols.append(CSVColumnProfile(name=h, inferred_role=_role(h),
                                          null_rate=null_rate, unique_rate=unique_rate,
                                          avg_length=avg_len))
    return CSVProfile(file_path=rel, size_bytes=size, encoding=encoding,
                      delimiter=repr(delimiter), total_rows_estimated=total,
                      sampled_rows=len(rows), columns=cols,
                      duplicate_fingerprints=dup_fps, duplicate_groups=dup_groups,
                      errors=errors)
