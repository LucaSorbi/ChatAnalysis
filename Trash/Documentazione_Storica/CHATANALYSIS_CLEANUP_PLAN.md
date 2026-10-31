# CHATANALYSIS — PIANO DI PULIZIA E ALLEGGERIMENTO DEL REPOSITORY (FASE C)

## 1. OBIETTIVI E PRINCIPI GUIDA

Il presente piano definisce la procedura operativa di pulizia e alleggerimento del repository **ChatAnalysis**, nel rispetto rigoroso dei vincoli accademici, scientifici e forensi:

1. **Invarianza Funzionale e Scientifica**: nessun modulo essenziale, file di configurazione, dataset di prova, fixture sintetica o codice di benchmark viene alterato o rimosso.
2. **Preservazione dei Modelli e del Benchmark Congelato**: i/experiment.py e i/benchmark.py rimangono intatti al 100% (0 diff).
3. **Fail-Closed e Nessuna Cancellazione Indiscriminata**: divieto tassativo di utilizzo di comandi distruttivi (git clean -fdx, git reset --hard o rimozioni cieche).
4. **Backup Verificato Preventivo Esterno**: prima di qualsiasi operazione, tutti i file candidati sono stati compressi nell'archivio esterno verificabile:
   C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021.zip
   con relativo manifest crittografico SHA-256:
   C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021_manifest.sha256.
5. **Archiviazione Esterna degli Script di Audit Storici**: gli script per la generazione di vecchi pacchetti di audit (create_*_archive.py), non utilizzati dai flussi applicativi né dai test, vengono archiviati esternamente nella cartella C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_historical_audit_scripts\ per garantire la tracciabilità storica.
6. **Conservazione Prudenziale**: lo script scripts/create_scalability_audit_archive.py è stato rilevato come dipendenza diretta del test 	ests/unit/test_packaging_release.py e viene **conservato nel repository** per garantire la perfetta esecuzione delle regressioni.

---

## 2. CLASSIFICAZIONE DELLE RISORSE E VALUTAZIONE DEL RISCHIO

In base all'inventario formale preventivo registrato in CHATANALYSIS_FILE_INVENTORY_BEFORE.csv (16.534 file complessivi):

| Classe | Categoria | Conteggio File | Spazio Occupato | Azione Prevista | Rischio Stimato |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Classe A** | Indispensabili (Codice sorgente, CLI, Streamlit, .venv, .git, config) | 15.593 | 1.528,61 MB | **Conservazione integrale** | Nessuno (Preservati) |
| **Classe B** | Scientifici / Forensi (Benchmark, dataset, fixture, checkpoint, WAL/SHM) | 90 | 0,95 MB | **Conservazione integrale** | Nessuno (Preservati) |
| **Classe C** | Temporanei rigenerabili (uild/, dist/, egg-info, .pytest_cache, logs, bytecode *.pyc) | 843 | 24,68 MB | **Rimozione sicura post-backup** | **NULLO** (Rigenerabili automaticamente dal runtime/toolchain) |
| **Classe D** | Storici / Obsoleti (scripts/create_*_archive.py) | 8 | 0,09 MB | **Archiviazione esterna (7 script)** + **Conservazione (1 script per test)** | **NULLO / GESTITO** (Preservati in archivio e directory esterna) |

---

## 3. DETTAGLIO DEI CANDIDATI ALLA RIMOZIONE E ALL'ARCHIVIAZIONE

### 3.1 Classe C — Artefatti Temporanei e Cache Rigenerabili

1. **Directory di build setuptools (uild/)**:
   - *Percorso*: uild/lib/**
   - *Numero file*: 178 file (.py, .pyc)
   - *Spazio*: ~2,75 MB
   - *Motivazione*: Copie di compilazione generate da precedenti invocazioni di pip wheel o setuptools.
   - *Rischio*: **NULLO**. Setuptools e pip rigenerano questa directory su richiesta.

2. **Directory di distribuzione locale (dist/)**:
   - *Percorso*: dist/*.whl
   - *Numero file*: 1 file
   - *Spazio*: ~0,32 MB
   - *Motivazione*: Wheel binaria generata durante i test di packaging.
   - *Rischio*: **NULLO**. Ricostruita all'occorrenza tramite pip wheel.

3. **Metadati di packaging (whatsapp_forensic_importer.egg-info/)**:
   - *Percorso*: whatsapp_forensic_importer.egg-info/**
   - *Numero file*: 5 file
   - *Spazio*: ~0,01 MB
   - *Motivazione*: Metadati temporanei di build locale di setuptools.
   - *Rischio*: **NULLO**.

4. **Cache di Pytest (.pytest_cache/)**:
   - *Percorso*: .pytest_cache/**
   - *Numero file*: 5 file
   - *Spazio*: ~0,23 MB
   - *Motivazione*: Cache interna di pytest per tracking dei fallimenti e metadati step.
   - *Rischio*: **NULLO**. Rigenerata automaticamente al lancio successivo di pytest.

5. **Log di test conclusi (	est_logs/)**:
   - *Percorso*: 	est_logs/*.log
   - *Numero file*: 10 file
   - *Spazio*: ~0,02 MB
   - *Motivazione*: File di log di esecuzioni passate di suite di test.
   - *Rischio*: **NULLO**. I test producono nuovi log durante l'esecuzione se richiesto.

6. **Bytecode precompilato Python (__pycache__/*.pyc)**:
   - *Percorsi*: Tutte le sottocartelle del progetto sorgente (i/, nalysis/, core/, entity_resolution/, importer/, multimodal/, 
ormalization/, profiler/, search/, ui/, unified/, alidation/, scripts/, 	ests/) ad esclusione di .venv/ e .git/.
   - *Numero file*: 733 file
   - *Spazio*: ~21,35 MB
   - *Motivazione*: Bytecode Python compilato automaticamente durante l'esecuzione dell'interprete.
   - *Rischio*: **NULLO**. Python rigenera trasparentemente i file .pyc durante l'import dei moduli.

---

### 3.2 Classe D — Script Storici di Creazione Pacchetti di Audit

Sono stati esaminati gli 8 script di creazione archivi presenti in scripts/:
1. scripts/create_failclosed_integrity_archive.py (8.355 B) -> Archiviazione esterna
2. scripts/create_final_fix_archive.py (8.499 B) -> Archiviazione esterna
3. scripts/create_inode_reuse_ownership_archive.py (8.670 B) -> Archiviazione esterna
4. scripts/create_resume_preflight_archive.py (7.388 B) -> Archiviazione esterna
5. scripts/create_snapshot_isolation_archive.py (8.237 B) -> Archiviazione esterna
6. scripts/create_wal_integrity_archive.py (8.261 B) -> Archiviazione esterna
7. scripts/create_windows_readiness_archive.py (9.120 B) -> Archiviazione esterna
8. scripts/create_scalability_audit_archive.py (34.905 B) -> **CONSERVATO NEL REPOSITORY** (importato attivamente da 	ests/unit/test_packaging_release.py)

I 7 script archiviati esternamente sono stati copiati in:
- Archivio ZIP esterno: C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021.zip
- Directory esterna di consultazione: C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_historical_audit_scripts\

---

## 4. RISORSE RIGOROSAMENTE PROTETTE (NESSUNA CANCELLAZIONE)

Le seguenti categorie sono formalmente escluse da qualsiasi operazione di pulizia:
- Repository Git (.git/) e configurazioni .gitignore.
- Ambiente virtuale locale (.venv/).
- Database SQLite, checkpoint e file di transazione (.checkpoints/, *.db, *.sqlite, *.wal, *.shm).
- Dataset forensi e conversazioni reali/sintetiche (	est_data/, whatsapp_export/, cellebrite_export/).
- Moduli scientifici del benchmark congelato (i/experiment.py, i/benchmark.py).
- Codice applicativo e architetturale in uso (i/, nalysis/, core/, ui/, pp.py, pyproject.toml).
- Tutti i test e le relative fixture sintetiche (	ests/).

---

## 5. PROCEDURA DI ROLLBACK

Qualora si verificasse una qualsivoglia anomalia funzionale o di regressione nei test dopo l'esecuzione della pulizia:
1. I file archiviati sono immediatamente ripristinabili estraendo l'archivio esterno:
   C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021.zip
2. L'integrità di ciascun file ripristinato può essere validata contro il manifest SHA-256:
   C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021_manifest.sha256.
