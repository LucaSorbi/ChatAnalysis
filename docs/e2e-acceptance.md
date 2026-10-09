# Full E2E System Acceptance Document — Pre-Hardware Gate

## 1. Scope del Gate di Accettazione

Il **Full E2E System Acceptance — Pre-Hardware Gate** costituisce la validazione complessiva, end-to-end e integrata del software forense implementato nel repository, prima dell'esecuzione dei benchmark sperimentali con modelli LLM locali su workstation dotata di set istruzioni AVX2.

L'obiettivo primario del gate è certificare:
1. La piena operatività della pipeline cross-layer dall'ingestion alla presentazione grafica.
2. Il determinismo dell'importazione e dell'indicizzazione forense verificato sui dataset di test (assenza di UUID casuali).
3. L'integrità referenziale della catena di provenance per ogni evidenza.
4. Il comportamento transazionale dello stato applicativo in caso di upload non validi, preservando il dataset precedentemente caricato.
5. L'audit sul trattamento dei riferimenti multimediali (assenza di trascrizioni o descrizioni visive fittizie su file reali in assenza di file fisici).
6. L'assenza di connessioni verso reti esterne (**EXTERNAL NETWORK REQUIRED: NO**) e l'assenza di invocazioni di client LLM su file reali.
7. La stabilità dell'interfaccia Streamlit verificata tramite test headless automatizzati con nessuna failure nella suite corrente.

---

## 2. Percorsi E2E Verificati e Fixture Forensi

Tutti i percorsi sono stati verificati tramite test end-to-end automatizzati ([test_full_pipeline_e2e.py](file:///C:/Users/lucas/Desktop/Tesi/tests/integration/test_full_pipeline_e2e.py)) impiegando esclusivamente le fixture sintetiche autoritative presenti in `test_data/`:

| Percorso / Formato Sorgente | Fixture di Riferimento | Record Raw | Messaggi Unificati | Conversazioni | Record Ausiliari | Note e Partizionamento |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **WhatsApp msgstore** | `test_data/whatsapp_export/msgstore.db` | 622 | 504 | 3 | 118 | 115 media_refs preservati; 3 chat distinte |
| **WhatsApp msgstore + wa.db** | `msgstore.db` + `wa.db` | 622 + 4 | 504 | 3 | 122 | Arricchimento dei `display_name` dei contatti |
| **WhatsApp wa.db Standalone** | `test_data/whatsapp_export/wa.db` | 4 | 0 | 0 | 4 | Stato `AUXILIARY_ONLY`; nessun doc fittizio |
| **Cellebrite CSV** | `test_data/cellebrite_export/messages.csv` | 302 | 302 | 3 | 0 | Ripartizione multi-conversazione |
| **Cellebrite JSON** | `test_data/cellebrite_export/messages.json` | 100 | 100 | 3 | 0 | Report strutturato multi-conversazione |
| **Cellebrite XML** | `test_data/cellebrite_export/report.xml` | 50 | 50 | 1 | 0 | Partizione sicura `UNRESOLVED` |
| **Modalità Dimostrativa** | Generatore puro `ui.demo` | N/A | 8 bundle | 1 | N/A | 12 sezioni, 5 tipi probatori, Topic precomputati |

---

## 3. Determinismo Forense e Re-Import Identico

Il re-import dello stesso file identico (`msgstore.db` e `messages.csv`) eseguito due volte in esecuzioni consecutive è risultato deterministico per le fixture esaminate:
- **Identità Metrica**: Conteggi identici di record raw, messaggi unificati, conversazioni estratte e SHA-256.
- **Identità Identificatori**: I `document_id` generati secondo la policy `doc::{source_name}::{file_sha256[:12]}::{chat_hash}` risultano identici e nello stesso ordine.
- **Identità Evidenze**: La sequenza degli `evidence_id`, il loro contenuto e l'ordinamento naturale risultano identici al 100%.
- **Identità Risultati di Ricerca**: La medesima `EvidenceSearchQuery` produce la stessa lista di hit nello stesso ordine.
- **Assenza di UUID Casuali**: Nessun identificatore è generato tramite entropia pseudo-casuale; ogni chiave è funzione deterministica dei dati di ingresso.

---

## 4. Integrità della Provenance e Invarianti

Per ogni documento estratto dalla pipeline:
1. **Bundle -> UnifiedMessage**: Ogni `MessageEvidenceBundle` incapsula un `UnifiedMessage` valido, immutabile e congelato strutturalmente.
2. **Sezione -> Messaggio**: Ogni `TextEvidenceSection` referenzia coerentemente `message_id`, `source_name` e `source_record_id` del messaggio padre.
3. **Univocità Evidence ID**: Nessuna collisione di `evidence_id` all'interno dell'indice o della conversazione.
4. **Tracciabilità della Sorgente**: Nessuna evidenza derivata (trascrizione, OCR, osservazione visiva) viene presentata come `ORIGINAL_TEXT`.

---

## 5. Audit Multimodale (Handoff e File Fisici)

> [!IMPORTANT]
> **REAL PHYSICAL MEDIA PROCESSING: NOT EXECUTED IN THIS PRE-HARDWARE GATE.**

- **Preservazione Riferimenti**: Nei dataset reali (es. WhatsApp msgstore con 115 record recanti `media_reference`), il puntamento all'allegato è conservato su `UnifiedMessage.media_reference`.
- **Assenza di Simulazioni Fittizie**: In assenza di file fisici reali (audio opus, immagini jpeg/png), la pipeline:
  - **NON** istanzia `ResolvedMediaAsset`.
  - **NON** genera testi per STT (`STT_TRANSCRIPTION`), OCR (`OCR_TEXT`) o Vision (`VISION_DESCRIPTION`, `VISION_OBSERVATION`).
  - Tutte le sezioni testuali nei documenti REAL FILE appartengono al tipo probatorio primario `ORIGINAL_TEXT`.
- **Verifica Funzionale nella Modalità Demo**: La modalità sintetica `ui.demo` espone tutti e 5 i tipi probatori per verificare l'architettura e i filtri UI in presenza di evidenze multimodali.

---

## 6. Isolamento AI su File Reali e Assenza di Rete Esterna

1. **Zero LLM su File Reali**:
   - Per `DatasetMode.FILE`, `detection_results = ()` e `discovery_results = ()`.
   - Né `FakeLocalLlmClient` né `LmStudioClient` vengono istanziati durante il parsing, l'ingestion o l'esplorazione dei file reali.
   - La schermata Analisi Topic visualizza lo stato corretto: inferenza AI non eseguita, benchmark differito alla fase finale.
2. **EXTERNAL NETWORK REQUIRED: NO**:
   - Durante l'intero ciclo di vita (ingestion, cambio conversazione, ricerca lessicale, navigazione Streamlit), nessun tentativo di rete esterna è stato osservato durante i percorsi testati.
   - Il test intercetta `socket.socket.connect` consentendo unicamente le connessioni locali loopback necessarie al framework (`127.0.0.1`, `::1`, `localhost`, `0.0.0.0`) e bloccando categoricamente qualsiasi tentativo di connessione verso indirizzi esterni.

---

## 7. Motore di Ricerca End-to-End (Search Layer)

Verificato sui dataset di test su tutte le 4 sorgenti reali con le 4 modalità di matching autoritative:
- `MatchMode.PHRASE`: Corrispondenza della sequenza di termini con hit verificati (`total_hits > 0`).
- `MatchMode.ALL_TERMS`: Tutti i termini presenti indipendentemente dall'ordine con hit verificati (`total_hits > 0`).
- `MatchMode.ANY_TERM`: Almeno un termine presente con hit verificati (`total_hits > 0`).
- `MatchMode.EXACT`: Corrispondenza integrale su sezioni di evidenza reali selezionate deterministicamente, con riscontro puntuale dell'`evidence_id` target tra gli hit restituiti.

Per ciascuna modalità è verificato l'ordine deterministico dei risultati su ricerche ripetute (`[h.evidence_id for h in r1.hits] == [h.evidence_id for h in r2.hits]`).

Ogni hit restituito espone `source_name`, `source_record_id`, `original_text` e `evidence_id`, garantendo verificabilità immediata all'analista forense.

---

## 8. Transizioni di Stato di Sessione e Transazionalità dei Fallimenti

### 8.1 Ciclo di Vita delle Transizioni
Il ciclo `NONE -> DEMO -> FILE -> FILE B -> DEMO -> RESET / NONE` è stato validato con assenza di dati residui:
- Al passaggio a `FILE`, i risultati topic demo vengono azzerati.
- Al passaggio tra file diversi (`FILE A -> FILE B`), `SearchService` viene riallineato puntando al documento e all'indice del nuovo file.
- Il reset della sessione ripristina la totale assenza di dataset in memoria.

### 8.2 Comportamento Transazionale su Upload Non Validi
Qualora una sessione attiva (es. Demo o file reale) subisca un tentativo di caricamento di un file non conforme (file vuoto a 0 byte, firma SQLite errata, formato non supportato):
- L'errore viene intercettato prima di alterare lo stato applicativo.
- Il dataset precedentemente caricato rimane integro, attivo e consultabile, senza alterazioni parziali di stato.

---

## 9. Pulizia Ambientale e Gestione dei File Temporanei

- **Sandbox Volatile**: Ogni ingestion crea una cartella temporanea isolata con prefisso `forensic_ingest_`.
- **Successo**: Cartella e file eliminati al termine del processo.
- **Fallimento**: Cartella eliminata anche a seguito di eccezione durante il parsing o la normalizzazione.
- **Path Traversal**: Nomi file arbitrari (es. `../../evil.db`) vengono ridotti al basename sicuro senza alcuna scrittura al di fuori dell'area sandbox.

---

## 10. Etichette di Conversazione Pseudonimizzate e Riproducibili

Le etichette descrittive (`ImportedConversationInfo.display_label`) rispettano i criteri forensi:
- Prive di JID WhatsApp, numeri di telefono, titoli o testi raw.
- Formato: `Conversazione {idx} — {N} messaggi — [{short_hash}]` (oppure `Messaggi non associati — {N} messaggi — [{short_hash}]`).
- Lo `short_hash` è estratto dal segmento hash terminale del `document_id`, assicurando che conversazioni diverse della stessa sorgente abbiano suffissi distinti e identificabili.

---

## 11. Accettazione Streamlit AppTest Headless

L'applicazione grafica [app.py](file:///C:/Users/lucas/Desktop/Tesi/app.py) è stata testata con `streamlit.testing.v1.AppTest`:
- Avvio e navigazione verificati attraverso tutte le 6 sezioni (Panoramica, Importazione, Esplora, Ricerca, Topic, Stato).
- Caricamento ed esplorazione in modalità DEMO.
- Ingestion reale con selectbox multi-conversazione attiva.
- Visualizzazione Panoramica con label pseudonimizzata e confinamento del `chat_id` tecnico nell'expander.
- Esplorazione e Ricerca in Real File mode.
- Gestione avviso rinvio AI in Analisi Topic.
- Reset della sessione.
- **Esito**: nessuna eccezione o failure nella suite corrente.

---

## 12. Osservazioni Diagnostiche Prestazionali

Le rilevazioni temporali registrate sull'ambiente di test hanno scopo puramente diagnostico e non definiscono vincoli bloccanti di PASS/FAIL:
- **Ingestion WhatsApp msgstore.db (622 record raw)**: ~`0.266 s`
- **Esecuzione Query di Ricerca Lessicale (110 hit)**: ~`0.0015 s` (1.5 millisecondi)

Il comportamento osservato dimostra tempi di risposta sub-secondo per le operazioni locali sul sistema analizzato.

---

## 13. Architettura Sperimentale e Attività della Fase Finale

1. **Portabilità e Disaccoppiamento Host**: L'applicazione e la UI sono indipendenti dall'hardware host. La configurazione e i dettagli di sistema vengono rilevati e registrati nei report del benchmark sperimentale.
2. **Benchmark LM Studio**: Eseguito separatamente tramite la suite sperimentale dedicata (`ai/experiment.py` / `run_benchmark.sh`) su workstation con supporto GPU/AVX2.
3. **Multimodal Processing con File Binari Fisici**: Elaborazione con trascrizione STT e modelli Vision delegata alla fase sperimentale con acquisizioni complete comprensive di dump multimediali fisici.
