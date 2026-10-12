"""
scripts/benchmark_large_ingestion.py
------------------------------------
Strumento di benchmark e misurazione empirica per il BLOCKER 3:
LARGE-FILE INGESTION & BOUNDED-MEMORY PIPELINE.

Genera dataset sintetici deterministici (WhatsApp TXT format) a varie dimensioni:
- 10 MB
- 100 MB
- 250 MB
- 500 MB (opzionale/se abilitato)

Per ogni dimensione registra ed emette in tabella:
- input bytes
- record count
- peak RSS memory (MB)
- execution time (s)
- message count
- evidence count
- source hash (SHA-256)
- document integrity (validazione immutabilità e consistenza)

Distinzione architetturale tracciata:
- bounded working memory during ingestion (flusso di staging SQLite)
- total memory required by the final document (materializzazione finale in memoria)
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.models import SourceFormat
from ui.ingestion import ingest_file_payload


def get_peak_rss_mb() -> float:
    """Restituisce il picco di memoria RSS (Working Set) in MB."""
    # 1. Linux/macOS
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # Su Linux ru_maxrss è in KB; su macOS in byte
        if sys.platform == "darwin":
            return rusage.ru_maxrss / (1024 * 1024)
        return rusage.ru_maxrss / 1024.0
    except ImportError:
        pass

    # 2. Windows via psapi
    try:
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return float(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        pass

    return 0.0


def generate_synthetic_whatsapp_export(
    target_bytes: int,
    output_path: Path,
    message_text_len: int = 500,
) -> tuple[int, str]:
    """
    Genera un file TXT di export WhatsApp deterministico di dimensione ~target_bytes.
    Restituisce (record_count, sha256).
    """
    hasher = hashlib.sha256()
    record_count = 0
    written_bytes = 0

    base_payload = "Messaggio di prova forense con contenuto strutturato per verifica memoria e carichi. "
    multiplier = (message_text_len // len(base_payload)) + 1
    repeated_text = (base_payload * multiplier)[:message_text_len]

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        # Header WhatsApp standard
        header = "10/01/2024, 09:00 - I messaggi e le chiamate sono crittografati end-to-end.\n"
        f.write(header)
        hasher.update(header.encode("utf-8"))
        written_bytes += len(header.encode("utf-8"))
        record_count += 1

        actors = ["Alice", "Bob", "Charlie", "Diana"]
        chats = ["Chat-A", "Chat-B"]

        while written_bytes < target_bytes:
            record_count += 1
            actor = actors[record_count % len(actors)]
            chat_tag = chats[(record_count // 50) % len(chats)]
            minute = (record_count % 50) + 10
            hour = (record_count // 60) % 24

            line = (
                f"10/01/2024, {hour:02d}:{minute:02d} - {actor}: "
                f"[{chat_tag}] {repeated_text} (Seq: {record_count:08d})\n"
            )
            line_bytes = line.encode("utf-8")
            f.write(line)
            hasher.update(line_bytes)
            written_bytes += len(line_bytes)

    return record_count, hasher.hexdigest()


def run_benchmark_for_size(
    target_mb: int,
    output_dir: Path,
) -> dict[str, Any]:
    """Esegue il benchmark su una singola dimensione target."""
    target_bytes = target_mb * 1024 * 1024
    file_name = f"whatsapp_bench_{target_mb}MB.txt"
    file_path = output_dir / file_name

    print(f"\n[BENCHMARK] Generazione dataset sintetico: {target_mb} MB ({file_name})...")
    gen_start = time.perf_counter()
    expected_records, expected_hash = generate_synthetic_whatsapp_export(
        target_bytes=target_bytes,
        output_path=file_path,
        message_text_len=500,
    )
    actual_file_size = file_path.stat().st_size
    gen_time = time.perf_counter() - gen_start
    print(
        f"  Generato in {gen_time:.2f}s: {actual_file_size / (1024*1024):.2f} MB, "
        f"{expected_records} record, SHA256={expected_hash[:12]}..."
    )

    # Misura memoria iniziale e avvia ingestion
    start_rss = get_peak_rss_mb()
    bench_start = time.perf_counter()

    with open(file_path, "rb") as f_in:
        ingest_res = ingest_file_payload(
            file_bytes_or_io=f_in,
            original_filename=file_name,
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )

    exec_time = time.perf_counter() - bench_start
    peak_rss = get_peak_rss_mb()

    # Validazione integrità documentale
    summary = ingest_res.summary
    total_messages = summary.unified_message_count
    total_evidences = sum(len(d.all_evidence_sections) for d in ingest_res.documents.values())

    integrity_ok = (
        summary.sha256 == expected_hash
        and summary.raw_record_count == expected_records
        and len(ingest_res.documents) > 0
        and total_messages > 0
        and total_evidences >= total_messages
    )

    # Pulizia file temporaneo per non saturare disco
    try:
        file_path.unlink()
    except Exception:
        pass

    result = {
        "target_mb": target_mb,
        "input_bytes": actual_file_size,
        "record_count": expected_records,
        "peak_rss_mb": round(peak_rss, 2),
        "execution_time_sec": round(exec_time, 2),
        "message_count": total_messages,
        "evidence_count": total_evidences,
        "source_hash": expected_hash,
        "document_integrity": "VALID" if integrity_ok else "FAILED",
    }

    print(
        f"  Risultato {target_mb} MB: Tempo={exec_time:.2f}s | "
        f"Peak RSS={peak_rss:.2f} MB | Record={expected_records} | "
        f"Messaggi={total_messages} | Integrità={result['document_integrity']}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Large-File Ingestion ChatAnalysis")
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[10, 100, 250],
        help="Dimensioni in MB da verificare (es. 10 100 250)",
    )
    parser.add_argument(
        "--include-500",
        action="store_true",
        help="Include anche la verifica da 500 MB se le risorse hardware lo consentono",
    )
    args = parser.parse_args()

    sizes = list(args.sizes)
    if args.include_500 and 500 not in sizes:
        sizes.append(500)

    print("======================================================================")
    print("CHATANALYSIS — LARGE-FILE INGESTION BENCHMARK & BOUNDED MEMORY AUDIT")
    print("======================================================================")
    print(f"Dimensioni target pianificate: {sizes} MB")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="chatanalysis_bench_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for s in sorted(sizes):
            res = run_benchmark_for_size(s, tmp_path)
            results.append(res)

    print("\n======================================================================")
    print("TABELLA METRICHE OPERATIVE DI INGESTION (BLOCKER 3):")
    print("======================================================================")
    print(
        f"{'Target (MB)':<12} | {'Input Bytes':<12} | {'Record':<9} | "
        f"{'Peak RSS (MB)':<14} | {'Tempo (s)':<10} | {'Msg':<8} | "
        f"{'Evidence':<9} | {'Integrità':<10}"
    )
    print("-" * 95)
    for r in results:
        print(
            f"{r['target_mb']:<12} | {r['input_bytes']:<12} | {r['record_count']:<9} | "
            f"{r['peak_rss_mb']:<14} | {r['execution_time_sec']:<10} | {r['message_count']:<8} | "
            f"{r['evidence_count']:<9} | {r['document_integrity']:<10}"
        )
    print("======================================================================")

    # Salvataggio report json per packaging e audit
    report_file = PROJECT_ROOT / "large_file_benchmark_results.json"
    import json
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Report dettagliato salvato in: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
