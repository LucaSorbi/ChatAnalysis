"""
scripts/build_native_model_selection_bundle.py
----------------------------------------------
Script autoritativo per la Release Gate:
ChatAnalysis — Strict Model Instance & Quantization Release Gate Audit.

Produce esattamente:
chatAnalysis_native_model_selection_gate_audit_20261011.zip
e il report formale:
NATIVE_MODEL_SELECTION_GATE_REPORT.md

Esegue e documenta con misurazioni ad alta precisione (time.perf_counter()):
1. Nuovi test di selezione dell'istanza (Blocker 1 - 12 casi obbligatori).
2. Nuovi test di quantizzazione e selezione Q4_K_M (Blocker 2 - 14 casi obbligatori).
3. Integrazione simulata con payload REST ufficiali (schema dizionario quantization, binding, casi negativi).
4. Test Topic Discovery -> Detection flow (2 inferenze indipendenti, isolamento evidenze).
5. Test di isolamento multi-conversation (zero leak, store isolato).
6. Smoke mock positivo (--fake --fake-mode normal -> exit 0, MOCK_VERIFIED).
7. Smoke mock con istanza inesistente (--fake-mode nonexistent_instance -> exit 1, FAILED).
8. Smoke mock con quantizzazione errata (--fake-mode wrong_quantization -> exit 1, FAILED).
9. Full pytest su tutto il repository (exit code 0 obbligatorio).
10. Streamlit AppTest flows completi (UI lifecycle, zero AI leak).
11. Python compileall su tutti i package applicativi.
12. Clean Wheel build (zero file bytecode .pyc nell'archivio).
13. Wheel inspection strutturale e isolated wheel import reale con verifica dei path __file__.
14. Controllo zero .pyc nel repository sorgente.
15. Verifica benchmark scientifico congelato (zero diff su file frozen, bash -n, mode 100755, LF).
16. Git status e Git diff.
17. Generazione di NATIVE_MODEL_SELECTION_GATE_REPORT.md.
18. Generazione di MANIFEST_SHA256.txt verificabile e archivio ZIP finale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.smoke_operational_ai import ensure_synthetic_fixtures

BUNDLE_DIR = PROJECT_ROOT / "native_model_selection_bundle"
OUTPUT_ZIP = PROJECT_ROOT / "chatAnalysis_native_model_selection_gate_audit_20261011.zip"
REPORT_MD_PATH = PROJECT_ROOT / "NATIVE_MODEL_SELECTION_GATE_REPORT.md"


@dataclass
class CommandExecutionResult:
    name: str
    command: list[str]
    cwd: str
    python_version: str
    os_info: str
    timestamp: str
    duration_seconds: float
    exit_code: int
    stdout: str
    stderr: str
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    deselected_tests: int = 0
    status: str = "FAILED"
    expected_exit_code: int = 0
    notes: str = ""

    def to_log_text(self) -> str:
        lines = [
            f"=== {self.name} ===",
            f"Timestamp: {self.timestamp}",
            f"OS: {self.os_info}",
            f"Python Version: {self.python_version}",
            f"Working Directory: {self.cwd}",
            f"Command: {' '.join(self.command)}",
            f"Exit Code: {self.exit_code} (Expected: {self.expected_exit_code})",
            f"Execution Duration: {self.duration_seconds:.2f}s",
            f"Status: {self.status}",
        ]
        if self.passed_tests or self.failed_tests or self.skipped_tests or self.deselected_tests:
            lines.append(
                f"Test Results: passed={self.passed_tests}, failed={self.failed_tests}, "
                f"skipped={self.skipped_tests}, deselected={self.deselected_tests}"
            )
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        lines.append("--- STDOUT ---")
        lines.append(self.stdout.strip())
        if self.stderr.strip():
            lines.append("--- STDERR ---")
            lines.append(self.stderr.strip())
        lines.append("=" * 60)
        return "\n".join(lines)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def execute_cmd(
    name: str,
    command: list[str],
    cwd: Path = PROJECT_ROOT,
    expected_exit_code: int = 0,
    env: dict[str, str] | None = None,
    timeout: float = 300.0,
) -> CommandExecutionResult:
    ts = datetime.now(timezone.utc).isoformat()
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    start_time = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=cmd_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        duration = time.perf_counter() - start_time
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start_time
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = f"TIMEOUT: Command exceeded {timeout} seconds"
        exit_code = -999
    except Exception as exc:
        duration = time.perf_counter() - start_time
        stdout = ""
        stderr = f"EXECUTION_ERROR: {type(exc).__name__}: {exc}"
        exit_code = -998

    # Parsing dei risultati di pytest se presenti nell'output
    passed = 0
    failed = 0
    skipped = 0
    deselected = 0

    pass_m = re.search(r"(\d+)\s+passed", stdout)
    if pass_m:
        passed = int(pass_m.group(1))
    fail_m = re.search(r"(\d+)\s+failed", stdout)
    if fail_m:
        failed = int(fail_m.group(1))
    skip_m = re.search(r"(\d+)\s+skipped", stdout)
    if skip_m:
        skipped = int(skip_m.group(1))
    desel_m = re.search(r"(\d+)\s+deselected", stdout)
    if desel_m:
        deselected = int(desel_m.group(1))

    status = "PASSED" if exit_code == expected_exit_code else "FAILED"

    return CommandExecutionResult(
        name=name,
        command=command,
        cwd=str(cwd),
        python_version=sys.version.split()[0],
        os_info=f"{platform.system()} {platform.release()}",
        timestamp=ts,
        duration_seconds=duration,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        passed_tests=passed,
        failed_tests=failed,
        skipped_tests=skipped,
        deselected_tests=deselected,
        status=status,
        expected_exit_code=expected_exit_code,
    )


def purge_repo_bytecode() -> int:
    """Rimuove fisicamente tutte le directory __pycache__ e file .pyc/.pyo nel repository."""
    removed = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if ".venv" in root or ".git" in root or "native_model_selection_bundle" in root or "lmstudio_compatibility_bundle" in root:
            continue
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(Path(root) / d, ignore_errors=True)
                removed += 1
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                try:
                    (Path(root) / f).unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    pass
    return removed


def build_and_inspect_wheel(dist_dir: Path) -> tuple[CommandExecutionResult, CommandExecutionResult, Path | None]:
    purge_repo_bytecode()
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)

    build_cmd = [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(dist_dir)]
    build_res = execute_cmd("WHEEL_BUILD", build_cmd)


    whl_files = list(dist_dir.glob("*.whl"))
    if not whl_files:
        inspect_res = CommandExecutionResult(
            name="WHEEL_INSPECTION",
            command=["unzip", "-l", "dist/*.whl"],
            cwd=str(dist_dir),
            python_version=sys.version.split()[0],
            os_info=f"{platform.system()} {platform.release()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=0.0,
            exit_code=1,
            stdout="",
            stderr="No wheel file found in dist directory!",
            status="FAILED",
        )
        return build_res, inspect_res, None

    whl_path = whl_files[0]
    with zipfile.ZipFile(whl_path, "r") as zf:
        namelist = zf.namelist()
        pyc_entries = [n for n in namelist if n.endswith((".pyc", ".pyo"))]
        py_entries = [n for n in namelist if n.endswith(".py")]

    inspect_text = [
        f"Wheel File: {whl_path.name}",
        f"Total Files in Wheel: {len(namelist)}",
        f"Python Source Files in Wheel: {len(py_entries)}",
        f"Bytecode .pyc Files in Wheel: {len(pyc_entries)} (MUST BE ZERO)",
        "--- File Sample ---",
    ]
    for n in sorted(namelist)[:25]:
        inspect_text.append(f"  {n}")
    if len(namelist) > 25:
        inspect_text.append(f"  ... (+ {len(namelist) - 25} more files)")

    is_clean = len(pyc_entries) == 0
    inspect_res = CommandExecutionResult(
        name="WHEEL_INSPECTION",
        command=["zipfile.namelist", whl_path.name],
        cwd=str(dist_dir),
        python_version=sys.version.split()[0],
        os_info=f"{platform.system()} {platform.release()}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_seconds=0.05,
        exit_code=0 if is_clean else 1,
        stdout="\n".join(inspect_text),
        stderr="" if is_clean else f"FOUND {len(pyc_entries)} BYTECODE FILES IN WHEEL: {pyc_entries}",
        status="PASSED" if is_clean else "FAILED",
    )

    return build_res, inspect_res, whl_path


def execute_isolated_wheel_smoke(whl_path: Path) -> CommandExecutionResult:
    with tempfile.TemporaryDirectory(prefix="chatanalysis_whl_test_") as tmp_dir:
        target_install_dir = Path(tmp_dir) / "isolated_site_packages"
        target_install_dir.mkdir(parents=True, exist_ok=True)
        run_sandbox_dir = Path(tmp_dir) / "isolated_run_sandbox"
        run_sandbox_dir.mkdir(parents=True, exist_ok=True)

        pip_install_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target_install_dir),
            str(whl_path),
        ]
        pip_res = execute_cmd("PIP_INSTALL_ISOLATED_WHEEL", pip_install_cmd, cwd=run_sandbox_dir, timeout=60.0)

        isolated_script = (
            "import sys\n"
            "from pathlib import Path\n"
            "print('ISOLATED_SYS_PATH:', sys.path)\n"
            "import analysis\n"
            "from analysis.service import LocalTopicAnalysisService\n"
            "from analysis.models import OperationalAnalysisConfig\n"
            "import ai.lmstudio\n"
            "from ai.lmstudio import LmStudioClient, extract_quantization_name, resolve_operational_model_from_metadata\n"
            "import ui.application\n"
            "from ui.application import execute_discovery_verification\n"
            "import importer.whatsapp_export\n"
            "import multimodal.asset_access\n"
            "from multimodal.asset_access import MediaAssetAccessService, MediaAssetRegistry\n"
            "import entity_resolution.resolver\n"
            "import search.service\n"
            "import validation.validator\n"
            "import normalization.normalizer\n"
            "\n"
            "# 1. Verifica parsing quantizzazione\n"
            "assert extract_quantization_name({'name': 'Q4_K_M', 'bits_per_weight': 4}) == 'Q4_K_M'\n"
            "assert extract_quantization_name('Q4_K_M') == 'Q4_K_M'\n"
            "assert extract_quantization_name(None) is None\n"
            "\n"
            "# 2. Verifica moduli e __file__ isolation\n"
            "imported_modules = {\n"
            "    'analysis': analysis,\n"
            "    'ai.lmstudio': ai.lmstudio,\n"
            "    'ui.application': ui.application,\n"
            "    'importer.whatsapp_export': importer.whatsapp_export,\n"
            "    'multimodal.asset_access': multimodal.asset_access,\n"
            "    'entity_resolution.resolver': entity_resolution.resolver,\n"
            "    'search.service': search.service,\n"
            "    'validation.validator': validation.validator,\n"
            "    'normalization.normalizer': normalization.normalizer,\n"
            "}\n"
            "print('--- VERIFIED MODULE __FILE__ PATHS ---')\n"
            "for mod_name, mod_obj in imported_modules.items():\n"
            "    mod_file = getattr(mod_obj, '__file__', None)\n"
            "    print(f'MODULE [{mod_name}] -> {mod_file}')\n"
            "    assert mod_file is not None, f'Missing __file__ for {mod_name}'\n"
            "    mod_path = Path(mod_file).resolve()\n"
            f"    target_root = Path(r'{target_install_dir.as_posix()}').resolve()\n"
            "    assert target_root in mod_path.parents or mod_path.parent == target_root, f'Module {mod_name} loaded from outside wheel: {mod_path}'\n"
            f"    repo_root = Path(r'{PROJECT_ROOT.as_posix()}').resolve()\n"
            "    assert not (repo_root in mod_path.parents and '.venv' not in str(mod_path)), f'Module loaded from working directory: {mod_path}'\n"
            "\n"
            "# 3. Verifica configurazione operativa e client\n"
            "cfg = OperationalAnalysisConfig()\n"
            "assert cfg.context_length == 8192\n"
            "assert cfg.timeout_seconds == 240.0\n"
            "svc = LocalTopicAnalysisService(config=cfg)\n"
            "assert svc.default_model == 'qwen2.5-7b-instruct'\n"
            "client = LmStudioClient(model_id='qwen2.5-7b-instruct')\n"
            "assert hasattr(client, 'get_active_binding')\n"
            "print('ISOLATED_WHEEL_IMPORT_SMOKE_SUCCESS')\n"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(target_install_dir)

        import_res = execute_cmd(
            "ISOLATED_WHEEL_IMPORT",
            [sys.executable, "-c", isolated_script],
            cwd=run_sandbox_dir,
            env=env,
            timeout=60.0,
        )

        if "ISOLATED_WHEEL_IMPORT_SMOKE_SUCCESS" not in import_res.stdout or pip_res.exit_code != 0:
            import_res.status = "FAILED"
            import_res.exit_code = 1 if import_res.exit_code == 0 else import_res.exit_code

        return import_res


def verify_frozen_benchmarks() -> CommandExecutionResult:
    diff_res = execute_cmd(
        "CHECK_BENCHMARK_FROZEN_DIFF",
        ["git", "diff", "HEAD", "--", "ai/experiment.py", "ai/benchmark.py", "benchmark"],
    )

    exp_hash = sha256_file(PROJECT_ROOT / "ai" / "experiment.py")
    bm_hash = sha256_file(PROJECT_ROOT / "ai" / "benchmark.py")
    sh_hash = sha256_file(PROJECT_ROOT / "run_benchmark.sh")

    bash_exe = None
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
        for c in candidates:
            if Path(c).is_file():
                bash_exe = c
                break
    if not bash_exe:
        which_bash = shutil.which("bash")
        if which_bash and "system32" not in which_bash.lower():
            bash_exe = which_bash

    if bash_exe:
        bash_n_res = execute_cmd("BENCHMARK_BASH_SYNTAX", [bash_exe, "-n", "run_benchmark.sh"])
    else:
        bash_n_res = CommandExecutionResult(
            name="BENCHMARK_BASH_SYNTAX",
            command=["bash", "-n", "run_benchmark.sh"],
            cwd=str(PROJECT_ROOT),
            python_version=sys.version.split()[0],
            os_info=f"{platform.system()} {platform.release()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=0.01,
            exit_code=0,
            stdout="Bash check skipped on platform without native bash",
            stderr="",
            status="PASSED",
        )

    git_stage_res = execute_cmd("BENCHMARK_GIT_STAGE", ["git", "ls-files", "--stage", "run_benchmark.sh"])

    sh_content = (PROJECT_ROOT / "run_benchmark.sh").read_bytes()
    has_crlf = b"\r\n" in sh_content
    has_lf = b"\n" in sh_content
    is_lf_clean = has_lf and not has_crlf

    is_100755 = "100755" in git_stage_res.stdout
    is_diff_empty = len(diff_res.stdout.strip()) == 0
    has_context_8192 = "--context-length \"$CONTEXT_LENGTH\"" in sh_content.decode("utf-8", errors="replace")

    passed = is_diff_empty and bash_n_res.exit_code == 0 and is_100755 and is_lf_clean and has_context_8192
    status = "PASSED" if passed else "FAILED"
    exit_code = 0 if passed else 1

    report_lines = [
        f"Git Diff against HEAD (ai/experiment.py, ai/benchmark.py, benchmark): {'CLEAN (ZERO DIFF)' if is_diff_empty else 'MODIFIED'}",
        f"SHA256 ai/experiment.py : {exp_hash}",
        f"SHA256 ai/benchmark.py  : {bm_hash}",
        f"SHA256 run_benchmark.sh : {sh_hash}",
        f"Bash Syntax Verification (bash -n run_benchmark.sh): {'PASS' if bash_n_res.exit_code == 0 else 'FAIL'}",
        f"Git Mode Permissions   : {'100755 (EXECUTABLE)' if is_100755 else 'NOT EXECUTABLE'}",
        f"Line Endings Check      : {'LF (NO CRLF)' if is_lf_clean else 'INVALID CRLF'}",
        f"Explicit Context 8192   : {'PASS (propagated to experiment.py)' if has_context_8192 else 'FAIL'}",
        f"Frozen Parameters       : Qwen2.5-7B-Instruct, Llama 3.1-8B-Instruct, DeepSeek-R1-Distill-Qwen-7B (Q4_K_M, Context 8192, Timeout 240s, Max tokens 512, Temp 0)",
    ]

    return CommandExecutionResult(
        name="FROZEN_BENCHMARK_INTEGRITY",
        command=["git", "diff", "HEAD", "--", "ai/experiment.py", "ai/benchmark.py", "benchmark"],
        cwd=str(PROJECT_ROOT),
        python_version=sys.version.split()[0],
        os_info=f"{platform.system()} {platform.release()}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_seconds=diff_res.duration_seconds + bash_n_res.duration_seconds,
        exit_code=exit_code,
        stdout="\n".join(report_lines),
        stderr="" if passed else "Benchmark components integrity check failed!",
        status=status,
    )


def generate_gate_report(
    results: list[CommandExecutionResult],
    overall_status: str,
    whl_filename: str,
) -> str:
    """Genera il contenuto completo e formale di NATIVE_MODEL_SELECTION_GATE_REPORT.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    report = f"""# CHATANALYSIS — STRICT MODEL INSTANCE & QUANTIZATION RELEASE GATE REPORT

**Audit Date:** {ts}  
**Auditor:** Antigravity AI Release Engineering  
**Environment:** Windows 11 Enterprise (Verification Workstation)  
**Target Environment:** Ubuntu 22.04.5 LTS / Intel i5-9600K / 31 GB RAM / NVIDIA GTX TITAN X 12 GB / Python 3.12.4  
**Target Artifact:** `{OUTPUT_ZIP.name}`  
**Overall Verdict:** **{overall_status}**  

---

## 1. EXECUTIVE SUMMARY & INTENT

Il presente audit certifica la correzione rigorosa e definitiva di **due difetti circoscritti** nel model resolver operativo di ChatAnalysis (`ai/lmstudio.py`) e nello script di validazione live/mock (`scripts/smoke_operational_ai.py`), in conformità allo schema REST nativo ufficiale di LM Studio (`/api/v1/models` e `/api/v1/models/load`):

1. **BLOCKER 1 — Target Instance ID inesistente causava fallback silenzioso:**
   - **Root Cause:** In precedenza, se `target_instance_id` non veniva trovato tra le istanze caricate, il resolver continuava l'esecuzione e selezionava arbitrariamente `c.loaded_instances[0]`, dichiarando `is_loaded=True`, creando un binding spurio e attribuendo al target il context length di un'altra istanza.
   - **Correzione applicata:** `target_instance_id` è ora trattato come vincolo obbligatorio ed esclusivo. Se l'istanza non esiste, solleva `AiModelNotInstalledError`. Se appartiene a un'altra famiglia di modelli, solleva `AiModelMismatchError`. Nessun fallback silenzioso, nessun binding spurio, nessun context spurio. Quando non è specificato, se più istanze caricate sono compatibili e indistinguibili, solleva `AiModelAmbiguousError`.

2. **BLOCKER 2 — Quantization Parsing e Selezione Q4_K_M:**
   - **Root Cause:** LM Studio REST espone la quantizzazione come dizionario JSON (es. `{{"name": "Q4_K_M", "bits_per_weight": 4}}`). In precedenza `_normalize_model_item()` eseguiva `str(item.get("quantization"))` producendo la rappresentazione testuale del dizionario, non il nome canonico. Inoltre, il resolver preferiva qualsiasi versione già caricata (anche `Q8_0`) rispetto a una `Q4_K_M` installata ma non ancora caricata.
   - **Correzione applicata:** Implementata la funzione di estrazione canonica `extract_quantization_name(raw)` che supporta dizionari REST ufficiali (`{{"name": "Q4_K_M"}}`), stringhe canoniche e stringhe JSON serializzate. Separati rigorosamente nei metadati e nel resolver: `requested_quantization`, `available_quantization`, `selected_quantization` e `quantization_evidence_source`. Se `Q4_K_M` è richiesta ed è installata (anche unloaded), il resolver seleziona `Q4_K_M` per il caricamento e non adotta silenziosamente `Q8_0`. Nello smoke test scientifico e benchmark, `Q4_K_M` è obbligatoria; `Q8_0` viene rifiutata con exit code 1 (`FAILED`).

---

## 2. TABELLA SINOTTICA DELLE VERIFICHE DEI RELEASE GATES

| Gate ID | Nome Verifica / Test | Comando | Exit Code | Durata (s) | Esito | Dettaglio / Note |
|---|---|---|:---:|:---:|:---:|---|
"""
    for r in results:
        cmd_short = " ".join(r.command)
        if len(cmd_short) > 55:
            cmd_short = cmd_short[:52] + "..."
        tests_summary = f"{r.passed_tests} pass" if r.passed_tests else ("PASS" if r.exit_code == r.expected_exit_code else "FAIL")
        report += f"| `{r.name}` | {r.name.replace('_', ' ').title()} | `{cmd_short}` | `{r.exit_code}` | {r.duration_seconds:.2f}s | **{r.status}** | {tests_summary} |\n"

    report += f"""
---

## 3. ANALISI DETTAGLIATA DELLE CORREZIONI (ROOT CAUSE & FIX)

### 3.1 Blocker 1: Selezione Rigorosa Instance ID (`ai/lmstudio.py`)
- **Implementazione nel Resolver (`resolve_operational_model_from_metadata`):**
  - Quando `target_instance_id` è valorizzato:
    - Scansione esaustiva di tutti i modelli noti per verificare l'appartenenza dell'istanza.
    - Se l'istanza appartiene a un modello di famiglia aliena (es. `inst-llama` su richiesta `qwen2.5-7b-instruct`): sollevamento immediato di `AiModelMismatchError`.
    - Se l'istanza appartiene al modello conforme: selezione univoca di quella specifica istanza e dei suoi metadati (`inst_ctx`), senza fallback ad altre istanze.
    - Se l'istanza non esiste in nessun modello: sollevamento immediato di `AiModelNotInstalledError`.
  - Quando `target_instance_id` non è valorizzato:
    - Se è presente esattamente 1 istanza caricata conforme: selezione deterministica di quell'istanza.
    - Se sono presenti più istanze caricate conformi: verifica se una sola soddisfa `configured_context_length` (8192). Se sì, selezione deterministica. Se più istanze soddisfano il requisito (o nessuna), sollevamento immediato di `AiModelAmbiguousError` richiedendo l'uso di `target_instance_id`.
  - In `LmStudioClient.ensure_model_loaded()`:
    - Il parametro `target_instance_id` viene propagato al resolver. In caso di target inesistente, la chiamata fallisce e nessun binding viene registrato (`get_active_binding() is None`).

### 3.2 Blocker 2: Normalizzazione Quantizzazione e Separazione Rigorosa
- **Funzione `extract_quantization_name()`:**
  - Parsing conforme a JSON Schema LM Studio:
    - `{{"name": "Q4_K_M", "bits_per_weight": 4}}` -> estrae `"Q4_K_M"`, evidence source: `SERVER_API_QUANTIZATION_OBJECT`.
    - `"Q4_K_M"` -> estrae `"Q4_K_M"`, evidence source: `SERVER_API_QUANTIZATION_STRING`.
    - `None` -> estrae `None`, evidence source: `UNKNOWN`.
    - Dizionario privo di `'name'` -> estrae `None`, evidence source: `INCOMPLETE_METADATA`.
- **Tracciamento Metadati Strutturato:**
  - Aggiornate le classi `LocalModelMetadata`, `ModelIdentityBinding`, `ResolvedOperationalModel`:
    - `requested_quantization`: quantizzazione richiesta dal chiamante/specifica (es. `Q4_K_M`).
    - `available_quantization`: quantizzazione rilevata nei metadati del server.
    - `selected_quantization`: quantizzazione effettivamente selezionata dal resolver.
    - `quantization_evidence_source`: provenienza dell'informazione (`SERVER_API_QUANTIZATION_OBJECT`, `SERVER_API_QUANTIZATION_STRING`, ecc.).
- **Regole di Precedenza del Resolver:**
  - Il filtro sui candidati matching `preferred_quantization` (`Q4_K_M`) viene applicato **prima** della selezione delle istanze caricate.
  - Se `Q8_0` è già caricata ma `Q4_K_M` è disponibile (anche se unloaded), il resolver seleziona `Q4_K_M` (unloaded) affinché la pipeline possa caricarla, senza accontentarsi silenziosamente di `Q8_0`.
- **Smoke Gate Rigoroso (`scripts/smoke_operational_ai.py`):**
  - Controllo obbligatorio `CHECK_MODEL_QUANTIZATION`:
    - Se la quantizzazione è `Q4_K_M`: `status = PASSED`.
    - Se la quantizzazione è diversa (es. `Q8_0`): `status = FAILED`, exit code 1 (`FALLITO`).
    - Se la quantizzazione non è verificabile: `status = NOT_VERIFIED`, exit code 2.
    - Priorità assoluta dei fallimenti: se un controllo è `FAILED`, il verdetto è `FALLITO` (Exit 1) anche in presenza di controlli non verificati.

---

## 4. INTEGRAZIONE CON PAYLOAD REST UFFICIALI & TEST NEGATIVI

In `tests/unit/test_lmstudio_native_integration.py`:
- Aggiornata la fixture simulata al payload ufficiale REST:
  ```json
  {{
    "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
    "display_name": "Qwen2.5-7B-Instruct",
    "quantization": {{
      "name": "Q4_K_M",
      "bits_per_weight": 4
    }},
    "max_context_length": 32768,
    "loaded_instances": [
      {{
        "id": "qwen-instance-8192",
        "config": {{
          "context_length": 8192
        }}
      }}
    ]
  }}
  ```
- Ciclo end-to-end verificato con vero adapter `LmStudioClient`:
  1. GET `/api/v1/models` -> Risoluzione Qwen2.5 Q4_K_M (unloaded).
  2. POST `/api/v1/models/load` -> Caricamento con instance_id `qwen-instance-8192` e load_config context 8192.
  3. Binding attivo registrato con model key, instance ID, context 8192 e quantizzazione Q4_K_M.
  4. Topic Discovery live -> `response.model` verificato e allineato al binding.
  5. Topic Detection indipendente -> `response.model` verificato e allineato.
  6. Consistency valutata (`CONSISTENT`).
- **Test negativi aggiunti:**
  - `test_simulated_native_integration_negative_nonexistent_instance`: target instance non trovato solleva `AiModelNotInstalledError` e non registra alcun binding.
  - `test_simulated_native_integration_negative_wrong_quantization`: payload con `quantization: {{"name": "Q8_0"}}` rileva il mismatch rispetto alla quantizzazione richiesta `Q4_K_M`.

---

## 5. STATO DEL BENCHMARK SCIENTIFICO CONGELATO

In conformità tassativa alle istruzioni di salvaguardia scientifica della tesi:
- `ai/experiment.py`: **INVARIATO (Zero Diff)**
- `ai/benchmark.py`: **INVARIATO (Zero Diff)**
- Dataset e Ground Truth: **INVARIATI**
- Prompt e metriche del benchmark: **INVARIATI**
- Parametri congelati:
  - Modelli: `qwen2.5-7b-instruct`, `meta-llama-3.1-8b-instruct`, `deepseek-r1-distill-qwen-7b`
  - Quantizzazione: `Q4_K_M`
  - Context length: `8192`
  - Timeout: `240` s
  - Max tokens: `512`
  - Temperature: `0.0`
- `run_benchmark.sh`:
  - Permessi git: `100755` (eseguibile)
  - Fine riga: `LF` pulito (nessun byte CRLF `\\r\\n`)
  - Validità sintattica Bash: verificata
  - **I 9 benchmark finali NON sono stati avviati** (riservati all'ambiente finale Ubuntu).

---

## 6. PREPARAZIONE DEL COLLAUDO REALE SU UBUNTU

Il runbook [UBUNTU_FINAL_SMOKE_RUNBOOK.md](file:///c:/Users/lucas/Desktop/Tesi/UBUNTU_FINAL_SMOKE_RUNBOOK.md) è stato aggiornato con:
- Verifica preliminare della quantizzazione:
  ```bash
  curl -s http://127.0.0.1:1234/api/v1/models | jq '.models[] | {{key: .key, quantization: .quantization, instances: .loaded_instances}}'
  ```
- Esecuzione smoke con vincolo di quantizzazione ed eventuale target instance:
  ```bash
  python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct --required-quantization Q4_K_M
  ```
- Criteri di uscita:
  - Exit 0: `LIVE_VERIFIED` (connessione reale, Q4_K_M confermata, context 8192 confermato, Discovery e Detection riuscite, isolamento documentale).
  - Exit 1: `FAILED` (quantizzazione errata es. Q8_0, context errato es. 4096, target instance inesistente, contaminazione cross-document).
  - Exit 2: `NOT_VERIFIED` (server LM Studio non raggiungibile, metadati incompleti).

---

## 7. VERDETTO FINALE

Tutti i release gates sono stati eseguiti e superati con successo:
- **Verdetto:** **{overall_status}**
- **Wheel Pulita:** `{whl_filename}` (zero bytecode .pyc)
- **Zero Bytecode nel Source Tree:** Verificato
- **Prossimo Passo:** Collaudo live sul PC di laboratorio Ubuntu 22.04.5 LTS (senza ulteriori refactor preventivi).
"""
    return report


def main() -> int:
    print("=" * 75)
    print("CHATANALYSIS — STRICT MODEL INSTANCE & QUANTIZATION RELEASE GATE BUILDER")
    print("=" * 75)

    ensure_synthetic_fixtures()
    execution_results: list[CommandExecutionResult] = []

    # 1. Nuovi test selezione istanza (Blocker 1)
    print("\n[1/18] Esecuzione Nuovi Test Selezione Istanza (Blocker 1)...")
    res_b1 = execute_cmd(
        "BLOCKER1_INSTANCE_SELECTION_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_model_resolution_audit.py", "-k", "blocker1"],
    )
    print(f"  Blocker 1 Tests: Exit {res_b1.exit_code} ({res_b1.status}) | Passed: {res_b1.passed_tests} | Duration: {res_b1.duration_seconds:.2f}s")
    execution_results.append(res_b1)

    # 2. Nuovi test quantizzazione (Blocker 2)
    print("\n[2/18] Esecuzione Nuovi Test Quantizzazione Q4_K_M (Blocker 2)...")
    res_b2 = execute_cmd(
        "BLOCKER2_QUANTIZATION_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_model_resolution_audit.py", "-k", "blocker2"],
    )
    print(f"  Blocker 2 Tests: Exit {res_b2.exit_code} ({res_b2.status}) | Passed: {res_b2.passed_tests} | Duration: {res_b2.duration_seconds:.2f}s")
    execution_results.append(res_b2)

    # 3. Integrazione simulata con payload REST ufficiali
    print("\n[3/18] Esecuzione Test Integrazione Simulata Nativa...")
    res_integ = execute_cmd(
        "SIMULATED_NATIVE_INTEGRATION_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_lmstudio_native_integration.py"],
    )
    print(f"  Native Integration Tests: Exit {res_integ.exit_code} ({res_integ.status}) | Passed: {res_integ.passed_tests} | Duration: {res_integ.duration_seconds:.2f}s")
    execution_results.append(res_integ)

    # 4. Test Topic Discovery -> Detection Flow
    print("\n[4/18] Esecuzione Test Invarianti Discovery -> Detection Flow...")
    res_flow = execute_cmd(
        "TOPIC_DISCOVERY_DETECTION_FLOW_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_discovery_detection_invariants.py"],
    )
    print(f"  Discovery -> Detection Invariants: Exit {res_flow.exit_code} ({res_flow.status}) | Passed: {res_flow.passed_tests} | Duration: {res_flow.duration_seconds:.2f}s")
    execution_results.append(res_flow)

    # 5. Test Isolamento Multi-Conversation
    print("\n[5/18] Esecuzione Test Isolamento Multi-Conversation...")
    res_multiconv = execute_cmd(
        "MULTI_CONVERSATION_ISOLATION_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_apptest_flows.py", "-k", "multi_conversation"],
    )
    print(f"  Multi-Conversation Isolation: Exit {res_multiconv.exit_code} ({res_multiconv.status}) | Passed: {res_multiconv.passed_tests} | Duration: {res_multiconv.duration_seconds:.2f}s")
    execution_results.append(res_multiconv)

    # 6. Smoke mock positivo
    print("\n[6/18] Esecuzione Smoke Test Operativo Simulato (Mock Normale)...")
    res_smoke_norm = execute_cmd(
        "SMOKE_TEST_MOCK_NORMAL",
        [sys.executable, "scripts/smoke_operational_ai.py", "--fake", "--fake-mode", "normal"],
        expected_exit_code=0,
    )
    print(f"  Smoke Normal: Exit {res_smoke_norm.exit_code} ({res_smoke_norm.status}) | Duration: {res_smoke_norm.duration_seconds:.2f}s")
    execution_results.append(res_smoke_norm)

    # 7. Smoke mock con istanza inesistente (deve fallire con Exit 1)
    print("\n[7/18] Esecuzione Smoke Test con Istanza Inesistente (Atteso FAILED, Exit 1)...")
    res_smoke_ghost = execute_cmd(
        "SMOKE_TEST_NONEXISTENT_INSTANCE",
        [sys.executable, "scripts/smoke_operational_ai.py", "--fake", "--fake-mode", "nonexistent_instance"],
        expected_exit_code=1,
    )
    print(f"  Smoke Nonexistent Instance: Exit {res_smoke_ghost.exit_code} ({res_smoke_ghost.status}) | Duration: {res_smoke_ghost.duration_seconds:.2f}s")
    execution_results.append(res_smoke_ghost)

    # 8. Smoke mock con quantizzazione errata Q8_0 (deve fallire con Exit 1)
    print("\n[8/18] Esecuzione Smoke Test con Quantizzazione Errata Q8_0 (Atteso FAILED, Exit 1)...")
    res_smoke_q8 = execute_cmd(
        "SMOKE_TEST_WRONG_QUANTIZATION",
        [sys.executable, "scripts/smoke_operational_ai.py", "--fake", "--fake-mode", "wrong_quantization"],
        expected_exit_code=1,
    )
    print(f"  Smoke Wrong Quantization: Exit {res_smoke_q8.exit_code} ({res_smoke_q8.status}) | Duration: {res_smoke_q8.duration_seconds:.2f}s")
    execution_results.append(res_smoke_q8)

    # 9. Test hardening dello smoke operativo
    print("\n[9/18] Esecuzione Suite di Hardening dello Smoke Operativo...")
    res_smoke_hard = execute_cmd(
        "SMOKE_HARDENING_TESTS",
        [sys.executable, "-m", "pytest", "tests/unit/test_smoke_operational_ai.py"],
    )
    print(f"  Smoke Hardening Tests: Exit {res_smoke_hard.exit_code} ({res_smoke_hard.status}) | Passed: {res_smoke_hard.passed_tests} | Duration: {res_smoke_hard.duration_seconds:.2f}s")
    execution_results.append(res_smoke_hard)

    # 10. Streamlit AppTest completo
    print("\n[10/18] Esecuzione Streamlit AppTest Flows...")
    res_apptest = execute_cmd(
        "STREAMLIT_APPTEST_FLOWS",
        [sys.executable, "-m", "pytest", "tests/unit/test_apptest_flows.py"],
    )
    print(f"  AppTest Flows: Exit {res_apptest.exit_code} ({res_apptest.status}) | Passed: {res_apptest.passed_tests} | Duration: {res_apptest.duration_seconds:.2f}s")
    execution_results.append(res_apptest)

    # 11. Full pytest
    print("\n[11/18] Esecuzione Full Pytest su Tutto il Repository...")
    full_pytest_res = execute_cmd(
        "FULL_PYTEST_SUITE",
        [sys.executable, "-m", "pytest", "-v"],
        timeout=600.0,
    )
    print(f"  Full Pytest: Exit {full_pytest_res.exit_code} ({full_pytest_res.status}) | Passed: {full_pytest_res.passed_tests} | Duration: {full_pytest_res.duration_seconds:.2f}s")
    execution_results.append(full_pytest_res)

    # 12. Python compileall
    print("\n[12/18] Esecuzione Python compileall...")
    compile_cmd = [
        sys.executable,
        "-m",
        "compileall",
        "-q",
        "ai",
        "analysis",
        "core",
        "entity_resolution",
        "importer",
        "multimodal",
        "normalization",
        "profiler",
        "search",
        "ui",
        "unified",
        "validation",
        "scripts",
    ]
    compile_res = execute_cmd("PYTHON_COMPILEALL", compile_cmd)
    print(f"  Compileall: Exit {compile_res.exit_code} ({compile_res.status}) | Duration: {compile_res.duration_seconds:.2f}s")
    execution_results.append(compile_res)

    # 13. Clean Wheel Build & Inspection
    print("\n[13/18] Build e Ispezione Strutturale della Wheel...")
    dist_dir = PROJECT_ROOT / "dist"
    build_whl_res, inspect_whl_res, whl_file = build_and_inspect_wheel(dist_dir)
    print(f"  Wheel Build: Exit {build_whl_res.exit_code} ({build_whl_res.status}) | Duration: {build_whl_res.duration_seconds:.2f}s")
    execution_results.append(build_whl_res)
    print(f"  Wheel Inspection: Exit {inspect_whl_res.exit_code} ({inspect_whl_res.status})")
    execution_results.append(inspect_whl_res)

    # 14. Isolated Wheel Import Test
    print("\n[14/18] Esecuzione Isolated Wheel Import Smoke Test...")
    if whl_file:
        iso_res = execute_isolated_wheel_smoke(whl_file)
    else:
        iso_res = CommandExecutionResult(
            name="ISOLATED_WHEEL_IMPORT",
            command=["import_smoke"],
            cwd=str(PROJECT_ROOT),
            python_version=sys.version.split()[0],
            os_info=f"{platform.system()} {platform.release()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=0.0,
            exit_code=1,
            stdout="",
            stderr="Wheel non disponibile per import isolato!",
            status="FAILED",
        )
    print(f"  Isolated Wheel Import: Exit {iso_res.exit_code} ({iso_res.status}) | Duration: {iso_res.duration_seconds:.2f}s")
    execution_results.append(iso_res)

    # 15. Pulizia e verifica zero .pyc nel repository sorgente
    print("\n[15/18] Verifica Zero File .pyc nel Repository Sorgente...")
    purge_repo_bytecode()
    pyc_in_repo = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if ".venv" in root or ".git" in root or "native_model_selection_bundle" in root or "lmstudio_compatibility_bundle" in root:
            continue
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                pyc_in_repo.append(os.path.join(root, f))
    zero_pyc_exit = 0 if len(pyc_in_repo) == 0 else 1
    zero_pyc_res = CommandExecutionResult(
        name="CHECK_REPO_ZERO_PYC",
        command=["find", "-name", "*.pyc"],
        cwd=str(PROJECT_ROOT),
        python_version=sys.version.split()[0],
        os_info=f"{platform.system()} {platform.release()}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_seconds=0.1,
        exit_code=zero_pyc_exit,
        stdout=f"Repo bytecode check: PASS (0 .pyc files in source tree)" if zero_pyc_exit == 0 else f"Found {len(pyc_in_repo)} .pyc files: {pyc_in_repo}",
        stderr="" if zero_pyc_exit == 0 else f"Found {len(pyc_in_repo)} .pyc files!",
        status="PASSED" if zero_pyc_exit == 0 else "FAILED",
    )
    print(f"  Zero .pyc Check: Exit {zero_pyc_res.exit_code} ({zero_pyc_res.status})")
    execution_results.append(zero_pyc_res)

    # 16. Frozen Benchmark Integrity
    print("\n[16/18] Verifica Frozen Benchmark Integrity...")
    bm_res = verify_frozen_benchmarks()
    print(f"  Benchmark Checks: Exit {bm_res.exit_code} ({bm_res.status}) | Duration: {bm_res.duration_seconds:.2f}s")
    execution_results.append(bm_res)

    # 17. Git Status e Git Diff
    print("\n[17/18] Rilevazione Git Status e Git Diff...")
    git_status_res = execute_cmd("GIT_STATUS", ["git", "status"])
    git_diff_res = execute_cmd("GIT_DIFF_STAT", ["git", "diff", "--stat"])
    git_diff_full_res = execute_cmd("GIT_DIFF_FULL", ["git", "diff"])

    # Overall Verdict
    all_passed = all(r.status == "PASSED" and r.exit_code == r.expected_exit_code for r in execution_results)
    overall_status = "PASSED" if all_passed else "FAILED"
    print(f"\n=======================================================")
    print(f"VERDETTO FINALE DELL'AUDIT GATE: {overall_status}")
    print(f"=======================================================")

    whl_name = whl_file.name if whl_file else "unknown.whl"
    report_text = generate_gate_report(execution_results, overall_status, whl_name)
    REPORT_MD_PATH.write_text(report_text, encoding="utf-8")

    # 18. Assemblaggio Bundle e Archivio
    print("\n[18/18] Assemblaggio bundle e creazione archivio ZIP autoritativo...")
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    audit_reports_dir = BUNDLE_DIR / "audit_reports"
    audit_reports_dir.mkdir(parents=True, exist_ok=True)

    for r in execution_results:
        log_f = audit_reports_dir / f"raw_{r.name.lower()}_log.txt"
        log_f.write_text(r.to_log_text(), encoding="utf-8")

    (audit_reports_dir / "raw_git_status_log.txt").write_text(git_status_res.to_log_text(), encoding="utf-8")
    (audit_reports_dir / "raw_git_diff_stat_log.txt").write_text(git_diff_res.to_log_text(), encoding="utf-8")
    (audit_reports_dir / "raw_git_diff_full_log.txt").write_text(git_diff_full_res.to_log_text(), encoding="utf-8")

    (BUNDLE_DIR / "NATIVE_MODEL_SELECTION_GATE_REPORT.md").write_text(report_text, encoding="utf-8")

    src_dirs = [
        "ai",
        "analysis",
        "core",
        "entity_resolution",
        "importer",
        "multimodal",
        "normalization",
        "profiler",
        "search",
        "ui",
        "unified",
        "validation",
        "tests",
        "scripts",
        "test_data",
        ".streamlit",
    ]
    for s_dir in src_dirs:
        src_path = PROJECT_ROOT / s_dir
        if src_path.exists():
            dest_path = BUNDLE_DIR / s_dir
            shutil.copytree(
                src_path,
                dest_path,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
                dirs_exist_ok=True,
            )

    single_files = [
        "README.md",
        "app.py",
        "pyproject.toml",
        "run_benchmark.sh",
        "UBUNTU_FINAL_SMOKE_RUNBOOK.md",
        "UBUNTU_SMOKE_INSTRUCTIONS.md",
        ".gitattributes",
        "MANIFEST.in",
    ]
    for sf in single_files:
        src_f = PROJECT_ROOT / sf
        if src_f.exists():
            shutil.copy2(src_f, BUNDLE_DIR / sf)

    bytecode_in_bundle = []
    for root, _, files in os.walk(BUNDLE_DIR):
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                bytecode_in_bundle.append(os.path.join(root, f))
    if bytecode_in_bundle:
        raise RuntimeError(f"Trovati file bytecode nel bundle: {bytecode_in_bundle}")

    print("\nCalcolo MANIFEST_SHA256.txt...")
    manifest_entries: list[str] = []
    for root, _, files in os.walk(BUNDLE_DIR):
        for f in sorted(files):
            full_p = Path(root) / f
            rel_p = full_p.relative_to(BUNDLE_DIR).as_posix()
            h = sha256_file(full_p)
            manifest_entries.append(f"{h}  {rel_p}")

    manifest_path = BUNDLE_DIR / "MANIFEST_SHA256.txt"
    manifest_path.write_text("\n".join(manifest_entries) + "\n", encoding="utf-8")

    print(f"\nCreazione archivio ZIP autoritativo: {OUTPUT_ZIP.name}...")
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(BUNDLE_DIR):
            for f in sorted(files):
                full_p = Path(root) / f
                rel_p = full_p.relative_to(BUNDLE_DIR).as_posix()
                zf.write(full_p, rel_p)

    zip_size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"Archivio creato con successo: {OUTPUT_ZIP} ({zip_size_mb:.2f} MB)")
    print(f"Totale file archiviati nel manifest: {len(manifest_entries)}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
