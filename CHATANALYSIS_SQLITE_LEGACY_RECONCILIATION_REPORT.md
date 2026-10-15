# CHATANALYSIS — REPORT DI RICONCILIAZIONE FINALE SQLITE E CHECKPOINT LEGACY

**Release di riferimento**: `chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip`  
**Data di completamento**: 15 Ottobre 2026  
**Ambiente di esecuzione e collaudo**: Windows 11 (NTFS) / Python 3.14.7 / SQLite 3 (WAL mode)  
**Esito complessivo dei collaudi**: **1756 test superati**, 1 saltato, 5 esclusi (benchmark congelati), **0 fallimenti**  
**Suite Specialistica di Integrità e Scalabilità**: **23 su 23 test superati**  
**Streamlit AppTest E2E**: **7 su 7 test superati**  
**Packaging Wheel e Import Isolato**: **CONFORME** (89 sorgenti Python, zero errori)  
**Integrità File Scientifici Congelati**: **3 su 3 invariati (100% conformi)**  

---

## 1. SINTESI ESECUTIVA

In seguito all'audit indipendente condotto sulla release `chatAnalysis_final_checkpoint_validation_audit_20261014.zip`, è stata confermata la piena validità della cancellazione selettiva per fingerprint crittografico e della validazione esaustiva della provenienza forense. Tuttavia, sono stati riprodotti sperimentalmente tre difetti critici nel modulo `analysis/checkpoint.py`, relativi alla coesistenza e riconciliazione tra i checkpoint incrementali SQLite e i file residui legacy JSON:

1. **Un file JSON legacy corrotto mascherava un checkpoint SQLite valido**: se per una determinata run esisteva un record integro e valido in SQLite ma su disco compariva un file legacy JSON malformato, `load_checkpoint()` falliva prematuramente restituendo `None`, mentre `has_checkpoint()` restituiva `True`.
2. **Cancellazione incompleta per `document_id`**: il metodo `clear_all_for_document_id()` cancellava le righe SQLite ma cercava i file legacy JSON mediante il pattern errato `chk_{doc_id[:16]}_*.json` (mentre i file legacy sono nominati tramite fingerprint). Di conseguenza, i file JSON rimanevano su disco e una successiva chiamata a `load_checkpoint()` o `_migrate_legacy_json_if_needed()` li rimigrava in SQLite, resuscitando il checkpoint cancellato.
3. **Sovrascrittura distruttiva di record SQLite validi con dati legacy obsoleti**: durante la migrazione dei file legacy, l'istruzione SQL utilizzava `ON CONFLICT ... DO UPDATE`. Se in SQLite era memorizzato un risultato consolidato (es. `decision=PRESENT`) e nel file legacy era presente un dato obsoleto o difforme (es. `decision=ABSENT`), la migrazione sovrascriveva il dato SQLite valido con quello legacy ed eliminava il file originale, distruggendo la storia investigativa.

Tutti e tre i difetti sono stati risolti alla radice mediante interventi mirati e conservativi in `analysis/checkpoint.py`, senza riscrivere il programma e senza alterare i moduli scientifici, di ricerca o di interfaccia utente.

---

## 2. DETTAGLIO DELLE TRE CAUSE, DIAGNOSI E CORREZIONI IMPLEMENTATE

### CORREZIONE 1 — Priorità a SQLite e Tolleranza ai File Legacy JSON Corrotti

#### Causa Radice
Nel metodo `load_checkpoint()`, il codice eseguiva preliminarmente:
```python
p = self._get_checkpoint_path(document_fingerprint, config_fingerprint)
if p.is_file():
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        ...
        self._migrate_legacy_json_if_needed()
    except Exception:
        return None
```
Se il file legacy JSON conteneva caratteri corrotti o sintassi non valida, il blocco `except Exception: return None` interrompeva l'esecuzione ed usciva immediatamente, senza mai consultare la tabella SQLite. Al contrario, `has_checkpoint()` interrogava prioritariamente SQLite (`SELECT 1 FROM checkpoint_chunks ...`), restituendo `True`. Questa asimmetria faceva sì che la presenza accidentale di un artefatto corrotto su disco impedisse il caricamento di un'analisi valida già consolidata nel database relazionale.

#### Correzione Implementata
1. **Precedenza assoluta a SQLite**: `load_checkpoint()` interroga prioritariamente SQLite tramite il metodo helper protetto `_load_checkpoint_from_sqlite()`.
2. **Tolleranza ad artefatti legacy corrotti**: se SQLite contiene un checkpoint valido e su disco è presente un file legacy:
   - se il file legacy è corrotto o non decodificabile, viene emesso un avviso di diagnostica tecnica sicura (privo di PII):
     `logger.warning("File legacy JSON corrotto o illeggibile rilevato per (%s, %s): %s. Utilizzato checkpoint SQLite valido consolidato; file legacy preservato.")`
   - il file corrotto **non viene eliminato automaticamente** (preservazione per analisi tecnica);
   - il checkpoint valido di SQLite viene **restituito regolarmente all'applicazione**.
3. **Fallback protetto se SQLite è assente**: soltanto se SQLite non contiene il record, il metodo tenta il caricamento dal file legacy. Se anche il file legacy è corrotto o mancante, restituisce `None`.
4. **Verifica sperimentale**: Il nuovo test `test_21_corrupt_legacy_json_does_not_mask_valid_sqlite` dimostra che con SQLite integro e JSON legacy malformato (`{ MALFORMED_JSON_CORRUPTED_SYNTAX :::: [`), `load_checkpoint()` restituisce con successo il checkpoint valido, `has_checkpoint()` è coerente (`True`), il file corrotto è preservato e la validazione della provenienza ha esito positivo.

---

### CORREZIONE 2 — Cancellazione Completa per Document ID e Riconciliazione Legacy

#### Causa Radice
Nel metodo `clear_all_for_document_id(document_id)`:
1. Venivano eliminate le righe da SQLite con `WHERE document_id = ?`, ma **non venivano annotati preventivamente i `document_fingerprint`** associati a quelle righe.
2. La pulizia dei file legacy cercava file con pattern:
   ```python
   for p in self.checkpoint_dir.glob(f"chk_{doc_id[:16]}_*.json"):
   ```
   Tuttavia, i file legacy JSON di ChatAnalysis sono denominati in base al fingerprint del documento e della configurazione (`chk_{document_fingerprint[:16]}_{config_fingerprint[:16]}.json`) e **non** in base al `document_id`. Di conseguenza, il glob non corrispondeva ad alcun file reale, i file legacy rimanevano intatti su disco, e al successivo riavvio dello store o caricamento dell'analisi il file veniva rimigrato in SQLite, annullando la cancellazione richiesta.

#### Correzione Implementata
1. **Discovery preventiva dei fingerprint**: prima di qualunque cancellazione, viene eseguita la query:
   ```sql
   SELECT DISTINCT document_fingerprint FROM checkpoints WHERE document_id = ?;
   ```
   raccogliendo l'insieme `associated_fps`.
2. **Scansione e attribuzione rigorosa dei file legacy JSON**:
   - Vengono esaminati i file `chk_*.json` presenti nella cartella.
   - Per ciascun file, viene letto il payload JSON: se `data.get("document_id") == doc_id` oppure `data.get("document_fingerprint") in associated_fps`, il file viene marcato per l'eliminazione e il suo fingerprint viene aggiunto a `associated_fps`.
   - **Protezione di documenti terzi**: file appartenenti ad altri documenti logici non vengono mai toccati.
   - **Gestione file corrotti non attribuibili**: se un file non è decodificabile (es. corrotto), viene registrato un warning e il file viene preservato su disco, evitando cancellazioni arbitrarie e distruttive di file ignoti.
3. **Cancellazione atomica SQLite**:
   - `DELETE FROM checkpoints WHERE document_id = ?;` (con cancellazione a cascata dei chunk via Foreign Key);
   - `DELETE FROM checkpoints WHERE document_fingerprint IN (...);` per rimuovere eventuali record residui associati ai fingerprint identificati.
4. **Rimozione selettiva dei file legacy**: vengono eliminati esclusivamente i file confermati appartenenti al `document_id` selezionato.
5. **Garanzia di non resurrezione**: eliminando sia SQLite sia i corrispondenti file legacy, una successiva chiamata a `load_checkpoint()` per quel documento restituisce deterministicaamente `None`.
6. **Verifica sperimentale**: Il nuovo test `test_22_clear_all_for_document_id_reconciles_fingerprints_and_legacy_json` verifica che cancellando un `document_id` condiviso da più versioni (v1 e v2), entrambe le versioni vengano rimosse sia da SQLite sia da disco, che il documento terzo `other_doc` rimanga inalterato (sia in SQLite sia in JSON), che il file corrotto ignoto sia preservato e che nessuna resurrezione si verifichi.

---

### CORREZIONE 3 — Precedenza dei Dati Consolidati SQLite e Riconciliazione Non Distruttiva

#### Causa Radice
In `_migrate_legacy_json_if_needed()`, l'inserimento dei blocchi migrati utilizzava:
```sql
INSERT INTO checkpoint_chunks (
    document_fingerprint, config_fingerprint, chunk_index,
    serialized_result, created_at
) VALUES (?, ?, ?, ?, ?)
ON CONFLICT(document_fingerprint, config_fingerprint, chunk_index) DO UPDATE SET
    serialized_result = excluded.serialized_result;
```
Questa istruzione causava la sovrascrittura incondizionata dei risultati già presenti in SQLite con i dati del file legacy. Nel caso riprodotto in audit:
- SQLite conteneva un risultato valido e recente: `decision=PRESENT`;
- Su disco era presente un file legacy obsoleto: `decision=ABSENT`;
- Al riavvio dello store, il file legacy sovrascriveva `PRESENT` con `ABSENT`, alterando il giudizio investigativo, e poi il file JSON veniva cancellato con `unlink()`.

#### Correzione Implementata
1. **Ispezione preliminare dello stato SQLite**: prima di inserire dati da un file legacy, lo store legge lo stato consolidato in SQLite per la coppia `(document_fingerprint, config_fingerprint)`.
2. **Confronto analitico blocco per blocco**:
   - **Blocco identico**: se il blocco $c\_idx$ è già presente in SQLite con payload identico, non viene eseguita alcuna modifica.
   - **Blocco mancante (SQLite incompleto)**: se il blocco $c\_idx$ è presente nel legacy ma assente in SQLite (es. un'interruzione di corrente aveva impedito la sincronizzazione di quel solo blocco), il blocco viene inserito in SQLite.
   - **Blocco divergente (Conflitto)**: se il blocco $c\_idx$ è presente in SQLite ma con contenuto differente dal legacy (es. `PRESENT` vs `ABSENT`):
     - **Precedenza tassativa al dato consolidato in SQLite**: SQLite non viene in alcun modo sovrascritto (`ON CONFLICT ... DO NOTHING`);
     - **Segnalazione tecnica esplicita**: viene emesso un log `WARNING`:
       `"Conflitto rilevato per checkpoint (%s, %s) blocco %d nel file %s: il dato consolidato in SQLite prevale. Il file legacy viene preservato intatto."`
     - **Preservazione del file legacy**: il flag `has_conflict = True` **impedisce tassativamente l'eliminazione del file JSON** (`jf.unlink()` non viene eseguito), preservando il dato legacy discordante su disco per audit e revisione manuale.
3. **Condizione di eliminazione del file legacy**: il file JSON legacy viene rimosso solo e soltanto se:
   - non è stato rilevato alcun conflitto (`has_conflict is False`);
   - tutti i blocchi sono verificati come persistiti in SQLite (`persisted_count >= len(s_results)`).
4. **Verifica sperimentale degli 8 scenari obbligatori**:
   Il test `test_23_sqlite_precedence_no_overwrite_by_obsolete_legacy` testa sistematicamente e supera tutti gli 8 casi richiesti:
   1. *SQLite PRESENT / legacy ABSENT*: SQLite mantiene `PRESENT`, legacy preservato su disco;
   2. *SQLite ABSENT / legacy PRESENT*: SQLite mantiene `ABSENT`, legacy preservato su disco;
   3. *Risultati identici in entrambi gli archivi*: SQLite preservato, legacy eliminato dopo verifica;
   4. *SQLite valido / legacy corrotto*: SQLite restituito regolarmente, legacy corrotto preservato;
   5. *Legacy valido / SQLite assente*: migrazione completata con successo in SQLite, legacy eliminato;
   6. *Legacy valido / SQLite incompleto*: chunk 0 in SQLite preservato, chunk 1 mancante integrato in SQLite, legacy eliminato;
   7. *Riavvio dello store con dati già migrati*: 3 re-inizializzazioni consecutive mantengono i dati immutati e stabili;
   8. *Nessuna perdita o sovrascrittura silenziosa*: decisioni, motivazioni analitiche, evidence ID e metadati non subiscono alcuna mutazione artificiale.

---

## 3. RIEPILOGO DEI COLLAUDI COMPLESSIVI

Tutti i collaudi sono stati eseguiti con successo nell'ambiente di lavoro locale:

### 3.1 Suite Generale di Test (Pytest)
- **Comando eseguito**: `.venv\Scripts\python.exe scripts/run_and_log_tests_utf8.py`
- **File di log**: [`pytest_and_apptest.log`](file:///c:/Users/lucas/Desktop/Tesi/pytest_and_apptest.log)
- **Totale test raccolti**: 1.762
- **Test deselezionati**: 5 (benchmark scientifici congelati)
- **Test saltati**: 1 (`test_sanitize_dataset.py::test_dataset_sanitization_requires_explicit_consent`)
- **Test superati**: **1.756**
- **Test falliti**: **0**

### 3.2 Suite Specialistica Integrità e Scalabilità
Eseguiti tutti i 23 test in [`tests/unit/test_checkpoint_integrity_scalability.py`](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_checkpoint_integrity_scalability.py):
- `test_01` a `test_16`: Test storici di scalabilità, contatore HTTP, robustezza errori 400, profiling topic, verifica consistenza: **PASSED**
- `test_17`: Cancellazione selettiva per fingerprint (scenario di audit release precedente): **PASSED**
- `test_18`: Validazione completa della provenienza (rifiuto categorico di `FAKE_OTHER_DOCUMENT`): **PASSED**
- `test_19`: Migrazione atomica con durabilità e rollback: **PASSED**
- `test_20`: Garanzie di consistenza e durabilità SQLite WAL (`PRAGMA synchronous=NORMAL`): **PASSED**
- `test_21`: Un JSON corrotto non deve nascondere un checkpoint SQLite valido (CORREZIONE 1): **PASSED**
- `test_22`: Cancellazione completa per document_id e riconciliazione legacy JSON (CORREZIONE 2): **PASSED**
- `test_23`: Precedenza SQLite e riconciliazione con legacy nei relativi 8 scenari (CORREZIONE 3): **PASSED**

### 3.3 Streamlit AppTest End-to-End
Tutti i 7 flussi funzionali in [`tests/unit/test_apptest_flows.py`](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_apptest_flows.py) superati:
- `test_apptest_a` a `test_apptest_g`: **PASSED**

### 3.4 Packaging Wheel e Import Isolato
Eseguito [`tests/unit/test_packaging_release.py`](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_packaging_release.py):
- Costruzione della wheel in ambiente privo di build isolation;
- Ispezione dell'archivio: **89 sorgenti Python inclusi**, zero residui di compilazione;
- Estrazione ed esecuzione di uno script di test in directory isolata con `PYTHONPATH` limitato alla wheel: esito **PASSED**.

---

## 4. VERIFICA DI IMMUTABILITÀ DEI FILE SCIENTIFICI CONGELATI

I tre file scientifici frozen non sono stati toccati in alcuna riga. La verifica dei digest crittografici SHA-256 ne certifica la perfetta corrispondenza con i valori di baseline:

| File Scientifico Congelato | SHA-256 Rilevato | SHA-256 Baseline Ufficiale | Stato di Conformità |
| :--- | :--- | :--- | :---: |
| [`ai/experiment.py`](file:///c:/Users/lucas/Desktop/Tesi/ai/experiment.py) | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | **CONFORME** |
| [`ai/benchmark.py`](file:///c:/Users/lucas/Desktop/Tesi/ai/benchmark.py) | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | **CONFORME** |
| [`run_benchmark.sh`](file:///c:/Users/lucas/Desktop/Tesi/run_benchmark.sh) | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | **CONFORME** |

Tutti i vincoli scientifici del protocollo rimangono invariati:
- Quantizzazione pesi: `Q4_K_M`
- Dimensione finestra di contesto: `8192`
- Temperatura di campionamento: `0.0`
- Timeout massimo di inferenza: `240` secondi
- Lunghezza massima output scientifico: `512` token
- Protocolli analitici: `DIRECT_MULTILINGUAL` e `TRANSLATE_FIRST`
- I nove benchmark ufficiali **non sono stati eseguiti** in questa fase, come prescritto dalle linee guida.

---

## 5. DISTINZIONE METODOLOGICA: AMBIENTE LOCALE OFFLINE VS LIVE UBUNTU

| Ambito di Verifica | Modalità Eseguita | Requisiti e Limiti Operativi |
| :--- | :--- | :--- |
| **Integrità Checkpoint SQLite** | **Offline (Windows 11)** | Testato con mock controllato e database SQLite WAL su filesystem NTFS. |
| **Tolleranza JSON Corrotto** | **Offline (Windows 11)** | Verificato con file sintetici malformati e prioritizzazione di SQLite. |
| **Cancellazione per Document ID** | **Offline (Windows 11)** | Verificato con documenti multi-versione e file legacy associati. |
| **Riconciliazione Precedenza SQLite** | **Offline (Windows 11)** | Verificati tutti gli 8 scenari di conflitto, precedenza e integrazione. |
| **Flussi UI Streamlit** | **Offline (Windows 11)** | Testati con Streamlit `AppTest` verificando interazione, rendering e state. |
| **Inferenza Reale con Modello LLM** | *Non eseguita offline* | Richiede sistema Linux Ubuntu con GPU NVIDIA (GTX TITAN X), context 8192 e demone LM Studio attivo su porta 1234. |
| **Nove Benchmark Scientifici** | *Non eseguiti (congelati)*| Richiedono ambiente Ubuntu LIVE dedicato; codice e script congelati inalterati. |

### Istruzioni Operative per la Validazione su Ambiente Ubuntu
Quando il pacchetto viene trasferito sull'ambiente di collaudo target (Ubuntu 22.04 / 24.04 LTS con GPU NVIDIA GeForce GTX TITAN X):
1. Decomprimere l'archivio:
   ```bash
   unzip chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip -d chatAnalysis
   cd chatAnalysis
   ```
2. Verificare l'integrità dei file tramite il manifest SHA256:
   ```bash
   sha256sum -c MANIFEST_SHA256.txt
   ```
3. Avviare il demone LM Studio con il modello Q4_K_M caricato con finestra di contesto 8192 sulla porta locale 1234.
4. Eseguire la suite di test completa in ambiente isolato:
   ```bash
   pytest -v --tb=short
   ```
5. Per l'esecuzione dei nove benchmark ufficiali congelati, lanciare lo script dedicato:
   ```bash
   bash run_benchmark.sh
   ```

---

## 6. LIMITI RESIDUI E NOTE DI SICUREZZA FORENSE

1. **Permessi Filesystem e Crittografia**:
   La directory `.checkpoints/` e il file `checkpoints.db` sono protetti tramite permessi restrittivi del sistema operativo (`0o700` per la cartella, `0o600` per il file database). Si ribadisce che tali permessi impediscono l'accesso ad altri utenti locali non privilegiati, ma **non equivalgono a una crittografia a riposo dei dati (TDE)**: utenti con privilegi di root o accesso fisico al supporto di memorizzazione possono accedere ai dati SQLite se il filesystem sottostante non è cifrato (es. BitLocker o LUKS).
2. **Preservazione Prudenziale dei File Corrotti Non Attribuibili**:
   Se nella directory `.checkpoints/` è presente un file legacy non decodificabile o con struttura gravemente alterata da cui sia impossibile evincere con certezza il `document_id` di appartenenza, ChatAnalysis sceglie deliberatamente di **non eliminarlo automaticamente**. Tale politica garantisce che nessun artefatto potenzialmente rilevante per una perizia forense venga cancellato senza l'intervento esplicito e consapevole dell'operatore.
3. **Assenza di Dati Personali nei Log**:
   Le segnalazioni di conflitto, le eccezioni diagnostiche e i messaggi di warning contengono esclusivamente identificativi tecnici di chunk, codici di errore e digest crittografici abbreviati. Nessun testo di messaggio, evidence section o rationale investigativo viene riversato nei log.

---

## 7. CONTENUTO DEL PACCHETTO DI RILASCIO FINALE

L'archivio di release è denominato tassativamente:  
**`chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip`**  
Dimensione: ~1.48 MB.  
Verifica CRC: 310 file verificati con successo.

Esso include:
- Tutti i pacchetti sorgente (`ai`, `analysis`, `core`, `entity_resolution`, `importer`, `multimodal`, `normalization`, `profiler`, `search`, `ui`, `unified`, `validation`);
- La wheel aggiornata in `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl`;
- L'intera suite di test (`tests/`) comprendente i 23 test di integrità e scalabilità e i 7 test E2E Streamlit AppTest;
- I dati sintetici in `test_data/`;
- La patch unificata Git UTF-8 `git_diff_sqlite_legacy_reconciliation.patch`;
- Il manifest crittografico `MANIFEST_SHA256.txt` (309 file SHA-256 conformi);
- Il file di stato e tracciamento `GIT_STATUS_AND_BASELINE.md`;
- Il log completo di collaudo `pytest_and_apptest.log`;
- I risultati del benchmark quantitativo `benchmark_checkpoint_persistence_results.json`;
- Il presente documento tecnico `CHATANALYSIS_SQLITE_LEGACY_RECONCILIATION_REPORT.md`.
