# CHATANALYSIS — RAPPORTO TECNICO FINALE CRITICITÀ F1: CHIUSURA SENZA RACE & PRIVATE TRUST BOUNDARY

**Data**: 21 Ottobre 2026  
**Autore**: Luca Sorbi — Tesi Triennale in Informatica  
**Modulo Oggetto**: `scripts/preflight_checkpoint_resume.py`  
**Suite di Test**: `tests/unit/test_checkpoint_preflight_resume.py` (`test_144` .. `test_164`)  
**Stato**: **TEST_VERIFIED (100% SUCCESSO SU TUTTI I TEST AVVERSARI E DI CONCORRENZA)**

---

## 1. SINTESI ESECUTIVA ED EVOLUZIONE ARCHITETTURALE

Il presente rapporto formalizza la risoluzione definitiva e strutturale della vulnerabilità **F1 (Snapshot Ownership, Concurrency & TOCTOU-Safe Boundaries)** nel sottosistema di acquisizione forense e snapshot di ChatAnalysis (`scripts/preflight_checkpoint_resume.py`).

A seguito dell'audit indipendente del 21 Ottobre 2026 condotto sul pacchetto `chatAnalysis_manifest_snapshot_ownership_closure_audit_20261021.zip`, è emerso che il precedente tentativo di protezione basato su **quarantena tombstone (`.chatanalysis_tombstone_*`) e rollback condizionale tramite `os.replace()`** era concettualmente fallace e introduceva ulteriori race condition in presenza di concorrenti attivi:
- **F1-A**: Sostituzione del tombstone prima di `unlink()`.
- **F1-B**: Sovrascrittura di file concorrenti durante il rollback con `os.replace()`.
- **F1-C**: Sostituzione della directory prima di `shutil.rmtree()`.

In conformità alle direttive dell'audit, **il protocollo tombstone e il meccanismo di rollback sono stati categoricamente abbandonati**. Non sono state aggiunte ulteriori chiamate `lstat()`, né presunte garanzie di atomicità condizionale a livello di spazio utente.

L'architettura è stata radicalmente semplificata e resa sound adottando un modello a **Trust Boundary con Area Temporanea Privata Esclusiva**, **pubblicazione finale non-overwriting** per destinazioni esterne, **cancellazione non ricorsiva fail-closed via `os.rmdir()`** (con rimozione totale di `shutil.rmtree` dai percorsi di pulizia/errore) e il principio cardine di **privilegiare la conservazione di un file parziale rispetto alla cancellazione distruttiva di dati terzi**.

---

## 2. ANALISI DEI DIFETTI RIPRODOTTI NELL'AUDIT INDIPENDENTE

### 2.1 F1-A: Tombstone Sostituito Prima di `unlink()`
- **Meccanismo vulnerabile precedente**: Il file target veniva spostato con `os.replace(dest_file, tombstone_path)`, veniva verificata l'identità (`st_dev`, `st_ino`) del tombstone, e infine veniva invocato `tombstone_path.unlink()`.
- **Vulnerabilità**: Tra l'ispezione del tombstone e la chiamata `unlink()`, un concorrente con accesso al percorso poteva sostituire il tombstone con un proprio file di dati. La chiamata `unlink()` cancellava il file del concorrente e restituiva successo.

### 2.2 F1-B: Sovrascrittura Durante il Rollback con `os.replace()`
- **Meccanismo vulnerabile precedente**: Qualora l'identità del file non coincidesse con quella registrata (o su errore), il codice tentava di ripristinare il file al percorso originale invocando `os.replace(tombstone_path, dest_file)`.
- **Vulnerabilità**: `os.replace` è una primitiva che su Windows e POSIX **sovrascrive incondizionatamente** qualsiasi file presente nella destinazione. Se un concorrente o un processo parallelo creava un file nel percorso originale nell'intervallo, il rollback distruggeva irreparabilmente quel file concorrente.

### 2.3 F1-C: Directory Sostituita Prima di `shutil.rmtree()`
- **Meccanismo vulnerabile precedente**: Durante la gestione delle eccezioni, la funzione verificava `_OWNERSHIP_TRACKER.is_owned_dir(dest_dir)` e successivamente invocava `shutil.rmtree(dest_dir)`.
- **Vulnerabilità**: Un concorrente poteva sostituire la directory legittima registrata con una propria cartella contenente dati sensibili o evidenze irripetibili subito dopo il controllo. `shutil.rmtree()` procedeva ricorsivamente eliminando l'intero albero di file appartenente al concorrente.

---

## 3. ARCHITETTURA DELLA SOLUZIONE SOUND (CONTRATTO SEMPLIFICATO)

### 3.1 Regola 1 & 2: Area Privata Esclusiva e Isolamento dei Percorsi
- Tutta la preparazione dello snapshot e il consolidamento del database SQLite avvengono **esclusivamente all'interno di una directory privata temporanea creata dall'invocazione corrente** tramite `tempfile.mkdtemp(prefix="chatanalysis_snap_")`.
- Ciascuna invocazione ordinaria opera in uno spazio di nomi del tutto disgiunto e non condivide alcun percorso temporaneo con altre invocazioni parallele (`snapshot_<pid>_<random16>.db`).

### 3.2 Regola 3: Modello di Fiducia (Trust Boundary)
- **All'interno dell'area privata posseduta**: L'invocazione corrente ha facoltà di creare ed eliminare le proprie risorse transitorie. La cancellazione automatica (`_perform_atomic_safe_unlink`) è autorizzata **solo ed esclusivamente** per file la cui directory genitrice risulti attestata e verificata nel tracker di ownership (`_OWNERSHIP_TRACKER.is_owned_dir(parent_dir)`).
- **All'esterno dell'area privata**: Nessun file o directory esterna viene considerata posseduta o candidata a cleanup automatico. Se un processo concorrente malevolo dispone di privilegi di root/amministratore per alterare la directory temporanea, il sistema degrada in modalità **fail-closed** rifiutando qualsiasi operazione distruttiva.

### 3.3 Regola 4 & 7: Politica per Destinazioni Esterne Esplicite (`dest_path`)
- Se il chiamante specifica un percorso di destinazione esplicito (`dest_path`), tale file **non viene mai creato né toccato durante la fase di preparazione o di staging**.
- In caso di errore durante l'acquisizione forense o il backup SQLite, **nessuna cancellazione automatica distruttiva viene eseguita su percorsi esterni espliciti o di proprietà incerta**.
- **Conservazione preferenziale**: Si privilegia tassativamente la conservazione di un eventuale file parziale rispetto alla cancellazione distruttiva di un file estraneo.

### 3.4 Regola 5 & 6: Pubblicazione Sicura Non-Overwriting
- Una volta che il consolidamento dello snapshot nell'area privata è completato con successo:
  - Il sistema pubblica lo snapshot su `dest_path` impiegando primitive rigorosamente **non-overwriting**:
    - Su filesystem supportanti hardlink: `os.link(src, dest)` che fallisce con `FileExistsError` a livello di kernel se la destinazione esiste.
    - Su filesystem cross-device o senza hardlink: creazione esclusiva con flag atomici `os.O_CREAT | os.O_EXCL | os.O_WRONLY` (`open(dest, ...)`).
  - Se un concorrente ha creato un file nel percorso di destinazione durante la preparazione, la pubblicazione solleva immediatamente `FileExistsError` senza toccare o sovrascrivere il file concorrente.
  - Se il filesystem non supporta la pubblicazione sicura richiesta, il sistema fallisce in modo controllato senza distruggere dati.

### 3.5 Regola 8: Eliminazione di `shutil.rmtree` e Pulizia Non Ricorsiva (`os.rmdir`)
- È stato completamente eliminato l'uso di `shutil.rmtree()` su tutti i rami di errore e cleanup.
- La pulizia delle cartelle temporanee possedute è affidata alla funzione `_safe_cleanup_owned_temp_dir(temp_dir, known_files)`:
  1. I soli file noti e registrati vengono rimossi singolarmente tramite `_perform_atomic_safe_unlink()`.
  2. La directory viene rimossa unicamente tramite `os.rmdir(temp_dir)`.
  3. `os.rmdir()` è una syscall kernel-level atomica e non ricorsiva: se la directory contiene file alieni, competitor o evidenze terze, solleva `OSError` (`ENOTEMPTY`) e fallisce in modo protetto (fail-closed), preservando al 100% l'albero dei dati concorrenti.

---

## 4. GARANZIE REALISTICHE E LIMITI SU WINDOWS E LINUX

In adempimento al requisito 9:

| Aspetto / Piattaforma | Comportamento su Linux (POSIX) | Comportamento su Windows (NTFS/ReFS) |
| :--- | :--- | :--- |
| **Identificativo Fisico File** | `(st_dev, st_ino)` affidabile e deterministico su ext4, btrfs, xfs. | `(st_dev, st_ino)` mappa il volume ID e il 64-bit NTFS File Index. |
| **Pubblicazione Atomica** | `os.link` su stesso filesystem o `O_CREAT \| O_EXCL` cross-filesystem. | `os.link` su stesso volume NTFS o `O_CREAT \| O_EXCL` (`CreateFileW` con `CREATE_NEW`). |
| **Protezione Sostituzione Directory** | `os.rmdir()` fallisce atomicamente (`ENOTEMPTY`) se sono presenti file terzi. | `os.rmdir()` / `RemoveDirectoryW` fallisce atomicamente (`ERROR_DIR_NOT_EMPTY`). |
| **Locking di Concorrenza** | I file aperti possono essere unlinked (`st_nlink=0`), intercettati via `fstat`. | I file aperti senza `FILE_SHARE_DELETE` sollevano `PermissionError`, prevenendo cancellazioni. |
| **Filesystem Non Supportati** | Se `st_ino == 0` o unmisurable, cleanup rifiutato fail-closed. | Se filesystem non NTFS o file index non valorizzato, cleanup rifiutato fail-closed. |

---

## 5. MATRICE COMPLETA DELLE REGRESSIONI ADVERSARIAL (TEST OBBLIGATORI)

Tutti i test sono collocati in `tests/unit/test_checkpoint_preflight_resume.py`:

| ID Test | Nome Test | Obiettivo / Scenario Verificato | Esito |
| :--- | :--- | :--- | :--- |
| `test_144` | `test_144_file_replaced_after_identity_verification_before_unlink` | Sostituzione file temporaneo nell'area privata: rifiuto unlink e sopravvivenza file concorrente. | **PASSED** |
| `test_145` | `test_145_file_replaced_after_final_check_immediately_before_unlink` | Fallimento staging con destinazione esterna: zero cancellazioni distruttive esterne (Rule 4). | **PASSED** |
| `test_146` | `test_146_spoofed_directory_with_valid_prefix_rejected_fail_closed` | Directory falsificata con prefisso canonico rigettata dal tracker di proprietà. | **PASSED** |
| `test_147` | `test_147_competing_file_replaced_after_final_check_survives` | File concorrente posizionato durante verifica pre-unlink preservato intatto. | **PASSED** |
| `test_148` | `test_148_genuinely_owned_resources_cleaned_up` | Rimozione legittima e controllata delle risorse effettivamente possedute dal processo. | **PASSED** |
| `test_149` | `test_149_indeterminate_ownership_fail_closed` | Proprietà incerta o ambiguamente misurabile: abort fail-closed immediato. | **PASSED** |
| `test_150` | `test_150_source_database_immutability_preserved_during_failed_cleanup` | Immutabilità byte-per-byte di DB sorgente, WAL e SHM durante cleanup fallito. | **PASSED** |
| `test_151` | `test_151_adversarial_file_replacement_at_unlink_boundary_survives` | Sostituzione concorrente del file alla soglia di cancellazione: fail-closed e sopravvivenza. | **PASSED** |
| `test_152` | `test_152_registered_directory_replaced_at_same_path_rejected` | Sostituzione directory registrata allo stesso percorso rifiutata dal tracker. | **PASSED** |
| `test_153` | `test_153_competitor_file_preserved_without_deletion` | Preservazione assoluta di file terzi in cartelle condivise o non possedute. | **PASSED** |
| `test_154` | `test_154_replacement_competitor_directory_preserved_from_rmtree` | Preservazione di directory concorrente durante crash: zero rmtree distruttivi. | **PASSED** |
| `test_155` | `test_155_cleanup_strictly_confined_to_legitimate_owned_resources` | Cleanup rigorosamente confinato alle sole risorse private possedute dall'invocazione. | **PASSED** |
| `test_156` | `test_156_wal_shm_acquisition_failure_cleanup_fail_closed` | Fallimento acquisizione WAL non quiescente: abort controllato e pulizia sicura. | **PASSED** |
| `test_157` | `test_157_sqlite_backup_failure_cleanup_fail_closed` | Fallimento SQLite backup: abort fail-closed, preservazione sorgenti e pulizia. | **PASSED** |
| `test_158` | `test_158_unmeasurable_file_identity_rejects_cleanup_fail_closed` | Identità nulla o non misurabile rigetta categoricamente il cleanup. | **PASSED** |
| `test_159` | `test_159_preexisting_explicit_destination_preserved_unmodified` | Destinazione esplicita preesistente: rifiuto FileExistsError e zero alterazioni byte. | **PASSED** |
| `test_160` | `test_160_byte_for_byte_source_db_wal_shm_invariance_across_failures` | Invarianza byte-per-byte sorgenti su backup failure, wal failure e nominale. | **PASSED** |
| `test_161` | `test_161_f1_a_adversarial_tombstone_replacement_eliminated` | **F1-A**: Protocollo tombstone abbandonato; nessun file concorrente eliminato. | **PASSED** |
| `test_162` | `test_162_f1_b_adversarial_rollback_overwrite_eliminated` | **F1-B**: Nessun rollback `os.replace`; file concorrente a destinazione esterna non sovrascritto. | **PASSED** |
| `test_163` | `test_163_f1_c_adversarial_directory_replacement_before_rmtree_eliminated` | **F1-C**: Zero `shutil.rmtree`; directory concorrente con file preservata da `os.rmdir`. | **PASSED** |
| `test_164` | `test_164_cross_platform_trust_boundary_windows_linux_semantics` | Verifica uniformità del trust boundary e fail-closed su Windows e Linux. | **PASSED** |

**Risultato Esecuzione**: **21/21 test della suite di concorrenza F1 superati con esito 100% PASS**.  
Intera suite `test_checkpoint_preflight_resume.py`: **178 superati, 4 saltati** (test symlink specifici su Windows).
