"""
profiler/profiler.py
Orchestrator: ties together all analyzers. READ-ONLY contract.
"""
from __future__ import annotations
import dataclasses, datetime, re, hashlib, sqlite3, csv as csv_mod
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from profiler.config import ProfilerConfig
from profiler.analyzers.file_scanner import FileEntry, scan
from profiler.analyzers.sqlite_analyzer import SQLiteProfile, analyze as analyze_sqlite
from profiler.analyzers.csv_analyzer    import CSVProfile,    analyze as analyze_csv
from profiler.analyzers.json_analyzer   import JSONProfile,   analyze as analyze_json
from profiler.analyzers.xml_analyzer    import XMLProfile,    analyze as analyze_xml
from profiler.analyzers.media_analyzer  import MediaProfile,  analyze as analyze_media
from profiler.analyzers.text_analyzer   import TextProfile,   analyze as analyze_text


class Anonymizer:
    def __init__(self):
        self._map = {}; self._cnt = Counter()
    def _token(self, cat, raw):
        key = (cat, raw)
        if key not in self._map:
            self._cnt[cat] += 1
            self._map[key] = f"{cat.upper()}_{self._cnt[cat]:03d}"
        return self._map[key]
    def sender(self, raw): return self._token("sender", raw)


@dataclass
class FileSummary:
    total_files: int
    by_type: dict
    by_extension: dict
    total_size_bytes: int
    largest_file: str
    largest_file_bytes: int


@dataclass
class DataProfile:
    source_path: str
    profiler_version: str
    generated_at: str
    config_summary: dict
    file_summary: FileSummary
    sqlite_profiles: list
    csv_profiles: list
    json_profiles: list
    xml_profiles: list
    media_profile: MediaProfile
    text_profile: object  # TextProfile | None
    anomalies: list
    open_questions: list
    importer_hints: list


def run(cfg: ProfilerConfig) -> DataProfile:
    root = Path(cfg.source_path).resolve()
    print("[profiler] Scanning filesystem...")
    entries: list = scan(cfg)

    by_type = Counter(e.file_type for e in entries)
    by_ext  = Counter(e.extension for e in entries)
    total_size = sum(e.size_bytes for e in entries)
    largest = max(entries, key=lambda e: e.size_bytes, default=None)
    file_summary = FileSummary(
        total_files=len(entries),
        by_type=dict(by_type),
        by_extension=dict(by_ext.most_common()),
        total_size_bytes=total_size,
        largest_file=largest.relative_path if largest else "",
        largest_file_bytes=largest.size_bytes if largest else 0,
    )

    anomalies, importer_hints = [], []

    sqlite_entries = [e for e in entries if e.file_type == "sqlite"]
    sqlite_profiles = []
    for e in sqlite_entries:
        print(f"[profiler]   SQLite: {e.relative_path}")
        p = analyze_sqlite(e.path, cfg)
        sqlite_profiles.append(p)
        if p.errors:
            for err in p.errors: anomalies.append(f"SQLite {e.relative_path}: {err}")
        for t in p.tables:
            if t.is_likely_messages:
                importer_hints.append(
                    f"Likely message table: '{t.name}' in {e.relative_path} ({t.row_count:,} rows)")

    csv_entries = [e for e in entries if e.file_type == "csv"]
    csv_profiles = []
    for e in csv_entries:
        print(f"[profiler]   CSV: {e.relative_path}")
        p = analyze_csv(e.path, cfg)
        csv_profiles.append(p)
        if p.errors:
            for err in p.errors: anomalies.append(f"CSV {e.relative_path}: {err}")
        if any(c.inferred_role == "body" for c in p.columns):
            body_cols = [c.name for c in p.columns if c.inferred_role == "body"]
            importer_hints.append(
                f"Likely message CSV: {e.relative_path} ({p.total_rows_estimated:,} rows, body cols: {body_cols})")

    json_entries = [e for e in entries if e.file_type == "json"]
    json_profiles = []
    for e in json_entries:
        print(f"[profiler]   JSON: {e.relative_path}")
        p = analyze_json(e.path, cfg)
        json_profiles.append(p)
        if p.errors:
            for err in p.errors: anomalies.append(f"JSON {e.relative_path}: {err}")

    xml_entries = [e for e in entries if e.file_type == "xml"]
    xml_profiles = []
    for e in xml_entries:
        print(f"[profiler]   XML: {e.relative_path}")
        p = analyze_xml(e.path, cfg)
        xml_profiles.append(p)
        if p.errors:
            for err in p.errors: anomalies.append(f"XML {e.relative_path}: {err}")

    print("[profiler]   Media files...")
    media_profile = analyze_media(entries, cfg)

    # Text analysis: reload body column values from SQLite & CSV
    all_body_texts = []
    for e in sqlite_entries:
        try:
            uri = e.path.as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            for sp in sqlite_profiles:
                rel_check = str(e.path.relative_to(root))
                if sp.file_path == rel_check:
                    for tp in sp.tables:
                        if tp.is_likely_messages:
                            body_cols = [c.name for c in tp.columns if c.inferred_role == "body"]
                            for bc in body_cols:
                                cur = conn.cursor()
                                cur.execute(f"SELECT [{bc}] FROM [{tp.name}] LIMIT {cfg.max_sample_rows}")
                                vals = [str(r[0]) for r in cur.fetchall() if r[0] is not None]
                                all_body_texts.extend(vals)
            conn.close()
        except Exception as ex:
            anomalies.append(f"Text re-read {e.relative_path}: {ex}")

    for e in csv_entries:
        for cp in csv_profiles:
            if cp.file_path == str(e.path.relative_to(root)):
                body_col_names = [c.name for c in cp.columns if c.inferred_role == "body"]
                if body_col_names:
                    try:
                        with open(e.path, "r", encoding=cp.encoding, errors="replace", newline="") as f:
                            reader = csv_mod.DictReader(f)
                            for i, row in enumerate(reader):
                                if i >= cfg.max_sample_rows: break
                                for bc in body_col_names:
                                    v = row.get(bc, "")
                                    if v: all_body_texts.append(v)
                    except Exception as ex:
                        anomalies.append(f"Text re-read CSV {e.relative_path}: {ex}")

    text_profile = None
    if all_body_texts:
        print(f"[profiler]   Text analysis ({len(all_body_texts):,} messages)...")
        text_profile = analyze_text(all_body_texts)

    # Cross-cutting anomalies
    if media_profile.image.files_zero_bytes:
        anomalies.append(f"{media_profile.image.files_zero_bytes} image files are 0 bytes")
    if media_profile.audio.files_zero_bytes:
        anomalies.append(f"{media_profile.audio.files_zero_bytes} audio files are 0 bytes")
    if not sqlite_entries and not csv_entries and not json_entries and not xml_entries:
        anomalies.append("No structured data files (SQLite/CSV/JSON/XML) found.")

    open_questions = [
        "Are media files stored in a flat directory or nested subdirectories?",
        "Are audio messages referenced by filename or by a database ID?",
        "Are deleted messages present as rows with a flag, or absent entirely?",
        "Is there a separate contacts/participant table to resolve sender IDs?",
        "Are timestamps stored in UTC or local time? Is timezone stored?",
        "Are forwarded messages marked with a specific field or flag?",
        "What is the foreign key linking media records to message records?",
        "Are group chat participants stored separately from 1-to-1 chats?",
    ]

    config_summary = {
        "anonymize": cfg.anonymize,
        "include_sample_text": cfg.include_sample_text,
        "max_sample_rows": cfg.max_sample_rows,
        "recursive": cfg.recursive,
    }

    return DataProfile(
        source_path=str(root),
        profiler_version="1.0.0",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        config_summary=config_summary,
        file_summary=file_summary,
        sqlite_profiles=sqlite_profiles,
        csv_profiles=csv_profiles,
        json_profiles=json_profiles,
        xml_profiles=xml_profiles,
        media_profile=media_profile,
        text_profile=text_profile,
        anomalies=anomalies,
        open_questions=open_questions,
        importer_hints=importer_hints,
    )
