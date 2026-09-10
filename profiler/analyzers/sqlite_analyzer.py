"""
profiler/analyzers/sqlite_analyzer.py
---------------------------------------
Opens SQLite files READ-ONLY (?mode=ro) and profiles schema + statistics.
Never stores raw message text.
"""
from __future__ import annotations
import re, sqlite3, hashlib, datetime
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from profiler.config import ProfilerConfig

TS_RE     = re.compile(r"(timestamp|time|date|created|modified|sent|received|_at)$", re.I)
SENDER_RE = re.compile(r"(sender|from|author|source|participant|jid|phone)$", re.I)
BODY_RE   = re.compile(r"(body|text|message|content|msg|chat|caption)$", re.I)
MEDIA_RE  = re.compile(r"(media|attach|file|path|url|thumb|key|name)$", re.I)


def _role(name: str) -> str:
    n = name.lower()
    if TS_RE.search(n):     return "timestamp"
    if SENDER_RE.search(n): return "sender"
    if BODY_RE.search(n):   return "body"
    if MEDIA_RE.search(n):  return "media"
    if n in ("id", "_id", "rowid", "uuid", "z_pk"): return "id"
    return "unknown"


def _infer_ts(values: list) -> dict:
    info = {"formats_seen": [], "min": None, "max": None,
            "null_count": 0, "zero_epoch_count": 0,
            "future_count": 0, "negative_count": 0, "anomaly_count": 0}
    try:
        from dateutil import parser as dup
    except ImportError:
        return info
    parsed = []
    future_year = 2030
    for v in values:
        if v is None or v == "":
            info["null_count"] += 1
            continue
        try:
            n = float(v)
            if n == 0:
                info["zero_epoch_count"] += 1; info["anomaly_count"] += 1; continue
            if n < 0:
                info["negative_count"] += 1;  info["anomaly_count"] += 1; continue
            if n > 1e12:
                dt = datetime.datetime.fromtimestamp(n/1000, tz=datetime.timezone.utc)
                fmt = "unix_ms"
            else:
                dt = datetime.datetime.fromtimestamp(n, tz=datetime.timezone.utc)
                fmt = "unix_s"
            if fmt not in info["formats_seen"]: info["formats_seen"].append(fmt)
            if dt.year > future_year: info["future_count"] += 1; info["anomaly_count"] += 1
            parsed.append(dt)
            continue
        except (ValueError, TypeError, OSError):
            pass
        try:
            dt = dup.parse(str(v))
            fmt = "iso_string"
            if fmt not in info["formats_seen"]: info["formats_seen"].append(fmt)
            if dt.year > future_year: info["future_count"] += 1; info["anomaly_count"] += 1
            parsed.append(dt)
        except Exception:
            info["anomaly_count"] += 1
    if parsed:
        info["min"] = str(min(parsed))
        info["max"] = str(max(parsed))
    return info


@dataclass
class ColumnProfile:
    name: str
    declared_type: str
    inferred_role: str
    null_count: int = 0
    non_null_count: int = 0
    unique_count: int = 0
    sample_values_count: int = 0
    avg_length: float = 0.0
    timestamp_info: dict = field(default_factory=dict)


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: list
    is_likely_messages: bool = False
    duplicate_fingerprints: int = 0
    duplicate_groups: int = 0
    notes: list = field(default_factory=list)


@dataclass
class SQLiteProfile:
    file_path: str
    size_bytes: int
    tables: list
    views: list
    indexes: list
    errors: list = field(default_factory=list)


def _profile_table(conn, table: str, cfg: ProfilerConfig) -> TableProfile:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM [{table}]")
    row_count = cur.fetchone()[0]
    cur.execute(f"PRAGMA table_info([{table}])")
    col_info = cur.fetchall()
    col_names = [c[1] for c in col_info]
    columns = []
    is_likely_messages = False
    sample_limit = min(cfg.max_sample_rows, row_count)
    rows = []
    if col_names:
        placeholders = ", ".join(f"[{c}]" for c in col_names)
        cur.execute(f"SELECT {placeholders} FROM [{table}] LIMIT {sample_limit}")
        rows = cur.fetchall()
    for i, ci in enumerate(col_info):
        cname, ctype = ci[1], ci[2] or "UNTYPED"
        role = _role(cname)
        col_vals = [r[i] for r in rows]
        non_null = [v for v in col_vals if v is not None and v != ""]
        null_count = len(col_vals) - len(non_null)
        unique_count = len(set(non_null))
        avg_len = round(sum(len(str(v)) for v in non_null)/len(non_null), 1) if non_null else 0.0
        ts_info = {}
        if role == "timestamp" and non_null:
            ts_info = _infer_ts(non_null[:2000])
        columns.append(ColumnProfile(name=cname, declared_type=ctype,
                                     inferred_role=role, null_count=null_count,
                                     non_null_count=len(non_null),
                                     unique_count=unique_count,
                                     sample_values_count=len(col_vals),
                                     avg_length=avg_len, timestamp_info=ts_info))
        if role in ("body", "sender"):
            is_likely_messages = True
    fp_fields = [f for f in cfg.duplicate_fingerprint_fields if f in col_names]
    dup_fps = dup_groups = 0
    if fp_fields and rows:
        idx = [col_names.index(f) for f in fp_fields]
        fps = [hashlib.md5(str(tuple(r[i] for i in idx)).encode()).hexdigest() for r in rows]
        cnt = Counter(fps)
        dup_groups = sum(1 for v in cnt.values() if v > 1)
        dup_fps = sum(v-1 for v in cnt.values() if v > 1)
    notes = []
    if row_count > cfg.max_sample_rows:
        notes.append(f"Sampled {cfg.max_sample_rows:,} of {row_count:,} rows")
    return TableProfile(name=table, row_count=row_count, columns=columns,
                        is_likely_messages=is_likely_messages,
                        duplicate_fingerprints=dup_fps, duplicate_groups=dup_groups,
                        notes=notes)


def analyze(db_path: Path, cfg: ProfilerConfig) -> SQLiteProfile:
    errors, tables, views, indexes = [], [], [], []
    root = Path(cfg.source_path).resolve()
    rel = str(db_path.relative_to(root)) if db_path.is_absolute() else str(db_path)
    try:
        uri = db_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view','index') ORDER BY type, name")
        for row in cur.fetchall():
            if row["type"] == "view":    views.append(row["name"])
            elif row["type"] == "index": indexes.append(row["name"])
            else:
                try:    tables.append(_profile_table(conn, row["name"], cfg))
                except Exception as e: errors.append(f"Table {row['name']}: {e}")
        conn.close()
    except Exception as e:
        errors.append(str(e))
    size = db_path.stat().st_size if db_path.exists() else 0
    return SQLiteProfile(file_path=rel, size_bytes=size, tables=tables,
                         views=views, indexes=indexes, errors=errors)
