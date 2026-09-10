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
* `participant_id: str`: ID deterministico (es. `participant:entity_candidate:1` o `participant:LOCAL_USER`).
* `identifier: str`: identificatore normalizzato (numero telefonico canonicalizzato, JID, alias, o `LOCAL_USER`).
* `display_name: str | None`: nome descrittivo desunto deterministicamente (es. da `wa.db` contacts), se disponibile.
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

## 3. Costruzione e Pipeline (`unified/builder.py`)

La classe `UnifiedModelBuilder` gestisce l'assemblaggio dei messaggi unificati:

1. **Indicizzazione della Risoluzione**:
   - Pre-indicizza le `CandidateEntity` per reference `(source_name, source_record_id, actor_role)` e per identificatore normalizzato per garantire lookup deterministico $O(1)$.
   - Pre-indicizza i `DuplicateCandidate` per collegare istantaneamente ogni messaggio al suo cluster di duplicazione.
2. **Pre-scansione Contenitori Chat**:
   - Pre-indicizza i record descrittori `chat` (come le righe di `chat_list` in `msgstore.db`) per recuperare i titoli delle conversazioni e gli oggetti di gruppo.
3. **Elaborazione e Streaming**:
   - Filtra i record non di tipo messaggio (`contact`, `chat`, `media_ref`) senza emettere messaggi unificati spuri.
   - Assembla mittente e destinatario valorizzando `is_local_user` e `entity_candidate_id`.
   - Assegna `chat_type='group'` quando `chat_id` contiene `@g.us`, o `chat_type='direct'` per conversazioni 1-to-1.
   - Fornisce sia interfaccia streaming (`build_stream`) che materializzata (`build_all`).

---

## 4. Metriche di Verifica sui Dataset Sintetici Reali

| Sorgente | Record Raw Totali | Record Messaggio | UnifiedMessage Generati | Stato Timestamp Prevalente | Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `msgstore.db` | 622 | 504 | 504 | `KNOWN_UTC` | 18 record `chat`, 100 `media_ref` utilizzati come contesto |
| `wa.db` | 4 | 0 | 0 | N/A | 4 record `contact` impiegati per Entity Resolution |
| `messages.csv` | 302 | 302 | 302 | `NAIVE_UNKNOWN` | Nessuna assunzione di timezone; timestamp preservati |
| `messages.json` | 100 | 100 | 100 | `KNOWN_UTC` | Parsing streaming `ijson`, mittenti mappati |
| `report.xml` | 50 | 50 | 50 | `KNOWN_UTC` | Parsing protetto `defusedxml` |
| **TOTALE** | **1078** | **956** | **956** | - | **0 messaggi persi, 100% provenance preservata** |
