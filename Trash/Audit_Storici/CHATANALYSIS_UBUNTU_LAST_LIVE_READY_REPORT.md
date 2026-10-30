# CHATANALYSIS — RAPPORTO TECNICO AUDIT FINALE PRE-TEST LIVE UBUNTU
**Data:** 21 ottobre 2026  
**Ambiente di Riferimento Audit:** Windows 11 (Python 3.14.7) / Target Validazione Live: Ubuntu 24.04 LTS con GPU  
**Destinazione Consegna:** `chatAnalysis_ubuntu_last_live_ready_20261021.zip`  
**Stato Audit:** COMPLETO — Tutti i controlli chiusi in modalità fail-closed.  

---

## 1. OBIETTIVO E PERIMETRO DELL'INTERVENTO

Il presente intervento risolve **esclusivamente** i quattro difetti residui comprovati emersi dall'audit indipendente, garantendo la **stabilità sperimentale assoluta** per l'imminente campagna di Topic Discovery, Topic Detection e verifica della consistenza semantica su macchina Ubuntu con GPU.

### Vincoli di Conservazione Scientifica Rispettati al 100%
1. **Nessuna alterazione** ai 9 benchmark già congelati ed eseguiti (3 Qwen, 3 Llama, 3 DeepSeek).
2. **Nessuna modifica** a:
   - `ai/benchmark.py`
   - `ai/experiment.py`
   - `ai/chunking.py`
   - `ai/topics.py`
   - `ai/translation.py`
   - `ai/lmstudio.py`
   - `analysis/service.py`
   - `analysis/consistency.py`
   - prompt di sistema, ground truth, dataset di riferimento, metriche scientifiche.
3. Il protocollo scientifico rimane rigorosamente identico: quantizzazione `Q4_K_M`, context length `8192`, temperatura `0.0`, `max_tokens 512`, `timeout 240s`.
4. Il fix per `AiStructuredOutputError` (validato sui benchmark DeepSeek reali) è preservato intatto.
5. Nessuna connessione live automatica verso LM Studio o download di modelli durante i test.
6. Nessuna operazione Git (`commit`, `push`, `pull`, `reset`) eseguita automaticamente.

---

## 2. DETTAGLIO DELLE CORREZIONI APPLICATE

### P0 — 1. Verified Live Attestation Anti-Spoofing (`analysis/export.py`)
- **Difetto Risolto:** In precedenza, la presenza contemporanea nei metadati dei flag `authoritative_execution_certified=True`, `server_provenance_verified=True` e `runtime_binding_verified=True` promuoveva l'attestazione a `LIVE_AUTHORITATIVE` anche senza un riscontro nel registro indipendente `authoritative_live_run_ids`. Questi flag risiedevano nel medesimo dizionario non fidato e non costituivano prova terza.
- **Correzione:**
  - `reported_attestation_level` riflette le dichiarazioni dei metadati (`REPORTED_LIVE`), mentre `verified_attestation_level` **non le accetta mai**.
  - `verified_attestation_level` assume il valore `LIVE_AUTHORITATIVE` **esclusivamente** se una fonte autoritativa esterna del runtime (`authoritative_live_run_ids`) conferma l'ID raw originale (`raw_run_id`) della concreta run, con origine verificata `LIVE_LM_STUDIO` e client non-mock.
  - Con registro assente, vuoto o che non include l'ID: verdetto rigoroso `UNATTESTED` (fail-closed).
  - Il confronto avviene internamente su ID raw originali; la pseudonimizzazione interviene solo nella serializzazione finale.
  - L'esportazione di run parziali non è compromessa: il report JSON è sempre scaricabile anche con attestazione `UNATTESTED`.

### P0 — 2. Privacy dei JSON in Modalità Safe (`analysis/export.py`)
- **Difetto Risolto:** Identificativi sintetici come `run_MarioRossi` venivano esportati in chiaro perché conformi al pattern regex `run_[a-zA-Z0-9_-]+`; il campo `git_commit` accettava tag come `release_MarioRossi`; metadati liberi o campi tecnici potevano veicolare nomi, email o percorsi.
- **Correzione:**
  - `sanitize_run_id`: in modalità safe (`redact=True`), pseudonimizza **sempre** qualsiasi identificativo di run tramite digest SHA-256 troncato a 12 caratteri esadecimali (`run_<hash12>`), preservando la deterministica collegabilità tra Detection, Discovery e Consistency senza mai far trapelare identificativi originali o nomi personali.
  - `sanitize_git_commit`: accetta **esclusivamente** commit SHA conformi a `^[0-9a-fA-F]{7,40}$`. Qualsiasi tag (es. `release_MarioRossi`), canarino o testo libero viene convertito in `"NOT_RECORDED"`.
  - `sanitize_software_version`: valida la versione software con semver canonico e blocca qualsiasi testo libero contenente pattern personali o canarini, ricadendo sulla versione software ufficiale certificata.
  - `sanitize_validation_flag`: applica un'allowlist stretta sui flag di validazione (`STATICALLY_VERIFIED`, `RUNTIME_VERIFIED`, `TEST_VERIFIED`, `MOCK_VERIFIED`, `NOT_VERIFIED`, `UNVERIFIED`, `NOT_APPLIED`), rifiutando stringhe arbitrarie.
  - I nomi dei file esportati generati da `generate_export_filename` utilizzano esclusivamente identificativi sicuri pseudonimizzati.
  - Test avversari confermano l'assenza totale di stringhe personali (`MarioRossi`, email sintetiche, numeri telefonici, percorsi utente) sia nel JSON serializzato che nel nome file.

### P0 — 3. Veridicità della Copertura e Decoupling Decisionale (`analysis/export.py`)
- **Difetto Risolto:** Con metadati `{status: 'COMPLETE', chunk_count: 0, completed_chunk_count: 0, failed_chunk_count: 0}`, il report produceva `global_status: COMPLETE`, `completion_percentage: 0.0` e copiava il totale evidenze in `analyzed_evidence_count`.
- **Correzione:**
  - Con `planned_chunks == 0`: lo stato non è mai `COMPLETE`, ma viene marcato formalmente come `NO_WORK`, con `completion_percentage = None` e `is_complete = False`.
  - `analyzed_evidence_count`: proviene esclusivamente da conteggi osservati e registrati (`safe_int(meta.get("analyzed_evidence_count"))`). Se assente, rimane `None` (esportato come `null`), senza mai copiare il totale evidenze o inventare zero.
  - `offline_mode`: non assume più il default fittizio `True` se non registrato; se assente nei metadati viene esportato come `null` (`None`).
  - **Decoupling decisionale:** lo stato del run (`coverage_status`), la decisione sul topic (`decision`) e la percentuale di lavoro sono distinti. Una Detection con 17/26 chunk e decisione `PRESENT` rimane `decision: PRESENT` con `decision_status: PARTIAL`; una Detection 17/26 con assenza di riscontro non diventa `ABSENT` certo globale, ma riporta la specifica nota di disaccoppiamento: *"Negative decision on partial coverage cannot prove absence in unanalyzed chunks"*.

### P1 — 4. Snapshot F1, Destinazione Esterna e Pulizia (`scripts/preflight_checkpoint_resume.py`)
- **Difetto Risolto:** In precedenza, in caso di eccezione dopo la creazione di un file su `dest_path` esterno, la routine chiamava `_perform_atomic_safe_unlink` su una destinazione esterna all'area privata temporanea.
- **Correzione:**
  - Lo snapshot SQLite e la sincronizzazione transazionale avvengono **sempre e interamente** all'interno di una directory temporanea privata (`created_temp_dir = mkdtemp()`).
  - Solo a operazione completata con successo, il file consolidato viene pubblicato sulla destinazione esterna (`dest_path`) tramite `shutil.copy2` (con rifiuto preventivo se il file di destinazione esiste già per non sovrascrivere file utente preesistenti).
  - Successivamente alla copia, il file temporaneo nell'area privata viene rimosso in modo atomico.
  - **In caso di eccezione:** la pulizia è confinata rigorosamente all'area temporanea privata (`created_temp_dir`); la destinazione esterna (`dest_path`) **non viene mai toccata né cancellata automaticamente** (zero unlink esterni). Nessun uso di `shutil.rmtree()`.
  - **Limite di trust boundary dichiarato:** È formalmente riconosciuto che sul confine effettivo della chiamata di sistema `os.unlink`/`os.rmdir` un processo avversario concorrente appartenente allo **stesso UID** può teoricamente sostituire il file/directory nell'istante infinitesimale tra la verifica e la chiamata kernel. Tale limite non può essere rimosso a livello applicativo senza primitive atomiche native non disponibili per `rmdir`; il codice gestisce il confine con controlli di identità e fail-closed.

---

## 3. RISULTATI DELLA VERIFICA E TEST DI REGRESSIONE

Tutti i test sono stati eseguiti con successo nell'ambiente di audit:

| Suite di Test | File | Test Eseguiti | Esito | Note |
|---|---|---|---|---|
| **Export Scientifico JSON** | `tests/unit/test_analysis_export.py` | 44 / 44 | **PASSED (100%)** | Include 4 test di chiusura P0-1, P0-2, P0-3, P1-4 |
| **Regressioni F1 Checkpoint** | `tests/unit/test_checkpoint_preflight_resume.py` | 16 / 16 | **PASSED (100%)** | 6 storici (122, 136, 137, 138, 144, 151) + 10 estesi (156-167) |
| **Streamlit Windows Startup** | `tests/unit/test_priority3_streamlit_windows_startup.py` | 4 / 4 | **PASSED (100%)** | AppTest, fumo subprocess e PowerShell reali |
| **Verifica Manifest SHA-256** | `scripts/verify_package_manifest.py` | 11 / 11 | **PASSED (100%)** | 373/373 file coperti, zero discrepanze, zero duplicati |

### Tassonomia dei Livelli di Verifica Applicati
- `STATICALLY_VERIFIED`: Struttura dello schema JSON 1.1.0, costanti immutabili, parametri architetturali del congelamento scientifico.
- `TEST_VERIFIED`: Suite pytest automatizzata (64 test complessivi verdi, zero fallimenti, zero regressioni).
- `MOCK_VERIFIED`: Simulazioni di run non autorizzate e client fittizi (`FakeLocalLlmClient`, `ControlledMockClient`) rigorosamente distinti da esecuzioni autoritative.
- `LIVE_VERIFIED`: Convalidato sul percorso nominale autoritativo quando fornito con `authoritative_live_run_ids` corrispondente al `raw_run_id`.
- `NOT_VERIFIED`: Default fail-closed attribuito a qualsiasi sorgente, commit o conteggio non attestato in modo indipendente.

---

## 4. MINI-CHECKLIST OPERATIVA PER LA SESSIONE GPU UBUNTU (MAX 10 RIGHE)

1. **Backup Preventivo:** Eseguire backup completo di `benchmark_results/` e della cartella `Tesi` di lavoro funzionante su storage esterno prima di estrarre il pacchetto.
2. **Setup Ambiente:** Verificare versione Python (3.11+ / 3.12+), avviare LM Studio su Ubuntu e caricare il modello Q4_K_M (porta `1234`).
3. **Smoke Test su Copia:** Verificare l'avvio rapido (`streamlit run app.py`) su una copia di lavoro; se emergono anomalie d'ambiente, usare la versione precedente per non perdere la finestra GPU.
4. **Verifica Endpoint:** Confermare con curl o script che `http://127.0.0.1:1234/v1/models` risponda con il modello corretto.
5. **Sequenza Sperimentale (Conversazione ~1049 msg):**
   - (a) Eseguire **Topic Discovery** completa;
   - (b) Eseguire **Topic Detection** indipendente sul topic scoperto;
   - (c) Eseguire la **Verifica di Consistenza** (Prova del Nove).
6. **Esportazione Immediata:** Per ciascuna run, scaricare il report JSON Safe e annotare screenshot, log e conteggio esatto dei chunk (es. 26/26 o parziale).
7. **Conversazioni Grandi:** Tentare conversazioni >1049 messaggi SOLO se la sequenza base è terminata e rimane tempo utile.
8. **Salvataggio Dati:** Copiare immediatamente tutti i JSON esportati e i log di run su supporto esterno distinto.
9. **Invarianza Parametri:** NON variare temperatura (0.0), max_tokens (512), context length (8192) né versione modello durante le sessioni comparative.
10. **Integrità Baseline:** Non cancellare o sovrascrivere mai i file delle nove esecuzioni precedenti (Qwen, Llama, DeepSeek).
