# CHATANALYSIS — RAPPORTO TECNICO DI VALIDAZIONE: COPERTURA FORENSE, RECUPERO SOTTO-BLOCCHI, ISOLAMENTO MODELLO E INTEGRITÀ DIAGNOSTICA

**Data di rilascio:** 16 Ottobre 2026  
**Identificativo Release:** `chatAnalysis_split_recovery_coverage_integrity_20261016`  
**Archivio di Consegna:** `chatAnalysis_split_recovery_coverage_integrity_20261016.zip`  
**Autore / Ruolo:** Team di Sviluppo e Hardening Forense ChatAnalysis  
**Stato di Validazione:** VERIFICATO E SUPERATO (1784 test passati, 0 falliti)

---

## 1. CONTESTO, OBIETTIVI E QUADRO GENERALE

In continuità con la release `chatAnalysis_live_recovery_integration_validation_20261016.zip`, l'audit indipendente ha confermato la solidità delle fondamenta del sistema:
- Manifest SHA256: 317/317 corrispondenti al 100%.
- Sorgenti della wheel: 89/89 file perfettamente allineati.
- Benchmark scientifico invariato e congelato (3/3 hash identici).
- Meccanismo di riprova selettiva (`retry_failed_only`), budget del retry e diagnostica persistente operativi.

L'ispezione specialistica e le simulazioni di edge-case forensi hanno tuttavia evidenziato quattro anomalie mirate nell'analisi e nel ripristino della Topic Discovery:
1. **Contabilità errata della copertura delle evidenze dopo split:** in caso di suddivisione dinamica (da context overflow o split controllato), il servizio accreditava come "analizzate" tutte le evidenze del chunk padre anche se solo uno dei due sotto-blocchi era riuscito, producendo una dichiarazione mendace di copertura completa (es. 4/4 anziché 3/4).
2. **Mancata persistenza e mancato recupero dei sotto-blocchi riusciti:** i sotto-blocchi validati non venivano salvati atomicamente in SQLite preservando la relazione parent/child, provocando la perdita del lavoro già computato in caso di interruzione e costringendo a ripetere l'inferenza LLM al riavvio.
3. **Ripristino storico incoerente tra modelli operativi differenti:** `load_persisted_discovery_result()` non isolava rigorosamente i modelli; se un documento era stato analizzato con Qwen2.5-7B e l'utente richiedeva il ripristino impostando Llama 3.1, il sistema poteva restituire lo storico Qwen trattandolo impropriamente come compatibile per la riprova selettiva.
4. **Presunzione impropria del finish reason:** in `ai/topics.py` e `analysis/service.py` erano presenti assegnazioni fallback tipo `finish_reason or "stop"`, registrando artificialmente `"stop"` nella diagnostica anche quando il backend ometteva il campo o restituiva `None`.

Il presente intervento ha corretto in modo chirurgico e definitivo queste quattro anomalie senza stravolgere l'architettura del progetto, mantenendo intatti tutti i vincoli scientifici ed etici.

---

## 2. DIAGNOSI ED ARCHITETTURA DELLE CORREZIONI IMPLEMENTATE

### 2.1. Correzione della Copertura Forense dopo Split (Multi-chunk e Single-chunk)

#### Difetto Riscontrato:
Nella versione precedente, al termine di `_process_subchunks_discovery()`, sia nel percorso multi-chunk sia nel percorso single-chunk con suddivisione dinamica per `CONTEXT_OVERFLOW`, il codice eseguiva:
```python
# Comportamento errato precedente:
analyzed_evidence_ids.update(chunk_eids)  # Accreditava tutte le evidenze del chunk padre!
```
In uno scenario controllato con 4 messaggi divisi in 2 chunk, se il primo chunk andava in split in Sub0 (msg 0, riuscito) e Sub1 (msg 1, fallito), e il secondo chunk originale riusciva (msg 2, 3), il sistema riportava `analyzed_evidence_count = 4` anziché `3`, dichiarando analizzato il messaggio 1 appartenente al sotto-blocco fallito.

#### Correzione Applicata:
1. In `analysis/models.py`, `DiscoveryChunkOutcome` è stato esteso con il campo:
   ```python
   evidence_ids: tuple[str, ...] = ()
   ```
   e il relativo metodo `to_dict()` include `"evidence_ids": list(self.evidence_ids)`.
2. In `analysis/service.py`, `_process_subchunks_discovery()` associa rigorosamente a ogni `DiscoveryChunkOutcome` le sole evidenze appartenenti al sotto-blocco specifico (`sub_eids`).
3. Sia nel blocco single-chunk (linee 1804-1808) sia nel loop multi-chunk (linee 1984-1988 e linee 2116-2120), l'aggiornamento di `analyzed_evidence_ids` tiene conto esclusivamente dei sotto-blocchi con stato `"SUCCESS"` o `"RECOVERED_FROM_CHECKPOINT"`:
   ```python
   for so in sub_outs:
       if so.status in ("SUCCESS", "RECOVERED_FROM_CHECKPOINT"):
           analyzed_evidence_ids.update(so.evidence_ids)
       if so.diagnostic:
           all_chunk_diagnostics.append(so.diagnostic)
   ```
4. Risultato: nello scenario di test di 4 messaggi, con Sub0 SUCCESS, Sub1 FAILED e Chunk 1 SUCCESS, la copertura dichiarata è **esattamente 3/4 (75%)**. Nessuna evidenza appartenente a sotto-blocchi falliti viene accreditata come esaminata.

---

### 2.2. Salvataggio e Recupero Incrementale dei Sotto-Blocchi su SQLite

#### Difetto Riscontrato:
I sotto-blocchi computati durante `_process_subchunks_discovery()` non venivano salvati nel database transazionale SQLite `.checkpoints/checkpoints.db`. Se un'analisi multi-chunk subiva un'interruzione (o un fallimento del secondo sotto-blocco), il checkpoint conservava solo i chunk non suddivisi; al riavvio, il sotto-blocco già validato doveva essere re-inferito da zero verso il backend LLM.

#### Correzione Applicata:
1. **Schema SQLite Esteso:** In `analysis/checkpoint.py`, è stata aggiunta la tabella `checkpoint_subchunks`:
   ```sql
   CREATE TABLE IF NOT EXISTS checkpoint_subchunks (
       document_fingerprint TEXT NOT NULL,
       config_fingerprint TEXT NOT NULL,
       parent_chunk_index INTEGER NOT NULL,
       subchunk_index INTEGER NOT NULL,
       subchunk_id TEXT NOT NULL,
       total_subchunks INTEGER NOT NULL,
       serialized_result TEXT NOT NULL,
       evidence_ids TEXT NOT NULL,
       created_at TEXT NOT NULL,
       PRIMARY KEY (document_fingerprint, config_fingerprint, parent_chunk_index, subchunk_index),
       FOREIGN KEY (document_fingerprint, config_fingerprint)
           REFERENCES checkpoints (document_fingerprint, config_fingerprint)
           ON DELETE CASCADE
   );
   CREATE INDEX IF NOT EXISTS idx_subchunks_parent
   ON checkpoint_subchunks (document_fingerprint, config_fingerprint, parent_chunk_index);
   ```
2. **Metodi di Accesso Dedicati:**
   - `save_subchunk(...)`: salva atomicamente il singolo sotto-blocco validato in $O(1)$, aggiornando contestualmente la testata del checkpoint in `checkpoints`.
   - `get_subchunks(...)`: recupera la lista di sotto-blocchi validati per documento e configurazione, con filtro opzionale per `parent_chunk_index`.
   - `get_subchunk(...)`: recupera il singolo sotto-blocco identificato da `(parent_chunk_index, subchunk_index)`.
3. **Immutabilità della Struttura Dati:** `AnalysisCheckpoint` include ora la tupla immutabile `subchunks: tuple[dict[str, Any], ...] = ()`.
4. **Rivalidazione Forense Rigorosa (Fail-Closed):** In `validate_checkpoint_evidence_references()`, ogni sotto-blocco persistito viene sottoposto a verifica:
   - `parent_chunk_index` compreso in $[0, total\_planned\_chunks)$.
   - Evidenze dichiarate strettamente appartenenti al chunk padre di provenienza.
   - Tutti gli evidence ID citati nei topic generati devono appartenere all'insieme delle evidenze consentite per quel sotto-blocco.
   - In caso di discrepanza o manomissione, il checkpoint viene respinto integralmente (`None`).
5. **Zero Duplicate Inference:** In `_process_subchunks_discovery()`, prima di richiedere inferenza al modello per un sotto-blocco, il servizio consulta `chk_store.get_subchunk()`. Se presente e validato, lo registra come `RECOVERED_FROM_CHECKPOINT` con durata 0.0s e 0 tentativi, senza inviare alcuna chiamata HTTP a LM Studio.
6. **Consolidamento del Blocco Padre:** Il blocco padre viene salvato nella tabella primaria `checkpoint_chunks` **solo ed esclusivamente se tutti i sotto-blocchi hanno avuto successo** (`len(results) == total_subs`). Se uno fallisce, il blocco padre non viene marcato completo.

---

### 2.3. Ripristino Storico Coerente con Modello e Configurazione

#### Difetto Riscontrato:
In precedenza, `LocalTopicAnalysisService.load_persisted_discovery_result()` accettava `model_name`, ma in caso di mancata corrispondenza esatta ripiegava sull'ultimo checkpoint disponibile per il documento, senza distinguere se fosse stato generato con Qwen o Llama. La UI poteva quindi presentare un risultato Qwen come valido per la prosecuzione operativa sotto un modello Llama, causando tentativi di riprova selettiva con modelli incoerenti.

#### Correzione Applicata:
1. **Distinzione Chiara tra Storico e Modello Operativo:**
   - `load_persisted_discovery_result()` introduce il parametro `allow_incompatible_model: bool = False`.
   - Nel percorso operativo o di riprova selettiva (`allow_incompatible_model=False`), se il modello richiesto non coincide con quello del checkpoint, il metodo restituisce rigorosamente `None`.
   - Nel percorso UI di consultazione (`allow_incompatible_model=True`), il metodo restituisce lo storico disponibile ma contrassegna esplicitamente nei metadati:
     ```python
     "is_model_compatible": False,
     "is_compatible_for_retry": False,
     "historical_model": saved_model,
     "requested_model": resolved_model,
     ```
2. **Protezione UI in `ui/presentation.py`:**
   - Se `is_model_compatible` è `False`, viene visualizzato un banner informativo:
     > ⚠️ **Attenzione Storico Forense:** L'analisi ripristinata è stata elaborata con il modello storico `{saved_model}`, diverso dal modello attualmente selezionato `{active_model}`.
   - Se `is_compatible_for_retry` è `False`, il pulsante «🔄 Riprova solo i blocchi falliti» viene disabilitato ed è mostrato l'avviso:
     > ℹ️ *Riprova selettiva non disponibile: il modello dell'analisi salvata non corrisponde al modello attualmente selezionato.*
3. **Fail-Closed su Esecuzione Diretta:** Se un chiamante tenta di invocare `run_topic_discovery(..., retry_failed_only=True)` specificando un modello incompatibile con il checkpoint esistente, il servizio solleva immediatamente `OperationalAnalysisFailedError` senza effettuare alcuna chiamata LLM.

---

### 2.4. Preservazione Fedele del Finish Reason (Nessun Stop Presunto)

#### Difetto Riscontrato:
In `ai/topics.py` (linea 567) e in vari punti di `analysis/service.py`, il finish reason veniva assegnato con espressioni equivalenti a:
```python
"finish_reason": finish_reason or "stop"
```
Questo causava una falsificazione diagnostica: quando il backend LM Studio o una risposta valida non conteneva il campo `finish_reason`, la diagnostica registrava arbitrariamente `"stop"`, impedendo di distinguere una terminazione naturale verificata da un dato non pervenuto o omesso dal backend.

#### Correzione Applicata:
1. In `ai/topics.py` linea 567, il dizionario di metadati preserva fedelmente il valore osservato:
   ```python
   "finish_reason": finish_reason,  # Nessun fallback or "stop"
   ```
2. In `analysis/service.py`:
   - Nei record diagnostici di successo, fallimento e recupero da checkpoint, `finish_reason` riceve direttamente `disc_res.metadata.get("finish_reason")` o `valid_cached_res.metadata.get("finish_reason")`.
   - Se il backend non restituisce il dato, il valore registrato su `ChunkDiagnosticRecord` e persistito su SQLite è rigorosamente `None` (`NULL` in colonna SQLite).
   - In caso di errore per superamento token (`OUTPUT_TRUNCATED`), il `finish_reason` è valorizzato con `"length"`.
   - Nessun chunk recuperato da checkpoint inventa informazioni retroattive.

---

## 3. MATRICE COMPLETA DEI TEST E RISULTATI DI VALIDAZIONE

Per verificare rigorosamente la risoluzione dei quattro difetti e garantire la totale assenza di regressioni, è stata eseguita una suite completa di test specialistici e generali.

### 3.1. Test Specialistici Mirati (`test_subchunk_split_recovery_and_coverage.py`)

I nuovi 6 test specialistici riproducono esattamente le anomalie descritte e ne certificano la risoluzione:

| Test | Obiettivo Forense | Esito |
|---|---|:---:|
| `test_evidence_coverage_after_subchunk_split_multichunk` | Documento 4 messaggi, 2 chunk. Chunk 0 overflow split in Sub0 (OK) e Sub1 (KO). Chunk 1 OK. Verifica che la copertura dichiarata sia **3/4 (75%)** e NON 4/4. | **PASSED** |
| `test_evidence_coverage_after_subchunk_split_singlechunk` | Singolo chunk con split dinamico per overflow. Sub0 OK, Sub1 KO. Copertura dichiarata esattamente **1/2 (50%)**, non 2/2. | **PASSED** |
| `test_subchunk_persistence_and_recovery_without_duplicate_inference` | Persistenza Sub0 in `checkpoint_subchunks`. Ripresa con retry_failed_only: Sub0 e Chunk 1 recuperati da SQLite (0 chiamate LLM), solo Sub1 eseguito. Transizione a COMPLETE (4/4). | **PASSED** |
| `test_historical_restore_distinguishes_models_and_blocks_incompatible_retry` | Checkpoint salvato con Qwen2.5. Richiesta ripristino con Llama 3.1: default restituisce `None`; con allow_incompatible restituisce `is_compatible_for_retry=False`. Riprova selettiva respinta con `OperationalAnalysisFailedError`. | **PASSED** |
| `test_finish_reason_preservation_when_backend_omits_or_null` | Backend omette finish_reason: verificato che rimanga `None` in `TopicDiscoveryResult`, `ChunkDiagnosticRecord`, SQLite e nel recupero da checkpoint (nessun `"stop"` inventato). | **PASSED** |
| `test_subchunk_revalidation_tampered_evidence_rejected` | Manomissione in SQLite di un evidence ID di un sotto-blocco salvato: la rivalidazione fallisce closed (`load_and_validate_checkpoint` restituisce `None`). | **PASSED** |

### 3.2. Test Specialistici di Regressione e Integrazione

| Suite di Test | Modulo | Test Eseguiti | Esito |
|---|---|:---:|:---:|
| Scenario Reale Ubuntu 15/26 verso 26/26 | `test_ubuntu_live_chunk_failure_and_recovery.py` | 12 | **12 / 12 PASSED** |
| Checkpoint SQLite, Concorrenza e Riconciliazione JSON | `test_checkpoint_integrity_scalability.py` | 33 | **33 / 33 PASSED** |
| Streamlit UI, Riconciliazione Sessione e AppTest | `test_ui_manual_topic_detection.py` + `test_ui_smoke.py` | 26 | **26 / 26 PASSED** |
| Suite Pytest Completa del Progetto | `pytest -v` | 1790 | **1784 PASSED**, 1 skipped, 5 deselected |

---

## 4. VINCOLI SCIENTIFICI E INTEGRITÀ DEL BENCHMARK

Tutti i vincoli imposti dal protocollo scientifico della tesi sono stati rigorosamente rispettati:
- **File Scientifici Congelati Invariati:**
  - `ai/experiment.py`: SHA256 `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845`
  - `ai/benchmark.py`: SHA256 `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36`
  - `run_benchmark.sh`: SHA256 `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7`
- **Metriche e Parametri di Modello Preservati:** Nessuna alterazione a temperature (0.0), seed (42), context length (8192), timeout (240s) o max output token (512 token).
- **Benchmark Ufficiali:** I nove benchmark scientifici non sono stati eseguiti.
- **Nessun Commit o Push Autonomo:** Tutte le modifiche risiedono nel workspace locale e sono tracciate nel patch `git_diff_split_recovery_coverage.patch`.

---

## 5. PACCHETTO DI RILASCIO E VERIFICA WHEEL

Il pacchetto è stato generato mediante lo script `scripts/package_split_recovery_coverage_integrity_zip.py`:
- **Nome File:** `chatAnalysis_split_recovery_coverage_integrity_20261016.zip`
- **Integrità Wheel:** `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl` compilata con successo (89 sorgenti corrispondenti al 100% ai sorgenti del repository).
- **Manifest SHA256:** `MANIFEST_SHA256.txt` rigenerato con tutti i file inclusi.
- **Privacy e Conformità:** Nessun dato personale, conversazione reale, modello LLM o checkpoint investigativo è stato incluso nell'archivio.

---

## 6. CONCLUSIONE E RACCOMANDAZIONI OPERATIVE

L'intervento ha garantito che:
1. **Ogni evidenza dichiarata analizzata è stata realmente elaborata** da un blocco o sotto-blocco validato, eliminando qualunque sovrastima forense in caso di fallimenti parziali.
2. **Ogni sotto-blocco validato viene salvato e recuperato senza ripetere l'inferenza LLM**, preservando la computazione già avvenuta e riducendo a zero le chiamate ridondanti verso LM Studio.
3. **Lo storico forense è isolato per modello**, evitando commistioni tra Qwen e Llama e proteggendo la riprova selettiva.
4. **La diagnostica è fedele alla realtà osservata**, registrando `None` o l'effettivo `finish_reason` senza inventare `"stop"`.

Per la sperimentazione reale della chat da 1.049 messaggi su Ubuntu con LM Studio, il sistema è ora pronto per essere rieseguito con il comando standard o tramite l'interfaccia Streamlit, con la certezza che anche in presenza di context overflow o interruzioni di rete la contabilità delle evidenze e il recupero dei sotto-blocchi opereranno in conformità ai più stringenti standard forensi.
