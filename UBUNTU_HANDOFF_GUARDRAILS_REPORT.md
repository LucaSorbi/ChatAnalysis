# CHATANALYSIS — SCIENTIFIC EVIDENCE GUARD & UBUNTU HANDOFF REPORT

**Audit Date:** 2026-10-11 19:31:47 UTC  
**Auditor:** Antigravity AI Release Engineering  
**Environment:** Windows 11 Enterprise (Pre-Handoff Workstation)  
**Target Execution Environment:** Ubuntu 22.04.5 LTS / Intel i5-9600K / 31 GB RAM / NVIDIA GTX TITAN X 12 GB / Python 3.12.4  
**Target Bundle:** `chatAnalysis_ubuntu_handoff_guardrails_audit_20261011.zip`  
**Overall Verdict:** **PASSED**  

---

## 1. EXECUTIVE SUMMARY & INTENT

Il presente audit certifica l'implementazione del **Scientific Evidence Guard** in `scripts/smoke_operational_ai.py` e il congelamento finale prima del trasferimento al PC di laboratorio Ubuntu 22.04.5 LTS del professore:

1. **SCIENTIFIC EVIDENCE GUARD — Disabilitazione Promozione Automatica con Flag Manuali:**
   - **Root Cause Identificata:** I parametri `--confirmed-quantization` e `--confirmed-context` consentivano in precedenza la sostituzione acritica della quantizzazione o del contesto osservato con la stringa `DOCUMENTED_MANUAL_LOADER_EVIDENCE`, promuovendo al grado `PASSED` (Exit 0) controlli privi di attestazione documentale o provenienti solo dal nome del modello (`MODEL_KEY_INFERRED`).
   - **Politica Rigorosa Implementata:**
     - Se LM Studio espone `quantization.name=Q4_K_M` (o stringa ufficiale API): il controllo è **PASSED** (Exit 0).
     - Se LM Studio espone una quantizzazione differente (es. `Q8_0`): il controllo è **FAILED** (Exit 1). Nessun parametro manuale (es. `--confirmed-quantization Q4_K_M`) può sovrascrivere o sanare il mismatch dichiarato dal server.
     - Se LM Studio non espone la quantizzazione, oppure questa è `MODEL_KEY_INFERRED` o `UNKNOWN`: il gate scientifico automatico rimane tassativamente **NOT_VERIFIED** (Exit Code 2). La dichiarazione dell'operatore viene conservata separatamente nei log come evidenza supplementare (`operator_declared_quantization`), ma non promuove il test a `PASSED`.
     - Identico principio per il contesto: se il server espone 4096, il check è **FAILED** (Exit 1) anche se l'operatore dichiara 8192. Se il contesto è `UNKNOWN`, il check rimane **NOT_VERIFIED** (Exit 2).
     - Nei report e nei log vengono tracciati separatamente i valori osservati dal server (`server_observed_*`) e i valori dichiarati dall'operatore (`operator_declared_*`).
     - In modalità mock (`--fake`): la classificazione è esclusivamente **`MOCK_VERIFIED`**. Non viene mai dichiarato `LIVE_VERIFIED` prima dell'effettiva inferenza su Ubuntu con LM Studio attivo.

2. **STATO DI PREPARAZIONE AL PASSAGGIO UBUNTU:**
   - Nessun commit o push è stato eseguito autonomamente (preparati nel runbook).
   - I 9 benchmark congelati NON sono stati avviati.
   - Tutti i 1647+ test del repository risultano superati con successo.
   - Il runbook operativo [UBUNTU_FINAL_SMOKE_RUNBOOK.md](file:///c:/Users/lucas/Desktop/Tesi/UBUNTU_FINAL_SMOKE_RUNBOOK.md) è stato aggiornato in una procedura unica, essenziale, sequenziale e riproducibile.

---

## 2. TABELLA SINOTTICA DELLE VERIFICHE DEI RELEASE GATES

| Gate ID | Nome Verifica / Test | Comando | Exit Code | Durata (s) | Esito | Dettaglio / Note |
|---|---|---|:---:|:---:|:---:|---|
| `SCIENTIFIC_EVIDENCE_GUARD_TESTS` | Scientific Evidence Guard Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.03s | **PASSED** | 10 pass |
| `BLOCKER1_STRICT_QUANTIZATION_TESTS` | Blocker1 Strict Quantization Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 7.69s | **PASSED** | 12 pass |
| `BLOCKER2_PROVENANCE_TESTS` | Blocker2 Provenance Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 6.51s | **PASSED** | 23 pass |
| `MODEL_RESOLUTION_FULL_AUDIT_TESTS` | Model Resolution Full Audit Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.88s | **PASSED** | 73 pass |
| `SIMULATED_NATIVE_INTEGRATION_TESTS` | Simulated Native Integration Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.05s | **PASSED** | 3 pass |
| `TOPIC_DISCOVERY_DETECTION_FLOW_TESTS` | Topic Discovery Detection Flow Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 3.81s | **PASSED** | 12 pass |
| `MULTI_CONVERSATION_ISOLATION_TESTS` | Multi Conversation Isolation Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 9.15s | **PASSED** | 1 pass |
| `SMOKE_TEST_MOCK_NORMAL` | Smoke Test Mock Normal | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 2.23s | **PASSED** | PASS |
| `SMOKE_TEST_NONEXISTENT_INSTANCE` | Smoke Test Nonexistent Instance | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.80s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_QUANTIZATION` | Smoke Test Wrong Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.61s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_QUANT_OPERATOR_OVERRIDE_REJECTED` | Smoke Test Wrong Quant Operator Override Rejected | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.90s | **PASSED** | PASS |
| `SMOKE_TEST_INFERRED_QUANTIZATION` | Smoke Test Inferred Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.49s | **PASSED** | PASS |
| `SMOKE_TEST_INFERRED_QUANT_OPERATOR_FLAG_UNCERTIFIED` | Smoke Test Inferred Quant Operator Flag Uncertified | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.47s | **PASSED** | PASS |
| `SMOKE_TEST_UNKNOWN_QUANTIZATION` | Smoke Test Unknown Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.46s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_CONTEXT` | Smoke Test Wrong Context | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.48s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_CONTEXT_OPERATOR_OVERRIDE_REJECTED` | Smoke Test Wrong Context Operator Override Rejected | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.48s | **PASSED** | PASS |
| `SMOKE_TEST_UNKNOWN_CONTEXT_OPERATOR_FLAG_UNCERTIFIED` | Smoke Test Unknown Context Operator Flag Uncertified | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `2` | 1.48s | **PASSED** | PASS |
| `SMOKE_HARDENING_FULL_SUITE` | Smoke Hardening Full Suite | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 9.92s | **PASSED** | 31 pass |
| `STREAMLIT_APPTEST_FLOWS` | Streamlit Apptest Flows | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 16.63s | **PASSED** | 7 pass |
| `FULL_PYTEST_SUITE` | Full Pytest Suite | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 171.87s | **PASSED** | 1654 pass |
| `PYTHON_COMPILEALL` | Python Compileall | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 1.74s | **PASSED** | PASS |
| `WHEEL_BUILD` | Wheel Build | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 29.56s | **PASSED** | PASS |
| `WHEEL_INSPECTION` | Wheel Inspection | `zipfile.namelist whatsapp_forensic_importer-0.1.0-py...` | `0` | 0.05s | **PASSED** | PASS |
| `ISOLATED_WHEEL_IMPORT` | Isolated Wheel Import | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 1.56s | **PASSED** | PASS |
| `CHECK_REPO_ZERO_PYC` | Check Repo Zero Pyc | `find -name *.pyc` | `0` | 0.10s | **PASSED** | PASS |
| `FROZEN_BENCHMARK_INTEGRITY` | Frozen Benchmark Integrity | `git diff HEAD -- ai/experiment.py ai/benchmark.py be...` | `0` | 0.32s | **PASSED** | PASS |

---

## 3. ANALISI DELLE SALVAGUARDIE (SCIENTIFIC EVIDENCE GUARD)

### 3.1 Matrice di Comportamento Quantizzazione
| Scenario | Server API | Flag Operatore | Esito Gate | Exit Code | Note di Salvaguardia |
|---|---|---|:---:|:---:|---|
| Conforme | `Q4_K_M` (object/string) | Nessuno | **PASSED** | 0 | Evidenza server certificata. |
| Mismatch dichiarato | `Q8_0` | Nessuno | **FAILED** | 1 | Rifiuto categorico quantizzazione inadatta. |
| Mismatch con override | `Q8_0` | `--confirmed-quantization Q4_K_M` | **FAILED** | 1 | **Override operatore respinto**: il server prevale sui metadati discordanti. |
| Dedotto dal nome | `MODEL_KEY_INFERRED` | Nessuno | **NOT_VERIFIED** | 2 | Deduzione dal nome non costituisce prova scientifica. |
| Dedotto con flag | `MODEL_KEY_INFERRED` | `--confirmed-quantization Q4_K_M` | **NOT_VERIFIED** | 2 | **Nessuna promozione a PASS**: dichiarazione registrata come supplementare. |
| Sconosciuto | `UNKNOWN` / None | Nessuno | **NOT_VERIFIED** | 2 | Assenza di metadati API. |
| Sconosciuto con flag | `UNKNOWN` / None | `--confirmed-quantization Q4_K_M` | **NOT_VERIFIED** | 2 | **Nessuna promozione a PASS**: gate automatico bloccato. |

### 3.2 Matrice di Comportamento Context Length
| Scenario | Server API | Flag Operatore | Esito Gate | Exit Code | Note di Salvaguardia |
|---|---|---|:---:|:---:|---|
| Conforme | 8192 (runtime instance) | Nessuno | **PASSED** | 0 | Contesto server verificato. |
| Mismatch dichiarato | 4096 | Nessuno | **FAILED** | 1 | Contesto insufficiente per benchmark. |
| Mismatch con override | 4096 | `--confirmed-context 8192` | **FAILED** | 1 | **Override operatore respinto**: il server prevale. |
| Sconosciuto | `UNKNOWN` / None | Nessuno | **NOT_VERIFIED** | 2 | Nessuna informazione di context esposta. |
| Sconosciuto con flag | `UNKNOWN` / None | `--confirmed-context 8192` | **NOT_VERIFIED** | 2 | **Nessuna promozione a PASS**: dichiarazione annotata nei log. |

---

## 4. CASO DI CONTROLLO: "LOGISTICA E ISPEZIONE DEPOSITI"

- **Topic:** `Logistica e Ispezione Depositi`
- **Descrizione:** `Ispezione visiva del magazzino merci con casse sigillate e sicurezza`
- **Evidenza Target:** `msg::5::VISION_DESCRIPTION`
- **Principio di Integrità Scientifica:**
  - La Topic Detection esegue una reale inferenza indipendente sul documento inviando unicamente `label` e `short_description`.
  - Non viene iniettato alcun suggerimento di evidence IDs nel prompt.
  - Se il modello risponde `PRESENT`, vengono verificati la citazione dell'evidenza e il campo `rationale`.
  - Se il modello risponde `ABSENT`, **il risultato empirico viene preservato fedelmente e non forzato a PRESENT**. Non costituisce errore operativo ma evidenza sperimentale del comportamento del modello.

---

## 5. STATO DEL BENCHMARK SCIENTIFICO CONGELATO

In conformità tassativa con il protocollo della tesi:
- `ai/experiment.py`: **INVARIATO (Zero Diff contro HEAD)**
- `ai/benchmark.py`: **INVARIATO (Zero Diff contro HEAD)**
- Dataset benchmark e Ground Truth: **INVARIATI**
- Prompt e metriche del benchmark: **INVARIATI**
- Parametri congelati:
  - Modelli: `qwen2.5-7b-instruct`, `meta-llama-3.1-8b-instruct`, `deepseek-r1-distill-qwen-7b`
  - Quantizzazione: `Q4_K_M`
  - Context length: `8192`
  - Timeout: `240` s
  - Max tokens: `512`
  - Temperature: `0.0`
- `run_benchmark.sh`:
  - Git index mode: **`100755` (eseguibile)**
  - Fine riga: **`LF` pulito** (nessun byte CRLF)
  - Sintassi Bash: verificata con `bash -n` (Exit Code 0).
  - **I 9 benchmark finali NON sono stati avviati.**

---

## 6. PIANO DI COMMIT CONTROLLATO E HANDOFF UBUNTU

### 6.1 Comandi Git Preparati per Windows (da eseguire a cura dell'operatore)
```powershell
git add ai/lmstudio.py
git add scripts/smoke_operational_ai.py
git add tests/unit/test_model_resolution_audit.py
git add tests/unit/test_smoke_operational_ai.py
git add tests/unit/test_lmstudio_native_integration.py
git add UBUNTU_FINAL_SMOKE_RUNBOOK.md
git add UBUNTU_HANDOFF_GUARDRAILS_REPORT.md
git add scripts/build_ubuntu_handoff_guardrails_bundle.py

git commit -m "fix(operational): implement Scientific Evidence Guard and final Ubuntu handoff runbook"
git push origin main
```

### 6.2 Sequenza Operativa sul PC Ubuntu di Laboratorio
1. `git pull origin main` e verifica `test -z "$(git status --porcelain)"`.
2. Attivazione virtualenv: `source .venv/bin/activate` (no conda base).
3. Esecuzione suite di test su Ubuntu: `pytest -v`.
4. Avvio LM Studio su `127.0.0.1:1234` con Qwen2.5 Q4_K_M, offload GPU e context 8192.
5. Esecuzione smoke test live:
   ```bash
   python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct --required-quantization Q4_K_M
   ```
6. Verifica esito live:
   - Se Exit 0 (`LIVE_VERIFIED`): procedere all'avvio del benchmark congelato (`bash run_benchmark.sh`).
   - Se Exit 2 (`NOT_VERIFIED`): verificare metadati e porta LM Studio.
   - Se Exit 1 (`FAILED`): anomalia nei parametri caricati.

---

## 7. VERDETTO FINALE DELL'AUDIT

Tutti i release gates sono stati eseguiti e superati con successo:
- **Verdetto Generale:** **PASSED**
- **Wheel Pulita:** `whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (zero bytecode .pyc)
- **Zero Bytecode nel Source Tree:** Verificato (0 file `.pyc`)
- **Prossimo Passo:** Esecuzione del commit controllato e passaggio diretto al PC Ubuntu 22.04.5 LTS di laboratorio senza ulteriori refactor preventivi.
