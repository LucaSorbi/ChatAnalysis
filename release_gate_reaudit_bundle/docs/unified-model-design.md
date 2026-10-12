# Unified Model Foundation Design

## 1. Visione Architetturale e Scopo

Il package `unified/` implementa la **Unified Model Foundation**, il sesto stadio della pipeline forense rigorosa:

$$\text{DATI ORIGINALI} \longrightarrow \text{IMPORTER} \longrightarrow \text{RawRecord} \longrightarrow \text{VALIDAZIONE} \longrightarrow \text{NORMALIZZAZIONE} \longrightarrow \text{ENTITY RESOLUTION} \longrightarrow \text{UnifiedMessage} \longrightarrow \text{ANALISI AI}$$

### Principi Architetturali Fondamentali

1. **Rappresentazione Canonica Non-Distruttiva**:
   - `UnifiedMessage` non elimina né comprime record sorgente.
   - Ogni messaggio presente nelle 5 sorgenti supportate genera un'istanza distinta e immutabile di `UnifiedMessage`.
   - Eventuali messaggi identici cross-source o intra-source vengono associati tramite `duplicate_candidate_ids` ma rimangono integralmente preservati.
2. **Piena Provenance Forense**:
   - Ogni `UnifiedMessage` mantiene un riferimento diretto al `NormalizedRecord` d'origine (`provenance_record`), che a sua volta incapsula il `RawRecord` nativo e il `ValidationResult`.
   - Questo consente a qualsiasi perizia tecnica di risalire determinata informazione (testo, timestamp, mittente) al file sorgente originale, alla riga o all'offset esatto.
3. **Integrità Temporale Forense**:
   - Nessuna forzatura di timestamp naive a fusi orari arbitrari.
   - I messaggi Cellebrite CSV senza offset conservano lo stato `NAIVE_UNKNOWN`.
   - Non viene effettuata alcuna fusione forzata di timeline globale tra sorgenti con fuso noto (`KNOWN_UTC`) e sorgenti senza fuso (`NAIVE_UNKNOWN`).
4. **Identificatori Deterministici**:
   - Ogni `UnifiedMessage` possiede un `message_id` conforme allo standard:
     `unified:{source_name}:{source_record_id}`
     (es. `unified:msgstore_db:101`, `unified:cellebrite_csv:row:1`).
5. **Separazione Rigorosa di Attori e Chat**:
   - I JID individuali WhatsApp (`@s.whatsapp.net`) e i numeri telefonici internazionali identificano persone/partecipanti.
   - I JID di gruppo WhatsApp (`@g.us`) identificano esclusivamente contenitori di conversazione (`Chat`), non persone.
   - Il proprietario del dispositivo è tracciato deterministicamente come `LOCAL_USER` (`is_local_user=True`), senza attribuzione fittizia di numeri o nomi di fantasia.

---

## 2. Modelli Dati (`unified/models.py`)

### 2.1 `Participant`
Rappresenta un attore mittente o destinatario associato a una conversazione:
* `participant_id: str`: ID deterministico e non ambiguo con prefisso di tipo:
  - Entità JID risolta: `participant:jid:<canonical_jid>`
  - Entità telefonica risolta: `participant:phone:<canonical_phone>`
  - Ruolo tecnico locale: `participant:local_user:LOCAL_USER`
  - Gruppo come attore: `participant:group:<group_jid>` (con `group_jid_as_actor=True`, `display_name=None`)
  - Partecipante unresolved: `participant:unresolved:<source_name>:<source_record_id>:<role>` (scoping rigoroso per evitare collisioni spurie tra attori non risolti di sorgenti o record diversi).
* `identifier: str`: identificatore normalizzato (numero telefonico canonicalizzato, JID, alias, o `LOCAL_USER`).
* `display_name: str | None`: nome descrittivo desunto deterministicamente (es. da `wa.db` contacts), se disponibile (sempre `None` per `LOCAL_USER` e gruppi come attori).
* `entity_candidate_id: str | None`: puntatore al cluster di risoluzione identità (`CandidateEntity.candidate_id`), se risolto.
* `is_local_user: bool`: flag che segnala se l'attore è il proprietario del dispositivo sottoposto ad acquisizione forense.
* `metadata: MappingProxyType[str, Any]`: metadati immutabili.

### 2.2 `Chat`
Rappresenta il contenitore di conversazione (1-to-1 o di gruppo):
* `chat_id: str`: identificativo canonico (es. `chat:msgstore_db:00000000001-0000000000@g.us`).
* `chat_type: str`: tipologia strutturale (`direct`, `group`, `unknown`).
* `title: str | None`: titolo o oggetto della conversazione (es. ricavato dalla tabella `chat_list` di WhatsApp).
* `participants: tuple[Participant, ...]`: partecipanti noti alla chat.
* `source_name: str`: sorgente d'origine.
* `metadata: MappingProxyType[str, Any]`: metadati immutabili.

### 2.3 `UnifiedMessage`
Rappresentazione immutabile e verificabile del singolo messaggio:
* `message_id: str`: ID univoco deterministico `unified:{source_name}:{source_record_id}`.
* `source_name: str`: nome della sorgente (`msgstore_db`, `cellebrite_csv`, `cellebrite_json`, `cellebrite_xml`).
* `source_record_id: str`: ID primario nella sorgente originale.
* `source_path: str`: percorso assoluto del file forense sorgente.
* `record_type: str`: tipologia record sorgente (`message`).
* `timestamp: NormalizedTimestamp`: struttura temporale rigida con `status` (`KNOWN_UTC`, `NAIVE_UNKNOWN`, `ABSENT`), `utc_datetime`, `naive_datetime`, `iso_string`, `raw_value`.
* `sender: Participant | None`: partecipante mittente.
* `recipient: Participant | None`: partecipante destinatario.
* `chat: Chat | None`: conversazione di appartenenza.
* `message_type: CanonicalMessageType`: tassonomia canonica (`TEXT`, `IMAGE`, `AUDIO`, `VIDEO`, `SYSTEM`, `OTHER`, `UNKNOWN`).
* `raw_message_type: Any`: valore originale grezzo del tipo messaggio.
* `text_content: str | None`: corpo testuale del messaggio.
* `media_reference: str | None`: path o riferimento al file multimediale allegato.
* `is_deleted: bool | None`: flag di cancellazione, se supportato dalla sorgente.
* `raw_deleted: Any`: valore grezzo originale del campo deleted.
* `duplicate_candidate_ids: tuple[str, ...]`: riferimenti ai duplicati identificati da Entity Resolution.
* `provenance_record: NormalizedRecord`: puntatore all'intero record del layer precedente.
* `metadata: MappingProxyType[str, Any]`: metadati immutabili aggiuntivi.

---

## 3. Costruzione e Pipeline (`unified/builder.py`, `unified/context.py`)

### 3.1 Disaccoppiamento del Contesto (`UnifiedBuildContext`) e Semantica One-Shot
Per garantire la purezza funzionale e l'indipendenza dall'ordine di arrivo dei record, il layer introduce `UnifiedBuildContext`:
1. **Indicizzazione della Risoluzione**:
   - Pre-indicizza le `CandidateEntity` per reference `(source_name, source_record_id, actor_role)` e per identificatore normalizzato per garantire lookup deterministico $O(1)$.
   - Pre-indicizza i `DuplicateCandidate` per collegare istantaneamente ogni messaggio ai candidati duplicati.
2. **Pre-scansione Contenitori Chat**:
   - Pre-scansiona i record descrittori `chat` (come le 3 righe di `chat_list` in `msgstore.db`) prima della fase di emissione dei messaggi, registrando i titoli delle conversazioni e gli oggetti dei gruppi.
   - Tutti gli indici e le mappe sono congelati con immutabilità profonda (`freeze_structural` da `core.immutability`).
3. **Semantica One-Shot e Trasparenza Iteratori**:
   - `UnifiedBuildContext.from_records(records: Sequence[NormalizedRecord])`: richiede esplicitamente una sequenza ri-iterabile e indicizzabile.
   - `UnifiedBuildContext.from_records_and_stream(records: Iterable[NormalizedRecord]) -> tuple[UnifiedBuildContext, list[NormalizedRecord]]`: helper trasparente per flussi generatore monouso; consuma il flusso in un singolo passaggio, istanzia il contesto e restituisce la lista di record per la successiva costruzione dei messaggi.
4. **Elaborazione e Streaming**:
   - `build_stream(records)` è un generatore puro a singolo passaggio: non effettua scansioni condizionali, non accumula record in memoria e non muta lo stato interno durante l'iterazione.
   - `build_all(records)` è implementato semplicemente e rigorosamente come `list(self.build_stream(records))`.
   - Viene garantita la totale equivalenza tra streaming e materializzazione: `list(builder.build_stream(records)) == builder.build_all(records)`.

### 3.2 Regole di Costruzione Forense
- **Attori non risolti**: attori privi di identificatore telefonico o JID (es. `group_participant_A`) mantengono `display_name = None`, `entity_candidate_id = None`, metadato `{"unresolved": True}` e `participant_id = "participant:unresolved:<source_name>:<record_id>:<role>"`.
- **Tracciamento LOCAL_USER**: il proprietario del dispositivo forense ha `participant_id = "participant:local_user:LOCAL_USER"`, `identifier = "LOCAL_USER"`, `display_name = None` (nessun nome sintetico improprio), `is_local_user = True` e metadato con `technical_role = True`.
- **Gruppi come attori**: se un record ha come actor un JID di gruppo (`@g.us`), non viene mai promosso a persona (nessun `display_name` fasullo); riceve `participant_id = "participant:group:<group_jid>"`, `group_jid_as_actor = True`, `is_group = True` e collegamento facoltativo all'entità gruppo.
- **Chat per sorgenti eterogenee**:
  - `chat_1` in Cellebrite CSV possiede `chat_type = "unknown"` (nessuna presunzione di chat diretta o gruppo).
  - Record senza `chat_id` e con destinatario generico: `chat_type = "unknown"`. Solo in presenza di JID individuale esplicito (`@s.whatsapp.net`) viene assegnato `chat_type = "direct"`.
  - Record con `@g.us` in `chat_id`: `chat_type = "group"`.
  - Record UFDR XML privi di chat/session: `chat = None`.

---

## 4. Metriche di Verifica sui Dataset Sintetici Reali

### 4.1 Conteggi e Provenance per Sorgente
| Sorgente | Record Raw Totali | Record Messaggio | UnifiedMessage Generati | Stato Timestamp Prevalente | Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `msgstore.db` | 622 | 504 | 504 | `KNOWN_UTC` (502) / `ABSENT` (2) | 3 record `chat`, 115 `media_ref` utilizzati come contesto |
| `wa.db` | 4 | 0 | 0 | N/A | 4 record `contact` impiegati per Entity Resolution |
| `messages.csv` | 302 | 302 | 302 | `NAIVE_UNKNOWN` (301) / `ABSENT` (1) | Nessuna assunzione di timezone; timestamp preservati |
| `messages.json` | 100 | 100 | 100 | `KNOWN_UTC` (100) | Parsing streaming `ijson`, mittenti mappati |
| `report.xml` | 50 | 50 | 50 | `KNOWN_UTC` (50) | Parsing protetto `defusedxml`, nessun tag chat in UFDR |
| **TOTALE** | **1078** | **956** | **956** | - | **0 messaggi persi, 100% provenance preservata** |

### 4.2 Dettaglio Distribuzione Fusi Orari
- **`msgstore_db`** (504): `KNOWN_UTC`: 502, `ABSENT`: 2 (record 502 ts=0, record 504 ts=-1000), `NAIVE_UNKNOWN`: 0.
- **`cellebrite_csv`** (302): `NAIVE_UNKNOWN`: 301, `ABSENT`: 1 (riga 15 stringa vuota), `KNOWN_UTC`: 0.
- **`cellebrite_json`** (100): `KNOWN_UTC`: 100, `NAIVE_UNKNOWN`: 0, `ABSENT`: 0.
- **`cellebrite_xml`** (50): `KNOWN_UTC`: 50, `NAIVE_UNKNOWN`: 0, `ABSENT`: 0.
- **Totale complessivo**: `KNOWN_UTC`: 652, `NAIVE_UNKNOWN`: 301, `ABSENT`: 3 (Somma: 956 messaggi).

### 4.3 Dettaglio Distribuzione Tipi Chat
- **`msgstore_db`** (504): `direct`: 334, `group`: 170, `unknown`: 0, `chat=None`: 0.
- **`cellebrite_csv`** (302): `direct`: 0, `group`: 0, `unknown`: 302 (`chat_1`, `chat_2`, `chat_3`), `chat=None`: 0.
- **`cellebrite_json`** (100): `direct`: 0, `group`: 0, `unknown`: 100, `chat=None`: 0.
- **`cellebrite_xml`** (50): `direct`: 0, `group`: 0, `unknown`: 0, `chat=None`: 50 (nessun contenitore chat in export UFDR).
- **Totale complessivo**: `direct`: 334, `group`: 170, `unknown`: 402, `chat=None`: 50 (Somma: 956 messaggi).
