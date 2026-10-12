# CHATANALYSIS — STRICT MODEL INSTANCE & QUANTIZATION RELEASE GATE REPORT

**Audit Date:** 2026-10-11 16:41:43 UTC  
**Auditor:** Antigravity AI Release Engineering  
**Environment:** Windows 11 Enterprise (Verification Workstation)  
**Target Environment:** Ubuntu 22.04.5 LTS / Intel i5-9600K / 31 GB RAM / NVIDIA GTX TITAN X 12 GB / Python 3.12.4  
**Target Artifact:** `chatAnalysis_native_model_selection_gate_audit_20261011.zip`  
**Overall Verdict:** **PASSED**  

---

## 1. EXECUTIVE SUMMARY & INTENT

Il presente audit certifica la correzione rigorosa e definitiva di **due difetti circoscritti** nel model resolver operativo di ChatAnalysis (`ai/lmstudio.py`) e nello script di validazione live/mock (`scripts/smoke_operational_ai.py`), in conformità allo schema REST nativo ufficiale di LM Studio (`/api/v1/models` e `/api/v1/models/load`):

1. **BLOCKER 1 — Target Instance ID inesistente causava fallback silenzioso:**
   - **Root Cause:** In precedenza, se `target_instance_id` non veniva trovato tra le istanze caricate, il resolver continuava l'esecuzione e selezionava arbitrariamente `c.loaded_instances[0]`, dichiarando `is_loaded=True`, creando un binding spurio e attribuendo al target il context length di un'altra istanza.
   - **Correzione applicata:** `target_instance_id` è ora trattato come vincolo obbligatorio ed esclusivo. Se l'istanza non esiste, solleva `AiModelNotInstalledError`. Se appartiene a un'altra famiglia di modelli, solleva `AiModelMismatchError`. Nessun fallback silenzioso, nessun binding spurio, nessun context spurio. Quando non è specificato, se più istanze caricate sono compatibili e indistinguibili, solleva `AiModelAmbiguousError`.

2. **BLOCKER 2 — Quantization Parsing e Selezione Q4_K_M:**
   - **Root Cause:** LM Studio REST espone la quantizzazione come dizionario JSON (es. `{"name": "Q4_K_M", "bits_per_weight": 4}`). In precedenza `_normalize_model_item()` eseguiva `str(item.get("quantization"))` producendo la rappresentazione testuale del dizionario, non il nome canonico. Inoltre, il resolver preferiva qualsiasi versione già caricata (anche `Q8_0`) rispetto a una `Q4_K_M` installata ma non ancora caricata.
   - **Correzione applicata:** Implementata la funzione di estrazione canonica `extract_quantization_name(raw)` che supporta dizionari REST ufficiali (`{"name": "Q4_K_M"}`), stringhe canoniche e stringhe JSON serializzate. Separati rigorosamente nei metadati e nel resolver: `requested_quantization`, `available_quantization`, `selected_quantization` e `quantization_evidence_source`. Se `Q4_K_M` è richiesta ed è installata (anche unloaded), il resolver seleziona `Q4_K_M` per il caricamento e non adotta silenziosamente `Q8_0`. Nello smoke test scientifico e benchmark, `Q4_K_M` è obbligatoria; `Q8_0` viene rifiutata con exit code 1 (`FAILED`).

---

## 2. TABELLA SINOTTICA DELLE VERIFICHE DEI RELEASE GATES

| Gate ID | Nome Verifica / Test | Comando | Exit Code | Durata (s) | Esito | Dettaglio / Note |
|---|---|---|:---:|:---:|:---:|---|
| `BLOCKER1_INSTANCE_SELECTION_TESTS` | Blocker1 Instance Selection Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 6.89s | **PASSED** | 12 pass |
| `BLOCKER2_QUANTIZATION_TESTS` | Blocker2 Quantization Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.76s | **PASSED** | 14 pass |
| `SIMULATED_NATIVE_INTEGRATION_TESTS` | Simulated Native Integration Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 4.95s | **PASSED** | 3 pass |
| `TOPIC_DISCOVERY_DETECTION_FLOW_TESTS` | Topic Discovery Detection Flow Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 5.81s | **PASSED** | 12 pass |
| `MULTI_CONVERSATION_ISOLATION_TESTS` | Multi Conversation Isolation Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 11.86s | **PASSED** | 1 pass |
| `SMOKE_TEST_MOCK_NORMAL` | Smoke Test Mock Normal | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 2.78s | **PASSED** | PASS |
| `SMOKE_TEST_NONEXISTENT_INSTANCE` | Smoke Test Nonexistent Instance | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 2.29s | **PASSED** | PASS |
| `SMOKE_TEST_WRONG_QUANTIZATION` | Smoke Test Wrong Quantization | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `1` | 1.66s | **PASSED** | PASS |
| `SMOKE_HARDENING_TESTS` | Smoke Hardening Tests | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 9.39s | **PASSED** | 22 pass |
| `STREAMLIT_APPTEST_FLOWS` | Streamlit Apptest Flows | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 16.91s | **PASSED** | 7 pass |
| `FULL_PYTEST_SUITE` | Full Pytest Suite | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 186.84s | **PASSED** | 1625 pass |
| `PYTHON_COMPILEALL` | Python Compileall | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 2.45s | **PASSED** | PASS |
| `WHEEL_BUILD` | Wheel Build | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 32.92s | **PASSED** | PASS |
| `WHEEL_INSPECTION` | Wheel Inspection | `zipfile.namelist whatsapp_forensic_importer-0.1.0-py...` | `0` | 0.05s | **PASSED** | PASS |
| `ISOLATED_WHEEL_IMPORT` | Isolated Wheel Import | `C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe...` | `0` | 1.69s | **PASSED** | PASS |
| `CHECK_REPO_ZERO_PYC` | Check Repo Zero Pyc | `find -name *.pyc` | `0` | 0.10s | **PASSED** | PASS |
| `FROZEN_BENCHMARK_INTEGRITY` | Frozen Benchmark Integrity | `git diff HEAD -- ai/experiment.py ai/benchmark.py be...` | `0` | 0.61s | **PASSED** | PASS |

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
    - `{"name": "Q4_K_M", "bits_per_weight": 4}` -> estrae `"Q4_K_M"`, evidence source: `SERVER_API_QUANTIZATION_OBJECT`.
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
  {
    "key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF",
    "display_name": "Qwen2.5-7B-Instruct",
    "quantization": {
      "name": "Q4_K_M",
      "bits_per_weight": 4
    },
    "max_context_length": 32768,
    "loaded_instances": [
      {
        "id": "qwen-instance-8192",
        "config": {
          "context_length": 8192
        }
      }
    ]
  }
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
  - `test_simulated_native_integration_negative_wrong_quantization`: payload con `quantization: {"name": "Q8_0"}` rileva il mismatch rispetto alla quantizzazione richiesta `Q4_K_M`.

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

Il runbook [UBUNTU_FINAL_SMOKE_RUNBOOK.md](file:///c:/Users/lucas/Desktop/Tesi/UBUNTU_FINAL_SMOKE_RUNBOOK.md) è stato aggiornato con:
- Verifica preliminare della quantizzazione:
  ```bash
  curl -s http://127.0.0.1:1234/api/v1/models | jq '.models[] | {key: .key, quantization: .quantization, instances: .loaded_instances}'
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
- **Verdetto:** **PASSED**
- **Wheel Pulita:** `whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (zero bytecode .pyc)
- **Zero Bytecode nel Source Tree:** Verificato
- **Prossimo Passo:** Collaudo live sul PC di laboratorio Ubuntu 22.04.5 LTS (senza ulteriori refactor preventivi).
