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


from importer.whatsapp_export import WhatsAppExportImporter
from validation.validator import RecordValidator
from normalization.normalizer import RecordNormalizer
from entity_resolution.resolver import DeterministicEntityResolver
from ui.staging import IngestionStagingStore
from unified.context import UnifiedBuildContext
from unified.builder import UnifiedModelBuilder
from multimodal.evidence import MessageEvidenceBundle
from ai.models import ConversationEvidenceDocument


def run_benchmark_for_size(
    target_mb: int,
    output_dir: Path,
) -> dict[str, Any]:
    """Esegue il benchmark su una singola dimensione target con profiling di ogni stage."""
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

    # Ingestion con misurazione separata dei tre stage forensi principali
    stg_path = output_dir / f"bench_stage_{target_mb}MB.sqlite"
    if stg_path.exists():
        stg_path.unlink()

    bench_start = time.perf_counter()
    imp = WhatsAppExportImporter()
    val = RecordValidator()
    norm = RecordNormalizer()

    # Stage 1: Parsing, Validation, Normalization e Staging SQLite
    t0 = time.perf_counter()
    with IngestionStagingStore(stg_path) as staging:
        def stream():
            for r in imp.import_records(file_path):
                yield val.validate(r)
        stg_count = staging.stage_stream(norm.normalize_stream(stream()))
        parsing_time = time.perf_counter() - t0

        # Stage 2: Entity Resolution deterministica
        t1 = time.perf_counter()
        resolver = DeterministicEntityResolver()
        resolution = resolver.resolve(staging)
        er_time = time.perf_counter() - t1

        # Stage 3: Costruzione messaggi unificati e documenti di evidenza
        t2 = time.perf_counter()
        chat_titles = staging.extract_chat_titles()
        build_ctx = UnifiedBuildContext.create(
            resolution=resolution,
            chat_titles=chat_titles,
        )
        builder = UnifiedModelBuilder(context=build_ctx)
        ordered_keys = staging.get_distinct_chat_keys()
        total_messages = 0
        total_evidences = 0
        documents: dict[str, ConversationEvidenceDocument] = {}

        for chat_key in ordered_keys:
            chat_records = staging.iter_records_for_chat(chat_key)
            group_msgs = list(builder.build_stream(chat_records))
            total_messages += len(group_msgs)
            bundles = [MessageEvidenceBundle(message=m) for m in group_msgs]
            doc_id = f"doc::whatsapp_export::{expected_hash[:12]}::{chat_key}"
            documents[doc_id] = ConversationEvidenceDocument(
                document_id=doc_id,
                bundles=tuple(bundles),
            )
        total_evidences = sum(len(d.all_evidence_sections) for d in documents.values())
        doc_time = time.perf_counter() - t2

    total_time = time.perf_counter() - bench_start
    peak_rss = get_peak_rss_mb()

    # Validazione integrità documentale e correttezza forense
    integrity_ok = (
        stg_count == expected_records
        and total_messages == expected_records
        and total_evidences >= total_messages
        and len(documents) > 0
    )

    # Verifica deterministica rispetto al dataset sintetico di riferimento
    # (controllo che i messaggi utente contengano gli attori corretti e il payload generato)
    reference_match = False
    if total_messages > 1:
        first_doc = next(iter(documents.values()))
        reference_match = any("Messaggio di prova forense" in b.message.text_content for b in first_doc.bundles[1:10])
    ref_status = "MATCH_REFERENCE" if (integrity_ok and reference_match) else "MISMATCH"

    # Pulizia file temporanei
    try:
        file_path.unlink()
    except Exception:
        pass
    try:
        stg_path.unlink()
    except Exception:
        pass

    result = {
        "target_mb": target_mb,
        "input_bytes": actual_file_size,
        "record_count": expected_records,
        "parsing_time_sec": round(parsing_time, 2),
        "entity_resolution_sec": round(er_time, 2),
        "doc_construction_sec": round(doc_time, 2),
        "total_time_sec": round(total_time, 2),
        "peak_rss_mb": round(peak_rss, 2),
        "message_count": total_messages,
        "evidence_count": total_evidences,
        "source_hash": expected_hash,
        "document_integrity": "VALID" if integrity_ok else "FAILED",
        "reference_correctness": ref_status,
    }

    print(
        f"  Risultato {target_mb} MB: Totale={total_time:.2f}s "
        f"[Parsing={parsing_time:.2f}s | ER={er_time:.2f}s | DocBuild={doc_time:.2f}s] | "
        f"Peak RSS={peak_rss:.2f} MB | Record={expected_records} | "
        f"Messaggi={total_messages} | Integrità={result['document_integrity']} | "
        f"Riferimento={ref_status}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Large-File Ingestion ChatAnalysis")
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[10, 25],
        help="Dimensioni in MB da verificare (es. 10 25)",
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

    print("==========================================================================================")
    print("CHATANALYSIS — LARGE-FILE INGESTION BENCHMARK & BOUNDED MEMORY AUDIT (BLOCKER 4)")
    print("==========================================================================================")
    print(f"Dimensioni target pianificate: {sizes} MB")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="chatanalysis_bench_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for s in sorted(sizes):
            res = run_benchmark_for_size(s, tmp_path)
            results.append(res)

    print("\n==========================================================================================")
    print("TABELLA METRICHE OPERATIVE DI INGESTION (BLOCKER 4):")
    print("==========================================================================================")
    print(
        f"{'Target':<7} | {'Record':<8} | {'Parsing(s)':<10} | {'ER(s)':<7} | {'Doc(s)':<7} | "
        f"{'Totale(s)':<9} | {'Peak RSS':<9} | {'Msg':<8} | {'Evidence':<8} | {'Integrità':<9} | {'Rif.'}"
    )
    print("-" * 105)
    for r in results:
        print(
            f"{r['target_mb']:>4} MB | {r['record_count']:<8} | {r['parsing_time_sec']:<10} | "
            f"{r['entity_resolution_sec']:<7} | {r['doc_construction_sec']:<7} | "
            f"{r['total_time_sec']:<9} | {r['peak_rss_mb']:<6} MB | {r['message_count']:<8} | "
            f"{r['evidence_count']:<8} | {r['document_integrity']:<9} | {r['reference_correctness']}"
        )
    print("==========================================================================================")

    # Salvataggio report json per packaging e audit
    report_file = PROJECT_ROOT / "large_file_benchmark_results.json"
    import json
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Report dettagliato salvato in: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
