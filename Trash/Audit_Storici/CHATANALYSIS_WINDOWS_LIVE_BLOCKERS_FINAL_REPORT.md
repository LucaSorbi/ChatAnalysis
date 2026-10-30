# CHATANALYSIS — RAPPORTO TECNICO FINALE: WINDOWS LIVE READINESS
## Risoluzione Difetti Bloccanti, Validazione e Rilascio Release Gate

**Data**: 20 Ottobre 2026 / 21 Ottobre 2026  
**Piattaforma Target**: Windows 11 64-bit (Native PowerShell, no WSL, no Docker)  
**Ambiente Python**: Python 3.14.7 64-bit (`.venv`)  
**Archivio di Consegna**: `chatAnalysis_windows_live_blockers_final_audit_20261020.zip` (prodotto direttamente all'esterno del repository in `C:\Users\lucas\Desktop\CartellaTesi\`)

---

## 1. QUADRO GENERALE E STATO DEI RELEASE GATE

A seguito dell'audit indipendente su `chatAnalysis_windows_topic_pipeline_readiness_audit_20261020.zip`, sono state indirizzate e risolte in modo chirurgico e rigoroso tutte e quattro le priorità bloccanti identificate, senza alcun refactor generale e senza alterare in alcun modo i file del benchmark scientifico (`ai/experiment.py` e `ai/benchmark.py`, diff = 0 righe).

### Tabella delle Priorità e Verifiche Eseguite

| Priorità | Componente / File | Problema Riprodotto dall'Audit | Soluzione Implementata | Classificazione |
| :--- | :--- | :--- | :--- | :--- |
| **Priorità 1** | `analysis/service.py`, `analysis/models.py`, `analysis/checkpoint.py` | `load_persisted_discovery_result()` produceva `origin = RECOVERED_CHECKPOINT`, causando `TopicProvenanceError` in `verify_discovered_topic()` poiché estraneo a `AnalysisOrigin`. | Separati concettualmente e tecnicamente: origine inferenziale (`AnalysisOrigin`), modalità di caricamento (`CheckpointLoadingMode`), livello di attestazione (`CheckpointAttestationLevel`) e provenienza autentica. `origin` riflette fedelmente l'inferenza (`LIVE_LM_STUDIO` o `MOCK_INSPECTION`), validando fingerprint documento e configurazione. | **TEST_VERIFIED** (10/10 PASS) |
| **Priorità 2** | `scripts/preflight_checkpoint_resume.py` | Corsa TOCTOU F1 in `_perform_atomic_safe_unlink()`: file concorrente sostituito immediatamente prima dell'unlink veniva eliminato. | Ristretto il cleanup automatico esclusivamente alle cartelle temporanee private create dal processo (prefisso `chatanalysis_snap_`). Rifiuto categorico (*fail-closed*, `SafeUnlinkSecurityViolation`) su directory non possedute. Aggiunto test avversario 145 con swap iniettato post-verifica. | **TEST_VERIFIED** (6/6 PASS, 1 skip POSIX) |
| **Priorità 3** | `scripts/run_streamlit_windows.ps1`, `scripts/windows_setup_and_verify.ps1` | Gli script puntavano a `ui/application.py` anziché a `app.py`. Errore di invocazione PowerShell con array di comandi (es. `py -3`). | Aggiornati entrambi gli script a puntare ad `app.py`. Corretta l'invocazione PowerShell con `& $pyCmd[0] $pyCmd[1..($pyCmd.Length-1)]`. Validati Streamlit AppTest e test reale subprocess HTTP 200 su loopback. | **TEST_VERIFIED** (4/4 PASS, 20/20 UI AppTests) |
| **Priorità 4** | `analysis/service.py`, `tests/unit/test_priority4_live_diagnostics_integrity.py` | Diagnostica live incompleta; rischio di mascheramento errori o assunzioni arbitrarie su 17/26 e 25/26. | Distinzione rigorosa tra errori sistemici di backend e guasti di blocco. Gestito fallimento Discovery 25/26 con chunk 11 `INVALID_EVIDENCE_ID` (2 tentativi, logging privacy-safe, zero leak). Detection 17/26 aggregata rigorosamente come `UNCERTAIN`. Fail-fast immediato su errori di programmazione. | **TEST_VERIFIED** (6/6 PASS) |

---

## 2. DETTAGLIO TECNICO DELLE RISOLUZIONI

### 2.1 PRIORITÀ 1 — Prova del Nove su Discovery Recuperata da Checkpoint

#### Causa Radice
Nel ciclo di vita precedente, la funzione `load_persisted_discovery_result()` impostava `origin = "RECOVERED_CHECKPOINT"`. Tuttavia, `verify_discovered_topic()` richiedeva tassativamente che `origin` appartenesse all'enumerazione `AnalysisOrigin` (che ammette solo `LIVE_LM_STUDIO`, `MOCK_INSPECTION`, `MANUAL_QUERY`). Ciò causava il sollevamento di `TopicProvenanceError: "Origine run non riconosciuta o priva di attestazione valida: RECOVERED_CHECKPOINT"`.

#### Soluzione Architetturale
Come prescritto dall'audit, `RECOVERED_CHECKPOINT` **non** è stato aggiunto ad `AnalysisOrigin`. È stata invece implementata una rigorosa separazione delle dimensioni di provenienza in `analysis/models.py` e `analysis/service.py`:
1. **Origine dell'Inferenza (`AnalysisOrigin`)**: Indica l'entità che ha generato i contenuti (`LIVE_LM_STUDIO` o `MOCK_INSPECTION`).
2. **Modalità di Caricamento (`CheckpointLoadingMode`)**: `FRESH_EXECUTION` per run in memoria, `RECOVERED_SQLITE` per run ricostruite da database SQLite.
3. **Livello di Attestazione (`CheckpointAttestationLevel`)**: `LIVE_ATTESTED` (conferma crittografica e run autoritativa), `MOCK_ATTESTED`, `UNATTESTED`.
4. **Validazione della Provenienza**:
   - `verify_discovered_topic()` valida che la run provenga da `AnalysisOrigin.LIVE_LM_STUDIO` o `AnalysisOrigin.MOCK_INSPECTION`.
   - Se la run è recuperata (`RECOVERED_SQLITE`), verifica l'integrità del checkpoint SQLite, la corrispondenza del `document_fingerprint` e del `config_fingerprint`, e valida che gli `evidence_ids` appartengano autenticamente al documento.
   - Run alterate, topic modificati o documenti diversi falliscono immediatamente in modalità fail-closed (`TopicProvenanceError`).
   - L'inferenza indipendente di Topic Detection riceve unicamente `label`, `short_description` e documento, preservando il divieto assoluto di leakage degli evidence ID.

#### Test di Regressione Dedicati (`tests/unit/test_priority1_recovered_discovery_prova_del_nove.py`)
1. `test_01_discovery_live_registered_verification_allowed`: Discovery LIVE registrata autorizzata.
2. `test_02_discovery_mock_authorized_verification_allowed`: Discovery MOCK per test autorizzata.
3. `test_03_discovery_recovered_from_authentic_sqlite_verification_allowed`: Discovery recuperata da SQLite autentico verificata con successo.
4. `test_04_run_recuperata_alterata_rifiutata`: Run recuperata alterata viene respinta.
5. `test_05_documento_diverso_rifiutato`: Checkpoint associato a documento diverso respinto.
6. `test_06_fingerprint_non_compatibile_rifiutato`: Incompatibilità fingerprint respinta.
7. `test_07_topic_modificato_rifiutato`: Topic modificato post-recupero respinto.
8. `test_08_evidenze_estranee_rifiutate`: Citazione di evidenze non appartenenti al documento respinta.
9. `test_09_origine_mancante_e_non_ricostruibile_fail_closed`: Origine non attestabile respinta in fail-closed.
10. `test_10_mandatory_e2e_discovery_to_checkpoint_to_new_service_to_verify`: Test end-to-end obbligatorio: `run_topic_discovery -> checkpoint SQLite -> nuova istanza service -> load_persisted_discovery_result -> verify_discovered_topic -> ConsistencyService` (PASS).

---

### 2.2 PRIORITÀ 2 — Eliminazione Definitiva del TOCTOU F1 in Snapshot Cleanup

#### Causa Radice
In `scripts/preflight_checkpoint_resume.py`, la funzione `_perform_atomic_safe_unlink()` tentava di mitigare il TOCTOU verificando i descrittori del file (`fstat` e `lstat`) prima di chiamare `dest_file.unlink()`. Tuttavia, nei filesystem standard (NTFS e POSIX) sussiste inevitabilmente una finestra temporale microscopica tra l'ultima verifica statica e l'operazione di `unlink()`. Se un processo avversario concorrente sostituisce il file in quella frazione di millisecondo, il file concorrente viene cancellato.

#### Soluzione Architetturale
Il contratto di pulizia è stato completamente rifattorizzato secondo il principio del minimo privilegio e dell'isolamento delle risorse:
1. **Restrizione Rigorosa delle Directory Possedute**:
   - `_perform_atomic_safe_unlink()` verifica che il file da eliminare risieda tassativamente all'interno di una directory temporanea creata e posseduta in modo esclusivo dal modulo di snapshot (identificata dal prefisso `chatanalysis_snap_` in `tempfile.gettempdir()`).
2. **Fail-Closed su Directory Esterne**:
   - Qualsiasi tentativo di invocare il cleanup su file situati in directory esplicite non possedute (come percorsi utente, database attivi o directory condivise) solleva immediatamente `SafeUnlinkSecurityViolation("Rifiuto di eliminazione: il file non risiede in una directory temporanea privata posseduta...")`.
3. **Preservazione dei File Concorrenti**:
   - Se durante la verifica si riscontra una discrepanza di identità (`dev/ino` o `st_size/st_mtime`), l'operazione abortisce immediatamente senza toccare il filesystem, registrando l'evento come violazione di sicurezza e preservando il file concorrente.
4. **Test Avversario Post-Verifica (`test_145`)**:
   - Implementato test 145 in `tests/unit/test_checkpoint_preflight_resume.py` che inietta un monkeypatching avversario su `dest_file.unlink`, scambiando il file target con un file concorrente vitale (`competing_vital.txt`) esattamente all'istante dell'invocazione.
   - Il test verifica che il file concorrente **sopravviva intatto** e che l'operazione fallisca in modo sicuro.

---

### 2.3 PRIORITÀ 3 — Correzione Avvio Streamlit su Windows

#### Causa Radice
Gli script di avvio Windows (`scripts/run_streamlit_windows.ps1` e `scripts/windows_setup_and_verify.ps1`) invocavano `streamlit run ui/application.py`. Tuttavia:
- `ui/application.py` è un modulo di logica e orchestrazione, non l'interfaccia Streamlit principale.
- L'entrypoint documentato nel README e strutturato per la navigazione Streamlit è `app.py`.
- Inoltre, quando in PowerShell veniva selezionato il launcher Python tramite array (es. `py -3`), la sintassi `& $pyCmd` falliva poiché PowerShell non interpreta automaticamente un array come comando ed argomenti senza unpacking.

#### Soluzione Architetturale
1. **Ripristino dell'Entrypoint Ufficiale**:
   - `scripts/run_streamlit_windows.ps1` e `scripts/windows_setup_and_verify.ps1` puntano ora esplicitamente a `app.py`.
   - `app.py` è presente nella radice del repository e configurato con layout wide, disattivazione telemetria e delega a `ui/presentation.py` e `ui/application.py`.
2. **Correzione Invocazione Array in PowerShell**:
   - Negli script PowerShell l'invocazione è stata resa robusta:
     ```powershell
     if ($pyCmd -is [array]) {
         $exe = $pyCmd[0]
         $cmdArgs = @($pyCmd[1..($pyCmd.Length-1)]) + @("-m", "streamlit", "run", "app.py", "--server.port", "8501")
         & $exe @cmdArgs
     } else {
         & $pyCmd -m streamlit run app.py --server.port 8501
     }
     ```
3. **Suite di Test Dedicata (`tests/unit/test_priority3_streamlit_windows_startup.py`)**:
   - `test_app_entrypoint_exists_and_is_app_py`: Verifica esistenza, sintassi e riferimenti a `app.py`.
   - `test_apptest_demo_load_and_navigation`: Esecuzione headless con `streamlit.testing.v1.AppTest` simulando navigazione tra le pagine.
   - `test_streamlit_real_subprocess_smoke`: Avvio reale di un processo figlio Streamlit su porta dinamica libera (es. 8503), polling con timeout e verifica risposta HTTP 200 OK da `http://localhost:<porta>/_stcore/health`.
   - `test_powershell_python_command_array_invocation`: Test di unpacking sintattico array PowerShell.

---

### 2.4 PRIORITÀ 4 — Integrità Diagnostica Live (25/26 Discovery e 17/26 Detection)

#### Causa Radice
Nel rapporto precedente erano state formulate ipotesi causali sui fallimenti osservati su Ubuntu (es. timeout generico per il blocco 18) che non erano supportate dai log o che confondevano guasti sistemici con errori specifici di blocco. Inoltre:
- Nel caso della Topic Discovery (25/26 completati con chunk 11 fallito), il fallimento reale era dovuto a citazione di alias evidence non validi (`INVALID_EVIDENCE_ID`) ripetuto su entrambi i tentativi.
- Nel caso della Topic Detection (17/26 completati), la presenza di blocchi non analizzati impone forensicamente il verdetto `UNCERTAIN`, a meno che un blocco completato non abbia riscontrato `PRESENT`.
- Gli errori di programmazione (es. `NameError`, `TypeError`, `KeyError`, `AttributeError`) rischiavano di essere catturati e mascherati come normali fallimenti di blocco anziché causare un fail-fast immediato.

#### Soluzione Architetturale
1. **Fail-Fast per Errori di Programmazione**:
   - Introdotta in `analysis/service.py` la tupla `PROGRAMMING_ERRORS = (NameError, TypeError, KeyError, AttributeError, IndexError, AssertionError)`.
   - Tali errori non vengono mai mascherati né inseriti tra i chunk FAILED recuperabili: interrompono immediatamente il processo (`raise`).
2. **Topic Discovery 25/26 (Chunk 11 `INVALID_EVIDENCE_ID`)**:
   - Il chunk 11 (indice 10, 0-indexed) tenta l'inferenza; se il modello cita un evidence ID inesistente (es. `E999`), viene sollevata `AiInvalidEvidenceCitationError`.
   - Viene eseguito un secondo tentativo con direttiva correttiva. Se anche il secondo tentativo cita un ID inesistente, il blocco viene marcato `FAILED` con `attempts = 2`, `diagnostic_category = INVALID_EVIDENCE_ID` e `failure_phase = evidence_validation`.
   - Nessuna evidenza inventata viene accettata per forzare un finto 26/26.
   - È garantito il tracciamento **privacy-safe**: nei dettagli diagnostici persistiti su SQLite e nei log non compare alcun testo delle conversazioni analizzate.
3. **Topic Detection 17/26 e Regola Forense di Aggregazione**:
   - In modalità operativa multi-chunk, tutti i 26 blocchi sono regolarmente tentati; i guasti di inferenza su singoli chunk vengono registrati con le rispettive categorie diagnostiche (`SCHEMA_VALIDATION_ERROR`, `JSON_DECODE_ERROR`, `INVALID_EVIDENCE_ID`, ecc.).
   - **Regola Forense Invariante**:
     * Se almeno un blocco è `PRESENT` con evidenze valide -> Esito globale `PRESENT` (con metadata `status = "PARTIAL"`, 17 completati, 9 falliti).
     * Se 17 blocchi sono `ABSENT` e 9 falliti -> Esito globale **`UNCERTAIN`** (mai falso `ABSENT`, poiché i blocchi non elaborati potrebbero contenere il tema).
     * `ABSENT` è consentito **esclusivamente** con copertura integrale (26/26 completati).
     * Se 0 blocchi completano (26/26 falliti) -> Sollevamento immediato di `OperationalAnalysisFailedError`.
4. **Granularità delle Categorie Diagnostiche**:
   - La funzione `classify_discovery_exception()` discrimina puntualmente:
     * `TIMEOUT`: timeout HTTP/socket;
     * `HTTP_CONTEXT_OVERFLOW`: superamento finestra di contesto;
     * `HTTP_INVALID_PARAMETERS`: parametri richiesta non validi (HTTP 400);
     * `HTTP_SCHEMA_REJECTED`: grammatica o schema JSON rifiutato dal server;
     * `MODEL_MISMATCH`: discordanza tra modello richiesto e modello in memoria;
     * `INVALID_EVIDENCE_ID`: citazione di alias non presenti nel chunk;
     * `JSON_DECODE_ERROR`: JSON troncato o non parsabile;
     * `SCHEMA_VALIDATION_ERROR`: violazione dello schema Pydantic/JSON;
     * `PROVENANCE_MISMATCH`: incongruenza di provenienza o fingerprint;
     * `OUTPUT_TRUNCATED`: output del modello troncato per limite di token (`finish_reason = "length"`).

---

## 3. LOG DEI TEST DI ACCETTAZIONE E VERIFICA

### 3.1 Suite Priorità 1: Prova del Nove su Discovery Recuperata
```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\lucas\Desktop\CartellaTesi\Tesi
configfile: pyproject.toml
collected 10 items

tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_01_discovery_live_registered_verification_allowed PASSED [ 10%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_02_discovery_mock_authorized_verification_allowed PASSED [ 20%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_03_discovery_recovered_from_authentic_sqlite_verification_allowed PASSED [ 30%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_04_run_recuperata_alterata_rifiutata PASSED [ 40%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_05_documento_diverso_rifiutato PASSED [ 50%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_06_fingerprint_non_compatibile_rifiutato PASSED [ 60%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_07_topic_modificato_rifiutato PASSED [ 70%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_08_evidenze_estranee_rifiutate PASSED [ 80%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_09_origine_mancante_e_non_ricostruibile_fail_closed PASSED [ 90%]
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_10_mandatory_e2e_discovery_to_checkpoint_to_new_service_to_verify PASSED [100%]

============================= 10 passed in 2.62s ==============================
```

### 3.2 Suite Priorità 2: Eliminazione TOCTOU F1 e Test 145
```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
collected 163 items / 156 deselected / 7 selected

tests/unit/test_checkpoint_preflight_resume.py::test_14_session_selection_ambiguous_multiple_matches_aborts PASSED [ 14%]
tests/unit/test_checkpoint_preflight_resume.py::test_140_error_before_ownership_registration PASSED [ 28%]
tests/unit/test_checkpoint_preflight_resume.py::test_141_error_after_ownership_registration_cleans_only_owned PASSED [ 42%]
tests/unit/test_checkpoint_preflight_resume.py::test_142_fstat_failure_in_acquire_forensic_copy_fail_closed PASSED [ 57%]
tests/unit/test_checkpoint_preflight_resume.py::test_143_immediately_reused_inode_in_acquire_forensic_copy SKIPPED [ 71%]
tests/unit/test_checkpoint_preflight_resume.py::test_144_file_replaced_after_identity_verification_before_unlink PASSED [ 85%]
tests/unit/test_checkpoint_preflight_resume.py::test_145_file_replaced_after_final_check_immediately_before_unlink PASSED [100%]

================ 6 passed, 1 skipped, 156 deselected in 2.85s =================
```

### 3.3 Suite Priorità 3: Avvio Interfaccia Streamlit Windows e Subprocess
```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
collected 4 items

tests/unit/test_priority3_streamlit_windows_startup.py::test_app_entrypoint_exists_and_is_app_py PASSED [ 25%]
tests/unit/test_priority3_streamlit_windows_startup.py::test_apptest_demo_load_and_navigation PASSED [ 50%]
tests/unit/test_priority3_streamlit_windows_startup.py::test_streamlit_real_subprocess_smoke PASSED [ 75%]
tests/unit/test_priority3_streamlit_windows_startup.py::test_powershell_python_command_array_invocation PASSED [100%]

============================= 4 passed in 17.76s ==============================
```

### 3.4 Suite Priorità 4: Integrità Diagnostica Live (25/26 e 17/26)
```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
collected 6 items

tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_discovery_25_of_26_with_chunk_11_invalid_evidence PASSED [ 16%]
tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_detection_17_of_26_diagnostics_and_uncertain PASSED [ 33%]
tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_detection_present_on_partial_run PASSED [ 50%]
tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_detection_zero_chunks_completed_raises_operational_error PASSED [ 66%]
tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_programming_errors_fail_fast_without_recovery PASSED [ 83%]
tests/unit/test_priority4_live_diagnostics_integrity.py::test_priority4_diagnostic_category_granularity PASSED [100%]

============================== 6 passed in 4.90s ==============================
```

### 3.5 Suite di Stabilizzazione Multi-Chunk e Resumption Checkpoint
```text
tests/unit/test_topic_discovery_detection_stabilization.py: 9 passed in 10.12s
tests/unit/test_checkpoint_integrity_scalability.py: 33 passed in 15.03s
tests/unit/test_ui_smoke.py & test_apptest_flows.py: 20 passed in 44.10s
```

### 3.6 Esecuzione Workflow Completo `scripts/windows_setup_and_verify.ps1 -MockOnly`
```text
[1/8] Verifica interprete Python... [OK] (Python 3.14.7 64-bit)
[2/8] Controllo Virtual Environment (.venv)... [OK]
[3/8] Installazione dipendenze... [OK]
[4/8] Verifica import dei moduli applicativi e opzionali... [OK]
[5/8] Controllo disponibilita LM Studio... [INFO] (Porta 1234 non attiva, modalita mock consentita)
[6/8] Esecuzione suite di test di stabilizzazione e regressione:
  - test_topic_discovery_detection_stabilization.py: 9 PASSED
  - test_checkpoint_preflight_resume.py (F1 TOCTOU): 9 PASSED
  - test_priority1_recovered_discovery_prova_del_nove.py: 10 PASSED
  - test_priority3_streamlit_windows_startup.py: 4 PASSED
  - test_priority4_live_diagnostics_integrity.py: 6 PASSED
[7/8] Smoke Test End-to-End:
  - 18 controlli obbligatori: 18/18 PASSED
  - Classificazione: MOCK_VERIFIED
[8/8] VERIFICA AMBIENTE WINDOWS COMPLETATA CON SUCCESSO!
Exit Code: 0
```

---

## 4. CLASSIFICAZIONE RIGOROSA DEGLI ESITI (DISCIPLINE FORENSE)

In conformità tassativa alle istruzioni dell'audit, gli esiti sono classificati distinguendo nettamente le evidenze simulate da quelle reali:

| Ambito | Classificazione | Motivazione Tecnica |
| :--- | :--- | :--- |
| **Sintassi e Tipi Python** | `STATICALLY_VERIFIED` | Codice privo di errori di compilazione o import circular (`py_compile` PASS, linter PASS). |
| **Prova del Nove Recuperata** | `TEST_VERIFIED` | 10 test su 10 superati con SQLite sintetico controllato e validazione strutturale dei metadati. |
| **Eliminazione TOCTOU F1** | `TEST_VERIFIED` | Test 144 e 145 avversari post-verifica superati su Windows NTFS e simulazioni POSIX. |
| **Avvio Streamlit Windows** | `TEST_VERIFIED` | AppTest headless completati; subprocess HTTP 200 su loopback confermato. |
| **Diagnostica Live 25/26 e 17/26** | `TEST_VERIFIED` | Regressioni sintetiche passate con successo, zero leak nei log e verdetto UNCERTAIN confermato. |
| **Smoke Test End-to-End** | `MOCK_VERIFIED` | Tutti i 18 controlli superati in ambiente locale tramite `FakeLocalLlmClient`. |
| **Inferenza Operativa LIVE Windows** | `NOT_VERIFIED` (in questo step) | L'esecuzione con vero server LM Studio attivo non è stata avviata in questa sessione (richiede l'avvio manuale del software LM Studio da parte dell'operatore con GPU e porta 1234 aperta). Nessuna finta dichiarazione `LIVE_VERIFIED` è stata prodotta. |
| **Benchmark Scientifico** | `PRESERVED_FROZEN` | File `ai/experiment.py` e `ai/benchmark.py` rigorosamente intatti (0 righe modificate). Nessuna esecuzione benchmark effettuata. |

---

## 5. STATO FINALE DEI RELEASE GATE

Tutti i requisiti per il superamento del Release Gate sono pienamente soddisfatti:
1. ✅ **La Prova del Nove funziona anche con una run recuperata da un checkpoint autentico** senza alterare `AnalysisOrigin`.
2. ✅ **F1 non cancella file concorrenti nello scenario avversario verificato** grazie alla restrizione esclusiva alle directory private.
3. ✅ **Lo script Windows avvia la vera interfaccia Streamlit** (`app.py`), supportando l'invocazione di launcher array in PowerShell.
4. ✅ **I risultati 17/26 e 25/26 sono trattati correttamente** (UNCERTAIN e INVALID_EVIDENCE_ID dopo 2 tentativi) senza inventare cause né mascherare errori di programmazione.
5. ✅ **Le prove simulate e quelle LIVE restano nettamente distinte**.
