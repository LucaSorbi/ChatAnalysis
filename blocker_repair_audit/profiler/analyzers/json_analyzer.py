"""
profiler/analyzers/json_analyzer.py
Analyses JSON/JSONL files: structure, keys, depth.
"""
from __future__ import annotations
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from profiler.config import ProfilerConfig


@dataclass
class JSONProfile:
    file_path: str
    size_bytes: int
    format: str
    root_type: str
    total_records: int
    max_depth: int
    key_frequency: dict
    errors: list = field(default_factory=list)


def _depth(obj, d=0):
    if isinstance(obj, dict):  return max((_depth(v,d+1) for v in obj.values()), default=d)
    if isinstance(obj, list):  return max((_depth(v,d+1) for v in obj),           default=d)
    return d

def _keys(obj, prefix="", counter=None):
    if counter is None: counter = Counter()
    if isinstance(obj, dict):
        for k,v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            counter[full] += 1
            _keys(v, full, counter)
    elif isinstance(obj, list):
        for item in obj: _keys(item, prefix, counter)
    return counter

def analyze(json_path: Path, cfg: ProfilerConfig) -> JSONProfile:
    root = Path(cfg.source_path).resolve()
    rel = str(json_path.relative_to(root)) if json_path.is_absolute() else str(json_path)
    size = json_path.stat().st_size
    errors, records, fmt, root_type = [], [], "unknown", "unknown"
    try:
        with open(json_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(min(size, 10*1024*1024))
        try:
            obj = json.loads(content)
            if isinstance(obj, list):   fmt, root_type, records = "json_array", "array", obj[:cfg.max_sample_rows]
            elif isinstance(obj, dict): fmt, root_type, records = "json_object", "object", [obj]
            else:                       fmt, root_type = "json_scalar", type(obj).__name__
        except json.JSONDecodeError:
            fmt = "jsonl"; root_type = "lines"
            for line in content.splitlines():
                line = line.strip()
                if not line: continue
                try:
                    records.append(json.loads(line))
                    if len(records) >= cfg.max_sample_rows: break
                except: pass
    except Exception as e:
        errors.append(str(e))
    max_depth = 0; key_freq: Counter = Counter()
    for rec in records[:1000]:
        d = _depth(rec)
        if d > max_depth: max_depth = d
        _keys(rec, counter=key_freq)
    return JSONProfile(file_path=rel, size_bytes=size, format=fmt,
                       root_type=root_type, total_records=len(records),
                       max_depth=max_depth,
                       key_frequency=dict(key_freq.most_common(50)),
                       errors=errors)
