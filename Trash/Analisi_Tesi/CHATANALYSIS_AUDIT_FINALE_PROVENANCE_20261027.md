# CHATANALYSIS — RAPPORTO DI AUDIT FINALE SULLA PROVENANCE, CATENA DI CUSTODIA E VERIFICA 26/26

**Data dell'audit:** 27 ottobre 2026  
**Scadenza tesi:** Venerdì 6 novembre 2026  
**Repository:** `C:\Users\Luca\Desktop\Tesi\ChatAnalysis`  
**Ambiente verificato:** Windows 11 (`10.0.26200`), Python `3.12.10` (`.venv312`), LM Studio `http://127.0.0.1:1234`, modello `qwen2.5-7b-instruct` (GGUF `Q4_K_M`, `context_length=8192`, `temperature=0.0`, `seed=42`, `serialization_format=compact_jsonl_v1`).

---

## 1. Verdetto Sintetico e Base Documentale

| Ambito di Verifica | Verdetto | Base Documentale e Riscontro Forense |
| :--- | :--- | :--- |
| **Autenticità dei payload d'inferenza (26/26 parent, 140/140 microchunk)** | **`LIVE_VERIFIED`** | **140/140** microchunk verificati contro i **261** file grezzi `completion_*.json` (`output/live_run_1049_26chunks/raw_completions_audit/`): **93** microchunk diretti (`85` single-call + `8` retry a 2 chiamate con somma token esatta) e **47** microchunk bisecati adattivamente (`aggregation=True`). **26/26** parent chunk verificati come aggregazione deterministica esatta dei rispettivi microchunk. |
| **Catena di custodia del campo `origin` nel database SQLite (`checkpoints.db`)** | **`LIVE_PARTIALLY_VERIFIED`** | **12/26** parent chunk (`[7, 15..25]`) e **57/140** microchunk (Sessione 2, `run_id='run_disc_8221618677'`) sono stati scritti nativamente da `LocalTopicAnalysisService` con `origin='LIVE_LM_STUDIO'`. I **14/26** parent chunk (`[0..6, 8..14]`) e la riga di testata `checkpoints` della Sessione 1 (`17:37Z–19:22Z`) avevano `origin=''`/`None` nello snapshot pre-run e sono stati aggiornati a `'LIVE_LM_STUDIO'` dall'`UPDATE` SQL dello script (mentre gli **83/140** microchunk della Sessione 1 in `checkpoint_subchunks` hanno tuttora `origin=None` in SQLite ma sono riscontrati al 100% nei file HTTP `completion_0001..0182`). |
| **Isolamento da `run_123`, `RECONSTRUCTED_UNVERIFIED` e `MOCK_INSPECTION`** | **`VERIFIED_CLEAN`** | **0** record provenienti da `run_123` (`cfg_fp=8528f745...`), `RECONSTRUCTED_UNVERIFIED` o `MOCK_INSPECTION` sono confluiti nel checkpoint della campagna (`cfg_fp=e1ca43d4...`) o nei 10 topic finali. |
| **Rimozione promozione retroattiva e guardia fail-closed nel codice** | **`VERIFIED_FIXED`** | Rimossi i due `UPDATE` SQL da `scripts/run_canonical_live_discovery_26chunks.py`; introdotta guardia fail-closed in `analysis/service.py` (`_process_subchunks_discovery`, `run_topic_discovery`, `load_persisted_discovery_result`) validata dal test unitario `test_checkpoint_with_missing_origin_never_becomes_live_without_proof`. |

---

## 2. Preservazione degli Originali e Snapshot Read-Only (Criticità #1, Punti 1–2)

Prima di qualsiasi verifica, tutti i database SQLite originali, i backup pre-run e i file di completamento HTTP grezzi sono stati preservati senza alcuna operazione di scrittura (`0` `DELETE`, `0` `UPDATE`, `0` `clear_checkpoint` sui file originali). Tramite l'**Online Backup API** nativa di `sqlite3` (`src.backup(dst)`), sono stati generati 7 snapshot consistenti di sola lettura fuori dal repository in `C:\Users\Luca\Desktop\ChatAnalysis_Audit_Snapshots_20261027\`:

| Snapshot Read-Only (fuori dal repository) | Database Sorgente | Dimensione (byte) | SHA-256 | `PRAGMA integrity_check` |
| :--- | :--- | ---: | :--- | :---: |
| `pre_run_14of26_readonly_copy.db` | `output/backups_20261027/live_run_14of26_snapshot_20261027_194036.db` | `299.008` | `4c999c4d8405df686c0861eb79c18a6c0f76c749e13d76d156d65848fa7e0bd1` | `ok` |
| `pre_run_prod_readonly_copy.db` | `output/backups_20261027/checkpoints_pre_canonical_run_20261027_173712.db` | `512.000` | `8483948155de40c558dc8c88aa917fb9fa5ed1d516e2abdc6589dc7dfaa6a850` | `ok` |
| `final_campaign_26of26_readonly_copy.db` | `output/live_run_1049_26chunks/checkpoints/checkpoints.db` | `503.808` | `07e8f377bf76424c8b136c98212db5bc0daaa812be5420312d429828311b0bc5` | `ok` |
| `final_prod_active_readonly_copy.db` | `output/checkpoints/checkpoints.db` | `503.808` | `07e8f377bf76424c8b136c98212db5bc0daaa812be5420312d429828311b0bc5` | `ok` |
| `phase2_detection_readonly_copy.db` | `output/live_detection_phase2/checkpoints/checkpoints.db` | `40.960` | `fd45c7d01f1aa70cd42b7c389df997aa38a031978c9ecaa64aa8c901dbca8925` | `ok` |
| `phase3_synth_mock_readonly_copy.db` | `output/synthetic_scalability_phase3/checkpoints_mock/checkpoints.db` | `786.432` | `ddfa7703097d7f442a66680183a8acbb565132c967a73377a68a05a1089566e0` | `ok` |
| `phase3_synth_live_readonly_copy.db` | `output/synthetic_scalability_phase3/checkpoints_live_sample/checkpoints.db` | `65.536` | `30816fb3e0caee044931d81e80b97fbcdba5f74f98744fcfa2e5a9f776c74f12` | `ok` |

---

## 3. Confronto Pre-Run vs Finale: Effetto Esatto delle Istruzioni `UPDATE` (Criticità #1, Punti 3–5 e 7)

Il confronto riga per riga tra `pre_run_14of26_readonly_copy.db` (snapshot salvato alle `2026-10-27 19:40:36` al termine della Sessione 1) e `final_campaign_26of26_readonly_copy.db` (database finale al termine della Sessione 2 alle `2026-10-27 20:33:12Z`) dimostra con esattezza matematica quali righe sono state toccate dalle istruzioni `UPDATE` precedentemente presenti in `scripts/run_canonical_live_discovery_26chunks.py` (righe 193–208):

### 3.1 Causa radice dell'`origin` mancante nella Sessione 1 (`17:37:25Z` – `19:22:45Z`)
Durante la Sessione 1 (che ha completato i primi 14 parent chunk `[0..6, 8..14]` e i primi 83 microchunk), il metodo `_process_subchunks_discovery` in `analysis/service.py` invocava `chk_store.save_subchunk(...)` e `chk_store.save_chunk(...)` senza propagare i parametri `origin` e `run_id` nel dizionario `metadata` del risultato aggregato padre né nella chiamata a `save_chunk`. Di conseguenza, i record salvati legittimamente durante la Sessione 1 avevano `origin = ''` nella tabella `checkpoints` e `metadata.origin = None` nelle tabelle `checkpoint_chunks` e `checkpoint_subchunks`.

### 3.2 Conteggio esatto delle righe modificate dall'`UPDATE` dello script all'avvio della Sessione 2 (`19:51Z`)

| Tabella SQLite | Stato in `pre_run_14of26` (pre-Sessione 2) | Effetto esatto dell'`UPDATE` in `run_canonical_live_discovery_26chunks.py` | Scritture native della Sessione 2 (`run_disc_8221618677`) | Stato finale in `final_campaign_26of26` |
| :--- | :--- | :--- | :--- | :--- |
| **`checkpoints`** | **1 riga** (`doc_fp=e78e13f0...`, `cfg_fp=e1ca43d4...`, `created_at=17:37:25Z`, `updated_at=19:22:45Z`) con `origin=''`, `run_id=''` | **1 riga aggiornata**: `origin` cambiato da `''` a `'LIVE_LM_STUDIO'` | `ON CONFLICT DO UPDATE` ha aggiornato `updated_at='20:33:12Z'` e `run_id='run_disc_8221618677'` | **1 riga** (`origin='LIVE_LM_STUDIO'`, `run_id='run_disc_8221618677'`) |
| **`checkpoint_chunks`** | **14 righe** (`chunk_index` in `[0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14]`), tutte con `metadata.origin=None`, `metadata.run_id=None` | **14 righe aggiornate**: aggiunto `"origin": "LIVE_LM_STUDIO"` in `serialized_result.metadata` (lasciando `run_id=None`, `created_at`, topic ed `evidence_ids` invariati al 100%) | **12 nuove righe inserite** (`chunk_index` in `[7, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]`) nativamente con `origin='LIVE_LM_STUDIO'` e `run_id='run_disc_8221618677'` | **26 righe** (`0..25`), tutte con `metadata.origin='LIVE_LM_STUDIO'` (`14` con `run_id=None`, `12` con `run_id='run_disc_8221618677'`) |
| **`checkpoint_subchunks`** | **83 righe** (`parent_chunk_index` in `[0..6, 8..14]` + primi 2 subchunk del chunk `7`), tutte con `metadata.origin=None`, `metadata.run_id=None` | **0 righe modificate** (lo script non eseguiva alcun `UPDATE` su `checkpoint_subchunks`) | **57 nuove righe inserite** (`parent_chunk_index` in `[7, 15..25]`) nativamente con `origin='LIVE_LM_STUDIO'` e `run_id='run_disc_8221618677'` | **140 righe** (`83` della Sessione 1 con `metadata.origin=None`; `57` della Sessione 2 con `metadata.origin='LIVE_LM_STUDIO'`, `metadata.run_id='run_disc_8221618677'`) |
| **`checkpoint_chunk_diagnostics`** | **83 righe** diagnostiche | **0 righe modificate** | **57 nuove righe inserite** | **140 righe** diagnostiche (`140/140` con `status='SUCCESS'`, `http_status=200`, `model_id='qwen2.5-7b-instruct'`) |

### 3.3 Verifica d'integrità dei 14 parent chunk preesistenti e dei 140 microchunk contro i file HTTP grezzi
1. **Confronto bit-a-bit dei contenuti tra `pre_run_14of26` e `final_campaign_26of26`:** per tutti i 14 parent chunk della Sessione 1 (`[0..6, 8..14]`), i topic estratti (`label`, `short_description`, `evidence_ids`), il campo `provenance_document_id` e il timestamp `created_at` sono **identici al 100%** prima e dopo l'`UPDATE`. L'unica chiave aggiunta in `serialized_result.metadata` è `"origin": "LIVE_LM_STUDIO"`.
2. **Verifica deterministica `140 subchunks -> 26 parent chunks`:** ricalcolando `aggregate_topic_discovery(chunk_results=subchunks_of_parent_p, parent_document_id=..., max_topics=8)` sui 140 subchunk salvati in `checkpoint_subchunks`, tutti i **26/26** parent chunk in `checkpoint_chunks` (`195` topic parent complessivi) coincidono al 100% con l'aggregazione deterministica dei rispettivi microchunk (`319` topic candidati microchunk).
3. **Riscontro dei 140 microchunk nei 261 file HTTP grezzi (`raw_completions_audit/completion_*.json`):**
   - **93 microchunk diretti** (`model='qwen2.5-7b-instruct'`, `serialization_format='compact_jsonl_v1'`):
     - **85** microchunk a chiamata singola (`retries=0`) hanno corrispondenza esatta 1-a-1 per `(prompt_tokens, completion_tokens)` e contenuto JSON nei file `completion_*.json`;
     - **8** microchunk con retry interno (`retries=1`, ovvero `(0,1), (0,2), (0,4), (4,0), (14,1), (18,3), (20,4), (24,3)`) hanno in `metadata` la somma esatta dei token (`prompt_tokens = p1 + p2`, `completion_tokens = c1 + c2`) di due chiamate HTTP consecutive registrate in `raw_completions_audit/` (es. subchunk `(0,1)`: `786+791=1577` prompt token, `132+159=291` completion token nei file `completion_0002` e `completion_0003`).
   - **47 microchunk bisecati adattivamente** (`aggregation=True`): ciascuno dei 47 microchunk è il consolidamento deterministico delle chiamate foglia generate dopo errore HTTP 400 GBNF (`Unexpected empty grammar stack`), tutte presenti e verificate nei file `completion_*.json` nell'intervallo `2026-10-27T17:37:25Z` – `2026-10-27T20:33:12Z`.

### 3.4 Riconciliazione esatta tra il contatore di `307` chiamate HTTP e i `261` file `completion_*.json` su disco
L'analisi del codice di `AuditingLmStudioClient.chat_completion` e dei numeri di sequenza `call_index` (`1..307`) nei file `completion_{call_index:04d}_{req_hash}.json` spiega senza ambiguità la differenza tra `307` e `261`:
- `AuditingLmStudioClient` incrementa `self.calls_count += 1` **prima** di invocare `self.inner.chat_completion(...)` e scrive il file `completion_{abs_call_index:04d}_{req_hash}.json` **solo se** la chiamata HTTP restituisce una risposta valida (senza sollevare eccezione).
- Nell'intervallo `call_index` `1..307`, esattamente **81** indici di chiamata hanno sollevato un'eccezione HTTP 400 (`Unexpected empty grammar stack` del campionatore GBNF di LM Studio) o timeout prima della scrittura del file, innescando la bisezione adattiva dei 47 microchunk:
  - `307` tentativi totali − `81` tentativi falliti pre-scrittura = **`226`** chiamate HTTP riuscite nella sequenza principale `1..307`.
- Inoltre, per **35** numeri di `call_index` (`[1, 6, 8, 9, 12, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 24, 28, 30, 32, 33, 35, 36, 40, 41, 43, 45, 46, 51, 52, 54, 62, 64, 176, 181, 182]`) sono presenti su disco **2 file** con `req_hash` distinto (provenienti dal gate test preliminare sui 3 chunk `0, 12, 25` e dal tentativo interrotto sul chunk `7` prima del resume della Sessione 2):
  - `226` indici unici riusciti + `35` file con `req_hash` distinto = **`261` file `completion_*.json` totali** su disco (`172` file della Sessione 1 + `89` file della Sessione 2 = `261` file; somma token sui 261 file: `180.248` prompt token, `38.795` completion token, `2.702,37 s` di latenza netta delle chiamate riuscite su `4.322,07 s` di wall-clock complessivo).

---

## 4. Verifica #2: Copertura Insiemistica e Multi-Insiemistica (Molteplicità = 1)

La verifica sui dati originali del documento autentico (`test_data/reference_chat_document.zip`) e sullo snapshot `final_campaign_26of26_readonly_copy.db` conferma:
- **Identità documento:** `document_id` verificato, `1.049` bundle, `1.049` sezioni d'evidenza testuale (`unified:whatsapp_txt:1::ORIGINAL_TEXT` .. `unified:whatsapp_txt:1049::ORIGINAL_TEXT`), `document_fingerprint = e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478`, `config_fingerprint = e1ca43d4e66c54e0acbb30106f06745928c61f307c0775c616c172e6bab20290`.
- **Partizione dei 26 parent chunk (`0..25`):**
  - Somma delle lunghezze delle liste `evidence_ids`: **`1.049`**
  - Cardinalità dell'unione insiemistica: **`1.049`** (`0` mancanti, `0` extra)
  - Molteplicità massima (multiset): **`1`** (`0` duplicati intra-chunk, `0` duplicati inter-chunk).
- **Partizione dei 140 microchunk (`checkpoint_subchunks`):**
  - Somma delle lunghezze delle liste `evidence_ids`: **`1.049`**
  - Cardinalità dell'unione insiemistica: **`1.049`** (`0` mancanti, `0` extra)
  - Molteplicità massima (multiset): **`1`** (`0` duplicati intra-subchunk, `0` duplicati inter-subchunk).
- **Validità delle citazioni (`evidence_ids`):**
  - Tutte le **638** citazioni dei 10 topic finali (**604** `evidence_id` unici su `1.049`, pari al `57,58%` del corpus citato direttamente; `34` evidenze condivise tra 2 o più topic) appartengono al documento e agli specifici microchunk e parent chunk da cui il topic è stato estratto (**0** ID inventati, **0** contaminazioni cross-chunk).

---

## 5. Verifica #3: Qualità dei 10 Topic Finali e Analisi delle Sovrapposizioni Lessicali

Con `max_topics = 10` e deduplicazione lessicale deterministica (`semantic_deduplication = False`), i `319` topic candidati microchunk (`195` post-aggregazione parent) sono stati consolidati nei seguenti 10 topic globali (nomi propri redatti con `[PARTECIPANTE_1]` e `[PARTECIPANTE_2]`):

| Rank | Etichetta Topic | Citazioni (`evidence_ids`) | Citazioni Esclusive | Citazioni Condivise (`%`) | Parent Chunk Coinvolti (`/26`) | Esempi di `evidence_id` Reali | Classificazione Critica |
| :---: | :--- | ---: | ---: | ---: | ---: | :--- | :--- |
| **1** | `incontro` | `335` | `327` | `8` (`2,4%`) | `26/26` | `unified:whatsapp_txt:1::ORIGINAL_TEXT`, `...:7::ORIGINAL_TEXT` | **Macro-tema distinto #1** (organizzazione appuntamenti e incontri quotidiani) |
| **2** | `incontro sportivo` | `110` | `102` | `8` (`7,3%`) | `19/26` | `unified:whatsapp_txt:9::ORIGINAL_TEXT`, `...:18::ORIGINAL_TEXT` | **Macro-tema distinto #2** (organizzazione uscite sportive congiunte) |
| **3** | `Attività sportiva` | `74` | `59` | `15` (`20,3%`) | `19/26` | `unified:whatsapp_txt:17::ORIGINAL_TEXT`, `...:30::ORIGINAL_TEXT` | **Variante lessicale / sfaccettatura del Macro-tema #2** (resoconto di allenamenti e gare) |
| **4** | `insegnamento` | `30` | `26` | `4` (`13,3%`) | `7/26` | `unified:whatsapp_txt:266::ORIGINAL_TEXT`, `...:267::ORIGINAL_TEXT` | **Macro-tema distinto #3** (lezioni, laboratorio, esami e didattica) |
| **5** | `orari` | `23` | `3` | `20` (`87,0%`) | `5/26` | `unified:whatsapp_txt:5::ORIGINAL_TEXT`, `...:6::ORIGINAL_TEXT` | **Sotto-sfaccettatura operativa** (coordinamento orario: `14/23` condivise con il cluster sport, `6/23` con `incontro`) |
| **6** | `informazione` | `15` | `13` | `2` (`13,3%`) | `5/26` | `unified:whatsapp_txt:28::ORIGINAL_TEXT`, `...:29::ORIGINAL_TEXT` | **Sotto-sfaccettatura generica** (scambio di informazioni pratiche puntuali) |
| **7** | `incontro allenamento` | `16` | `12` | `4` (`25,0%`) | `4/26` | `unified:whatsapp_txt:246::ORIGINAL_TEXT`, `...:247::ORIGINAL_TEXT` | **Variante lessicale del Macro-tema #2** (sinonimo di `incontro sportivo`) |
| **8** | `incontro per corsa` | `16` | `12` | `4` (`25,0%`) | `3/26` | `unified:whatsapp_txt:56::ORIGINAL_TEXT`, `...:57::ORIGINAL_TEXT` | **Variante lessicale del Macro-tema #2** (specificazione sulla corsa del cluster sportivo) |
| **9** | `informazioni viaggio` | `11` | `10` | `1` (`9,1%`) | `3/26` | `unified:whatsapp_txt:450::ORIGINAL_TEXT`, `...:451::ORIGINAL_TEXT` | **Macro-tema distinto #4** (spostamenti, partenze e logistica di viaggio) |
| **10** | `ritardo` | `8` | `3` | `5` (`62,5%`) | `3/26` | `unified:whatsapp_txt:72::ORIGINAL_TEXT`, `...:124::ORIGINAL_TEXT` | **Sotto-sfaccettatura operativa** (avvisi di ritardo agli appuntamenti: `5/8` condivise con incontro/sport) |

**Sintesi critica sulla qualità tematica:** l'aggregatore deterministico (`semantic_deduplication = False`) preserva la tracciabilità esatta delle etichette emesse dal modello locale senza fusioni euristiche opache, ma produce **4 macro-temi realmente distinti** (`1. Incontri/appuntamenti`, `2. Sport/corsa/allenamento`, `3. Insegnamento/didattica`, `4. Viaggi`), accompagnati da **3 varianti lessicali** del cluster sportivo (`incontro sportivo`, `Attività sportiva`, `incontro allenamento`, `incontro per corsa`, che insieme raccolgono `199` evidenze uniche) e **3 sotto-sfaccettature operative** (`orari`, `informazione`, `ritardo`).

---

## 6. Verifica #4, #5 e #6: Smoke Test UI Streamlit, Detection (3/26) e Scalabilità Sintetica (2.101 Messaggi)

1. **Smoke test pratico UI Streamlit (`output/ui_smoke_verification_20261027/ui_smoke_verification_summary.json`):**
   - Eseguito tramite `streamlit.testing.v1.AppTest` su `app.py` con il documento reale da `1.049` messaggi e copia isolata dello snapshot SQLite.
   - Verificati: controllo UI `Numero max topic = 10` (`range 1–25`), rendering di tutti i `10` topic e delle `638` citazioni, `SearchService.search_discoveries` con corrispondenza `10/10` tra `matched_sections` ed `evidence_ids`, ricaricamento da checkpoint con **0 chiamate HTTP**, ed export JSON (`ui_discovery_export_redacted.json`) con rifiuto fail-closed di `LIVE_AUTHORITATIVE` (`PROVENANCE_NOT_VERIFIED` / `UNATTESTED`) in assenza di provenienza live verificata.
2. **Detection indipendente e Prova del Nove (finestra campionaria di `3/26` chunk, `102/1.049` messaggi):**
   - Eseguita **LIVE** sui chunk parent `0, 12, 25` (`102` messaggi, `9,72%` del corpus):
     - Query positiva (`label='incontro'`): decisione **`PRESENT`** (`3/3` chunk positivi, `15` citazioni valide, `20` chiamate HTTP, `231,58 s`).
     - Query negativa di controllo (`label='mutuo bancario ipotecario e compravendita immobiliare notarile'`): decisione **`ABSENT`** nella finestra di `3/26` chunk (`0` citazioni, `20` chiamate HTTP, `150,70 s`). *Nota: non estrapolabile come `ABSENT` globale sull'intero corpus da 1.049 messaggi senza esecuzione sui restanti 23 chunk.*
     - Consistenza Discovery vs Detection nella finestra di `102` messaggi: **12 evidenze condivise** su `15` citate da Detection (**overlap `80,00%`** rispetto a Detection) e `47` citate da Discovery nella medesima finestra (**indice di Jaccard `12 / (47 + 15 - 12) = 24,00%`**).
3. **Scalabilità su corpus sintetico da `2.101` messaggi (`output/synthetic_scalability_phase3/scalability_summary.json`):**
   - Partizionamento deterministico verificato sull'intero corpus (`2.101` messaggi -> `59` parent chunk -> `264` microchunk, copertura `2.101/2.101`, `0` mancanti, `0` duplicati).
   - Simulazione deterministica end-to-end completata su **59/59** parent chunk (`264/264` microchunk) in `5,06 s` con `10` topic consolidati (`max_topics=10`).
   - Esecuzione **LIVE** su LM Studio completata su un campione rappresentativo di **3/59** parent chunk (chunk `0, 29, 58`, `108` messaggi, `15` microchunk) in `429,98 s` (`23` chiamate HTTP, `0` errori).
4. **Suite di regressione Pytest Windows (`.venv312`, Python `3.12.10`):**
   - Log completo salvato e verificato: **`2.160 passed, 0 failed, 6 skipped, 5 deselected`** (`exit code 0`), comprensivo del nuovo test unitario fail-closed `test_checkpoint_with_missing_origin_never_becomes_live_without_proof`.

