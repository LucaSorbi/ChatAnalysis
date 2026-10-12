"""
profiler/analyzers/media_analyzer.py
Counts and statistically describes media files.
No OCR, no transcription.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from profiler.config import ProfilerConfig


@dataclass
class MediaStats:
    total_count: int = 0
    total_size_bytes: int = 0
    by_extension: dict = field(default_factory=dict)
    size_min_bytes: int = 0
    size_max_bytes: int = 0
    size_avg_bytes: float = 0.0
    files_zero_bytes: int = 0
    files_inspected: int = 0
    image_dimensions: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class MediaProfile:
    audio: MediaStats = field(default_factory=MediaStats)
    image: MediaStats = field(default_factory=MediaStats)
    video: MediaStats = field(default_factory=MediaStats)
    document: MediaStats = field(default_factory=MediaStats)


def _stats(entries, limit, read_dims=True) -> MediaStats:
    if not entries: return MediaStats()
    sizes = [e.size_bytes for e in entries]
    stats = MediaStats(
        total_count=len(entries),
        total_size_bytes=sum(sizes),
        by_extension=dict(Counter(e.extension for e in entries)),
        size_min_bytes=min(sizes), size_max_bytes=max(sizes),
        size_avg_bytes=round(sum(sizes)/len(sizes), 1),
        files_zero_bytes=sum(1 for s in sizes if s == 0),
    )
    to_inspect = entries[:limit] if limit > 0 else entries
    stats.files_inspected = len(to_inspect)
    if read_dims:
        dims = []
        for e in to_inspect:
            if e.extension.lower() in (".jpg",".jpeg",".png",".gif",".bmp",".webp"):
                try:
                    from PIL import Image
                    with Image.open(e.path) as img: dims.append(list(img.size))
                    if len(dims) >= 200: break
                except Exception: pass
        if dims: stats.image_dimensions = dims
    if limit > 0 and len(entries) > limit:
        stats.notes.append(f"Inspected {limit} of {len(entries)} files")
    return stats


def analyze(entries, cfg: ProfilerConfig) -> MediaProfile:
    lim = cfg.max_media_files_inspected
    return MediaProfile(
        audio=_stats([e for e in entries if e.file_type=="audio"],    lim, False),
        image=_stats([e for e in entries if e.file_type=="image"],    lim, True),
        video=_stats([e for e in entries if e.file_type=="video"],    lim, False),
        document=_stats([e for e in entries if e.file_type=="document"], lim, False),
    )
