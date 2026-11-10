# RUNBOOK OPERATIVO — VERIFICA SICURA DEI CHECKPOINT SU UBUNTU
**Documento di Istruzioni Operative e Guida all'Ispezione Forense (Versione Hardening Fingerprint & Valori Numerici SQLite)**  
**ID Versione**: `chatAnalysis_checkpoint_diagnostics_fingerprint_numeric_hardening_20261019`  
**Data**: 19 Ottobre 2026  
**Ambiente Target**: Ubuntu 22.04 LTS (Laboratorio), workstation NVIDIA GTX TITAN X 12 GB, LM Studio loopback  
**Percorso Repository**: `/home/nottedeiricercatori/Desktop/LUCA/Tesi`  

---

## 1. OBIETTIVO E GARANZIE DI SICUREZZA DELL'AUDIT

Questo runbook guida l'operatore nell'esecuzione dello strumento diagnostico di sola lettura:
`scripts/diagnose_checkpoints_safe.py`

### Garanzie Assolute di Sicurezza, Privacy e Robustezza:
1. **Accesso Esclusivo in Sola Lettura (Rigido `mode=ro`)**:
   - Apertura SQLite TASSATIVAMENTE con URI `file:{posix}?mode=ro` e `PRAGMA query_only = ON`.
   - Se l'accesso in sola lettura fallisce, l'audit per quel database viene arrestato restituendo il codice standard `SQLITE_READ_ONLY_MODE_FAILED`.
   - **Nessun fallback** a connessioni ordinarie in lettura/scrittura.
2. **Privacy Rigorosa ed Allowlist Chiuse (Zero Leak)**:
   - **Nessun percorso assoluto o personale esposto**: i database sono identificati tramite etichette opache (`database_1`, `database_2`) e ambiti simbolici (`WORKSPACE_LOCAL`, `USER_HOME`, etc.).
   - **Nessun identificativo documentale riconoscibile**: il `document_id` è mascherato tramite token deterministico a hash opaco (`doc_***[token=...,len=...]`), senza alcun prefisso o suffisso del testo originale.
   - **Validazione Fingerprint Canonico SHA-256**: viene accettata come fingerprint mostrabile esclusivamente una stringa esadecimale SHA-256 valida di 64 caratteri (abbreviata a 12 char con ellissi). Qualsiasi stringa non canonica, arbitraria o contenente testo personale viene sostituita con `FINGERPRINT_INVALID_OPAQUE`, senza esporre alcun prefisso o frammento originale.
   - **Allowlist chiusa dei metadati**: campi come `diagnostic_category`, `failure_phase`, `finish_reason`, `model_id`, `prompt_version` e `analysis_type` sono validati rispetto a insiemi chiusi ammessi. Valori non riconosciuti o arbitrari sono sostituiti da codici opachi (`MODEL_OPAQUE`, `PROMPT_VERSION_OPAQUE`, `DIAGNOSTIC_CATEGORY_OPAQUE`, etc.).
   - **Validazione rigorosa dei timestamp**: `created_at` e `updated_at` devono corrispondere a formato ISO 8601 standard; in caso contrario viene restituito `TIMESTAMP_OPAQUE`, escludendo qualsiasi testo, marcatore o nota investigativa.
   - **Nessun messaggio di errore SQLite grezzo**: impiego esclusivo di codici diagnostici predefiniti (`SQLITE_READ_ONLY_MODE_FAILED`, `SCHEMA_MISMATCH`, etc.).
   - **Confronto sui metadati originali**: la compatibilità confronta i valori originali prima della sanificazione per rilevare discrepanze anche tra modelli o prompt non riconosciuti.
3. **Robustezza dei Valori Numerici SQLite**:
   - Validazione preventiva di tipo e intervallo per contatori e indici letti da SQLite (`total_planned_chunks`, `chunk_index`, `attempts`, `http_status`).
   - Gestione trasparente di stringhe non numeriche, valori negativi o irragionevolmente grandi tramite codice standard `NUMERIC_VALUE_MALFORMED`, senza sollevare `ValueError`, senza esporre il valore grezzo e **senza interrompere l'analisi degli altri database**.
4. **Robustezza dello Schema e Gestione Tabelle Incomplete**:
   - Prima di eseguire le interrogazioni, lo script valida staticamente che tutte le colonne obbligatorie (`REQUIRED_TABLE_COLUMNS`) siano presenti nella tabella.
   - In presenza di schemi parziali o tabelle incomplete (es. `CREATE TABLE checkpoints (dummy TEXT);`), restituisce il codice `SCHEMA_MISMATCH` senza sollevare eccezioni e senza interrompere l'analisi degli altri database candidati.
5. **Robustezza su Checkpoint Malformati**:
   - Qualsiasi payload corrotto, troncato o con JSON non valido viene valutato e segnalato come `INVALID_JSON_SYNTAX`, `INVALID_EMPTY_PAYLOAD` o `CORRUPTED_PAYLOAD_EXCEPTION` garantendo la presenza di tutti i campi (incluso `evidence_validation_notice`).
6. **Allineamento al Modello di Dominio Reale (`TopicDiscoveryResult`)**:
   - Una lista `topics` vuota (`[]`) è considerata una struttura valida e conforme (`topic_count: 0`).
   - Per ciascun topic scoperto, `short_description` deve essere una stringa non vuota (invariante di `DiscoveredTopic`).
7. **Query Statiche su Tabelle Note**:
   - Interrogazione statica esclusiva per `checkpoints`, `checkpoint_chunks`, `checkpoint_chunk_diagnostics`.
   - Nessuna interpolazione dinamica da `sqlite_master` né esposizione di tabelle utente arbitrarie.
8. **Rigore Forense sulla Compatibilità**:
   - Se `config_fingerprint` è identico ma i metadati descrittivi differiscono, viene segnalata una `METADATA_DISCORDANCE_ANOMALY` e dichiarata l'incompatibilità a runtime (`runtime_interchangeable: False`).
   - La ripresa delle evidenze NON viene mai dichiarata verificata senza la validazione del runtime applicativo: lo stato è tassativamente `EVIDENCE_VALIDATION_NOT_PERFORMED`.
9. **Descrizione Neutrale dello Stato Osservato**:
   - I checkpoint sono descritti oggettivamente in base ai dati osservati (`OBSERVED_20_OF_26_CHUNKS`, `OBSERVED_24_OF_26_CHUNKS`), senza congetture o etichette premature quali "Run 1" o "Run 2".

---

## 2. PROCEDURA OPERATIVA SU WORKSTATION UBUNTU

### Passo 1: Accesso alla directory del progetto e attivazione ambiente virtuale
Aprire un terminale sulla macchina Ubuntu ed eseguire:
```bash
cd /home/nottedeiricercatori/Desktop/LUCA/Tesi
source .venv/bin/activate
```

### Passo 2: Verifica dello stato Git locale (Nessuna operazione remota)
Verificare che il working tree sia integro:
```bash
git status --short
```
*Attenzione: non eseguire `git pull`, `git fetch`, `git push` o `git reset`.*

### Passo 3: Esecuzione dell'Audit Multi-Database
Eseguire lo script diagnostico:
```bash
python scripts/diagnose_checkpoints_safe.py
```
Lo script individuerà e analizzerà separatamente tutti i database candidati presenti nei percorsi canonici (directory locale `.checkpoints`, root repository, directory utente). Se uno dei database presenta schemi incompleti o colonne mancanti, verrà registrato con codice `SCHEMA_MISMATCH` mentre l'ispezione degli altri database procederà regolarmente.

### Passo 4: Esportazione in Formato JSON Sanificato
Se si desidera esportare o archiviare l'output per l'ispezione dell'audit:
```bash
python scripts/diagnose_checkpoints_safe.py --json > /tmp/sanitized_checkpoint_audit.json
```
Verificare che il file JSON contenga solo riferimenti opachi e nessun identificatore completo, percorso assoluto o testo di errore SQLite grezzo.

### Passo 5: Ispezione di un Database Specifico (opzionale)
Per esaminare uno specifico file database:
```bash
python scripts/diagnose_checkpoints_safe.py --db /home/nottedeiricercatori/.checkpoints/checkpoints.db
```

---

## 3. GUIDA ALL'INTERPRETAZIONE DEI RISULTATI

L'output testuale e JSON struttura l'analisi in sezioni chiare e verificabili:

### 1. Consistenza Tabelle Schema ChatAnalysis
- Interroga staticamente le tabelle `checkpoints`, `checkpoint_chunks` e `checkpoint_chunk_diagnostics`.
- Connessione garantita in `mode=ro` con `PRAGMA query_only = ON`.
- Valida la presenza preventiva di tutte le colonne richieste.

### 2. Testate di Checkpoint Censite
- Mostra tutte le testate censite con `document_id` opaco, modello normalizzato o `MODEL_OPAQUE`, versione prompt, blocchi pianificati e timestamp validati (o `TIMESTAMP_OPAQUE`).

### 3. Chunk Validati per Configurazione
- Riporta:
  - `physical_chunks_count`: numero di chunk fisicamente registrati.
  - `structurally_intact_chunks_count`: chunk conformi al modello di dominio.
  - `compromised_chunks_count`: chunk non conformi, alieni o corrotti.
  - `observed_state`: descrizione oggettiva dello stato osservato (es. `OBSERVED_24_OF_26_CHUNKS`).
  - `evidence_validation`: `EVIDENCE_VALIDATION_NOT_PERFORMED`.

### 4. Stato Approfondito del Chunk 10
- Verifica la presenza fisica e la conformità strutturale del chunk 10.
- Gestisce payload corrotti restituendo sempre la struttura completa senza eccezioni.

### 5. Analisi di Compatibilità delle Configurazioni
- Confronta i metadati **originali** prima della sanificazione.
- Se i documenti differiscono: `INCOMPATIBLE_DIFFERENT_DOCUMENTS`.
- Se i `config_fingerprint` differiscono: `CONFIG_FINGERPRINT_MISMATCH`.
- Se i fingerprint coincidono ma i metadati originali differiscono: `METADATA_DISCORDANCE_ANOMALY`.
- In tutti questi casi: `runtime_interchangeable: False`.

### 6. Diagnostica dei Blocchi Falliti
- Espone categoria diagnostica (`INVALID_EVIDENCE_ID`), stato HTTP (200 OK), fase del fallimento (`evidence_validation`) ed esiti di conformità sintattica.
- Metadati arbitrari o non riconosciuti sono opacizzati.

### 7. Verdetto di Recupero e Linea Guida Operativa
- Dichiara `safe_recovery_verified: False` e `status: STRUCTURAL_RESUMPTION_FEASIBLE_MISSING_10_AND_19` (se i 24 chunk sono strutturalmente conformi).
- Chiarisce che la ripresa applicativa nativa da Streamlit richiede la rigenerazione e validazione probatoria dei soli blocchi mancanti `[10, 19]`.

---

## 4. LINEA GUIDA PER LA RIPRESA APPLICATIVA IN STREAMLIT

Qualora l'audit confermi la presenza strutturale dei 24 blocchi nella sessione attiva:

1. Avviare Streamlit dalla directory del repository:
   ```bash
   cd /home/nottedeiricercatori/Desktop/LUCA/Tesi
   streamlit run app.py
   ```
2. Accedere alla scheda di Topic Discovery e verificare la visualizzazione dello stato parziale (24 blocchi su 26 completati).
3. Utilizzare il comando di ripresa dei soli blocchi falliti:
   - I 24 blocchi già salvati per la medesima configurazione non verranno rielaborati.
   - Verranno rieseguiti con il modello LLM esclusivamente i chunk mancanti (10 e 19).
   - Solo al completamento e alla validazione probatoria di tutti i 26 blocchi la sessione transiterà a stato validato completo.
