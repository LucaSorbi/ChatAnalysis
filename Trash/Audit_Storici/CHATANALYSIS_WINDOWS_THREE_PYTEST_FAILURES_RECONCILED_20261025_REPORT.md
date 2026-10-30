# CHATANALYSIS — RAPPORTO TECNICO AUDIT E RICONCILIAZIONE TEST PYTEST SU WINDOWS
**Data:** 25 ottobre 2026  
**Ambiente di Riferimento:** Windows 11 Pro, Python 3.12.10 64-bit, pytest 9.1.1  
**Workspace:** `C:\Users\Luca\Desktop\Tesi\ChatAnalysis`  
**Archivio Consegna:** `chatAnalysis_windows_three_pytest_failures_reconciled_20261025.zip` (posizionato direttamente in `C:\Users\Luca\Desktop\`)  
**Esito Finale:** SUCCESSO COMPLETO — 0 fallimenti (2133 passed, 3 skipped, 5 deselected, 2 warnings).  

---

## 1. RIPRODUZIONE INIZIALE E STATO PRE-INTERVENTO

All'avvio dell'attività sull'ambiente locale Windows con interprete Python 3.12.10 (`.venv312`), l'esecuzione preliminare della suite di test completa ha riprodotto fedelmente i 3 fallimenti segnalati:

- **Comando:** `.\.venv312\Scripts\python.exe -m pytest tests/unit/test_operational_integration_audit.py::test_14_partial_and_complete_analysis_reporting tests/unit/test_subchunk_split_recovery_and_coverage.py::test_subchunk_persistence_and_recovery_without_duplicate_inference tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py::test_accurate_observed_metrics_attempts_finish_reason_http_status`
- **Esito riproduzione mirata:** 3 failed in 1.87s (Exit Code: 1).
- **Esito suite completa iniziale:** `3 failed, 2130 passed, 3 skipped, 5 deselected, 2 warnings in 184.40s` (Exit Code: 1).

### Dettaglio dei 3 fallimenti originari:
1. `tests/unit/test_operational_integration_audit.py::test_14_partial_and_complete_analysis_reporting`:
   - Linea 488: `assert metadata.status == "PARTIAL"` fallita: valore effettivo `"SUCCESS"`.
2. `tests/unit/test_subchunk_split_recovery_and_coverage.py::test_subchunk_persistence_and_recovery_without_duplicate_inference`:
   - Linea 424: `assert calls_for_sub1 == 1` fallita: valore effettivo `2`.
3. `tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py::test_accurate_observed_metrics_attempts_finish_reason_http_status`:
   - Linea 908: `assert diag0['attempts'] == 1` fallita: valore effettivo `2`.

---

## 2. DIAGNOSI E DISTINZIONE TRA OBSOLESCENZA DEI TEST E BUG DI MOTORE

### Test 1 — `test_14_partial_and_complete_analysis_reporting`
- **Natura:** *Test mock non conforme alla semantica operativa dei retry*.
- **Root Cause Accertata:**
  Nelle pipeline operative (`ai/topics.py`), la gestione degli errori strutturati (`AiStructuredOutputError`) adotta una policy di resilienza controllata con `max_attempts=2`. Nel test originario, il mock `ControlledMockClient` riceveva un'unica risposta di errore (`AiStructuredOutputError("JSON invalido irreversibile")`) destinata al Chunk 1. Al verificarsi dell'errore al tentativo 1, il motore attivava legittimamente il secondo tentativo; tuttavia, il mock possedeva una logica di fallback silente:
  ```python
  if not self.responses:
      return {"decision": "ABSENT", "confidence": 0.0, ...}
  ```
  Esaurita la lista di risposte programmate, il mock non segnalava l'anomalia ma restituiva arbitrariamente un esito positivo (`ABSENT`), convertendo il chunk fallito in successo. Di conseguenza, tutti i chunk risultavano completati e `metadata.status` assumeva `"SUCCESS"` invece di `"PARTIAL"`.
- **Risoluzione Applicata:**
  1. **Hardening del mock:** `ControlledMockClient` ora solleva tassativamente `RuntimeError("ControlledMockClient: risposte programmate esaurite...")` se la coda programmata viene consumata oltre il previsto, impedendo successi silenziosi non dichiarati.
  2. **Isolamento dei 4 scenari contrattuali:**
     - Scenario (i): Errore strutturato transitorio al 1° tentativo e risposta valida al 2° tentativo -> `status == "SUCCESS"`, `attempts == 2`.
     - Scenario (ii): Errore strutturato persistente su entrambi i tentativi (2 risposte di errore fornite nel mock) -> `status == "PARTIAL"`, `decision == "UNCERTAIN"`, chunk marcato fallito con `attempts == 2`.
     - Scenario (iii): Errore permanente non recuperabile (HTTP 400 / `HTTP_INVALID_PARAMETERS`) -> fail-closed immediato al 1° tentativo senza retry (`attempts == 1`), `status == "PARTIAL"`.
     - Scenario (iv): Baseline di controllo in cui tutti i chunk completano con successo -> `status == "SUCCESS"`, `is_complete is True`.

### Test 2 — `test_subchunk_persistence_and_recovery_without_duplicate_inference`
- **Natura:** *Duplice: aspettativa di test obsoleta + difetto funzionale nel motore (`analysis/service.py`)*.
- **Root Cause Accertata:**
  1. *Obsolescenza del test:* Il subchunk `Sub1` simulava un errore transitorio (`AiBackendRequestError("Temporary network glitch on Sub1")`). La Discovery operativa con `max_attempts=2` ritenta giustamente la richiesta transitoria: la Run 1 esegue quindi 2 chiamate per `Sub1` (1° tentativo fallito + 2° tentativo fallito). Il test assumeva un'architettura senza retry e pretendeva rigidamente `assert calls_for_sub1 == 1` alla fine del Run 1.
  2. *Difetto reale nel motore:* In `analysis/service.py` (`_process_subchunks_discovery`), il blocco che cattura l'eccezione di un subchunk impostava `attempts=1` in modo hardcoded nel dizionario diagnostico (`sub_diag`) e nell'oggetto `DiscoveryChunkOutcome(attempts=1)`. Qualora il client avesse eseguito 2 tentativi, il motore sovrascriveva arbitrariamente il conteggio reale a 1, violando l'invariante di veridicità e osservabilità.
- **Risoluzione Applicata:**
  1. **Correzione funzionale del motore (`analysis/service.py`):**
     Nel metodo `_process_subchunks_discovery`, introdotto il tracking locale dei retry tramite callback `_on_sub_retry` passata a `run_chunk_topic_discovery`. Alla cattura dell'eccezione del subchunk, il valore di `attempts` viene desunto da `eff_sub_att = int(getattr(s_exc, "attempts", sub_attempts))` e propagato sia a `sub_diag["attempts"]` sia all'outcome `DiscoveryChunkOutcome(attempts=eff_sub_att)`.
     Simmetricamente, in `run_topic_detection`, il fallback generico assegna `attempts=int(getattr(exc, "attempts", chunk_attempts))`.
  2. **Riconciliazione del test:**
     - Alla fine della Run 1, l'asserzione attende coerentemente `calls_for_sub1 == 2` (1° tentativo + retry operativo).
     - Per verificare in modo inequivocabile l'assenza di duplicazione dell'inferenza dopo il riavvio (Run 2), è stato introdotto il tracking dei differenziali (delta) per-run:
       - Delta chiamate `Sub0`: `calls_run2_sub0 - calls_run1_sub0 == 0` (ripescato al 100% dal checkpoint).
       - Delta chiamate `Chunk1`: `calls_run2_chunk1 - calls_run1_chunk1 == 0` (ripescato dal checkpoint).
       - Delta chiamate `Sub1`: `calls_run2_sub1 - calls_run1_sub1 == 1` (esattamente 1 inferenza di successo al riavvio, per un totale complessivo di 3 chiamate).

### Test 3 — `test_accurate_observed_metrics_attempts_finish_reason_http_status`
- **Natura:** *Test difettoso per contaminazione di stato globale tra chunk distinti*.
- **Root Cause Accertata:**
  Il mock `ScriptedFailureClient` gestiva le risposte in base a un indice sequenziale globale `self.calls`:
  ```python
  self.calls += 1
  if self.calls == 1:
      raise TimeoutError(...)
  elif self.calls == 2:
      raise HTTPError(500, ...)
  else:
      return success_response
  ```
  Nel disegno concettuale del test, la chiamata 1 era intesa per Chunk 0, la chiamata 2 per Chunk 1 e le successive per Chunk 2. Tuttavia, poiché il motore esegue i retry operativi per chunk (`max_attempts=2`), il Chunk 0 incorreva nel timeout alla chiamata 1 e veniva ritentato immediatamente: il suo 2° tentativo consumava la chiamata 2 (ottenendo HTTP 500). Di conseguenza, il Chunk 1 riceveva la chiamata 3 (che restituiva successo inaspettato!).
  Questo contaminava completamente l'assegnazione: il Chunk 0 terminava con 2 tentativi ed errore HTTP 500, mentre il Chunk 1 risultava completato con successo al 1° tentativo. Le asserzioni del test (`diag0['attempts'] == 1`, `diag0['diagnostic_category'] == TIMEOUT`) fallivano di conseguenza.
- **Risoluzione Applicata:**
  1. **Disaccoppiamento per identità di chunk:** `ScriptedFailureClient` è stato riprogettato per smistare le risposte in base all'identità del chunk (riconosciuta tramite regex deterministica su `bundle X` / `msg_doc_obs_metrics_X` presente nei messaggi del prompt) e a un contatore di tentativi *isolato per ciascun chunk*:
     - Chunk 0: solleva `TimeoutError` per tutti i tentativi (esegue 2 tentativi, `attempts == 2`, `http_status is None`, `diagnostic_category == "TIMEOUT"`).
     - Chunk 1: solleva HTTP 500 per tutti i tentativi (esegue 2 tentativi, `attempts == 2`, `http_status == 500`, `finish_reason is None`).
     - Chunk 2: restituisce successo immediato al 1° tentativo (`attempts == 1`, `http_status == 200`, `finish_reason == "stop"`).
  2. **Aggiunta di casi di verifica isolati integrati:**
     - Caso (a): Timeout transitorio al 1° tentativo seguito da successo al 2° tentativo -> `attempts == 2`, `finish_reason == "stop"`, chunk completato con successo.
     - Caso (b): Errore permanente HTTP 400 (`HTTP_INVALID_PARAMETERS`) su documento con 2 bundle -> fail-closed immediato al 1° tentativo senza retry (`attempts == 1`, `http_status == 400`), status risultante `"PARTIAL"`.

---

## 3. TRACCIATURA CRONOLOGICA DELLE CHIAMATE A `chat_completion`

Di seguito la tracciatura cronologica sintetica e anonimizzata (zero log di messaggi, prompt, PII o chiavi) che documenta la corrispondenza esatta tra chunk, tentativi, classe di errore ed esiti osservati:

| Test / Scenario | Run | Chunk ID / Subchunk ID | Attempt | Esito / Classe Errore | HTTP Status Osservato | Finish Reason | Da Checkpoint |
| :--- | :---: | :--- | :---: | :--- | :---: | :---: | :---: |
| **Test 1 — Caso (i)** | 1 | `bundle_0` | 1 | `AiStructuredOutputError` (JSON malformato) | None | None | No |
| | 1 | `bundle_0` | 2 | Successo (`PRESENT`) | 200 | `stop` | No |
| **Test 1 — Caso (ii)** | 1 | `bundle_0` | 1 | Successo | 200 | `stop` | No |
| | 1 | `bundle_1` | 1 | `AiStructuredOutputError` | None | None | No |
| | 1 | `bundle_1` | 2 | `AiStructuredOutputError` | None | None | No |
| **Test 1 — Caso (iii)**| 1 | `bundle_0` | 1 | Successo | 200 | `stop` | No |
| | 1 | `bundle_1` | 1 | `AiBackendRequestError` (HTTP 400) | 400 | None | No |
| **Test 2** | 1 | `chunk_0_sub_0` | 1 | Successo (`discovery_subchunk`) | 200 | `stop` | No |
| | 1 | `chunk_0_sub_1` | 1 | `AiBackendRequestError` (Glitch) | None | None | No |
| | 1 | `chunk_0_sub_1` | 2 | `AiBackendRequestError` (Glitch) | None | None | No |
| | 1 | `chunk_1` | 1 | Successo | 200 | `stop` | No |
| | 2 | `chunk_0_sub_0` | — | *Non invocato* (Zero inferenze) | — | — | **Sì** |
| | 2 | `chunk_0_sub_1` | 1 (tot 3)| Successo (`discovery_subchunk`) | 200 | `stop` | No |
| | 2 | `chunk_1` | — | *Non invocato* (Zero inferenze) | — | — | **Sì** |
| **Test 3 — Multi-chunk**| 1 | `chunk_0` (Bundle 0) | 1 | `TimeoutError` | None | None | No |
| | 1 | `chunk_0` (Bundle 0) | 2 | `TimeoutError` | None | None | No |
| | 1 | `chunk_1` (Bundle 1) | 1 | `AiBackendRequestError` (HTTP 500) | 500 | None | No |
| | 1 | `chunk_1` (Bundle 1) | 2 | `AiBackendRequestError` (HTTP 500) | 500 | None | No |
| | 1 | `chunk_2` (Bundle 2) | 1 | Successo | 200 | `stop` | No |
| **Test 3 — Caso (a)** | 1 | `chunk_0` | 1 | `TimeoutError` | None | None | No |
| | 1 | `chunk_0` | 2 | Successo | 200 | `stop` | No |
| **Test 3 — Caso (b)** | 1 | `chunk_0` | 1 | Successo | 200 | `stop` | No |
| | 1 | `chunk_1` | 1 | `AiBackendRequestError` (HTTP 400) | 400 | None | No |

---

## 4. ACCOUNTING ESAUSTIVO DEI TEST, SKIP E DESELEZIONI

### Riepilogo Esecuzioni Pytest

1. **Test mirati sui 3 file modificati:**
   - **Comando:** `.\.venv312\Scripts\python.exe -m pytest tests/unit/test_operational_integration_audit.py tests/unit/test_subchunk_split_recovery_and_coverage.py tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py -q`
   - **Risultato:** `36 passed in 10.73s` (Exit Code: 0).
2. **Suite focalizzata (topic, chunking, checkpoint, diagnostica, contabilità metrica):**
   - **Comando:** `.\.venv312\Scripts\python.exe -m pytest tests/unit/test_ai_topic_discovery.py tests/unit/test_ai_topic_detection.py tests/unit/test_topic_discovery_detection_stabilization.py tests/unit/test_discovery_detection_invariants.py tests/unit/test_subchunk_split_recovery_and_coverage.py tests/unit/test_operational_integration_audit.py tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py tests/unit/test_checkpoint_safe_diagnostics.py tests/unit/test_performance_accounting_audit.py -q`
   - **Risultato:** `321 passed, 2 skipped in 90.17s` (Exit Code: 0).
3. **Suite completa di regressione del repository:**
   - **Comando:** `.\.venv312\Scripts\python.exe -m pytest -q`
   - **Risultato:** `2133 passed, 3 skipped, 5 deselected, 2 warnings in 196.29s (0:03:16)` (Exit Code: 0).

### Motivazione Dettagliata delle Deselezioni (5 deselected)
Le 5 deselection sono determinate dalla configurazione standard di `pyproject.toml`:
```toml
addopts = [
    "-m", "not smoke",
    ...
]
```
I test marcati `@pytest.mark.smoke` sono test di integrazione/smoke a lunga durata riservati ai workflow specifici di verifica runtime e vengono disattivati nella suite unitaria ordinaria.

### Motivazione Dettagliata dei Test Saltati (3 skipped)
Tutti e 3 gli skip derivano da vincoli intrinseci dell'ambiente host Windows rispetto a semantiche POSIX/Linux e sono formalmente dichiarati nel codice:
1. `tests/unit/test_smoke_operational_ai.py:184`:
   - *Messaggio:* `"Bash nativo non disponibile in questo ambiente Windows"`
   - *Causa:* Verifica l'invocazione di script Bash POSIX; l'ambiente operativo corrente è Windows Powershell.
2. `tests/unit/test_checkpoint_preflight_resume.py:2722`:
   - *Messaggio:* `"Chmod 0o400 non impedisce la scrittura in questo filesystem (es. Windows NTFS)"`
   - *Causa:* Il filesystem Windows NTFS gestisce i permessi tramite ACL e non rispetta la semantica restrittiva UNIX di `os.chmod(p, 0o400)` per l'utente proprietario.
3. `tests/unit/test_checkpoint_preflight_resume.py:4622`:
   - *Messaggio:* `"La sostituzione concorrente di un file aperto con descrittore attivo è una semantica specifica POSIX/Linux."`
   - *Causa:* Su Windows il lock a livello di OS impedisce l'operazione di `replace` atomico su file aperti da un file handle concorrente (`WinError 32: Accesso negato`).

---

## 5. CONSERVAZIONE DEI VINCOLI SCIENTIFICI E ARCHITETTURALI

- **Invarianti Scientifiche:** Nessuna alterazione ai file `ai/benchmark.py`, `ai/experiment.py`, `run_benchmark.sh`, né a dataset, ground truth o parametri congelati (quantizzazione `Q4_K_M`, context length `8192`, temperatura `0.0`, `max_tokens 512`, `timeout 240s`).
- **Controllo di Versione:** Nessun comando git di mutazione (`commit`, `push`, `pull`, `reset`) eseguito.
- **Isolamento Forense:** Nessun dato o checkpoint reale/personale cancellato o alterato; l'analisi ha operato unicamente con mock sintetici isolati in memoria.
- **Accuratezza delle Metriche:** Il valore `http_status` non viene inventato: rimane `None` per timeout o errori di rete pre-connessione, assume il codice esatto (es. `500`, `400`) quando osservato, e `200` per risposte convalidate. Nessun backoff lungo è stato introdotto nei test (tutti i `time.sleep` sono disattivati via patch mirata).

---

## 6. PACCHETTO DI CONSEGNA E VERIFICA DEL MANIFEST SHA-256

Conformemente al requisito 10:
- **Nome archivio:** `chatAnalysis_windows_three_pytest_failures_reconciled_20261025.zip`
- **Posizione fisica:** Creato **DIRETTAMENTE** all'esterno del repository e all'esterno della cartella `Tesi`, nella cartella Desktop dell'utente:
  `C:\Users\Luca\Desktop\chatAnalysis_windows_three_pytest_failures_reconciled_20261025.zip`
- **File di checksum esterno:** `C:\Users\Luca\Desktop\chatAnalysis_windows_three_pytest_failures_reconciled_20261025.zip.sha256`
- **Manifest interno:** `manifest_sha256.txt` integrato nella radice dell'archivio con percorsi POSIX normalizzati per tutti i 371 file inclusi.
- **Esito verifica integrità (`scripts/verify_package_manifest.py`):** `VERIFIED_PASS` (100% hash corrispondenti, zero duplicati, zero percorsi non conformi, compilazione `compileall` superata).
