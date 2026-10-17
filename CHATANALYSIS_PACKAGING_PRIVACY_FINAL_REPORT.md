# CHATANALYSIS — RAPPORTO FINALE GUARDRAIL PRIVACY DEL PACKAGING
**Data**: 17 Ottobre 2026  
**Ambiente di convalida**: Windows (sviluppo) in preparazione al rilascio per Ubuntu (test scalabilità)  
**Deliverable primario**: `chatAnalysis_packaging_privacy_final_audit_20261017.zip`  
**File di verifica hash**: `chatAnalysis_packaging_privacy_final_audit_20261017.zip.sha256`  

---

## 1. OBIETTIVO E CONTESTO DELL'AUDIT

A seguito dell'audit indipendente su `chatAnalysis_scalability_packaging_guardrails_audit_20261017.zip`, è stato confermato che:
- La duplicazione del manifest è stata completamente risolta.
- Le 3 fixture sintetiche autorizzate sono presenti.
- 315/315 hash SHA-256 nel manifest sono verificati e corretti.
- I test di packaging superano la verifica su Linux.

L'audit ha tuttavia rilevato **due specifici casi limite di sicurezza/privacy**:
1. **Database non approvato con estensione non standard**: un file non approvato come `test_data/local_messages.sqlite3` (o `.db3`, o file laterali WAL/SHM o database rinominati con estensioni innocue) veniva potenzialmente incluso nello ZIP perché il filtro considerava un set limitato di estensioni (`.db`, `.sqlite`, `.zip`).
2. **Attraversamento tramite collegamenti simbolici (Symlinks)**: un symlink interno al repository puntante a un file o a una directory esterna alla radice del progetto veniva seguito (`os.walk(followlinks=...)` o `Path.read()`), causando la fuga di dati esterni non autorizzati all'interno dell'archivio.

Il presente intervento implementa i guardrail definitivi in `scripts/create_scalability_audit_archive.py` ed estende la suite di test in `tests/unit/test_packaging_release.py`, senza alterare in alcun modo il codice applicativo, le ottimizzazioni di scalabilità o i file scientifici congelati.

---

## 2. MODIFICHE IMPLEMENTATE

Tutte le modifiche sono state circoscritte a due soli file:
1. `scripts/create_scalability_audit_archive.py`
2. `tests/unit/test_packaging_release.py`

### 2.1 Guardrail Anti-Symlink e Confine di Radice (Path Traversal Protection)
- **Rifiuto tassativo di qualsiasi symlink a file**:
  ```python
  if fpath.is_symlink():
      return False
  ```
- **Rifiuto tassativo di qualsiasi symlink a directory**:
  Nel ciclo `os.walk(repo_root, followlinks=False)`, le directory individuate come symlink vengono potate immediatamente:
  ```python
  dirs[:] = [
      d for d in dirs
      if d not in EXCLUDE_DIRS
      and not d.endswith(".egg-info")
      and not (Path(root) / d).is_symlink()
  ]
  ```
- **Risoluzione e confinamento entro la radice del repository**:
  Ogni percorso viene risolto con `fpath.resolve()` e viene verificato che appartenga strettamente a `repo_root_resolved`:
  ```python
  resolved = fpath.resolve()
  if not resolved.is_relative_to(repo_root_resolved):
      return False
  ```
  In questo modo, nessun link simbolico o relativo né path traversal verso file esterni (es. cartelle utente, system directories) può essere letto o inserito nell'archivio.

### 2.2 Estensione del Blocco Database e File Laterali SQLite
L'insieme delle estensioni bloccate è stato ampliato per includere tutte le convenzioni SQLite e i file transazionali/laterali:
```python
DATABASE_EXTENSIONS: frozenset[str] = frozenset({
    ".db", ".sqlite", ".sqlite3", ".db3", ".s3db", ".sl3",
    ".wal", ".shm", ".journal",
})

DATABASE_SIDECAR_SUFFIXES: tuple[str, ...] = (
    "-wal", "-shm", "-journal",
)
```
Se un file termina con queste estensioni o con i suffissi transazionali SQLite (es. `msgstore.db-wal`, `wa.db-shm`), esso è ammesso **esclusivamente** se figura nell'allowlist approvata.

### 2.3 Rilevamento della Firma Magica SQLite (Binary Magic Header Inspection)
Per impedire il mascheramento di database SQLite tramite rinomina con estensioni innocue (es. `.txt`, `.csv`, `.dat`), è stata introdotta la funzione `has_sqlite_signature()` che ispeziona i primi 16 byte del file:
```python
SQLITE3_MAGIC_HEADER: bytes = b"SQLite format 3\x00"

def has_sqlite_signature(fpath: Path) -> bool:
    try:
        if fpath.is_symlink() or not fpath.is_file():
            return False
        if fpath.stat().st_size >= 16:
            with open(fpath, "rb") as f:
                header = f.read(16)
                return header == SQLITE3_MAGIC_HEADER
    except (OSError, PermissionError):
        pass
    return False
```
Qualsiasi file contenente l'header binario `SQLite format 3\x00` viene classificato come database e, a meno che non sia una delle 3 fixture sintetiche autorizzate, viene categoricamente scartato.

### 2.4 Preservazione Rigorosa delle Tre Fixture Sintetiche
L'allowlist `APPROVED_SYNTHETIC_FIXTURES` garantisce la presenza immutata e legittima delle sole fixture sintetiche richieste per i test:
```python
APPROVED_SYNTHETIC_FIXTURES: frozenset[str] = frozenset({
    "test_data/whatsapp_export/msgstore.db",
    "test_data/whatsapp_export/wa.db",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
})
```

### 2.5 Generazione di un Unico Manifest SHA-256 Valido
- `MANIFEST_SHA256.txt`, `MANIFEST.txt` e qualsiasi file `*.sha256` vengono scartati durante la scansione dei file sorgente.
- Il manifest viene compilato ordinando deterministicamente tutti i file inclusi.
- Il manifest viene scritto una sola volta nello ZIP al termine della procedura (`z.writestr("MANIFEST_SHA256.txt", manifest_content)`).
- `MANIFEST_SHA256.txt` non elenca mai se stesso né vecchie istanze residue.

---

## 3. SUITE DI TEST DI PACKAGING

Nel file `tests/unit/test_packaging_release.py` sono stati eseguiti 4 test approfonditi:

1. **`test_packaging_wheel_contains_analysis_and_core_packages`**:
   Costruisce una wheel isolata (`pip wheel --no-deps --no-build-isolation`), verifica che contenga tutti i 6 moduli di `analysis/` e tutti i 10 package fondamentali, ed esegue un test di import isolato in sottoprocesso (`PYTHONPATH` limitato alla sola directory estratta, privo di accesso al codice sorgente locale).
   - **Esito**: `PASSED`

2. **`test_packaging_archive_double_run_no_duplicate_manifest`**:
   Esegue due confezionamenti consecutivi, simula un vecchio file manifest presente nel filesystem e verifica l'assenza assoluta di duplicati e l'integrità 1-a-1 degli hash SHA-256.
   - **Esito**: `PASSED`

3. **`test_packaging_archive_rejects_unapproved_db_under_test_data`**:
   Riproduce il caso limite 1 iniettando:
   - `test_data/local_messages.sqlite3`
   - `test_data/backup.db3`
   - `test_data/whatsapp_export/msgstore.db-wal` e `-shm`
   - `test_data/temp.journal`
   - File con firma magica SQLite rinominati in `innocent_export.txt` e `disguised_chat.csv`
   - File `.db` e `.sqlite` non approvati sia in root che in sottocartelle
   Verifica che le 3 fixture autorizzate siano presenti e che **tutti** i file non approvati sopra elencati siano rigorosamente esclusi dall'archivio ZIP.
   - **Esito**: `PASSED`

4. **`test_packaging_archive_rejects_symlinks_and_external_traversal`**:
   Riproduce il caso limite 2:
   - Crea symlink reali a file e directory esterni contenenti segreti fittizi.
   - Verifica che nessun file o cartella collegata simbolicamente venga inclusa o letta.
   - Esegue verifiche deterministiche tramite Mock su `should_pack_file` per confermare il rigetto incondizionato di qualsiasi symlink e di percorsi che risolvono all'esterno del repository.
   - **Esito**: `PASSED`

**Riepilogo esecuzione pytest**:
```
tests/unit/test_packaging_release.py::test_packaging_wheel_contains_analysis_and_core_packages PASSED [ 25%]
tests/unit/test_packaging_release.py::test_packaging_archive_double_run_no_duplicate_manifest PASSED [ 50%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_unapproved_db_under_test_data PASSED [ 75%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_symlinks_and_external_traversal PASSED [100%]
4 passed in 47.35s
```

---

## 4. INTEGRITÀ DEI BENCHMARK SCIENTIFICI E CODICE APPLICATIVO

Come prescritto dal protocollo, nessun file applicativo, benchmark o algoritmo scientifico è stato modificato:
- `ai/experiment.py`: `91100c0a84cb4a8b7c2eef34a06cff9ec21aa6e6eec14ffd09ce6fea8bcee845` (INVARIATO)
- `ai/benchmark.py`: `dc57081dbce3463a8017aa26bb4e4a18889af0a3296853fe40968561d6d44d36` (INVARIATO)
- `run_benchmark.sh`: `7f9cae97df2fae1bc3b5964e639b9f80e3ac5a388ec9c3e20305ff64d48044a7` (INVARIATO)

Nessuna operazione git mutativa (`commit`, `push`, `pull`, `fetch`, `reset`) è stata eseguita.

---

## 5. GENERAZIONE E AUDIT DELL'ARCHIVIO FINALE

L'archivio è stato generato esternamente alla directory Git, al percorso:
`c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_packaging_privacy_final_audit_20261017.zip`

### 5.1 Caratteristiche dell'Archivio
- **Nome file**: `chatAnalysis_packaging_privacy_final_audit_20261017.zip`
- **Percorso assoluto**: `C:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_packaging_privacy_final_audit_20261017.zip`
- **Dimensione**: ~1.44 MB
- **Conteggio file totali nello ZIP**: 317 (316 file sorgente/documentali + 1 manifest)
- **File Checksum separato**: `C:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_packaging_privacy_final_audit_20261017.zip.sha256`
- **Presenza MANIFEST_SHA256.txt**: Esattamente 1 istanza, valida, che certifica 316 file senza auto-referenzialità.
- **Fixture Sintetiche**: 3/3 presenti (`msgstore.db`, `wa.db`, `synthetic_whatsapp_chat.zip`).
- **Database non autorizzati**: 0 presenti (rigorosamente esclusi anche `.sqlite3`, `.db3`, `.wal`, `.shm`, `.journal`).
- **File con firma binaria SQLite non approvati**: 0 presenti.
- **Symlink e percorsi esterni**: 0 presenti.
- **Dati personali / Checkpoint reali / Modelli LLM**: 0 presenti.
- **Esito Audit di Integrità e Privacy**: 100% CONFORME.


