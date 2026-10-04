# PRELIMINARY CLOUD E2E VALIDATION — FINAL CLOSEOUT

## 1. Executive Summary e Chiusura della Fase Cloud

Il presente documento costituisce il report finale di **Closeout e Freeze** della fase **Preliminary Cloud E2E Validation** del progetto di tesi ChatAnalysis.

In conformità con il gate del supervisore:
- **La fase sperimentale su cloud è formalmente CHIUSA e CONGELATA.**
- Nessuna ulteriore chiamata verso API cloud sarà eseguita.
- Gli artefatti delle tre sessioni sperimentali svolte (**Protocol V1**, **Protocol V2**, **Protocol V3**) sono stati archiviati in modo immutabile e non modificabile nelle rispettive directory (`output/cloud_e2e/`, `output/cloud_e2e_v2/`, `output/cloud_e2e_v3/`).
- Dataset e ground truth sintetici restano rigorosamente congelati con i rispettivi hash crittografici SHA-256.
- Il percorso forense primario e il **benchmark sperimentale finale della tesi** restano interamente destinati all'ambiente locale su loopback: **LM Studio + modelli open-weight dedicati (Qwen, Llama, DeepSeek) su hardware locale compatibile**.

---

## 2. Sintesi delle Tre Sessioni Sperimentali (V1, V2, V3)

### 2.1 Protocol V1 — Incompatibilità del Parametro Seed
- **Data e Timestamp:** `2026-10-03T11:02:20.892415+00:00`
- **Configurazione:** Parametro `seed = 42` inviato formalmente nel payload JSON HTTP.
- **Riscontro Provider:** L'endpoint Google Gemini Developer API (`/v1beta/openai`) ha rifiutato categoricamente le richieste con `HTTP 400 Bad Request` (`Invalid JSON payload received. Unknown name "seed": Cannot find field.`).
- **Bilancio V1:** 7 richieste HTTP inviate, 0 inferenze completate, 0 output ricevuti.
- **Valore Tecnico:** Dimostrazione documentata e preservata dell'incompatibilità del campo `seed` sul gateway OpenAI di Google. Zero retry eseguiti.

### 2.2 Protocol V2 — Omissione Seed e Rilevamento Limiti Provider
- **Data e Timestamp:** `2026-10-03T15:19:05.338428+00:00`
- **Modifica Metodologica:** Omissione preventiva del parametro `seed` dal payload HTTP (`seed = None`, metadato `UNSUPPORTED_OMITTED`).
- **Esito:** Prima validazione reale parziale: 8 richieste inviate, 3 inferenze completate (Case 1 PRESENT corretto, Case 4 ABSENT corretto, Case 5 Translation Step eseguito con successo).
- **Rilevamento Limiti:** È emerso che l'invio non cadenzato di 8 richieste consecutive ha superato la soglia di throughput del provider:
  - `RPM LIMIT:` 5 requests/minuto
  - `TPM LIMIT:` ~250.000 tokens/minuto
  - `RPD LIMIT:` 20 requests/giorno
  Questo ha determinato l'insorgenza di errori `HTTP 429 Too Many Requests` (compatibili con superamento quota/rate) e `HTTP 503 Service Unavailable` (dovuti a saturazione e carico temporaneo dei nodi del provider).
- **Bilancio V2:** 2 su 6 casi di detection completati, 100% accuratezza decisionale sui completati, exact model match `gemini-3.8-flash`.

### 2.3 Protocol V3 — Pacing Preventivo a 15 Secondi (Rate-Limit-Aware Execution)
- **Data e Timestamp:** `2026-10-04T07:19:59.138923+00:00`
- **Modifica Metodologica:** Introduzione del vincolo temporale start-to-start preventivo:
  $$\text{START}(\text{request } N+1) - \text{START}(\text{request } N) \ge 15.0\text{s}$$
  mediante `time.monotonic()`, garantendo un throughput massimo teorico $\le 4\text{ RPM}$ (strettamente inferiore al limite di 5 RPM). Nessun retry o backoff: una richiesta equivale esattamente a un solo tentativo (1:1).
- **Esito:**
  - 8 richieste HTTP inviate.
  - 5 inferenze LLM completate con successo (Case 1, Case 3, Case 4, Case 5 Translation Step, Case 6 Topic Discovery).
  - 3 su 6 casi di Topic Detection completati (50.0% completion rate).
  - 2 decisioni corrette su 3 casi completati (**66.7% Decision Accuracy sui completati**).
  - **HTTP 429:** **0 errori** (azzerati rispetto a V2 grazie al pacing).
  - **HTTP 503:** 3 errori (indisponibilità transitoria del provider "high demand" su Case 2, Case 5-Direct e Case 5-Translate Detection).
  - Pacing minimo osservato: `15.000s` (media `15.001s`).
  - Citazioni evidenze invalide: `0` | Errori structured output: `0` | Model mismatch: `0`.

---

## 3. Quadro Sinottico Comparativo V1 / V2 / V3

| Parametro / Metrica | Protocol V1 | Protocol V2 | Protocol V3 |
|---|---|---|---|
| **Data Esecuzione** | 2026-10-03 | 2026-10-03 | 2026-10-04 |
| **Model Requested** | `gemini-3.8-flash` | `gemini-3.8-flash` | `gemini-3.8-flash` |
| **Model Returned** | N/A (bloccato pre-inferenza) | `gemini-3.8-flash` (Exact) | `gemini-3.8-flash` (Exact) |
| **Parametro Seed** | `42` (Requisito) | `null` (UNSUPPORTED_OMITTED) | `null` (UNSUPPORTED_OMITTED) |
| **Request Start Pacing** | Nessuno (burst) | Nessuno (burst) | **15.0s preventivo** (Observed min: 15.000s) |
| **Cloud API Requests Sent** | 7 | 8 | 8 |
| **LLM Inferences Completed** | 0 | 3 | **5** |
| **Model Outputs Received** | 0 | 3 | **5** |
| **Detection Completion Rate** | 0.0% (0 / 6) | 33.3% (2 / 6) | **50.0% (3 / 6)** |
| **Decision Accuracy (Completati)** | N/A (`null`) | 100.0% (2 / 2) | **66.7% (2 / 3)** |
| **CASE 1 (Explicit Present)** | HTTP 400 | SUCCESS (`PRESENT`, corretto) | SUCCESS (`PRESENT`, corretto) |
| **CASE 2 (Implicit Present)** | HTTP 400 | FAILED (HTTP 503) | FAILED (HTTP 503) |
| **CASE 3 (Ambiguous Uncertain)**| HTTP 400 | FAILED (HTTP 503) | SUCCESS (`PRESENT`, **Model Decision Error**) |
| **CASE 4 (Absent)** | HTTP 400 | SUCCESS (`ABSENT`, corretto) | SUCCESS (`ABSENT`, corretto) |
| **CASE 5 Direct Multilingual** | HTTP 400 | FAILED (HTTP 503) | FAILED (HTTP 503) |
| **CASE 5 Translate Step** | Non eseguito | SUCCESS (Traduzione fedele) | SUCCESS (Traduzione fedele) |
| **CASE 5 Translate Detection** | Non eseguito | FAILED (HTTP 429) | FAILED (HTTP 503) |
| **CASE 6 Topic Discovery** | Non eseguito | FAILED (HTTP 429) | SUCCESS (6 topic estratti, 100% validi) |
| **HTTP 429 Errors** | 0 | 2 | **0 (azzerato)** |
| **HTTP 503 Errors** | 0 | 3 | 3 |
| **Citazioni Evidenze Invalide** | 0 | 0 | 0 |
| **Errori Structured Output** | 0 | 0 | 0 |
| **Retry Eseguiti** | 0 | 0 | 0 |

---

## 4. Chiarimento Concettuale: HTTP 429 vs HTTP 503

I risultati sperimentali di Protocol V3 forniscono una chiara conferma empirica della separazione concettuale tra le tipologie di fallimento di rete:
- **`HTTP 429 Too Many Requests`:** Identifica una *Execution Failure* direttamente collegata ai vincoli di rate limit o quota (superamento della soglia di 5 RPM durante Protocol V2). L'introduzione del pacing preventivo a 15.0s ha eliminato completamente gli errori HTTP 429 (da 2 occorrenze in V2 a 0 in V3).
- **`HTTP 503 Service Unavailable`:** Identifica una *Execution Failure* determinata esclusivamente da indisponibilità transitoria o congestione di carico dei server Google ("This model is currently experiencing high demand. Spikes in demand are usually temporary."). La frequenza delle richieste non causa direttamente l'HTTP 503, come dimostrato dalla sua persistenza anche con pacing a 15 secondi.
- Entrambi i codici sono rubricati come **Execution Failures** e tenuti rigorosamente distinti dagli errori decisionali del modello.

---

## 5. Analisi del Caso 3 (Model Decision Error)

Nel Caso 3 (`CASE_3_AMBIGUOUS_UNCERTAIN`):
- **Ground Truth Attesa:** `UNCERTAIN`. La conversazione sintetizzata presenta indicatori ambigui (oggetto ingombrante in borsa, richiesta di riservatezza, preferenza a discutere dettagli a voce), compatibili sia con un contesto lecito (sorpresa, prestito confidenziale) sia con una transazione opaca, mancando i marcatori univoci di illiceità presenti nel Caso 2.
- **Decisione Restituita dal Modello:** `PRESENT`. Il modello `gemini-3.8-flash` ha interpretato gli elementi di furtività e riservatezza come prova positiva del topic illecito.
- **Classificazione Scientifica:** Trattasi di un autentico **MODEL DECISION ERROR** su inferenza regolarmente completata con successo, **NON** di un errore tecnico o di formato. Esso rivela una propensione del modello verso la formulazione di conclusioni binarie certe anche a fronte di quadri probatori incompleti.

---

## 6. Risultati Topic Discovery e Translate-First

- **Topic Discovery (Caso 6 — Valutazione Qualitativa):** Il modello ha estratto autonomamente 6 topic complessi (*Riunione condominiale, Partita di calcetto, Cena al ristorante giapponese, Acquisto di un romanzo, Visita veterinaria e passaggio in auto, Concerto jazz*), citando esclusivamente sezioni di evidenza reali (6 su 6 valide, zero allucinazioni). Il confronto post-hoc ha confermato una concordanza qualitativa del 100% rispetto ai temi previsti nella ground truth.
- **Translate-First (Caso 5 — Validazione Parziale):** Lo Step 1 (traduzione forense con `EvidenceTranslator`) è stato validato con pieno successo in 5.593s e 2098 token, preservando integralmente le chiavi `evidence_id` originali nel testo tradotto. Lo Step 2 (detection finale sul testo tradotto) non è stato completato a causa di una risposta HTTP 503 del provider; pertanto la pipeline Translate-First è **parzialmente validata** e non completamente conclusa end-to-end.

---

## 7. Nota di Erratum: Dataset Sintetico e Ground Truth Discovery

Si segnala un erratum documentale circoscritto:
- Nel `CASE_6_TOPIC_DISCOVERY`, il messaggio sintetico sorgente `ce2e_c6_005` in `messages.json` cita un "gatto" portato dal veterinario per il vaccino.
- Nella ground truth congelata (`ground_truth.json`), una keyword del topic descrittivo cita accidentalmente "cane".
- **Impatto:** La discrepanza è puramente lessicale e non altera in alcun modo il topic atteso ("Animali domestici / Cure veterinarie"), gli evidence ID, i document ID, né la valutazione qualitativa di Topic Discovery (in cui il modello ha correttamente estratto "Visita veterinaria e passaggio in auto" citando l'evidenza `ce2e_c6_005`).
- In ossequio al principio di congelamento e integrità crittografica, `ground_truth.json` **NON viene modificato post-hoc**.

---

## 8. Conclusioni Metodologiche e Delimitazioni Scientifiche

La fase **Preliminary Cloud E2E Validation** ha dimostrato con successo che l'infrastruttura software di ChatAnalysis è in grado di:
1. Dialogare con un LLM remoto reale tramite endpoint standard OpenAI-compatible;
2. Ottenere output strutturato conforme a JSON Schema con validazione deterministica lato Python;
3. Preservare l'integrità ontologica e la provenienza delle evidenze (`invalid_evidence_citations = 0`);
4. Verificare l'identità del modello (`gemini-3.8-flash` exact match);
5. Separare le *Execution Failures* (HTTP 429/503) dai *Model Decision Errors*;
6. Eseguire Topic Detection e Topic Discovery reali su conversazioni sintetiche;
7. Operare a garanzia assoluta di privacy senza trasmissione di dati forensi reali né esposizione di chiavi.

**Delimitazioni Scientifiche:**
- **Non tutti i casi sono stati completati:** A causa di risposte HTTP 503 di indisponibilità dei server Google, il completion rate è stato del **50.0% (3 / 6)**.
- **Accuratezza Contestuale:** La Decision Accuracy del **66.7%** è calcolata esclusivamente sui 3 casi di Topic Detection validamente completati da `gemini-3.8-flash` e non costituisce una metrica generale o statisticamente esaustiva delle prestazioni del modello.
- **Ragioni per il Percorso Finale Locale:** La volatilità della disponibilità (HTTP 503), l'impossibilità di garantire seed deterministico e le quote rigide confermano definitivamente la scelta progettuale della tesi: **il benchmark forense primario e finale viene condotto in locale (LM Studio su loopback) con modelli open-weight (Qwen, Llama, DeepSeek) su hardware compatibile**.

---

## Dichiarazioni Obbligatorie di Chiusura

- **CLOUD E2E PHASE:** CLOSED
- **ADDITIONAL LIVE RUNS:** NO
- **V1/V2/V3 ARTIFACTS MODIFIED:** NO
- **DATASET MODIFIED:** NO
- **GROUND TRUTH MODIFIED:** NO
- **REAL FORENSIC DATA SENT:** NO
- **FINAL THESIS BENCHMARK:** NO
- **NEXT EXPERIMENTAL PHASE:** LOCAL LM STUDIO QWEN/LLAMA/DEEPSEEK
