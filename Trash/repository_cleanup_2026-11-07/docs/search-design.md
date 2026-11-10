# Search Layer Design: Architettura, Modelli e Fondamenta Tecniche

**Repository**: `c:\Users\lucas\Desktop\Tesi`  
**Autore**: Agente di Sviluppo Software  
**Stato**: FINAL SEARCH ACCEPTANCE GATE SUPERATO — STREAMLIT READY  
**Pipeline**:  
DATI ORIGINALI → IMPORTER → VALIDAZIONE → NORMALIZZAZIONE → ENTITY RESOLUTION → UnifiedMessage → MULTIMODAL → MessageEvidenceBundle → ConversationEvidenceDocument → LOCAL AI → SEARCH LAYER → STREAMLIT UI (Future)  

---

## 1. Posizione del Search Layer nella Pipeline Forense

Il Search Layer si inserisce a valle del Multimodal Processing e del Local AI Layer, fungendo da componente di indicizzazione, interrogazione, filtraggio deterministico e preparazione per la futura interfaccia grafica (Streamlit UI).

```text
+-----------------------+     +-------------------------------+
|    MULTIMODAL LAYER   |     |        LOCAL AI LAYER         |
| MessageEvidenceBundle |     | TopicDetection / Discovery    |
+-----------+-----------+     +---------------+---------------+
            |                                 |
            +----------------+----------------+
                             |
                             v
               +---------------------------+
               |        SEARCH LAYER       |
               | - EvidenceIndex           |
               | - EvidenceSearchEngine    |
               | - TopicSearchEngine       |
               | - SearchService           |
               +-------------+-------------+
                             |
                             v (SearchViewResult)
               +---------------------------+
               |     FUTURE STREAMLIT UI   |
               +---------------------------+
```

### Separazione Netta: AI vs SEARCH
- **LOCAL AI**: Ha la responsabilità esclusiva dell'inferenza semantica ad alto livello (Topic Detection mirata, Open Topic Discovery induttiva, traduzione derivata). Produce modelli immutabili (`TopicDetectionResult`, `TopicDiscoveryResult`).
- **SEARCH LAYER**: Consuma strutture dati preesistenti e immutabili. Non invoca modelli linguistici, non calcola embedding vettoriali e non altera i testi sorgente. Permette di trovare, filtrare, ordinare in modo deterministico e risolvere le evidenze probatorie con tracciabilità forense completa.

---

## 2. Modelli Dati Immutabili e Invarianti di Provenance (`search/models.py`)

Tutti i modelli del package adottano il principio della *Deep Immutability*:
- `@dataclass(frozen=True)`
- Congelamento ricorsivo di dizionari tramite `core.immutability.freeze_structural` (esposizione come `MappingProxyType`)
- Sequenze rigorosamente convertite in `tuple`.

### 2.1 `MatchMode` (Enum)
- `PHRASE`: la stringa query deve comparire come sequenza contigua (sottostringa post-normalizzazione).
- `ALL_TERMS`: tutti i termini della query devono essere presenti come token interi nell'evidenza (AND logico).
- `ANY_TERM`: almeno uno dei termini della query deve essere presente come token intero nell'evidenza (OR logico).
- `EXACT`: corrispondenza identica dell'intero testo dell'evidenza (post-normalizzazione).

### 2.2 Invarianti Rigorosi di `EvidenceSearchHit`
`EvidenceSearchHit.__post_init__()` valida ciascun campo rispetto alla `TextEvidenceSection` collegata:
- `evidence_id == section.evidence_id`
- `source_type == section.source_type`
- `message_id == section.message_id`
- `source_name == section.source_name`
- `source_record_id == section.source_record_id`
- `language == section.language`
- `original_text == section.text`
Qualsiasi discordanza solleva immediatamente `ValueError`.

### 2.3 Invarianti di `TopicSearchHit`
Quando l'indice è disponibile e `matched_sections` è popolato:
- Cardinalità identica: `len(evidence_ids) == len(matched_sections)`.
- Corrispondenza d'ordine uno a uno: per ogni indice `idx`, `evidence_ids[idx] == matched_sections[idx].evidence_id`.

### 2.4 Nuova Semantica DTO per Streamlit: `SearchViewResult`
Per evitare qualsiasi ambiguità semantica prima dell'integrazione con Streamlit:
- **`display_text`**: testo principale comune da mostrare nell'interfaccia.
  - Per `EVIDENCE`: è il testo originale dell'evidenza.
  - Per `TOPIC_DETECTION` o `TOPIC_DISCOVERY`: è la descrizione o motivazione del topic.
- **`original_text`**: presente e valorizzato **ESCLUSIVAMENTE** per i riscontri di tipo `EVIDENCE`. Per i risultati AI generati è rigorosamente `None` (una sintesi o descrizione AI non viene mai qualificata come "testo originale forense").
- **Disaccoppiamento totale**: nessun tag HTML, nessun markup Streamlit, nessun tipo dipendente dal framework UI.

---

## 3. Normalizzazione e Tokenizzazione Whole-Term (`search/normalization.py`)

### 3.1 Definizione Formale di "Termine" (Term / Token)
Un **termine** è una sequenza contigua massimale di caratteri alfanumerici Unicode (glifi alfabetici di qualsiasi alfabeto con i rispettivi segni diacritici/accenti combinati con cifre numeriche), delimitata da spaziature (` `, `\t`, `\n`) o da qualsiasi segno di interpunzione e simbolo speciale (`.,;:!?()[]{}"'-/`).

### 3.2 Tokenizzazione Deterministica Unicode
La funzione `tokenize_terms(text)` applica:
1. Normalizzazione `Unicode NFKC + casefold()`.
2. Estrazione di sequenze con pattern standard library `r'\w+'` con flag `re.UNICODE`.
3. Nessuna rimozione di accenti: `caffè` produce il token `'caffè'`, distinto da `'caffe'`.
4. Eliminazione dei falsi positivi da sottostringa: la query `art` genera il token `'art'`, che **NON** matcha il testo `'partita'` (token `'partita'`), ma matcha correttamente `'(art. 5)'` (token `'art'`, `'5'`).

---

## 4. Indicizzazione e Risoluzione Deterministica (`search/index.py`)

La classe `EvidenceIndex` costruisce in memoria una mappa `evidence_id -> TextEvidenceSection` a partire da un `ConversationEvidenceDocument`:
- **Unicità e Integrità**: Rifiuto immediato di duplicati con `EvidenceIntegrityError`.
- **Risoluzione Negativa Controllata**: `.get()` restituisce `None`, `.resolve()` solleva `EvidenceNotFoundError`.
- **Ordine Naturale Preservato**: L'indice conserva la sequenza naturale del documento (`doc.bundles` → `bundle.text_evidence_sections`).

---

## 5. Filtraggio, Semantica Total Hits e Limit (`search/engine.py`)

### 5.1 Semantica di `total_hits` vs `limit`
- **`total_hits`**: rappresenta il numero totale effettivo di evidenze che soddisfano sia i criteri testuali (match mode) che tutti i filtri attivi (`source_types`, `language`, `source_name`).
- **`hits`**: tupla contenente i primi N risultati fino al `limit` impostato (se presente).
- **Metadati**: l'oggetto `EvidenceSearchResult.metadata` traccia `returned_hits`, `total_hits`, `truncated` (booleano) e `document_id`.

---

## 6. Risoluzione Strict e Validazione Provenance dei Topic AI (`search/topics.py`)

### 6.1 Strict Topic → Evidence Provenance
Il motore tematico `TopicSearchEngine` opera esclusivamente in modalità **STRICT**:
- Se un `TopicDetectionResult` o `TopicDiscoveryResult` cita uno o più `evidence_ids`, **TUTTI** gli identificatori devono essere fisicamente presenti nell'indice.
- Qualsiasi `evidence_id` inesistente solleva immediatamente l'eccezione tipizzata `TopicEvidenceIntegrityError`.

### 6.2 Validazione Document Provenance
Se l'indice ha un `document_id` valorizzato, viene verificato che:
`result.provenance_document_id == index.document_id`
Se il risultato AI appartiene a un altro documento di conversazione, l'integrazione viene bloccata sollevando `TopicEvidenceIntegrityError`.

---

## 7. Sorgente Autorevole Unica in `SearchService` (`search/service.py`)

Per eliminare qualsiasi ambiguità architetturale, `SearchService.__init__()`:
- Accetta `document` **oppure** `index`.
- Se entrambi vengono passati contemporaneamente, solleva immediatamente un `ValueError` ("Fornire 'document' o 'index', non entrambi contemporaneamente").

---

## 8. Divieto Assoluto di Ricerca Semantica

In conformità ai requisiti scientifici e metodologici della tesi:
- Nessun modello di embedding (Sentence-Transformers, OpenAI, ecc.).
- Nessun vector database (FAISS, Chroma, Qdrant, Milvus).
- Nessun calcolo di cosine similarity o recupero denso.
- La ricerca è al 100% deterministica, verificabile, testabile offline e priva di allucinazioni probabilistiche.
