# Entity Resolution Layer Design

**Versione:** 1.0.0  
**Data:** 10 Settembre 2026  
**Autore:** Forensic Software Engineering Agent  
**Stato:** Approvato e Conforme  

---

## 1. Principi e Confini Architetturali

Il layer di **Entity Resolution Foundation** costituisce il quinto stadio obbligatorio e non invertibile della pipeline:

$$\text{DATI ORIGINALI} \longrightarrow \text{IMPORTER} \longrightarrow \text{RawRecord} \longrightarrow \text{VALIDAZIONE} \longrightarrow \text{NORMALIZZAZIONE} \longrightarrow \mathbf{\text{ENTITY RESOLUTION}} \longrightarrow \text{UnifiedMessage} \longrightarrow \text{ANALISI AI}$$

### 1.1 Obiettivo
L'obiettivo esclusivo di questo layer è stabilire **relazioni candidate basate su evidenze forensi verificabili** tra entità (attori, contatti, mittenti, destinatari) e rilevare **candidati duplicati** provenienti da record e sorgenti differenti, preservando in modo categorico la provenance e l'integrità dei record sorgente.

### 1.2 Confine con il Layer di Normalizzazione
- La **Normalizzazione** opera su *singoli record in isolamento*: converte timestamp verso rappresentazioni uniformi (`TimestampTzStatus`), ripulisce sintatticamente i separatori telefonici, decompone i JID in local/domain e mappa i tipi di messaggio.
- L'**Entity Resolution** opera *cross-record e cross-source*: mette in relazione record diversi (es. un messaggio in `msgstore.db` e un contatto in `wa.db`) confrontandone gli identificatori normalizzati.

### 1.3 Confine con il Layer `UnifiedMessage`
- L'**Entity Resolution Foundation NON costruisce `UnifiedMessage`**: non produce modelli unificati di messaggio, non crea `Participant` canonici definitivi e non istanzia `Chat` unificate.
- Gli identificatori generati sono puramente tecnici e provvisori (es. `entity_candidate:<n>`, `duplicate_candidate:<n>`).
- L'unificazione cross-chat e cross-source definitiva appartiene esclusivamente allo stadio successivo.

---

## 2. Modello di Evidenza Forense (`entity_resolution/models.py`)

Per garantire trasparenza, riproducibilità e valore probatorio in sede giudiziaria, ogni associazione deve essere supportata da un'evidenza esplicita e tracciabile.

### 2.1 Livelli di Affidabilità (`EvidenceLevel`)

```python
class EvidenceLevel(str, Enum):
    EXACT = "EXACT"              # Corrispondenza identica su identificatore univoco globale (es. JID completo)
    STRONG = "STRONG"            # Corrispondenza forte e semanticamente solida (es. numero telefonico canonico)
    WEAK = "WEAK"                # Indizio debole non sufficiente da solo per unire entità (es. testo simile)
    UNRESOLVED = "UNRESOLVED"    # Riferimento o entità non risolta (es. alias di gruppo o ChatId senza mapping)
```

### 2.2 Tipologie di Evidenza (`EvidenceType`)

```python
class EvidenceType(str, Enum):
    IDENTIFIER_OBSERVED = "IDENTIFIER_OBSERVED"  # Singolo identificatore osservato senza riscontro cross-reference (WEAK)
    JID_EXACT = "JID_EXACT"                      # Stesso JID completo presente in due o più sorgenti (EXACT)
    PHONE_CANONICAL = "PHONE_CANONICAL"          # Stesso numero telefonico canonicalizzato presente in due o più sorgenti (STRONG)
    PHONE_JID_LOCAL = "PHONE_JID_LOCAL"          # Corrispondenza deterministica tra telefono e local part JID (STRONG)
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"        # Alias pseudonimizzato senza metadati di collegamento (UNRESOLVED)
    UNRESOLVED_CHAT_ID = "UNRESOLVED_CHAT_ID"    # Identificatore di sessione/chat privo di mapping dimostrabile (UNRESOLVED)
    UNRESOLVED_HEURISTIC = "UNRESOLVED_HEURISTIC"# Euristica debole rifiutata per prevenire false unificazioni (UNRESOLVED)
```

### 2.3 Strutture Dati Immutabili
- **`EntityReference`**: punta in modo immutabile all'attore di un record:
  - `source_name`, `source_record_id`, `actor_role` (`'sender'`, `'recipient'`, `'contact'`), `raw_value`, `actor_type`, `normalized_value`.
- **`ResolutionEvidence`**: documenta il legame tra record:
  - `evidence_type`, `evidence_level`, `matched_value`, `reason`, `source_records` (tupla di `(source_name, source_record_id)`).
- **`CandidateEntity`**: raggruppa riferimenti ed evidenze:
  - `candidate_id` (`entity_candidate:<n>`), `canonical_identifier`, `entity_type`, `references` (tupla), `evidence_chain` (tupla), `display_names` (tupla).
- **`DuplicateCandidate`**: raggruppa record candidati duplicati:
  - `candidate_id` (`duplicate_candidate:<n>`), `records` (tupla di coordinate sorgente), `reason`, `confidence` (`STRONG` o `WEAK`, mai `EXACT`).
- **`ResolutionResult`**: output complessivo immutabile con deep immutability su `metadata` (`freeze_structural`).

---

## 3. Strategia di Risoluzione Deterministica

L'implementazione `DeterministicEntityResolver` in `entity_resolution/resolver.py` applica una gerarchia rigorosa di regole:

### 3.1 WhatsApp JID ↔ `wa.db` Contacts (Priorità 1)
- Nel dataset reale sintetico, i JID `+390000000001@s.whatsapp.net` e `+390000000002@s.whatsapp.net` compaiono sia nella tabella `messages` di `msgstore.db` (colonna `key_remote_jid`) sia nella tabella `contacts` di `wa.db` (colonna `jid`).
- Se il JID è riscontrato in **due o più riferimenti**, viene associato con evidenza `EvidenceType.JID_EXACT` e livello `EvidenceLevel.EXACT`.
- Se il JID compare in **un singolo riferimento isolato**, viene registrata l'evidenza `EvidenceType.IDENTIFIER_OBSERVED` con livello `EvidenceLevel.WEAK`.
- Il `display_name` (es. "Contatto_001") viene arricchito nell'entità come metadato descrittivo, lasciando intatti i record sorgente.

### 3.2 Corrispondenza Telefonica Canonica (Priorità 2)
- I mittenti e destinatari canonicalizzati via `canonicalize_phone_syntax` nelle esportazioni Cellebrite (CSV, JSON, XML) e nella colonna `phone_number` di `wa.db` (es. `+39 000 0000001` $\rightarrow$ `+390000000001`):
  - In presenza di **2 o più riferimenti**, vengono associati via `EvidenceType.PHONE_CANONICAL` con livello `EvidenceLevel.STRONG`.
  - In presenza di **un solo riferimento**, producono `EvidenceType.IDENTIFIER_OBSERVED` con livello `EvidenceLevel.WEAK`.

### 3.3 Collegamento Telefono ↔ JID Local Part (Priorità 3 — `STRONG`)
- Se la parte locale di un JID (es. `+390000000001`) coincide in modo esatto con un numero telefonico canonico, i riferimenti vengono uniti nell'entità del JID, registrando sia l'evidenza `JID_EXACT` sia l'evidenza `PHONE_JID_LOCAL`.
- Nessun prefisso nazionale mancante viene mai inventato o forzato.

### 3.4 Trattamento LOCAL_USER
- Il dispositivo forense genera `LOCAL_USER` come ruolo tecnico di direzione (inviato/ricevuto).
- Viene istanziata una `CandidateEntity` con `candidate_identifier="LOCAL_USER"`, `display_names=()` (nessun display name sintetico forzato) e metadato `technical_role=True`.

### 3.5 Trattamento dei Riferimenti Non Risolti
1. **Alias di gruppo (`group_participant_A`)**:
   - Nel dataset non esiste alcuna evidenza strutturale che leghi `group_participant_A` a un numero telefonico o a un JID specifico.
   - Viene categoricamente preservato come **`UNRESOLVED`** (`EvidenceType.UNRESOLVED_ALIAS`).
   - È vietato l'uso di LLM, euristiche di prossimità o inferenze probabilistiche per tentare di indovinarne l'identità.
2. **Cellebrite `ChatId` (`chat_1`, `chat_2`, `chat_3`)**:
   - Non esiste nei metadati alcuna mappatura esplicita tra `chat_1` e il JID di gruppo `00000000001-0000000000@g.us`.
   - Viene mantenuto come **`UNRESOLVED`** (`EvidenceType.UNRESOLVED_CHAT_ID`).
3. **Report XML UFDR**:
   - I record XML contengono unicamente `<Sender>` e non dichiarano alcun tag per destinatario/partecipanti né `ChatId`.
   - L'assenza di destinatario viene preservata; non viene dichiarata alcuna coppia fittizia mittente/destinatario.
4. **Timestamp simile o testo uguale da soli**:
   - Due record aventi lo stesso timestamp o testo analogo ma mittenti differenti **NON vengono mai fusi** (`UNRESOLVED_HEURISTIC`).

---

## 4. Rilevamento Non-Distruttivo dei Duplicati (`DuplicateCandidate`)

In ambito forense, **nessun dato originale può essere eliminato o sovrascritto**.
La deduplicazione in questa fase consiste nell'identificazione trasparente di gruppi di record candidati duplicati:
- **Disabilitazione per timestamp assente**: se il timestamp è `ABSENT`, il rilevamento duplicati è rigorosamente disabilitato per quel record (nessun clustering cieco sul solo testo).
- **Separazione temporale rigorosa**: i record `KNOWN_UTC` e `NAIVE_UNKNOWN` sono indicizzati su chiavi temporali distinte e **non vengono mai confrontati tra loro** per prevenire collisioni spurie dovute all'assenza di fuso orario.
- **Compatibilità positiva del mittente (Tri-State `ActorCompatibility`)**:
  Per superare il rischio di considerare compatibili due record solo per assenza di contraddizione, l'analisi degli attori distingue tre stati categorici:
  - `MATCH`: entrambi hanno un identificatore forte identico (stesso JID, stesso telefono canonicalizzato, entrambi `LOCAL_USER`, oppure stesso alias nella medesima sorgente).
  - `INCOMPATIBLE`: identificatori forti palesemente discordanti (telefoni diversi, JID diversi, `LOCAL_USER` vs contatto remoto certo).
  - `UNKNOWN`: uno o entrambi i record mancano di mittente, hanno alias opaco, oppure presentano lo stesso alias testuale ma provengono da sorgenti eterogenee non correlate.
  *Regola di esclusione*: record con mittenti `INCOMPATIBLE` **non possono mai formare** un `DuplicateCandidate`.
- **Compatibilità del tipo di messaggio**:
  - `MATCH`: stesso tipo di messaggio canonico noto (es. entrambi `TEXT`, entrambi `IMAGE`).
  - `INCOMPATIBLE`: tipi incompatibili (es. `TEXT` vs `AUDIO`, `IMAGE` vs `CALL`). Record incompatibili non possono mai formare duplicati.
  - `UNKNOWN`: tipi non specificati o generici (`UNKNOWN`, `OTHER`).
- **Livello di confidenza e motivazione forense**:
  - `EvidenceLevel.STRONG`: assegnato **esclusivamente** a duplicati cross-source che soddisfano simultaneamente:
    1. Timeline con fuso orario accertato `KNOWN_UTC`;
    2. Compatibilità mittente positiva (`ActorCompatibility.MATCH`) su tutti i record del cluster;
    3. Compatibilità del tipo messaggio positiva (`MATCH`);
    4. Testo identico.
  - `EvidenceLevel.WEAK`: assegnato in tutti gli altri casi ammissibili:
    1. Duplicati interni alla medesima sorgente;
    2. Duplicati su timeline priva di timezone (`NAIVE_UNKNOWN`);
    3. Duplicati con mittente `UNKNOWN` (es. alias identico cross-sorgente);
    4. Duplicati con tipo messaggio `UNKNOWN`.
    Il campo `reason` di ogni `DuplicateCandidate` con confidenza `WEAK` esplicita in modo trasparente tutti i fattori di debolezza del segnale (es. `naive timestamp`, `actor signal uncertainty`).
  - **Mai `EXACT`**: riservato a identità strutturali perfette; non viene mai impiegato per deduplicazioni cross-source basate su correlazioni di testo/timestamp.
- **Tutti i record originali rimangono presenti e inalterati** sia in `RawRecord` che in `NormalizedRecord`.

---

## 5. Stato Globale e Trasparenza di Memoria

A differenza dei layer precedenti (Importer, Validatore, Normalizzatore) che operano in streaming puro record-per-record, la risoluzione di entità cross-sorgente richiede per definizione il mantenimento di indici in memoria per confrontare record provenienti da sorgenti indipendenti:
- **Indici mantenuti**:
  1. `jid_groups`: dizionario in memoria mappante ciascun JID univoco alla lista dei suoi `EntityReference`;
  2. `phone_groups`: dizionario in memoria mappante ciascun numero canonico alla lista dei suoi `EntityReference`;
  3. `display_names`: mappe di supporto per nomi di rubrica;
  4. `text_time_map`: indice per il rilevamento di duplicati candidati basato su `(testo, timestamp)`.
- **Crescita della memoria**: $O(U)$ dove $U$ è il numero di identificatori unici distinti (JID e numeri), e $O(M)$ per i messaggi testuali con timestamp.
- **Nessun finto claim di streaming $O(1)$**: il layer dichiara con onestà intellettuale la necessità di questi indici per consentire correlazioni cross-source globali.

---

## 6. Determinismo e Assenza di AI

Il layer è interamente basato su funzioni pure e regole deterministiche:
- **Nessun modello probabilistico o fuzzy**: nessun utilizzo di Levenshtein name distance, TF-IDF o simili per fondere persone;
- **Nessun LLM o embedding**: nessun utilizzo di modelli locali o remoti in questa fase di foundation forense;
- **Riproducibilità totale**: due esecuzioni consecutive sullo stesso dataset producono esattamente lo stesso ordinamento di entità candidate e candidati duplicati.
