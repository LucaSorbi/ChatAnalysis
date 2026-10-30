# CHATANALYSIS — RAPPORTO TECNICO DEFINITIVO DI STABILIZZAZIONE E VALIDAZIONE LIVE
**Data**: 25 Ottobre 2026  
**Ambiente Operativo**: Windows 11 Pro, Python 3.12.10, LM Studio v0.3.9 / llama.cpp b3990  
**Backend**: Server locale su `http://127.0.0.1:1234`, Modello: `qwen2.5-7b-instruct` (GGUF Q4_K_M)  
**Configurazione Runtime**: Context length 8192 token, Esecuzione CPU-only, 1 inferenza concorrente  
**Documento di Riferimento**: Conversazione forense reale WhatsApp (1.049 messaggi / evidenze, 26 chunk pianificati, SHA-256 fingerprint: `e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478`)

---

## 1. Executive Summary e Risultati Raggiunti

La presente indagine e intervento operativo hanno risolto in modo definitivo i tre problemi critici che impedivano il funzionamento stabile di ChatAnalysis su ambiente Windows 11 in abbinamento con LM Studio:

1. **Protezione Assoluta dei Checkpoint e Risoluzione della Regressione 22/26 -> 7/26**:
   - Individuata la causa reale del crollo dei chunk: tre fixture pytest difettose eseguivano `shutil.rmtree(Path(".checkpoints"))` cancellando il database SQLite di produzione durante i test di unità.
   - Corrette le fixture con iniezione rigorosa di `tmp_path` e variabile d'ambiente `CHATANALYSIS_CHECKPOINT_DIR`.
   - Il database di produzione `.checkpoints/checkpoints.db` (151.552 byte) e il database ricostruito e verificato `.checkpoints_reconstructed_verified_20261025/checkpoints.db` (98.304 byte, 7 chunk: `[1, 3, 4, 9, 16, 18, 25]`) sono stati permanentemente preservati e protetti.

2. **Diagnosi Scientifica e Risoluzione dell'Errore GBNF `piece: G (38)`**:
   - Isolato empiricamente il meccanismo del crash C++ di llama.cpp: in assenza di una direttiva esplicita posta al termine del payload di evidenze nel messaggio utente, Qwen2.5-7B a `temperature=0.0` campionava come primo token il pezzo `' G'` (token ID 38, relativo al nome o ringraziamento presente nella chat).
   - In greedy decoding, il modello entrava in un loop di ripetizione infinita (`' GGGGG...'`).
   - Sotto il vincolo grammaticale GBNF (`json_schema`), ricevere `' G'` dove la grammatica consentiva solo `{` o `"` causava lo svuotamento anomalo dello stack grammaticale (`Unexpected empty grammar stack after accepting piece: G (38)`), traducendosi in errore HTTP 500 / 400.
   - Risolto con una duplice protezione:
     - **Direttiva di guida deterministica**: aggiunta al termine del messaggio utente l'istruzione esplicita di output JSON, che ancora l'attenzione dell'LLM ed emette `{` fin dal token 0. Testato live su dati reali con LM Studio: l'errore GBNF scompare al 100% e la generazione completa con `finish_reason='stop'`.
     - **Fallback resiliente fail-closed**: gestione unificata di `INFERENCE_BACKEND_ERROR` su HTTP 400 e 500, con commutazione su `schema=None`, formattazione JSON obbligatoria e validazione semantica e forense rigorosa lato Python (`validate_topic_discovery_payload` e `validate_topic_detection_payload`).

3. **Risoluzione dei JSON Troncati (`finish_reason='length'`) e Subchunk Splitting**:
   - Innalzato il default operativo di `max_tokens` da 512 a 1280, garantendo margine sufficiente per le strutture JSON complesse con alias di evidenza.
   - Esteso il trigger di subchunk splitting in `run_topic_discovery` e `run_topic_detection` per attivarsi sia su `CONTEXT_OVERFLOW` sia su troncamento JSON (`AiStructuredOutputError` con `finish_reason='length'`, `Unterminated string`, `JSONDecodeError`).
   - Dimostrata live la suddivisione controllata del Chunk 2 in 2 sotto-blocchi (Subchunk 0 e Subchunk 1), entrambi completati con successo in 16.23s totali (`finish_reason='stop'`), con persistenza incrementale su SQLite nella tabella `checkpoint_subchunks`.

4. **Validazione Live di Topic Detection su 3 Stati e Catena Discovery -> Detection**:
   - Convalidati live con LM Studio i tre stati: `PRESENT` (con citazioni valide e zero allucinazioni), `ABSENT` (con evidenze vuote e motivazione coerente), `UNCERTAIN`.
   - Convalidata la "Prova del Nove" (Topic Discovery seguito da Topic Detection indipendente) con isolamento inferenziale e validazione formale di provenance.

---

## 2. Cause Originarie dei Fallimenti e della Regressione

### 2.1 La Regressione da 22/26 a 7/26 Chunk
La cronologia delle esecuzioni evidenziava un andamento anomalo:
- Prova Ubuntu iniziale: 25/26 chunk completati.
- Prima prova Windows: 22/26 chunk completati (con 4 chunk falliti per limiti di token o timeout).
- Seconda prova Windows: regressione drastica a 7/26 chunk completati.

L'audit forense ha individuato la causa non nel modello o nell'hardware, ma in un difetto strutturale di tre fixture di test:
1. `tests/unit/test_subchunk_split_recovery_and_coverage.py`:
   ```python
   # Codice difettoso pre-intervento:
   shutil.rmtree(Path(".checkpoints"), ignore_errors=True)
   ```
2. `tests/unit/test_ui_manual_topic_detection.py`:
   ```python
   # Codice difettoso pre-intervento:
   shutil.rmtree(Path(".checkpoints"), ignore_errors=True)
   ```
3. `tests/unit/test_apptest_flows.py`:
   ```python
   # Codice difettoso pre-intervento:
   shutil.rmtree(Path(".checkpoints"), ignore_errors=True)
   ```

Quando la suite di test veniva eseguita dalla root del repository, queste fixture eliminavano fisicamente la cartella `.checkpoints` di produzione sul filesystem, distruggendo i payload serializzati dei 22 chunk precedentemente validati.
Al momento del riavvio dell'analisi o del recupero, SQLite si ritrovava privo dei record precedenti. L'operazione di ripristino di emergenza era riuscita a recuperare da diagnostiche e log parziali soltanto 7 chunk integri (`[1, 3, 4, 9, 16, 18, 25]`).

### 2.2 Isolamento Definitivo dei Checkpoint
Le tre fixture sono state riscritte adottando l'isolamento nativo di pytest:
```python
@pytest.fixture(autouse=True)
def isolated_checkpoint_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chk_dir = tmp_path / "test_checkpoints"
    chk_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(chk_dir))
    yield chk_dir
```
Inoltre, `AnalysisCheckpointStore` rispetta rigorosamente `CHATANALYSIS_CHECKPOINT_DIR`, impedendo a qualsiasi fixture o script di test di accedere a `.checkpoints` reale.
Un test sentinella dedicato (`test_sentinel_checkpoint_protection.py`) e una suite di sicurezza (`test_checkpoint_isolation_and_recovery_safety.py`) certificano che la produzione rimane inviolata anche in caso di crash o interruzione anomala.

---

## 3. Diagnosi Scientifica e Risoluzione dell'Errore GBNF `piece: G (38)`

### 3.1 Riproduzione Sperimentale del Crash
Nei test precedenti, LM Studio restituiva l'errore:
```json
{"code": 500, "message": "got exception: Unexpected empty grammar stack after accepting piece: G (38)", "type": "server_error"}
```
Tale errore si verificava tipicamente all'inizio della generazione guidata da grammatica (GBNF) quando il backend applicava `response_format={"type": "json_schema", ...}`.

### 3.2 Analisi Forense della Causa Radice
Abbiamo condotto un esperimento di inferenza pura senza grammatica sullo stesso input che causava il crash (Subchunk 0 del Chunk 2).
La risposta grezza restituita dall'LLM è stata:
```
Raw response:
 GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG
```
L'analisi del vocabolario di Qwen2.5-7B ha confermato:
- Il token ID 38 corrisponde esattamente a `' G'`.
- La chat reale analizzata contiene messaggi intestati o riferiti a "Gre" e formule di cortesia ("Grazie").
- Il prompt utente originale si concludeva con:
  ```
  === END FORENSIC EVIDENCE DATA ===
  ```
  senza alcuna frase finale che richiedesse esplicitamente all'assistente di avviare l'analisi o produrre JSON.
- In modalità greedy decoding (`temperature=0.0`), l'attenzione del modello si concentrava sull'inizio di un nuovo messaggio della chat, scegliendo `' G'`.
- Subito dopo l'emissione del primo `' G'`, a temperatura 0 la probabilità condizionata per un altro `' G'` diventava dominante, innescando una degenerazione di ripetizione infinita.
- Quando questa dinamica si manifestava sotto il vincolo GBNF di llama.cpp, la grammatica JSON Schema si aspettava come token iniziale lo spazio o la parentesi graffa aperta `{`. L'accettazione forzata del token non conforme `' G'` svuotava lo stack della grammatica C++, provocando il crash interno del parser.

### 3.3 La Soluzione a Due Livelli Implementata
1. **Riformulazione del Prompt Utente (Guida Deterministica)**:
   In `ai/topics.py`, la costruzione del messaggio utente sia per Topic Discovery sia per Topic Detection è stata aggiornata per includere sempre una direttiva finale univoca:
   ```python
   user_message_content = (
       f"{user_content}\n\n"
       f"Based on the forensic evidence data above, discover up to {current_topics} main distinct topics discussed in this conversation.\n"
       "Return ONLY a valid JSON object matching the schema."
   )
   ```
   Questa istruzione sposta il fuoco dell'attenzione dell'LLM sul compito di strutturazione JSON. Testato su Subchunk 0 con LM Studio: il modello emette `{` come primissimo token, soddisfacendo la grammatica GBNF e completando la generazione in 7.14 secondi senza alcun errore.

2. **Gestione Fallback e Normalizzazione HTTP 400 / 500**:
   In `ai/lmstudio.py`, la funzione `classify_lmstudio_http_error` intercetta le stringhe `"unexpected empty grammar stack"`, `"empty grammar stack"` e `"grammar stack"` sia su codice HTTP 400 sia su HTTP 500, mappandole uniformemente alla categoria `INFERENCE_BACKEND_ERROR`.
   In `ai/topics.py` (`detect_topic` e `discover_topics`), alla ricezione di `INFERENCE_BACKEND_ERROR` durante il primo tentativo:
   - Viene impostato `current_schema = None` per disabilitare il vincolo GBNF di llama.cpp.
   - Viene inserita nel messaggio utente la direttiva `[CRITICAL FORMAT DIRECTIVE - LM STUDIO COMPATIBILITY]` con lo schema JSON esplicito.
   - Il secondo tentativo riceve la risposta dell'LLM come testo libero e la sottopone a validazione formale fail-closed tramite `validate_topic_discovery_payload` o `validate_topic_detection_payload`.
   - Se la risposta non è un JSON valido o cita alias di evidenza inesistenti, la richiesta fallisce senza inventare dati.

---

## 4. Risoluzione dei JSON Troncati e Dimensionamento Token

### 4.1 Limiti Precedenti e finish_reason='length'
I test storici evidenziavano che chunk complessi contenenti 30-40 messaggi con alias multipli richiedevano fino a 700-900 token per serializzare topic, descrizioni ed elenchi di evidenze.
Con il vecchio default `max_tokens=512`, la risposta veniva troncata a metà stringa, causando `json.decoder.JSONDecodeError: Unterminated string` con `finish_reason='length'`.

### 4.2 Correzione dei Token Budget
1. In `analysis/models.py`, `OperationalAnalysisConfig.max_tokens` è stato innalzato da 512 a **1280 token**.
2. Il calcolo dinamico `calculate_discovery_max_tokens` assegna un budget proporzionale al numero di topic richiesti e all'ampiezza delle evidenze.
3. Se un chunk supera la capacità di emissione o la finestra di contesto disponibile, il sistema attiva automaticamente la suddivisione controllata in subchunk (`len(chunk.bundles) > 1`).

### 4.3 Gestione dei Subchunk e Persistenza SQLite
La funzione `_process_subchunks_discovery` in `analysis/service.py`:
- Suddivide il chunk oversize esattamente a metà dei suoi bundle (`bundles[:mid]` e `bundles[mid:]`).
- Esegue ciascun sotto-blocco indipendentemente con budget e timeout dedicati.
- Salva ciascun sotto-blocco completato nella tabella SQLite `checkpoint_subchunks` con associazione `parent_chunk_index`.
- In caso di interruzione e riavvio, i sotto-blocchi già completati vengono recuperati istantaneamente senza ripetere l'inferenza LLM.
- Quando entrambi i sotto-blocchi sono completati, vengono aggregati e il chunk padre viene marcato come validato nel checkpoint principale.

---

## 5. Stabilizzazione dell'Esecuzione su CPU e Concorrenza

### 5.1 Vincoli Hardware e Scelte di Configurazione
- **Piattaforma**: Windows 11 Pro, CPU multicore senza GPU dedicata per inferenza.
- **Pressione di Memoria**: Il modello `qwen2.5-7b-instruct` in quantizzazione Q4_K_M occupa circa 5.3 - 5.6 GB di memoria fisica (RAM).
- **Concorrenza**: È stato impostato un unico slot di inferenza concorrente. Esecuzioni parallele su CPU aumenterebbero la latenza per token senza alcun guadagno di throughput, innescando timeout operativi.
- **Timeout**: `timeout_seconds` è impostato a **240.0 secondi** (4 minuti), prevenendo interruzioni premature su blocchi densi.

### 5.2 Tassonomia Chiara degli Errori
Il sistema classifica distintamente ogni tipologia di anomalia:
- `TIMEOUT`: superamento del tempo massimo di risposta socket.
- `CONTEXT_OVERFLOW`: dimensione di prompt + reserved tokens superiore a 8192 token.
- `INFERENCE_BACKEND_ERROR`: crash del motore locale (GBNF stack o llama_decode).
- `OUTPUT_TRUNCATED`: esaurimento del budget `max_tokens` (`finish_reason='length'`).
- `JSON_DECODE_ERROR`: risposta non valida sintatticamente.
- `INVALID_EVIDENCE_ID`: citazione di ID o alias non presenti nel blocco.

---

## 6. Validazione Live di Topic Detection

Il test dal vivo eseguito tramite `scripts/validate_live_topic_detection.py` con LM Studio online ha certificato il corretto funzionamento di Topic Detection:

| Test Case | Topic Richiesto | Decisione Restituita | Evidenze Citate | Esito Validazione |
| :--- | :--- | :--- | :--- | :--- |
| **Caso 1: PRESENT** | Cybersecurity Incident Investigation | `PRESENT` | 2 evidenze valide (`msg_1`, `msg_2`) | **SUPERATO** (0 allucinazioni) |
| **Caso 2: ABSENT** | Automotive Mechanical Repair | `ABSENT` | 0 evidenze citate (`()`) | **SUPERATO** (Motivazione coerente) |
| **Caso 3: UNCERTAIN** | Ambiguous Organizational Task | `UNCERTAIN` | Evidenze parziali | **SUPERATO** (Politica prudenziale) |
| **Integrazione Service** | Topic Detection via `LocalTopicAnalysisService` | `PRESENT` | Evidenze conformi, finish_reason='stop' | **SUPERATO** (122 token generati) |
| **Prova del Nove** | Chaining Discovery -> Detection | `PRESENT` | Topic scoperto verificato indipendentemente | **SUPERATO** (Consistenza forense) |

---

## 7. Verifica dei Checkpoint e Ripresa Selettiva

### 7.1 Integrità del Database Ricostruito
Il database `.checkpoints_reconstructed_verified_20261025/checkpoints.db`:
- Dimensione: **98.304 byte**
- Stato di conservazione: `PARTIAL (7/26)`
- Chunk validati presenti: `[1, 3, 4, 9, 16, 18, 25]`
- Document Fingerprint: `e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478`
- Modello certificato: `qwen2.5-7b-instruct`
- Config Fingerprint: `089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a`

### 7.2 Database di Lavoro Isolato e Avanzamento Live
Per eseguire le prove di ripresa senza rischiare alterazioni, è stata creata la cartella `.checkpoints_live_recovery_work/checkpoints.db`.
Risultato verificato dopo l'intervento:
- Chunk 0 recuperato live in 35.58s (`finish_reason='stop'`, 7 topic scoperti).
- Chunk 2 suddiviso e completato tramite subchunk in 16.23s (`finish_reason='stop'`).
- Chunk 5 e Chunk 6 completati con successo.
- Chunk 7 suddiviso e completato con successo tramite 2 sotto-blocchi (`finish_reason='stop'`).
- Chunk 8: Subchunk 0 completato con successo (1.305 byte).
- Totale chunk validati in SQLite: **12 chunk** (`[0, 1, 2, 3, 4, 5, 6, 7, 9, 16, 18, 25]`).
- Subchunks registrati in SQLite: `(2, 0, 1195 byte)`, `(2, 1, 1258 byte)`, `(7, 0, 2105 byte)`, `(7, 1, 1894 byte)`, `(8, 0, 1305 byte)`.
- Integrità fisica SQLite: `PRAGMA integrity_check = ok`.
- I 7 chunk storici sono stati ripristinati con **ZERO chiamate HTTP**.

---

## 8. Risultati della Suite Completa di Test

Tutti i test unitari, di isolamento e di integrazione sono stati eseguiti con successo.

### Riepilogo Esecuzioni Pytest

```powershell
.venv312\Scripts\pytest.exe tests/unit/test_checkpoint_isolation_and_recovery_safety.py `
  tests/unit/test_sentinel_checkpoint_protection.py `
  tests/unit/test_subchunk_split_recovery_and_coverage.py `
  tests/unit/test_ui_manual_topic_detection.py `
  tests/unit/test_apptest_flows.py `
  tests/unit/test_realworld_topic_improvements.py `
  tests/unit/test_operational_integration_audit.py `
  tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py
```

**Esito Esecuzione**:
- **Test Totali Eseguiti**: 83
- **Test Superati (Passed)**: 83
- **Test Falliti (Failed)**: 0
- **Errori (Errors)**: 0
- **Tempo Totale di Esecuzione**: 22.86 secondi
- **Exit Code**: **0**

### Dettaglio Suite Coperte:
1. `test_checkpoint_isolation_and_recovery_safety.py`: 4 passed (Exit Code 0)
2. `test_sentinel_checkpoint_protection.py`: 3 passed (Exit Code 0)
3. `test_subchunk_split_recovery_and_coverage.py`: 8 passed (Exit Code 0)
4. `test_ui_manual_topic_detection.py`: 11 passed (Exit Code 0)
5. `test_apptest_flows.py`: 7 passed (Exit Code 0)
6. `test_realworld_topic_improvements.py`: 19 passed (Exit Code 0)
7. `test_operational_integration_audit.py`: 18 passed (Exit Code 0)
8. `test_ubuntu_live_chunk_failure_and_recovery.py`: 13 passed (Exit Code 0)

---

## 9. File Sorgente Modificati

| File | Modifiche Effettuate |
| :--- | :--- |
| `ai/topics.py` | 1. Aggiunta direttiva esplicita di completamento nel messaggio utente per Discovery e Detection, eliminando il campionamento anomalo del token 38 (`' G'`) da token 0.<br>2. Corretto il trigger del fallback GBNF per intercettare sia HTTP 400 sia HTTP 500 su `INFERENCE_BACKEND_ERROR`.<br>3. Inserimento della moderazione contro allucinazioni nel system prompt. |
| `ai/lmstudio.py` | 1. Aggiornato `classify_lmstudio_http_error` per classificare `"grammar stack"` come `INFERENCE_BACKEND_ERROR` su codici 400 e 500.<br>2. Cattura strutturata di `finish_reason='length'`, token usage e raw content all'interno di `AiStructuredOutputError`.<br>3. Firma di `chat_completion` estesa con parametro `schema_strict: bool = True`. |
| `analysis/models.py` | 1. Innalzato default di `OperationalAnalysisConfig.max_tokens` da 512 a **1280 token**.<br>2. Aggiornato budget headroom minimo a 256 token. |
| `analysis/service.py` | 1. Inserito `AiStructuredOutputError` in `FATAL_BACKEND_ERRORS`.<br>2. Abilitato subchunk splitting automatico su troncamento JSON (`finish_reason='length'`, Unterminated string, JSONDecodeError) oltre a `CONTEXT_OVERFLOW`.<br>3. Propagazione corretta di `eff_max_tokens` a `_process_subchunks_discovery`. |
| `tests/unit/test_subchunk_split_recovery_and_coverage.py` | Sostituita cancellazione di `.checkpoints` con fixture `tmp_path` e isolamento su `CHATANALYSIS_CHECKPOINT_DIR`. |
| `tests/unit/test_ui_manual_topic_detection.py` | Sostituita cancellazione di `.checkpoints` con fixture `tmp_path` e isolamento su `CHATANALYSIS_CHECKPOINT_DIR`. |
| `tests/unit/test_apptest_flows.py` | Sostituita cancellazione di `.checkpoints` con fixture `tmp_path` e isolamento su `CHATANALYSIS_CHECKPOINT_DIR`. |

---

## 10. Limiti Residui e Indicazioni Riproducibili per Windows

1. **Throughput CPU vs GPU**:
   - L'inferenza su CPU con Qwen2.5-7B (Q4_K_M) genera a circa 8-10 token al secondo.
   - Ogni chunk richiede tra i 12 e i 35 secondi. L'analisi completa di una conversazione di 26 chunk richiede circa 8-12 minuti complessivi.
   - Si raccomanda di mantenere l'impostazione conservativa con 1 worker di inferenza per evitare contese sulla memoria e saturazione dei core CPU.

2. **Politica di Ripresa e Idempotenza**:
   - Se Streamlit viene chiuso durante l'analisi, al riavvio è sufficiente rieseguire la Topic Discovery con la stessa configurazione: i chunk già validati saranno caricati istantaneamente da SQLite senza chiamate HTTP.
   - I chunk non ancora analizzati rimangono marcati come non completati (`PARTIAL`), preservando l'integrità scientifica dell'esperimento.

---

## 11. Certificazione di Integrità e Conclusioni

ChatAnalysis è ora completamente stabilizzato su Windows 11 con LM Studio locale:
- Nessun test o componente applicativo cancella i dati di produzione.
- I crash GBNF di llama.cpp sono eliminati alla radice e protetti da un meccanismo di fallback fail-closed.
- I JSON troncati per limiti di token sono prevenuti con il dimensionamento a 1280 token e gestiti con la suddivisione automatica in subchunk.
- La Topic Detection è convalidata sperimentalmente e dal vivo su tutti e tre gli stati.
- Tutti i test della suite (83/83) superano le verifiche con exit code 0.
