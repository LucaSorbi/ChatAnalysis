# Builder storici di archivi di release (`scripts/create_*_archive.py` e affini)

Stato: **STORICI — non eseguire sul repository attuale.** Ogni script assembla lo ZIP di consegna di UNA specifica
release del passato con un elenco fisso di file (allowlist) e nomi di report in radice. Dopo il riordino del 30/10/2026
(PR #2) i report storici elencati sono in `Trash/` (vedi `Trash/MANIFEST.csv`, colonne `old_path` → `new_path`):
uno script che li cerca in radice li troverebbe assenti e li escluderebbe dall'archivio o fallirebbe.

Nessuno di questi script è stato modificato né eseguito (i risultati sperimentali non cambiano). Classificazione basata su:
nomi legati a una release, elenco fisso di file, nessun test che li esegua sul repository reale.

| Script | Stato |
|---|---|
| `create_closure_audit_archive.py` | storico |
| `create_discovery_zero_topics_release_archive.py` | storico |
| `create_engine_recovery_windows_archive.py` | storico |
| `create_final_seven_failures_closure_archive.py` | storico |
| `create_full_experimental_json_exports_archive.py` | storico |
| `create_microchunk_fix_release_archive.py` | storico |
| `create_real_discovery_validation_release_archive.py` | storico |
| `create_sanitized_final_zip.py` | storico |
| `create_scalability_audit_archive.py` | storico, ma le sue funzioni (`create_archive`, `should_pack_file`) sono testate su un repository FINTO in `tests/unit/test_packaging_release.py`: non spostare né rinominare |
| `create_stabilized_windows_live_final_archive.py` | storico |
| `create_truthfulness_deepseek_f1_fix_archive.py` | storico |
| `create_windows_live_json_schema_discovery_detection_fix_archive.py` | storico |
| `create_windows_three_pytest_failures_reconciled_archive.py` | storico |

## Ripristino (solo se serve rieseguire un builder)
Riportare i file in radice con `git mv` seguendo `Trash/MANIFEST.csv` (righe `status=MOVED`, da `new_path` a `old_path`), rieseguire,
poi rimetterli in `Trash/`. Lo SHA-256 originale di ogni file è nel manifest. Per una NUOVA release non usare questi script:
scrivere un builder nuovo con allowlist esplicita.
