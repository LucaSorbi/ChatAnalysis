"""
profiler/config.py
------------------
Configuration for the forensic data profiler.

PRIVACY RULES enforced here:
- anonymize=True  (default): sender IDs, phone numbers are replaced with
  opaque tokens before appearing in any report.
- include_sample_text=False (default): no raw message text is ever written
  to the report files.
- max_sample_rows: limits how many rows are loaded into memory for schema
  inference; the rest are counted, not read.

The profiler NEVER writes to the source directory.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProfilerConfig:
    # --- Paths ---------------------------------------------------------------
    source_path: Path = Path(".")
    output_dir: Path = Path("output")

    # --- Privacy -------------------------------------------------------------
    anonymize: bool = True
    include_sample_text: bool = False
    sample_text_max_chars: int = 80

    # --- Sampling limits -----------------------------------------------------
    max_sample_rows: int = 50_000
    max_media_files_inspected: int = 2_000

    # --- Duplicate detection -------------------------------------------------
    duplicate_fingerprint_fields: list = field(
        default_factory=lambda: ["timestamp", "sender", "body"]
    )

    # --- Timestamp anomalies -------------------------------------------------
    future_threshold_year: int = 2030
    epoch_zero_is_anomaly: bool = True

    # --- Misc ----------------------------------------------------------------
    min_text_bytes: int = 1
    recursive: bool = True
    skip_extensions: tuple = (
        ".ds_store", ".thumbs.db", ".ini", ".lnk",
    )

    # --- Media type sets -----------------------------------------------------
    audio_extensions: frozenset = field(default_factory=lambda: frozenset({
        ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac",
        ".wav", ".flac", ".amr", ".3gp", ".wma", ".aiff",
    }))
    image_extensions: frozenset = field(default_factory=lambda: frozenset({
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".heic", ".heif", ".tiff", ".tif", ".avif",
    }))
    video_extensions: frozenset = field(default_factory=lambda: frozenset({
        ".mp4", ".mov", ".avi", ".mkv", ".webm",
        ".m4v", ".wmv", ".flv", ".ts",
    }))
    document_extensions: frozenset = field(default_factory=lambda: frozenset({
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
        ".ppt", ".txt", ".rtf", ".zip", ".rar", ".7z",
        ".apk", ".vcf", ".ics",
    }))
