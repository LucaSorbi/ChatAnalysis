# CHATANALYSIS — REPORT DI HARDENING FINALE DELLA RICONCILIAZIONE SQLITE/JSON
**Data rilascio**: 15 Ottobre 2026  
**Release target**: `chatAnalysis_sqlite_legacy_reconciliation_hardening_audit_20261015.zip`  
**Baseline verificata**: `chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip` (Commit: `6c1dc468c4d2fe94e9fcf0963aa36fc48bc9568b`)  
**Modulo oggetto di hardening**: `analysis/checkpoint.py`  
**Suite di regressione specialistica**: `tests/unit/test_checkpoint_integrity_scalability.py` (29/29 superati)  
**Suite complessiva pytest**: 1762 superati, 0 falliti, 1 skipped, 5 deselected (100% success)

---

## 1. EXECUTIVE SUMMARY & VERIFICA DELLA BASELINE

In seguito all'audit indipendente condotto sulla release `chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip` (311/311 file SHA256 integri, 89/89 sorgenti della wheel corrispondenti, 1756 test superati, 23/23 test specialistici ripetuti e benchmark scientifico invariato), sono stati riprodotti tre ulteriori difetti specifici nel sottosistema di riconciliazione e cancellazione tra SQLite e file legacy JSON (`analysis/checkpoint.py`).

L'intervento è stato condotto secondo una rigorosa politica di non-regressione e di isolamento:
- **Nessuna riscrittura del progetto**: le modifiche sono circoscritte alle funzioni difettose di `analysis/checkpoint.py` (`_migrate_legacy_json_if_needed`, `clear_all_for_document_id`, `clear_for_document`);
- **Preservazione dei requisiti scientifici**: i file congelati del benchmark (`ai/experiment.py`, `ai/benchmark.py`, `run_benchmark.sh`) e i parametri quantitativi (Context 8192, Q4_K_M, Temperatura 0, Timeout 240s, Max output 512) sono rimasti rigorosamente intatti con hash SHA256 identici;
- **Nessun impatto architetturale**: preservate intatte le pipeline di Topic Discovery/Detection, semantic search label-only, chunking operativo, profili Streamlit, progress bar HTTP e gestione incrementale;
- **Zero perdite di dati forensi**: garantita l'assoluta preservazione di decisioni, rationale, evidence ID e metadati di chunk.

---

## 2. ANOMALIE RIPRODOTTE, CAUSE RADICE E CORREZIONI APPLICATE

### CORREZIONE 1 — Non eliminare un legacy valido se SQLite è corrotto

#### Riproduzione del difetto
1. Inserimento di una testata valida in SQLite per la coppia `(doc_fp, cfg_fp)`;
2. Inserimento forzato nel chunk 0 di un `serialized_result` corrotto non parsabile come JSON (`CORRUPTED_NON_JSON_DATA{{{`);
3. Presenza nella directory `.checkpoints` di un file legacy JSON valido contenente il risultato del chunk 0 per la stessa coppia;
4. Inizializzazione o riavvio di `AnalysisCheckpointStore`.

#### Comportamento anomalo osservato
- Durante l'ispezione preliminare di SQLite, l'eccezione di parsing del chunk corrotto veniva intercettata con `except Exception: pass`, lasciando l'indice 0 fuori dal dizionario dei blocchi noti;
- Il codice di migrazione considerava il blocco 0 mancante e tentava un inserimento con `INSERT INTO checkpoint_chunks ... ON CONFLICT(document_fingerprint, config_fingerprint, chunk_index) DO NOTHING`;
- La clausola `ON CONFLICT DO NOTHING` non modificava la riga esistente (mantenendo il payload corrotto);
- La verifica successiva eseguiva un generico `SELECT COUNT(*) FROM checkpoint_chunks`: conteggiando la riga (seppur illeggibile), considerava la migrazione completata con successo;
- Di conseguenza, il file legacy JSON **valido veniva eliminato da disco** (`p.unlink()`), lasciando nel database unicamente il record corrotto;
- Una successiva chiamata a `load_checkpoint()` falliva restituendo `None`, causando perdita irrecuperabile dell'evidenza validata.

#### Causa radice individuata
Utilizzo del conteggio aggregato `COUNT(*)` come indicatore di successo della migrazione senza verificare la parsabilità dei payload, associato all'omissione di un conflitto diagnostico esplicito in presenza di eccezioni di deserializzazione da SQLite.

#### Correzione implementata in `analysis/checkpoint.py`
1. **Rilevamento e categorizzazione esplicita dei chunk SQLite**: l'ispezione controlla la parsabilità JSON (`json.loads(row[1])`) e la conformità a dizionario (`isinstance(p, dict)`). In caso di errore o payload non conforme, l'indice viene inserito nel set `sqlite_corrupted_indices`;
2. **Conflitto diagnostico esplicito**: se `sqlite_corrupted_indices` non è vuoto, viene sollevato lo stato `has_conflict = True` e registrato un avviso diagnostico dettagliato;
3. **Divieto di sovrascrittura o fusione cieca**: nessun dato legacy viene inserito a forza su indici corrotti consolidati senza intervento deliberato;
4. **Verifica analitica rigorosa pre-eliminazione**: la rimozione del file legacy (`jf.unlink()`) è condizionata a:
   - Assenza totale di conflitti (`not has_conflict`);
   - Verifica di parsabilità al 100% di tutti i chunk SQLite estratti con `SELECT chunk_index, serialized_result`;
   - Controllo biunivoco che tutti gli indici attesi del file legacy siano presenti ed integri nel database (`expected_set.issubset(actual_set)`).
5. **Preservazione assoluta**: se la verifica analitica fallisce o se esiste un chunk corrotto, il file legacy valido **rimane intatto su disco** a disposizione dell'analista.

---

### CORREZIONE 2 — Preservare i file legacy non decodificabili in `clear_all_for_document_id()`

#### Riproduzione del difetto
1. Esiste un checkpoint SQLite valido per `document_id='target_doc'`;
2. Esiste un file legacy JSON danneggiato/illeggibile nella directory (es. `chk_{doc_fp[:16]}_corrupt.json`), contenente frammenti binari o JSON troncato;
3. Esiste un file legacy valido appartenente a un altro documento (`document_id='other_doc'`);
4. Invocazione di `clear_all_for_document_id('target_doc')`.

#### Comportamento anomalo osservato
- `clear_all_for_document_id` eseguiva una seconda scansione sui file nominati con il prefisso `chk_{fp[:16]}_*.json`;
- Se il file non era parsabile, il blocco `except Exception: pass` inghiottiva l'errore consentendo all'esecuzione di procedere direttamente alla riga successiva: `p.unlink()`;
- Il file danneggiato veniva cancellato sulla sola base di un'assonanza o prefisso del nome, violando la regola di conservazione forense dei file corrotti non chiaramente attribuibili.

#### Causa radice individuata
Presenza di una scansione secondaria ridondante per prefisso nome che conteneva un fallthrough non protetto (`except Exception: pass; p.unlink()`), in contrasto con la scansione principale verificata.

#### Correzione implementata in `analysis/checkpoint.py`
1. **Unica procedura di attribuzione verificata**: eliminata integralmente la seconda scansione per nome con fallthrough. Tutti i file candidati della cartella checkpoint vengono processati da un'unica procedura rigorosa;
2. **Preservazione dei file non decodificabili**: se un file solleva eccezioni di lettura o decodifica (`json.JSONDecodeError`, `UnicodeDecodeError`, `OSError`) o non presenta una radice dizionario conforme, viene inserito nella lista `unattributable_files` e **non viene mai inserito tra i file da eliminare**;
3. **Preservazione dei file di altri documenti**: se `data.get("document_id") != doc_id`, il file viene preservato anche se il fingerprint dovesse collidere parzialmente;
4. **Segnalazione di cancellazioni parziali**: la presenza di file illeggibili o non attribuibili viene esplicitamente notificata tramite `logger.warning`, indicando il conteggio e i nomi dei file preservati per consentire l'ispezione dell'analista;
5. **Allineamento di `clear_for_document()`**: applicata analoga protezione in `clear_for_document(doc_or_fp)`: i file con payload non parsabile o con fingerprint divergente vengono rigorosamente preservati.

---

### CORREZIONE 3 — Validare i metadati prima di fondere chunk

#### Riproduzione del difetto
1. SQLite contiene una testata consolidata per `document_id='doc'`, `analysis_type='detection'`, `model_id='test-model'`, `prompt_version='v1'`, con `total_planned_chunks=1` e chunk 0 salvato;
2. Un file legacy dichiara casualmente gli stessi fingerprint (`doc_fp`, `cfg_fp`), ma dichiara:
   - `document_id='OTHER_DOCUMENT'`
   - `total_planned_chunks=2`
   - chunk 1 nei `serialized_results`.
3. Inizializzazione o ricaricamento di `AnalysisCheckpointStore`.

#### Comportamento anomalo osservato
- La testata SQLite conservava `document_id='doc'`, ma il valore `total_planned_chunks` veniva incrementato arbitrariamente a 2 tramite `MAX(total_planned_chunks, excluded.total_planned_chunks)`;
- Il chunk 1 (generato per un altro documento `OTHER_DOCUMENT`) veniva inserito nella tabella `checkpoint_chunks`;
- Il file legacy discordante veniva cancellato, inquinando il checkpoint di `doc` con chunk alieni e alterando la copertura pianificata.

#### Causa radice individuata
Omissione della validazione incrociata preliminare tra i metadati consolidati in SQLite e i metadati dichiarati nel file legacy, unitamente all'applicazione indiscriminata dell'operatore `MAX` su `total_planned_chunks`.

#### Correzione implementata in `analysis/checkpoint.py`
1. **Confronto preliminare rigoroso della testata**: prima di valutare l'importazione di qualunque chunk, vengono confrontati punto per punto:
   - `document_id`
   - `analysis_type`
   - `model_id`
   - `prompt_version`
   - `total_planned_chunks`
2. **Incompatibilità = Nessuna alterazione**: se uno solo di questi campi differisce tra SQLite e legacy, il flag `header_compatible` viene impostato a `False`. Il ciclo salta immediatamente (`continue`):
   - La testata SQLite **non viene alterata**;
   - Nessun chunk alieno viene importato in SQLite;
   - `total_planned_chunks` **non viene aggiornato** con `MAX`;
   - Il file legacy incompatibile viene **preservato intatto su disco**;
   - Viene emesso un log diagnostico di avviso sul conflitto rilevato.
3. **Validazione di indici e payload**:
   - Rifiuto esplicito di indici chunk non numerici o con indice negativo o $\ge \text{total\_planned\_chunks}$;
   - Validazione della provenienza del chunk: `provenance_document_id` deve coincidere con `document_id` o `f"{doc_id}::chunk::{c_idx}"`;
   - Qualsiasi anomalia marca il file come conflittuale e ne blocca l'eliminazione.

---

## 3. MATRICE DEI TEST DI REGRESSIONE E VALIDAZIONE (29/29)

Nella suite specialistica `tests/unit/test_checkpoint_integrity_scalability.py` sono stati eseguiti con successo tutti i 23 test preesistenti e aggiunti 6 nuovi test dedicati che coprono integralmente i 12 requisiti obbligatori:

| ID | Nome Test | Esito Pre-Fix | Esito Post-Fix | Requisito Coperto |
|---|---|:---:|:---:|---|
| **01-16** | Requisiti Sezione 9 (UI, istanza LM Studio, atomicità, linearità O(N), HTTP counter, profiling) | PASSED | **PASSED** | Requisiti di baseline congelati |
| **17** | Cancellazione selettiva per versione documento (fingerprint crittografico) | FAILED (1!=2) | **PASSED** | Nessuna cancellazione collaterale di versioni diverse |
| **18** | Validazione provenienza e payload | PASSED | **PASSED** | Rifiuto payload alieni o contraffatti |
| **19** | Migrazione atomica transazionale JSON -> SQLite | PASSED | **PASSED** | Transazionalità e consistenza |
| **20** | Durabilità WAL e concorrenza multi-processo | PASSED | **PASSED** | Robustezza crash/riavvio |
| **21** | Precedenza SQLite su file legacy corrotto | PASSED | **PASSED** | JSON corrotto non maschera SQLite valido |
| **22** | Riconciliazione unificata clear_all_for_document_id | PASSED | **PASSED** | Eliminazione completa per identificativo |
| **23** | Precedenza SQLite su legacy divergente o parziale (8 scenari) | PASSED | **PASSED** | SQLite consolidato prevale su legacy discordante |
| **24** | `test_24_valid_legacy_preserved_when_sqlite_chunk_corrupted` | **FAILED** (legacy unlinked) | **PASSED** | **Correzione 1**: SQLite corrotto non elimina legacy valido |
| **25** | `test_25_clear_all_for_document_id_preserves_corrupted_legacy_with_matching_name` | **FAILED** (pass unlink) | **PASSED** | **Correzione 2**: File corrotti non attribuibili preservati |
| **26** | `test_26_metadata_validation_before_merge_rejects_incompatible_legacy` | **FAILED** (total=2 & chunk alieno) | **PASSED** | **Correzione 3**: Testata SQLite intatta, nessun MAX, legacy preservato |
| **27** | `test_27_reconciliation_rejects_non_numeric_and_out_of_range_indices` | **FAILED** | **PASSED** | Indici non numerici e fuori intervallo pianificato rifiutati |
| **28** | `test_28_reconciliation_rejects_incompatible_analysis_metadata` | **FAILED** | **PASSED** | Rifiuto discrepanze in `analysis_type`, `model_id`, `prompt_version` |
| **29** | `test_29_migration_interruption_recovery_and_evidence_preservation` | PASSED | **PASSED** | Migrazione fedele 100% di decisioni, rationale, evidence ID e metadati |

---

## 4. VERIFICA INTEGRITÀ E CONGELAMENTO SCIENTIFICO

### File scientifici congelati
I tre file scientifici e le metriche di benchmark sono stati verificati prima e dopo l'intervento:

| File | SHA-256 Rilevato | Stato |
|---|---|:---:|
| `ai/experiment.py` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | **INVARIATO** |
| `ai/benchmark.py` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | **INVARIATO** |
| `run_benchmark.sh` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | **INVARIATO** |

### Verifica della Wheel e corrispondenza sorgenti
- Costruzione eseguita tramite: `python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .`
- File wheel generato: `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (316.078 byte)
- **Sorgenti Python nella wheel**: esattamente **89/89 corrispondenti byte-per-byte** ai sorgenti del repository.
- Nessuna presenza di cartelle `__pycache__` o file `.pyc`.
- Test di import isolato in ambiente `isolated_site_packages`: **SUCCESS**.

---

## 5. ESITO DELLA SUITE COMPLETA (PYTEST & APPTEST)

Esecuzione automatizzata completa registrata in `pytest_and_apptest.log`:
```
========== 1762 passed, 1 skipped, 5 deselected in 187.44s (0:03:07) ==========
```
- **Test totali superati**: 1762 (incremento netto di +6 test rispetto ai 1756 della release precedente);
- **Test falliti**: 0;
- **Errori di esecuzione**: 0;
- **Streamlit AppTest**: 7/7 superati in `test_apptest_flows.py`.

---

## 6. LIMITAZIONI RESIDUE & SCELTE DI DESIGN FORENSE

1. **Riparazione automatica conservativa**: se un chunk in SQLite è corrotto e il file legacy valido è disponibile, ChatAnalysis non sovrascrive automaticamente SQLite con il dato legacy per prevenire conflitti a catena in installazioni concorrenti. Il file legacy viene preservato per consentire all'analista forense la decisione esplicita di ripristino o la pulizia tramite l'apposita API `clear_for_document()`;
2. **Conservazione permanente dei file non attribuibili**: i file danneggiati il cui contenuto non è parsabile né attribuibile con certezza matematica rimangono nella directory `.checkpoints` con avviso diagnostico nei log, per preservare l'integrità probatoria della catena di custodia;
3. **Nessun impatto su throughput o latenza**: la verifica analitica viene eseguita una sola volta in fase di riconciliazione e non impatta le normali operazioni O(1) di append dei blocchi SQLite durante l'analisi.

---

## 7. PROCEDURA DI AGGIORNAMENTO SU UBUNTU (RUNBOOK)

Per applicare l'aggiornamento su sistemi Ubuntu di produzione:

```bash
# 1. Arrestare eventuali istanze di ChatAnalysis / Streamlit
pkill -f "streamlit run" || true

# 2. Decomprimere il pacchetto di release di hardening
unzip -q chatAnalysis_sqlite_legacy_reconciliation_hardening_audit_20261015.zip -d chatAnalysis_update
cd chatAnalysis_update

# 3. Verificare l'integrità del manifest SHA256
sha256sum -c MANIFEST_SHA256.txt | grep -v "OK$" || echo "Tutti i file sono integri!"

# 4. Installare la wheel aggiornata nell'ambiente virtuale
source /opt/chatAnalysis_venv/bin/activate
pip install --no-deps --force-reinstall dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl

# 5. Eseguire la suite di verifica specialistica di integrità
pytest tests/unit/test_checkpoint_integrity_scalability.py -v

# 6. Riavviare l'applicazione Streamlit
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```

---
*Report validato e conforme per l'audit di rilascio.*
