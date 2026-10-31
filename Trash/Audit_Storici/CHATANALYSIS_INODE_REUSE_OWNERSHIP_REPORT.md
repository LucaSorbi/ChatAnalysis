# CHATANALYSIS — RAPPORTO TECNICO: CORREZIONE FINALE OWNERSHIP SNAPSHOT E RIUTILIZZO INODE (F1–F3)

**Risoluzione Definitiva dell'Ownership Fail-Closed e Protezione contro il Riutilizzo degli Inode su Linux**  
**ID Versione**: `chatAnalysis_checkpoint_inode_reuse_ownership_audit_20261020`  
**Data**: 20 Ottobre 2026  
**Autore**: Team Antigravity AI / Forensic Software Engineering  
**Stato**: Concluso — Zero modifiche al motore scientifico / benchmark — Suite Linux: 211 Passed (100%) — Suite Windows: 207 Passed, 4 Skipped (100%)

---

## 1. CONTESTO DELL'AUDIT E RIPRODUZIONE DEL GUASTO

### 1.1 Risultati dell'Audit Indipendente Precedente (Linux Python 3.13.5)
Il bundle `chatAnalysis_checkpoint_wal_failclosed_final_fix_audit_20261020.zip` è stato sottoposto ad audit indipendente su Linux (Python 3.13.5). L'ispezione ha prodotto:
- **Manifest SHA-256**: 113/113 verificati correttamente.
- **Compileall**: PASS.
- **Suite di test**: 201 passed, 1 failed, 1 skipped.
- **Test fallito**: `test_122_file_replaced_by_another_process_before_cleanup`.
- **Verdetto**: RELEASE GATE FAILED per incompletezza della correzione F1.

### 1.2 Causa Radice (Root Cause): Riutilizzo Immediato dell'Inode
Nel codice precedente di `scripts/preflight_checkpoint_resume.py`:
1. `create_consistent_readonly_snapshot()` creava il file di destinazione tramite `os.open(..., O_CREAT | O_EXCL)`.
2. Misurava l'identità del file `(st_dev, st_ino)` tramite `os.fstat(fd)` e **chiudeva immediatamente** il descrittore `os.close(fd)`.
3. Nel test di concorrenza (`test_122`), un processo simulato cancellava il file orfano appena chiuso e ne creava uno nuovo con lo stesso percorso.
4. Su filesystem Linux (es. ext4, tmpfs), l'allocatore degli inode riutilizza frequentemente e immediatamente l'ultimo inode liberato. Su 50 cicli consecutivi eseguiti durante l'audit, il kernel ha riassegnato lo stesso inode per 50 volte consecutive.
5. In sede di cleanup su errore, il codice eseguiva `curr_stat = os.lstat(dest_file)` e verificava `(curr_stat.st_dev, curr_stat.st_ino) == dest_file_identity`.
6. Poiché l'inode era identico per riutilizzo, il cleanup riconosceva erroneamente il nuovo file concorrente come proprio e ne eseguiva la cancellazione `dest_file.unlink()`.

---

## 2. ARCHITETTURA DELLA CORREZIONE DEFINITIVA (F1 HARDENED)

La nuova architettura non si affida unicamente a `(st_dev, st_ino)` dopo la chiusura del file, ma stabilisce un contratto rigoroso di **ownership persistente e verificabile** attraverso:

### 2.1 Mantenimento del Descrittore Originario Aperto (Open Descriptor Retention)
- In `create_consistent_readonly_snapshot()`, il descrittore `dest_fd` ottenuto da `os.open(..., O_CREAT | O_EXCL)` viene **trattenuto aperto** per l'intera durata dell'operazione.
- In `acquire_forensic_readonly_copy()`, il descrittore aperto per ciascun file di staging (`src_name`) viene duplicato tramite `dup_fd = os.dup(fd)` e memorizzato nel registro delle risorse aperte (`opened_descriptors`).
- **Proprietà POSIX fondamentale**: finché un descrittore di file rimane aperto nel processo, il kernel Linux garantisce che l'inode associato non possa essere riciclato per nessun altro file nel filesystem, anche se il percorso nella directory viene scollegato (`unlink`).

### 2.2 Rilevamento dello Scollegamento della Directory (`st_nlink == 0`)
Se un processo concorrente o un attore esterno rimuove o sostituisce il file dal filesystem durante l'operazione:
- Il nostro descrittore aperto continua a puntare all'oggetto orfano originario.
- Eseguendo `fd_st = os.fstat(dest_fd)` (o `os.fstat(dup_fd)`), il contatore dei collegamenti hardware `fd_st.st_nlink` risulta esattamente **`0`**.
- La procedura di cleanup verifica formalmente:
  ```python
  if fd_st.st_nlink == 0:
      # Il nostro file originario è già stato scollegato o sostituito da terzi nel filesystem.
      # Qualsiasi file attualmente presente nel percorso appartiene a un altro processo!
      should_unlink = False
  ```
- Questo meccanismo garantisce in modo matematico che il cleanup **non cancellerà mai** un file sostitutivo, persino nell'eventualità in cui il sistema operativo o un mock simuli la coincidenza di `st_dev` e `st_ino`.

### 2.3 Rimozione Atomica su POSIX Prima della Chiusura
- Su piattaforme POSIX/Linux, la rimozione del file posseduto (`dest_file.unlink()`) viene eseguita **mentre il descrittore `dest_fd` è ancora attivo**.
- Solo dopo che il nome nella directory è stato scollegato con successo, il descrittore viene chiuso via `os.close(dest_fd)`.
- Questa sequenza elimina qualsiasi finestra di race post-chiusura.

### 2.4 Compatibilità Multi-Piattaforma Windows (Win32 Semantics)
- Su Windows, il locking predefinito a livello kernel vieta la cancellazione di file con descrittori aperti (`PermissionError: WinError 32`).
- Pertanto, su `os.name == "nt"`, il descrittore viene chiuso in modo controllato dopo la verifica di identità e `st_nlink`, consentendo al cleanup di operare senza errori di violazione di condivisione e senza compromettere la semantica di sicurezza.

### 2.5 Politica Fail-Closed su Identità Non Misurata
- Se `os.fstat(fd)` o `_measure_source_file_state()` fallisce per qualsiasi anomalia del kernel o I/O (`dest_file_identity is None` o `f_ident is None`), la procedura rifiuta categoricamente di cancellare il percorso:
  - Lo stato viene marcato `should_unlink = False`.
  - Viene sollevata immediatamente l'eccezione tipizzata `SnapshotCreationFailedError` con messaggio sanificato.
  - Nessun file con identità ignota o non comprovata viene mai toccato.

---

## 3. PRESERVAZIONE TOTALE DELLE CORREZIONI F2 ED F3

La correzione dell'ownership è stata integrata preservando rigorosamente tutte le garanzie precedentemente convalidate:
1. **Registrazione immediata dei file parziali (F2):** ciascun file creato tramite `O_CREAT | O_EXCL` viene aggiunto al set delle risorse possedute `owned_created_files` prima di qualsiasi operazione di scrittura o hashing.
2. **Cleanup integrale dei parziali:** in caso di eccezioni durante lettura o scrittura del database o dei sidecar (`-wal`, `-shm`), tutti i file registrati vengono eliminati tempestivamente.
3. **Controllo dei sidecar WAL e SHM:** misurazione rigorosa dello stato a $t_0$, $t_1$ e $t_2$, con verifica dell'invarianza di dimensione, hash crittografico, `mtime_ns` e permessi `mode`.
4. **Misurazione attiva di `st_mode` (F3):** rilevamento di eventuali mutazioni nei permessi dei file sorgente tra $t_0$, $t_1$ e $t_2$ con abort fail-closed immediato.
5. **Esclusione documentata di `atime`:** gestione trasparente di filesystem con `relatime`/`strictatime` e divieto assoluto di scritture fittizie o alterazioni a posteriori di timestamp/permessi.
6. **Protezione della directory `.checkpoints`:** divieto di creazione di file o snapshot all'interno della directory dei checkpoint sorgente o del repository.
7. **Reportistica JSON sicura:** zero leak di percorsi assoluti sensibili, zero esposizione di dati personali o digest esadecimali completi a 64 caratteri.

---

## 4. MATRICE COMPLETA DEI TEST E RISULTATI

### 4.1 Test Chiave di Ownership e Inode Reuse
| ID Test | Descrizione e Vincolo di Prova | Esito Windows | Esito Linux (Ubuntu) |
|:---|:---|:---:|:---:|
| `test_122` | **Test originale preservato:** file sostituito da altro processo prima del cleanup NON viene eliminato | **PASSED** | **PASSED** |
| `test_136` | Sostituzione del file con inode differente: preservazione garantita del file concorrente | **PASSED** | **PASSED** |
| `test_137` | **Sostituzione con inode immediatamente riutilizzato:** prova deterministica con mock di `(st_dev, st_ino)` identico | **PASSED** | **PASSED** |
| `test_138` | Sostituzione in directory esterna esplicita: la directory utente e il file concorrente sopravvivono | **PASSED** | **PASSED** |
| `test_139` | Fallimento di `fstat` su snapshot: fail-closed categorico, nessuna cancellazione su identità non misurata | **PASSED** | **PASSED** |
| `test_140` | Errore prima della registrazione ownership: nessun cleanup improprio se `os.open` fallisce | **PASSED** | **PASSED** |
| `test_141` | Errore post-registrazione: pulizia esclusiva del file posseduto, file contigui lasciati intatti | **PASSED** | **PASSED** |
| `test_142` | Fallimento di `fstat` durante copia forense: abort fail-closed immediato | **PASSED** | **PASSED** |
| `test_143` | Riutilizzo immediato dell'inode durante la copia di staging forense (`acquire_forensic_readonly_copy`) | SKIPPED (Win32 lock) | **PASSED** |

### 4.2 Risultati Suite Completa su Linux (Ubuntu 22.04 / WSL2)
- **Comando eseguito**:
  ```bash
  python3 -m pytest tests/unit/test_checkpoint_safe_diagnostics.py tests/unit/test_checkpoint_integrity_scalability.py tests/unit/test_checkpoint_preflight_resume.py -v
  ```
- **Exit Code**: `0`
- **Riepilogo**: **211 passed in 234.63s (0:03:54)**
- **Verdetto**: **100% PASS — ZERO ERRORI — ZERO TEST REGREDITI**

### 4.3 Risultati Suite Completa su Windows (Python 3.14)
- **Comando eseguito**:
  ```powershell
  python -m pytest tests/unit/test_checkpoint_safe_diagnostics.py tests/unit/test_checkpoint_integrity_scalability.py tests/unit/test_checkpoint_preflight_resume.py -v
  ```
- **Exit Code**: `0`
- **Riepilogo**: **207 passed, 4 skipped in 204.49s (0:03:24)**
  *(I 4 test skipped corrispondono a semantiche strettamente POSIX: symlink permission check, chmod 0000 dir check, chmod 0000 shm check, e race di sostituzione con file lock Win32 attivo).*

---

## 5. COMANDI OPERATIVI E CONFORMITÀ SCIENTIFICA

### 5.1 Verifica Immutabilità e Assenza di Inferenze
1. **Nessuna chiamata LLM**: tutti i test e le routine di verifica sono vincolati a 0 chiamate di inferenza (garantito da asserzioni in `test_115`, `test_134` e architettura mock).
2. **Nessun avvio dei chunk 10 e 19**: la procedura si limita all'ispezione dello stato e alla preparazione forense fail-closed, nel rigoroso rispetto del benchmark congelato.
3. **Nessuna modifica al motore scientifico**: i file `analysis/service.py`, `analysis/checkpoint.py`, `ai/chunking.py`, `ai/experiment.py` e il dataset di Topic Detection/Discovery sono rimasti rigorosamente immutati.

---

## 6. PACCHETTO DI CONSEGNA E INTEGRITÀ

- **Nome Archivio Esatto**: `chatAnalysis_checkpoint_inode_reuse_ownership_audit_20261020.zip`
- **Posizione di Creazione**: creata **direttamente fuori da `Tesi`** e fuori dal repository Git:
  `c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_checkpoint_inode_reuse_ownership_audit_20261020.zip`
- **Contenuto**:
  - `scripts/preflight_checkpoint_resume.py` (correzione F1 hardened)
  - `tests/unit/test_checkpoint_preflight_resume.py` (test 122 + nuove regressioni 136–143)
  - `UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md` (runbook operativo aggiornato)
  - `CHATANALYSIS_INODE_REUSE_OWNERSHIP_REPORT.md` (questo rapporto tecnico)
  - `test_results_audit.log` (log completo dell'esecuzione della suite di audit)
  - Sorgenti reali applicativi, dipendenze (`wheels/`, `requirements_audit.txt`), manifest SHA-256.
