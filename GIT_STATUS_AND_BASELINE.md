# CHATANALYSIS — STATO GIT E BASELINE DI RIFERIMENTO (RELEASE 2026-10-16)

## 1. Commit Baseline e Riferimento Git

- **Branch attivo**: `main`
- **Release di Riferimento**: `chatAnalysis_split_recovery_coverage_integrity_20261016.zip`
- **Baseline di partenza**: `chatAnalysis_live_recovery_integration_validation_20261016.zip`
- **Politica di rilascio**: Nessun commit né push eseguito autonomamente (modifiche fornite come patch unificata UTF-8 `git_diff_split_recovery_coverage.patch`).

---

## 2. File Modificati Tracciati e Sintesi delle Correzioni

I seguenti file contengono le correzioni mirate dell'audit di integrità:

1. `analysis/models.py`:
   - Aggiunto il campo `evidence_ids: tuple[str, ...] = ()` alla dataclass `DiscoveryChunkOutcome`.
   - Aggiornato `to_dict()` con `"evidence_ids": list(self.evidence_ids)` per garantire la serializzazione e la tracciabilità diagnostica delle evidenze associate a ciascun esito di blocco/sotto-blocco.

2. `analysis/checkpoint.py`:
   - Aggiunta la tabella SQLite `checkpoint_subchunks` con vincolo di foreign key cascading su `checkpoints` e indice su `(document_fingerprint, config_fingerprint, parent_chunk_index)`.
   - Implementati i metodi di persistenza e recupero dei sotto-blocchi: `save_subchunk()`, `get_subchunks()`, `get_subchunk()`.
   - Aggiunto il campo immutabile `subchunks: tuple[dict[str, Any], ...] = ()` su `AnalysisCheckpoint`.
   - Estesa la funzione di rivalidazione forense `validate_checkpoint_evidence_references()` per validare rigorosamente la provenienza, i bound degli indici e l'appartenenza degli evidence ID dei sotto-blocchi (fail-closed su discrepanze o manomissioni).
   - Esteso `get_latest_checkpoint_metadata_for_document()` con filtro opzionale `model_id`.
   - Aggiornati i metodi `clear_checkpoint` e `clear_for_document` per pulizia atomica coordinata.

3. `analysis/service.py`:
   - **Copertura Forense dopo Split**: corretto il calcolo in `run_topic_discovery()`, sia nel percorso multi-chunk sia in quello single-chunk con split dinamico per `CONTEXT_OVERFLOW`. Solo i sotto-blocchi con status `"SUCCESS"` o `"RECOVERED_FROM_CHECKPOINT"` accreditano i rispettivi `so.evidence_ids` ad `analyzed_evidence_ids`.
   - **Persistenza e Recupero Sotto-Blocchi**: in `_process_subchunks_discovery()`, salvataggio incrementale di ogni sotto-blocco riuscito in `checkpoint_subchunks`. Al riavvio/ripresa, recupero da SQLite come `RECOVERED_FROM_CHECKPOINT` con 0 chiamate LLM. Consolidamento del chunk padre in `checkpoint_chunks` solo ed esclusivamente se tutti i sotto-blocchi sono completati.
   - **Isolamento Storico per Modello**: `load_persisted_discovery_result()` introduce `allow_incompatible_model`. Se `False` (default operativo), restituisce `None` qualora il modello richiesto non coincida con quello salvato. Se `True` (richiesto da UI per consultazione), restituisce lo storico contrassegnato con `is_model_compatible=False` e `is_compatible_for_retry=False`. Riprova selettiva con modello incompatibile bloccata con `OperationalAnalysisFailedError`.
   - **Preservazione Finish Reason**: eliminato qualsiasi fallback `"stop"`. Se il backend non restituisce `finish_reason`, viene preservato rigorosamente `None` (`NULL` su SQLite) e non inventato retroattivamente.

4. `ai/topics.py`:
   - Linea 567: sostituito `"finish_reason": finish_reason or "stop"` con `"finish_reason": finish_reason`, eliminando la presunzione arbitraria di completamento naturale.

5. `ui/presentation.py`:
   - Aggiornata la visualizzazione diagnostica: visualizzazione del modello storico associato all'analisi ripristinata con avviso di incompatibilità qualora differisca dal modello operativo selezionato.
   - Disabilitazione protetta del pulsante «🔄 Riprova solo i blocchi falliti» quando `is_compatible_for_retry` è `False`.

6. `tests/unit/test_subchunk_split_recovery_and_coverage.py`:
   - Suite specialistica di 6 nuovi test mirati:
     1. Copertura esatta 3/4 (75%) su documento 4 messaggi dopo split parziale.
     2. Copertura esatta 1/2 (50%) su single-chunk con split dinamico.
     3. Persistenza e ripresa sotto-blocchi con zero duplicate inference verso l'LLM.
     4. Isolamento storico coerente per modello e blocco riprova con modello incompatibile.
     5. Preservazione di finish_reason=None sia in memoria sia su SQLite e dopo recupero.
     6. Rigetto fail-closed di sotto-blocchi con evidenze manomesse.

---

## 3. Risultati della Suite di Test

- **Pytest completo**: **1.784 superati**, 1 saltato, 5 esclusi (benchmark congelati), **0 fallimenti** (in `pytest_and_apptest.log`).
- **Suite Specialistica Sotto-Blocchi e Copertura (`test_subchunk_split_recovery_and_coverage.py`)**: **6 su 6 superati**.
- **Suite Live Ubuntu Recovery (`test_ubuntu_live_chunk_failure_and_recovery.py`)**: **12 su 12 superati**.
- **Suite Checkpoint SQLite e Riconciliazione (`test_checkpoint_integrity_scalability.py`)**: **33 su 33 superati**.
- **Suite Streamlit UI e AppTest (`test_ui_manual_topic_detection.py` + `test_ui_smoke.py`)**: **26 su 26 superati**.

---

## 4. Benchmark Scientifico Congelato (SHA256 Invariati)

| File | Hash SHA256 Verificato | Stato |
|---|---|---|
| `ai/experiment.py` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | CONGELATO / INVARIATO |
| `ai/benchmark.py` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | CONGELATO / INVARIATO |
| `run_benchmark.sh` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | CONGELATO / INVARIATO |
