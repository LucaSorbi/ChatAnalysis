# CHATANALYSIS — RUNBOOK UBUNTU PER L'ACQUISIZIONE FORENSE FAIL-CLOSED E AUDIT PRE-RIPRESA (24/26)

**Data Revisione:** 20 Ottobre 2026 (Final Inode Reuse & Snapshot Ownership Repair Audit)  
**Ambiente Sperimentale Target:** Ubuntu 22.04.5 LTS (x86_64) / Linux generico (senza presupporre Ubuntu 24.04)  
**Infrastruttura:** Python 3.10+, SQLite 3.37+, LM Studio 0.3.x (server locale porta 1234)  
**Sessione di Riferimento:** Topic Discovery interrotta con 24 blocchi su 26  
**Indici Mancanti (Zero-Based):** 10 e 19 *(corrispondenti ai blocchi ordinali 11 e 20 su 26)*  
**Obiettivo Operativo Esclusivo:** Eseguire l'acquisizione forense fail-closed e l'audit preliminare in modalità rigorosamente protetta, garantendo l'immutabilità fisica byte-per-byte verificata dei file originali (database, `-wal`, `-shm`), senza alterazioni di read-mark, senza chiamate LLM e senza avviare la ripresa reale dei chunk 10 e 19.

---

## 1. PRINCIPI DI SICUREZZA, INTEGRITÀ FORENSE E LIMITI DI COERENZA (B1–B5)

> [!CAUTION]
> NON eseguire la ripresa reale o inferenze LLM in questa fase.
> L'obiettivo attuale è condurre l'audit indipendente per validare se i 24 blocchi sono riutilizzabili.
> Il database reale e i relativi file di sessione non devono essere modificati, rinominati o eliminati.

1. **Separazione Totale tra Acquisizione e Motore SQLite (B1/B2):**  
   In modalità WAL (`PRAGMA journal_mode=WAL`), SQLite utilizza il file sidecar `-shm` come indice di memoria condivisa (*WAL-index*). Qualsiasi connessione SQLite (anche con `mode=ro`, `PRAGMA query_only = ON;` o invocando la SQLite Backup API `conn.backup(...)`) aperta direttamente sui file originali aggiorna i *read-marks* nell'header del file `-shm`, alterandone inevitabilmente l'hash crittografico SHA-256 e il timestamp di modifica `mtime`. Per garantire l'immutabilità fisica dei file originali, il preflight ChatAnalysis **NON apre alcuna connessione SQLite sui file originali**: acquisisce preventivamente una copia forense a livello OS (`open(..., "rb")`) in un'area di staging isolata con permessi `0600`. Tutte le successive interrogazioni e diagnostiche SQLite (`PRAGMA integrity_check`, query tabelle) avvengono esclusivamente sulla copia isolata.

2. **Misurazione Fail-Closed dei Sidecar e Divieto di Certificazioni Parziali (B1):**  
   Tutti i sidecar SQLite (`-wal`, `-shm`, `-journal`) sono formalmente contabilizzati e monitorati. La funzione di misurazione distingue esplicitamente un file realmente assente (`FileNotFoundError`) da un file presente ma non accessibile per permessi (`PermissionError`) o errori I/O (`OSError`). In presenza di errori di accesso, permessi o hashing su un sidecar presente, la procedura solleva un'eccezione tipizzata (`SnapshotPermissionDeniedError` o `SnapshotCreationFailedError`) con codice statico, abortendo immediatamente l'acquisizione. Nessuna omissione accidentale produce certificazioni positive; se uno SHM o WAL originale non è misurabile, l'acquisizione è categoricamente respinta.

3. **Aborto Immediato su Discrepanze a $t_2$ e Assenza di Fallback (B2):**  
   Se durante la verifica finale ($t_2$, post-acquisizione o post-audit) si rileva una qualsiasi alterazione dei file sorgenti rispetto a $t_0$ o $t_1$ (differenza di dimensione, `mtime_ns`, hash SHA-256, o variazione dell'insieme dei sidecar presenti):
   - L'acquisizione forense viene dichiarata non riuscita.
   - La procedura viene interrotta immediatamente con eccezione tipizzata sanificata (`SnapshotCreationFailedError`).
   - Vengono eliminati esclusivamente i file temporanei creati dall'invocazione corrente.
   - Tutti i file sorgenti vengono conservati intatti.
   - Nessun fallback permissivo è ammesso e nessun tentativo artificiale di riscrittura/ripristino degli hash originali viene eseguito.

4. **Metrica Tri-State e Nessun Default Hardcoded (B3):**  
   Tutte le metriche di immutabilità (`DATABASE_LOGICAL_CONTENT_UNCHANGED`, `WAL_CONTENT_UNCHANGED`, `SHM_BYTES_UNCHANGED`, `SOURCE_METADATA_UNCHANGED`, `CONSISTENT_SNAPSHOT_VERIFIED`, `SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED`) derivano unicamente dalle misurazioni effettive eseguite durante l'acquisizione. In assenza di misurazione fisica, assumono il valore rigorosamente tipizzato `"NOT_MEASURED"`. `"NOT_MEASURED"` non può mai generare `SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED=True` e impedisce categoricamente l'emissione del verdetto `SAFE_RESUME_VERIFIED`.

5. **Definizione Esatta della Garanzia sui Metadati e Controllo Attivo di `mode` (F3):**  
   La garanzia di immutabilità fisica sui file sorgenti copre rigorosamente:
   - dimensione in byte (`size_bytes` / `st_size`);
   - timestamp di modifica in nanosecondi (`mtime_ns` / `st_mtime_ns`);
   - permessi e modalità POSIX (`mode` / `st_mode`);
   - digest crittografico SHA-256 dei contenuti byte-per-byte.  
   Lo stato misurato a $t_0$, $t_1$ e $t_2$ include attivamente `st_mode`. Se `st_mode` differisce tra $t_0$, $t_1$ o $t_2$, l'acquisizione fallisce immediatamente (fail-closed) e non viene concessa alcuna certificazione positiva.  
   **Trasparenza su `atime` (Access Time):** Su filesystem montati con flag di aggiornamento dell'access time in lettura (`relatime` o `strictatime`), la sola lettura OS per l'hashing altera inevitabilmente `atime_ns`. Lo script impiega `O_NOATIME` su Linux dove supportato e autorizzato dai privilegi del processo (owner del file o `CAP_FOWNER`), ma non assume che funzioni universalmente su tutti i mount point. ChatAnalysis **dichiara con precisione i metadati effettivamente verificati** (`size_bytes`, `mtime_ns`, `mode`, `sha256`), non promette l'immutabilità di metadati non misurati, e **NON esegue mai scritture o chiamate a `utime`/`chmod` a posteriori** per mascherare le letture o falsificare i timestamp/permessi originali.

6. **Quadro di Coerenza Transazionale e Finestra di Quiescenza (B5):**  
   La copia binaria a livello OS del database principale e dei file sidecar (`-wal`, `-shm`) è un'operazione sequenziale e non un'operazione atomica a livello filesystem. Il confronto degli hash $t_0$ e $t_1$ rileva l'assenza di variazioni osservate durante la finestra di copia, ma non equivale a uno snapshot atomico di filesystem (es. LVM o ZFS) né sostituisce la quiescenza operativa. Pertanto:
   - È **obbligatorio** garantire operativamente una finestra di quiescenza (assenza di processi writer concorrenti attivi accertata tramite `lsof` / `fuser`).
   - Se esiste attività di scrittura concorrente non controllata, l'acquisizione deve essere interrotta o non considerata idonea alla certificazione.
   - Non viene implementato alcun meccanismo di locking invasivo che alteri o scriva nei checkpoint originali.
   - La verifica di integrità strutturale SQLite (`PRAGMA integrity_check`) è mantenuta tassativamente sulla sola copia isolata.

7. **Isolamento dell'Area Temporanea, Tracciamento Rigoroso dell'Ownership con Conservazione del Descrittore e Pulizia Fail-Closed (F1/F2/F3):**  
   L'area temporanea per la copia di staging o per lo snapshot consolidato viene creata in una directory dedicata con prefisso univoco e permessi restrittivi `0600` al di fuori del repository `Tesi` (es. `/tmp/chatanalysis_forensic_...` o `/tmp/chatanalysis_snap_...`).
   - **Ownership rigorosa e immunità dal riutilizzo degli Inode (F1 Hardened):** Un file è considerato posseduto dall'invocazione se e soltanto se la creazione esclusiva via `os.open(O_CREAT | O_EXCL)` ha avuto esito positivo. L'invocazione trattiene aperto il descrittore del file (`dest_fd` o `dup_fd`) per tutta la durata dell'operazione. Su sistemi POSIX/Linux, la conservazione del descrittore garantisce che:
     1. Se un processo concorrente o un evento esterno scambia o cancella il file nel filesystem, il descrittore originale continua a puntare all'oggetto originario orfano.
     2. In sede di pulizia su errore, l'invocazione ispeziona preventivamente lo stato del descrittore originale: se `st_nlink == 0`, il file è già stato rimosso o sostituito da terzi nel percorso della directory; l'invocazione dichiara immediatamente `should_unlink = False` e non tocca il percorso, impedendo categoricamente la cancellazione di file concorrenti anche qualora il filesystem Linux riutilizzi all'istante il medesimo inode (`st_ino`).
     3. La rimozione su POSIX avviene prima della chiusura del descrittore (`dest_file.unlink()` mentre `dest_fd` è aperto), eliminando qualsiasi finestra di race post-chiusura.
   - **Politica Fail-Closed su identità non misurata (F1.2):** Se `os.fstat(fd)` o la misurazione iniziale fallisce e `dest_file_identity` oppure `f_ident` risulta `None`, il cleanup applica la politica fail-closed: il file non viene mai cancellato (`should_unlink = False`) per evitare distruzioni improprie di file con identità sconosciuta.
   - **Pulizia immediata dei file parziali (F2):** Ciascun file creato viene registrato come risorsa posseduta immediatamente dopo la creazione esclusiva. In caso di errore durante l'apertura, lettura, scrittura, flush o hashing, tutti i file parziali creati vengono tempestivamente e integralmente rimossi.
   - **Preservazione cartelle utente:** Le directory esplicite fornite dall'utente e i file preesistenti vengono rigorosamente preservati.

8. **Divieto di Falsa Attestazione delle Chunk Boundaries (B2/B4):**  
   I checkpoint SQLite legacy storici non conservano il digest crittografico indipendente delle boundaries dei messaggi nei singoli blocchi. Anche con modello verificato in memoria, il verdetto per i 24 checkpoint Ubuntu è categoricamente `RUNTIME_VALIDATION_REQUIRED` (Exit code 2). Non dichiarare MAI `SAFE_RESUME_VERIFIED` per i checkpoint legacy Ubuntu.

---

## 2. PROCEDURA OPERATIVA UBUNTU (PASSO-PASSO)

### PASSO 1: Identificazione Certa del Database, Sidecar e Verifica Quiescenza
Accedere alla macchina Ubuntu 22.04.5 LTS, posizionarsi nella root del repository ed eseguire i controlli preliminari di quiescenza senza aprire alcuna connessione SQLite:

```bash
cd /percorso/a/ChatAnalysis
source .venv/bin/activate

# 0. Verifica ambiente offline / air-gapped
pip install --no-index --find-links wheels/ -r requirements_audit.txt

# 1. Identificazione certa del database e dei sidecar presenti
ls -la .checkpoints/

# 2. Verifica formale dell'assenza di processi writer attivi (finestra di quiescenza obbligatoria)
# Entrambi i comandi devono restituire esito vuoto
lsof .checkpoints/checkpoints.db* 2>/dev/null
fuser .checkpoints/checkpoints.db* 2>/dev/null

# Se lsof o fuser evidenziano processi attivi, arrestarli o attenderne la fine prima di procedere.
# NON procedere se vi sono scrittori concorrenti attivi!

# 3. Misurazione baseline t0 di integrità fisica dei file sorgenti
sha256sum .checkpoints/checkpoints.db
stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db

if [ -f .checkpoints/checkpoints.db-wal ]; then
    sha256sum .checkpoints/checkpoints.db-wal
    stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db-wal
fi

if [ -f .checkpoints/checkpoints.db-shm ]; then
    sha256sum .checkpoints/checkpoints.db-shm
    stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db-shm
fi
```

---

### PASSO 2: Acquisizione Forense Coerente e Audit Isolato

> [!IMPORTANT]
> Tutte le operazioni di audit o snapshot utilizzano il modulo fail-closed:
> 1. Copia binaria OS in area temporanea isolata con permessi `0600` (`/tmp/chatanalysis_forensic_...`).
> 2. Verifica tripla $t_0$, $t_1$, $t_2$. Se un sidecar scompare, compare o fallisce l'hashing, abort immediato.
> 3. Diagnostica SQLite ed esame dei payload eseguiti **esclusivamente sulla copia isolata**.
> 4. Distruzione automatica della copia temporanea al termine.

```bash
# Esecuzione standard dell'audit con staging forense isolato (read-only):
python3 scripts/preflight_checkpoint_resume.py --db ".checkpoints/checkpoints.db"

# Modalità snapshot consolidato (opzionale):
python3 scripts/preflight_checkpoint_resume.py --db ".checkpoints/checkpoints.db" --snapshot
```

---

### PASSO 3: Preflight Forense Completo con Documento Originale (Zero Inferenze)
Quando si dispone della chat investigativa WhatsApp estratta, associare il documento specificando il formato corretto e i parametri operativi congelati:

> [!CAUTION]
> **DESTINAZIONE REPORT JSON SICURA:**
> Il percorso specificato per `--output-json` DEVE essere un nuovo file in una directory sicura (es. `/tmp/...`). È tassativamente vietato salvarlo dentro `.checkpoints`, dentro la cartella del database o dentro il repository `Tesi`.

```bash
python3 scripts/preflight_checkpoint_resume.py \
    --db ".checkpoints/checkpoints.db" \
    --chat "/percorso/alla/chat_whatsapp_reale.txt" \
    --format "whatsapp_export" \
    --model "qwen2.5-7b-instruct" \
    --resolved-model-key "qwen2.5-7b-instruct" \
    --profile "BALANCED" \
    --check-live-backend \
    --backend-url "http://localhost:1234" \
    --output-json "/tmp/preflight_audit_report_24_26.json"
```

- **Esito atteso per i checkpoint legacy Ubuntu:** Codice di uscita `2` (`RUNTIME_VALIDATION_REQUIRED`).
- **Comportamento backend:** Interroga `GET /api/v1/models` in modalità read-only; zero prompt inviati.

---

### PASSO 4: Verifica Post-Audit dell'Immutabilità dei File Originali ($t_2$)
Al termine di qualsiasi esecuzione, riverificare manualmente che i file originali e i sidecar non abbiano subito alcuna alterazione byte-per-byte:

```bash
sha256sum .checkpoints/checkpoints.db
stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db

if [ -f .checkpoints/checkpoints.db-wal ]; then
    sha256sum .checkpoints/checkpoints.db-wal
    stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db-wal
fi

if [ -f .checkpoints/checkpoints.db-shm ]; then
    sha256sum .checkpoints/checkpoints.db-shm
    stat -c "File: %n | Dimensione: %s byte | mtime_ns: %Y.%N | mode: %a" .checkpoints/checkpoints.db-shm
fi
```

I valori di `sha256sum`, `Dimensione` e `mtime_ns` devono risultare identici a quelli registrati al Passo 1.

---

### PASSO 5: Esame dell'Output Sanificato per l'Audit Indipendente
Estrarre unicamente i dati di certificazione dal report JSON senza esportare testi o metadati sensibili:

```bash
python3 -c "
import json
with open('/tmp/preflight_audit_report_24_26.json') as f:
    r = json.load(f)
summary = {
    'verdetto': r['verdict'],
    'verification_classification': r.get('verification_classification'),
    'blocchi_totali': r['total_planned_chunks'],
    'blocchi_completati': r['completed_chunks_count'],
    'indici_mancanti_zero_based': r['missing_chunks_zero_based'],
    'runtime_binding_status': r['configuration_details'].get('runtime_binding_status'),
    'source_immutability_status': r.get('source_immutability_status'),
    'controlli_superati': [c['name'] for c in r['checks'] if c['status'] == 'PASS'],
    'controlli_falliti': [c['name'] for c in r['checks'] if c['status'] == 'FAIL'],
}
print(json.dumps(summary, indent=2))
"
```

Stato atteso delle metriche di immutabilità:
- `DATABASE_LOGICAL_CONTENT_UNCHANGED: true`
- `WAL_CONTENT_UNCHANGED: true`
- `SHM_BYTES_UNCHANGED: true`
- `SOURCE_METADATA_UNCHANGED: true` *(dimensione, mtime_ns, mode)*
- `CONSISTENT_SNAPSHOT_VERIFIED: true`
- `SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED: true`

---

## 3. INTERPRETAZIONE DEI CODICI DI USCITA (EXIT CODES)

| Exit Code | Verdetto Emesso | Significato Operativo |
|:---:|:---|:---|
| **0** | `SAFE_RESUME_VERIFIED` | **TUTTI I CONTROLLI SUPERATI CON BINDING ATTIVO E PROVA COMPLETA DELLE BOUNDARIES.** Modello accertato in memoria e partizionamento crittograficamente verificato. Emesso unicamente se sussiste prova crittografica indipendente delle boundaries persistita nel database storico. **Non applicabile ai 24 checkpoint legacy storici Ubuntu.** |
| **1** | `NOT_COMPATIBLE` | **CONTROLLO FALLITO O IMMUTABILITÀ VIOLATA.** Discrepanza accertata di fingerprint, parametri, modello, corruzione record o fallimento della misurazione di immutabilità sorgente ($t_0 \neq t_1$ o $t_2 \neq t_0$). Procedura interrotta. Zero modifiche al database. |
| **2** | `RUNTIME_VALIDATION_REQUIRED` | **ESITO ATTESO PER I 24 CHECKPOINT UBUNTU.** Struttura e prove conformi, ma limite storico delle boundaries: per i checkpoint SQLite legacy privi di hash indipendente delle boundaries dei messaggi, la compatibilità configurazionale e probatoria è provata (24 blocchi recuperabili, 2 mancanti), ma l'identità matematica completa delle boundaries richiede validazione runtime all'avvio. **NON dichiara MAI `SAFE_RESUME_VERIFIED` prematuramente.** |
| **3** | `ERROR` | **ERRORE CONTROLLATO SENZA TRACEBACK NÉ PERCORSI.** Percorso database non trovato, sidecar presente ma non leggibile (`PermissionError`), violazione o errore durante la creazione dello snapshot isolato (`SNAPSHOT_DESTINATION_FORBIDDEN`, `SNAPSHOT_DIRECTORY_INVALID`, `SNAPSHOT_PERMISSION_DENIED`, `SNAPSHOT_CREATION_FAILED`). Messaggi diagnostici statici, zero esposizione di dati e zero modifiche al database originale. |

---

## 4. DIVIETO DI RIPRESA AUTOMATICA E ATTESTAZIONE FINALE

1. **DIVIETO DI AVVIO AUTOMATICO:** Non eseguire alcuna ripresa reale in questa fase. La ripresa reale dei chunk 10 e 19 NON deve essere eseguita adesso: attendere l'esito dell'audit indipendente del presente bundle.
2. **Dichiarazione di Non Esecuzione su Dati Reali:** Nessuna prova di preflight né comando operativo è stato eseguito sulla macchina Ubuntu reale o sui dati investigativi reali. Tutti i test e le verifiche di immutabilità sono stati validati in ambiente sintetico isolato con dati fittizi.
