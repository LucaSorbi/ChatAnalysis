# CAPITOLI DI TESI: RISULTATI SPERIMENTALI, VALIDAZIONE FORENSE E DISCUSSIONE CRITICA

**Progetto:** ChatAnalysis — Piattaforma Forense Locale per l'Analisi Tematica e la Ricerca su Conversazioni Messaggistiche  
**Data di consolidamento:** 27 ottobre 2026 (Consegna tesi: venerdì 6 novembre 2026)  
**Nota di riservatezza:** Tutti gli esempi e le descrizioni tematiche riportati nel presente testo sono stati sanitizzati sostituendo i nomi propri dei partecipanti con i segnaposto `[PARTECIPANTE_1]` e `[PARTECIPANTE_2]` e omettendo qualsiasi testo integrale dei messaggi privati.

---

## Sezione A — Architettura Operativa di ChatAnalysis

### A.1 Pipeline di Ingestione Forense e Modello delle Evidenze
L'architettura di ChatAnalysis è progettata secondo il principio di immutabilità profonda (*deep structural immutability*) e tracciabilità deterministica delle fonti probatorie (*chain of custody*). Il flusso di ingestione converte un archivio di esportazione messaggistica (quale un archivio `.zip` WhatsApp contenente il trascritto testuale e gli allegati multimediali) in una sequenza ordinata di `RawRecord`, sottoposti a validazione formale (`ValidationResult`) e normalizzazione temporale (`NormalizedRecord` con stato esplicito del fuso orario `TimestampTzStatus`). Ciascun messaggio normalizzato viene incapsulato in un `UnifiedMessage` e associato a un `MessageEvidenceBundle`, il quale espone una o più sezioni di evidenza testuale (`TextEvidenceSection`) dotate di identificativo univoco globale (`evidence_id`, ad esempio `unified:whatsapp_txt:1::ORIGINAL_TEXT`). L'intero documento conversazionale (`ConversationEvidenceDocument`) è sigillato crittograficamente mediante un digest SHA-256 (`document_fingerprint`) calcolato sull'ordinamento, sui timestamp, sui mittenti, sugli identificativi e sul testo integrale di tutte le sezioni d'evidenza.

### A.2 Doppia Modalità di Serializzazione: Scientifica Congelata vs Operativa (`compact_jsonl_v1`)
Per coniugare la riproducibilità dei benchmark scientifici storici con la stabilità dell'inferenza locale su conversazioni reali estese, il sottosistema di serializzazione (`ai/serializer.py`) mantiene due percorsi rigorosamente separati:
1. **Serializzazione scientifica storica (invariata e congelata):** impiegata dal framework di benchmark (`ai/benchmark.py`, `ai/experiment.py`) e dalle 9 esecuzioni storiche congelate, preserva il formato verboso multilinea con intestazioni complete per ciascuna sezione probatoria.
2. **Serializzazione operativa `compact_jsonl_v1`:** introdotta per l'esecuzione operativa locale (`operational_mode=True`) su LM Studio, rappresenta ogni sezione d'evidenza come un record JSON compatto su singola riga `{"id": "E001", "msg_id": "...", "sender": "...", "text": "..."}` accompagnato da una mappa di traduzione bidirezionale deterministica (`alias_to_evidence_id` / `evidence_id_to_alias`) tra gli alias brevi (`E001`, `E002`, ...) e gli `evidence_id` forensi originali. Tale rappresentazione riduce di oltre il `55%` l'ingombro di token del prompt e annulla la probabilità di errori di trascrizione sintattica degli identificatori da parte del modello generativo, mantenendo intatta la verifica post-inferenza sull'insieme esatto degli `evidence_id` ammessi per lo specifico blocco.

### A.3 Microchunking Preventivo e Bisezione Adattiva Ricorsiva
Nel campionamento vincolato tramite grammatiche formali GBNF (*Grammar-Based Normalization Format*) impiegato da LM Studio (`llama.cpp` backend) per forzare l'output JSON conforme allo schema, l'elaborazione di blocchi contenenti decine di messaggi densi determinava su modelli quantizzati a 7 miliardi di parametri due modalità critiche di guasto:
- l'eccezione HTTP 400 del campionatore grammaticale (`Unexpected empty grammar stack after accepting piece: G (38)`);
- la degenerazione ripetitiva del token `G` (`"GGGG..."`) fino all'esaurimento del budget di output (`finish_reason = length`).

Per risolvere strutturalmente tale fenomeno senza alterare il partizionamento dei blocchi padre (`parent chunks`), ChatAnalysis adotta una strategia gerarchica a due livelli:
1. **Microchunking preventivo (`split_chunk_into_microchunks` in `ai/chunking.py`):** ogni blocco padre che contenga più di `max_substantive_messages = 8` messaggi sostanziali viene preventivamente suddiviso in sotto-blocchi contigui (*microchunks*) di al più 8 messaggi sostanziali, preservando gli identificativi `{chunk_id}::sub::{i}` e la partizione esatta delle sezioni d'evidenza.
2. **Bisezione adattiva ricorsiva (`_execute_microchunk_with_adaptive_fallback` in `analysis/service.py`):** qualora un singolo microchunk incontri un errore HTTP 400 GBNF, un superamento della finestra di contesto o un troncamento dell'output JSON, il servizio dimezza ricorsivamente il microchunk fino a una profondità massima `max_depth = 3` (arrivando, se necessario, al singolo messaggio). I risultati validati delle foglie vengono consolidati deterministicamente nel microchunk padre e da questo nel blocco padre (`0..25`).

### A.4 Validazione Strutturata Fail-Closed, Persistenza SQLite e UI Streamlit
Ogni risposta restituita dal modello locale viene sottoposta in `ai/structured.py` a validazione JSON, verifica dello schema formale (`TOPIC_DISCOVERY_SCHEMA` / `TOPIC_DETECTION_SCHEMA`), traduzione inversa degli alias `E001...` e controllo insiemistico rigoroso (`set(topic.evidence_ids).issubset(allowed_chunk_evidence_ids)`). Qualsiasi citazione inesistente o esterna al blocco in esame solleva `AiInvalidEvidenceCitationError` (politica *fail-closed*). I sotto-blocchi e i blocchi padre superati vengono persistiti atomicamente in modalità incrementale $O(1)$ nel database SQLite locale (`analysis/checkpoint.py`, tabelle `checkpoints`, `checkpoint_chunks`, `checkpoint_subchunks` e `checkpoint_chunk_diagnostics`), consentendo alla UI Streamlit (`app.py`, `ui/presentation.py`, `ui/application.py`) il ripristino immediato post-riavvio a zero chiamate HTTP, la ricerca testuale e tematica (`SearchService`) e l'esportazione JSON con attestazione verificata della provenienza (`analysis/export.py`).

---

## Sezione B — Metodologia Sperimentale e Provenance

### B.1 Configurazione Hardware, Software e Parametri Operativi
Gli esperimenti sono stati condotti sull'ambiente locale Windows di riferimento con i seguenti parametri registrati e verificati:
- **Sistema Operativo e Runtime:** Windows 11 (`10.0.26200`), Python `3.12.10` in ambiente virtuale isolato `.venv312`.
- **Server d'Inferenza Locale:** LM Studio su endpoint locale `http://127.0.0.1:1234` (`parallel_slots = 1`, esecuzione sequenziale).
- **Modello Linguistico:** `qwen2.5-7b-instruct` (formato GGUF, quantizzazione `Q4_K_M`).
- **Parametri Operativi e Fingerprint:** `context_length = 8192`, `temperature = 0.0`, `seed = 42`, `timeout_seconds = 240.0`, `max_topics = 10` (target per singolo blocco padre: `min(max_topics, 8) = 8`), `serialization_format = compact_jsonl_v1`, `prompt_version = discovery_operational` (`topic_discovery_operational_v2`).
- **Identità Crittografica del Corpus Autentico:** archivio `test_data/reference_chat_document.zip` (`1.049` messaggi, `1.049` sezioni d'evidenza), `document_fingerprint = e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478`, `config_fingerprint = e1ca43d4e66c54e0acbb30106f06745928c61f307c0775c616c172e6bab20290`.

### B.2 Distinzione tra Verificabilità nel Pacchetto Distribuito e Verifica Locale su Windows
Per ragioni di tutela dei dati personali, il pacchetto `.zip` distribuito per revisione esterna non include né l'archivio della conversazione privata (`reference_chat_document.zip`), né i database SQLite dei checkpoint (`checkpoints.db`), né i `261` file JSON contenenti i prompt e le risposte grezze di LM Studio (`raw_completions_audit/completion_*.json`). Pertanto:
- **Un revisore esterno che ispezioni il solo pacchetto sanitizzato** può verificare staticamente l'architettura del codice, la compilazione dei moduli, i report JSON aggregati e sanitizzati (`live_26chunks_summary.json`, `detection_phase2_summary.json`, `scalability_summary.json`, `ui_smoke_verification_summary.json`) e l'esecuzione della suite di test unitari.
- **La verifica forense sull'autenticità delle inferenze LIVE** è stata eseguita direttamente nell'ambiente Windows locale confrontando gli snapshot read-only dei database SQLite (realizzati tramite `sqlite3` Online Backup API in `C:\Users\Luca\Desktop\ChatAnalysis_Audit_Snapshots_20261027\`) con i `261` file di completamento HTTP grezzi registrati durante l'esecuzione.

### B.3 Audit della Catena di Custodia e Risoluzione della Criticità di Provenance
Durante la revisione della tracciabilità sperimentale è stata individuata ed esaminata una criticità metodologica nello script di orchestrazione `scripts/run_canonical_live_discovery_26chunks.py` (righe 193–208 nella versione antecedente all'audit), il quale eseguiva all'avvio due istruzioni SQL:
1. `UPDATE checkpoints SET origin = 'LIVE_LM_STUDIO' WHERE origin = '' OR origin IS NULL`
2. un ciclo `UPDATE checkpoint_chunks SET serialized_result = ?` che inseriva `"origin": "LIVE_LM_STUDIO"` nei metadati JSON dei blocchi padre qualora assente.

Un'istruzione `UPDATE` retroattiva sul database non costituisce prova d'inferenza e rappresenta una vulnerabilità potenziale nella catena di custodia. Per accertare l'impatto effettivo di tali istruzioni sulla campagna del 27 ottobre 2026 senza alterare i file originali, è stato eseguito un confronto riga per riga tra lo snapshot pre-run `live_run_14of26_snapshot_20261027_194036.db` (acquisito alle `19:40:36` tra la Sessione 1 e la Sessione 2) e il database finale `output/live_run_1049_26chunks/checkpoints/checkpoints.db`:
- **Causa dell'`origin` vuota nella Sessione 1 (`17:37:25Z` – `19:22:45Z`):** nella Sessione 1, che ha elaborato i primi **14 blocchi padre** (`[0..6, 8..14]`) e i primi **83 microchunk**, il metodo `_process_subchunks_discovery` non propagava ancora il parametro `origin` alla chiamata `chk_store.save_chunk(...)`. Di conseguenza, lo snapshot `pre_run_14of26` conteneva `1` riga in `checkpoints` con `origin=''`, `14` righe in `checkpoint_chunks` con `metadata.origin=None` e `83` righe in `checkpoint_subchunks` con `metadata.origin=None`.
- **Effetto esatto dell'`UPDATE` all'avvio della Sessione 2 (`19:51Z`):** lo script ha aggiornato il campo `origin` a `'LIVE_LM_STUDIO'` su **1 riga** in `checkpoints` e su **14 righe** in `checkpoint_chunks` (`[0..6, 8..14]`), lasciando invariati al 100% i topic, le descrizioni, gli `evidence_ids`, i timestamp `created_at` e il campo `run_id=None`, e non toccando alcuna riga in `checkpoint_subchunks` (`0` righe modificate su `83`). I restanti **12 blocchi padre** (`[7, 15..25]`) e **57 microchunk** della Sessione 2 (`19:51Z` – `20:33:12Z`) sono stati invece scritti nativamente da `LocalTopicAnalysisService` con `origin='LIVE_LM_STUDIO'` e `run_id='run_disc_8221618677'`.
- **Riscontro indipendente sui 261 file HTTP grezzi:** la verifica incrociata tra i `140` microchunk in `checkpoint_subchunks` (inclusi gli `83` della Sessione 1 privi di `origin` in SQLite) e i `261` file `completion_*.json` in `output/live_run_1049_26chunks/raw_completions_audit/` ha dimostrato che **140/140 microchunk** e **26/26 blocchi padre** derivano integralmente da risposte HTTP 200 reali restituite da LM Studio (`qwen2.5-7b-instruct`), con **zero** contaminazioni da run storiche ricostruite (`run_123`, `RECONSTRUCTED_UNVERIFIED`) o simulate (`MOCK_INSPECTION`).
- **Correzione chirurgica fail-closed nel codice:** le due istruzioni `UPDATE` sono state rimosse da `scripts/run_canonical_live_discovery_26chunks.py` e in `analysis/service.py` è stata introdotta una guardia *fail-closed* che impedisce a qualsiasi checkpoint o blocco recuperato privo di `origin` verificata di essere promosso a `LIVE_LM_STUDIO` o `LIVE_AUTHORITATIVE` (validata dal test unitario `test_checkpoint_with_missing_origin_never_becomes_live_without_proof`).

---

## Sezione C — Risultati Topic Discovery su Chat Reale da 1.049 Messaggi

### C.1 Copertura Insiemistica e Multi-Insiemistica (26/26 Parent Chunk, 140/140 Microchunk)
L'esecuzione completa della Topic Discovery sul documento autentico da `1.049` messaggi ha raggiunto il completamento integrale di tutti i blocchi pianificati:

| Metrica di Copertura e Partizionamento | Valore Verificato | Riscontro / Invariante |
| :--- | ---: | :--- |
| Messaggi totali (`bundles`) / Sezioni d'evidenza (`all_evidence_sections`) | `1.049` / `1.049` | `100,00%` del documento ingerito |
| Blocchi padre pianificati e completati (`checkpoint_chunks`) | `26 / 26` | Indici `0..25` tutti presenti con stato `SUCCESS` (`0` falliti) |
| Microchunk totali pianificati e completati (`checkpoint_subchunks`) | `140 / 140` | `93` diretti (`85` single-call + `8` con 1 retry) + `47` bisecati adattivamente |
| Copertura insiemistica sui 26 blocchi padre ($\lvert \bigcup_{p=0}^{25} E_p \rvert$) | `1.049 / 1.049` | `0` sezioni mancanti, `0` sezioni extra |
| Molteplicità massima multi-insiemistica sui 26 blocchi padre ($\sum_{p=0}^{25} \lvert E_p \rvert$) | `1.049` (molteplicità = `1`) | `0` duplicati intra-chunk, `0` duplicati inter-chunk |
| Copertura insiemistica e multi-insiemistica sui 140 microchunk ($\sum_{s=1}^{140} \lvert E_s \rvert$) | `1.049` (molteplicità = `1`) | Partizione esatta disgiunta: ogni sezione compare esattamente una volta |
| Evidenze citate nei 10 topic consolidati finali (totale citazioni / ID unici) | `638` / `604` | `57,58%` del corpus citato direttamente (`34` ID condivisi tra $\ge 2$ topic, `0` ID inventati) |

### C.2 Contabilità delle Chiamate HTTP, Token e Tempi d'Inferenza
La riconciliazione puntuale tra il contatore del wrapper di audit (`307` tentativi HTTP) e i file salvati in `raw_completions_audit/` (`261` file `completion_*.json`) documenta il comportamento esatto del meccanismo di bisezione adattiva:
- **Tentativi HTTP totali nella sequenza `1..307`:** `307` chiamate inviate a LM Studio (`http://127.0.0.1:1234/v1/chat/completions`).
- **Errori HTTP 400 GBNF assorbiti dalla bisezione adattiva:** `81` chiamate hanno sollevato un errore HTTP 400 (`Unexpected empty grammar stack`) prima del ritorno di una risposta valida, attivando la suddivisione ricorsiva dei `47` microchunk densi senza perdere alcuna sezione d'evidenza.
- **Chiamate HTTP 200 riuscite e salvate su disco:** `307 - 81 = 226` chiamate riuscite nella sequenza principale, a cui si sommano `35` file `completion_*.json` con indice condiviso ma `req_hash` distinto prodotti durante il gate test preliminare sui 3 chunk `0, 12, 25` e il tentativo interrotto sul chunk `7`, per un totale di **`261` file `completion_*.json` salvati su disco** (`172` nella Sessione 1 e `89` nella Sessione 2).
- **Consumo di token e latenza:** attraverso i `261` completamenti registrati sono stati elaborati complessivamente **`180.248` prompt token** e generati **`38.795` completion token** (`219.043` token totali), con una latenza netta delle chiamate riuscite pari a **`2.702,37 s`** (`45,04 min`, media `10,35 s/chiamata`) e un tempo complessivo wall-clock (comprensivo dei tentativi HTTP 400 pre-bisezione) pari a **`4.322,07 s`** (`72,03 min`).
- **Ricaricamento da checkpoint (`CHECKPOINT_RESTORE`):** una volta completati i `26/26` blocchi padre, il ricaricamento del documento tramite `load_persisted_discovery_result` e nella UI Streamlit richiede **`0` chiamate HTTP** e meno di `0,15 s`.

### C.3 Consolidamento Gerarchico (`319 -> 195 -> 10`) e Analisi della Qualità dei 10 Topic Finali
Dai `140` microchunk sono emersi **`319` topic candidati grezzi**, aggregati a livello dei `26` blocchi padre in **`195` topic intermedi** e infine consolidati nei **`10` topic globali** richiesti dal parametro utente `max_topics = 10` (`aggregate_topic_discovery(..., max_topics=10, semantic_deduplication=False)`):

| Rank | Etichetta Topic | Citazioni Totali | Citazioni Esclusive | Citazioni Condivise (`%`) | Blocchi Padre (`/26`) | Sintesi Critica (Sanitizzata) e Ruolo Semantico |
| :---: | :--- | ---: | ---: | ---: | ---: | :--- |
| **1** | `incontro` | `335` | `327` | `8` (`2,4%`) | `26/26` | **Macro-tema #1 (Incontri e appuntamenti):** organizzazione di incontri quotidiani tra `[PARTECIPANTE_1]` e `[PARTECIPANTE_2]`. |
| **2** | `incontro sportivo` | `110` | `102` | `8` (`7,3%`) | `19/26` | **Macro-tema #2 (Sport e corsa — nucleo principale):** pianificazione di sessioni sportive congiunte. |
| **3** | `Attività sportiva` | `74` | `59` | `15` (`20,3%`) | `19/26` | **Variante lessicale del Macro-tema #2:** resoconti di allenamenti, tempi di percorrenza e stato fisico. |
| **4** | `insegnamento` | `30` | `26` | `4` (`13,3%`) | `7/26` | **Macro-tema #3 (Didattica e studio):** attività di laboratorio, lezioni, esami e impegni accademici. |
| **5** | `orari` | `23` | `3` | `20` (`87,0%`) | `5/26` | **Sotto-sfaccettatura operativa:** concordamento di orari (`14/23` condivise con il cluster sportivo, `6/23` con `incontro`). |
| **6** | `informazione` | `15` | `13` | `2` (`13,3%`) | `5/26` | **Sotto-sfaccettatura operativa:** richieste e conferme informative rapide. |
| **7** | `incontro allenamento` | `16` | `12` | `4` (`25,0%`) | `4/26` | **Variante lessicale del Macro-tema #2:** sinonimo specifico di `incontro sportivo`. |
| **8** | `incontro per corsa` | `16` | `12` | `4` (`25,0%`) | `3/26` | **Variante lessicale del Macro-tema #2:** specificazione lessicale focalizzata sulla corsa. |
| **9** | `informazioni viaggio` | `11` | `10` | `1` (`9,1%`) | `3/26` | **Macro-tema #4 (Viaggi e spostamenti):** organizzazione di partenze, bagagli e spostamenti. |
| **10** | `ritardo` | `8` | `3` | `5` (`62,5%`) | `3/26` | **Sotto-sfaccettatura operativa:** comunicazioni di ritardo rispetto agli appuntamenti (`5/8` condivise). |

---

## Sezione D — Verifica tramite Topic Detection e Consistenza (Finestra Campionaria 3/26 Chunk)

### D.1 Disegno Sperimentale e Ambito della Finestra (`102 / 1.049` Messaggi)
Per verificare l'indipendenza tra il flusso di *Open Topic Discovery* e il flusso di *Targeted Topic Detection* ("Prova del Nove") utilizzando lo stesso modello locale `qwen2.5-7b-instruct` e la serializzazione `compact_jsonl_v1`, è stata eseguita una campagna di Detection **LIVE** su una finestra campionaria costituita da **3 blocchi padre su 26** (chunk `0`, chunk `12` e chunk `25`, corrispondenti all'inizio, al centro e alla fine della conversazione, per un totale di **`102` messaggi su `1.049`**, pari al **`9,72%`** del corpus).

> **Avvertenza metodologica:** poiché il test di Detection è stato eseguito su `3/26` blocchi padre (`102` messaggi), i risultati dimostrano il funzionamento end-to-end del classificatore mirato e la coerenza locale con la Discovery all'interno della finestra esaminata, ma non costituiscono una misura di precisione/richiamo sull'intero documento né consentono di dichiarare un tema globalmente `ABSENT` sui restanti `23` blocchi non sottoposti a Detection.

### D.2 Risultati della Detection Positiva, Controllo Negativo e Consistenza nella Finestra

| Prova Sperimentale (Finestra Chunk `0, 12, 25` — `102` msg) | Query Tematica (`TopicQuery`) | Decisione Aggregata | Chunk Positivi | Citazioni (`evidence_ids`) | Chiamate HTTP | Latenza (`s`) | Token (Prompt / Completion) |
| :--- | :--- | :---: | :---: | ---: | ---: | ---: | ---: |
| **Detection Positiva (Prova del Nove)** | `label="incontro"` (Organizzazione di incontri, appuntamenti, orari e luoghi di ritrovo) | **`PRESENT`** | `3 / 3` | `15` | `20` | `231,58` | `13.489` / `1.703` |
| **Controllo Negativo (nella finestra 3/26)** | `label="mutuo bancario ipotecario e compravendita immobiliare notarile"` | **`ABSENT`** *(su 3/26)* | `0 / 3` | `0` | `20` | `150,70` | `13.669` / `1.229` |

All'interno della finestra di `102` messaggi (chunk `0, 12, 25`):
- La **Discovery** aveva associato al tema `incontro` **`47` sezioni d'evidenza**;
- La **Detection** indipendente ha selezionato **`15` sezioni d'evidenza** (`0` ID inventati);
- L'intersezione esatta tra i due insiemi è pari a **`12` sezioni d'evidenza condivise** (`unified:whatsapp_txt:1::ORIGINAL_TEXT`, `...:7`, `...:485`, `...:507`, `...:515`, `...:1015`, `...:1017`, `...:1021`, `...:1022`, `...:1032`, `...:1037`, `...:1048`), corrispondenti a un **overlap rispetto alla Detection dell'`80,00%` (`12/15`)** e a un **indice di Jaccard pari al `24,00%` (`12 / (47 + 15 - 12) = 12 / 50`)**. La differenza di cardinalità (`15` vs `47`) riflette la maggiore selettività del prompt di Detection mirata rispetto all'aggregazione ampia della Discovery.

---

## Sezione E — Analisi di Scalabilità su Conversazione Sintetica da 2.101 Messaggi

Per valutare il comportamento strutturale del partizionamento gerarchico, del database SQLite dei checkpoint e dell'aggregatore `max_topics = 10` oltre la soglia dei 2.000 messaggi, è stato generato in modo deterministico (`seed = 42`) un corpus conversazionale sintetico da **`2.101` messaggi** (`output/synthetic_scalability_phase3/scalability_summary.json`):

| Fase di Verifica su Corpus Sintetico (`2.101` messaggi) | Ambito Coperto | Blocchi Padre | Microchunk | Chiamate LLM / HTTP | Durata (`s`) | Esito e Invarianti Verificati |
| :--- | :--- | ---: | ---: | ---: | ---: | :--- |
| **1. Partizionamento e Invarianti di Copertura** | **Intero corpus (`2.101` msg)** | `59 / 59` | `264 / 264` | `0` | `< 0,20` | Copertura insiemistica e multiset `2.101 / 2.101` (molteplicità = `1`, `0` mancanti, `0` duplicati). |
| **2. Simulazione End-to-End con Client Mock Deterministico** | **Intero corpus (`2.101` msg)** | `59 / 59` | `264 / 264` | `264` (mock) | `5,06` | `264` salvataggi incrementali in `checkpoint_subchunks`, `59` in `checkpoint_chunks`, consolidamento in `10` topic (`max_topics=10`). |
| **3. Campione Rappresentativo LIVE su LM Studio (`qwen2.5-7b-instruct`)** | **Campione `3/59` chunk (`108` msg: chunk `0, 29, 58`)** | `3 / 59` | `15 / 264` | `23` (HTTP LIVE) | `429,98` | `3/3` chunk completati LIVE (`0` errori), `30` candidati -> `21` parent -> `10` topic estratti, `72` citazioni valide (`13.848` prompt tok, `2.707` completion tok). |

**Proiezione di scalabilità LIVE:** sulla base della latenza media osservata sul campione LIVE di `3/59` chunk (`143,33 s` per blocco padre / `28,67 s` per microchunk su `qwen2.5-7b-instruct` `Q4_K_M`), l'elaborazione LIVE integrale dei `59` blocchi padre (`264` microchunk) richiede un tempo stimato di circa **`7.560 s` – `8.450 s` (`~126 – 141 minuti`)**, interamente frazionabile su più sessioni grazie al checkpointing incrementale SQLite a livello di singolo microchunk.

---

## Sezione F — Discussione Critica e Limiti

1. **Risoluzione del collasso GBNF tramite `compact_jsonl_v1` e microchunking adattivo:** l'evidenza sperimentale più rilevante del lavoro consiste nella dimostrazione che il fallimento `0` topic / `"GGGG..."` su conversazioni lunghe non dipendeva da un'incapacità semantica del modello `qwen2.5-7b-instruct`, bensì dall'interazione tra prompt multi-messaggio estesi e lo stack grammaticale GBNF di LM Studio. La combinazione di alias brevi (`E001...`), microchunking preventivo a 8 messaggi sostanziali e bisezione adattiva ha assorbito tutti gli `81` errori HTTP 400 GBNF verificatisi durante la campagna da `1.049` messaggi, portando la copertura effettiva da parziale/degenerata al **`100%` (`26/26` parent chunk, `140/140` microchunk)**.
2. **Compromesso tra determinismo forense e ridondanza lessicale (`semantic_deduplication = False`):** l'aggregatore tematico privilegia il determinismo e l'assenza di fusioni euristiche opache. Come mostrato nella Tabella C.3, ciò comporta che su `10` posizioni disponibili (`max_topics = 10`) emergano **4 macro-temi realmente distinti** (`incontro`, cluster sportivo, `insegnamento`, `informazioni viaggio`), mentre **3 posizioni** sono occupate da varianti lessicali dello stesso macro-tema sportivo (`incontro sportivo`, `Attività sportiva`, `incontro allenamento`, `incontro per corsa`) e **3 posizioni** da sotto-sfaccettature operative (`orari`, `informazione`, `ritardo`) con elevata sovrapposizione di evidenze (`87,0%` per `orari`, `62,5%` per `ritardo`). In ambito forense tale comportamento è preferibile a una fusione silenziosa errata, ma riduce la diversità tematica della top-10.
3. **Analisi critica della provenance e necessità di guardie fail-closed:** l'audit sul database della campagna ha evidenziato come un difetto di propagazione del metadato `origin` nella Sessione 1 avesse indotto all'inserimento nello script di lancio di un `UPDATE` SQL retroattivo sulle 14 righe parent preesistenti. Sebbene la verifica incrociata con i `261` file HTTP grezzi `completion_*.json` abbia confermato l'autenticità LIVE di tutti i `140/140` microchunk, sul piano metodologico la promozione retroattiva via SQL viola la separazione tra dato osservato e metadato attestato. La rimozione dell'`UPDATE` e l'introduzione della guardia fail-closed in `LocalTopicAnalysisService` garantiscono che nessuna esecuzione futura possa ottenere `LIVE_LM_STUDIO` o `LIVE_AUTHORITATIVE` in presenza di blocchi recuperati privi di provenienza nativa.
4. **Limiti di copertura della Detection LIVE (`3/26`) e della Scalabilità LIVE (`3/59`):** mentre la Topic Discovery sulla chat reale da `1.049` messaggi è stata eseguita e verificata integralmente (`26/26` chunk, `100%`), sia la Detection LIVE (positiva e negativa) sia la prova LIVE sul corpus sintetico da `2.101` messaggi sono state condotte su finestre campionarie di `3` blocchi padre (`102/1.049` e `108/2.101` messaggi rispettivamente) per ragioni di budget computazionale locale (`~72 min` per 26 chunk).

---

## Sezione G — Conclusioni e Sviluppi Futuri

Il lavoro svolto ha dimostrato la fattibilità ingegneristica e il rigore probatorio di una piattaforma locale *offline-first* per l'analisi tematica forense di conversazioni reali su hardware consumer tramite modelli open-weight quantizzati (`qwen2.5-7b-instruct`). In particolare:
1. La **Topic Discovery LIVE** sulla conversazione reale da **`1.049` messaggi** ha completato **`26/26` blocchi padre e `140/140` microchunk** con copertura insiemistica e multi-insiemistica pari al **`100%`** (molteplicità = `1`), **`0` ID inventati**, **`638` citazioni verificate** e ricaricamento istantaneo da checkpoint SQLite a **`0` chiamate HTTP**.
2. La **catena di custodia** è stata integralmente verificata su snapshot SQLite read-only e sui **`261` file di completamento HTTP grezzi**, isolando la storica `run_123` (`RECONSTRUCTED_UNVERIFIED`) e blindando il codice con guardie fail-closed validate da **`2.160` test automatici superati (`0` falliti)**.
3. Gli **sviluppi futuri** includono: *(i)* l'introduzione di un livello opzionale di raggruppamento semantico post-aggregazione che accorpi le varianti lessicali (es. cluster sportivo) preservando in un albero ispezionabile le etichette originali di ciascun microchunk; *(ii)* l'estensione della Detection LIVE sull'intero corpus `26/26` mediante pre-filtro lessicale/embedding locale con verifica LLM; *(iii)* il supporto a batching controllato multi-slot per dimezzare i tempi di inferenza su conversazioni superiori a `2.000` messaggi.

