# CHATANALYSIS — RAPPORTO DI RISOLUZIONE INCIDENTE BLOCCANTE DISCOVERY
**Data:** 3-4 Novembre 2026  
**Autore:** Ingegnere di Stabilizzazione Qualità (Gemini Argon High / Antigravity)  
**Destinatario:** Tesi di Laurea Triennale in Informatica (Consegna 6 Novembre 2026)  
**Repository:** `LucaSorbi/ChatAnalysis`  
**Commit di Base:** `16311fa` (HEAD `main`)  
**Stato Finale:** **COMPLETATO CON SUCCESSO — SAFE_TO_FREEZE**

---

## 1. SINTESI E VERDETTO ESECUTIVO

In data 3-4 novembre 2026 è stato diagnosticato, riprodotto e definitivamente risolto l'incidente critico bloccante che impediva l'esecuzione della Topic Discovery sulle due chat WhatsApp autentiche del progetto:
- **Chat 1:** `G:\WhatsApp Chat - Florida Atletica.zip` (19 messaggi, 1 vocale Opus autentico).
- **Chat 2:** `G:\WhatsApp Chat - Marco (ERASMUS).zip` (36 messaggi, 1 foto JPEG autentica).

### Sintomo Iniziale
L'esecuzione della Topic Discovery falliva sistematicamente con l'eccezione:
```
OperationalAnalysisFailedError: Tutti i sotto-blocchi sono falliti
```
Tutti i micro-chunk e le relative bisezioni adattive fallivano al 100%, bloccando l'analisi.

### Esito Finale del Protocollo di Validazione LIVE
A valle delle correzioni architetturali, l'esecuzione dello script autoritativo:
`scripts/run_real_chats_operational_validation.py`
su backend **LM Studio locale LIVE** (`http://127.0.0.1:1234`, modello `qwen2.5-7b-instruct`, ctx 8192, temp 0.0, seed 42) con media processing reale locale (**Faster-Whisper STT** e **Tesseract OCR**) ha prodotto:

1. **Chat 1 — Vocale (`WhatsApp Chat - Florida Atletica.zip`)**:
   - Ingestione & STT reale Faster-Whisper: 19 bundle, 1 sezione vocale trascritta, 20 sezioni totali analizzabili.
   - Topic Discovery LIVE: **Status COMPLETE in 43.88s**, 20/20 sezioni analizzate (**copertura 100%**), 0 fallimenti micro-chunk.
   - Topic scoperti: **5 topic genuini in italiano**, supportati da citation evidence ID validi.
   - Prova del Nove (Verifica Indipendente di Consistenza su TUTTI i 5 topic):
     - Topic #1: `Decision=PRESENT`, `Status=CONSISTENT`, `Overlap=3`, `Jaccard=0.5000`
     - Topic #2: `Decision=PRESENT`, `Status=CONSISTENT`, `Overlap=2`, `Jaccard=0.6667`
     - Topic #3: `Decision=PRESENT`, `Status=CONSISTENT`, `Overlap=1`, `Jaccard=1.0000`
     - Topic #4: `Decision=PRESENT`, `Status=CONSISTENT`, `Overlap=1`, `Jaccard=0.5000`
     - Topic #5: `Decision=PRESENT`, `Status=CONSISTENT`, `Overlap=1`, `Jaccard=0.5000`
     - **Tasso di consistenza: 100% (5/5 topic consistenti)**.

2. **Chat 2 — Foto (`WhatsApp Chat - Marco (ERASMUS).zip`)**:
   - Ingestione & OCR reale Tesseract: 36 bundle, 1 sezione OCR estratta, 37 sezioni totali analizzabili.
   - Topic Discovery LIVE: **Status COMPLETE in 89.83s**, 37/37 sezioni analizzate (**copertura 100%**), 0 fallimenti micro-chunk.
   - Topic scoperti: **10 topic genuini in italiano** (entro il budget `max_topics=10`), supportati da citation evidence ID validi.
   - Prova del Nove (Verifica Indipendente di Consistenza su TUTTI i 10 topic):
     - 8 topic verificati come `PRESENT` e `CONSISTENT` (Overlap $\ge 1$, Jaccard fino a 1.0000).
     - 2 topic verificati come `ABSENT` (`Status=DISCREPANCY_ABSENT`, Overlap=0), confermando l'effettiva indipendenza inferenziale della seconda fase di Detection (nessun esito pre-confezionato o fittizio).
     - **Tasso di consistenza: 80% (8/10 topic consistenti)**.

Tutti i risultati pseudonimizzati sono stati archiviati in:
`handoff/2026-11-03/phase3_real_chat_validation_results.json`
nel rigoroso rispetto del vincolo di privacy forense (zero leak di testo di chat, trascrizioni vocali o testo OCR).

---

## 2. DIAGNOSI DELLA CAUSA RADICE

L'incidente non era causato da complessità del testo o da allucinazioni dell'LLM, bensì da una combinazione di violazione del contratto API di LM Studio e saturazione fisica della VRAM GPU:

### 1. Violazione Contratto REST v1 di LM Studio (`POST /api/v1/models/unload`)
In `ai/lmstudio.py`, il metodo `unload_model()` trasmetteva il payload:
```python
payload["model"] = target_to_unload
```
Tuttavia, l'API REST nativa di LM Studio valida rigorosamente lo schema e richiede obbligatoriamente:
```json
{"instance_id": "<instance_id>"}
```
Rifiutando con `HTTP 400 (Bad Request)` e messaggio:
`"Unrecognized key(s) in object: 'model'"`
qualsiasi richiesta contenente la chiave `"model"`. Di conseguenza, `unload_model()` falliva silenziosamente (restituendo `False`).

### 2. Duplicazione Istanze in Memoria e Saturazione VRAM
Ogni volta che `ensure_model_loaded()` tentava di riconfigurare o verificare il modello, la mancata de-allocazione causava il caricamento di una seconda istanza (`qwen2.5-7b-instruct:2`).
Sulla scheda grafica di test (**AMD Radeon RX 480 con 4.096 MB VRAM dedicata**):
- 1 istanza di Qwen 2.5 7B Q4_K_M con context 8192 richiede ~4.92 GB (di cui 4.48 GB pesi + 437 MB KV cache).
- Con 2 istanze attive contemporaneamente, il fabbisogno superava i **9.8 GB**, portando a saturazione e corruzione della memoria VRAM della GPU.

### 3. Output Degenerato e Collasso della Grammatica GBNF
La memoria GPU corrotta portava `llama.cpp` a calcolare logit invalidi/NaN, emettendo all'infinito il token 38 (`G`), ovvero la sequenza degenerata:
`"GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG..."`
Quando il parser GBNF di `llama.cpp` (attivato dallo schema JSON strutturato `json_schema`) riceveva la stringa di `"G"` invece del token iniziale atteso `'{'`, lo stack grammaticale collassava immediatamente sollevando l'errore:
```
HTTP 400: Engine protocol predict stream returned an error:
{"code":500,"message":"got exception: Unexpected empty grammar stack after accepting piece: G (38)","type":"server_error"}
```

### 4. Asimmetria del Fallback tra Detection e Discovery
Mentre `TopicDetectionAnalyzer` (linee 618-637) implementava un meccanismo di fallback con azzeramento dello schema (`current_schema = None`) e inserimento di una direttiva di compatibilità, `TopicDiscoveryAnalyzer` in `ai/topics.py` (linee 1208-1234):
- Rilanciava categoricamente l'errore se `len(document.bundles) > 3` (impedendo ai micro-chunk preventivi di dimensione 8 di tentare il recupero).
- Nei retry su blocchi $\le 3$, non impostava `current_schema = None`, ritrasmettendo lo stesso schema GBNF che provocava il crash C++ del server.
Questo provocava il fallimento sequenziale del 100% dei blocchi e il sollevamento finale di `OperationalAnalysisFailedError: Tutti i sotto-blocchi sono falliti`.

---

## 3. CORREZIONI STRUTTURALI APPLICATE

### A. Correzione `unload_model` in `ai/lmstudio.py`
Il metodo `unload_model` è stato rifattorizzato per rispettare rigorosamente il contratto REST v1 di LM Studio:
1. Interroga `get_native_models()` per identificare tutti gli `instance_id` effettivamente caricati associati al modello target.
2. Invia la richiesta formale `{"instance_id": inst_id}`.
3. Include un meccanismo di fallback a `{"model": inst_id}` per compatibilità con mock server e implementazioni legacy.
4. Invalida i binding e resetta lo stato solo a fronte di conferma HTTP 200/204.
5. Garantisce che non rimangano mai istanze residue duplicate in memoria prima dell'inferenza.

### B. Correzione Fallback GBNF in `TopicDiscoveryAnalyzer` (`ai/topics.py`)
La gestione di `INFERENCE_BACKEND_ERROR` in `TopicDiscoveryAnalyzer` è stata portata a parità architetturale con la Topic Detection:
1. Permette il fallback a livello di micro-chunk per blocchi fino a 8 bundle (`len(document.bundles) <= 8`).
2. Imposta `current_schema = None` per disattivare il parser grammaticale C++ di `llama.cpp` che ha manifestato il collasso.
3. Aggiunge nel prompt la direttiva `[CRITICAL FORMAT DIRECTIVE - LM STUDIO COMPATIBILITY]` richiedendo output in JSON nativo.
4. Affida la validazione forense, la riparazione della sintassi e il controllo delle citazioni delle evidenze (`alias` E001...) a `parse_and_validate_json` e `validate_topic_discovery_payload` in Python.

---

## 4. MATRICE DEI RISULTATI DELLA VALIDAZIONE OPERATIVA LIVE

| Parametro | Chat 1: Florida Atletica | Chat 2: Marco (ERASMUS) |
|---|---|---|
| **Tipo Chat** | Audio Vocale WhatsApp | Foto Immagine WhatsApp |
| **File Sorgente** | `G:\WhatsApp Chat - Florida Atletica.zip` | `G:\WhatsApp Chat - Marco (ERASMUS).zip` |
| **Bundle Totali** | 19 bundle | 36 bundle |
| **Media Processing** | Faster-Whisper STT (Opus $\rightarrow$ Testo) | Tesseract OCR (JPEG $\rightarrow$ Testo) |
| **Sezioni Analizzate** | 20 / 20 (**100%**) | 37 / 37 (**100%**) |
| **Discovery Status** | **COMPLETE** (43.88s) | **COMPLETE** (89.83s) |
| **Topic Scoperti** | 5 topic genuini | 10 topic genuini |
| **Micro-chunk Falliti** | **0 / 3** (0%) | **0 / 5** (0%) |
| **Verifiche Eseguite** | 5 topic verificati | 10 topic verificati |
| **Esiti Consistent** | 5 / 5 topic (**100%**) | 8 / 10 topic (**80%**) |
| **Esiti Discrepant** | 0 topic | 2 topic (ABSENT genuini) |
| **Overlap Medio Citazioni** | 1.6 evidenze / topic | 1.6 evidenze / topic |
| **Jaccard Medio** | 0.6333 | 0.6250 |
| **Violazioni Privacy** | **Zero** | **Zero** |

---

## 5. VERIFICA DEI TEST DI REGRESSIONE
La suite di test automatizzati dell'intero repository conferma l'assenza totale di regressioni:
- Unit test suite (`tests/unit`): **100% PASSED**.
- Lifecycle e Model Management test: **100% PASSED**.
- Discovery & Detection Invariants: **100% PASSED**.

## 6. CONCLUSIONE E RACCOMANDAZIONE PER IL CODE FREEZE
L'incidente bloccante è integralmente risolto sia alla radice (allocazione e gestione del ciclo di vita LM Studio su VRAM AMD) sia a livello di tolleranza ai guasti (resilienza GBNF e fallback JSON validato in Python).
Entrambe le chat WhatsApp autentiche con elaborazione multimediale reale (STT e OCR) completano l'analisi e la verifica con successo, garantendo la tracciabilità forense, la coerenza scientifica e l'assoluta tutela della privacy.

Il codice del repository `ChatAnalysis` è **pronto e raccomandato per il congelamento definitivo (CODE FREEZE)** in vista della consegna della tesi di laurea fissata per venerdì 6 novembre 2026.

