# CHATANALYSIS — RAPPORTO TECNICO CONCLUSIVO: FAIL-CLOSED WAL FINAL OWNERSHIP & INTEGRITY CORRECTION (F1–F3)

**ID Consegna**: `chatAnalysis_checkpoint_wal_failclosed_final_fix_audit_20261020`  
**Data**: 20 Ottobre 2026  
**Autore**: Team Antigravity AI / Forensic Software Engineering  
**Stato**: Concluso con Successo — Difetti F1, F2, F3 Risolti — Tracciamento Rigoroso dell'Ownership — Pulizia Fail-Closed dei File Parziali — Verifica Attiva dei Permessi `st_mode` — 200/203 Test Unitari Superati (3 Skipped su Windows per Symlink/Chmod privi di privilegi amministratore) — Exit Code 0 — Benchmark Scientifico Congelato Immutabile

---

## 1. CONTESTO DELL'AUDIT INDIPENDENTE ED EXECUTIVE SUMMARY

Il bundle precedente `chatAnalysis_checkpoint_wal_acquisition_failclosed_audit_20261020.zip` è stato sottoposto ad audit indipendente su ambiente di riferimento Linux / Python 3.13.5 con ijson 3.6.0.

### Esiti Convalidati dall'Audit:
- **110/110 SHA-256** verificati con esito positivo.
- **Compileall**: PASS (100% bytecode valido, zero errori sintattici).
- **Suite completa**: 184 passed, 1 skipped, exit code 0.
- **Topic Discovery Engine & Benchmark Congelato**: rigorosamente invariati.
- **Confermate le correzioni precedenti**: propagazione errori WAL/SHM, verifica finale $t_2$ con abort fail-closed, metriche `NOT_MEASURED`, isolamento diagnostico, test sintetici 24/26 checkpoint.

Tuttavia, l'audit indipendente ha individuato e riprodotto tre difetti specifici (F1, F2, F3) che hanno impedito il superamento del release gate. Conformemente alle direttive ricevute, non è stato effettuato alcun refactor generale, ma sono stati corretti puntualmente e rigorosamente i tre difetti.

---

## 2. ANALISI DEI DIFETTI E CORREZIONI IMPLEMENTATIVE (F1–F3)

### 2.1 F1 — Cleanup dello Snapshot: Non Cancellare File Altrui (`create_consistent_readonly_snapshot`)

#### Problema Riscontrato:
La funzione utilizzava `O_CREAT | O_EXCL` per creare il file di destinazione dello snapshot. Tuttavia, nel blocco `except BaseException`, la clausola di cleanup eseguiva `dest_file.unlink()` controllando semplicemente `if dest_file.exists()`.
Se un processo concorrente creava il file tra la validazione e la chiamata a `os.open`, `os.open` sollevava `FileExistsError`. Il ramo `except` intercettava l'eccezione ed eliminava il file creato dall'altro processo (`competing_file_survives=False`).

#### Correzione Implementata:
1. **Tracciamento Rigoroso dell'Ownership**: È stato introdotto il flag booleano `dest_file_owned = False` e la tupla identificativa `dest_file_identity: tuple[int, int] | None = None`.
2. **Assegnazione di Ownership Post-Creazione Esclusiva**: Il file è contrassegnato come posseduto (`dest_file_owned = True`) **esclusivamente** dopo che `os.open(O_CREAT | O_EXCL)` ha avuto successo. Se `os.open` fallisce con `FileExistsError`, `dest_file_owned` rimane `False`.
3. **Verifica Identità via Device/Inode**: Prima di eseguire qualsiasi unlink nel blocco di cleanup, la funzione verifica tramite `os.lstat(dest_file)` che `(cur_st.st_dev, cur_st.st_ino) == dest_file_identity`. Se un altro processo ha nel frattempo sostituito il file (inode differente), il cleanup non lo elimina.
4. **Preservazione dei Permessi**: Mantenuta la creazione con permessi restrittivi `0600` e umask protetta `0077`.
5. **Divieto di TOCTOU**: Nessun controllo `exists()` permissivo è stato anteposto a sostituzione di `O_EXCL`.

```python
# Estratto implementativo F1 in scripts/preflight_checkpoint_resume.py
dest_file_owned = False
dest_file_identity = None
try:
    fd = os.open(str(dest_file), flags, 0o600)
    try:
        st = os.fstat(fd)
        dest_file_identity = (st.st_dev, st.st_ino)
        dest_file_owned = True
    finally:
        os.close(fd)
except FileExistsError:
    # Il file esiste già ed è di un altro processo: dest_file_owned rimane False!
    raise
...
except BaseException:
    if dest_file_owned:
        should_unlink = True
        can_verify_id = (dest_file_identity is not None and (dest_file_identity[1] != 0 or os.name != "nt"))
        if can_verify_id:
            try:
                cur_st = os.lstat(dest_file)
                if (cur_st.st_dev, cur_st.st_ino) != dest_file_identity:
                    should_unlink = False  # File sostituito da altro processo!
            except Exception:
                should_unlink = False
        if should_unlink and (dest_file.is_file() or os.path.lexists(dest_file)):
            dest_file.unlink()
```

---

### 2.2 F2 — Eliminazione File Parziali dopo Errori di Copia (`acquire_forensic_readonly_copy`)

#### Problema Riscontrato:
La funzione registrava i file copiati nella lista di cleanup soltanto al termine dell'operazione di copia. Se un errore I/O o un'interruzione si verificava durante l'apertura in lettura, la scrittura su disco, il flush o il calcolo dell'hash, il file di destinazione parziale o vuoto già creato tramite `os.open` non veniva tracciato e rimaneva orfano nella directory di destinazione.

#### Correzione Implementata:
1. **Registrazione Immediata dell'Ownership**: È stata introdotta la lista `owned_created_files: list[tuple[Path, tuple[int, int] | None]]`. Ciascun file di destinazione (database principale o sidecar `-wal`, `-shm`) viene aggiunto a questa lista **immediatamente** dopo il successo di `os.open(df, flags, 0o600)`.
2. **Pulizia Completa su Qualsiasi Errore**: La funzione interna `_cleanup_owned_files()` rimuove tutti i file parziali creati dall'invocazione corrente in caso di fallimento in apertura, lettura, scrittura, flush o hashing.
3. **Preservazione Risorse Esterne**: Le directory esplicite fornite dall'utente (`dest_dir`) e i file preesistenti legittimi non vengono mai eliminati.
4. **Chiusura Robusta dei File Descriptor**: `fd` viene protetto in un blocco `try/finally` per garantire la chiusura del descrittore OS anche in caso di eccezione prima o durante il context manager `open(fd, "wb", closefd=True)`.

```python
# Estratto implementativo F2 in scripts/preflight_checkpoint_resume.py
fd = os.open(str(df), flags, 0o600)
try:
    f_st = os.fstat(fd)
    f_ident = (f_st.st_dev, f_st.st_ino)
except Exception:
    f_ident = None
# Registrazione immediata prima di qualsiasi lettura o scrittura
owned_created_files.append((df, f_ident))

fd_closed = False
try:
    try:
        with open(fd, "wb", closefd=True) as out_fp:
            fd_closed = True
            with open(sf, "rb") as in_fp:
                while chunk := in_fp.read(65536):
                    out_fp.write(chunk)
                out_fp.flush()
    finally:
        if not fd_closed:
            try:
                os.close(fd)
            except Exception:
                pass
except PermissionError as pe:
    raise SnapshotPermissionDeniedError(...) from pe
except OSError as oe:
    raise SnapshotCreationFailedError(...) from oe
```

---

### 2.3 F3 — Verifica Reale dei Metadati Dichiarati (`_measure_source_file_state`)

#### Problema Riscontrato:
La documentazione tecnica attestava la verifica della quadrupla `(size_bytes, mtime_ns, mode, sha256)`, ma la funzione restituiva una tupla a 3 elementi `(st_size, st_mtime_ns, sha256)`, omettendo il controllo di `st_mode`.
Un test con variazione esclusiva dei permessi (da 0600 a 0644) senza alterazione di byte o mtime consentiva il passaggio della verifica con certificazione positiva `SOURCE_METADATA_UNCHANGED=True` e `SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED=True`.

#### Correzione Implementata:
1. **Inclusione di `st_mode` nello Stato Misurato**: `_measure_source_file_state()` registra ora la tupla a 4 elementi:
   $$\text{file\_state} = (\text{st\_size}, \text{st\_mtime\_ns}, \text{st\_mode}, \text{sha256})$$
   per il database principale e per ciascun sidecar presente (`-wal`, `-shm`, `-journal`).
2. **Confronto Rigoroso tra $t_0$, $t_1$ e $t_2$**:
   ```python
   all_metadata_unchanged = all(
       state_t0[k][:3] == state_t1[k][:3] == state_t2[k][:3] for k in state_t0
   )
   ```
3. **Abort Immediato su Variazione di Modalità**: Se `st_mode` differisce tra $t_0$, $t_1$ o $t_2$, la procedura solleva `SnapshotCreationFailedError` e rifiuta qualsiasi certificazione positiva.
4. **Trasparenza su `atime`**: `atime` rimane formalmente escluso dalle garanzie assolute (essendo soggetto alle policy di mount del kernel `relatime`/`strictatime`), e non viene impiegato alcun artificio retroattivo di riscrittura o chiamata `utime`/`chmod` per mascherare le letture.

---

## 3. SUITE DI REGRESSIONE OBBLIGATORIA (18 NUOVI TEST F1–F3)

All'interno di `tests/unit/test_checkpoint_preflight_resume.py` sono stati integrati i 18 test obbligatori (`test_118`–`test_135`), preservando integralmente tutti i test precedenti.

### Tabella Sinottica delle Prove Eseguite (Requisiti 1–18)

| N. | Metodo di Test | Scenario Testato | Categoria | Esito |
|:---:|:---|:---|:---:|:---:|
| **1** | `test_118_race_between_destination_check_and_o_excl` | Race tra validazione di sicurezza e `O_EXCL`: file creato da terzo prima di `os.open` provoca `FileExistsError` senza essere cancellato | F1 Ownership | **PASS** |
| **2** | `test_119_competing_file_survives_snapshot_failure` | File concorrente preesistente alla destinazione sopravvive al fallimento dello snapshot (`competing_file_survives=True`) | F1 Ownership | **PASS** |
| **3** | `test_120_preexisting_file_not_deleted` | File utente legittimo già esistente nella cartella di destinazione non viene cancellato | F1/F2 Isolation | **PASS** |
| **4** | `test_121_file_created_by_invocation_properly_deleted_after_error` | File creato dall'invocazione corrente viene tempestivamente eliminato in caso di errore durante il backup SQLite | F1 Cleanup | **PASS** |
| **5** | `test_122_file_replaced_by_another_process_before_cleanup` | File sostituito da un processo concorrente (stesso path, diverso inode) prima del cleanup NON viene eliminato | F1 Inode Check | **PASS** |
| **6** | `test_123_error_during_source_db_read_deletes_partial_destination` | Errore I/O simulato durante la lettura del DB sorgente: il file di destinazione parziale creato viene rimosso | F2 Partial Cleanup | **PASS** |
| **7** | `test_124_error_during_destination_db_write_deletes_partial_destination` | Errore I/O simulato (spazio su disco esaurito) durante la scrittura del DB: file parziale eliminato | F2 Partial Cleanup | **PASS** |
| **8** | `test_125_error_during_wal_and_shm_copy_cleans_up_all_partial_files` | Errore I/O durante la copia di `-wal` e `-shm`: pulizia completa di DB e tutti i sidecar parziali posseduti | F2 Multi-Sidecar | **PASS** |
| **9** | `test_126_deletion_of_all_partial_owned_files_across_sidecars` | Fallimento di copia sullo SHM: eliminazione di tutti i file creati fino a quel punto | F2 Multi-Sidecar | **PASS** |
| **10** | `test_127_preservation_of_preexisting_external_directories` | Fallimento di copia: la directory esterna fornita dall'utente e i file interni preesistenti sono preservati | F2 Directory Guard | **PASS** |
| **11** | `test_128_source_permission_change_mode_aborts_without_certification` | Variazione dei soli permessi del sorgente (da 0600 a 0644): abort immediato e zero certificazione | F3 Mode Check | **PASS** |
| **12** | `test_129_metadata_comparison_t0_t1_t2_detects_mode_discrepancy` | Confronto metadati $t_0$, $t_1$, $t_2$: rilevazione discrepanza di `st_mode` a $t_2$ con aborto fail-closed | F3 Metadata Guard | **PASS** |
| **13** | `test_130_no_positive_certification_after_mode_discrepancy` | Nessuna certificazione positiva (`SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED=False`) dopo variazione di permessi | F3 Non-Fakery | **PASS** |
| **14** | `test_131_non_regression_wal_recovery_with_uncheckpointed_frames` | Non regressione recupero WAL: record non consolidati nel db principale vengono estratti dallo snapshot | Non-Regression | **PASS** |
| **15** | `test_132_non_regression_snapshots_and_json_reports` | Non regressione report JSON e snapshot CLI con opzioni sicure e codice di uscita controllato (2) | Non-Regression | **PASS** |
| **16** | `test_133_no_modifications_to_original_files_under_concurrency` | Nessuna alterazione a DB, WAL e SHM sotto carichi di acquisizione ripetuta o concorrente | Non-Regression | **PASS** |
| **17** | `test_134_zero_llm_calls_enforced_under_ownership_and_integrity_checks` | Zero chiamate LLM durante l'esecuzione delle verifiche di ownership e integrità fisica | Zero LLM | **PASS** |
| **18** | `test_135_selective_recovery_24_of_26_only_via_mock` | Ripresa selettiva 24/26: `RUNTIME_VALIDATION_REQUIRED` senza binding; `MOCK_VERIFIED` con mock; mai `SAFE_RESUME` non verificato | Provenance Guard | **PASS** |

---

## 4. ESECUZIONE COMPLETA DELLA SUITE IN UN UNICO PROCESSO PYTEST

L'intera suite di test è stata eseguita concatenando tutti i file di test in un unico processo pytest continuo.

### Parametri e Risultati Ufficiali della Sessione:
- **Ambiente Operativo**: Windows 11 Enterprise (build 26100), x86_64
- **Runtime Python**: Python 3.14.7 (tags/v3.14.7:d0ef3de, Oct 2026)
- **Framework di Test**: pytest-9.1.1, pluggy-1.6.0
- **Comando Eseguito**:
  ```powershell
  .venv\Scripts\python.exe -m pytest tests/unit/test_checkpoint_safe_diagnostics.py tests/unit/test_checkpoint_integrity_scalability.py tests/unit/test_checkpoint_preflight_resume.py -v
  ```
- **Totale Test Raccolti**: 203
- **Test Superati (Passed)**: **200**
- **Test Falliti (Failed)**: **0**
- **Test Saltati (Skipped)**: **3** (`test_47`, `test_63`, `test_66`, specifici di funzionalità POSIX symlink e permessi 0000 su directory che richiedono privilegi root su Linux e vengono saltati su Windows non elevato)
- **Durata di Esecuzione**: **244.86 secondi (04:04)**
- **Exit Code**: **0 (SUCCESS)**
- **File di Log Ufficiale Generato**: `test_results_audit.log` (32.542 byte)

---

## 5. CONFORMITÀ RIGOROSA AI VINCOLI SCIENTIFICI ED ETICI

Si certifica formalmente che:
1. **Componenti Scientifici Intatti**: I file `analysis/service.py`, `analysis/checkpoint.py`, `ai/chunking.py`, `ai/experiment.py` non hanno subito alcuna modifica.
2. **Topic Detection e Topic Discovery**: Gli algoritmi di clustering, estrazione topic ed euristiche sono al 100% identici e non toccati.
3. **Benchmark Congelato**: Il benchmark congelato, i prompt, la ground truth e i dataset non sono stati alterati.
4. **Nessun Accesso a Dati Reali Ubuntu**: Nessuna connessione è stata stabilita verso server remoti o database investigativi reali Ubuntu. I chunk 10 e 19 non sono stati inviati ad alcuna inferenza.
5. **Zero Operazioni Git**: Non è stato eseguito alcun comando `git commit`, `push`, `pull`, `fetch` o `reset`.
6. **Zero Dipendenze Cloud / Vettoriali**: Nessun componente cloud, framework RAG, embedding o database vettoriale è stato introdotto.

---

## 6. SPECIFICHE DEL BUNDLE DI CONSEGNA

Il pacchetto di consegna è generato direttamente nella cartella genitrice:
`c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_checkpoint_wal_failclosed_final_fix_audit_20261020.zip`
(tassativamente **fuori dalla cartella `Tesi`** e **fuori dal repository Git**).

### Contenuto Certificato dell'Archivio:
- **Sorgenti modificati e corretti**:
  - `scripts/preflight_checkpoint_resume.py`
  - `tests/unit/test_checkpoint_preflight_resume.py`
- **Sorgenti di diagnostica e integrità**:
  - `scripts/diagnose_checkpoints_safe.py`
  - `scripts/diagnose_ubuntu_live_24of26.py`
  - `tests/unit/test_checkpoint_safe_diagnostics.py`
  - `tests/unit/test_checkpoint_integrity_scalability.py`
- **Sorgenti applicativi per audit completo**:
  - `ai/`, `analysis/`, `core/`, `entity_resolution/`, `importer/`, `multimodal/`, `normalization/`, `profiler/`, `search/`, `ui/`, `unified/`, `validation/`
- **Configurazioni e dipendenze**:
  - `requirements_audit.txt`, `pyproject.toml`, `MANIFEST.in`
- **Documentazione operativa e tecnica**:
  - `UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md` (aggiornato)
  - `CHATANALYSIS_WAL_FAILCLOSED_INTEGRITY_REPORT.md` (questo rapporto)
- **Prove di audit e certificazione crittografica**:
  - `test_results_audit.log` (log reale dell'esecuzione con exit code 0)
  - `manifest_sha256.txt` (elenco esaustivo degli hash SHA-256 di tutti i file nel bundle)
