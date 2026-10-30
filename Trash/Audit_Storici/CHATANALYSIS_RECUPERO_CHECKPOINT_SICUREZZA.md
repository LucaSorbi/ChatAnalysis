# CHATANALYSIS — RAPPORTO TECNICO DI MESSA IN SICUREZZA DEI CHECKPOINT E RECUPERO CONTROLLATO

**Data:** 25 Ottobre 2026 — Ore 21:45  
**Ambiente:** Microsoft Windows 11 Pro 64-bit | Python 3.12.10 (`.venv312`)  
**Backend:** LM Studio `lms.exe` v1.3.3.0 su `http://127.0.0.1:1234`  
**Modello Verificato:** `qwen2.5-7b-instruct` (Q4_K_M GGUF, context 8192, 4 slot paralleli, CPU-only)  
**Database Checkpoint Isolato:** `.checkpoints_reconstructed_verified_20261025/checkpoints.db` (98.304 byte)  
**Backup Consistente Immutato:** `.checkpoints_backup_20261025_regression/checkpoints_backup_20261025_204920.db`  
**Produzione Immutata:** `.checkpoints/checkpoints.db` (143.360 byte)  

---

## EXECUTIVE SUMMARY

In ottemperanza alle direttive di messa in sicurezza, è stato eseguito un intervento strutturale e forense completo su cinque obiettivi:
1. **Eliminazione Radicale del Rischio di Cancellazione:** Rimosse le tre fixture pytest distruttive che eseguivano `shutil.rmtree(Path(".checkpoints"))`. L'intera suite di test opera ora esclusivamente su directory temporanee isolate (`tmp_path`) iniettate tramite `CHATANALYSIS_CHECKPOINT_DIR`.
2. **Dimostrazione Controllata della Sicurezza:** Un test con file sentinella sintetico pre/post esecuzione ha certificato l'identità crittografica byte-per-byte (SHA-256 e timestamp identici, zero alterazioni).
3. **Verifica Forense dei Payload e Ricostruzione Controllata:**
   - La verifica approfondita dei log ha accertato che i 22 blocchi validati della Run 1 (`run_disc_3f3731eb04`) hanno diagnostiche registrate nei log, ma i payload JSON completi con topic ed evidenze risiedevano unicamente nel database cancellato prima della Run 2.
   - In conformità alla regola aurea di non inventare payload e non accettare mere stringhe SUCCESS, il recupero dei 16 chunk non presenti nella Run 2 è stato responsabilmente **interrotto**, documentando la lacuna.
   - È stato creato e validato un **nuovo database di checkpoint isolato** contenente i 7 chunk con payload verificato e integro (compreso il **Chunk 25**), con stato calcolato `PARTIAL (7/26)`, `PRAGMA integrity_check: ok` e zero violazioni di foreign key.
4. **Verifica Tecnica LM Studio:** Documentata l'origine del crash dello stack grammaticale GBNF interno a llama.cpp (`Unexpected empty grammar stack after accepting piece: G (38)`) e chiarito che i troncamenti JSON derivano dallo sbilanciamento tra densità di token del chunk e budget di output `max_tokens` (finish_reason `length`), non dalla mera lentezza CPU.
5. **Validazione Isolata:** Eseguiti test di sicurezza negativi, integrità SQLite, ripresa selettiva e rifiuto allucinazioni con **32 test passati su 32 (Exit Code: 0)** senza alcuna inferenza sui dati reali.

---

## 1. QUALI FIXTURE DISTRUTTIVE SONO STATE CORRETTE

Tre file di test unitari contenevano fixture `@pytest.fixture(autouse=True)` responsabili della cancellazione della directory `.checkpoints` di produzione:

| File di Test | Fixture Originale | Difetto Rilevato | Correzione Applicata |
| :--- | :--- | :--- | :--- |
| `tests/unit/test_subchunk_split_recovery_and_coverage.py` (linee 60-68) | `clean_test_checkpoints` | Eseguiva `shutil.rmtree(Path(".checkpoints"), ignore_errors=True)` pre e post yield. | Sostituita con fixture isolata `isolated_checkpoint_dir(tmp_path, monkeypatch)`: alloca una directory univoca in `tmp_path / "test_checkpoints"` e imposta `CHATANALYSIS_CHECKPOINT_DIR`. Pulizia vincolata alla sola cartella temporanea. |
| `tests/unit/test_ui_manual_topic_detection.py` (linee 56-66) | `clean_checkpoints` | Eseguiva `shutil.rmtree(Path(".checkpoints"), ignore_errors=True)`. | Sostituita con fixture isolata `isolated_checkpoint_dir(tmp_path, monkeypatch)` con mock store su `tmp_path`. |
| `tests/unit/test_apptest_flows.py` (linee 46-56) | `clean_checkpoints` | Eseguiva `shutil.rmtree(Path(".checkpoints"), ignore_errors=True)`. | Sostituita con `isolated_apptest_checkpoints(tmp_path, monkeypatch)` che reindirizza le sessioni AppTest Streamlit su `tmp_path`. |

Inoltre, per garantire che l'applicazione rispetti l'isolamento anche a runtime:
- `ui/application.py` e `ui/presentation.py` sono stati aggiornati per dare precedenza alla variabile d'ambiente `os.environ.get("CHATANALYSIS_CHECKPOINT_DIR")` prima di ripiegare sul percorso relativo di produzione `.checkpoints`.

---

## 2. QUALI ALTRI RISCHI DI CANCELLAZIONE SONO STATI TROVATI

È stato condotto un audit sistematico su tutti i 74 file di test (`tests/**/*.py`) ricercando comandi potenzialmente distruttivi:
- `shutil.rmtree`
- `os.remove` / `Path.unlink`
- Percorsi relativi condivisi (`Path(".checkpoints")`, `Path(".checkpoints_test")`, `data/`, `output/`).

### Esito dell'Audit:
1. **Nessun'altra fixture o test cancella `Path(".checkpoints")`:**
   Le fixture corrette nei 3 file sopra citati erano le uniche istruzioni dell'intero repository a colpire la directory `.checkpoints`.
2. **Uso controllato di `shutil.rmtree` nel resto della suite:**
   Tutte le altre chiamate a `shutil.rmtree` nella suite sono rigorosamente confinate a directory create a runtime tramite `tempfile.TemporaryDirectory()`, sottocartelle gestite da `tmp_path`, o directory di build effimere (`build/lib`, `dist/`).
3. **Risolto Rischio di Cablaggio Hardcoded in `ui/presentation.py` (Linea 200):**
   In precedenza, l'interfaccia Streamlit per l'analisi creava l'istanza `AnalysisCheckpointStore(checkpoint_dir=Path(".checkpoints"))` in modo statico. È stato aggiornato inserendo la lettura di `CHATANALYSIS_CHECKPOINT_DIR`, impedendo interferenze accidentali quando i test di integrazione UI interagiscono con la sessione Streamlit.

---

## 3. PROVA CONCRETA DELLA PRESERVAZIONE DEI CHECKPOINT

Per dimostrare l'assoluta sicurezza della suite è stata eseguita una prova controllata tramite lo script [`scripts/demo_sentinel_preservation.py`](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/scripts/demo_sentinel_preservation.py).

### Metodologia della Prova:
1. Creato un file sentinella sintetico in cartella isolata: `sentinel_controlled_guard/sentinel_control.db` (1.824 byte).
2. Registrati metadati prima dell'esecuzione:
   - **SHA-256 PRE-TEST:** `e5de264625b838d95f8103c447082d6824e0d20c009a655b33a7bede22e3b6ad`
   - **Dimensione:** 1.824 byte
   - **Timestamp di modifica:** `1792960693.9003806`
3. Eseguita l'intera suite di test che precedentemente cancellava i dati (`test_subchunk_split_recovery_and_coverage.py`, `test_ui_manual_topic_detection.py`, `test_apptest_flows.py`).
4. Verificati metadati post-test:
   - **SHA-256 POST-TEST:** `e5de264625b838d95f8103c447082d6824e0d20c009a655b33a7bede22e3b6ad`
   - **Dimensione:** 1.824 byte (identica)
   - **Timestamp di modifica:** `1792960693.9003806` (invariato)
5. **Verifica sui Checkpoint di Produzione Reali:**
   Il file `.checkpoints/checkpoints.db` di produzione è stato controllato dopo tutte le esecuzioni di pytest:
   - Dimensione: 143.360 byte
   - Timestamp di modifica: `1792958886.543041` (immutato, non è stato né aperto in scrittura né cancellato).

---

## 4. ESITO DELLA VERIFICA DEI 22 PAYLOAD STORICI

È stata effettuata un'ispezione analitica esaustiva sui log del sistema (`transcript_full.jsonl`, `transcript.jsonl`), sui log del motore LM Studio (`main.log`) e su tutte le cartelle temporanee.

### Risultanze Forensi:
1. **Presenza delle Diagnostiche di Esecuzione (Run 1):**
   Nel log `transcript_full.jsonl` (linea 399) sono conservate le informazioni di monitoraggio per tutti i 26 chunk della run `run_disc_3f3731eb04`:
   - 22 chunk risultano con `status: SUCCESS`, `attempts: 1` o `2`, `http_status: 200`, `finish_reason: stop` e durata tra 56.6s e 100.8s.
   - 4 chunk risultano con `status: FAILED` (chunk 19, 23, 24, 25).
2. **Assenza dei Corpi Serializzati (`serialized_result` JSON):**
   Nei log non sono presenti i payload completi contenenti gli array `topics`, i testi delle `short_description` e i vettori di `evidence_ids` estratti per i 22 blocchi della Run 1. Tali dati erano residenti unicamente nella tabella SQLite `checkpoint_chunks` del file `.checkpoints/checkpoints.db` che è stato cancellato dalle vecchie fixture pytest prima dell'avvio della Run 2.
3. **Decisione Forense Fail-Closed:**
   In ossequio al principio deontologico e all'ordine imperativo:
   - *"Non fidarti di semplici stringhe SUCCESS nei log."*
   - *"Non inventare payload mancanti."*
   - *"Se il materiale dei log non è sufficiente, interrompi il recupero e documenta la lacuna."*
   Il recupero forzato dei 16 chunk mancanti è stato **interrotto**. Nessun payload sintetico o fittizio è stato generato.

---

## 5. ESITO DELLA VERIFICA DEL CHUNK 25

Il Chunk 25 è stato analizzato nel database consistente `.checkpoints_backup_20261025_regression/checkpoints_backup_20261025_204920.db` relativo alla Run 2 (`run_disc_8db1424645`):

| Parametro Chunk 25 | Esito Run 1 (13:23 UTC) | Esito Run 2 (18:59 UTC) | Valore Verificato |
| :--- | :--- | :--- | :--- |
| **Stato** | FAILED | **SUCCESS** | Validato regolarmente |
| **Causa Run 1** | `INVALID_EVIDENCE_ID` | Risolto | Allucinazione non ripetuta |
| **HTTP Status** | 200 OK | 200 OK | 200 OK |
| **finish_reason** | stop | stop | stop |
| **Completion Tokens**| ~280 | 625 | 625 token emessi |
| **Durata** | 87.27s | 47.73s | 47.73s |
| **Dimensione Payload**| N/A | 4.008 byte | 4.008 byte serializzati |
| **Topic Estratti** | 0 validati | **8 topic** | 'Appuntamenti', 'Scuse e Paranoie', 'Allenamenti', 'Proclamazione', 'Conversazione WhatsApp', 'Parole Inopportune', 'Calisthenics', 'Orari e Puntualità' |
| **Evidenze Citate** | ID inesistenti | **44 citazioni valide** | Tutte nel range `unified:whatsapp_export:msg_1031..msg_1049::ORIGINAL_TEXT` |

Il Chunk 25 è **pienamente recuperato, integro e conforme forensemente**.

---

## 6. NUMERO DI CHUNK REALMENTE RECUPERABILI

- **Chunk con Payload Completo e Accertato al 100%:** **7 chunk**  
  Indici: `[1, 3, 4, 9, 16, 18, 25]`.
- **Chunk con Payload Mancante (Gap Documentato):** **19 chunk**  
  Indici: `[0, 2, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 17, 19, 20, 21, 22, 23, 24]`.
- **Stato di Copertura:** **7/26** accertati con prova crittografica e testuale. L'unione teorica a 23/26 non è perseguibile senza re-inferenza dei 16 chunk andati perduti nella cancellazione di `.checkpoints/`.

---

## 7. POSIZIONE DEL NUOVO CHECKPOINT ISOLATO

Il nuovo database ricostruito è stato allocato in una directory dedicata e non in conflitto:
- **Percorso Assoluto:** `c:\Users\Luca\Desktop\Tesi\ChatAnalysis\.checkpoints_reconstructed_verified_20261025\checkpoints.db`
- **Dimensione:** 98.304 byte
- **Regole di Conservazione:**
  - Il file di produzione `.checkpoints/checkpoints.db` **NON è stato modificato**.
  - Il backup `.checkpoints_backup_20261025_regression/checkpoints_backup_20261025_204920.db` **NON è stato modificato**.

---

## 8. VERIFICA DI INTEGRITÀ E PROVENIENZA

Sul database isolato `.checkpoints_reconstructed_verified_20261025/checkpoints.db` sono stati eseguiti i seguenti controlli:
1. **Integrità SQLite:**
   - `PRAGMA integrity_check`: **ok**
   - `PRAGMA foreign_key_check`: **0 violazioni**
2. **Provenienza e Fingerprint:**
   - Document ID: `doc::whatsapp_export::04691438289a::cb5ef8378d12`
   - Document Fingerprint (SHA-256): `e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478`
   - Config Fingerprint (SHA-256): `089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a`
   - Modello di Riferimento: `qwen2.5-7b-instruct`
3. **Compatibilità con il Meccanismo di Ripresa:**
   - L'istanza `AnalysisCheckpointStore(checkpoint_dir=Path(".checkpoints_reconstructed_verified_20261025"))` carica con successo il checkpoint, restituendo esattamente i 7 chunk validati `(1, 3, 4, 9, 16, 18, 25)`.
4. **Stato Calcolato del Checkpoint:**
   - **Stato:** `PARTIAL (7/26)`.
   - Il flag di completamento è rigorosamente `False`. Lo stato non viene mai promosso a `SUCCESS`.

---

## 9. TEST ESEGUITI ED EXIT CODE

Tutti i test sono stati condotti in ambiente rigorosamente isolato, senza avviare inferenze sui dati reali:

| Test / Script | Descrizione Funzionale | Risultato | Exit Code |
| :--- | :--- | :---: | :---: |
| `scripts/demo_sentinel_preservation.py` | Verifica pre/post hash SHA-256 e timestamp del file sentinella durante l'esecuzione dei test. | **PASS** | **0** |
| `scripts/build_reconstructed_checkpoint.py` | Clonazione sicura, integrity check SQLite, validazione chunk 25, verifica compatibilità store. | **PASS** | **0** |
| `tests/unit/test_sentinel_checkpoint_protection.py` | Verifica assenza di pattern rmtree hardcoded e immutabilità sentinella. | **2 passed** | **0** |
| `tests/unit/test_checkpoint_isolation_and_recovery_safety.py` | Test negativo cancellazione, integrità SQLite, ripresa selettiva e rifiuto allucinazioni. | **4 passed** | **0** |
| `tests/unit/test_subchunk_split_recovery_and_coverage.py` | Verifica ripresa subchunk e non duplicazione inferenza su sandbox `tmp_path`. | **6 passed** | **0** |
| `tests/unit/test_ui_manual_topic_detection.py` | Flussi UI di detection manuale con checkpoint isolato. | **13 passed** | **0** |
| `tests/unit/test_apptest_flows.py` | Flussi AppTest Streamlit integrati con variabile `CHATANALYSIS_CHECKPOINT_DIR`. | **7 passed** | **0** |
| **Suite Integrata di Sicurezza (32 test)** | Esecuzione congiunta di tutti i file d'area corretti e di sicurezza. | **32 passed in 12.18s** | **0** |

---

## 10. DIAGNOSTICA TECNICA LM STUDIO (GBNF E JSON) E BLOCCHI APERTI

### 1. Diagnostica Reale dei Fallimenti GBNF:
- **Manifestazione nei Log:** Nel file di LM Studio (`C:\Users\Luca\AppData\Roaming\LM Studio\logs\main.log`) è registrato:
  `{"code": 500, "message": "got exception: Unexpected empty grammar stack after accepting piece: G (38)", "type": "server_error"}`
- **Causa Radice Tecnica:** Nel parser grammaticale BNF di llama.cpp (versione v1.3.3.0), quando la generazione guidata tenta di inserire un frammento di token (in questo caso 'G' con codice token 38 o token id analogo) su un confine di stato privo di transizioni grammaticali aperte, la funzione `llama_grammar_accept_piece` svuota lo stack e solleva un'eccezione C++.
- **Mitigazione Applicata:** In `ai/lmstudio.py`, l'errore è ora correttamente classificato come `INFERENCE_BACKEND_ERROR` (invece di falso `UNSUPPORTED_STRUCTURED_OUTPUT`), consentendo il retry transitorio in `ai/topics.py`.
- **Rigorismo Scientifico:** Non si assume né si afferma che l'uso di una GPU elimini automaticamente il bug GBNF. L'errore risiede nella logica dello stack BNF di llama.cpp nello spazio dei token e richiede verifiche sperimentali mirate.

### 2. Diagnostica Reale dei Fallimenti JSON (`JSON_DECODE_ERROR`):
- **Causa Radice:** Non è la "CPU lenta" a corrompere il JSON. La CPU lenta comporta circa 5 token/s. Nei chunk ad alta densità informativa, la serializzazione dell'array dei topic richiede oltre 600-1000 token. Al raggiungimento del limite `max_tokens` (o del budget di generazione), il server LM Studio tronca la risposta restituendo `finish_reason: "length"`.
- Il JSON inviato al client termina a metà di una stringa o prima di `]}`. Il deserializzatore Python `json.loads` fallisce legittimamente con `JSONDecodeError`.
- La durata di ~97s per tentativo è la conseguenza del raggiungimento del budget di generazione alla velocità della CPU, non la causa dell'errore.

### 3. Blocchi Aperti e Istruzioni per il Completamento:
- Il sistema è **totalmente protetto contro cancellazioni accidentali dei checkpoint**.
- Per completare l'elaborazione dei 19 chunk mancanti della conversazione reale originale (`doc::whatsapp_export::04691438289a::cb5ef8378d12`), l'utente potrà:
  1. Caricare il file esportato nella sessione Streamlit di ChatAnalysis.
  2. Premere il pulsante per riprendere i soli blocchi non ancora validati.
  3. Il sistema salterà i 7 chunk già validati (1, 3, 4, 9, 16, 18, 25) ed eseguirà in inferenza unicamente i restanti 19 chunk.
- Nessun commit, push o creazione di ZIP è stato eseguito, in piena conformità ai vincoli operativi.
