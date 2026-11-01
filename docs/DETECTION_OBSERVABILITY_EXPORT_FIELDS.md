# Topic Detection — campi diagnostici additivi dell'export

Questa nota documenta i campi **aggiunti** all'export JSON di Topic Detection dalla PR di sola osservabilità.
Lo `schema_version` resta `1.1.0`: tutte le modifiche sono **additive** (nessun campo esistente è stato rimosso,
rinominato o ha cambiato tipo/semantica). Nessun campo contiene testo delle conversazioni, prompt, output del
modello, dettagli di errore, percorsi o identificativi: solo **interi, booleani ed enum chiusi**.

Convenzione: `null` significa **non disponibile** ed è distinto da `0` (zero reale).

## 1. `execution.detection_diagnostics` (nuovo, oggetto)

Presente solo se la Detection lo ha prodotto. Contatori a livello di run.

| Campo | Significato |
|---|---|
| `parent_chunks_total / _completed / _failed` | parent (blocchi principali) pianificati / certificati / non certificati |
| `parents_recovered_from_checkpoint` | parent con origine `CHECKPOINT_VALIDATED` (nessuna nuova chiamata) |
| `parents_computed_this_run` | parent con origine `COMPUTED_THIS_RUN` (include quelli falliti in questa esecuzione) |
| `parents_mixed_checkpoint_and_computed` | parent con microchunk in parte recuperati e in parte calcolati |
| `microchunks_total / _completed / _failed` | unità di primo livello (bisezioni e retry non creano unità) |
| `microchunks_recovered_from_checkpoint` / `microchunks_computed_this_run` | completati da checkpoint / da nuova inferenza |
| `leaf_attempts_total` | tentativi `detect_topic` a livello **foglia** (esiti finali dei nodi terminali) |
| `detect_topic_calls` | chiamate a `detect_topic` (storico) |
| `http_requests_sent / _successful / _failed / _in_flight` | richieste HTTP **effettivamente inviate** su TUTTI i nodi (anche intermedi), dal `request_listener` |
| `http_requests_basis` | `REQUEST_LISTENER` oppure `NOT_AVAILABLE` (nodi eseguiti ma il client non ha riportato richieste → i conteggi HTTP sono `null`, non 0) |
| `http_requests_accounting_consistent` | invariante `sent == successful + failed + in_flight` |
| `node_failures`, `degenerate_failures` | nodi falliti (foglie e nodi poi bisecati) / di cui `DEGENERATE_OUTPUT` |
| `degeneration_trigger_counts` | `{CHAR_RUN, TAB_RUN, PATTERN_LOOP}` → conteggio dei nodi per tipo di innesco |
| `retries`, `bisections`, `timeouts`, `callback_errors` | contatori di run |
| `checkpoint_writes_ok`, `checkpoint_write_failures` | scritture checkpoint riuscite / fallite |
| `checkpoint_persistence` | `OK` / `WRITE_FAILURES` / `NOT_ATTEMPTED` |
| `resume_guaranteed` | `false` se almeno una scrittura checkpoint è fallita |
| `duration_seconds` | durata del run |

**Tentativi ≠ richieste HTTP.** Con la bisezione adattiva un microchunk fallito genera fino a 15 nodi × 2 tentativi
= 30 richieste HTTP ma solo 16 tentativi a livello foglia (8 foglie × 2). I due contatori non vanno confusi né sommati.

## 2. `chunks[].diagnostic_metrics` (nuovo, opzionale)

Presente sul record **parent** fallito. Solo numeri/enum.

- `failed_leaf_count`
- `failure_category_counts` — categoria (allowlist) → conteggio
- `degeneration_trigger_counts` — `CHAR_RUN | TAB_RUN | PATTERN_LOOP` → conteggio
- `first_failure` — categoria, fase, tipo d'errore e metriche del **primo** fallimento reale
  (un `TIME_BUDGET_EXHAUSTED` non maschera un fallimento reale precedente o successivo).
  Metriche: `bundle_count`, `trigger_kind`, `pattern_length`, `repetition_count`, `run_length`, `response_length`,
  `finish_reason`, `completion_tokens_cumulative`. Ogni valore non disponibile è `null`.

Il pattern ripetuto **non** viene mai memorizzato: si conservano solo lunghezza e conteggio.
`finish_reason` e `completion_tokens_cumulative` dipendono da ciò che il backend riporta.

Al parent fallito vengono inoltre propagati `diagnostic_category` ed `error_type` del primo fallimento reale.

## 3. `chunks[].resume_provenance` (nuovo, opzionale)

| Campo | Significato |
|---|---|
| `result_origin` | `CHECKPOINT_VALIDATED`, `COMPUTED_THIS_RUN`, `MIXED_CHECKPOINT_AND_COMPUTED` |
| `via_microchunks` | il parent è stato eseguito a microchunk (`null` se non noto, es. parent recuperato per intero) |
| `microchunks_recovered / _computed / _failed` | contatori per parent (`null` se non disponibili) |
| `current_run_http_requests` | richieste HTTP spese **in questa esecuzione** (0 reale per un parent recuperato) |
| `original_attempts` | spesa storica del run originale: **`null`** se non disponibile (mai convertita in 0) |

I metadati storici (`attempts` legacy) restano separati dalla spesa dell'esecuzione corrente.
L'export **non** dichiara `LIVE_VERIFIED`: la provenienza LIVE richiede un'attestazione indipendente.

## 4. Allowlist estese (chiuse)

- Categorie: `DEGENERATE_OUTPUT`, `GBNF_GRAMMAR_ERROR`, `RETRY_EXHAUSTED`, `TIME_BUDGET_EXHAUSTED`
  (tutti i valori dell'enum `ChunkDiagnosticCategory` sono ora esportabili; un test lo verifica).
- Fasi: `json_parsing`, `schema_validation`, `budget`, `checkpoint_save`.
- Tipi d'errore: `TimeBudgetExhausted`, `CheckpointSaveError`.
- Un valore fuori allowlist non viene mai riportato in chiaro (ricondotto a un valore generico o `null`).

## 5. Metadati del risultato

- `recovered_chunk_count` ora riflette i parent realmente recuperati da checkpoint (prima restava 0 anche dopo un resume).
- `subchunks_used` è `true` anche quando almeno un parent è stato eseguito a microchunk.

## 6. Privacy dei messaggi

Messaggi di progresso, `error_message` dei diagnostici e testo mostrato all'utente per un errore di output strutturato
usano `safe_exception_text()` (nome della classe + descrizione statica per categoria) invece di `str(exc)`.
Il messaggio dell'eccezione **non è modificato** (i prompt di retry lo usano ancora: cambiarlo è una modifica
comportamentale fuori ambito).

## 7. Limiti noti

- Il percorso a **blocco singolo** (documento ≤ 8 messaggi sostanziali, senza pre-split) riceve solo le protezioni di
  privacy: non produce `resume_provenance` né `diagnostic_metrics` a livello parent.
- Per il blocco singolo l'eccezione sollevata al chiamante è invariata (stesso tipo e messaggio storico).
- `completion_tokens_cumulative` e `finish_reason` sono `null` se il backend non li riporta.
- Per i parent recuperati la spesa storica (`original_attempts`) non è esposta dal checkpoint: `null`.
- Il controllo di degenerazione continua a operare sul testo grezzo prima del parsing JSON (comportamento storico):
  i falsi positivi restano possibili e ora sono **distinguibili** tramite `trigger_kind`.
