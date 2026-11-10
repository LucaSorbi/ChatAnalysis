"""
scripts/offline_scalability_benchmark.py
----------------------------------------
Benchmark offline riproducibile per la profilazione scientifica e forense della scalabilità
di ChatAnalysis senza alcuna invocazione di rete o inferenza LLM.

Conforme ai requisiti FASE 1:
- Dataset sintetici deterministici: 1.000, 5.000, 10.000, 50.000 messaggi.
- Misurazione disaggregata per fase:
  1. Tempo di importazione (WhatsApp TXT parsing).
  2. Tempo di validazione, normalizzazione e staging SQLite + Entity Resolution.
  3. Tempo di costruzione UnifiedMessages e ConversationEvidenceDocument.
  4. Tempo di fingerprint documentale (SHA-256 forense).
  5. Tempo di pianificazione e chunking deterministico.
  6. Tempo di serializzazione e mappatura alias (generate_evidence_alias_maps + serialize_document_for_llm).
  7. Tempo di consolidamento Topic Discovery (aggregate_topic_discovery).
  8. Tempo di salvataggio e recupero/validazione checkpoint SQLite.
- Misurazione del picco di memoria RAM (Working Set RSS via psapi su Windows / rusage su POSIX + tracemalloc).
- Tempo totale CPU (process_time) e tempo reale (perf_counter).
- Conteggio dei chunk e relative dimensioni (messaggi per chunk, caratteri, token stimati).
- Ripetizione configurabile delle misurazioni per abbattere il rumore sperimentale.
- Output JSON strutturato e report tabellare.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import gc
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Sequence

# Imposta il root del progetto in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.chunking import (
    aggregate_topic_discovery,
    split_document_into_chunks,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDiscoveryResult,
)
from ai.serializer import (
    generate_evidence_alias_maps,
    serialize_document_for_llm,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.models import OperationalAnalysisConfig, OperationalProfile
from entity_resolution.resolver import DeterministicEntityResolver
from importer.whatsapp_export import WhatsAppExportImporter
from normalization.normalizer import RecordNormalizer
from ui.staging import IngestionStagingStore
from unified.builder import UnifiedModelBuilder
from unified.context import UnifiedBuildContext
from validation.validator import RecordValidator


def get_peak_rss_mb() -> float:
    """Restituisce il picco di memoria RSS (Working Set) in MB."""
    # 1. POSIX
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
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


def generate_synthetic_chat_file(num_messages: int, output_path: Path) -> int:
    """
    Genera un file export WhatsApp deterministico con esattamente num_messages messaggi.
    """
    actors = ["Alice", "Bob", "Charlie", "Diana"]
    sample_texts = [
        "Concordato incontro operativo per la verifica documentale del caso.",
        "In allegato i dettagli relativi alle transazioni sospette del periodo estivo.",
        "Nessuna anomalia riscontrata nei registri contabili primari esaminati.",
        "Richiesta chiarimenti urgenti in merito alla gestione dei carichi sequestrati.",
        "Attendo conferma della ricezione degli atti forensi entro domani mattina.",
    ]

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("10/01/2024, 08:30 - I messaggi e le chiamate sono crittografati end-to-end.\n")
        for i in range(num_messages):
            actor = actors[i % len(actors)]
            text = sample_texts[i % len(sample_texts)]
            minute = i % 60
            hour = (i // 60) % 24
            day = 10 + ((i // (60 * 24)) % 20)
            f.write(f"{day:02d}/01/2024, {hour:02d}:{minute:02d} - {actor}: {text} (ID_{i:06d})\n")

    return num_messages


def benchmark_single_run(num_messages: int, temp_dir: Path) -> dict[str, Any]:
    """Esegue una singola misurazione completa per una specifica dimensione di messaggi."""
    gc.collect()
    tracemalloc.start()
    rss_start = get_peak_rss_mb()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()

    data_file = temp_dir / f"chat_synth_{num_messages}.txt"
    generate_synthetic_chat_file(num_messages, data_file)

    # 1. Importazione
    t0 = time.perf_counter()
    importer = WhatsAppExportImporter()
    raw_records = list(importer.import_records(data_file))
    import_time = time.perf_counter() - t0

    # 2. Validazione, Normalizzazione e Staging SQLite
    t1 = time.perf_counter()
    validator = RecordValidator()
    normalizer = RecordNormalizer()
    stg_path = temp_dir / f"staging_{num_messages}.sqlite"
    if stg_path.exists():
        stg_path.unlink()

    with IngestionStagingStore(stg_path) as staging:
        def validated_stream():
            for r in raw_records:
                yield validator.validate(r)

        norm_stream = normalizer.normalize_stream(validated_stream())
        stg_count = staging.stage_stream(norm_stream)

        # Entity Resolution
        resolver = DeterministicEntityResolver()
        resolution = resolver.resolve(staging)
        chat_titles = staging.extract_chat_titles()
        build_ctx = UnifiedBuildContext.create(resolution=resolution, chat_titles=chat_titles)
        builder = UnifiedModelBuilder(context=build_ctx)

        norm_staging_time = time.perf_counter() - t1

        # 3. Costruzione Documento (Unified Messages + Bundles + Document)
        t2 = time.perf_counter()
        ordered_keys = staging.get_distinct_chat_keys()
        primary_key = ordered_keys[0] if ordered_keys else "UNRESOLVED"
        chat_records = staging.iter_records_for_chat(primary_key)
        
        # Flusso di ingestion ottimizzato (streaming diretto in bundles, identico a ui/ingestion.py)
        from multimodal.evidence import MessageEvidenceBundle
        bundles: list[MessageEvidenceBundle] = []
        for m in builder.build_stream(chat_records):
            bundles.append(MessageEvidenceBundle(message=m))

        doc_id = f"doc::whatsapp::{num_messages}"
        document = ConversationEvidenceDocument(
            document_id=doc_id,
            bundles=tuple(bundles),
            chat_id=primary_key,
            source_name="whatsapp_export",
        )
        doc_build_time = time.perf_counter() - t2

    # 4. Fingerprint Documentale
    t3 = time.perf_counter()
    doc_fp = compute_document_fingerprint(document)
    fingerprint_time = time.perf_counter() - t3

    # 5. Pianificazione e Chunking
    t4 = time.perf_counter()
    # Profilo BALANCED standard (15k caratteri, 100 bundle, 4200 token budget)
    chunks = split_document_into_chunks(
        document,
        max_characters=15000,
        max_bundles=100,
        max_tokens=4200,
    )
    chunking_time = time.perf_counter() - t4
    chunk_count = len(chunks)
    chunk_sizes = [len(c.bundles) for c in chunks]

    # 6. Serializzazione e Alias Maps per tutti i chunk
    t5 = time.perf_counter()
    serialized_chunks_data: list[str] = []
    for c in chunks:
        id_to_alias, alias_to_id = generate_evidence_alias_maps(c)
        s_text = serialize_document_for_llm(c, evidence_id_map=id_to_alias)
        serialized_chunks_data.append(s_text)
    serializer_time = time.perf_counter() - t5

    # 7. Consolidamento Topic Discovery (simulazione realistica di risultati per ciascun chunk)
    t6 = time.perf_counter()
    simulated_chunk_results: list[TopicDiscoveryResult] = []
    for idx, c in enumerate(chunks):
        c_eids = [sec.evidence_id for sec in c.all_evidence_sections[:2]]
        topics = [
            DiscoveredTopic(
                label=f"Argomento Investigativo {idx % 5}",
                short_description="Dettaglio probatorio estratto dal contesto forense",
                evidence_ids=tuple(c_eids),
            )
        ]
        cr = TopicDiscoveryResult(
            topics=tuple(topics),
            provenance_document_id=c.document_id,
            metadata={"chunk_id": c.document_id},
        )
        simulated_chunk_results.append(cr)

    discovery_consolidated = aggregate_topic_discovery(
        chunk_results=simulated_chunk_results,
        parent_document_id=document.document_id,
        max_topics=10,
    )
    consolidation_time = time.perf_counter() - t6

    # 8. Checkpoint Store: Salvataggio e Validazione Recupero
    t7 = time.perf_counter()
    chk_dir = temp_dir / f"chk_{num_messages}"
    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    eff_cfg = OperationalAnalysisConfig(
        model_spec="qwen2.5-7b-instruct",
        profile=OperationalProfile.BALANCED,
        max_chunk_bundles=100,
        chunk_character_budget=15000,
        max_chunk_tokens=4200,
    )
    cfg_fp = compute_run_config_hash(
        model_id="qwen2.5-7b-instruct",
        prompt_version="discovery_operational",
        config=eff_cfg,
        chunks=chunks,
    )

    # Simula salvataggio di tutti i chunk tranne l'ultimo (simulando ripresa da checkpoint)
    for c_idx, cr in enumerate(simulated_chunk_results):
        chk_store.save_chunk(
            document_id=document.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=c_idx,
            result=cr,
            total_planned_chunks=chunk_count,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
        )

    # Validazione e recupero da checkpoint
    loaded_chk = chk_store.load_and_validate_checkpoint(
        document=document,
        config_fingerprint=cfg_fp,
        chunks=chunks,
        expected_analysis_type="discovery",
    )
    checkpoint_time = time.perf_counter() - t7
    assert loaded_chk is not None, "Il checkpoint salvato deve essere validato con successo"

    total_wall_time = time.perf_counter() - wall_start
    total_cpu_time = time.process_time() - cpu_start
    current_traced, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss_after = get_peak_rss_mb()
    gc.collect()

    # Pulizia file temporanei
    try:
        data_file.unlink(missing_ok=True)
        stg_path.unlink(missing_ok=True)
        shutil.rmtree(chk_dir, ignore_errors=True)
    except Exception:
        pass

    return {
        "num_messages": num_messages,
        "import_time": import_time,
        "norm_staging_time": norm_staging_time,
        "doc_build_time": doc_build_time,
        "fingerprint_time": fingerprint_time,
        "chunking_time": chunking_time,
        "serializer_time": serializer_time,
        "consolidation_time": consolidation_time,
        "checkpoint_time": checkpoint_time,
        "total_wall_time": total_wall_time,
        "total_cpu_time": total_cpu_time,
        "peak_rss_mb": peak_rss_after,
        "traced_peak_mb": peak_traced / (1024.0 * 1024.0),
        "chunk_count": chunk_count,
        "min_chunk_bundles": min(chunk_sizes) if chunk_sizes else 0,
        "max_chunk_bundles": max(chunk_sizes) if chunk_sizes else 0,
        "avg_chunk_bundles": (sum(chunk_sizes) / len(chunk_sizes)) if chunk_sizes else 0,
        "evidence_count": document.evidence_count,
    }


def run_benchmark_suite(
    sizes: Sequence[int] = (1000, 5000, 10000, 50000),
    runs: int = 3,
    output_json_path: Path | None = None,
) -> dict[str, Any]:
    """Esegue la suite di benchmark su dimensioni multiple con ripetizione sperimentale."""
    results_by_size: dict[str, Any] = {}

    print("=" * 80)
    print("CHATANALYSIS — OFFLINE SCALABILITY & PROFILING BENCHMARK")
    print("=" * 80)
    print(f"Dimensioni target: {list(sizes)}")
    print(f"Ripetizioni per dimensione: {runs}")
    print(f"Piattaforma: {sys.platform} | Python: {sys.version.split()[0]}")
    print("-" * 80)

    with tempfile.TemporaryDirectory(prefix="scalability_bench_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for size in sizes:
            print(f"\n>>> Avvio benchmark per {size:,} messaggi ({runs} esecuzioni)...")
            runs_data: list[dict[str, Any]] = []

            for r in range(1, runs + 1):
                print(f"  [Run {r}/{runs}] Elaborazione in corso...", end="", flush=True)
                run_res = benchmark_single_run(size, tmp_dir)
                runs_data.append(run_res)
                print(
                    f" Completato in {run_res['total_wall_time']:.2f}s "
                    f"(CPU: {run_res['total_cpu_time']:.2f}s, "
                    f"RAM Peak: {run_res['peak_rss_mb']:.1f} MB, "
                    f"Heap Peak: {run_res['traced_peak_mb']:.1f} MB, "
                    f"Chunk: {run_res['chunk_count']})"
                )

            # Calcolo medie sui run
            def _avg(key: str) -> float:
                return sum(d[key] for d in runs_data) / len(runs_data)

            aggregated = {
                "num_messages": size,
                "runs_count": runs,
                "import_time_s": round(_avg("import_time"), 4),
                "norm_staging_time_s": round(_avg("norm_staging_time"), 4),
                "doc_build_time_s": round(_avg("doc_build_time"), 4),
                "fingerprint_time_s": round(_avg("fingerprint_time"), 4),
                "chunking_time_s": round(_avg("chunking_time"), 4),
                "serializer_time_s": round(_avg("serializer_time"), 4),
                "consolidation_time_s": round(_avg("consolidation_time"), 4),
                "checkpoint_time_s": round(_avg("checkpoint_time"), 4),
                "total_cpu_time_s": round(_avg("total_cpu_time"), 4),
                "total_wall_time_s": round(_avg("total_wall_time"), 4),
                "peak_rss_mb": round(_avg("peak_rss_mb"), 2),
                "traced_peak_mb": round(_avg("traced_peak_mb"), 2),
                "chunk_count": runs_data[0]["chunk_count"],
                "min_chunk_bundles": runs_data[0]["min_chunk_bundles"],
                "max_chunk_bundles": runs_data[0]["max_chunk_bundles"],
                "evidence_count": runs_data[0]["evidence_count"],
                "raw_runs": runs_data,
            }
            results_by_size[str(size)] = aggregated

    print("\n" + "=" * 80)
    print("RIEPILOGO BENCHMARK OFFLINE — TEMPI (s) E MEMORIA (MB)")
    print("=" * 80)
    header = (
        f"{'Messaggi':>9} | {'Import(s)':>9} | {'Staging(s)':>10} | {'DocBld(s)':>9} | "
        f"{'Fp(s)':>7} | {'Chunk(s)':>8} | {'Consol(s)':>9} | {'TotalCPU':>9} | "
        f"{'RSS(MB)':>8} | {'Heap(MB)':>8} | {'Chunk#':>6}"
    )
    print(header)
    print("-" * len(header))
    for sz_str, d in results_by_size.items():
        print(
            f"{int(sz_str):>9,d} | {d['import_time_s']:>9.3f} | {d['norm_staging_time_s']:>10.3f} | "
            f"{d['doc_build_time_s']:>9.3f} | {d['fingerprint_time_s']:>7.3f} | {d['chunking_time_s']:>8.3f} | "
            f"{d['consolidation_time_s']:>9.3f} | {d['total_cpu_time_s']:>9.3f} | "
            f"{d['peak_rss_mb']:>8.1f} | {d['traced_peak_mb']:>8.1f} | {d['chunk_count']:>6d}"
        )
    print("=" * 80)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(results_by_size, f, indent=2)
        print(f"\n[REPORT] Risultati salvati in: {output_json_path}")

    return results_by_size


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChatAnalysis Offline Scalability Benchmark")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000, 10000, 50000],
                        help="Dimensioni in numero di messaggi da testare")
    parser.add_argument("--runs", type=int, default=3, help="Numero di ripetizioni per ciascuna dimensione")
    parser.add_argument("--out", type=str, default="baseline_scalability_measurements.json",
                        help="Percorso file JSON per salvare i risultati")
    args = parser.parse_args()

    out_p = Path(args.out)
    run_benchmark_suite(sizes=args.sizes, runs=args.runs, output_json_path=out_p)
