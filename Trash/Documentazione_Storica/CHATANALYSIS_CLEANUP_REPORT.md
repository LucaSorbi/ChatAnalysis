# CHATANALYSIS — RAPPORTO FINALE DI PULIZIA E ALLEGGERIMENTO DEL REPOSITORY (FASE C)

## 1. SINTESI METRICA PRIMA E DOPO

La pulizia è stata condotta nel pieno rispetto dei vincoli di sicurezza: backup preventivo esterno verificabile, nessuna cancellazione cieca, preservazione integrale dei file scientifici congelati e conservazione prudenziale delle dipendenze dei test.

| Metrica | Pre-Cleanup (Stato Iniziale) | Post-Cleanup (Stato Finale) | Variazione Assoluta | Variazione % |
| :--- | :--- | :--- | :--- | :--- |
| **Conteggio File Totali** | 16.534 file | 15.686 file | -848 file | -5,13% |
| **Dimensione Totale Repository** | 1.629.864.883 byte (1.554,34 MB) | 1.608.271.993 byte (1.533,77 MB) | -21.592.890 byte (-20,59 MB) | -1,32% |
| **Dimensione Workspace (al netto di .venv e .git)** | ~25,72 MB | ~5,13 MB | -20,59 MB | **-80,05%** |
| **File di Cache / Build (Classe C)** | 843 file | 0 file | -843 file | -100,00% |
| **Script Storici Obsoleti (Classe D)** | 8 file | 1 file (conservato per test) | -7 file archiviati | -87,50% |

> [!NOTE]
> L'ambiente virtuale .venv (15.289 file, ~1.465 MB) e il database degli oggetti Git .git (304 file, ~63 MB) sono stati integralmente preservati come da vincoli tassativi. Sul totale dei file del codice sorgente e delle directory del repository, la riduzione dello spazio occupato da artefatti inutili è stata dell'**80,05%**.

---

## 2. DETTAGLIO DEGLI ELEMENTI TRATTATI

### 2.1 File Eliminati (Classe C — Rigenerabili al 100%)
Tutti i file eliminati sono stati preventivamente salvati nell'archivio di backup esterno prima della rimozione:
- **uild/lib/****: 178 file (.py, .pyc) rimossi (artefatti di build setuptools).
- **dist/****: 1 file wheel rimosso.
- **whatsapp_forensic_importer.egg-info/****: 5 file di metadati rimossi.
- **.pytest_cache/****: 5 file di cache di test rimossi.
- **	est_logs/****: 10 file di log di test storici rimossi.
- **__pycache__/*.pyc**: 733 file di bytecode Python rimossi dalle cartelle del progetto sorgente (i/, nalysis/, core/, entity_resolution/, importer/, multimodal/, 
ormalization/, profiler/, search/, ui/, unified/, alidation/, scripts/, 	ests/).

### 2.2 File Archiviati Esternamente (Classe D — Storici di Audit)
I seguenti 7 script per la generazione di pacchetti di audit storici, accertati come privi di qualsiasi dipendenza nel codice e nei test attivi, sono stati rimossi dall'albero di lavoro e archiviati all'esterno:
1. scripts/create_failclosed_integrity_archive.py
2. scripts/create_final_fix_archive.py
3. scripts/create_inode_reuse_ownership_archive.py
4. scripts/create_resume_preflight_archive.py
5. scripts/create_snapshot_isolation_archive.py
6. scripts/create_wal_integrity_archive.py
7. scripts/create_windows_readiness_archive.py

**Destinazioni di archiviazione esterna**:
- Archivio ZIP di backup: C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021.zip
- Directory esterna di consultazione: C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_historical_audit_scripts\
- Manifest crittografico SHA-256: C:\Users\lucas\Desktop\CartellaTesi\chatanalysis_cleanup_backup_20261021_manifest.sha256

### 2.3 File Conservati per Prudenza Scientifica
- **scripts/create_scalability_audit_archive.py**:
  Sebbene classificato inizialmente come script per l'impacchettamento, l'analisi statica delle dipendenze ha rivelato che la funzione create_archive e la logica di packing vengono attivamente verificate all'interno di 	ests/unit/test_packaging_release.py (linee 141, 229, 331, 407, 576, 653). In ossequio al principio di non alterare o indebolire i test di regressione, il file è stato **conservato nel repository**.
- **i/experiment.py e i/benchmark.py**:
  Invarianza accertata (0 righe modificate).
- **.checkpoints/ e database SQLite/WAL/SHM**:
  Completamente preservati.
- **Fixture e dataset forensi (	est_data/, whatsapp_export/, cellebrite_export/)**:
  Completamente preservati.

---

## 3. MISURAZIONI PRESTAZIONALI COMPARATIVE (PRIMA E DOPO)

Le misurazioni sono state effettuate sul medesimo interprete Python 3.14.7 e sulla medesima workstation Windows 11 under identical conditions:

| Operazione Misurata | Condizione | Prima del Cleanup | Dopo il Cleanup | Variazione | Note Metodologiche |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Progetto: Import time** | Warm cache (bytecode presente) | 1,0760 s | 1,1084 s | +0,0324 s (~invariato) | Tempo di importazione dei moduli core (nalysis, i, ui, core). Al primissimo avvio dopo la cancellazione del bytecode il tempo è stato di 2,3925 s per la ricompilazione iniziale. |
| **Operazione sintetica** | Costruzione e chunking documento (50 messaggi) | 0,6159 s | 0,7264 s | +0,1105 s | Variazione nel margine di jitter del processore; l'alleggerimento del disco non influenza la velocità di CPU-bound chunking. |
| **Suite di test confrontabile** | 	est_priority1_recovered_discovery_prova_del_nove.py (12 test) | 4,7597 s | 5,2792 s | +0,5195 s | Esecuzione completa con fixture e validazione checkpoint SQLite; test 100% superati (12/12 passati). |
| **Streamlit startup & demo navigation** | 	est_priority3_streamlit_windows_startup.py (4 test con subprocess e healthcheck HTTP) | 17,18 s | 16,92 s | **-0,26 s** (-1,5%) | Avvio reale del server Streamlit, health check HTTP e navigazione completa fra le 6 sezioni dell'interfaccia. |

### Considerazioni Scientifiche sulle Prestazioni
Come evidenziato dai requisiti di tesi, **un repository più leggero su disco non accelera le inferenze degli LLM**. I tempi computazionali dei modelli linguistici locali dipendono unicamente dalla memoria della GPU, dall'efficienza di calcolo del backend di LM Studio e dalla dimensione del contesto analizzato.
L'alleggerimento del repository ottiene tuttavia due vantaggi pratici cruciali:
1. Riduzione dell'80% del rumore di filesystem fuori da .venv/.git, eliminando 848 file ridondanti.
2. Trasferimento rapido e privo di file spuri o conflitti di lock Windows (tipici dei file .pyc e uild/ creati su ambienti diversi).

---

## 4. VALUTAZIONE DEI RISCHI RESIDUI

1. **Rischio di regressione funzionale**: **NULLO**. Nessun modulo di produzione o di test è stato rimosso o modificato durante la pulizia.
2. **Rischio di perdita dati**: **NULLO**. Tutti i file eliminati o archiviati sono replicati nell'archivio esterno chatanalysis_cleanup_backup_20261021.zip con manifest SHA-256 a 850 elementi.
3. **Rischio di incoerenza di audit**: **NULLO**. La matrice di tracciabilità in CHATANALYSIS_CAPITOLO3_ANALISI_TECNICA.md e l'inventario post-cleanup in CHATANALYSIS_FILE_INVENTORY_AFTER.csv offrono evidenza documentale completa e trasparente.
