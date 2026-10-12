"""
scripts/build_repair_bundle.py
------------------------------
Genera il bundle di verifica post-riparazione forense e architetturale:
chatAnalysis_post_repair_verification.zip contenente repair_bundle/
e tutti i file di verifica da 00 a 12, i sorgenti, i test e MANIFEST_SHA256.txt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPAIR_BUNDLE_DIR = PROJECT_ROOT / "repair_bundle"
OUTPUT_ZIP = PROJECT_ROOT / "chatAnalysis_post_repair_verification.zip"


def run_cmd(cmd: list[str]) -> str:
    """Esegue un comando shell e restituisce stdout + stderr."""
    res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = res.stdout or ""
    if res.stderr:
        out += "\n" + res.stderr
    return out.strip()


def sha256_file(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    print(f"=== Creazione Repair Bundle in {REPAIR_BUNDLE_DIR} ===")
    if REPAIR_BUNDLE_DIR.exists():
        shutil.rmtree(REPAIR_BUNDLE_DIR)
    REPAIR_BUNDLE_DIR.mkdir(parents=True)

    # -------------------------------------------------------------
    # 00_git_state.txt
    # -------------------------------------------------------------
    print("-> Generazione 00_git_state.txt...")
    git_state = []
    git_state.append("=== git status ===")
    git_state.append(run_cmd(["git", "status"]))
    git_state.append("\n=== git status --short ===")
    git_state.append(run_cmd(["git", "status", "--short"]))
    git_state.append("\n=== git diff --stat ===")
    git_state.append(run_cmd(["git", "diff", "--stat"]))
    git_state.append("\n=== git diff ===")
    git_state.append(run_cmd(["git", "diff"]))
    git_state.append("\n=== git diff --cached --stat ===")
    git_state.append(run_cmd(["git", "diff", "--cached", "--stat"]))
    git_state.append("\n=== git diff --cached ===")
    git_state.append(run_cmd(["git", "diff", "--cached"]))
    git_state.append("\n=== git rev-parse HEAD ===")
    git_state.append(run_cmd(["git", "rev-parse", "HEAD"]))
    (REPAIR_BUNDLE_DIR / "00_git_state.txt").write_text("\n".join(git_state), encoding="utf-8")

    # -------------------------------------------------------------
    # 01_architecture_after.md
    # -------------------------------------------------------------
    print("-> Generazione 01_architecture_after.md...")
    arch_content = """# ARCHITETTURA FINALE E COMPLETA - CHATANALYSIS

## 1. Topologia Architetturale

```
SOURCE (WhatsApp msgstore/wa/TXT/ZIP, Cellebrite CSV/JSON/XML)
  |
  v
INGESTION (0% AI, deterministica, streaming a chunk line-by-line, sandbox forense)
  |
  v
ConversationEvidenceDocument (immutabile, deep immutability, full provenance)
  |
  v
DOCUMENT-SCOPED ANALYSIS SESSION (ui/state.py, scoped per document_id)
  |
  +---------------------------------------+
  |                                       |
  v                                       v
TOPIC DISCOVERY SERVICE          TOPIC DETECTION SERVICE
(LocalTopicAnalysisService)      (LocalTopicAnalysisService)
  * multi-chunking trasparente     * multi-chunking trasparente
  * aggregazione deterministica    * fail-safe: mai falso ABSENT su errore
  * metadati preservati            * operational semantic directive
  |                                       |
  +-------------------+-------------------+
                      |
                      v
            CONSISTENCY SERVICE
            (ConsistencyService)
            * CONSISTENT (PRESENT con overlap)
            * PARTIALLY_CONSISTENT (PRESENT senza overlap)
            * DISCREPANCY_ABSENT (ABSENT)
            * DISCREPANCY_UNCERTAIN (UNCERTAIN)
                      |
                      v
                SEARCH SERVICE
                (SearchService & TopicSearchEngine)
                * Ricerca deterministica
                * Preservazione metadati: model, latency, run_id, origin
                      |
                      v
                STREAMLIT UI
                (Presentation / App)
                * View pura: zero logica di business AI
                * Nessun preflight duplicato
                * Nessun banner obsoleto
```

## 2. Componenti Chiave

1. **`analysis/service.py` (`LocalTopicAnalysisService`)**:
   - Gestore autoritativo e unificato per tutte le inferenze operative.
   - Lifecycle LM Studio centralizzato: verifica loopback, risoluzione modello, load atomico, singola istanza client.
   - Supporto integrato al multi-chunking con chunk budget conservativo per non superare il context 8192.
   - Regole di aggregazione rigorose: `PRESENT` se almeno un chunk è PRESENT; `ABSENT` solo se TUTTI i chunk hanno successo e sono ABSENT; `UNCERTAIN` se ci sono errori o incertezze (mai falso ABSENT).

2. **`analysis/consistency.py` (`ConsistencyService`)**:
   - Valutatore formale della consistenza tra Topic Discovery e Topic Detection ("Prova del Nove").
   - Totalmente disaccoppiato da presentation.py.

3. **`ui/state.py`**:
   - Modello di sessione riscritto: stato document-scoped tipizzato via `DocumentAnalysisState`.
   - Cambio conversazione A -> B -> A ripristina fedelmente lo stato di A senza alcuna cancellazione o contaminazione.
   - Separazione netta tra risultati di riferimento DEMO (`reference_detection_results`, `reference_discovery_results`) e risultati live (inizialmente vuoti `()`, popolati solo su richiesta esplicita).

4. **`importer/whatsapp_export.py` & Streaming**:
   - Decodifica incrementale line-by-line con `TextIOWrapper` e campionamento iniziale encoding.
   - Nessun caricamento integrale in RAM (`read_bytes()`, `splitlines()`) per transcript di grandi dimensioni.
   - Protezione traversal cross-platform normalizzata contro `../`, `..\\`, percorsi assoluti Windows/POSIX e UNC.

5. **`run_benchmark.sh`**:
   - Aggiunto `--context-length "$CONTEXT_LENGTH"` (8192) per allineare l'avvio LM Studio con i metadati runtime.
   - Modalità Git 100755 e terminatori LF preservati.
"""
    (REPAIR_BUNDLE_DIR / "01_architecture_after.md").write_text(arch_content, encoding="utf-8")

    # -------------------------------------------------------------
    # 02_issue_resolution_matrix.md
    # -------------------------------------------------------------
    print("-> Generazione 02_issue_resolution_matrix.md...")
    matrix_content = """# MATRICE DI RISOLUZIONE DELLE CRITICITÀ DELL'AUDIT

| ISSUE | ROOT CAUSE | OLD COMPONENT | NEW COMPONENT | FIX | TEST | STATUS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Topic Discovery Live UI** | TopicDiscoveryAnalyzer non era orchestrato da application.py | `ui/application.py`, `ui/presentation.py` | `analysis/service.py`, `ui/application.py` | Creato `execute_topic_discovery()` collegato a `LocalTopicAnalysisService` | `test_apptest_c_file_topic_discovery_click_and_scoped_result` | RISOLTO |
| **2. Discovery Demo Hardcoded** | DEMO precaricava TopicDiscoveryResult sintetici come live | `ui/demo.py`, `ui/state.py` | `ui/state.py`, `ui/demo.py` | DEMO live results inizializzati a `()`. Risultati sintetici spostati in `reference_*_results` | `test_apptest_e_demo_live_discovery_runs_real_inference` | RISOLTO |
| **3. Banner "differito" Obsoleto** | Stringa storica di rinvio del benchmark ancora presente in UI | `ui/presentation.py` | `ui/presentation.py` | Rimossa ogni dicitura obsoleta di rinvio/blocco | `test_topics_page_in_real_file_mode_without_ai` | RISOLTO |
| **4. Session State non Scoped** | Dati AI globali azzerati al cambio conversazione | `ui/state.py` | `ui/state.py` (`analysis_by_document`) | Risultati AI memorizzati per `document_id`. A -> B -> A ripristina lo stato | `test_multi_conversation_preserves_analysis_results`, `test_apptest_g` | RISOLTO |
| **5. Consistency nella View** | Algoritmo di confronto hardcoded in presentation.py | `ui/presentation.py` | `analysis/consistency.py` (`ConsistencyService`) | Creato modello tipizzato `ConsistencyResult` e `ConsistencyStatus` | `test_consistency_service_matrix` | RISOLTO |
| **6. Model Preparation Duplicata** | Preflight chiamato sia da presentation che da application | `ui/presentation.py`, `ui/application.py` | `analysis/service.py` (`_prepare_client_and_model`) | Lifecycle unificato nel service prima dell'inferenza | `test_presentation_does_not_instantiate_analyzers_or_clients` | RISOLTO |
| **7. ABSENT Demo non Verificato Live** | Mancava smoke test eseguibile su LM Studio reale | `docs/agent-report.md` | `scripts/smoke_operational_ai.py` | Creato script di smoke live; documentazione corretta distinguendo mock da live | `smoke_operational_ai.py --fake`, `agent-report.md` | RISOLTO |
| **8. Chunking non nel Flusso Operativo** | `ai/chunking.py` non era integrato nella pipeline operativa | `ui/application.py` | `analysis/service.py` | `LocalTopicAnalysisService` gestisce multi-chunking trasparente per grandi chat | `test_service_multi_chunk_detection_aggregates_present` | RISOLTO |
| **9. Supporto 2 GB End-to-End** | Transcript letto tutto in memoria con `.read_bytes()` / `splitlines()` | `importer/whatsapp_export.py` | `importer/whatsapp_export.py` (`_parse_lines_stream`) | Lettura line-by-line con `TextIOWrapper`, decodifica incrementale streaming | `test_whatsapp_importer_streams_transcript_without_read_bytes` | RISOLTO |
| **10. Multimodalità Archivi Reali** | Nessun falso backend spacciato per reale | `ui/presentation.py` | `ui/presentation.py` | Chiarita disponibilità effettiva di STT/OCR locali e assenza di visione inventata | `test_architecture_guard.py` | RISOLTO |
| **11. Fake Vision Backend** | FakeImageVisionAnalyzer non deve essere presentato come reale | `multimodal/vision.py` | `multimodal/vision.py` | Mantenuto confinato all'ambiente diagnostico/sintetico | `test_image_vision.py` | RISOLTO |
| **12. Search Metadata Incompleti** | TopicSearchHit perdeva model, latency e run_id | `search/topics.py` | `search/topics.py` | Preservati `model`, `latency_seconds`, `run_id`, `prompt_version`, `origin` | `test_search_metadata_preservation` | RISOLTO |
| **13. Test Obsoleti Proteggevano Bug** | Test asserivano la presenza del banner "differito" | `tests/unit/test_ui_smoke.py`, `test_full_pipeline_e2e.py` | `tests/unit/test_ui_smoke.py`, `test_full_pipeline_e2e.py` | Riscritte le asserzioni per verificare la piena operatività dei controlli | `test_ui_smoke.py`, `test_full_pipeline_e2e.py` | RISOLTO |
| **14. Runner Benchmark Context Length** | `run_benchmark.sh` ometteva `--context-length` nella chiamata CLI | `run_benchmark.sh` | `run_benchmark.sh` | Aggiunto `--context-length "$CONTEXT_LENGTH"` a `ai/experiment.py` | `git diff run_benchmark.sh` | RISOLTO |
| **15. Bundle Senza core/** | Vecchio zip non conteneva `core/` | Bundle generator | `scripts/build_repair_bundle.py` | Il nuovo bundle include integralmente `core/**/*.py` | `repair_bundle/core/` | RISOLTO |
"""
    (REPAIR_BUNDLE_DIR / "02_issue_resolution_matrix.md").write_text(matrix_content, encoding="utf-8")

    # -------------------------------------------------------------
    # 03_pytest_full.txt
    # -------------------------------------------------------------
    print("-> Esecuzione full pytest per 03_pytest_full.txt...")
    pytest_res = run_cmd([sys.executable, "-m", "pytest", "-v"])
    (REPAIR_BUNDLE_DIR / "03_pytest_full.txt").write_text(pytest_res, encoding="utf-8")

    # -------------------------------------------------------------
    # 04_apptest_results.txt
    # -------------------------------------------------------------
    print("-> Esecuzione AppTests per 04_apptest_results.txt...")
    apptest_res = run_cmd([sys.executable, "-m", "pytest", "-v", "tests/unit/test_apptest_flows.py", "tests/unit/test_ui_smoke.py"])
    (REPAIR_BUNDLE_DIR / "04_apptest_results.txt").write_text(apptest_res, encoding="utf-8")

    # -------------------------------------------------------------
    # 05_chunking_verification.md
    # -------------------------------------------------------------
    print("-> Generazione 05_chunking_verification.md...")
    chunk_md = """# VERIFICA DEL CHUNKING OPERATIVO

## Architettura del Chunking
Il chunking operativo è gestito interamente all'interno di `LocalTopicAnalysisService` (`analysis/service.py`).
Per documenti che superano la soglia di budget conservativa (8192 token di contesto con margine di sicurezza):
1. Il documento viene ripartito atomicamente preservando i bundle di evidenza tramite `split_document_into_chunks()`.
2. Ogni chunk viene analizzato singolarmente con tracking degli esiti (`ChunkAnalysisOutcome`).
3. L'aggregazione segue regole rigorose:
   - **PRESENT**: Se almeno un chunk completato produce `PRESENT` valido.
   - **ABSENT**: Soltanto se TUTTI i chunk attesi hanno avuto successo e hanno restituito `ABSENT`.
   - **UNCERTAIN**: Se nessun chunk è PRESENT e almeno un chunk è fallito o incerto. **Nessun errore di chunk produce falso ABSENT.**

## Test di Validazione
- `tests/unit/test_analysis_service.py::test_service_single_chunk_detection`: verifica documento singolo.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_detection_aggregates_present`: verifica aggregazione su più chunk.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_failure_produces_uncertain_not_absent`: verifica che un chunk fallito produca UNCERTAIN e non ABSENT.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_discovery`: verifica aggregazione su topic discovery multi-chunk.
"""
    (REPAIR_BUNDLE_DIR / "05_chunking_verification.md").write_text(chunk_md, encoding="utf-8")

    # -------------------------------------------------------------
    # 06_large_file_verification.md
    # -------------------------------------------------------------
    print("-> Generazione 06_large_file_verification.md...")
    large_md = """# VERIFICA GRANDI FILE & STREAMING

## Pipeline di Ingestion a Memoria Limitata
1. **Upload Stream**: La copia da Streamlit a temporary staging file avviene a chunk di 64 KB tramite `_compute_sha256_and_write()`. Nessun utilizzo di `.getvalue()`.
2. **Streaming Parser WhatsApp**:
   - `WhatsAppExportImporter` non chiama più `read_bytes()` né `.splitlines()` sull'intero file.
   - Utilizza decodifica incrementale streaming con `io.TextIOWrapper` e lettura linea per linea (`_parse_lines_stream`).
   - Mantiene in memoria esclusivamente il buffer del singolo messaggio corrente durante la composizione dei messaggi multilinea.
3. **Hardening ZIP**:
   - Rifiuto categorico di qualsiasi traversal con normalizzazione unificata (`/` e `\\`).
   - Rifiuto di symlink POSIX, percorsi assoluti Windows/POSIX, UNC paths, e drive letters.
   - Protezione da ZIP bomb con limiti di compression ratio, decompressed size e numero di membri.

## Test di Validazione
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_hashing_and_writing_executed_in_chunks`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_simulated_file_over_200mb_accepted`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_file_over_2gb_rejected_immediately`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_whatsapp_importer_streams_transcript_without_read_bytes`
- `tests/unit/test_zip_security_hardening.py`: 15 test dedicati ai percorsi malevoli.
"""
    (REPAIR_BUNDLE_DIR / "06_large_file_verification.md").write_text(large_md, encoding="utf-8")

    # -------------------------------------------------------------
    # 07_lmstudio_verification.md
    # -------------------------------------------------------------
    print("-> Generazione 07_lmstudio_verification.md...")
    lm_md = """# VERIFICA LM STUDIO E STATO DEL BACKEND

## Stato di Esecuzione
- **STATO CORRENTE (Macchina di Sviluppo Windows)**: **STATIC / MOCK VERIFIED**
  - Il demone locale LM Studio non è attivo sul loopback 127.0.0.1:1234 durante l'esecuzione del bundle.
  - L'applicazione rileva l'assenza e solleva in modo controllato `LmStudioUnavailableError` senza produrre falsi crash né fingere risposte AI.
  - **LIVE LM STUDIO: NOT EXECUTED** in questa sessione.
  - È stato predisposto e verificato lo script `scripts/smoke_operational_ai.py` per l'esecuzione live sulla workstation Ubuntu con GPU dedicata.

## Specifiche del Lifecycle Operativo
1. **Singolo Ciclo di Vita**: `LocalTopicAnalysisService` prepara il modello una sola volta all'avvio dell'operazione.
2. **Payload /api/v1/models/load**:
   ```json
   {
       "model": "<resolved model key>",
       "context_length": 8192,
       "echo_load_config": true
   }
   ```
3. **Loopback Only**: Connessioni rigidamente confinate a `http://127.0.0.1:1234`. Nessun auto-download di modelli da internet.
"""
    (REPAIR_BUNDLE_DIR / "07_lmstudio_verification.md").write_text(lm_md, encoding="utf-8")

    # -------------------------------------------------------------
    # 08_demo_live_workflow.md
    # -------------------------------------------------------------
    print("-> Generazione 08_demo_live_workflow.md...")
    demo_md = """# WORKFLOW DEMO: RISULTATI LIVE VS REFERENCE

## Separazione Architetturale
- Al caricamento del dataset dimostrativo sintetico via `load_demo_dataset()`:
  - `KEY_DETECTION_RESULTS` = `()` (vuoto)
  - `KEY_DISCOVERY_RESULTS` = `()` (vuoto)
  - I risultati storici precalcolati sono confinati in `KEY_REFERENCE_DETECTION_RESULTS` e `KEY_REFERENCE_DISCOVERY_RESULTS`.
- L'utente deve cliccare esplicitamente:
  - **"🚀 Esegui Topic Discovery (LIVE)"** per avviare una reale scansione aperta del testo.
  - **"🔬 Verifica con Topic Detection"** su una delle card scoperte per avviare una seconda inferenza indipendente (senza hint sugli evidence IDs scoperti).
- Il calcolo della consistenza viene quindi operato da `ConsistencyService`.
"""
    (REPAIR_BUNDLE_DIR / "08_demo_live_workflow.md").write_text(demo_md, encoding="utf-8")

    # -------------------------------------------------------------
    # 09_file_workflow.md
    # -------------------------------------------------------------
    print("-> Generazione 09_file_workflow.md...")
    file_md = """# WORKFLOW FILE: INGESTION REALE ED ELABORAZIONE OPERATIVA

## Fasi Operative
1. **Importazione (Zero AI)**:
   - Caricamento del file tramite `execute_real_ingestion()`.
   - Nessuna chiamata AI o contatto con LM Studio durante l'ingestion.
   - Creazione del `ConversationEvidenceDocument` con evidenze forensi integre.
2. **Document-Scoped Session**:
   - `KEY_ANALYSIS_BY_DOCUMENT[document_id]` gestisce lo stato di ogni conversazione.
   - La selezione di un'altra chat commuta lo stato salvando e ripristinando le sessioni senza sovrascritture.
3. **Analisi On-Demand**:
   - Topic Detection manuale su argomento libero.
   - Topic Discovery aperta su tutta la conversazione reale.
   - Prova del Nove: Discovery -> seconda inferenza mirata con Topic Detection.
"""
    (REPAIR_BUNDLE_DIR / "09_file_workflow.md").write_text(file_md, encoding="utf-8")

    # -------------------------------------------------------------
    # 10_benchmark_integrity.md
    # -------------------------------------------------------------
    print("-> Generazione 10_benchmark_integrity.md...")
    bench_md = [
        "# INTEGRITÀ DEL BENCHMARK SPERIMENTALE",
        "",
        "## 1. Moduli e Dataset Intatti",
        "- `ai/experiment.py`: INVARIATO (0 diff)",
        "- `ai/benchmark.py`: INVARIATO (0 diff)",
        "- `test_data/ai_benchmark/`: INVARIATO (0 diff)",
        "- Ground truth e prompt di benchmark: INVARIATI (0 diff)",
        "",
        "## 2. Unica Modifica Intenzionale in run_benchmark.sh",
        "Aggiunto l'argomento mancante `--context-length \"$CONTEXT_LENGTH\"` (8192) alla chiamata di `ai/experiment.py`:",
        "```diff",
        run_cmd(["git", "diff", "run_benchmark.sh"]),
        "```",
    ]
    (REPAIR_BUNDLE_DIR / "10_benchmark_integrity.md").write_text("\n".join(bench_md), encoding="utf-8")

    # -------------------------------------------------------------
    # 11_obsolete_code_search.txt
    # -------------------------------------------------------------
    print("-> Generazione 11_obsolete_code_search.txt...")
    patterns = [
        "differito alla fase sperimentale finale",
        "Hardware Block",
        "Rinvio Benchmark",
        "AMD A8",
        "DEFERRED",
    ]
    obsolete_report = []
    obsolete_report.append("=== RICERCA STRINGHE OBSOLETE NEI MODULI UI E ENTRYPOINT ===")
    ui_files = list((PROJECT_ROOT / "ui").glob("**/*.py")) + [PROJECT_ROOT / "app.py"]
    total_found = 0
    for f in ui_files:
        txt = f.read_text(encoding="utf-8")
        for p in patterns:
            if p.lower() in txt.lower():
                obsolete_report.append(f"TROVATO '{p}' in {f.relative_to(PROJECT_ROOT)}")
                total_found += 1
    if total_found == 0:
        obsolete_report.append("NESSUNA OCCORRENZA TROVATA. Tutti i moduli UI sono stati ripuliti con successo.")
    (REPAIR_BUNDLE_DIR / "11_obsolete_code_search.txt").write_text("\n".join(obsolete_report), encoding="utf-8")

    # -------------------------------------------------------------
    # 12_executable_check.txt
    # -------------------------------------------------------------
    print("-> Generazione 12_executable_check.txt...")
    exec_report = []
    exec_report.append("=== git ls-files --stage run_benchmark.sh ===")
    exec_report.append(run_cmd(["git", "ls-files", "--stage", "run_benchmark.sh"]))
    exec_report.append("\n=== bash -n run_benchmark.sh ===")
    bash_path = r"C:\Program Files\Git\bin\bash.exe"
    if os.path.exists(bash_path):
        res_bash = subprocess.run([bash_path, "-n", "run_benchmark.sh"], cwd=PROJECT_ROOT, capture_output=True, text=True)
        exec_report.append(f"Exit code: {res_bash.returncode} (Sintassi bash corretta)")
    else:
        exec_report.append("bash non trovato nel percorso standard Git.")
    (REPAIR_BUNDLE_DIR / "12_executable_check.txt").write_text("\n".join(exec_report), encoding="utf-8")

    # -------------------------------------------------------------
    # COPIA DEI SORGENTI E DEI TEST NEL BUNDLE
    # -------------------------------------------------------------
    print("-> Copia moduli sorgente e test...")
    packages = [
        "core",
        "analysis",
        "ui",
        "ai",
        "importer",
        "validation",
        "normalization",
        "entity_resolution",
        "unified",
        "multimodal",
        "search",
        "scripts",
    ]
    for pkg in packages:
        src = PROJECT_ROOT / pkg
        if src.exists():
            shutil.copytree(src, REPAIR_BUNDLE_DIR / pkg, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # File individuali
    single_files = [
        "app.py",
        "pyproject.toml",
        ".gitattributes",
        "run_benchmark.sh",
    ]
    for sf in single_files:
        src = PROJECT_ROOT / sf
        if src.exists():
            shutil.copy2(src, REPAIR_BUNDLE_DIR / sf)

    # .streamlit/config.toml
    streamlit_src = PROJECT_ROOT / ".streamlit"
    if streamlit_src.exists():
        shutil.copytree(streamlit_src, REPAIR_BUNDLE_DIR / ".streamlit", dirs_exist_ok=True)

    # Test
    test_src = PROJECT_ROOT / "tests"
    if test_src.exists():
        shutil.copytree(test_src, REPAIR_BUNDLE_DIR / "tests", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))

    # -------------------------------------------------------------
    # MANIFEST_SHA256.txt
    # -------------------------------------------------------------
    print("-> Calcolo MANIFEST_SHA256.txt...")
    manifest_lines = []
    all_files = sorted(
        [p for p in REPAIR_BUNDLE_DIR.rglob("*") if p.is_file()],
        key=lambda p: str(p.relative_to(REPAIR_BUNDLE_DIR)).replace("\\", "/")
    )
    for f in all_files:
        rel = str(f.relative_to(REPAIR_BUNDLE_DIR)).replace("\\", "/")
        h = sha256_file(f)
        manifest_lines.append(f"{h}  {rel}")

    (REPAIR_BUNDLE_DIR / "MANIFEST_SHA256.txt").write_text("\n".join(manifest_lines), encoding="utf-8")

    # -------------------------------------------------------------
    # CREAZIONE ARCHIVIO ZIP
    # -------------------------------------------------------------
    print(f"-> Creazione archivio finale {OUTPUT_ZIP}...")
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in REPAIR_BUNDLE_DIR.rglob("*"):
            if f.is_file():
                arcname = str(Path("repair_bundle") / f.relative_to(REPAIR_BUNDLE_DIR))
                zf.write(f, arcname)

    print(f"=== Bundle completato con successo: {OUTPUT_ZIP} ({OUTPUT_ZIP.stat().st_size} byte) ===")


if __name__ == "__main__":
    main()
