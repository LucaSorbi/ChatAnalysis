# Local AI Analysis Layer — Architectural & Technical Design
## Local LLM Integration, LM Studio Adapter, Topic Detection, Topic Discovery, Multilingual Strategies, and Model Comparison Harness

---

## 1. Ambito e Posizionamento nella Pipeline Forense

Il package `ai` costituisce il layer di intelligenza artificiale locale della pipeline forense:

$$\begin{aligned}
\text{DATI ORIGINALI} &\longrightarrow \text{IMPORTER} \longrightarrow \text{RawRecord} \\
&\longrightarrow \text{VALIDAZIONE} \longrightarrow \text{NORMALIZZAZIONE} \\
&\longrightarrow \text{ENTITY RESOLUTION} \longrightarrow \text{UnifiedMessage} \\
&\longrightarrow \text{MediaResolver} \longrightarrow \text{STT / OCR / Vision} \\
&\longrightarrow \text{MessageEvidenceBundle} \longrightarrow \text{ConversationEvidenceDocument} \\
&\longrightarrow \mathbf{Local\ LLM\ (LM\ Studio\ /\ Fake\ Client)} \longrightarrow \begin{cases} \text{TopicDetectionResult} \\ \text{TopicDiscoveryResult} \\ \text{EvidenceTranslationResult} \end{cases}
\end{aligned}$$

### 1.1 Principi Fondamentali e Confini Invalicabili
1. **Esecuzione Esclusivamente Locale e Sovrana**:
   - Tutte le interazioni con i modelli di linguaggio avvengono esclusivamente tramite interfaccia di loopback locale (`127.0.0.1`, `localhost`, `::1`).
   - È categoricamente vietata qualsiasi chiamata verso provider cloud esterni (OpenAI, Anthropic, HuggingFace, Google Gemini) o trasmissione telemetrica di dati probatori.
2. **Nessun Download Automatico di Modelli Pesanti**:
   - Il software non scarica automaticamente file di pesi o modelli (GGUF, binari, pesi HF).
   - Non avvia processi server esterni né installa automaticamente LM Studio.
   - La suite di test standard (`pytest`) esegue tutte le verifiche tramite `FakeLocalLlmClient` e mock di rete, garantendo esecuzione veloce e offline.
3. **Integrità Probatoria Assoluta e Non-Contaminazione**:
   - Il testo originale (`UnifiedMessage.text_content`) e i testi estratti dalle evidenze multimodali (`TextEvidenceSection.text`) sono rigorosamente **immutabili**.
   - Nessuna operazione di analisi semantica o traduzione altera o sovrascrive il record originale.
4. **Esclusioni di Fase**:
   - Nessun database vettoriale o embedding neurale (FAISS, Chroma, Qdrant).
   - Nessuna ricerca semantica o RAG non strutturato.
   - Nessuna interfaccia grafica o applicazione Streamlit.

---

## 2. Modello dei Dati AI (`ai.models`)

Tutte le strutture dati del package sono progettate con immutabilità profonda (`@dataclass(frozen=True)` e tuple immutabili):

### 2.1 Identificazione dei Modelli e Famiglie
- **`ModelFamily`**: Enumerazione standardizzata delle famiglie di modelli approvate per la sperimentazione di tesi:
  - `LLAMA` (es. Llama-3.2-1B-Instruct, Llama-3.2-3B-Instruct)
  - `QWEN` (es. Qwen2.5-0.5B-Instruct, Qwen2.5-1.5B-Instruct, Qwen2.5-3B-Instruct)
  - `DEEPSEEK` (es. DeepSeek-R1-Distill-Qwen-1.5B)
  - `OTHER` (altre architetture locali caricate in LM Studio)
- **`ExperimentModelSpec`**: Specifica descrittiva del modello con identificatore (`model_id`), famiglia, quantizzazione dichiarata (es. `Q4_K_M`), dimensione stimata dei parametri e contesto massimo supportato.

### 2.2 Aggregazione Documentale: `ConversationEvidenceDocument`
Rappresenta l'unità di contesto probatorio sottoposta all'analisi dell'LLM:
- **Unicità delle Evidenze (G1)**: In `__post_init__`, viene verificata l'assoluta unicità di ciascun `evidence_id` in tutti i bundle aggregati. Qualsiasi collisione solleva immediatamente `ValueError`.
- **Ambito della Sorgente (G2)**: Se `source_name` è specificato, tutti i messaggi aggregati devono coincidere con tale sorgente (nessuna fusione cross-source implicita).
- **Ambito Chat Dichiarativo (G3)**: Il campo `chat_id` costituisce uno scope dichiarativo fornito dal chiamante; l'architettura non presume alcuna risoluzione cross-source automatica delle chat.

### 2.3 Topic Detection (Specific Topics)
- **`TopicQuery`**: Definizione del tema investigativo cercato (`topic_id`, `label`, `description`).
- **`TopicDecision`**: Esito tri-state rigoroso (`PRESENT`, `ABSENT`, `UNCERTAIN`).
- **`TopicDetectionResult`**: Invarianti di dominio verificate nel costruttore:
  - `PRESENT`: richiede almeno un `evidence_id` valido e unico;
  - `ABSENT`: richiede che `evidence_ids` sia rigorosamente vuoto `()`;
  - `UNCERTAIN`: consente evidenze parziali uniche o lista vuota;
  - `rationale`: stringa non vuota;
  - Rifiuto tassativo di identificatori duplicati.

### 2.4 Topic Discovery (Open Themes)
- **`DiscoveredTopic`**: Singolo tema emergente (`label`, `short_description`, `evidence_ids` non vuota e priva di duplicati).
- **`TopicDiscoveryResult`**: Collezione di temi scoperti (`discovered_topics`), provenance e metadati.

### 2.5 Strategie Linguistiche Multilingue
- **`AnalysisLanguageStrategy`**:
  - `DIRECT_MULTILINGUAL`: L'LLM esamina direttamente i testi nelle loro lingue originali (IT, EN, ES, frammenti misti o gergali). Non accetta oggetti di traduzione derivata.
  - `TRANSLATE_FIRST`: Le evidenze testuali vengono preliminarmente tradotte in una lingua pivot (es. Italiano) tramite `EvidenceTranslator`. Richiede un `EvidenceTranslationResult` completo, validato e congruente per provenance con il documento analizzato. Nessun fallback silenzioso al testo originale è consentito.
- **`EvidenceTranslationResult`**: Mappatura tipizzata tra `evidence_id` originario, `translated_text`, `original_language` preservato dalla sezione originaria e lingua target.

---

## 3. Sicurezza, Threat Modeling e Difesa da Prompt Injection

Nei contesti di analisi forense di chat, il testo dei messaggi estratti è intrinsecamente non fidato e può contenere istruzioni malevole volte a manipolare l'LLM o a falsificare delimitatori testuali.

### 3.1 Incapsulamento con JSON Escaping Deterministico
Il modulo `ai.serializer` adotta una strategia a doppio strato:
1. **Serializzazione JSON dei Dati Probatori**:
   Ciascuna sezione di evidenza viene convertita in un oggetto dati strutturato tramite `json.dumps(..., ensure_ascii=False)`.
   Poiché la serializzazione JSON esegue automaticamente l'escaping di caratteri speciali, apici, ritorni a capo e sequenze di tag, qualsiasi testo malevolo presente nel messaggio (come `=== END FORENSIC EVIDENCE DATA ===` o `</untrusted_data>`) rimane confinato all'interno del valore stringa letterale senza poter interrompere il flusso logico del parser.
2. **Delimitatori di Confine Espliciti**:
   L'intero blocco probatorio è racchiuso tra delimitatori univoci:
   `=== BEGIN FORENSIC EVIDENCE DATA (UNTRUSTED RAW JSON) ===` e `=== END FORENSIC EVIDENCE DATA ===`.
3. **Direttiva di Sicurezza di Sistema Inviolabile (`SYSTEM_SECURITY_DIRECTIVE`)**:
   Ogni interazione con il modello include nel prompt di sistema una direttiva esplicita che impone all'assistente di trattare il contenuto del blocco come dati probatori passivi da analizzare e mai come comandi operativi.

---

## 4. Architettura Client LM Studio e Loopback Security (`ai.lmstudio`)

### 4.1 Client HTTP Standard Library
Implementato interamente con i moduli nativi `urllib.request`, `urllib.error` e `http.client` di Python 3, evitando dipendenze di terze parti non necessarie.

### 4.2 Restrizione Forzata su Loopback Locale
Il client convalida tassativamente l'URL di base fornito contro `{"127.0.0.1", "localhost", "::1", "[::1]"}`.

### 4.3 Divieto di Auto-Selezione, Strict Model Identity e Tassonomia degli Errori
- **Model ID Esplicito**: `chat_completion()` richiede obbligatoriamente un `model_id`. Se omesso, solleva `AiModelNotSpecifiedError`. Il client non seleziona mai arbitrariamente il primo modello disponibile.
- **Strict Model Identity from Backend Response**: `raw_json` deve essere un dict contenente un campo `model` valido (stringa non vuota). Se assente, nullo o non-stringa, solleva `AiBackendProtocolError`. Nessun fallback automatico a `target_model`. `LlmCompletionResponse.model` riflette rigorosamente il modello identificato dal backend, rendendo effettivo il controllo `AiModelMismatchError`.
- **Validazione Rigorosa della Forma OpenAI**: Validazione esplicita della struttura: top-level dict, `choices` lista non vuota, `choices[0]` dict, `choices[0]["message"]` dict, `message["content"]` stringa, `finish_reason` stringa o `None`, `usage` dict opzionale con token numerici interi non negativi (esclusione esplicita di booleani). Qualsiasi violazione HTTP 200 solleva `AiBackendProtocolError`.
- **Gestione `/api/v1/models`**: JSON malformato solleva `AiBackendProtocolError` e NON attiva fallback verso `/v1/models`. Fallback ammesso unicamente per codici HTTP 404, 405, 501.
- **Tassonomia Timeout e Connettività**:
  - `AiBackendTimeoutError`: Timeout effettivo di rete o socket durante handshake/lettura, mappato in `BACKEND_TIMEOUT` nella tassonomia errori di benchmark;
  - `AiBackendUnavailableError` / `LmStudioUnavailableError`: Server offline, connessione rifiutata o host non raggiungibile;
  - `AiBackendProtocolError`: Risposta HTTP 200 malformata o semanticamente incompatibile con il protocollo OpenAI;
  - `AiBackendRequestError`: Codici di stato HTTP 4xx o 5xx;
  - `AiStructuredOutputError`: Output non conforme al JSON Schema atteso;
  - `AnalysisInputTooLargeError`: Superamento del budget massimo per chunk o bundle tradotto.

---

## 5. Output Strutturato e Validazione Semantica (`ai.structured`)

Le richieste di completamento specificano `response_format={"type": "json_schema", "json_schema": {"strict": True, ...}}`.
Sul lato Python, la risposta viene sottoposta a una validazione deterministica rigorosa:
1. **Parser JSON**: `parse_and_validate_json()` cattura unicamente `json.JSONDecodeError`.
2. **Forma Esatta dell'Oggetto**: Rifiuto tassativo di chiavi extra (`additionalProperties: False` verificato in Python) e controllo rigoroso dei tipi senza coercizioni forzate (es. numeri interi non vengono convertiti in stringhe).
3. **Topic Detection**:
   - `PRESENT`: almeno 1 `evidence_id`, tutti validi, nessun duplicato;
   - `ABSENT`: `evidence_ids` rigorosamente vuoto `[]`;
   - `UNCERTAIN`: `evidence_ids` validi e unici;
   - `rationale`: stringa non vuota.
4. **Topic Discovery**:
   - Rifiuto categorico di silent truncation: se i topic restituiti superano `max_topics`, solleva `AiStructuredOutputError`.
   - Controllo rigoroso di chiavi, etichette e citazioni uniche.
5. **Traduzione**:
   - Ogni evidenza attesa deve essere tradotta esattamente una volta;
   - Preservazione del metadato `original_language`;
   - Rigetto di traduzioni con testo vuoto se il testo originario non era vuoto.

---

## 6. Chunking Documentale e Aggregazione Multi-Chunk (`ai.chunking`)

- **Nessuna Falsa Frammentazione**: Il parametro `allow_fragmentation` è stato rimosso; se una singola sezione o la somma delle sezioni di un bundle atomico supera `max_characters`, viene sollevata `AnalysisInputTooLargeError`.
- **Budget di Caratteri**: `max_characters` rappresenta una stima prudenziale e deterministica di caratteri, non un conteggio di token esatto.
- **Verifica Espansione Traduzione (Zero Fallback)**: `verify_translated_document_size()` è integrata prima di ogni completamento LLM in modalità `TRANSLATE_FIRST` sia in `TopicDetectionAnalyzer` sia in `TopicDiscoveryAnalyzer`. Se manca la traduzione per un'evidenza attesa, solleva errore. Se una traduzione è vuota ma validamente ammessa, NON viene sostituita automaticamente con il testo originale (nessun fallback silenzioso). Se una singola sezione tradotta o la somma del bundle tradotto supera il limite, solleva `AnalysisInputTooLargeError`.
- **Aggregazione Consapevole dei Guasti (`ChunkAnalysisOutcome`)**:
  - `PRESENT` è confermato se almeno un chunk ha rilevato evidenze con successo.
  - `ABSENT` è ammesso **SOLTANTO** se tutti i chunk previsti sono stati completati con successo e sono risultati `ABSENT`.
  - In presenza di chunk falliti o mancanti, l'aggregatore conclude obbligatoriamente `UNCERTAIN` spiegando la parzialità dell'analisi.

---

## 7. Model Discovery e Semantica delle Famiglie (`ai.discovery`)

- **Stato `UNVERIFIED` se Server Offline**: Se LM Studio non risponde o il listing fallisce, lo stato delle famiglie è `UNVERIFIED`. `missing_families` è vuoto per evitare falsi allarmi su famiglie assenti quando il server è semplicemente spento.
- **Mappatura Famiglie**:
  - `LLAMA`: pattern `llama`, `alpaca`, `vicuna`;
  - `DEEPSEEK`: pattern `deepseek` (valutato **prima** di Qwen per classificare correttamente i modelli distillati `deepseek-r1-distill-qwen`);
  - `QWEN`: pattern `qwen`.
- **JIT Loading**: La lista in `/v1/models` rappresenta i modelli registrati nel server che possono essere caricati Just-In-Time nella memoria volatile.

---

## 8. Harness di Benchmark Scientifico (`ai.benchmark`, `ai.experiment`)

### 8.1 Metodologia di Valutazione e Semantica delle Latenze
I risultati sono strutturati per **MODEL × LANGUAGE × STRATEGY**:
- Scenari controllati in Italiano (`scenario_it_travel`), Inglese (`scenario_en_work`), Spagnolo (`scenario_es_food`) e misto (`scenario_mixed_sport`).
- **Semantica Rigorosa della Latenza**:
  - `translation_stage_latency_seconds`: latenza effettiva della traduzione eseguita una tantum a livello di scenario;
  - `analysis_latency_seconds`: latenza di inferenza della specifica query;
  - `cold_end_to_end_latency_seconds`: somma della latenza di traduzione dell'intero scenario e dell'analisi (scenario worst-case / prima query);
  - `amortized_end_to_end_latency_seconds`: latenza con quota amortizzata di traduzione ripartita equamente tra le query che condividono la traduzione dello scenario;
  - Il confronto principale del benchmark si basa su `cold_end_to_end_latency_seconds` per query isolate e `amortized_end_to_end_latency_seconds` per scenari multi-query, prevenendo false dichiarazioni di fairness.
- Metriche Topic Detection:
  - `attempted_queries`, `completed_valid_queries`, `failed_queries`, `completion_rate`;
  - `decision_accuracy`, `precision`, `recall`, `f1_score` calcolate sulle sole query valide completate;
  - `invalid_evidence_references` e relativo tasso;
  - `detailed_runs`: metadati granulari per ogni singola esecuzione (nessun testo di chat, prompt grezzo o segreto salvato).
- Metriche Topic Discovery:
  - `invalid_evidence_reference_count` e `evidence_reference_validity_rate`;
  - `structured_output_failure_count`, `model_mismatch_count`, `error_counts`;
  - `manual_review_status` (inizializzato a `"NOT_REVIEWED"`, nessuna metrica supervisionata fittizia).

### 8.2 Distinzione Modalità, Validazione Famiglia e CLI Runner
- `FAKE_HARNESS_VALIDATION`: Esecuzione deterministica con `FakeLocalLlmClient` per CI e test unitari.
- `REAL_MODEL_BENCHMARK`: Esecuzione empirica con `ai/experiment.py`, protetta da opt-in guard (`RUN_LM_STUDIO_BENCHMARK=1`).
- **Validazione Famiglia**: Verifica automatica della coerenza tra `--family` e la famiglia inferita da `infer_model_family(model_id)`; discrepanze bloccano l'esecuzione con errore esplicito.
- **Metadati Dichiarati vs Osservati**: Distinzione rigorosa tra `declared_model_metadata` (parametri CLI) e `observed_model_metadata` (interrogati via `get_models_detailed()`), evitando attribuzioni arbitrarie.
- **Propagazione Errori di Programmazione**: Nessun mascheramento globale con `except Exception`; bug interni (`AttributeError`, `TypeError`, ecc.) propagano per garantire trasparenza diagnostica.

