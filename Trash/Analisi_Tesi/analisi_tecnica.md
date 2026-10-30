# Analisi Semantica Locale di Conversazioni Forensi
## Documento Tecnico di Progetto — Tesi di Laurea

> **Versione**: 0.1 — Bozza architetturale iniziale  
> **Data**: Settembre 2026  
> **Stato**: In attesa di approvazione e analisi dei dati reali

---

## Indice

1. [A — Descrizione del problema](#a--descrizione-del-problema)
2. [B — Requisiti funzionali](#b--requisiti-funzionali)
3. [C — Requisiti non funzionali](#c--requisiti-non-funzionali)
4. [D — Architettura proposta](#d--architettura-proposta)
5. [E — Pipeline completa](#e--pipeline-completa)
6. [F — Strategia di pulizia e normalizzazione](#f--strategia-di-pulizia-e-normalizzazione)
7. [G — Strategia audio e speech-to-text](#g--strategia-audio-e-speech-to-text)
8. [H — Strategia immagini e OCR](#h--strategia-immagini-e-ocr)
9. [I — Strategia multilingue](#i--strategia-multilingue)
10. [J — Strategia embeddings](#j--strategia-embeddings)
11. [K — Strategia topic detection](#k--strategia-topic-detection)
12. [L — Strategia ricerca semantica](#l--strategia-ricerca-semantica)
13. [M — Strategia rilevamento argomento specifico](#m--strategia-rilevamento-argomento-specifico)
14. [N — Tecnologie candidate e confronto](#n--tecnologie-candidate-e-confronto)
15. [O — Requisiti hardware](#o--requisiti-hardware)
16. [P — Struttura del progetto](#p--struttura-del-progetto)
17. [Q — Metodologia di valutazione](#q--metodologia-di-valutazione)
18. [R — Roadmap](#r--roadmap)
19. [S — Proof of Concept](#s--proof-of-concept)
20. [T — Rischi tecnici](#t--rischi-tecnici)

---

## A — Descrizione del problema

### Contesto

Nell'ambito delle indagini forensi digitali, le acquisizioni di dispositivi mobili e sistemi informatici producono grandi quantità di dati di comunicazione: messaggi di chat, audio, immagini, documenti allegati. Strumenti come Cellebrite UFED consentono l'estrazione di questi dati in formati strutturati (UFDR, XML, CSV, SQLite, JSON).

Il problema centrale è che l'**analisi semantica** di queste conversazioni viene ancora eseguita manualmente dagli investigatori, oppure affidata a strumenti cloud che sollevano gravi problemi di privacy, catena di custodia e ammissibilità delle prove.

### Problema specifico

Non esiste oggi uno strumento open, locale, modulare e scientificamente valutabile capace di:

1. Importare conversazioni da diversi formati forensi
2. Pulire e normalizzare dati eterogenei
3. Trascrivere audio, estrarre testo da immagini
4. Rilevare automaticamente gli argomenti principali
5. Effettuare ricerca semantica multilingue
6. Classificare la presenza di un argomento specifico
7. Restituire risultati verificabili, tracciabili e collegati ai dati originali
8. Operare interamente offline, senza dipendere da API cloud

### Obiettivo della tesi

Progettare, implementare e valutare sperimentalmente un sistema modulare e locale per l'analisi semantica di conversazioni forensi, con particolare attenzione a:

- **Tracciabilità**: ogni risultato è ricondotto al dato originale
- **Privacy by design**: nessun dato lascia il sistema locale
- **Valutazione scientifica**: metriche riproducibili per ogni componente
- **Modularità**: ogni componente è sostituibile indipendentemente

---

## B — Requisiti funzionali

### B.1 — Importazione

| ID | Requisito |
|----|-----------|
| F-IMP-01 | Il sistema deve importare conversazioni da almeno un formato forense (es. export CSV/JSON/XML da Cellebrite) |
| F-IMP-02 | Il sistema deve supportare l'aggiunta di nuovi importer senza modificare il core |
| F-IMP-03 | Il sistema deve associare i file multimediali ai messaggi corrispondenti |
| F-IMP-04 | Il sistema deve conservare i dati originali separati da quelli elaborati |
| F-IMP-05 | Il sistema deve mantenere l'identificativo univoco del dato di origine |

### B.2 — Pulizia e normalizzazione

| ID | Requisito |
|----|-----------|
| F-NRM-01 | Rimozione di duplicati mantenendo il record originale |
| F-NRM-02 | Gestione di campi mancanti con valori sentinel identificabili |
| F-NRM-03 | Normalizzazione encoding (UTF-8) |
| F-NRM-04 | Conservazione sempre del testo originale affianco alla versione normalizzata |
| F-NRM-05 | Marcatura di messaggi eliminati/inoltrati/di sistema quando identificabili |
| F-NRM-06 | Validazione e correzione dei timestamp |
| F-NRM-07 | Il contenuto semantico originale NON deve mai essere alterato |

### B.3 — Trascrizione audio

| ID | Requisito |
|----|-----------|
| F-STT-01 | Trascrizione locale dei messaggi vocali |
| F-STT-02 | Rilevamento automatico della lingua |
| F-STT-03 | Conservazione dell'audio originale |
| F-STT-04 | Associazione trascrizione ↔ messaggio originale |
| F-STT-05 | Gestione errori di trascrizione con flag di confidenza |
| F-STT-06 | Scelta del modello configurabile (velocità vs accuratezza) |

### B.4 — Analisi immagini

| ID | Requisito |
|----|-----------|
| F-OCR-01 | Rilevamento della presenza di testo nelle immagini |
| F-OCR-02 | Estrazione OCR locale del testo presente |
| F-OCR-03 | Score di confidenza per ogni estrazione |
| F-OCR-04 | Conservazione dell'immagine originale |
| F-OCR-05 | Associazione testo OCR ↔ immagine ↔ messaggio originale |
| F-OCR-06 | Supporto a screenshot, testo piccolo, più lingue |

### B.5 — Analisi semantica

| ID | Requisito |
|----|-----------|
| F-SEM-01 | Topic detection automatica: nome, descrizione, rilevanza, messaggi associati, intervallo temporale, confidenza |
| F-SEM-02 | Distinzione tra: menzione, riferimento, discussione, argomento ricorrente, argomento centrale |
| F-SEM-03 | Rilevamento sottoargomenti |
| F-SEM-04 | Ricerca semantica in linguaggio naturale |
| F-SEM-05 | Il sistema deve trovare messaggi semanticamente rilevanti anche senza corrispondenza testuale letterale |
| F-SEM-06 | Il sistema deve mostrare contesto precedente e successivo al risultato rilevante |
| F-SEM-07 | Classificazione della presenza di un argomento specifico: SÌ / NO / INCERTO |
| F-SEM-08 | Per ogni risultato: confidenza, motivazione, messaggi rilevanti, timestamp, mittente, fonte |

### B.6 — Tracciabilità

| ID | Requisito |
|----|-----------|
| F-TRC-01 | Ogni risultato AI è collegato al messaggio normalizzato che lo ha prodotto |
| F-TRC-02 | Ogni messaggio normalizzato è collegato al record interno |
| F-TRC-03 | Ogni record interno è collegato al dato originale |
| F-TRC-04 | La catena di tracciabilità deve essere completa e verificabile |

### B.7 — Analisi temporale

| ID | Requisito |
|----|-----------|
| F-TMP-01 | Mantenimento ordine cronologico dei messaggi |
| F-TMP-02 | Individuazione del periodo di comparsa di un argomento |
| F-TMP-03 | Individuazione dei periodi con maggiore concentrazione |
| F-TMP-04 | Calcolo della durata e ricorrenza delle discussioni |

---

## C — Requisiti non funzionali

### C.1 — Privacy e sicurezza

| ID | Requisito |
|----|-----------|
| NF-PRV-01 | **Nessun dato** deve essere inviato a servizi cloud |
| NF-PRV-02 | I log non devono contenere contenuti sensibili |
| NF-PRV-03 | I dati originali devono essere separati dai dati derivati |
| NF-PRV-04 | Deve essere possibile cancellare tutti i dati derivati senza alterare gli originali |
| NF-PRV-05 | Le elaborazioni devono essere tracciate (audit log) |

### C.2 — Scalabilità

| ID | Requisito |
|----|-----------|
| NF-SCL-01 | Il sistema deve gestire conversazioni con milioni di messaggi |
| NF-SCL-02 | L'analisi deve essere incrementale e riprendibile |
| NF-SCL-03 | Gli embedding e i risultati intermedi devono essere cacheable |
| NF-SCL-04 | Non si deve mai caricare l'intera conversazione in memoria contemporaneamente |

### C.3 — Modularità

| ID | Requisito |
|----|-----------|
| NF-MOD-01 | Ogni componente deve poter essere sostituito senza riscrivere il sistema |
| NF-MOD-02 | Le interfacce tra moduli devono essere definite e stabili |
| NF-MOD-03 | I modelli AI devono essere configurabili e intercambiabili |

### C.4 — Riproducibilità scientifica

| ID | Requisito |
|----|-----------|
| NF-SCI-01 | I risultati devono essere riproducibili dati gli stessi input |
| NF-SCI-02 | Ogni componente deve essere valutabile con metriche standardizzate |
| NF-SCI-03 | Il dataset di test deve essere costruibile in modo controllato |

### C.5 — Prestazioni

| ID | Requisito |
|----|-----------|
| NF-PRF-01 | Il sistema deve funzionare su CPU (senza GPU) |
| NF-PRF-02 | Il sistema deve sfruttare GPU quando disponibile |
| NF-PRF-03 | Deve essere possibile selezionare il trade-off velocità/qualità |

---

## D — Architettura proposta

### D.1 — Principi architetturali

L'architettura si basa su quattro principi fondamentali:

1. **Separazione delle responsabilità**: ogni layer ha una singola responsabilità
2. **Interfacce stabili**: i moduli comunicano tramite protocolli definiti, non implementazioni concrete
3. **Privacy by design**: i dati sensibili non attraversano mai confini di processo non controllati
4. **Tracciabilità integrale**: ogni dato porta con sé la sua provenienza

### D.2 — Layer principali

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAYER 0 — DATI ORIGINALI                 │
│  File system read-only. Conservato intatto. Mai modificato.     │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                     LAYER 1 — IMPORTAZIONE                      │
│  Importer modulari. Interfaccia comune. Lettura-parsing.        │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│               LAYER 2 — VALIDAZIONE E NORMALIZZAZIONE           │
│  Pulizia. Deduplicazione. Normalizzazione. Schema unificato.    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   LAYER 3 — ARRICCHIMENTO                       │
│  STT. OCR. Language detection. Completamento dei campi.        │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                LAYER 4 — INDICIZZAZIONE SEMANTICA               │
│  Embedding. Chunking. Vector store. Clustering.                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   LAYER 5 — ANALISI SEMANTICA                   │
│  Topic detection. Semantic search. Argomento classification.   │
│  LLM locale per sintesi e ragionamento contestuale.            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   LAYER 6 — INTERFACCIA / OUTPUT                │
│  CLI. Web UI. Report. Export. API locale.                       │
└─────────────────────────────────────────────────────────────────┘
```

### D.3 — Modello a plugin

Ogni componente funzionale è definito da un'**interfaccia astratta** (abstract base class o protocol in Python). Le implementazioni concrete sono registrate tramite un registro di plugin. Il sistema seleziona l'implementazione tramite configurazione.

```
BaseImporter           → CellebriteCSVImporter, WhatsAppExportImporter, ...
BaseNormalizer         → DefaultNormalizer
BaseSTT                → WhisperSTT, FasterWhisperSTT, ...
BaseOCR                → TesseractOCR, EasyOCROCR, ...
BaseLanguageDetector   → LangDetectDetector, FastTextDetector, ...
BaseEmbedder           → SentenceTransformerEmbedder, ...
BaseVectorStore        → FAISSStore, ChromaDBStore, ...
BaseTopicDetector      → BERTopicDetector, NMFDetector, ...
BaseLLM                → OllamaLLM, LlamaCppLLM, ...
```

### D.4 — Database locale

Si utilizzeranno **due livelli di persistenza**:

| Layer | Tecnologia | Contenuto |
|-------|-----------|-----------|
| Strutturato | SQLite | Messaggi, metadati, relazioni, audit log |
| Vettoriale | FAISS o ChromaDB | Embedding per ricerca semantica |
| File system | Directory strutturata | File originali, file derivati (trascrizioni, OCR) |

---

## E — Pipeline completa

### E.1 — Diagramma della pipeline

```
DATI ORIGINALI (read-only)
│
├─── file multimediali (audio, immagini, allegati)
└─── file strutturati (CSV, JSON, XML, SQLite, UFDR...)
         │
         ▼
┌─────────────────────────────────────────────────────┐
│ IMPORTER                                            │
│ Input:  file/directory di origine                   │
│ Output: RawRecord[] (oggetti Python non validati)   │
│ Errori: file non leggibili, formato non riconosciuto│
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ VALIDAZIONE                                         │
│ Input:  RawRecord[]                                 │
│ Output: ValidRecord[] + ValidationReport            │
│ Azioni: schema check, tipo messaggio, timestamp     │
│ Errori: record scartati con motivazione             │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ PULIZIA E NORMALIZZAZIONE                           │
│ Input:  ValidRecord[]                               │
│ Output: NormalizedMessage[] → SQLite                │
│ Azioni: dedup, encoding, emoji, campi mancanti...   │
│ Regola: original_text sempre preservato             │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ MODELLO INTERNO UNIFICATO (UnifiedMessage)          │
│ Persiste in SQLite. Chiave: internal_id             │
│ Collega a: source_id (dato originale)               │
└──────┬──────────┬──────────────┬───────────────────┘
       │          │              │
       ▼          ▼              ▼
┌──────────┐ ┌─────────┐ ┌────────────┐
│  TESTO   │ │  AUDIO  │ │  IMMAGINI  │
│          │ │         │ │            │
│ già nel  │ │ STT     │ │ Text det.  │
│ modello  │ │ locale  │ │ + OCR loc. │
└────┬─────┘ └────┬────┘ └─────┬──────┘
     │            │            │
     └────────────┴────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ CONTENUTO ANALIZZABILE (AnalyzableContent)          │
│ text_for_analysis = testo | trascrizione | OCR text │
│ Ogni campo mantiene il riferimento alla fonte       │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ LANGUAGE DETECTION                                  │
│ Input:  text_for_analysis                           │
│ Output: language_code (ISO 639-1), confidence       │
│ Modelli: fastText lid.176 o langdetect              │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ CHUNKING                                            │
│ Input:  messaggi ordinati cronologicamente          │
│ Output: Chunk[] (finestra scorrevole con overlap)   │
│ Note:   chunk per messaggio singolo + chunk         │
│         contestuale (N messaggi vicini)             │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ EMBEDDING                                           │
│ Input:  Chunk[].text                                │
│ Output: vector[float] per ogni chunk → VectorStore  │
│ Modello: multilingual sentence-transformer          │
│ Cache: embedding salvato, non ricalcolato           │
└──────┬──────────────────────────┬───────────────────┘
       │                          │
       ▼                          ▼
┌──────────────────┐   ┌───────────────────────────┐
│ TOPIC DETECTION  │   │ VECTOR STORE (FAISS/Chroma)│
│ BERTopic /       │   │ Indicizzazione per ANN     │
│ clustering       │   │ search                     │
│ + LLM labeling   │   └───────────┬───────────────┘
└──────┬───────────┘               │
       │                           │
       ▼                           ▼
┌──────────────────────────────────────────────────────┐
│ LIVELLO QUERY                                        │
│ ┌──────────────────────┐  ┌─────────────────────┐   │
│ │ RICERCA SEMANTICA    │  │ ARG. CLASSIFICATION │   │
│ │ query → embedding →  │  │ query → top-k →     │   │
│ │ ANN search → ranking │  │ scoring → SÌ/NO/    │   │
│ │ → contesto           │  │ INCERTO + motiv.    │   │
│ └──────────────────────┘  └─────────────────────┘   │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│ LLM LOCALE (opzionale, per sintesi/ragionamento)     │
│ Riceve SOLO i chunk rilevanti, non l'intera conv.    │
│ Output: risposta strutturata + citazioni             │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│ INTERFACCIA / EXPORT                                 │
│ CLI · Web UI · Report JSON/PDF · Export CSV          │
└──────────────────────────────────────────────────────┘
```

### E.2 — Oggetti principali del flusso

```python
# Oggetto grezzo dall'importer (non validato)
class RawRecord:
    source_id: str          # ID nel file sorgente
    source_format: str      # "cellebrite_csv", "whatsapp_export"...
    raw_data: dict          # tutto quello che arriva dal parser
    source_file_path: str   # percorso del file originale

# Dopo validazione e normalizzazione
class UnifiedMessage:
    internal_id: str        # UUID generato dal sistema
    source_id: str          # collega a RawRecord
    source_format: str
    timestamp: datetime     # normalizzato UTC
    timestamp_original: str # valore originale conservato
    sender: str
    recipients: list[str]
    message_type: MessageType  # TEXT | AUDIO | IMAGE | VIDEO | ...
    original_text: str | None  # MAI modificato
    normalized_text: str | None
    language: str | None
    transcription: str | None        # da STT
    ocr_text: str | None             # da OCR
    media_file_path: str | None      # percorso file originale
    derived_file_paths: dict         # trascrizioni, OCR salvati
    flags: MessageFlags              # deleted, forwarded, system...
    metadata: dict                   # campi extra specifici del formato
    processing_log: list[dict]       # ogni operazione applicata
```

---

## F — Strategia di pulizia e normalizzazione

### F.1 — Fasi della normalizzazione

La normalizzazione avviene in stadi sequenziali e documentati. Ogni stadio produce un log entry nel campo `processing_log` del messaggio.

#### Stadio 1: Parsing e schema check

- Verifica che i campi obbligatori siano presenti
- Conversione dei tipi (stringhe → datetime, int, ecc.)
- Record non recuperabili → scartati con motivazione nel `ValidationReport`

#### Stadio 2: Deduplicazione

**Strategia**: fingerprint composito = hash(source_id + timestamp + sender + testo[:100])

- Se il fingerprint esiste già → record marcato come `DUPLICATE`, conservato ma non indicizzato
- Il record originale è sempre quello con `is_primary = True`

> [!IMPORTANT]
> **Decisione da prendere dopo l'analisi dei dati reali**: la strategia di deduplicazione dipende fortemente dal formato sorgente. Alcuni formati possono produrre duplicati con piccole variazioni. Va analizzato su dati reali.

#### Stadio 3: Normalizzazione timestamp

- Conversione in UTC con zona originale conservata
- Rilevamento anomalie: timestamp nel futuro, timestamp epoch=0, timestamp incongruenti con l'ordine dei messaggi
- Messaggi con timestamp irrecuperabile → flag `TIMESTAMP_INVALID`

#### Stadio 4: Normalizzazione encoding

- Conversione in UTF-8
- Gestione BOM
- Sostituzione caratteri non validi con il replacement character U+FFFD (mai rimozione silenziosa)
- Tutto registrato nel processing_log

#### Stadio 5: Pulizia del testo (solo per `normalized_text`)

`original_text` non viene mai toccato. Sulla copia normalizzata:

- Rimozione/sostituzione metadata di sistema (es. "Messaggio cancellato", "Chiamata persa") → classificati nel flag
- Normalizzazione spazi e newline
- Gestione emoji: **conservate nel normalized_text** (portano significato semantico), rimosse solo se esplicitamente richiesto da una configurazione
- Normalizzazione caratteri Unicode equivalenti (es. lettere accentate in forme diverse)

> [!IMPORTANT]
> **Decisione consigliata**: conservare le emoji nel testo normalizzato. Modelli di embedding moderni (es. multilingual-e5) le gestiscono correttamente e le emoji portano informazione semantica.

#### Stadio 6: Classificazione del tipo di messaggio

Identificazione di:
- Messaggi di sistema (es. "Mario ha aggiunto Lucia al gruppo")
- Messaggi inoltrati (forwarded)
- Messaggi eliminati (quando il formato li riporta)
- Messaggi vuoti / solo media
- Messaggi con media allegato

Ogni classificazione viene registrata in `MessageFlags`.

#### Stadio 7: Risoluzione dei file multimediali

- Verifica che il path del file multimediale sia accessibile
- Hash SHA-256 del file originale (per verifica integrità)
- Dimensione e tipo MIME
- Se il file manca → flag `MEDIA_MISSING`

### F.2 — Regole fondamentali della normalizzazione

| Regola | Descrizione |
|--------|-------------|
| **Immutabilità originale** | `original_text` e il file originale NON vengono mai modificati |
| **Tracciabilità completa** | Ogni operazione è loggata con timestamp e motivo |
| **Fail-safe** | In caso di errore in uno stadio, il record avanza comunque con il flag di errore |
| **Determinismo** | La stessa normalizzazione sullo stesso input produce sempre lo stesso output |

### F.3 — Catena di tracciabilità

```
Risultato AI
  └── chunk_id → Chunk
        └── message_id → UnifiedMessage.internal_id
              └── source_id → RawRecord
                    └── source_file_path → file originale
```

Questa catena è navigabile via query SQL e deve essere esposta nell'interfaccia.

---

## G — Strategia audio e speech-to-text

### G.1 — Modelli candidati

| Modello | Tipo | Lingue | Velocità | Accuratezza | VRAM |
|---------|------|--------|----------|-------------|------|
| **Whisper large-v3** | OpenAI (locale) | 99+ | Lenta | Eccellente | ~6 GB |
| **Whisper medium** | OpenAI (locale) | 99+ | Media | Buona | ~3 GB |
| **Whisper small** | OpenAI (locale) | 99+ | Veloce | Discreta | ~1 GB |
| **faster-whisper** | CTranslate2 port | 99+ | 2-4x più rapido | = Whisper | ~50% meno |
| **whisper.cpp** | C++ port | 99+ | CPU-ottimizzato | = Whisper | RAM only |

> [!TIP]
> **Scelta consigliata**: `faster-whisper` con modello `large-v3` su GPU, `medium` su CPU. faster-whisper offre le stesse prestazioni di Whisper con il 50% della VRAM in meno e supporta la quantizzazione INT8.

### G.2 — Pipeline STT

```
File audio originale (mp3, ogg, m4a, wav, aac...)
  │
  ▼
Preprocessing (normalizzazione sample rate, formato WAV)
  │
  ▼
Language detection audio (Whisper lo fa internamente)
  │
  ▼
Trascrizione (faster-whisper)
  │  Output: text, language, confidence, segments con timestamp
  ▼
Post-processing: pulizia artefatti STT
  │
  ▼
Salvataggio trascrizione (testo + JSON con segmenti)
  │
  ▼
Aggiornamento UnifiedMessage.transcription
  │
  ▼
Embedding della trascrizione (trattata come testo)
```

### G.3 — Gestione degli errori STT

| Caso | Comportamento |
|------|--------------|
| File corrotto | Flag `STT_FAILED`, log con motivo |
| Audio troppo breve (<1s) | Flag `STT_TOO_SHORT`, skip |
| Confidenza bassa | Trascrizione conservata + flag `STT_LOW_CONFIDENCE` |
| Lingua non riconoscibile | Tentativo con `language=None`, flag `STT_LANGUAGE_UNKNOWN` |
| Out of memory | Retry con modello più piccolo, log |

### G.4 — Configurazione

```yaml
stt:
  engine: faster-whisper
  model: large-v3          # | medium | small | base
  device: cuda             # | cpu | auto
  compute_type: float16    # | int8 | float32
  beam_size: 5
  language: null           # null = auto-detect
  min_confidence: 0.6
  fallback_model: medium   # usato se OOM
```

### G.5 — Output STT

Ogni trascrizione produce due file nella directory `derived/`:
- `{internal_id}_transcript.txt` — testo puro
- `{internal_id}_transcript.json` — segmenti con timestamp, confidenza per parola

Il campo `UnifiedMessage.transcription` contiene il testo puro; il JSON con i segmenti è accessibile tramite `derived_file_paths['stt_segments']`.

---

## H — Strategia immagini e OCR

### H.1 — Pipeline di analisi delle immagini

L'analisi delle immagini è divisa in due fasi distinte per evitare di processare inutilmente immagini senza testo.

#### Fase 1 — Text detection (rapida)

Determina se l'immagine contiene testo degno di estrazione.

**Tecnologie candidate**:

| Strumento | Approccio | Velocità | Note |
|-----------|-----------|----------|------|
| OpenCV + EAST detector | Deep learning | Veloce | Rileva regioni di testo |
| easyOCR (confidence threshold) | End-to-end | Media | Restituisce score |
| Tesseract (confidence) | Classico | Media | Affidabile su testo chiaro |
| PaddleOCR detector | Deep learning | Veloce | Ottimo su screenshot |

> [!TIP]
> **Consigliato**: usare PaddleOCR per la fase di detection (solo detector, non riconoscimento), che è molto veloce. Se lo score supera una soglia configurabile → si procede con OCR completo.

#### Fase 2 — OCR (solo se testo rilevato)

**Tecnologie candidate**:

| Strumento | Lingue | Qualità | Note |
|-----------|--------|---------|------|
| **Tesseract 5** (LSTM) | 100+ | Buona su testo chiaro | Open source, maturo |
| **EasyOCR** | 80+ | Buona su scene text | Deep learning, multi-language |
| **PaddleOCR** | 80+ | Ottima | Veloce, buono su screenshot |
| **TrOCR** (Microsoft) | Limitata | Eccellente su testo stampato | HuggingFace, pesante |

> [!TIP]
> **Scelta consigliata**: PaddleOCR come default (ottimo su screenshot di conversazioni). Tesseract come alternativa leggera per chi ha poca RAM. Architettura a plugin permette di aggiungere altri.

### H.2 — Tipi di immagini da gestire

| Tipo | Strategia |
|------|-----------|
| Screenshot di conversazione | PaddleOCR, preprocessing minimo |
| Fotografia di documento | Deskewing + Binarizzazione + Tesseract |
| Testo piccolo | Upscaling + OCR |
| Testo inclinato | Deskewing automatico |
| Più lingue | OCR multilingual (EasyOCR o PaddleOCR) |
| Testo parzialmente illeggibile | OCR + flag `OCR_LOW_QUALITY` |
| Immagini senza testo | Skip dopo detection, flag `NO_TEXT_DETECTED` |

### H.3 — Preprocessing pipeline

```
Immagine originale (conservata intatta)
  │
  ▼
Ridimensionamento se necessario (max 4096px sul lato lungo)
  │
  ▼
Conversione in scala di grigi (per OCR)
  │
  ▼
Binarizzazione adattiva (se qualità bassa)
  │
  ▼
Deskewing (se testo inclinato)
  │
  ▼
Applicazione OCR
  │
  ▼
Post-processing: pulizia del testo estratto
  │
  ▼
Score di confidenza aggregato
  │
  ▼
Salvataggio testo OCR + JSON con bounding box
  │
  ▼
Aggiornamento UnifiedMessage.ocr_text
```

### H.4 — Output OCR

- `{internal_id}_ocr.txt` — testo puro estratto
- `{internal_id}_ocr.json` — bounding box, parole, score per regione
- `UnifiedMessage.ocr_text` — testo puro per analisi semantica
- `UnifiedMessage.metadata['ocr_confidence']` — score aggregato

> [!IMPORTANT]
> **Soglia OCR**: il testo estratto con confidenza < 0.5 viene conservato ma flaggato come `OCR_LOW_CONFIDENCE` e non incluso nell'analisi semantica per default (configurabile).

---

## I — Strategia multilingue

### I.1 — Approccio generale

Il sistema non assume una lingua di default. L'approccio multilingual è **nativo**, non un'aggiunta.

**Principio chiave**: preferire modelli di embedding multilingue che mappano lingue diverse nello stesso spazio semantico, piuttosto che tradurre tutto.

### I.2 — Language detection

**Tecnologie candidate**:

| Strumento | Velocità | Lingue | Accuratezza | Note |
|-----------|----------|--------|-------------|------|
| **fastText lid.176** | Molto rapida | 176 | Alta | Consigliato |
| langdetect | Rapida | 55 | Media | Python puro |
| lingua-py | Media | 75 | Alta | Migliore su testi brevi |
| CLD3 | Rapida | 107 | Alta | Google, C++ |

> [!TIP]
> **Scelta consigliata**: fastText `lid.176.ftz` (modello compresso, <2MB). Rapido, accurato, funziona bene anche su testi brevi. Per testi molto brevi (<10 caratteri) → flag `LANGUAGE_UNCERTAIN`.

I messaggi vocali ottengono la lingua da Whisper/faster-whisper, che effettua rilevamento linguistico sull'audio.

### I.3 — Embedding multilingue

**Non è necessario tradurre**. I modelli multilingue moderni proiettano lingue diverse nello stesso spazio vettoriale.

**Modelli candidati**:

| Modello | Lingue | Dimensione | RAM | Qualità |
|---------|--------|-----------|-----|---------|
| **paraphrase-multilingual-mpnet-base-v2** | 50+ | 420 MB | ~1 GB | Ottima |
| **multilingual-e5-base** | 100+ | 540 MB | ~1.5 GB | Ottima |
| **multilingual-e5-large** | 100+ | 2.2 GB | ~4 GB | Eccellente |
| **LaBSE** | 109 | 1.8 GB | ~3 GB | Ottima per similarità cross-lingue |
| **paraphrase-multilingual-MiniLM-L12-v2** | 50+ | 120 MB | <1 GB | Buona, leggera |

> [!TIP]
> **Scelta consigliata**: `multilingual-e5-base` come default. Ottimo equilibrio tra qualità, velocità e consumo di memoria. Supporta 100+ lingue, incluso italiano. Per macchine molto limitate: `paraphrase-multilingual-MiniLM-L12-v2`.

### I.4 — Traduzione opzionale

La traduzione non è necessaria per la ricerca semantica se si usano embedding multilingue. Può essere utile per:

- Presentare risultati all'utente in lingua italiana
- Fornire al LLM locale un contesto in una lingua che comprende meglio
- Generare report monolingue

Se implementata, deve essere:
- 100% locale (es. CTranslate2 con modelli OPUS-MT o Helsinki-NLP)
- Opzionale e configurabile
- Applicata solo al `normalized_text`, non all'`original_text`

---

## J — Strategia embeddings

### J.1 — Cosa si trasforma in embedding

| Tipo | Cosa viene embeddato |
|------|---------------------|
| Testo | `normalized_text` |
| Audio | `transcription` |
| Immagine | `ocr_text` (se disponibile e con confidenza sufficiente) |
| Chunk contestuale | Concatenazione di N messaggi vicini |

### J.2 — Strategia di chunking

Per conversazioni molto lunghe, due livelli di granularità:

**Livello 1 — Messaggio singolo**: ogni `UnifiedMessage` produce un embedding (se ha contenuto testuale ≥ N caratteri, configurabile)

**Livello 2 — Finestra contestuale**: finestre di K messaggi consecutivi con overlap di M messaggi, usate per la topic detection e per catturare il contesto di una discussione

```
Messaggi: [M1, M2, M3, M4, M5, M6, M7, M8...]
Window K=5, overlap M=2:
  Chunk 1: [M1, M2, M3, M4, M5]
  Chunk 2: [M4, M5, M6, M7, M8]
  ...
```

### J.3 — Persistenza e caching

- Gli embedding sono calcolati una volta e salvati nel vector store
- La chiave di cache è `hash(internal_id + model_version + text_version)`
- Se il testo di un messaggio cambia (es. OCR aggiornato), l'embedding viene ricalcolato
- Il processo è riprendibile: messaggi già embeddati vengono saltati

### J.4 — Vector store

**Tecnologie candidate**:

| Strumento | Tipo | Persistenza | Scala | Note |
|-----------|------|-------------|-------|------|
| **FAISS** | In-memory/file | File flat | Milioni | Velocissimo, no metadata |
| **ChromaDB** | Embedded | SQLite | 100k-1M | Metadata + embeddings, semplice |
| **Qdrant** | Server/embedded | Disco | Milioni+ | Ricco, ma più complesso |
| **LanceDB** | Embedded | File | Milioni | Nuovo, integra bene con pandas |

> [!TIP]
> **Scelta consigliata**: ChromaDB per prototipo (semplicità, metadata integrati). FAISS per produzione se la scala lo richiede (velocità). L'architettura a plugin permette di cambiare senza riscrivere l'analisi.

---

## K — Strategia topic detection

### K.1 — Approccio ibrido

Il topic detection usa una pipeline in due fasi:

**Fase 1 — Clustering non supervisionato** (trova i topic automaticamente):

```
Embedding dei chunk
  │
  ▼
Riduzione dimensionalità (UMAP: da N→5 dim)
  │
  ▼
Clustering (HDBSCAN: numero cluster automatico)
  │
  ▼
Topic per cluster (TF-IDF o c-TF-IDF su messaggi del cluster)
  │
  ▼
Label automatica (top-N parole chiave)
```

**Tecnologia**: BERTopic (integra UMAP + HDBSCAN + c-TF-IDF + label generation)

**Fase 2 — Labeling semantico con LLM locale** (arricchisce i topic):

Per ogni topic trovato, un piccolo campione di messaggi viene inviato all'LLM locale per generare:
- Un nome comprensibile del topic
- Una descrizione breve
- La distinzione tra menzione/discussione/argomento centrale

> [!TIP]
> **Scelta consigliata**: BERTopic. Supporta nativamente sentence-transformers multilingue, è configurabile, produce topic coerenti e scala bene. Non richiede di specificare il numero di topic a priori.

### K.2 — Struttura di output del topic

```python
class Topic:
    topic_id: str
    name: str                  # generato dal LLM o da c-TF-IDF
    description: str
    keywords: list[str]
    relevance_score: float     # proporzione messaggi nel corpus
    message_count: int
    percentage: float
    time_range: tuple[datetime, datetime]
    first_occurrence: datetime
    last_occurrence: datetime
    recurrence_count: int      # quante "sessioni" distinte
    representative_messages: list[str]  # internal_id
    subtopics: list['Topic']
    engagement_level: TopicEngagement  # MENTION | REFERENCE | DISCUSSION | RECURRING | CENTRAL
    confidence: float
```

### K.3 — Distinzione tra livelli di engagement

| Livello | Criteri |
|---------|---------|
| `MENTION` | 1-2 messaggi, bassa densità semantica, nessun follow-up |
| `REFERENCE` | 3-5 messaggi, risposta ricevuta ma non continuata |
| `DISCUSSION` | 5+ messaggi, continuità temporale, scambio bilaterale |
| `RECURRING` | Topic compare in sessioni separate |
| `CENTRAL` | Topic dominante per quantità e distribuzione nel corpus |

I criteri numerici (5 messaggi, ecc.) sono **soglie configurabili**, non hardcoded.

### K.4 — Scalabilità topic detection

Per corpus molto grandi (>100k messaggi):

- Campionamento stratificato temporalmente (es. 10k messaggi rappresentativi)
- Topic detection sul campione
- Classificazione di tutti i messaggi rispetto ai topic trovati (più veloce del clustering)
- Processo incrementale: nuovi messaggi vengono classificati senza ri-clusterizzare

---

## L — Strategia ricerca semantica

### L.1 — Funzionamento

```
Query utente (es. "esami universitari")
  │
  ▼
Embedding della query (stesso modello usato per i messaggi)
  │
  ▼
ANN search nel vector store (top-K più simili per cosine similarity)
  │
  ▼
Re-ranking (opzionale, con cross-encoder o BM25 ibrido)
  │
  ▼
Risultati con score di similarità
  │
  ▼
Recupero contesto (M messaggi precedenti e successivi)
  │
  ▼
Presentazione risultati con tracciabilità
```

### L.2 — Ibrido semantico + lessicale

Per ridurre i falsi negativi, si può combinare:

- **Semantic search** (embedding cosine similarity): trova concetti correlati
- **BM25** (full-text, es. via SQLite FTS5): trova corrispondenze lessicali esatte

Fusione con Reciprocal Rank Fusion (RRF):

```
score_finale = α * rank_semantico + (1-α) * rank_lessicale
```

`α` configurabile (default 0.7 → preferenza semantica).

### L.3 — Finestre di contesto

Per ogni risultato rilevante, si recuperano:

- `context_before`: P messaggi precedenti (configurabile, default 3)
- `context_after`: F messaggi successivi (configurabile, default 3)

Il contesto è presentato all'utente ma con score di rilevanza distinto dal messaggio principale.

### L.4 — Output ricerca

```python
class SearchResult:
    query: str
    results: list[SearchHit]

class SearchHit:
    message: UnifiedMessage
    similarity_score: float
    rank: int
    context_before: list[UnifiedMessage]
    context_after: list[UnifiedMessage]
    source_type: str   # "text" | "transcription" | "ocr"
    highlight: str     # parte del testo che ha matchato
```

---

## M — Strategia rilevamento argomento specifico

### M.1 — Il problema

Classificare se un argomento specifico viene trattato in una conversazione è diverso dalla ricerca semantica. Richiede:

1. Trovare messaggi rilevanti (ricerca semantica)
2. Valutare la quantità, qualità e continuità di tali messaggi
3. Produrre una risposta categorica: **SÌ / NO / INCERTO**

### M.2 — Pipeline di classificazione

```
Query argomento (es. "Si parla di droga?")
  │
  ▼
Espansione della query (sinonimi, concetti correlati via LLM)
  │
  ▼
Ricerca semantica → top-K messaggi rilevanti
  │
  ▼
Scoring multi-fattore (vedi M.3)
  │
  ▼
Classificazione SÌ/NO/INCERTO
  │
  ▼
Output con evidenze e motivazione
```

### M.3 — Sistema di scoring multi-fattore

Lo score finale è una combinazione ponderata di fattori:

| Fattore | Peso (default) | Descrizione |
|---------|---------------|-------------|
| Similarità semantica media (top-K) | 0.35 | Quanto i messaggi trovati sono simili alla query |
| Numero di messaggi rilevanti | 0.20 | Più messaggi → più probabile |
| Continuità temporale | 0.15 | Messaggi vicini nel tempo → discussione reale |
| Scambio bilaterale | 0.10 | Più mittenti → discussione, non monologo |
| Ricorrenza | 0.10 | Topic appare in sessioni diverse |
| Score massimo singolo messaggio | 0.10 | Un messaggio molto rilevante vale comunque |

Pesi configurabili.

### M.4 — Soglie

| Score | Classificazione |
|-------|----------------|
| ≥ 0.75 | **SÌ** — argomento trattato |
| 0.45 – 0.75 | **INCERTO** — evidenza debole o ambigua |
| < 0.45 | **NO** — argomento non trattato |

Le soglie sono configurabili. Lo stato INCERTO è un risultato legittimo e scientificamente onesto.

### M.5 — Output con motivazione

```python
class ArgumentDetectionResult:
    query: str
    verdict: Verdict         # YES | NO | UNCERTAIN
    confidence: float        # 0.0 – 1.0
    score: float
    score_breakdown: dict    # dettaglio per ogni fattore
    relevant_messages: list[SearchHit]
    reasoning: str           # generato dall'LLM con le evidenze
    time_range: tuple[datetime, datetime] | None
    first_occurrence: datetime | None
    recurrence: int
```

Il campo `reasoning` viene generato dall'LLM locale **solo** sui messaggi rilevanti trovati, non sull'intera conversazione.

---

## N — Tecnologie candidate e confronto

### N.1 — Speech-to-Text

| Strumento | Lingue | Qualità | Velocità CPU | Velocità GPU | Memoria | Licenza |
|-----------|--------|---------|-------------|-------------|---------|---------|
| **faster-whisper large-v3** | 99 | ⭐⭐⭐⭐⭐ | Lenta | Veloce | 6 GB VRAM | MIT |
| **faster-whisper medium** | 99 | ⭐⭐⭐⭐ | Media | Molto veloce | 3 GB VRAM | MIT |
| **whisper.cpp** | 99 | ⭐⭐⭐⭐ | Media | Veloce | RAM only | MIT |
| Vosk | 20 | ⭐⭐⭐ | Veloce | N/A | <1 GB | Apache 2 |

**Consiglio**: faster-whisper. Ottimo per italiano e lingue europee. Supporta quantizzazione INT8 per ridurre memoria.

### N.2 — OCR

| Strumento | Lingue | Screenshot | Doc. fotografici | Velocità | Licenza |
|-----------|--------|-----------|-----------------|----------|---------|
| **PaddleOCR** | 80+ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Veloce | Apache 2 |
| **EasyOCR** | 80+ | ⭐⭐⭐⭐ | ⭐⭐⭐ | Media | Apache 2 |
| **Tesseract 5** | 100+ | ⭐⭐⭐ | ⭐⭐⭐⭐ | Media | Apache 2 |
| TrOCR | Limitata | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Lenta | MIT |

**Consiglio**: PaddleOCR come default. Tesseract come fallback leggero.

### N.3 — Embedding multilingue

| Modello | Lingue | Dim. | RAM | Qualità ITA | Qualità cross-lingua |
|---------|--------|------|-----|------------|---------------------|
| **multilingual-e5-base** | 100+ | 768 | ~2 GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **multilingual-e5-large** | 100+ | 1024 | ~4 GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **LaBSE** | 109 | 768 | ~3 GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **paraphrase-multilingual-mpnet-base-v2** | 50+ | 768 | ~1.5 GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **MiniLM-L12-v2-multilingual** | 50+ | 384 | <1 GB | ⭐⭐⭐ | ⭐⭐⭐ |

**Consiglio**: `multilingual-e5-base` come default. `MiniLM` per macchine con poca RAM.

### N.4 — LLM locale

| Modello | VRAM | Qualità ITA | Velocità | Quantizzazione | Note |
|---------|------|------------|----------|---------------|------|
| **Llama 3.1 8B (Q4)** | ~6 GB | ⭐⭐⭐⭐ | Buona | Q4_K_M | Ottimo per ITA |
| **Mistral 7B (Q4)** | ~5 GB | ⭐⭐⭐⭐ | Buona | Q4_K_M | Eccellente per analisi |
| **Phi-3 Mini (Q4)** | ~2.5 GB | ⭐⭐⭐ | Veloce | Q4 | Leggero |
| **Gemma 2 9B (Q4)** | ~6 GB | ⭐⭐⭐⭐ | Media | Q4 | Google, buono ITA |
| Qwen2.5 7B | ~5 GB | ⭐⭐⭐⭐ | Buona | Q4 | Multilingue |

**Framework**: Ollama (semplicità, API locale) o llama.cpp (massimo controllo)

**Consiglio**: Ollama + Llama 3.1 8B Q4. Ollama gestisce il serving, il download dei modelli e la VRAM automaticamente.

> [!IMPORTANT]
> **Decisione da prendere dopo esperimenti**: il modello LLM ottimale dipende dall'italiano e dal tipo di ragionamento richiesto. Va valutato sperimentalmente.

### N.5 — Vector store

| Strumento | Tipo | Scala | Metadata | Setup | Note |
|-----------|------|-------|----------|-------|------|
| **ChromaDB** | Embedded | 100k-1M | ✅ | Semplicissimo | Ideale per prototipo |
| **FAISS** | In-memory/file | Milioni | ❌ (separati) | Semplice | Velocissimo, produzione |
| **Qdrant (embedded)** | Embedded | Milioni | ✅ | Medio | Feature-ricco |
| LanceDB | Embedded | Milioni | ✅ | Semplice | Nuovo, promettente |

**Consiglio**: ChromaDB per PoC, FAISS per produzione se la scala lo richiede.

### N.6 — Topic Detection

| Strumento | Approccio | Scalabilità | Qualità | Setup |
|-----------|-----------|-------------|---------|-------|
| **BERTopic** | BERT + clustering | Alta | ⭐⭐⭐⭐⭐ | Medio |
| NMF (sklearn) | Matrix factorization | Alta | ⭐⭐⭐ | Semplice |
| LDA (Gensim) | Probabilistico | Alta | ⭐⭐⭐ | Semplice |
| Top2Vec | Embedding + clustering | Alta | ⭐⭐⭐⭐ | Semplice |

**Consiglio**: BERTopic. Integra nativamente sentence-transformers, produce topic coerenti, supporta topic gerarchici (utile per subtopic).

---

## O — Requisiti hardware

### O.1 — Configurazioni target

#### Configurazione minima (CPU only)

| Componente | Requisito |
|-----------|-----------|
| RAM | 16 GB |
| CPU | 4+ core moderni |
| Storage | 50 GB liberi (SSD raccomandato) |
| GPU | Non necessaria |

**Funzionamento**: Modelli piccoli (Whisper small, MiniLM, Phi-3 Mini). Lento ma funzionante.

#### Configurazione raccomandata (CPU + GPU)

| Componente | Requisito |
|-----------|-----------|
| RAM | 32 GB |
| CPU | 8+ core |
| GPU VRAM | 8 GB |
| Storage | 100 GB liberi (SSD) |

**Funzionamento**: Whisper medium/large, multilingual-e5-base, Mistral/Llama 8B Q4. Velocità accettabile.

#### Configurazione ottimale

| Componente | Requisito |
|-----------|-----------|
| RAM | 64 GB |
| CPU | 12+ core |
| GPU VRAM | 16+ GB |
| Storage | 500 GB NVMe |

**Funzionamento**: Tutti i modelli large. Analisi rapida anche su corpus molto grandi.

### O.2 — Consumo memoria per componente

| Componente | Modello | RAM/VRAM | Modalità |
|-----------|---------|----------|----------|
| STT | faster-whisper small | ~1 GB VRAM | GPU |
| STT | faster-whisper medium | ~3 GB VRAM | GPU |
| STT | faster-whisper large-v3 | ~6 GB VRAM | GPU |
| STT | faster-whisper (INT8) | ~50% meno | GPU/CPU |
| OCR | PaddleOCR | ~500 MB RAM | CPU |
| Embedding | MiniLM-L12 | ~500 MB RAM | CPU |
| Embedding | multilingual-e5-base | ~2 GB RAM/VRAM | CPU/GPU |
| LLM | Llama 3.1 8B Q4 | ~5-6 GB VRAM | GPU |
| LLM | Mistral 7B Q4 | ~4-5 GB VRAM | GPU |
| Vector store | FAISS (1M vettori 768d) | ~3 GB RAM | CPU |

### O.3 — Strategia di gestione memoria

1. **Elaborazione sequenziale**: non caricare tutti i modelli contemporaneamente in VRAM
2. **Pipeline a stadi**: STT → unload → OCR → unload → Embedding → LLM
3. **Configurazione automatica**: il sistema rileva la VRAM disponibile e seleziona i modelli appropriati
4. **Quantizzazione**: INT8 per STT e embedding su GPU con poca VRAM
5. **Swap configurabile**: possibilità di usare RAM come fallback (lento)

---

## P — Struttura del progetto

### P.1 — Albero delle directory

```
forensic-chat-analyzer/
│
├── pyproject.toml              # Dipendenze e metadata progetto
├── README.md
├── .env.example                # Template configurazione (NO secrets)
│
├── config/
│   ├── default.yaml            # Configurazione di default del sistema
│   ├── models.yaml             # Configurazione modelli AI
│   └── profiles/               # Profili hardware (cpu_only, gpu_8gb...)
│
├── src/
│   └── forensic_analyzer/
│       │
│       ├── __init__.py
│       ├── core/               # Logica di dominio, nessuna dipendenza esterna
│       │   ├── models.py       # UnifiedMessage, Topic, SearchResult...
│       │   ├── enums.py        # MessageType, Verdict, TopicEngagement...
│       │   └── exceptions.py   # Eccezioni dominio-specifiche
│       │
│       ├── importers/          # Layer di importazione
│       │   ├── base.py         # BaseImporter (abstract)
│       │   ├── registry.py     # Registro importer disponibili
│       │   └── formats/
│       │       ├── cellebrite_csv.py
│       │       ├── whatsapp_export.py
│       │       └── generic_json.py
│       │
│       ├── pipeline/           # Orchestrazione della pipeline
│       │   ├── orchestrator.py # Coordina i passi della pipeline
│       │   ├── validator.py    # Validazione dei RawRecord
│       │   └── stages.py       # Definizione degli stage della pipeline
│       │
│       ├── normalizer/         # Pulizia e normalizzazione
│       │   ├── base.py
│       │   ├── normalizer.py   # Implementazione default
│       │   ├── deduplicator.py
│       │   ├── timestamp_normalizer.py
│       │   └── text_cleaner.py
│       │
│       ├── enrichment/         # Arricchimento: STT, OCR, language detection
│       │   ├── stt/
│       │   │   ├── base.py     # BaseSTT
│       │   │   └── faster_whisper.py
│       │   ├── ocr/
│       │   │   ├── base.py     # BaseOCR
│       │   │   ├── paddle_ocr.py
│       │   │   └── tesseract.py
│       │   └── language/
│       │       ├── base.py     # BaseLanguageDetector
│       │       └── fasttext_detector.py
│       │
│       ├── storage/            # Persistenza
│       │   ├── database.py     # SQLite (messaggi, metadati)
│       │   ├── vector_store/
│       │   │   ├── base.py     # BaseVectorStore
│       │   │   ├── chroma.py
│       │   │   └── faiss.py
│       │   └── file_store.py   # Gestione file originali e derivati
│       │
│       ├── embeddings/         # Generazione embedding
│       │   ├── base.py         # BaseEmbedder
│       │   ├── sentence_transformer.py
│       │   └── chunker.py      # Chunking dei messaggi
│       │
│       ├── analysis/           # Analisi semantica
│       │   ├── topic_detection/
│       │   │   ├── base.py     # BaseTopicDetector
│       │   │   └── bertopic_detector.py
│       │   ├── semantic_search/
│       │   │   ├── searcher.py
│       │   │   └── ranker.py
│       │   ├── argument_classifier/
│       │   │   ├── classifier.py
│       │   │   └── scorer.py
│       │   └── temporal/
│       │       └── analyzer.py # Analisi temporale dei topic
│       │
│       ├── llm/                # Integrazione LLM locale
│       │   ├── base.py         # BaseLLM
│       │   ├── ollama.py
│       │   ├── llamacpp.py
│       │   └── prompts/        # Template dei prompt
│       │       ├── topic_labeling.txt
│       │       ├── argument_reasoning.txt
│       │       └── summarization.txt
│       │
│       └── interface/          # Interfaccia utente
│           ├── cli/
│           │   └── commands.py # Comandi CLI (Click o Typer)
│           └── web/            # Eventuale Web UI (fase avanzata)
│
├── data/                       # NON versionato (.gitignore)
│   ├── originals/              # Dati originali (read-only)
│   ├── derived/                # Trascrizioni, OCR, ecc.
│   ├── database/               # SQLite
│   └── vector_store/           # FAISS/ChromaDB
│
├── tests/
│   ├── unit/
│   │   ├── test_normalizer.py
│   │   ├── test_deduplicator.py
│   │   ├── test_timestamp.py
│   │   └── ...
│   ├── integration/
│   │   ├── test_pipeline.py
│   │   └── test_search.py
│   ├── evaluation/             # Script per valutazione scientifica
│   │   ├── eval_stt.py
│   │   ├── eval_ocr.py
│   │   ├── eval_search.py
│   │   ├── eval_topic.py
│   │   └── eval_argument.py
│   └── fixtures/               # Dataset di test controllati
│       ├── conversations/
│       ├── audio/
│       └── images/
│
├── docs/
│   ├── architecture/           # Diagrammi e decisioni architetturali
│   │   └── ADR/                # Architecture Decision Records
│   ├── evaluation/             # Risultati esperimenti
│   └── thesis/                 # Materiale tesi
│
└── scripts/
    ├── setup_models.py         # Download e configurazione modelli
    ├── benchmark.py            # Benchmark hardware
    └── generate_test_data.py   # Generazione dataset di test
```

### P.2 — Responsabilità di ogni componente

| Directory | Responsabilità |
|-----------|---------------|
| `core/` | Definizioni di dominio puro. Nessuna dipendenza esterna. Stabile. |
| `importers/` | Traduce formati sorgente → `RawRecord`. Ogni formato è isolato. |
| `pipeline/` | Coordina l'esecuzione degli stage in sequenza. Gestisce errori e ripresa. |
| `normalizer/` | Tutto ciò che riguarda pulizia, dedup, validazione. |
| `enrichment/` | STT, OCR, language detection. Modelli AI di arricchimento. |
| `storage/` | Persistenza. SQLite per dati strutturati, vector store per embedding. |
| `embeddings/` | Generazione embedding e chunking. Indipendente dall'analisi. |
| `analysis/` | Logica di analisi semantica. Usa storage e embeddings. |
| `llm/` | Integrazione LLM locale. Solo per sintesi e ragionamento, non per tutto. |
| `interface/` | Presentazione e interazione utente. Non contiene logica di business. |
| `tests/` | Unit, integration, evaluation. Dataset controllati in `fixtures/`. |
| `data/` | Mai versionato. Separazione dati originali / derivati. |
| `docs/ADR/` | Architecture Decision Records. Ogni decisione importante è documentata. |

---

## Q — Metodologia di valutazione

### Q.1 — Costruzione del dataset di test

Per una tesi di laurea, la riproducibilità è essenziale. Il dataset di test deve essere:

- **Controllato**: contenuto noto, ground truth definita manualmente
- **Diversificato**: diversi tipi di messaggi, lingue, media
- **Anonimizzato**: nessun dato personale reale
- **Versionato**: insieme al codice (senza dati sensibili)

**Strategia di costruzione**:

1. **Conversazioni sintetiche**: scritte appositamente con argomenti noti, in italiano e in altre lingue
2. **Audio sintetici**: generati con TTS (es. Coqui TTS) con testo noto → WER misurabile
3. **Immagini di test**: screenshot costruiti ad hoc con testo noto → accuratezza OCR misurabile
4. **Ground truth**: per ogni conversazione, definire manualmente:
   - Quali topic sono presenti
   - Quali messaggi appartengono a ogni topic
   - Quale livello di engagement (menzione/discussione/...)
   - Quali query semantiche devono restituire quali messaggi

### Q.2 — Valutazione STT

**Metrica principale**: Word Error Rate (WER)

```
WER = (S + D + I) / N
```
dove S=sostituzioni, D=cancellazioni, I=inserzioni, N=parole totali nel riferimento.

**Protocollo**:
- Dataset: X audio sintetici + (se disponibili) audio reali con trascrizione nota
- Stratificazione: lunghezza (breve <10s, medio 10-60s, lungo >60s), qualità audio, lingua
- Report per strato

**Metriche aggiuntive**:
- CER (Character Error Rate): utile per lingue con morfologia complessa
- RTF (Real-Time Factor): velocità di elaborazione
- Distribuzione WER per lingua

### Q.3 — Valutazione OCR

**Protocollo**:
- Dataset: immagini con testo noto (screenshot costruiti, fotografie di documenti di test)
- Stratificazione: tipo immagine, qualità, lingua, dimensione testo

**Metriche**:
- Accuratezza carattere (1 - CER)
- Precision e recall a livello di parola
- F1 a livello di parola
- Score OCR confidence vs accuratezza reale (calibrazione)

### Q.4 — Valutazione ricerca semantica

**Protocollo**:
- Dataset: conversazioni con ground truth di rilevanza per ogni query
- Query: formulate in linguaggio naturale, semanticamente varie
- Ground truth: annotazione manuale (rilevante/non rilevante per ogni messaggio)

**Metriche**:

| Metrica | Descrizione |
|---------|-------------|
| **Precision@K** | Quanti dei top-K risultati sono rilevanti |
| **Recall@K** | Quanti dei risultati rilevanti compaiono nei top-K |
| **MRR** | Mean Reciprocal Rank: quanto in alto compare il primo risultato rilevante |
| **NDCG@K** | Normalized Discounted Cumulative Gain: considera la posizione |

### Q.5 — Valutazione topic detection

**Protocollo**:
- Dataset: conversazioni con topic annotati manualmente
- Confronto: topic trovati vs topic attesi

**Metriche intrinseche** (senza ground truth):
- **Coherence (C_v)**: misura la coerenza semantica delle parole di ogni topic
- **Topic Diversity**: quanto i topic sono distinti tra loro

**Metriche estrinseche** (con ground truth):
- **Precision/Recall/F1** a livello di assegnazione messaggio-topic
- **NMI** (Normalized Mutual Information): concordanza tra cluster trovati e cluster attesi

### Q.6 — Valutazione rilevamento argomento

**Protocollo**:
- Dataset: conversazioni con etichetta binaria per ogni argomento (tratta / non tratta)
- Distribuzione bilanciata SÌ/NO

**Metriche**:

| Metrica | Formula |
|---------|---------|
| Accuracy | (TP + TN) / totale |
| Precision | TP / (TP + FP) |
| Recall | TP / (TP + FN) |
| F1 | 2 * P * R / (P + R) |
| Falsi positivi | FP / (FP + TN) |
| Falsi negativi | FN / (FN + TP) |

Valutare separatamente i casi classificati come INCERTO.

### Q.7 — Valutazione prestazioni

| Metrica | Strumento |
|---------|-----------|
| Tempo di elaborazione per fase | `time` / `cProfile` |
| RAM peak | `memory_profiler` / `tracemalloc` |
| VRAM peak | `nvidia-smi` / `torch.cuda.memory_stats()` |
| CPU utilization | `psutil` |
| Throughput (msg/s) | Misurato su dataset di dimensioni diverse |

Il benchmark deve essere ripetibile e salvato in formato strutturato (JSON/CSV).

---

## R — Roadmap

### FASE 0 — Definizione del problema e requisiti
**Obiettivo**: Chiarire i requisiti, analizzare i dati reali, formalizzare il design.

**Attività**:
- Analisi dei formati di esportazione disponibili (Cellebrite, WhatsApp, ecc.)
- Raccolta e studio di un campione anonimizzato di dati forensi
- Finalizzazione del modello dati interno
- Stesura delle interfacce astratte
- Scrittura degli ADR per le prime decisioni tecniche

**Dipendenze**: Nessuna

**Risultato atteso**: Documento di requisiti, modello dati definitivo, interfacce definite

**Criteri di completamento**: Il modello dati copre tutti i campi identificati. Le interfacce sono scritte (senza implementazione). I formati sorgente sono analizzati.

---

### FASE 1 — Analisi dei dati di input
**Obiettivo**: Comprendere struttura e qualità dei dati reali prima di scrivere codice.

**Attività**:
- Esplorazione manuale dei formati di esportazione disponibili
- Identificazione di anomalie, duplicati, campi mancanti
- Analisi della distribuzione dei tipi di messaggi
- Analisi della presenza di audio/immagini
- Documentazione dei casi limite

**Dipendenze**: Accesso a un campione di dati

**Risultato atteso**: Report di analisi dei dati. Lista di casi limite da gestire.

**Criteri di completamento**: Report scritto, casi limite documentati.

> [!IMPORTANT]
> **Decisione critica**: questa fase determina le priorità degli importer e i casi limite del normalizer. Non saltarla.

---

### FASE 2 — Importer
**Obiettivo**: Implementare il layer di importazione per i formati identificati.

**Attività**:
- Implementazione `BaseImporter`
- Implementazione importer per i formati prioritari (es. Cellebrite CSV/JSON)
- Test con dati reali
- Gestione degli errori di parsing

**Dipendenze**: FASE 0, FASE 1

**Test**: Parsing corretto di ogni formato. Errori gestiti gracefully.

**Criteri di completamento**: Import di un dataset reale senza crash. Tutti i campi del modello dati popolati correttamente.

---

### FASE 3 — Pulizia e normalizzazione
**Obiettivo**: Produrre `UnifiedMessage` puliti e tracciabili da `RawRecord`.

**Attività**:
- Implementazione stadi di normalizzazione (vedi Sezione F)
- Implementazione deduplicatore
- Implementazione normalizzatore timestamp
- Implementazione text cleaner
- Test di regressione per ogni stadio

**Dipendenze**: FASE 2

**Test**: Ogni stadio è testato con input noti (unit test). Test di integrazione su dataset completo.

**Criteri di completamento**: Tasso di messaggi normalizzati con successo >95%. Nessuna modifica silente del contenuto semantico.

---

### FASE 4 — Modello dati interno e storage
**Obiettivo**: Persistenza stabile e query efficienti.

**Attività**:
- Schema SQLite definitivo
- Implementazione `database.py` con operazioni CRUD
- Implementazione `file_store.py`
- Migrazione automatica schema
- Test di performance (insert/select su dataset grande)

**Dipendenze**: FASE 3

**Test**: Round-trip completo (insert → select). Query su 100k messaggi < 1s.

**Criteri di completamento**: Catena di tracciabilità completa navigabile via query.

---

### FASE 5 — Trascrizione audio
**Obiettivo**: STT locale funzionante con buona accuratezza su italiano.

**Attività**:
- Integrazione faster-whisper
- Implementazione pipeline preprocessing audio
- Gestione errori e fallback
- Prima valutazione WER su audio sintetici

**Dipendenze**: FASE 4

**Test**: WER su dataset di test STT.

**Criteri di completamento**: WER < 15% su italiano pulito (target). Tutti i file audio processati senza crash.

---

### FASE 6 — Analisi immagini e OCR
**Obiettivo**: Estrazione locale di testo da immagini con buona accuratezza.

**Attività**:
- Integrazione PaddleOCR (e Tesseract come fallback)
- Implementazione text detection
- Implementazione preprocessing immagini
- Prima valutazione accuratezza OCR

**Dipendenze**: FASE 4

**Test**: Accuratezza OCR su dataset di immagini di test.

**Criteri di completamento**: F1 a livello di parola >80% su screenshot (target). Testo estratto associato al messaggio originale.

---

### FASE 7 — Gestione multilingue
**Obiettivo**: Language detection affidabile e embedding multilingue funzionanti.

**Attività**:
- Integrazione fastText language detection
- Test su messaggi brevi in varie lingue
- Selezione e test del modello embedding multilingue
- Verifica cross-lingual semantic similarity

**Dipendenze**: FASE 4, FASE 5, FASE 6

**Test**: Accuracy language detection per lingua. Similarity cross-lingua su coppie di testo con significato equivalente.

**Criteri di completamento**: Language detection accuracy >90% per lingue principali (IT, EN, FR, DE, ES).

---

### FASE 8 — Embeddings e vector store
**Obiettivo**: Indicizzazione semantica di tutti i messaggi.

**Attività**:
- Implementazione chunker
- Integrazione sentence-transformer
- Integrazione ChromaDB (poi eventualmente FAISS)
- Implementazione caching degli embedding
- Test di performance (embedding di 100k messaggi)

**Dipendenze**: FASE 7

**Test**: Embedding di dataset completo. Verifica che messaggi simili abbiano embedding simili.

**Criteri di completamento**: Tutti i messaggi indicizzati. Ricerca ANN funzionante. Caching verificato.

---

### FASE 9 — Ricerca semantica
**Obiettivo**: Ricerca semantica funzionante con buona precision e recall.

**Attività**:
- Implementazione searcher
- Implementazione re-ranker ibrido (semantic + BM25)
- Implementazione recupero contesto
- Valutazione Precision@K, Recall@K, MRR

**Dipendenze**: FASE 8

**Test**: Valutazione su dataset con ground truth di ricerca.

**Criteri di completamento**: MRR > 0.6 su query di test (target). Contesto recuperato correttamente.

---

### FASE 10 — Topic detection
**Obiettivo**: Identificazione automatica degli argomenti principali.

**Attività**:
- Integrazione BERTopic
- Tuning parametri (UMAP, HDBSCAN)
- Implementazione temporal analysis dei topic
- Valutazione coherence e precisione

**Dipendenze**: FASE 8

**Test**: Topic detection su dataset annotato manualmente.

**Criteri di completamento**: Topic coerenti e interpretabili. Coherence score > soglia stabilita sperimentalmente.

---

### FASE 11 — Rilevamento argomento specifico
**Obiettivo**: Classificatore SÌ/NO/INCERTO funzionante.

**Attività**:
- Implementazione scorer multi-fattore
- Calibrazione soglie
- Valutazione precision/recall/F1

**Dipendenze**: FASE 9, FASE 10

**Test**: Dataset bilanciato con ground truth binaria.

**Criteri di completamento**: F1 > 0.75 su dataset di test (target). Stato INCERTO calibrato correttamente.

---

### FASE 12 — Integrazione LLM locale
**Obiettivo**: LLM locale per labeling topic, sintesi, ragionamento contestuale.

**Attività**:
- Setup Ollama
- Integrazione BaseLLM / OllamaLLM
- Implementazione prompt per labeling topic
- Implementazione prompt per argument reasoning
- Test qualitativo output LLM

**Dipendenze**: FASE 10, FASE 11

**Test**: Valutazione qualitativa output LLM su casi noti.

**Criteri di completamento**: LLM produce output strutturato e coerente. Nessun dato reale inviato a cloud.

---

### FASE 13 — Interfaccia
**Obiettivo**: Interfaccia usabile per un investigatore.

**Attività**:
- CLI completa (Click/Typer)
- Eventuale Web UI (Flask/FastAPI + frontend minimale)
- Export risultati (JSON, CSV, report)

**Dipendenze**: FASE 12

**Criteri di completamento**: Workflow completo end-to-end accessibile dall'interfaccia.

---

### FASE 14 — Ottimizzazione
**Obiettivo**: Rendere il sistema usabile su dataset grandi.

**Attività**:
- Profiling bottleneck
- Ottimizzazione batch embedding
- Ottimizzazione query SQLite (indici)
- Test su dataset sintetico grande (1M messaggi)

**Dipendenze**: FASE 13

---

### FASE 15 — Valutazione sperimentale
**Obiettivo**: Produrre i risultati scientifici della tesi.

**Attività**:
- Costruzione dataset di test finale
- Esecuzione protocolli di valutazione (STT, OCR, search, topic, argument)
- Benchmark hardware su 3 configurazioni
- Analisi e interpretazione risultati

**Dipendenze**: FASE 14

---

### FASE 16 — Documentazione della tesi
**Obiettivo**: Documentazione completa.

**Attività**:
- Stesura capitoli della tesi
- Diagrammi architetturali
- Documentazione API del codice
- README e guida all'installazione

---

## S — Proof of Concept

Prima di implementare l'intero sistema, è fondamentale verificare rapidamente che le componenti critiche funzionino come atteso. Il PoC va realizzato in un notebook Jupyter o in script isolati.

### PoC 1 — Importazione e normalizzazione

**Obiettivo**: Verificare che un piccolo dataset possa essere importato e normalizzato correttamente.

**Input**: 50-100 messaggi in formato CSV/JSON (costruiti manualmente o da export reale)

**Cosa verificare**:
- Parsing corretto dei campi
- Gestione di campi mancanti
- Deduplicazione
- Normalizzazione timestamp
- Catena di tracciabilità

**Criteri di successo**: Ogni messaggio è trasformato in `UnifiedMessage` con tutti i campi valorizzati o flaggati correttamente.

---

### PoC 2 — Trascrizione audio

**Obiettivo**: Verificare faster-whisper su audio di test.

**Input**: 5-10 audio brevi in italiano con testo noto

**Cosa verificare**:
- WER su italiano
- Rilevamento automatico lingua
- Tempo di elaborazione CPU vs GPU
- Gestione audio corrotto

**Criteri di successo**: WER < 20% su italiano pulito. Lingua rilevata correttamente.

---

### PoC 3 — OCR su immagini

**Obiettivo**: Verificare PaddleOCR su screenshot di conversazioni.

**Input**: 10 screenshot costruiti con testo noto + 5 fotografie di documenti

**Cosa verificare**:
- Accuratezza estrazione testo
- Text detection (presenza/assenza testo)
- Score di confidenza

**Criteri di successo**: F1 > 80% su screenshot puliti.

---

### PoC 4 — Embedding multilingue

**Obiettivo**: Verificare che il modello embedding proietti correttamente concetti cross-lingua.

**Test**:
- Coppie di frasi equivalenti in IT/EN: la similarità coseno deve essere alta (>0.8)
- Frasi non correlate: similarità bassa (<0.3)
- Query "esami universitari" → deve essere simile a "appello", "sessione", "professore"

**Criteri di successo**: Le relazioni semantiche attese sono rispettate.

---

### PoC 5 — Ricerca semantica

**Obiettivo**: Verificare che la ricerca semantica trovi messaggi rilevanti senza corrispondenza testuale.

**Input**: 200 messaggi sintetici su 5 topic noti

**Test**: 10 query semantiche diverse per ogni topic

**Criteri di successo**: Precision@5 > 0.6 su query di test.

---

### PoC 6 — Topic detection

**Obiettivo**: Verificare BERTopic su una conversazione sintetica.

**Input**: 500 messaggi sintetici su 5-7 topic noti

**Cosa verificare**:
- Numero di topic trovati vs attesi
- Coerenza dei topic
- Assegnazione dei messaggi ai topic

**Criteri di successo**: Topic principali identificati. Coherence score > 0.4.

---

### PoC 7 — Classificazione argomento

**Obiettivo**: Verificare lo scorer SÌ/NO/INCERTO.

**Input**: 10 conversazioni sintetiche con etichetta GT (topic presente/assente)

**Criteri di successo**: F1 > 0.7 su questo piccolo dataset.

---

## T — Rischi tecnici

### T.1 — Rischi alti

| Rischio | Probabilità | Impatto | Mitigazione |
|---------|-------------|---------|-------------|
| **Qualità bassa degli audio forensi** (rumore, compressione) | Alta | Alto | Valutare WER su audio reali prima di scegliere modello STT. Preprocessing audio aggressivo. |
| **Formati forensi non documentati** | Media | Alto | Analizzare i dati reali nella FASE 1 prima di implementare gli importer. |
| **RAM/VRAM insufficiente** per LLM + embedding simultanei | Media | Alto | Pipeline a stadi. Configurazione automatica del modello in base all'hardware. |
| **Accuratezza OCR bassa** su immagini di qualità forense | Media | Medio | Preprocessing. Valutazione su dati reali. Soglia di confidenza configurabile. |

### T.2 — Rischi medi

| Rischio | Probabilità | Impatto | Mitigazione |
|---------|-------------|---------|-------------|
| **Topic detection instabile** su conversazioni corte | Media | Medio | BERTopic con parametri tuned. Fallback a keyword extraction. |
| **Falsi positivi** nel rilevamento argomento | Alta | Medio | Scoring multi-fattore. Soglie configurabili. Stato INCERTO. |
| **Dipendenze incompatibili** tra librerie AI | Media | Medio | Ambienti virtuali. Dipendenze bloccate. Test CI. |
| **Scalabilità ChromaDB** per corpus molto grandi | Bassa | Medio | Architettura a plugin permette migrazione a FAISS/Qdrant. |

### T.3 — Rischi bassi

| Rischio | Probabilità | Impatto | Mitigazione |
|---------|-------------|---------|-------------|
| **Lingue rare o dialetti** non supportati | Bassa | Basso | Flag `LANGUAGE_UNCERTAIN`. Analisi semantica degradata ma non bloccante. |
| **LLM locale** con output non strutturato | Bassa | Basso | Validazione output LLM. Retry con prompt diverso. Fallback senza LLM. |
| **Emoji e caratteri speciali** rompono il parsing | Bassa | Basso | Test specifici su emoji. Encoding UTF-8 con error handling esplicito. |

### T.4 — Decisioni da posticipare all'analisi dei dati reali

> [!IMPORTANT]
> Le seguenti decisioni NON devono essere prese ora. Devono essere basate sui dati reali.

1. **Strategia di deduplicazione**: dipende da come i duplicati appaiono nel formato sorgente
2. **Soglie del text detector OCR**: dipende dalla qualità delle immagini forensi reali
3. **Modello STT ottimale**: dipende dalla qualità e durata degli audio forensi
4. **Parametri BERTopic** (min_cluster_size, ecc.): dipende dalla densità e lunghezza della conversazione
5. **Soglie scorer argomento**: devono essere calibrate su dati annotati reali
6. **Scelta LLM locale**: dipende dalla qualità del ragionamento sull'italiano e dal hardware disponibile

### T.5 — Aspetti da verificare con esperimenti

> [!NOTE]
> I seguenti aspetti richiedono esperimenti empirici prima di prendere decisioni definitive.

1. **WER di faster-whisper** su audio forensi italiani reali
2. **Accuratezza OCR** su screenshot di conversazioni in condizioni forensi
3. **Qualità topic detection** su conversazioni reali (non sintetiche)
4. **Calibrazione soglie** SÌ/NO/INCERTO su un set annotato
5. **Comparazione embedding** multilingual-e5-base vs LaBSE vs MiniLM su italiano
6. **Velocità embedding** su corpus di 100k messaggi su hardware target

---

## Appendice — Decisioni già consigliabili

Le seguenti scelte tecniche sono già supportate da evidenze sufficienti e possono essere considerate **decisioni preliminari**:

| Componente | Scelta consigliata | Motivazione |
|-----------|-------------------|-------------|
| Linguaggio | Python 3.11+ | Ecosistema AI dominante, librerie disponibili |
| Database strutturato | SQLite | Offline, nessun server, ottimo per dati forensi |
| STT | faster-whisper | Migliore velocità/accuratezza locale, supporto IT |
| Embedding | multilingual-e5-base | Ottimo cross-lingual, supporta 100+ lingue |
| OCR | PaddleOCR | Ottimo su screenshot, multilingue |
| Language detection | fastText lid.176 | Veloce, 176 lingue, leggero |
| Topic detection | BERTopic | Standard de facto per embedding-based topic modeling |
| LLM framework | Ollama | Semplicità, gestione automatica modelli |
| Vector store (PoC) | ChromaDB | Semplice, embedded, metadata integrati |
| Gestione config | YAML + pydantic | Validazione, configurabilità, leggibilità |
| Testing | pytest | Standard Python |
| Architettura | Plugin + interfacce astratte | Modularità, sostituibilità componenti |

---

*Fine del documento tecnico — Versione 0.1*

*Le decisioni architetturali importanti sono documentate come Architecture Decision Records (ADR) nella directory `docs/architecture/ADR/`.*
