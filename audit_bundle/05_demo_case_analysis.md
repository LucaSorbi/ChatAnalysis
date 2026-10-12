# AUDIT DEL CASO DEMO E DEL DATASET DIMOSTRATIVO

Analisi tecnica dettagliata del dataset sintetico dimostrativo implementato in `ui/demo.py`, dei risultati precalcolati e delle evidenze multimodali associate.

---

## 1. Parametri Strutturali del Documento DEMO

* **Document ID**: `doc::demo_forensic_chat`
* **Chat ID**: `chat::synthetic_demo_01`
* **Source Name**: `demo_msgstore_db`
* **Bundle Count**: **8** bundle (`msg::1` .. `msg::8`)
* **Evidence Sections Count**: **12** sezioni testuali totali
* **Modalità Generazione**: Funzione deterministica `build_synthetic_demo_dataset()` in `ui/demo.py`

---

## 2. Topic Detection Precalcolati nella DEMO

| # | Topic Label | Descrizione | Decisione | Evidence IDs | Rationale |
| :--- | :--- | :--- | :---: | :--- | :--- |
| 1 | **Pianificazione Incontri Riservati** | Accordi per vedersi di persona e fissare appuntamenti | `PRESENT` | `msg::1::ORIGINAL_TEXT`<br>`msg::7::ORIGINAL_TEXT` | Accordi espliciti per vedersi alle 18:00 (msg::1) e conferma dell'orario e luogo in caffetteria (msg::7). |
| 2 | **Cessione Sostanze Illecite** | Trattative relative a compravendita o scambio di sostanze stupefacenti | `ABSENT` | `()` (vuoto) | Nessun riferimento a cessione, detenzione o commercio di sostanze illecite riscontrato nella conversazione. |
| 3 | **Transazioni Finanziarie Sospette** | Pagamenti in contanti o termini di pagamento anomali non tracciati | `UNCERTAIN` | `msg::3::OCR_TEXT`<br>`msg::4::ORIGINAL_TEXT` | Rilevata fattura da 15.000 EUR pagata al contado (msg::3) e sollecito a saldare i termini entro venerdì (msg::4), senza evidenza esplicita di illiceità. |

---

## 3. Topic Discovery Precalcolati nella DEMO

I topic mostrati nella scheda "Topic Discovery" all'avvio della DEMO sono **100% HARDCODED / PRECALCOLATI** all'interno di `ui/demo.py` (righe 329–346). Nessun modello AI li ha generati in tempo reale durante il caricamento.

### Topic A: "Logistica e Ispezione Depositi"
* **Label**: `"Logistica e Ispezione Depositi"`
* **Short Description**: `"Ispezione visiva del magazzino merci con casse sigillate e sicurezza"`
* **Evidence IDs dichiarati**: `("msg::5::VISION_DESCRIPTION",)`
* **Analisi dell'Evidenza `msg::5`**:
  - `msg_id`: `msg::5`
  - `text_content`: `"Foto scattata all'ingresso del deposito merci."` (source type: `ORIGINAL_TEXT`)
  - `media_reference`: `images/deposito_ingresso.jpg`
  - `image_vision.description`: `"Un magazzino industriale con diverse casse etichettate e una porta di sicurezza blindata."` (source type: `VISION_DESCRIPTION`)
  - `image_vision.observations`:
    - `0`: `"Casse di legno sigillate"` (source type: `VISION_OBSERVATION`)
    - `1`: `"Porta di sicurezza blindata"` (source type: `VISION_OBSERVATION`)
* **Verifica Semantica**: L'evidenza `msg::5::VISION_DESCRIPTION` esiste realmente nel documento e contiene integralmente i concetti di:
  - *magazzino industriale*;
  - *deposito merci* (nel testo originario);
  - *casse etichettate* e *casse sigillate*;
  - *porta di sicurezza blindata*.

### Topic B: "Gestione Contratti e Bozze"
* **Label**: `"Gestione Contratti e Bozze"`
* **Short Description**: `"Scambio di richieste per bozze contrattuali e revisione documenti firmati"`
* **Evidence IDs dichiarati**: `("msg::2::STT_TRANSCRIPTION", "msg::8::ORIGINAL_TEXT")`
* **Analisi delle Evidenze Associate**:
  - `msg::2::STT_TRANSCRIPTION`: Trascrizione audio vocale (`STT_TRANSCRIPTION`):
    `"Ciao, ti ho inviato la bozza del documento aggiornata. Fammi sapere appena puoi verificare i dettagli."`
  - `msg::8::ORIGINAL_TEXT`: Messaggio testuale utente (`ORIGINAL_TEXT`):
    `"Ricevuto, procedo con la revisione della bozza e ti aggiorno."`
* **Verifica Semantica**: I due elementi provano oggettivamente lo scambio di bozze contrattuali attraverso sorgenti eterogenee (audio vocale trascritto + testo chat).

---

## 4. Conclusioni sulla Natura dei Topic Demo
1. I topic visualizzati nelle card di Topic Discovery sono **oggetti statici istanziati a codice** (`hardcoded`).
2. La pressione del pulsante `"🔬 Verifica con Topic Detection"` su tali card attiva invece una **vera inferenza computazionale indipendente** tramite `TopicDetectionAnalyzer`.
3. Il disallineamento storico tra il Discovery precalcolato e la Detection live è nato proprio perché il Discovery demo presumeva un'analisi concettuale/semantica, mentre il prompt standard di Detection richiedeva sovrapposizione letterale all'interno del dialogo discorsivo.
