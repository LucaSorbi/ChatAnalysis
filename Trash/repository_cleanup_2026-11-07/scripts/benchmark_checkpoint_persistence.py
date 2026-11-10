"""
scripts/benchmark_checkpoint_persistence.py
-------------------------------------------
Benchmark quantitativo riproducibile di persistenza dei checkpoint:
Confronto prestazionale e di scalabilità tra:
1. Legacy JSON (riscrittura integrale del file ad ogni chunk - O(N^2))
2. SQLite transazionale incrementale (scrittura del singolo blocco - O(1))

Esegue misurazioni su 100, 1.000 e 10.000 chunk sintetici,
registrando:
- Tempo cumulativo totale di scrittura (secondi)
- Throughput (chunk/sec)
- Picco di allocazione memoria (tracemalloc in MB)
- I/O cumulativo stimato (byte scritti)
- Tempo di caricamento e recupero dello stato finale (secondi)
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.models import (
    ConversationEvidenceDocument,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="test",
        source_path="/path/test",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=datetime.fromisoformat("2026-10-14T10:00:00+00:00"),
        naive_datetime=None,
        iso_string="2026-10-14T10:00:00Z",
        raw_value="2026-10-14T10:00:00Z",
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="test",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:test:{idx}",
        source_name="test",
        source_record_id=idx,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def create_synthetic_doc(num_bundles: int = 5) -> ConversationEvidenceDocument:
    bundles = [_create_bundle(str(i), f"Messaggio sintetico di test forense {i}") for i in range(num_bundles)]
    return ConversationEvidenceDocument(
        document_id="doc_bench_001",
        bundles=tuple(bundles),
    )


class LegacyJsonCheckpointStore:
    """Implementazione di riferimento del sistema precedente O(N^2)."""

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, doc_fp: str, cfg_fp: str) -> Path:
        return self.checkpoint_dir / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"

    def save_chunk(
        self,
        doc_id: str,
        doc_fp: str,
        cfg_fp: str,
        chunk_idx: int,
        result: TopicDetectionResult,
        total_chunks: int,
    ) -> int:
        p = self._get_path(doc_fp, cfg_fp)
        data: dict[str, Any] = {
            "document_id": doc_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "total_planned_chunks": total_chunks,
            "completed_chunk_indices": [],
            "serialized_results": {},
        }
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        if chunk_idx not in data["completed_chunk_indices"]:
            data["completed_chunk_indices"].append(chunk_idx)
            data["completed_chunk_indices"].sort()

        data["serialized_results"][str(chunk_idx)] = {
            "topic": {"topic_id": result.topic.topic_id, "label": result.topic.label},
            "decision": result.decision.value,
            "evidence_ids": list(result.evidence_ids),
            "rationale": result.rationale,
        }

        # Scrittura atomica dell'intero file
        tmp_p = p.with_suffix(".tmp")
        serialized_bytes = json.dumps(data, indent=2).encode("utf-8")
        with open(tmp_p, "wb") as f:
            f.write(serialized_bytes)
        tmp_p.replace(p)
        return len(serialized_bytes)

    def load_checkpoint(self, doc_fp: str, cfg_fp: str) -> dict[str, Any] | None:
        p = self._get_path(doc_fp, cfg_fp)
        if not p.is_file():
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)


def run_benchmark_for_scale(
    scale_n: int,
    t_json_1000: float | None = None,
    io_json_1000: float | None = None,
) -> dict[str, Any]:
    print(f"\n=======================================================")
    print(f" Avvio Benchmark Persistenza Checkpoint: N = {scale_n:,} chunk")
    print(f"=======================================================")

    doc = create_synthetic_doc(5)
    doc_fp = compute_document_fingerprint(doc)
    cfg_fp = compute_run_config_hash("test_model", "v1")

    valid_ev = doc.all_evidence_sections[0].evidence_id if doc.all_evidence_sections else "ev_0"
    dummy_topic = TopicQuery(topic_id="t1", label="Test", description="Test topic description")
    dummy_result = TopicDetectionResult(
        topic=dummy_topic,
        decision=TopicDecision.PRESENT,
        evidence_ids=(valid_ev,),
        rationale="Evidence validated in synthetic benchmark chunk",
        provenance_document_id=doc.document_id,
        metadata={"bench_scale": scale_n},
    )

    results: dict[str, Any] = {
        "scale": scale_n,
        "environment": {
            "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "python_version": sys.version.split()[0],
            "benchmark_scope": "I/O persistenza checkpoint sintetici (non misura inferenza LLM su GTX TITAN X)",
        },
    }

    # 1. SQLite Store (misurazione diretta per tutte le scale)
    with tempfile.TemporaryDirectory() as sqlite_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(sqlite_dir))
        tracemalloc.start()
        t0 = time.perf_counter()

        for idx in range(scale_n):
            store.save_chunk(
                document_id=doc.document_id,
                document_fingerprint=doc_fp,
                config_fingerprint=cfg_fp,
                chunk_idx=idx,
                result=dummy_result,
                total_planned_chunks=scale_n,
                analysis_type="detection",
                model_id="test_model",
                prompt_version="v1",
            )

        t_sqlite = time.perf_counter() - t0
        _, peak_sqlite = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Load time
        t_load_0 = time.perf_counter()
        loaded_chk = store.load_checkpoint(doc_fp, cfg_fp)
        t_load_sqlite = time.perf_counter() - t_load_0

        db_file = Path(sqlite_dir) / "checkpoints.db"
        sqlite_bytes = db_file.stat().st_size if db_file.exists() else 0

        assert loaded_chk is not None
        assert len(loaded_chk.completed_chunk_indices) == scale_n

        results["sqlite"] = {
            "status": "MEASURED",
            "measured": True,
            "time_sec": round(t_sqlite, 4),
            "throughput_cps": round(scale_n / max(0.0001, t_sqlite), 1),
            "mem_peak_mb": round(peak_sqlite / (1024 * 1024), 2),
            "file_size_kb": round(sqlite_bytes / 1024, 1),
            "load_time_sec": round(t_load_sqlite, 4),
        }
        print(f" [SQLite O(1)]        Tempo MISURATO: {t_sqlite:.3f}s | Throughput: {results['sqlite']['throughput_cps']} chunk/s | Mem peak: {results['sqlite']['mem_peak_mb']} MB | DB: {results['sqlite']['file_size_kb']} KB")

    # 2. Legacy JSON Store
    # Per scale <= 1000 eseguiamo direttamente la misurazione.
    # Per scale > 1000 omettiamo la misurazione per I/O proibitivo e calcoliamo la proiezione analitica O(N^2).
    if scale_n <= 1000:
        with tempfile.TemporaryDirectory() as json_dir:
            legacy_store = LegacyJsonCheckpointStore(checkpoint_dir=Path(json_dir))
            tracemalloc.start()
            t0 = time.perf_counter()
            total_bytes_written = 0

            for idx in range(scale_n):
                bw = legacy_store.save_chunk(
                    doc_id=doc.document_id,
                    doc_fp=doc_fp,
                    cfg_fp=cfg_fp,
                    chunk_idx=idx,
                    result=dummy_result,
                    total_chunks=scale_n,
                )
                total_bytes_written += bw

            t_json = time.perf_counter() - t0
            _, peak_json = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            t_load_0 = time.perf_counter()
            loaded_json = legacy_store.load_checkpoint(doc_fp, cfg_fp)
            t_load_json = time.perf_counter() - t_load_0

            assert loaded_json is not None
            assert len(loaded_json["completed_chunk_indices"]) == scale_n

            results["legacy_json"] = {
                "status": "MEASURED",
                "measured": True,
                "time_sec": round(t_json, 4),
                "throughput_cps": round(scale_n / max(0.0001, t_json), 1),
                "mem_peak_mb": round(peak_json / (1024 * 1024), 2),
                "io_written_mb": round(total_bytes_written / (1024 * 1024), 2),
                "load_time_sec": round(t_load_json, 4),
                "speedup_sqlite": round(t_json / max(0.0001, t_sqlite), 1),
            }
            print(f" [Legacy JSON O(N^2)]   Tempo MISURATO: {t_json:.3f}s | Throughput: {results['legacy_json']['throughput_cps']} chunk/s | Mem peak: {results['legacy_json']['mem_peak_mb']} MB | I/O riscritto: {results['legacy_json']['io_written_mb']} MB")
            print(f" ==> SPEEDUP MISURATO SQLite vs JSON: {results['legacy_json']['speedup_sqlite']}x")
    else:
        # Per 10.000 chunk, la complessità O(N^2) del legacy JSON riscriverebbe il file 10.000 volte
        # con I/O cumulativo previsto > 13 GB e tempi stimati > 10.700 secondi (~3 ore).
        # Pertanto il valore NON viene misurato né spacciato per misura reale.
        t_base = t_json_1000 if t_json_1000 is not None else 107.6603
        io_base = io_json_1000 if io_json_1000 is not None else 131.84
        quad_factor = (scale_n / 1000.0) ** 2  # 100.0x per 10.000 rispetto a 1.000
        proj_time_sec = round(t_base * quad_factor, 1)
        proj_io_mb = round(io_base * quad_factor, 1)
        proj_cps = round(scale_n / max(0.0001, proj_time_sec), 2)
        proj_speedup = round(proj_time_sec / max(0.0001, t_sqlite), 1)

        results["legacy_json"] = {
            "status": "NOT_MEASURED_PROHIBITIVE_IO",
            "measured": False,
            "time_sec": None,
            "throughput_cps": None,
            "mem_peak_mb": None,
            "io_written_mb": None,
            "load_time_sec": None,
            "speedup_sqlite": None,
            "mathematical_projection": {
                "projected_time_sec": proj_time_sec,
                "projected_io_written_mb": proj_io_mb,
                "projected_throughput_cps": proj_cps,
                "projected_speedup_sqlite_vs_legacy": proj_speedup,
                "scaling_formula": f"T({scale_n:,}) = T(1000) * ({scale_n}/1000)^2 = {t_base:.2f}s * {quad_factor:.0f} = {proj_time_sec:,.1f}s",
                "baseline_1000_measured_time_sec": t_base,
                "baseline_1000_measured_io_mb": io_base,
            },
            "note": (
                "NON MISURATO: l'esecuzione effettiva comporterebbe la riscrittura integrale del file "
                f"{scale_n:,} volte per ~{proj_io_mb:,.1f} MB (~{proj_io_mb/1024:.2f} GB) di I/O disco cumulativo "
                f"e oltre {proj_time_sec:,.0f} secondi (~{proj_time_sec/3600:.1f} ore). "
                "Riportato formalmente come non misurato per rigore sperimentale, con proiezione O(N^2) coerente."
            ),
        }
        print(f" [Legacy JSON O(N^2)]   NON MISURATO (O(N^2) proibitivo) | Proiezione analitica O(N^2): ~{proj_time_sec:,.1f}s (~{proj_time_sec/3600:.1f}h) | I/O stimato: ~{proj_io_mb:,.1f} MB | Speedup teorico: ~{proj_speedup}x")

    return results


def main() -> None:
    scales = [100, 1000, 10000]
    all_results = []
    t_json_1000 = None
    io_json_1000 = None

    for s in scales:
        res = run_benchmark_for_scale(s, t_json_1000=t_json_1000, io_json_1000=io_json_1000)
        if s == 1000 and "legacy_json" in res and res["legacy_json"].get("measured"):
            t_json_1000 = res["legacy_json"]["time_sec"]
            io_json_1000 = res["legacy_json"]["io_written_mb"]
        all_results.append(res)

    out_file = Path("benchmark_checkpoint_persistence_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nRisultati esportati con successo in {out_file}")


if __name__ == "__main__":
    main()
