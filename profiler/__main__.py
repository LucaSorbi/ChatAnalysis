"""
profiler/__main__.py  --  Entry point: python -m profiler SOURCE [OPTIONS]
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from profiler.config import ProfilerConfig
from profiler.profiler import run
from profiler.report_writer import write

def main():
    parser = argparse.ArgumentParser(
        prog="python -m profiler",
        description="Forensic conversation data profiler (read-only).",
    )
    parser.add_argument("source", help="Path to directory or file to profile")
    parser.add_argument("--output", "-o", default="output")
    parser.add_argument("--no-anonymize", action="store_true")
    parser.add_argument("--include-text", action="store_true")
    parser.add_argument("--max-rows", type=int, default=50_000)
    parser.add_argument("--no-recursive", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        print(f"ERROR: {source} does not exist", file=sys.stderr); sys.exit(1)

    output = Path(args.output).resolve()

    cfg = ProfilerConfig(
        source_path=source, output_dir=output,
        anonymize=not args.no_anonymize,
        include_sample_text=args.include_text,
        max_sample_rows=args.max_rows,
        recursive=not args.no_recursive,
    )

    print(f"[profiler] Source    : {cfg.source_path}")
    print(f"[profiler] Output    : {cfg.output_dir}")
    print(f"[profiler] Anonymize : {cfg.anonymize}")
    print()

    profile = run(cfg)
    json_path, md_path = write(profile, cfg)

    print()
    print(f"[profiler] Reports written:")
    print(f"  JSON -> {json_path}")
    print(f"  MD   -> {md_path}")
    print()
    print(f"[profiler] Summary:")
    print(f"  Files      : {profile.file_summary.total_files:,}")
    print(f"  SQLite DBs : {len(profile.sqlite_profiles)}")
    print(f"  CSV files  : {len(profile.csv_profiles)}")
    print(f"  JSON files : {len(profile.json_profiles)}")
    print(f"  XML files  : {len(profile.xml_profiles)}")
    print(f"  Audio      : {profile.media_profile.audio.total_count:,}")
    print(f"  Images     : {profile.media_profile.image.total_count:,}")
    print(f"  Anomalies  : {len(profile.anomalies)}")

if __name__ == "__main__":
    main()
