# CHATANALYSIS — STRICT QUANTIZATION ENFORCEMENT & PROVENANCE REPORT

**Audit Date:** 2026-10-11 19:07:48 UTC  
**Auditor:** Antigravity AI Release Engineering  
**Environment:** Windows 11 Enterprise (Pre-Handoff Workstation)  
**Target Execution Environment:** Ubuntu 22.04.5 LTS / Intel i5-9600K / 31 GB RAM / NVIDIA GTX TITAN X 12 GB / Python 3.12.4  
**Target Bundle:** `chatAnalysis_strict_quantization_provenance_audit_20261011.zip`  
**Overall Verdict:** **PASSED**  

---

## 1. EXECUTIVE SUMMARY & INTENT

Il presente audit certifica la correzione rigorosa e definitiva di **due difetti specifici riprodotti nel codice**, relativi all'imposizione della quantizzazione richiesta e alla provenienza scientifica dell'evidenza nei metadati LM Studio:

1. **BLOCKER 1 — `required_quantization` non veniva imposta rigorosamente:**
   - **Root Cause:** In `ai/lmstudio.py`, `resolve_operational_model_from_metadata()` continuava a selezionare un modello con quantizzazione alternativa (es. `Q8_0`) quando `required_quantization="Q4_K_M"` e `allow_fallback_quantization=False`, accettando inoltre un `target_instance_id` puntante a un modello con quantizzazione incompatibile.
   - **Correzione applicata:** Imposizione tassativa a livello di resolver. Quando `required_quantization` è specificata e `allow_fallback_quantization=False` (strict mode scientifico), esclusivamente la quantizzazione richiesta è ammissibile. Se non disponibile, solleva `AiModelNotInstalledError`. Se `target_instance_id` punta a un modello con quantizzazione incompatibile, solleva `AiModelMismatchError`. Il fallback a quantizzazione alternativa è consentito esclusivamente nella UI operativa con `allow_fallback_quantization=True` e viene esplicitamente tracciato in `selected_quantization`.

2. **BLOCKER 2 — `MODEL_KEY_INFERRED` non costituisce prova server scientifica:**
   - **Root Cause:** Quando i metadati LM Studio non espongono il campo quantizzazione, `_normalize_model_item()` deduce il token dal model key marcando la provenienza come `MODEL_KEY_INFERRED`. In `scripts/smoke_operational_ai.py`, il check `CHECK_MODEL_QUANTIZATION` considerava sufficiente l'uguaglianza del valore canonico senza verificare la fonte di provenienza dell'evidenza.
   - **Correzione applicata:** Distinzione formale delle sorgenti di evidenza (`SERVER_API_QUANTIZATION_OBJECT`, `SERVER_API_QUANTIZATION_STRING`, `DOCUMENTED_MANUAL_LOADER_EVIDENCE`, `MODEL_KEY_INFERRED`, `UNKNOWN`). Nello smoke scientifico rigoroso, `MODEL_KEY_INFERRED` non certifica la quantizzazione e produce lo stato `NOT_VERIFIED` (Exit Code 2). Se il server dichiara esplicitamente una quantizzazione incompatibile (es. `Q8_0`), il test fallisce tassativamente con `FAILED` (Exit Code 1).

---

## 2. TABELLA SINOTTICA DELLE VERIFICHE DEI RELEASE GATES

| Gate ID | Nome Verifica / Test | Comando | Exit Code | Durata (s) | Esito | Dettaglio / Note |
|---|---|---|:---:|:---:|:---:|---|
| `BLOCKER1_STRICT_QUANTIZATION_TESTS` | Blocker1 Strict Quantization Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.47s | **PASSED** | 12 pass |
| `BLOCKER2_PROVENANCE_TESTS` | Blocker2 Provenance Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 7.08s | **PASSED** | 23 pass |
| `MODEL_RESOLUTION_FULL_AUDIT_TESTS` | Model Resolution Full Audit Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 7.22s | **PASSED** | 73 pass |
| `SIMULATED_NATIVE_INTEGRATION_TESTS` | Simulated Native Integration Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 5.18s | **PASSED** | 3 pass |
| `TOPIC_DISCOVERY_DETECTION_FLOW_TESTS` | Topic Discovery Detection Flow Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 3.90s | **PASSED** | 12 pass |
| `MULTI_CONVERSATION_ISOLATION_TESTS` | Multi Conversation Isolation Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 8.11s | **PASSED** | 1 pass |
| `SMOKE_TEST_MOCK_NORMAL` | Smoke Test Mock Normal | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 3.02s | **PASSED** | PASS |
| `SMOKE_TEST_NONEXISTENT_INSTANCE` | Smoke Test Nonexistent Instance | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.59s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_QUANTIZATION` | Smoke Test Wrong Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.87s | **PASSED** | PASS |
| `SMOKE_TEST_INFERRED_QUANTIZATION` | Smoke Test Inferred Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.47s | **PASSED** | PASS |
| `SMOKE_TEST_UNKNOWN_QUANTIZATION` | Smoke Test Unknown Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.47s | **PASSED** | PASS |
| `SMOKE_HARDENING_TESTS` | Smoke Hardening Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 10.67s | **PASSED** | 24 pass |
| `STREAMLIT_APPTEST_FLOWS` | Streamlit Apptest Flows | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 17.24s | **PASSED** | 7 pass |
| `FULL_PYTEST_SUITE` | Full Pytest Suite | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 170.63s | **PASSED** | 1647 pass |
| `PYTHON_COMPILEALL` | Python Compileall | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 2.10s | **PASSED** | PASS |
| `WHEEL_BUILD` | Wheel Build | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 28.20s | **PASSED** | PASS |
| `WHEEL_INSPECTION` | Wheel Inspection | `zipfile.namelist whatsapp_forensic_importer-0.1.0-py...` | `0` | 0.05s | **PASSED** | PASS |
| `ISOLATED_WHEEL_IMPORT` | Isolated Wheel Import | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 1.51s | **PASSED** | PASS |
| `CHECK_REPO_ZERO_PYC` | Check Repo Zero Pyc | `find -name *.pyc` | `0` | 0.10s | **PASSED** | PASS |
| `FROZEN_BENCHMARK_INTEGRITY` | Frozen Benchmark Integrity | `git diff HEAD -- ai/experiment.py ai/benchmark.py be...` | `0` | 0.33s | **PASSED** | PASS |

---

## 3. ANALISI DETTAGLIATA DELLE CORREZIONI (ROOT CAUSE & FIX)

### 3.1 Blocker 1: Imposizione Rigorosa di `required_quantization` (`ai/lmstudio.py`)
- **File Modificati:**
  - `ai/lmstudio.py`
  - `scripts/smoke_operational_ai.py`
  - `tests/unit/test_model_resolution_audit.py`
  - `tests/unit/test_smoke_operational_ai.py`
  - `tests/unit/test_lmstudio_native_integration.py`
- **Correzioni Applicate nel Resolver:**
  1. Nel caso con `target_instance_id` (Case A):
     - Verifica che il modello associato all'istanza target soddisfi `required_quantization`. Se la quantizzazione è diversa e `allow_fallback_quantization=False`, solleva `AiModelMismatchError`.
  2. Nel caso di selezione generale (Case B):
     - Quando `required_quantization` è specificata e `allow_fallback_quantization=False`:
       - I candidati vengono filtrati tassativamente con `c.available_quantization == required_quantization`.
       - Se nessun modello con la quantizzazione richiesta è installato, viene sollevato immediatamente `AiModelNotInstalledError`. Nessun fallback silenzioso a `Q8_0` è possibile.
  3. Nel percorso UI operativo (`allow_fallback_quantization=True`):
     - Se `Q4_K_M` non è disponibile ma un'altra quantizzazione (es. `Q8_0`) è disponibile, il resolver seleziona il fallback registrando fedelmente:
       - `requested_quantization = "Q4_K_M"`
       - `selected_quantization = "Q8_0"`
       - `is_fallback = True`
  4. In `resolve_qwen_operational_model()`, `load_model()` e `ensure_model_loaded()`:
     - Parametro `allow_fallback_quantization: bool = False` propagato di default per garantire la rigorosità scientifica.

### 3.2 Blocker 2: Classificazione Provenienza Quantizzazione (`scripts/smoke_operational_ai.py`)
- **Correzioni Applicate nello Smoke Test:**
  1. Ispezione rigorosa del campo `quantization_evidence_source` registrato dal resolver e dal binding:
     - `SERVER_API_QUANTIZATION_OBJECT`: Certificazione valida (da dizionario JSON ufficiale `quantization.name`).
     - `SERVER_API_QUANTIZATION_STRING`: Certificazione valida (da stringa ufficiale API).
     - `DOCUMENTED_MANUAL_LOADER_EVIDENCE`: Certificazione valida (da prova manuale documentata con flag `--confirmed-quantization`).
     - `MODEL_KEY_INFERRED`: Valore solo dedotto dal nome del modello. **NON costituisce prova scientifica**. Produce `status = "NOT_VERIFIED"` ed Exit Code 2.
     - `UNKNOWN`: Quantizzazione non disponibile. Produce `status = "NOT_VERIFIED"` ed Exit Code 2.
  2. Rilevazione del mismatch dichiarato:
     - Se il server dichiara esplicitamente una quantizzazione diversa da quella richiesta (es. `Q8_0`), il test viene marcato come `status = "FAILED"` con Exit Code 1.
  3. Priorità assoluta dei fallimenti:
     - Se un controllo è `FAILED`, il verdetto è `FALLITO` (Exit 1) anche in presenza di controlli non verificati (`NOT_VERIFIED`).

---

## 4. EVIDENZA DEI TEST EFFETTUATI ED EXIT CODES

### 4.1 Matrice di Validazione Blocker 1 (Strict Quantization Enforcement)
- `Q4_K_M` richiesta e disponibile -> ACCEPTED (`selected_quantization="Q4_K_M"`)
- `Q4_K_M` richiesta e caricata -> ACCEPTED (`is_loaded=True`, `instance_id` valido)
- `Q4_K_M` richiesta ma solo `Q8_0` disponibile (`allow_fallback_quantization=False`) -> REJECTED (`AiModelNotInstalledError`)
- `allow_fallback_quantization=True` con `Q8_0` disponibile -> ACCEPTED con fallback tracciato (`selected_quantization="Q8_0"`)
- Target instance `q8-instance` con `required_quantization="Q4_K_M"` (`allow_fallback_quantization=False`) -> REJECTED (`AiModelMismatchError`)
- Target instance `q4-instance` con `required_quantization="Q4_K_M"` -> ACCEPTED
- `Q4_K_M` disponibile ma non caricata, `Q8_0` già caricata -> Seleziona `Q4_K_M` per caricamento (`is_loaded=False`)
- Quantizzazione sconosciuta in modalità rigorosa -> REJECTED (`AiModelNotInstalledError`)
- Nessun binding spurio registrato dopo errore di risoluzione.

### 4.2 Matrice di Validazione Blocker 2 (Quantization Evidence Provenance)
- `--fake --fake-mode normal`: Quantizzazione certificata `Q4_K_M` (`SERVER_API_QUANTIZATION_OBJECT`) -> **Exit Code 0 (SUCCESSO)**
- `--fake --fake-mode nonexistent_instance`: Istanza non trovata -> **Exit Code 1 (FALLITO)**
- `--fake --fake-mode wrong_quantization`: Server dichiara `Q8_0` -> **Exit Code 1 (FALLITO)**
- `--fake --fake-mode inferred_quantization`: Provenienza `MODEL_KEY_INFERRED` -> **Exit Code 2 (NOT_VERIFIED)**
- `--fake --fake-mode unknown_quantization`: Provenienza `UNKNOWN` -> **Exit Code 2 (NOT_VERIFIED)**
- Esecuzione live su Windows senza demone attivo -> **Exit Code 2 (NOT_VERIFIED)**

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
  - Fine riga: `LF` pulito (nessun byte CRLF `\r\n`)
  - Validità sintattica Bash: verificata
  - **I 9 benchmark finali NON sono stati avviati** (riservati all'ambiente finale Ubuntu).

---

## 6. PREPARAZIONE DEL COLLAUDO REALE SU UBUNTU

Il runbook [UBUNTU_FINAL_SMOKE_RUNBOOK.md](file:///c:/Users/lucas/Desktop/Tesi/UBUNTU_FINAL_SMOKE_RUNBOOK.md) e le istruzioni operative sono pienamente allineati alle nuove garanzie di provenienza:
1. Verifica preliminare della quantizzazione esposta da LM Studio su Ubuntu:
   ```bash
   curl -s http://127.0.0.1:1234/api/v1/models | jq '.models[] | {key: .key, quantization: .quantization, instances: .loaded_instances}'
   ```
2. Esecuzione dello smoke test operativo rigoroso:
   ```bash
   python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct --required-quantization Q4_K_M
   ```
3. Comportamento deterministico:
   - Se LM Studio espone `quantization.name == "Q4_K_M"`: Exit 0 (`SUCCESSO`).
   - Se LM Studio omette il campo quantization ma il modello è Q4_K_M: Exit 2 (`NOT_VERIFIED`). Può essere validato con documentazione manuale tramite:
     ```bash
     python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct --required-quantization Q4_K_M --confirmed-quantization Q4_K_M
     ```
   - Se il modello caricato è `Q8_0` o altra quantizzazione diversa da Q4_K_M: Exit 1 (`FALLITO`).

---

## 7. VERDETTO FINALE

Tutti i release gates sono stati eseguiti e superati con successo:
- **Verdetto Generale:** **PASSED**
- **Wheel Pulita:** `whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (zero bytecode .pyc)
- **Zero Bytecode nel Source Tree:** Verificato (0 file `.pyc`)
- **Prossimo Passo:** Handoff e collaudo live sul PC di laboratorio Ubuntu 22.04.5 LTS senza ulteriori modifiche o refactor preventivi.
