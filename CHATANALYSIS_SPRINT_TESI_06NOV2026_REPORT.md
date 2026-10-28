# ChatAnalysis — Sprint Operativo Tesi (Consegna Venerdì 6 Novembre 2026)
## Rapporto Tecnico Unificato: Topic Discovery LIVE 26/26, Audit Provenance, Detection Indipendente e Scalabilità

**Data di esecuzione e audit**: 27 Ottobre 2026  
**Scadenza consegna tesi**: Venerdì 6 Novembre 2026  
**Repository**: `C:\Users\Luca\Desktop\Tesi\ChatAnalysis`  
**Ambiente operativo**: Windows 11 (`10.0.26200`), Python `3.12.10` (`.venv312`), LM Studio `http://127.0.0.1:1234`, Modello `qwen2.5-7b-instruct` (`GGUF Q4_K_M`, `context_length = 8192`, `temperature = 0.0`, `seed = 42`, serializzazione operativa `compact_jsonl_v1`).

---

## 1. Verdetto Esplicito di Audit sulla Provenance e Tabella Sinottica (P0 – P4)

### 1.1 Verdetto Finale di Tracciabilità e Catena di Custodia (Audit 27/10/2026)
- **Payload d'inferenza (`26/26` parent chunk, `140/140` microchunk)**: **`LIVE_VERIFIED`**. Tutti i `140/140` microchunk (`93` diretti: `85` single-call + `8` retry a 2 chiamate con somma token esatta; `47` bisecati adattivamente) sono verificati contro i **`261` file di completamento HTTP grezzi** (`output/live_run_1049_26chunks/raw_completions_audit/completion_*.json`) generati da LM Studio (`qwen2.5-7b-instruct`), e tutti i `26/26` parent chunk coincidono al `100%` con l'aggregazione deterministica dei rispettivi microchunk. Zero record provenienti da `run_123` (`RECONSTRUCTED_UNVERIFIED`) o `MOCK_INSPECTION` sono confluiti nell'aggregato.
- **Colonna `origin` nel database SQLite (`checkpoints.db`)**: **`LIVE_PARTIALLY_VERIFIED`**. Il confronto tra lo snapshot read-only pre-Sessione 2 (`pre_run_14of26_readonly_copy.db`, `19:40:36`) e il database finale (`final_campaign_26of26_readonly_copy.db`, `20:33:12Z`) dimostra che **12/26** parent chunk (`[7, 15..25]`) e **57/140** microchunk (Sessione 2, `run_id='run_disc_8221618677'`) sono stati scritti nativamente da `LocalTopicAnalysisService` con `origin='LIVE_LM_STUDIO'`, mentre **14/26** parent chunk (`[0..6, 8..14]`) e `1` riga in `checkpoints` della Sessione 1 (`17:37Z–19:22Z`) avevano `origin=''`/`None` prima dell'`UPDATE` SQL dello script (e gli `83/140` subchunk della Sessione 1 in `checkpoint_subchunks` hanno tuttora `origin=None` in SQLite, ma sono riscontrati al `100%` nei file HTTP `completion_0001..0182`).
- **Correzione chirurgica fail-closed**: le due istruzioni `UPDATE` sono state rimosse da `scripts/run_canonical_live_discovery_26chunks.py` e `analysis/service.py` applica una guardia fail-closed validata da `test_checkpoint_with_missing_origin_never_becomes_live_without_proof`.

### 1.2 Tabella Sinottica delle Prove e dei Risultati

| Priorità / Fase | Obiettivo / Metrica Richiesta | Risultato Misurato | Stato | Evidenza Tecnica e Provenance |
| :--- | :--- | :--- | :--- | :--- |
| **Fase 0 — Snapshot Read-Only & Audit Provenance** | 7 snapshot SQLite consistenti (`Online Backup API`) fuori dal repo + `PRAGMA integrity_check` | `ok` su tutti i 7 DB; `1` riga `checkpoints` e `14` righe `checkpoint_chunks` identificate per l'`UPDATE`; `run_123` isolata | **VERIFICATO** | `C:\Users\Luca\Desktop\ChatAnalysis_Audit_Snapshots_20261027\` + `CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md` |
| **P0 — Topic Discovery LIVE (1.049 msg)** | Elaborazione integrale dei parent chunk originali della chat autentica | **26 / 26 Parent Chunk (`COMPLETE`)**, `0` falliti | **LIVE_VERIFIED** *(DB origin: 12 nativi + 14 rietichettati)* | `doc_fp = e78e13f0...`, `cfg_fp = e1ca43d4...`, verificato contro `261` file `completion_*.json` |
| **P0 — Copertura Insiemistica e Multiset** | Partizione esatta su `26` parent chunk e `140` microchunk | **140 / 140 Microchunk**, somma = `1.049`, unici = `1.049`, **molteplicità max = `1`** (`0` perdite, `0` duplicati) | **VERIFICATO** | `output/live_run_1049_26chunks/checkpoints/checkpoints.db` (`26` chunk + `140` subchunk salvati) |
| **P1 — Consolidamento (`max_topics = 10`)** | Riduzione da candidati grezzi a max 10 argomenti e analisi critica delle varianti | **319** grezzi $\rightarrow$ **195** parent $\rightarrow$ **10 topic finali** (**4 macro-temi distinti**, **3 varianti lessicali sportive**, **3 sotto-sfaccettature**) | **VERIFICATO** | `638` citazioni totali (`604` ID unici su `1.049`, `34` condivisi), **100% valide**, `0` ID inventati |
| **P1 — Smoke Test UI Streamlit, Reload & Export** | Test pratico `AppTest` su `app.py`, reload `0` HTTP, `SearchService` ed export JSON fail-closed | **0 chiamate HTTP** al reload, `Numero max topic = 10`, `10` topic renderizzati in UI, rifiuto fail-closed di `LIVE_AUTHORITATIVE` senza prova | **VERIFICATO** | `output/ui_smoke_verification_20261027/ui_smoke_verification_summary.json` |
| **P2 — Detection LIVE Positiva (finestra 3/26)** | Inferenza indipendente sul tema #1 (`'incontro'`) sui chunk `0, 12, 25` (`102/1.049` msg) | Decisione **`PRESENT`** (`3/3` chunk nella finestra `0, 12, 25`), **15 evidenze citate**, `0` allucinate (`231,58 s`, `20` chiamate HTTP) | **VERIFICATO** *(su finestra 3/26)* | `output/live_detection_phase2/detection_phase2_summary.json` |
| **P2 — Detection LIVE Negativa (finestra 3/26)** | Controllo negativo (`'mutuo bancario...'`) sui chunk `0, 12, 25` (`102/1.049` msg) | Decisione **`ABSENT` nella finestra `3/26`** (`0` evidenze, `150,70 s`, `20` chiamate HTTP) | **VERIFICATO** *(su finestra 3/26)* | Ambito circoscritto al `9,72%` dei messaggi (`102/1.049`); non estrapolato globalmente ai restanti 23 chunk |
| **P2 — Consistenza nella Finestra 3/26** | Confronto Discovery (`47` citazioni nella finestra) vs Detection (`15` citazioni) | **12 evidenze condivise**, Overlap/Detection **80,00%** (`12/15`), **Jaccard 24,00%** (`12/50`) | **VERIFICATO** *(su finestra 3/26)* | Intersezione esatta verificata su `12` `evidence_id` reali |
| **P3 — Scalabilità Sintetica (2.101 msg)** | Invarianti `59/59` + Simulazione mock `59/59` + Prova LIVE parziale `3/59` | Copertura **2.101 / 2.101** (`59` parent, `264` microchunk, molteplicità `1`); Sim **`59/59`** (`5,06 s`); Live **`3/59`** (`108` msg, `429,98 s`, `10` topic) | **VERIFICATO** | `output/synthetic_scalability_phase3/scalability_summary.json` |
| **P4 — Regressione & Rilascio ZIP** | Suite `pytest` completa + unico ZIP sanitizzato fuori dal repo | **2.160 passed, 0 failed, 6 skipped, 5 deselected** (`exit code 0`) | **COMPLETATO** | `C:\Users\Luca\Desktop\CHATANALYSIS_SPRINT_TESI_06NOV2026.zip` + `.sha256` |

---

## 2. Riconciliazione delle Chiamate HTTP (307 Tentativi vs 261 File Salvati) e Qualità dei 10 Topic

### 2.1 Contabilità Esatta delle Chiamate HTTP nella Campagna 26/26
- **Tentativi HTTP totali inviati a LM Studio (`call_index` `1..307`)**: **`307`**.
- **Tentativi HTTP 400 GBNF pre-bisezione (nessun file scritto)**: **`81`** chiamate hanno sollevato eccezione HTTP 400 (`Unexpected empty grammar stack`) prima del ritorno di una risposta, attivando la bisezione adattiva dei `47` microchunk densi (`307 - 81 = 226` indici di chiamata riusciti nella sequenza principale).
- **File con `call_index` condiviso e `req_hash` distinto (gate test preliminare sui 3 chunk `0, 12, 25` e tentativo interrotto sul chunk `7`)**: **`35`** file.
- **Totale file `completion_*.json` salvati su disco**: `226 + 35 =` **`261` file** (`172` nella Sessione 1 + `89` nella Sessione 2; `180.248` prompt token, `38.795` completion token, `2.702,37 s` latenza netta delle chiamate riuscite su `4.322,07 s` wall-clock complessivo).

### 2.2 Tabella Critica dei 10 Topic Consolidati (`max_topics = 10`, `semantic_deduplication = False`)

| # | Etichetta Topic (`label`) | Citazioni Totali | Citazioni Esclusive | Citazioni Condivise (`%`) | Parent Chunk (`/26`) | Classificazione Critica e Ruolo Semantico |
| :---: | :--- | ---: | ---: | ---: | ---: | :--- |
| 1 | **`incontro`** | **335** | `327` | `8` (`2,4%`) | `26/26` | **Macro-tema distinto #1**: organizzazione di incontri e appuntamenti quotidiani tra `[PARTECIPANTE_1]` e `[PARTECIPANTE_2]`. |
| 2 | **`incontro sportivo`** | **110** | `102` | `8` (`7,3%`) | `19/26` | **Macro-tema distinto #2**: pianificazione di uscite sportive congiunte. |
| 3 | **`Attività sportiva`** | **74** | `59` | `15` (`20,3%`) | `19/26` | **Variante lessicale del Macro-tema #2**: resoconto di allenamenti, tempi e stato fisico. |
| 4 | **`insegnamento`** | **30** | `26` | `4` (`13,3%`) | `7/26` | **Macro-tema distinto #3**: lezioni, attività di laboratorio, esami e didattica. |
| 5 | **`orari`** | **23** | `3` | `20` (`87,0%`) | `5/26` | **Sotto-sfaccettatura operativa**: coordinamento orario (`14/23` condivise con il cluster sport, `6/23` con `incontro`). |
| 6 | **`informazione`** | **15** | `13` | `2` (`13,3%`) | `5/26` | **Sotto-sfaccettatura operativa**: richieste e conferme informative puntuali. |
| 7 | **`incontro allenamento`** | **16** | `12` | `4` (`25,0%`) | `4/26` | **Variante lessicale del Macro-tema #2**: sinonimo di `incontro sportivo`. |
| 8 | **`incontro per corsa`** | **16** | `12` | `4` (`25,0%`) | `3/26` | **Variante lessicale del Macro-tema #2**: specificazione sulla corsa del cluster sportivo. |
| 9 | **`informazioni viaggio`** | **11** | `10` | `1` (`9,1%`) | `3/26` | **Macro-tema distinto #4**: organizzazione di partenze, bagagli e spostamenti. |
| 10 | **`ritardo`** | **8** | `3` | `5` (`62,5%`) | `3/26` | **Sotto-sfaccettatura operativa**: comunicazioni di ritardo agli appuntamenti (`5/8` condivise). |

---

## 3. Documenti di Audit e Capitoli di Tesi Generati

1. **Rapporto di Audit Finale sulla Provenance**: `CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md` (presente anche in `docs/CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md`).
2. **Capitoli di Tesi (Risultati Sperimentali, Validazione Forense e Discussione Critica, Sezioni A–G)**: `CHATANALYSIS_TESI_RISULTATI_E_DISCUSSIONE_20261027.md` (presente anche in `docs/CHATANALYSIS_TESI_RISULTATI_E_DISCUSSIONE_20261027.md`).
3. **Riepilogo Smoke Test UI Streamlit ed Export Redatto**: `output/ui_smoke_verification_20261027/ui_smoke_verification_summary.json` e `output/ui_smoke_verification_20261027/ui_discovery_export_redacted.json`.
