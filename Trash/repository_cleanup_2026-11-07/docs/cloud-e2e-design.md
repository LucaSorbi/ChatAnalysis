# Cloud E2E Benchmark Design — Synthetic Dataset

> **CLOUD E2E DATA POLICY: SYNTHETIC DATA ONLY.**
>
> **FINAL THESIS BENCHMARK: LM Studio + Qwen/Llama/DeepSeek local execution.**

## 1. Scopo del Benchmark Temporaneo

Questo benchmark sintetico prepara un test end-to-end preliminare con un LLM online,
tramite un adapter sperimentale dedicato (`RemoteOpenAICompatibleTestClient`),
**senza eseguire chiamate non autorizzate o trasmettere dati forensi reali**.

Lo scopo è:

- Creare un dataset completamente fittizio con ground truth verificabile.
- Validare che il dataset attraversi correttamente l'intera pipeline
  (Importer → RawRecord → Validation → Normalization → Entity Resolution →
  UnifiedMessage → MessageEvidenceBundle → ConversationEvidenceDocument).
- Produrre evidence_id deterministici e reali che serviranno da riferimento
  per la valutazione automatica delle risposte di un LLM.
- Disporre di un backend remoto sperimentale (`ai/cloud_test.py`) isolato e rigoroso,
  attivabile solo programmaticamente e MAI esposto nella UI forense di Streamlit.

## 2. Test Software Fake vs Test AI Reale

| Aspetto | Test Software (attuale) | Test AI Reale (futuro) |
|---------|------------------------|----------------------|
| LLM coinvolto | NO — nessun modello invocato | SÌ — modello cloud o locale |
| Cosa verifica | Integrità pipeline, determinismo, evidence_id | Qualità decisionale del modello |
| Dati | Sintetici fittizi | Sintetici fittizi (stessi) |
| Ground truth | Usata per validare la pipeline | Usata per misurare accuracy/F1 |
| Network | Zero connessioni | Connessione al provider LLM |

## 3. Percorso Forense Finale (Locale) vs Percorso E2E Cloud Sperimentale

| Dimensione | Percorso Forense / Finale Tesi | Percorso E2E Cloud Sperimentale |
|------------|--------------------------------|--------------------------------|
| **Client Adapter** | `LmStudioClient` (`ai/lmstudio.py`) | `RemoteOpenAICompatibleTestClient` (`ai/cloud_test.py`) |
| **Interfaccia Base** | `BaseLocalLlmClient` | `BaseLlmClient` (NON `BaseLocalLlmClient`) |
| **Network Policy** | **LOOPBACK ONLY** (`127.0.0.1`, `localhost`, `::1`) | Endpoint remoto HTTPS esplicito |
| **Modelli** | Modelli Open Weight locali (Qwen, Llama, DeepSeek) | Modelli remoti compatibili OpenAI (es. OpenAI, OpenRouter) |
| **Hardware** | Workstation locale con AVX2/GPU | Qualsiasi (inferenza remota) |
| **Politica Dati** | **Dati forensi reali** autorizzati e sintetici | **SYNTHETIC DATA ONLY — DIVIETO ASSOLUTO DI DATI REALI** |
| **Streamlit UI** | Integrato per analisi investigativa locale | **NON DISPONIBILE — NESSUNA OPZIONE CLOUD IN UI** |
| **Credenziali** | Token opzionale per server locale | API key esclusivamente da environment variable |
| **Scopo Scientifico** | Benchmark sperimentale comparativo della tesi | Validazione E2E preliminare della pipeline analitica |


## 4. Struttura dei 6 Casi

### CASO 1 — EXPLICIT_PRESENT
- **Conversazione:** Pianificazione di un giro in bicicletta su una nuova pista ciclabile.
- **Lingua:** Italiano.
- **Topic target:** Ciclismo e percorsi ciclabili.
- **Decisione attesa:** `PRESENT`.
- **Funzione:** Baseline facile. Il topic è nominato esplicitamente e ripetutamente.

### CASO 2 — IMPLICIT_PRESENT
- **Conversazione:** Transazione clandestina: oggetto privo di numeri di serie e documenti,
  pagamento solo in contanti, appuntamento notturno evitando telecamere, canale ufficiale
  che non fornisce più l'articolo, ordine di cancellare la chat.
- **Lingua:** Italiano.
- **Topic target:** Compravendita illecita.
- **Decisione attesa:** `PRESENT`.
- **Funzione:** Test di comprensione contestuale. Nessun termine illegale esplicito
  né menzione di un bene specifico, ma la convergenza di indicatori (non-rintracciabilità,
  contanti, evasione della sorveglianza, canale ufficiale precluso, distruzione delle
  comunicazioni) rende difendibile la conclusione che si tratti di beni non commerciabili
  legalmente.

### CASO 3 — AMBIGUOUS_UNCERTAIN
- **Conversazione:** Incontro per consegnare "qualcosa di importante", borsa capiente,
  richiesta di riservatezza, possibile compenso, preferenza per discutere dettagli
  di persona anziché per iscritto. Nessuna risoluzione esplicita della natura
  dell'oggetto.
- **Lingua:** Italiano.
- **Topic target:** Compravendita illecita (stesso del Caso 2).
- **Decisione attesa:** `UNCERTAIN`.
- **Funzione:** Test di calibrazione. L'ambiguità NON viene risolta dal contesto:
  la conversazione supporta sia l'interpretazione sospetta (segretezza, compenso,
  dettagli non scritti) sia quella innocente (favore tra amici, sorpresa, regalo).
  Mancano i marcatori decisivi di illiceità presenti nel Caso 2 (non-rintracciabilità,
  evasione sorveglianza, canale ufficiale precluso). PRESENT e ABSENT sono entrambi
  indifendibili. UNCERTAIN è la sola ground truth metodologicamente corretta.

### CASO 4 — ABSENT
- **Conversazione:** Lezioni di chitarra, cucina (risotto), pallavolo.
- **Lingua:** Italiano.
- **Topic target:** Compravendita illecita.
- **Decisione attesa:** `ABSENT`.
- **Funzione:** Controllo falsi positivi. Zero indicatori del topic target.

### CASO 5 — MULTILINGUAL_PRESENT
- **Conversazione:** Viaggio a Barcellona per una conferenza accademica.
- **Lingue:** Italiano, Inglese, Spagnolo (code-switching naturale).
- **Topic target:** Viaggio per conferenza accademica.
- **Decisione attesa:** `PRESENT`.
- **Funzione:** Test multilingue. Servirà per confrontare `DIRECT_MULTILINGUAL`
  vs `TRANSLATE_FIRST`.

### CASO 6 — TOPIC_DISCOVERY
- **Conversazione:** Vita quotidiana con almeno 5 argomenti distinguibili:
  riunione condominiale, calcetto, ristorante giapponese, libreria/romanzo,
  veterinario/animali, concerto jazz.
- **Lingua:** Italiano.
- **Topic target:** Nessuno (Open Discovery).
- **Decisione attesa:** N/A.
- **Funzione:** Test Topic Discovery. I topic NON vengono forniti come input al modello.

> **Nota di Erratum (Ground Truth Discovery):**
> Nel `CASE_6_TOPIC_DISCOVERY` il messaggio sintetico sorgente `ce2e_c6_005` fa riferimento a un "gatto" portato dal veterinario.
> Una descrizione/keyword della ground truth congelata utilizza accidentalmente "cane".
> Questa discrepanza è esclusivamente lessicale e NON modifica:
> - il topic atteso "Animali domestici / Cure veterinarie";
> - gli evidence ID;
> - il document ID;
> - la valutazione qualitativa Topic Discovery.
> La ground truth congelata (`ground_truth.json`) NON viene modificata post-hoc per preservare l'integrità crittografica SHA-256 congelata.

## 5. Ground Truth

Il file `test_data/cloud_e2e/ground_truth.json` contiene per ogni caso:

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `case_id` | string | Identificativo univoco del caso |
| `conversation_chat_id` | string | chat_id prodotto dalla pipeline |
| `document_id` | string | document_id deterministico della pipeline |
| `language_profile` | string[] | Lingue presenti nella conversazione |
| `difficulty` | string | `explicit`/`implicit`/`ambiguous`/`absent`/`multilingual`/`discovery` |
| `target_topic_id` | string? | ID del topic target per Detection (null per Discovery) |
| `target_topic_label` | string? | Label del topic target |
| `target_topic_description` | string? | Descrizione del topic target |
| `expected_decision` | string? | `PRESENT`/`ABSENT`/`UNCERTAIN`/null |
| `expected_evidence_ids` | string[] | Evidence_id reali prodotti dalla pipeline |
| `expected_source_record_ids` | string[] | Source record ID originali |
| `human_rationale` | string | Spiegazione umana della ground truth |
| `expected_discovery_topics` | object[]? | Topic attesi per il caso Discovery |

**IMPORTANTE:** La ground truth NON viene mai serializzata nel prompt inviato all'LLM.
È materiale esclusivamente per la valutazione post-hoc.

## 6. Rischio False Positive / False Negative

- **False Positive:** Il Caso 4 (ABSENT) è progettato per rilevare falsi positivi.
  La conversazione è realistica ma priva del topic target.
- **False Negative:** I Casi 1 e 2 verificano che il modello non manchi evidenze
  esplicite e implicite.
- **Calibrazione:** Il Caso 3 (UNCERTAIN) testa la capacità del modello di astenersi
  quando l'evidenza è ambigua, evitando sia falsi positivi che falsi negativi.

## 7. Funzione di UNCERTAIN

Il verdetto `UNCERTAIN` svolge un ruolo critico nel benchmark:

- **Non è un errore:** È una decisione deliberata e corretta quando l'evidenza
  è insufficiente o contraddittoria.
- **Distinzione da ABSENT:** ABSENT = assenza verificata. UNCERTAIN = impossibilità
  di concludere in modo definitivo.
- **Rilevanza forense:** In ambito investigativo, segnalare incertezza è preferibile
  a generare certezze infondate.
- **Metrica:** Un modello che restituisce UNCERTAIN per il Caso 3 è più calibrato
  di uno che forza PRESENT o ABSENT.

## 8. Dati Cloud Esclusivamente Sintetici

Tutti i dati nel benchmark cloud E2E sono **interamente fittizi**:

- ❌ Nessun nome reale
- ❌ Nessun numero telefonico reale
- ❌ Nessun JID reale
- ❌ Nessun indirizzo reale
- ❌ Nessun dato proveniente da persone reali
- ❌ Nessuna copia di conversazioni autentiche

**Motivazione:** Il benchmark cloud è un test tecnico temporaneo. L'uso di dati sintetici
garantisce:

1. **Privacy:** Nessun rischio di esporre dati personali a provider cloud.
2. **Riproducibilità:** Il dataset è identico tra esecuzioni.
3. **Controllabilità:** La ground truth è verificabile a priori.
4. **Compliance:** Nessun vincolo GDPR o etico relativo a dati reali.

## 9. Formato Sorgente

Il dataset usa il formato **Cellebrite JSON** (`messages.json`) perché:

- L'importer `CellebriteJsonImporter` esiste già e non richiede nuova logica.
- Il formato JSON è leggibile e facile da creare manualmente.
- Lo streaming via `ijson` è già implementato e testato.
- La struttura `{id, chat_id, sender, timestamp, type, content, metadata}` è semplice.

## 10. Architettura del Runner Sperimentale (`ai/cloud_experiment.py`)

Il modulo `ai/cloud_experiment.py` fornisce l'infrastruttura CLI autonoma per eseguire la validazione E2E preliminare su provider cloud, operando in modo rigorosamente separato dall'applicazione investigativa Streamlit.

### 10.1 Frozen Dataset Hash Lock

Il runner opera esclusivamente sui due file congelati nel repository:
- `test_data/cloud_e2e/messages.json`
  - SHA-256: `2E49A9C62D89125D225726953FF0E879FB49E07D7DB803DB5503C345B051B781`
- `test_data/cloud_e2e/ground_truth.json`
  - SHA-256: `377FB95B7690BA507481F357449B1A56D1F89CB60636DA9DEBD66FE603E5AD6D`

Entrambi gli hash vengono verificati preventivamente all'avvio. Qualsiasi mismatch, assenza di file o tentativo di indicare path arbitrari tramite CLI provoca l'abort immediato dell'esecuzione (`CloudExperimentIntegrityError`). Non è consentito alcun override da CLI (parametri come `--dataset` o `--ground-truth` sono esplicitamente proibiti).

### 10.2 Doppio Meccanismo di Opt-In Obbligatorio

Per scongiurare qualsiasi invocazione accidentale di endpoint cloud a pagamento o esterni, il runner impone un vincolo di **doppio opt-in**:
1. Variabile d'ambiente: `RUN_CLOUD_E2E=1`
2. Flag CLI esplicito: `--force-run`

L'assenza di anche una sola delle due condizioni abortisce immediatamente l'esecuzione prima dell'inizializzazione del client o di qualsiasi connessione di rete.

### 10.3 Separazione Rigorosa Ground Truth / Prompt e Flusso Post-Ingestion

La segregazione metodologica impone un ordine rigoroso di esecuzione in 9 fasi sequenziali:
1. **Verifica opt-in:** controllo congiunto di `RUN_CLOUD_E2E=1` e `--force-run`.
2. **Risoluzione percorsi congelati:** ancoraggio ai file immutabili del repository senza override CLI.
3. **Verifica hash DATASET:** verifica crittografica SHA-256 del solo file `messages.json`.
4. **Validazione endpoint URL:** verifica secret-safe dell'URL (solo HTTPS, no credenziali, no query, no fragment).
5. **Verifica API key:** controllo presenza della variabile d'ambiente configurata.
6. **Esecuzione pipeline forense locale reale (`run_synthetic_pipeline`):**
   - Esecuzione completa `CellebriteJsonImporter` → `RawRecord` → `Validation` → `Normalization` → `Entity Resolution` → `UnifiedMessage` → `MessageEvidenceBundle` → `ConversationEvidenceDocument`.
   - **Verifica stato ingestion:** convalida esplicita che l'ingestion abbia status `IngestionStatus.SUCCESS`. In caso contrario, sollevamento immediato di `CloudExperimentError` e blocco dell'esecuzione prima di qualsiasi contatto con il provider AI.
7. **SOLO ORA verifica hash GROUND TRUTH:** calcolo e verifica dello SHA-256 di `ground_truth.json` esclusivamente dopo che la costruzione dei documenti è completata. Nessuna lettura o hashing del file avviene prima di questo punto.
8. **Caricamento e parsing della ground truth (`load_ground_truth_post_ingestion`):** caricamento dei casi e metadati attesi.
9. **Inizializzazione backend e valutazione casi:** avvio delle inferenze.

I dati di ground truth (`expected_decision`, `expected_evidence_ids`, `human_rationale`, `expected_discovery_topics`) non vengono **mai** serializzati o forniti nei prompt dell'LLM:
- Per i compiti di Topic Detection (Casi 1-5), al modello vengono forniti unicamente la definizione del topic target (`target_topic_id`, `target_topic_label`, `target_topic_description`) e le sezioni di evidenza del documento.
- Per il compito di Topic Discovery (Caso 6), nessun topic, keyword o identificativo atteso viene fornito al modello (scoperta completamente open e non supervisionata).
La ground truth viene impiegata esclusivamente post-hoc per la comparazione e la validazione delle evidenze.

### 10.4 Flusso dei Casi e Strategie Multilingue

Il runner esegue 7 compiti analitici sui 6 casi congelati:
- **Casi 1, 2, 3, 4:** Topic Detection con strategia `DIRECT_MULTILINGUAL`.
- **Caso 5 (Multilingue):** Eseguito due volte in modo indipendente per consentire il confronto metodologico:
  - `CASE_5_MULTILINGUAL_DIRECT`: inferenza diretta `DIRECT_MULTILINGUAL`.
  - `CASE_5_MULTILINGUAL_TRANSLATE_FIRST`: pipeline a due stadi con traduzione preliminare delle evidenze (`EvidenceTranslator` → `EvidenceTranslationResult` → `TopicDetectionAnalyzer`). Gli `evidence_id` continuano a fare riferimento alle evidenze originali immutabili (la traduzione è derivata). Latenza e token usage della traduzione vengono tracciati separatamente.
- **Caso 6 (Open Topic Discovery):** Esecuzione non supervisionata con classificazione `QUALITATIVE / POST-HOC`.

### 10.5 Policy di Exact Match, Preservazione Model ID e Assenza di Retry

- **Exact Model ID Match & Preservazione Telemetria:** Il model ID restituito dal server cloud deve coincidere esattamente con il `model_id` richiesto (`EXACT MODEL ID MATCH`). Se il server restituisce un modello differente (es. fallback silenzioso del gateway o versione datata), la richiesta viene considerata `FAILED` e solleva l'eccezione tipizzata `AiModelMismatchError(message, requested_model, returned_model)`. Il runner registra fedelmente:
  - `model_id_requested = <valore richiesto>`
  - `model_id_returned = exc.returned_model` (oppure `null` se assente)
  conservando l'identificativo effettivo del modello restituito dal provider (NON la stringa generica `"MISMATCH"`), per garantire piena auditabilità scientifica.
- **Zero Automatic Retry:** Nessun retry automatico in caso di errore HTTP, timeout, rate limit o malformazione JSON. Ogni fallimento viene registrato fedelmente nel report.

### 10.6 Validazione Rigorosa Evidenze, Metrica Non-Vacua e Sicurezza Segreti

- **Secret-Safe URL Validation:** La funzione `_validate_remote_url()` impone HTTPS, hostname obbligatorio e rifiuta tassativamente credenziali embedded, query parameters (`?...`) e fragment (`#...`). I messaggi di eccezione non echoano mai `parsed.query`, `parsed.fragment` né valori di token o credenziali.
- **Sanitizzazione Errori CLI & Report:** Nel `main()` e nei serializzatori, qualsiasi messaggio di errore o eccezione generica viene filtrato tramite `_sanitize_output_text()` per rimuovere qualsiasi occorrenza dell'API key o token Bearer. L'API key non compare mai su `stdout`, `stderr`, report JSON o Markdown.
- **Metrica Citazioni Evidenze Invalide Significativa:** Il validatore strutturato distingue gli errori di allucinazione/citazione tramite la classe dedicata `AiInvalidEvidenceCitationError(AiStructuredOutputError)`. Citazioni di ID inesistenti o inventati (es. `INVENTED_ID_999`) sollevano questa eccezione e vengono conteggiate nella metrica `invalid_evidence_citation_count >= 1`, oltre che nella metrica generale `structured_output_failure_count`. Nessuna correzione euristica, typo-fix o fuzzy matching viene applicata.

### 10.7 Formato Output, Metriche e Distinzione CLI Success vs Failure

I risultati vengono salvati nella directory `output/cloud_e2e/` (inclusa in `.gitignore`):
- `cloud-e2e-result.json`: report strutturato machine-readable con metadati per ciascun caso (latenza, token usage, decisioni, evidenze, modello richiesto/restituito, prompt version, errori).
- `cloud-e2e-report.md`: report riepilogativo leggibile con tabella dei casi, breakdown delle decisioni (PRESENT, ABSENT, UNCERTAIN) e metriche di accuratezza tecnica.

**Distinzione Esecuzione Completata vs Successo Analitico nella CLI:**
Il runner separa concettualmente il completamento dell'esecuzione del programma dal successo dei compiti analitici:
- Se tutti i casi di Topic Detection sono completati con successo e Topic Discovery ha status `SUCCESS`, la CLI stampa:
  `PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON SUCCESSO`
- Se sono presenti casi falliti (`failed_detection_cases > 0` o Topic Discovery con status `FAILED`), la CLI stampa:
  `PRELIMINARY CLOUD E2E BENCHMARK COMPLETATO CON FAILURE REGISTRATE`
  evitando categoricamente false dichiarazioni di successo all'operatore forense.

Dato il campione deliberatamente compatto (6 casi / 7 run), il report classifica esplicitamente i risultati come **TECHNICAL VALIDATION BENCHMARK** e non come inferenza statistica generale.

### 10.8 Distinzione tra Richieste di Rete, Inferenze ed Errori Decisionali

Il runner adotta una rigorosa tripartizione delle metriche di esecuzione:
1. **CLOUD API REQUESTS SENT:** Numero totale di tentativi di chiamata HTTP avviati verso l'endpoint remoto.
2. **LLM INFERENCES COMPLETED:** Numero di richieste accettate dal server con codice HTTP 200 per cui il modello ha effettivamente eseguito l'inferenza (escludendo errori 4xx/5xx o timeout di rete).
3. **MODEL OUTPUTS RECEIVED:** Numero di risposte in cui il payload restituito è stato validato e decodificato (in assenza di model mismatch o malformazioni di protocollo).

Questa distinzione impedisce qualsiasi ambiguità interpretativa:
- Se il provider rigetta il payload (es. HTTP 400 Bad Request), l'evento viene classificato come **EXECUTION FAILURE** (`AiBackendRequestError`), non come errore analitico o fallimento decisionale del modello.
- Se il modello completa l'inferenza e produce una decisione divergente dalla ground truth, l'evento viene classificato come **MODEL DECISION ERROR**.
- La metrica `detection_completion_rate = completed_detection_cases / total_detection_cases` misura il successo tecnico dell'esecuzione.
- La metrica `decision_accuracy = correct_decisions / completed_detection_cases` misura la qualità analitica del modello esclusivamente sui casi validamente completati; qualora `completed_detection_cases == 0`, la metrica è formalmente non definita (`null` in JSON, `N/A` nei report Markdown), evitando la rappresentazione fuorviante di `0.0%`.

### 10.9 Evoluzione del Protocollo: Protocol V1 vs Protocol V2

- **Protocol V1 (Baseline con Seed obbligatorio):**
  - Configurazione: `seed = 42` inviato nel payload HTTP di chat completion.
  - Esito con Google Gemini Developer API (`/v1beta/openai`): il provider ha respinto tutte le richieste con HTTP 400 (`Invalid JSON payload received. Unknown name "seed": Cannot find field.`).
  - Bilancio V1: `CLOUD API REQUESTS SENT: 7`, `LLM INFERENCES COMPLETED: 0`, `MODEL OUTPUTS RECEIVED: 0`. Nessun retry è stato eseguito, preservando la run come evidenza tecnica oggettiva di incompatibilità del parametro.
- **Protocol V2 (Gemini Provider Compatibility — Seed Provider-Optional):**
  - Modifica metodologica autorizzata: parametro `seed` reso facoltativo (`--seed none` → `seed = None`), con omissione della proprietà `"seed"` dal payload JSON quando non supportata dal provider.
  - Registrazione metadati: `seed_requested: null`, `seed_provider_status: "UNSUPPORTED_OMITTED"`.
  - Invarianza del protocollo: immutati dataset, ground truth, prompt versions, schema, endpoint, temperature (`0.0`), max tokens (`1024`) e assenza categorica di retry automatici.
  - Benchmark locale: il benchmark su LM Studio continuerà a utilizzare il seed deterministico (`42`) laddove supportato dal runtime locale.

### 10.10 PROTOCOL V3 — RATE-LIMIT-AWARE EXECUTION (Pacing Preventivo a 15 Secondi)

- **Contesto Operativo e Limiti Provider Osservati:**
  Durante l'esecuzione sperimentale di Protocol V2 è stato osservato un burst di traffico (picco di circa 6 richieste/minuto) che ha ecceduto i limiti di servizio della Google Gemini Developer API associati al progetto/modello (`gemini-3.8-flash` su tier free/developer):
  - **RPM LIMIT:** 5 requests per minute
  - **TPM LIMIT:** circa 250.000 input tokens per minute
  - **RPD LIMIT:** 20 requests per day

- **Distinzione Tecnica tra HTTP 429 e HTTP 503:**
  È fondamentale distinguere la natura concettuale e infrastrutturale dei due errori osservati durante le sessioni sperimentali:
  - **`HTTP 429 Too Many Requests`:** Execution failure compatibile con il superamento dei vincoli di rate limit o quota (RPM/TPM).
  - **`HTTP 503 Service Unavailable`:** Execution failure dovuta a indisponibilità infrastrutturale transitoria o carico temporaneo del cluster provider ("This model is currently experiencing high demand").
  Il superamento della frequenza di picco durante Protocol V2 è compatibile con l'insorgenza di HTTP 429, ma non causa direttamente HTTP 503. I risultati di Protocol V3 hanno confermato empiricamente questa distinzione: l'adozione del pacing preventivo a 15.0s ha azzerato completamente gli errori HTTP 429, mentre errori HTTP 503 sono rimasti presenti a causa delle fluttuazioni di carico dei server remoti di Google.

- **Design del Pacing Preventivo (Interval >= 15.0s):**
  Protocol V3 introduce una policy di rate limiting preventivo gestita transparentemente dal wrapper `_ExperimentClientWrapper`.
  - **Algoritmo di Start-to-Start Pacing:** Il timer vincola il tempo di inizio di ciascuna richiesta consecutiva rispetto alla precedente:
    $$\text{START}(\text{request } N+1) - \text{START}(\text{request } N) \ge 15.0\text{ secondi}$$
    Calcolato mediante `time.monotonic()` (immune da derive del clock di sistema).
  - **Funzionamento:** La prima richiesta parte immediatamente a $t=0$. Se prima dell'avvio della richiesta successiva il tempo trascorso dall'avvio della precedente è inferiore a 15.0s, il sistema sospende preventivamente il thread per la differenza residua (`15.0 - elapsed`). Se una richiesta ha richiesto un tempo di esecuzione superiore a 15 secondi, non viene introdotta alcuna attesa superflua.
  - **Copertura Totale:** Il pacing si applica centralmente a ogni invocazione di `chat_completion()`, coprendo uniformemente Topic Detection standard, la fase intermedia tra traduzione e detection in `TRANSLATE_FIRST`, e Topic Discovery.
  - **Tasso Massimo Teorico:** Con un intervallo di 15.0 secondi, il throughput massimo è limitato rigorosamente a $\le 4\text{ RPM}$, restando sempre al di sotto della soglia limite di 5 RPM.

- **Distinzione Concettuale e Operativa: Pacing Preventivo vs Retry:**
  - Il pacing è **preventivo e a priori**: cadenza le richieste prima di inviarle per non saturare i rate limiter del provider.
  - Il pacing **NON è un retry**: nessuna richiesta viene mai ripetuta in caso di errore.
  - Se un tentativo riceve un codice `HTTP 429`, `HTTP 503`, timeout di rete o errore di validazione dello schema JSON, l'evento viene registrato irreversibilmente come `FAILED`. Non viene attuato alcun exponential backoff o replay del singolo caso. Una richiesta equivale tassativamente a un solo tentativo (1:1).

- **Preservazione degli Artefatti Sperimentali V1 e V2:**
  Protocol V1 e Protocol V2 rimangono storicizzati e congelati nelle rispettive directory (`output/cloud_e2e/` e `output/cloud_e2e_v2/`). Protocol V3 scrive i propri artefatti esclusivamente in `output/cloud_e2e_v3/` (`cloud-e2e-result.json` e `cloud-e2e-report.md`), garantendo la tracciabilità scientifica dell'evoluzione sperimentale.

- **Invarianza Semantica del Benchmark:**
  Dataset (`messages.json`), ground truth (`ground_truth.json`), prompt versions (`topic_detection_v1`, `evidence_translation_v1`, `topic_discovery_v1`), temperature (`0.0`), max tokens (`1024`), timeout (`120s`) e modalità JSON (`json_schema`) rimangono rigorosamente identici a Protocol V2. L'unica variazione metodologica è il pacing preventivo a 15.0 secondi.


## 11. CLOUD E2E VALIDATION — FINAL STATUS

Con l'accettazione di Protocol V3 da parte del supervisore, la fase sperimentale preliminare su cloud è formalmente **CHIUSA e CONGELATA** (`CLOUD E2E PHASE: CLOSE / FREEZE`). Nessuna ulteriore chiamata cloud o riesecuzione di benchmark è prevista né autorizzata.

### 11.1 Quadro Comparativo dei Risultati (V1, V2, V3)

| Dimensione | Protocol V1 (Seed Requisito) | Protocol V2 (Seed Omitted) | Protocol V3 (Rate-Limit Pacing 15s) |
|---|---|---|---|
| **Data Run** | 2026-10-03 | 2026-10-03 | 2026-10-04 |
| **API Requests Sent** | 7 | 8 | 8 |
| **LLM Inferences Completed** | 0 | 3 | 5 |
| **Model Outputs Received** | 0 | 3 | 5 |
| **Detection Cases Completed** | 0 / 6 (0.0%) | 2 / 6 (33.3%) | 3 / 6 (50.0%) |
| **Detection Corrette** | 0 | 2 / 2 | 2 / 3 |
| **Decision Accuracy (Casi Completati)**| N/A (`null`) | 100.0% | **66.7%** (2 su 3 completati) |
| **CASE 1 (Explicit Present)** | HTTP 400 (Seed error) | SUCCESS (`PRESENT`, corretto) | SUCCESS (`PRESENT`, corretto) |
| **CASE 2 (Implicit Present)** | HTTP 400 (Seed error) | FAILED (HTTP 503) | FAILED (HTTP 503) |
| **CASE 3 (Ambiguous Uncertain)**| HTTP 400 (Seed error) | FAILED (HTTP 503) | SUCCESS (`PRESENT`, **MODEL DECISION ERROR**) |
| **CASE 4 (Absent)** | HTTP 400 (Seed error) | SUCCESS (`ABSENT`, corretto) | SUCCESS (`ABSENT`, corretto) |
| **CASE 5 Direct Multilingual** | HTTP 400 (Seed error) | FAILED (HTTP 503) | FAILED (HTTP 503) |
| **CASE 5 Translate Step** | Non eseguito | SUCCESS (Traduzione fedele) | SUCCESS (Traduzione fedele) |
| **CASE 5 Translate Detection** | Non eseguito | FAILED (HTTP 429) | FAILED (HTTP 503) |
| **CASE 6 Topic Discovery** | Non eseguito | FAILED (HTTP 429) | SUCCESS (6 topic pertinenti, 100% validi) |
| **Citazioni Evidenze Invalide** | 0 | 0 | 0 |
| **Structured Output Failures** | 0 | 0 | 0 |
| **Model Mismatch** | 0 | 0 (`gemini-3.8-flash` exact) | 0 (`gemini-3.8-flash` exact) |
| **HTTP 429 Errors** | 0 | 2 | **0** (completamente debellato) |
| **HTTP 503 Errors** | 0 | 3 | 3 |
| **Pacing Minimo Osservato** | Nessuno (burst) | Nessuno (burst) | **15.000s** (media 15.001s) |
| **Retry Eseguiti** | 0 | 0 | 0 |

### 11.2 Analisi Semantica del Caso 3 (Model Decision Error)

Nel Caso 3 (`CASE_3_AMBIGUOUS_UNCERTAIN`), a fronte di un'evidenza deliberatamente ambigua in cui la ground truth metodologicamente corretta è `UNCERTAIN`, il modello `gemini-3.8-flash` ha risposto `PRESENT`, motivando la scelta sulla base della borsa capiente, della riservatezza e della discussione a voce dei dettagli.
Tale esito è classificato rigorosamente come **MODEL DECISION ERROR** (errore analitico del modello) e **NON** come fallimento tecnico o di esecuzione. Esso evidenzia la naturale tendenza del modello commerciale a ricercare pattern di certezza anche in presenza di presupposti indiziari insufficienti.

### 11.3 Conclusioni Metodologiche della Validazione Cloud Preliminare

La Preliminary Cloud E2E Validation ha raggiunto pienamente i propri scopi ingegneristici e metodologici, dimostrando che l'architettura ChatAnalysis è capace di:
1. Invocare un LLM remoto reale attraverso standard aperti compatibili OpenAI;
2. Ottenere output strutturato conforme a JSON Schema con validazione deterministica lato Python;
3. Preservare l'integrità ontologica e la provenienza delle evidenze forensi (`invalid_evidence_citations = 0`);
4. Verificare in modo rigoroso l'identità del modello remoto (`EXACT MODEL ID MATCH`);
5. Separare concettualmente e operativamente le *Execution Failures* (HTTP 429/503) dai *Model Decision Errors*;
6. Eseguire Topic Detection reale con corretta discriminazione dei negativi (Caso 4) e positivi espliciti (Caso 1);
7. Eseguire Topic Discovery reale non supervisionata con estrazione coerente di 6 topic tematici (Caso 6);
8. Eseguire la fase di traduzione forense preservando le chiavi di evidenza originarie;
9. Operare esclusivamente su dataset sintetico congelato senza mai esporre dati forensi reali né credenziali.

**Limiti e Delimitazioni Scientifiche:**
- **Non tutti i casi sono stati completati:** La persistenza di errori `HTTP 503 Service Unavailable` ha impedito il completamento dei Casi 2, 5-Direct e 5-Translate Detection.
- **Translate-First parzialmente validato:** La fase di traduzione strutturata è stata validata con pieno successo (Step 1), mentre il passaggio finale di detection sul testo tradotto (Step 2) non è stato concluso a causa dell'indisponibilità del server remoto.
- **Accuratezza Non Generalizzabile:** L'accuratezza riscontrata è rigorosamente del **66.7% sui 3 casi di Topic Detection validamente completati** (con un completion rate del 50.0%), su un campione di validazione tecnica deliberatamente compatto. Non costituisce né deve essere presentata come una valutazione statistica generale del modello `gemini-3.8-flash`.

La vulnerabilità dei provider commerciali cloud a indisponibilità di cluster e politiche esterne di rate limiting conferma in modo definitivo la scelta metodologica della tesi: **il benchmark sperimentale finale sarà condotto interamente in locale (LM Studio su loopback) con modelli open-weight dedicati (Qwen, Llama, DeepSeek) su hardware compatibile.**


