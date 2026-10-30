# CHATANALYSIS — RAPPORTO TECNICO CONCLUSIVO: INTEGRITÀ FORENSE WAL/SHM E PREFLIGHT SAFETY

**ID Consegna**: `chatAnalysis_checkpoint_wal_source_integrity_audit_20261020`  
**Data**: 20 Ottobre 2026  
**Autore**: Team Antigravity AI / Forensic Software Engineering  
**Stato**: Concluso con Successo — Zero Modifiche al Runtime Core di Topic Discovery — 160/163 Test Unitari Superati (3 Skipped su Windows per Symlink/Chmod privi di privilegi amministratore) — Benchmark Scientifico Congelato Immutabile

---

## 1. EXECUTIVE SUMMARY

L'audit indipendente condotto sul bundle `chatAnalysis_checkpoint_resume_snapshot_isolation_final_audit_20261020.zip` ha confermato:
- 108/108 hash SHA-256 corretti nel manifest;
- `python -m compileall` PASS senza errori di compilazione;
- Suite concatenata completa: 142 passed, 1 skipped, exit code 0 su Linux / Python 3.13.5;
- Efficacia delle protezioni di isolamento degli snapshot fuori da `.checkpoints` e fuori dal repository `Tesi`;
- Totale assenza del precedente timeout pytest.

Tuttavia, l'audit indipendente ha individuato un'anomalia fisica di integrità sorgente sui database SQLite in modalità WAL (Write-Ahead Logging):
- **Difetto B1 (Mutazione file `-shm` durante il preflight)**: Aprendo una connessione SQLite sui file originali, anche specificando `mode=ro` o `PRAGMA query_only=ON`, il motore VFS di SQLite aggiorna i *read-marks* nell'header dell'indice di memoria condivisa (`-shm`). Sebbene il contenuto logico del database e il file `-wal` rimangano intatti, l'hash SHA-256 e l'mtime del file `-shm` vengono alterati, violando la garanzia di immutabilità fisica byte-per-byte dei file sorgente originali.
- **Difetto B2 (Test di regressione incompleto)**: Il test `test_70_preservation_of_sqlite_sidecars` verificava solo il database principale e il file `-wal`, omettendo la verifica dell'hash, dimensione ed mtime del file `-shm`.
- **Obiettivo B3 (Separazione forense dell'acquisizione)**: Eliminare qualsiasi collegamento del motore SQLite ai file sorgente originali. Eseguire l'acquisizione tramite copia binaria a livello di sistema operativo durante una finestra di quiescenza verificata ($t_0 == t_1$), eseguendo poi tutte le analisi diagnostiche e snapshot esclusivamente sulla copia isolata.
- **Obiettivo B4 (Distinzione tra integrità logica e immutabilità fisica)**: Strutturare il report di audit con una sezione dedicata `source_immutability_status` comprendente sei metriche booleane distinte.
- **Obiettivo B5 (Suite di test obbligatoria)**: Preservare i 143 test esistenti e aggiungere 20 test sintetici (`test_76`–`test_95`) per verificare writer aperti, file `-shm` non vuoto, recupero record non consolidati, gestione concorrenza, rifiuto copie incoerenti e assenza di leak o inferenze.
- **Obiettivo B6 (Aggiornamento Runbook Ubuntu)**: Formalizzare nel runbook i comandi di ispezione writer (`lsof`/`fuser`), la spiegazione della mutazione dei read-marks `-shm`, e la procedura passo-passo di copia ed analisi forense.
- **Obiettivo B7 (Preservazione contratti congelati)**: Nessuna modifica a `service.py`, `checkpoint.py`, `chunking.py`, `experiment.py`, prompt, metriche o dataset congelati.

Tutti gli obiettivi sono stati implementati e verificati con **160 test passati, 3 skipped, exit code 0**.

---

## 2. ANALISI DELLA ROOT CAUSE (B1)

### 2.1 Architettura SQLite WAL e Meccanismo dei Read-Marks in `-shm`
In SQLite con journaling WAL (`PRAGMA journal_mode=WAL`), il file `-shm` non è un file di dati persistente ma un file di mappatura in memoria condivisa (`sqlite3_shm`) utilizzato per coordinare concorrenza tra lettori e scrittori:
1. Quando un processo writer inserisce record, aggiunge frame al file `-wal`.
2. I lettori leggono l'indice di memoria condivisa (`-shm`) per determinare quali frame nel `-wal` sono validi per la loro specifica transazione snapshot-isolated.
3. Nell'header del file `-shm` (i primi byte della pagina di controllo), SQLite alloca e aggiorna dinamicamente i **read-marks** (indicatori del punto di lettura per ciascun lettore attivo).
4. Di conseguenza, **qualsiasi apertura di connessione SQLite** contro i file sorgenti (compresi `mode=ro`, `PRAGMA query_only = ON;`, e la SQLite Online Backup API `sconn.backup()`) invoca il modulo VFS di SQLite che scrive i nuovi read-marks nel file `-shm`.

### 2.2 Dimostrazione Sperimentale di Fallimento
Il test di regressione dedicato `test_80_direct_sqlite_readonly_connection_mutates_shm_regression` ha dimostrato inequivocabilmente che:
```python
# Connessione con writer aperto e transazione attiva
writer_conn = sqlite3.connect(db_path)
writer_conn.execute("PRAGMA journal_mode=WAL;")
writer_conn.execute("INSERT INTO checkpoints ...")

# Lettura con mode=ro
ro_uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
ro_conn = sqlite3.connect(ro_uri, uri=True)
ro_conn.execute("SELECT COUNT(*) FROM checkpoints;").fetchone()
ro_conn.close()

# RISULTATO:
# sha256(db_path) == inalterato
# sha256(wal_path) == inalterato
# sha256(shm_path) != INIZIALE (MUTATO!)
```
Pertanto, **dichiarare zero scritture sui file originali basandosi esclusivamente su `mode=ro` o `conn.backup()` era un assunto tecnicamente non dimostrato**.

---

## 3. ARCHITETTURA DI ACQUISIZIONE FORENSE E PREFLIGHT ISOLATO (B3)

Per risolvere alla radice l'anomalia, l'architettura è stata ristrutturata separando nettamente l'acquisizione fisica dall'esecuzione del motore SQLite:

```
[Sorgente Investigativa Originale]
   ├── checkpoints.db       (NESSUNA CONNESSIONE SQLITE APERTA)
   ├── checkpoints.db-wal   (NESSUN PRAGMA O BACKUP API SU FILE ORIGINALI)
   └── checkpoints.db-shm   (ZERO SCRITTURE: READ-MARKS INTATTI)
            │
            │  1. Rilevazione stato t0: size, mtime_ns, SHA-256 (lettura binaria SO)
            │  2. Copia binaria atomica OS in directory temporanea isolata
            │  3. Rilevazione stato t1: verifica finestra di quiescenza (t0 == t1)
            │  4. Validazione SQLite magic header (b"SQLite format 3\x00")
            ▼
[Staging Forense Isolato / Snapshot Esterno]
   ├── copy_checkpoints.db
   ├── copy_checkpoints.db-wal
   └── copy_checkpoints.db-shm
            │
            │  5. Validazione transazionale isolata: PRAGMA quick_check / integrity_check
            │  6. Consolidamento transazioni WAL non checkpointate via Backup API / Preflight
            ▼
[Preflight Engine & Report Diagnostico]
   └── Esecuzione audit, verifica sessioni, estrazione metadati sicuri, zero leak
```

### 3.1 Funzione `acquire_forensic_readonly_copy()`
Implementata in [scripts/preflight_checkpoint_resume.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/scripts/preflight_checkpoint_resume.py):
- **Copia puramente binaria**: Utilizza buffer a blocchi in lettura binaria OS (`open(..., "rb")`) senza caricare estensioni SQLite.
- **Finestra di quiescenza verificata**:
  - Calcola metadati e hash SHA-256 di database principale e sidecar prima della copia ($t_0$).
  - Esegue la copia fisica in una cartella di staging temporanea (permessi `0700` cartella, `0600` file).
  - Ricalcola metadati e hash SHA-256 dei file sorgente subito dopo la copia ($t_1$).
  - Se $t_0 \ne t_1$ (scrittore attivo che ha modificato i file durante la copia), l'algoritmo effettua fino a 3 tentativi con backoff esponenziale; se la quiescenza non viene raggiunta, interrompe l'operazione sollevando `SnapshotCreationFailedError` con codice diagnostico `SOURCE_MODIFIED_DURING_COPY`.
- **Fidelità della copia verificata**: Verifica che la copia isolata abbia esattamente gli stessi hash SHA-256 rilevati a $t_0$.
- **Integrità transazionale sulla copia isolata**: Apre una connessione SQLite **esclusivamente sulla copia isolata** per eseguire `PRAGMA quick_check;` e `PRAGMA integrity_check;`, garantendo che transazioni WAL pendenti siano coerenti e pronte al recupero.
- **Cleanup rigoroso**: Qualsiasi eccezione o interruzione pulisce immediatamente la directory di staging creata, senza toccare la sorgente.

---

## 4. INTEGRAZIONE DELLE METRICHE DI IMMUTABILITÀ FISICA (B4)

Il report di audit `PreflightAuditReport` distingue ora esplicitamente tra integrità logica e immutabilità fisica tramite il dizionario `source_immutability_status`:

```json
{
  "source_immutability_status": {
    "DATABASE_LOGICAL_CONTENT_UNCHANGED": true,
    "WAL_CONTENT_UNCHANGED": true,
    "SHM_BYTES_UNCHANGED": true,
    "SOURCE_METADATA_UNCHANGED": true,
    "CONSISTENT_SNAPSHOT_VERIFIED": true,
    "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": true
  }
}
```

### Definizione dei Criteri di Immutabilità:
1. `DATABASE_LOGICAL_CONTENT_UNCHANGED`: Il contenuto dei record e lo schema del database principale non hanno subito modifiche logiche.
2. `WAL_CONTENT_UNCHANGED`: Il file `-wal` (o la sua assenza) ha conservato l'esatto hash SHA-256 iniziale.
3. `SHM_BYTES_UNCHANGED`: Il file `-shm` ha conservato byte-per-byte lo stesso hash SHA-256, senza mutazione dei read-marks SQLite.
4. `SOURCE_METADATA_UNCHANGED`: I timestamp di modifica a risoluzione nanosecondo (`mtime_ns`) e le dimensioni di tutti i file sorgente sono identici prima e dopo l'operazione.
5. `CONSISTENT_SNAPSHOT_VERIFIED`: La copia o snapshot creato è transazionalmente valido, supera `PRAGMA quick_check` e consolida tutti i frame del WAL.
6. `SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED`: Congiunzione logica rigorosa: tutti gli hash e metadata dei file sorgenti sono invariati e nessuna operazione di scrittura o mutazione VFS è avvenuta.

---

## 5. CORREZIONE DEL TEST DI REGRESSIONE ESISTENTE (B2)

In [tests/unit/test_checkpoint_preflight_resume.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/tests/unit/test_checkpoint_preflight_resume.py), il test `test_70_preservation_of_sqlite_sidecars` è stato corretto e potenziato per non limitarsi a DB e WAL:
- Include ora esplicitamente il file `-shm` generato da una connessione writer attiva;
- Misura esistenza, dimensione, mtime nanosecondi e hash SHA-256 di `db_path`, `wal_path` e `shm_path`;
- Esegue la procedura di snapshot isolato mantenendo la connessione writer aperta;
- Verifica prima e dopo che:
  - `pre_db_sha == post_db_sha` e `pre_db_mtime == post_db_mtime`;
  - `pre_wal_sha == post_wal_sha` e `pre_wal_mtime == post_wal_mtime`;
  - `pre_shm_sha == post_shm_sha` e `pre_shm_mtime == post_shm_mtime`;
  - Il file `-shm` post-snapshot esiste e non è vuoto.

---

## 6. SUITE DI TEST DI REGRESSIONE FORENSE AGGIUNTA (B5)

In [tests/unit/test_checkpoint_preflight_resume.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/tests/unit/test_checkpoint_preflight_resume.py), è stata implementata la classe `TestForensicSourceIntegrityAndQuiescentAcquisition` comprendente i 20 test richiesti (`test_76`–`test_95`):

| ID Test | Nome Test | Descrizione e Vincolo Verificato | Esito |
|:---|:---|:---|:---:|
| `test_76` | `test_76_wal_with_open_writer_and_non_empty_shm` | Database WAL con writer aperto e file `-shm` non vuoto. | **PASS** |
| `test_77` | `test_77_preservation_of_main_database_hash` | Verifica che l'hash SHA-256 del DB principale rimanga intatto. | **PASS** |
| `test_78` | `test_78_preservation_of_wal_hash` | Verifica che l'hash SHA-256 del file WAL rimanga intatto. | **PASS** |
| `test_79` | `test_79_preservation_of_shm_hash_and_mtime` | Verifica che hash SHA-256 e `mtime_ns` del file `-shm` rimangano intatti. | **PASS** |
| `test_80` | `test_80_direct_sqlite_readonly_connection_mutates_shm_regression` | Dimostra che la connessione SQLite diretta `mode=ro` muta `-shm` (documentando il difetto B1). | **PASS** |
| `test_81` | `test_81_consistent_acquisition_via_quiescent_forensic_copy` | Acquisizione coerente tramite nuova procedura di copia quiescente. | **PASS** |
| `test_82` | `test_82_uncheckpointed_wal_records_recovered_in_isolated_snapshot` | Recupero di record presenti esclusivamente nel WAL non consolidato. | **PASS** |
| `test_83` | `test_83_active_writer_concurrent_retry_or_quiescence_window` | Gestione di writer attivo con retry e raggiungimento della finestra di quiescenza. | **PASS** |
| `test_84` | `test_84_abort_when_consistent_copy_impossible` | Rifiuto controllato se il writer continua a scrivere e impedisce la quiescenza. | **PASS** |
| `test_85` | `test_85_incomplete_or_corrupt_copy_rejected` | Snapshot o copia incompleta/corrotta rilevata e respinta con errore. | **PASS** |
| `test_86` | `test_86_permission_denied_during_acquisition_handled_safely` | Mancanza di permessi gestita in modo controllato senza crash. | **PASS** |
| `test_87` | `test_87_interruption_during_forensic_acquisition_cleans_up` | Interruzione improvvisa (`KeyboardInterrupt`) ripulisce le cartelle temporanee. | **PASS** |
| `test_88` | `test_88_cleanup_exclusive_to_created_temporary_resources` | Il cleanup rimuove solo le risorse create, senza toccare file esterni o sorgenti. | **PASS** |
| `test_89` | `test_89_no_files_created_inside_checkpoints_dir` | Garanzia di assenza di file creati dentro `.checkpoints`. | **PASS** |
| `test_90` | `test_90_no_writes_inside_tesi_repository` | Garanzia di assenza totale di scritture all'interno del repository `Tesi`. | **PASS** |
| `test_91` | `test_91_no_sensitive_paths_in_reports_and_diagnostics` | Nessuna esposizione di percorsi o token sensibili nei report diagnostici. | **PASS** |
| `test_92` | `test_92_cli_subprocess_execution_with_open_writer` | Esecuzione CLI da sottoprocesso con writer aperto e verifica output sicuro. | **PASS** |
| `test_93` | `test_93_zero_llm_inference_during_forensic_preflight` | Garanzia di zero chiamate ad API o modelli LLM durante la procedura forense. | **PASS** |
| `test_94` | `test_94_no_regression_of_secure_output_json_with_forensic_copy` | Non-regressione del salvataggio report protetto con permessi restrittivi. | **PASS** |
| `test_95` | `test_95_report_distinguishes_logical_and_physical_immutability` | Verifica che il report distingua accuratamente le 6 metriche di immutabilità. | **PASS** |

---

## 7. RISULTATI DELLA VERIFICA ESECUTIVA CONCATENATA

L'esecuzione della suite completa concatenata ha prodotto:
- **Comando**: `python -m pytest tests/unit/test_checkpoint_preflight_resume.py tests/unit/test_checkpoint_safe_diagnostics.py tests/unit/test_checkpoint_integrity_scalability.py`
- **Piattaforma**: Windows 11 / Python 3.14.7
- **Test Raccolti**: 163
- **Esito**: **160 PASSED, 3 SKIPPED** (skip legittimi su Windows NTFS per symlink e chmod senza privilegi amministratore)
- **Durata**: **146.49s (0:02:26)**
- **Exit Code**: **0**
- **Log registrato**: `test_results.log`

Tutti i 143 test preesistenti sono stati preservati intatti, e i 20 nuovi test sono tutti passati con successo.

---

## 8. RUNBOOK UBUNTU AGGIORNATO (B6)

Il documento [UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md) è stato completamente aggiornato:
1. **Comandi di verifica writer attivi**: Aggiunta istruzione preventiva tramite `lsof checkpoints.db*` o `fuser -v checkpoints.db*`.
2. **Spiegazione tecnica della mutazione `-shm`**: Chiarito perché la lettura SQLite standard anche con `mode=ro` o `conn.backup()` altera i read-marks nel file `-shm`.
3. **Procedura di copia forense quiescente passo-passo**:
   - Rilevazione preliminare degli hash SHA-256 e timestamp `mtime` nanosecondi di DB, WAL e SHM.
   - Acquisizione della copia binaria in directory isolata tramite `scripts/preflight_checkpoint_resume.py --snapshot-dir /tmp/safe_audit_staging/`.
   - Verifica immediata che gli hash sorgente non siano mutati.
4. **Ispezione report sicuro**: Estrazione della sezione `source_immutability_status` tramite `jq` per verificare che tutte le 6 metriche siano `true`.
5. **Dichiarazione di non-esecuzione**: È esplicitamente ribadito che **nessuna operazione è stata eseguita sulla macchina Ubuntu reale del docente**, preservando lo stato congelato dell'ambiente investigativo.

---

## 9. VINCOLI FORENSI E REQUISITI DI CONSEGNA

1. **Integrità del Repository**: Nessun commit, push, merge o reset Git effettuato.
2. **Isolamento dell'Archivio**: Il file ZIP `chatAnalysis_checkpoint_wal_source_integrity_audit_20261020.zip` è stato generato **direttamente nella cartella superiore** `c:\Users\lucas\Desktop\CartellaTesi\`, fuori dall'albero Git e dalla cartella `Tesi`.
3. **Privacy Forense**: Nessun file SQLite reale (`.db`, `.sqlite`), nessun export WhatsApp o dato personale è stato incluso nell'archivio.
4. **Verifica Manifest**: L'archivio include `audit_manifest_sha256.txt` con tutti i 109 hash SHA-256 verificati al 100%.

Il bundle è pronto per l'audit indipendente di conformità finale.
