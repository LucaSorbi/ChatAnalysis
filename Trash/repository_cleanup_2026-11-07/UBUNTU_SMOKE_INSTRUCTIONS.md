# ISTRUZIONI OPERATIVE SMOKE TEST SU UBUNTU (LABORATORIO)

**Destinazione Ambiente Target:**
- **OS:** Ubuntu 22.04.5 LTS
- **CPU:** Intel Core i5-9600K (6 core @ 3.70GHz - 4.60GHz)
- **RAM:** 31 GB DDR4
- **GPU:** NVIDIA GeForce GTX TITAN X 12 GB VRAM
- **Python:** 3.12.4
- **LM Studio:** Server locale v0.3.x / v0.4.x
- **Directory Progetto:** `/home/nottedeiricercatori/Desktop/LUCA/Tesi`

---

## 1. PRE-REQUISITI E AVVIO LM STUDIO

1. **Avvio di LM Studio:**
   - Aprire l'applicazione LM Studio su Ubuntu.
   - Accedere alla sezione **Developer / Local Server** (icona `<->` o pannello laterale).
   - Verificare che il server sia configurato su:
     - **Host:** `127.0.0.1` (loopback)
     - **Port:** `1234`
     - **URL finale:** `http://127.0.0.1:1234`

2. **Caricamento Modello Canonico:**
   - Selezionare e caricare il modello:
     `qwen2.5-7b-instruct` (o variante quantizzata compatibile `qwen2.5-7b-instruct-q4_k_m.gguf`).
   - Nella configurazione di caricamento (Model Loader Settings):
     - **Context Length:** Impostare rigorosamente a `8192` (non lasciare default 2048 o auto inferiore).
     - **GPU Offload:** Maximize / Full offload (12 GB VRAM TITAN X supportano interamente il modello 7B Q4).
   - Premere **Start Server** e verificare che nei log di LM Studio compaia:
     `Server listening on 127.0.0.1:1234`.

3. **Verifica Rapida Connettività Endpoint:**
   Da terminale Ubuntu:
   ```bash
   curl http://127.0.0.1:1234/v1/models
   ```
   Dovrà rispondere un JSON contenente l'elenco dei modelli caricati.

---

## 2. ESECUZIONE DELLO SMOKE TEST OPERATIVO

Lo script `scripts/smoke_operational_ai.py` utilizza **esattamente la stessa architettura di analisi** (`LocalTopicAnalysisService`) impiegata dalla UI Streamlit (`app.py`), senza percorsi sintetici separati.

### Comando di Esecuzione

Posizionarsi nella directory del repository ed eseguire con l'interprete Python 3.12 dell'ambiente virtuale:

```bash
cd /home/nottedeiricercatori/Desktop/LUCA/Tesi
source .venv/bin/activate
python3 scripts/smoke_operational_ai.py
```

*Nota: Se si desidera specificare un identificatore di modello alternativo qualora LM Studio esponga un alias diverso:*
```bash
python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct
```

---

## 3. CHECKLIST DEI 13 PUNTI DI CONTROLLO

Durante l'esecuzione, verificare che lo script completi tutti i punti della checklist:

1. [x] **Avvio LM Studio:** Rilevamento disponibilità server loopback.
2. [x] **Endpoint loopback:** Connessione HTTP su `http://127.0.0.1:1234`.
3. [x] **Identità modello:** Risoluzione automatica di `qwen2.5-7b-instruct` tramite `OPERATIONAL_SPECS`.
4. [x] **Context 8192:** Verifica context length esplicito configurato.
5. [x] **Caricamento modello:** Conferma istanza attiva e binding isolato.
6. [x] **Importazione DEMO:** Caricamento di `doc::demo_forensic_chat` (`ConversationEvidenceDocument` immutabile).
7. [x] **Topic Discovery LIVE:** Estrazione non supervisionata dei topic con schema JSON vincolato.
8. [x] **Selezione topic:** Prelievo del primo topic scoperto e conservazione dell'ordine originale.
9. [x] **Nuova Topic Detection indipendente:** Esecuzione della seconda query senza riuso di cache (Prova del Nove).
10. [x] **Consistency:** Valutazione automatica dello stato di coerenza (`CONSISTENT`, `PARTIAL`, `CONFLICT`).
11. [x] **Registrazione metadata:** Tracciamento immutabile di `run_id`, `latency`, `model`, `tokens`, `origin=LIVE_LM_STUDIO`.
12. [x] **Test FILE sintetico:** Importazione ed analisi di una seconda conversazione (`chat_file_investigative_02`).
13. [x] **Nessuna contaminazione:** Verifica che le run di Discovery e Detection tra DEMO e FILE siano rigorosamente isolate e confinate ai rispettivi `document_id`.

---

## 4. CASO SEMANTICO DI CONTROLLO

Lo script include il test diagnostico controllato per la tesi:
- **Topic:** `Logistica e Ispezione Depositi`
- **Descrizione:** `Ispezione visiva del magazzino merci con casse sigillate e sicurezza`
- **Evidenza Target:** `msg::5::VISION_DESCRIPTION`

> **REGOLA FORENSE FONDAMENTALE:**
> - Il prompt scientifico non deve essere modificato.
> - La decisione `PRESENT` non deve essere forzata.
> - Se il modello risponde `ABSENT`, il risultato viene registrato fedelmente nei log scientifici della tesi come comportamento empirico del modello sotto esame.

---

## 5. FORMATO ATTESO DEL REPORT TERMINALE

Al termine dell'esecuzione, lo script deve restituire Exit Code 0 e visualizzare il blocco:

```text
===========================================================================
REPORT STRUTTURATO AUDIT & RELEASE GATE AI:
===========================================================================
document_id             : doc::demo_forensic_chat
discovery_run_id        : run_disc_<hash>
model_key               : <qwen2.5-7b-instruct...>
instance_id             : <instance_id...>
response.model          : <modello restituito da LM Studio>
context_length          : 8192
discovered_topic        : <Etichetta del topic scoperto>
discovery_evidence_ids  : ['msg::...']
detection_decision      : PRESENT / ABSENT
detection_evidence_ids  : [...]
consistency             : CONSISTENT / PARTIAL / CONFLICT
latency                 : Discovery=...s, Detection=...s, Control=...s
---------------------------------------------------------------------------
control_topic           : Logistica e Ispezione Depositi
control_target_evidence : msg::5::VISION_DESCRIPTION
control_decision        : PRESENT / ABSENT
control_evidence_ids    : [...]
control_rationale       : <Motivazione addotta dal modello>
---------------------------------------------------------------------------
file_document_id        : chat_file_investigative_02
file_discovery_run_id   : run_disc_<hash_2>
multi_conv_isolation    : PASSED (Zero leak)
===========================================================================
ESITO SMOKE TEST OPERATIVO: SUCCESSO
```

Salvare l'output completo in un file di log:
```bash
python3 scripts/smoke_operational_ai.py | tee /tmp/smoke_operational_ai_live.log
```
