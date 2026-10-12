"""
profiler/report_writer.py
Writes DataProfile to data_profile.json and data_profile.md.
Never writes to the source directory. Never includes raw message text.
"""
from __future__ import annotations
import dataclasses, json
from pathlib import Path
from profiler.profiler import DataProfile
from profiler.config import ProfilerConfig


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if dataclasses.is_dataclass(obj): return dataclasses.asdict(obj)
        if isinstance(obj, (Path, frozenset)): return str(obj)
        return super().default(obj)


def _mb(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1024**2:   return f"{b/1024:.1f} KB"
    return f"{b/1024**2:.2f} MB"

def _pct(n: int, total: int) -> str:
    return f"{n/total*100:.1f}%" if total else "0%"

def _table(headers, rows) -> str:
    if not rows: return "_No data_"
    col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                  for i, h in enumerate(headers)]
    sep  = "| " + " | ".join("-"*w for w in col_widths) + " |"
    head = "| " + " | ".join(str(h).ljust(col_widths[i]) for i,h in enumerate(headers)) + " |"
    lines = [head, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))) + " |")
    return "\n".join(lines)


def write(profile: DataProfile, cfg: ProfilerConfig):
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "data_profile.json"
    md_path   = out / "data_profile.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(profile), f, cls=_Encoder, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_build_md(profile))
    return json_path, md_path


def _build_md(p: DataProfile) -> str:
    L = []
    A = L.append
    A("# Forensic Data Profile")
    A(f"")
    A(f"**Generated**: {p.generated_at}  ")
    A(f"**Profiler version**: {p.profiler_version}  ")
    A(f"**Anonymized**: {p.config_summary.get('anonymize',True)}  ")
    A(f"**Sample text included**: {p.config_summary.get('include_sample_text',False)}  ")
    A("")
    A("> [!CAUTION]")
    A("> This report does not contain raw message content. All sender identifiers have been anonymized.")
    A("")
    fs = p.file_summary
    A("---"); A("## 1. File Structure"); A("")
    A(f"| Metric | Value |"); A(f"|--------|-------|")
    A(f"| Total files | {fs.total_files:,} |")
    A(f"| Total size | {_mb(fs.total_size_bytes)} |")
    A(f"| Largest file | `{fs.largest_file}` ({_mb(fs.largest_file_bytes)}) |")
    A("")
    A("### Files by type"); A("")
    A(_table(["Type","Count","% of total"],
             [[t,f"{c:,}",_pct(c,fs.total_files)] for t,c in sorted(fs.by_type.items())]))
    A(""); A("### Files by extension"); A("")
    A(_table(["Extension","Count"],
             [[ext or "(none)",f"{c:,}"] for ext,c in list(fs.by_extension.items())[:25]]))
    A("")

    if p.sqlite_profiles:
        A("---"); A("## 2. SQLite Databases"); A("")
        for sp in p.sqlite_profiles:
            A(f"### `{sp.file_path}` ({_mb(sp.size_bytes)})")
            A(f"**Tables**: {len(sp.tables)} | **Views**: {len(sp.views)} | **Indexes**: {len(sp.indexes)}")
            A("")
            if sp.errors:
                A("> [!WARNING]")
                for e in sp.errors: A(f"> - {e}")
                A("")
            for t in sp.tables:
                tag = " ⚠️ *likely messages*" if t.is_likely_messages else ""
                A(f"#### Table: `{t.name}`{tag}")
                A(f"**Rows**: {t.row_count:,}")
                if t.notes:
                    for n in t.notes: A(f"> *{n}*")
                A("")
                col_rows = []
                for c in t.columns:
                    null_pct  = _pct(c.null_count, c.sample_values_count) if c.sample_values_count else "?"
                    uniq_pct  = _pct(c.unique_count, c.non_null_count)    if c.non_null_count else "?"
                    ts_fmt    = ", ".join(c.timestamp_info.get("formats_seen",[])) if c.timestamp_info else ""
                    ts_range  = ""
                    if c.timestamp_info:
                        mn = c.timestamp_info.get("min",""); mx = c.timestamp_info.get("max","")
                        if mn and mx: ts_range = f" [{mn[:10]} → {mx[:10]}]"
                    col_rows.append([c.name, c.declared_type, c.inferred_role,
                                     f"{null_pct} null", f"{uniq_pct} unique",
                                     f"avg {c.avg_length}ch", f"{ts_fmt}{ts_range}"])
                A(_table(["Column","Type","Role","Null","Unique","Avg len","Timestamp info"], col_rows))
                A("")
                if t.duplicate_fingerprints > 0:
                    A(f"> ⚠️ **Duplicate fingerprints**: {t.duplicate_fingerprints:,} extra rows ({t.duplicate_groups:,} groups)")
                A("")

    if p.csv_profiles:
        A("---"); A("## 3. CSV Files"); A("")
        for cp in p.csv_profiles:
            A(f"### `{cp.file_path}` ({_mb(cp.size_bytes)})")
            A(f"**Encoding**: {cp.encoding} | **Delimiter**: `{cp.delimiter}` | **Rows**: {cp.total_rows_estimated:,}")
            A("")
            if cp.errors:
                A("> [!WARNING]")
                for e in cp.errors: A(f"> - {e}")
                A("")
            A(_table(["Column","Role","Null","Unique","Avg len"],
                     [[c.name,c.inferred_role,f"{c.null_rate*100:.1f}%",
                       f"{c.unique_rate*100:.1f}%",f"avg {c.avg_length}ch"] for c in cp.columns]))
            if cp.duplicate_fingerprints > 0:
                A(f""); A(f"> ⚠️ **Duplicates**: {cp.duplicate_fingerprints:,} extra rows")
            A("")

    if p.json_profiles:
        A("---"); A("## 4. JSON Files"); A("")
        for jp in p.json_profiles:
            A(f"### `{jp.file_path}` ({_mb(jp.size_bytes)})")
            A(f"**Format**: {jp.format} | **Records**: {jp.total_records:,} | **Max depth**: {jp.max_depth}")
            A("")
            if jp.key_frequency:
                A(_table(["Key","Count"],[[k,f"{v:,}"] for k,v in list(jp.key_frequency.items())[:20]]))
            A("")

    if p.xml_profiles:
        A("---"); A("## 5. XML / UFDR Files"); A("")
        for xp in p.xml_profiles:
            A(f"### `{xp.file_path}` ({_mb(xp.size_bytes)})")
            A(f"**Root**: `{xp.root_tag}` | **Depth**: {xp.max_depth} | **Elements**: {xp.total_elements:,}")
            A("")
            if xp.tag_frequency:
                A(_table(["Tag","Count"],[[t,f"{c:,}"] for t,c in list(xp.tag_frequency.items())[:15]]))
            A("")

    A("---"); A("## 6. Media Files"); A("")
    mp = p.media_profile
    def ms(name, stats):
        if stats.total_count == 0:
            A(f"**{name}**: none found"); return
        A(f"### {name}")
        A(f"**Count**: {stats.total_count:,} | **Total**: {_mb(stats.total_size_bytes)} | "
          f"**Avg**: {_mb(int(stats.size_avg_bytes))} | **Zero-byte**: {stats.files_zero_bytes}")
        A("")
        if stats.by_extension:
            A(_table(["Extension","Count"],[[e,f"{c:,}"] for e,c in stats.by_extension.items()]))
        if stats.image_dimensions:
            ws=[d[0] for d in stats.image_dimensions]; hs=[d[1] for d in stats.image_dimensions]
            A(f"**Dimensions sample** ({len(ws)} files): avg {int(sum(ws)/len(ws))}x{int(sum(hs)/len(hs))}px, "
              f"max {max(ws)}x{max(hs)}px")
        for n in stats.notes: A(f"> *{n}*")
        A("")
    ms("Audio", mp.audio); ms("Images", mp.image)
    ms("Video", mp.video); ms("Documents / Attachments", mp.document)

    A("---"); A("## 7. Text / Message Content Analysis"); A("")
    if p.text_profile:
        tp = p.text_profile
        A(f"| Metric | Value |"); A(f"|--------|-------|")
        A(f"| Total messages | {tp.total_messages:,} |")
        A(f"| Empty messages | {tp.empty_messages:,} ({_pct(tp.empty_messages,tp.total_messages)}) |")
        A(f"| Avg length | {tp.avg_length_chars} chars |")
        A(f"| Min / Max length | {tp.min_length_chars} / {tp.max_length_chars} chars |")
        A(f"| With emoji | {tp.messages_with_emoji:,} ({_pct(tp.messages_with_emoji,tp.total_messages)}) |")
        A(f"| With URLs | {tp.messages_with_urls:,} |")
        A(f"| With phone numbers | {tp.messages_with_phone_numbers:,} |")
        A(f"| With newlines | {tp.messages_with_newlines:,} |")
        A(f"| Media placeholder only | {tp.messages_with_only_media_placeholder:,} |")
        A(f"| Encoding issues (U+FFFD) | {tp.encoding_issues_count:,} |")
        A(""); A("### Length distribution"); A("")
        A(_table(["Bucket","Count","%"],
                 [[b,f"{c:,}",_pct(c,tp.total_messages)] for b,c in tp.length_distribution.items()]))
        if tp.top_emoji:
            A(""); A("### Top emoji"); A("")
            A(_table(["Emoji","Count"],[[e,f"{c:,}"] for e,c in tp.top_emoji]))
        A("")
    else:
        A("> No text content found to analyse."); A("")

    A("---"); A("## 8. Anomalies"); A("")
    if p.anomalies:
        for a in p.anomalies: A(f"- ⚠️ {a}")
    else:
        A("- No anomalies detected.")
    A("")
    A("---"); A("## 9. Importer Hints"); A("")
    if p.importer_hints:
        for h in p.importer_hints: A(f"- 💡 {h}")
    else:
        A("- No specific hints.")
    A("")
    A("---"); A("## 10. Open Questions"); A("")
    for q in p.open_questions: A(f"- ❓ {q}")
    A("")
    return "\n".join(L)
