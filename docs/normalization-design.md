# Normalization Layer Design

**Versione:** 1.0.0  
**Data:** 10 Settembre 2026  
**Autore:** Forensic Software Engineering Agent  
**Stato:** Approvato e Conforme  

---

## 1. Principi e Scope Architetturale

Il layer di **Normalizzazione** costituisce il quarto stadio obbligatorio e non invertibile della pipeline:

$$\text{DATI ORIGINALI} \longrightarrow \text{IMPORTER} \longrightarrow \text{RawRecord} \longrightarrow \text{VALIDAZIONE} \longrightarrow \mathbf{\text{NORMALIZZAZIONE}} \longrightarrow \text{ENTITY RESOLUTION} \longrightarrow \text{UnifiedMessage} \longrightarrow \text{ANALISI AI}$$

### 1.1 Finalità del Normalizzatore
Il compito esclusivo del layer di Normalizzazione è quello di accettare un `ValidationResult` (contenente il `RawRecord` originale e la tupla dei problemi rilevati) e trasformarlo deterministicamente in un `NormalizedRecord`.
La normalizzazione converte le rappresentazioni eterogenee, proprietarie o tipiche del formato d'origine (ad es. timestamp in millisecondi Unix vs ISO-8601 vs stringhe naive locali; numeri con spazi o trattini; valori booleani/interi/stringa per i messaggi eliminati) in strutture standardizzate, tipizzate e semanticamente coerenti.

### 1.2 Delimitazioni Rigorose — Cosa NON Fa
Per preservare la separazione delle responsabilità e l'integrità forense:
1. **NON effettua Entity Resolution**: non unifica mittenti, non deduce che un numero telefonico coincida con un contatto in rubrica (`wa.db`), né collega JID a persone fisiche.
2. **NON inventa o assume fusi orari**: non assume che un timestamp senza timezone sia UTC o ora locale italiana (`Europe/Rome`).
3. **NON unisce sorgenti (No Cross-Source Linkage)**: ogni record è normalizzato in isolamento.
4. **NON produce `UnifiedMessage`**: l'unificazione cross-chat e cross-source appartiene al layer successivo.
5. **NON altera `RawRecord` né `ValidationResult`**: la provenance è garantita da riferimenti immutabili.
6. **NON fa uso di modelli AI o euristiche probabilistiche**: tutte le trasformazioni sono funzioni pure, deterministiche e riproducibili.

---

## 2. Modello di Dati: `NormalizedRecord`

Il modello è definito in `normalization/models.py` ed è caratterizzato da immutabilità profonda (`frozen=True`) e provenance esplicita.

```python
@dataclass(frozen=True)
class NormalizedRecord:
    raw_record: RawRecord
    validation_result: ValidationResult
    source_name: str
    source_record_id: str
    record_type: str
    timestamp: NormalizedTimestamp
    actor_from: NormalizedActor | None
    actor_to: NormalizedActor | None
    message_type: CanonicalMessageType
    raw_message_type: Any
    is_deleted: bool | None
    raw_deleted: Any
    text_content: str | None
    media_reference: str | None
    metadata: Mapping[str, Any]
```

### 2.1 Provenance Obbligatoria
Ogni istanza di `NormalizedRecord` detiene i riferimenti immutabili:
- `raw_record`: istanza originaria di `RawRecord`, contenente `raw_fields` verbatim.
- `validation_result`: istanza di `ValidationResult`, contenente gli eventuali `ValidationIssue` rilevati.

---

## 3. Gestione Rigorosa dei Timestamp

Nei procedimenti digital forensic, alterare o forzare un fuso orario in assenza di evidenza certa equivale a inquinare la prova. Per questo motivo la gestione dei timestamp adotta lo stato esplicito `TimestampTzStatus`:

```python
class TimestampTzStatus(str, Enum):
    KNOWN_UTC = "KNOWN_UTC"        # Timestamp con timezone noto, normalizzato a datetime aware UTC
    NAIVE_UNKNOWN = "NAIVE_UNKNOWN"# Timestamp privo di offset; datetime naive preservato tal quale
    ABSENT = "ABSENT"              # Timestamp assente o non valido; nessun epoch 0 o data fittizia
```

### 3.1 Regole Source-Aware per Sorgente

| Sorgente | Formato Origine | Normalizzazione Adottata | Stato Risultante |
|---|---|---|---|
| **WhatsApp `msgstore.db`** | Millisecondi Unix epoch (es. `1746978406000`, verificati matematicamente: `/1000 = 1746978406` s $\rightarrow$ maggio 2025; se fossero secondi corrisponderebbero all'anno ~57284) | Divisione `/ 1000.0` e conversione in `datetime` aware con `tz=timezone.utc`. Valori non positivi $\rightarrow$ `ABSENT`. | `KNOWN_UTC` |
| **Cellebrite JSON** | ISO-8601 con offset esplicito `+00:00` (es. `2024-10-31T16:50:00+00:00`, riscontrato su tutti i 100 record) | Parsing con `datetime.fromisoformat()` e conversione deterministica in UTC via `.astimezone(timezone.utc)`. | `KNOWN_UTC` |
| **Cellebrite XML** | ISO-8601 con offset esplicito `+00:00` (es. `2024-04-23T20:30:15+00:00`, riscontrato su tutti i 50 record) | Parsing con `datetime.fromisoformat()` e conversione deterministica in UTC. | `KNOWN_UTC` |
| **Cellebrite CSV** | Stringa datetime naive `YYYY-MM-DD HH:MM:SS` (senza indicazione di tz) | Parsing in `datetime` naive. **Nessun offset inventato** (né UTC né Europe/Rome) per preservare l'integrità probatoria forense. | `NAIVE_UNKNOWN` |
| **Cellebrite CSV (vuoto)** | Stringa vuota `""` (es. riga 15 del file reale) | `utc_datetime=None`, `naive_datetime=None`, `iso_string=None`. Nessun fallback arbitrario a epoch 0 o data odierna. | `ABSENT` |
| **WhatsApp `wa.db`** | Nessun timestamp per-record (tabella contatti `contacts`) | `status=ABSENT`, `raw_value=None`. | `ABSENT` |

---

## 4. Normalizzazione Conservativa degli Attori

Gli attori (mittenti e destinatari) vengono analizzati senza preconcetti o forzature numeriche tramite il modello `NormalizedActor`:

```python
@dataclass(frozen=True)
class NormalizedActor:
    raw_value: str
    actor_type: str  # 'phone', 'jid', 'alias', 'chat_id', 'unknown'
    normalized_phone: str | None = None
    jid_local: str | None = None
    jid_domain: str | None = None
    alias: str | None = None
    chat_id: str | None = None
```

### 4.1 Tipologie Trattate e Distinzione E.164
1. **Numeri telefonici internazionali (`actor_type='phone'`)**:
   - Riconosciuti dalla presenza del prefisso `+` seguito da cifre e separatori convenzionali.
   - Normalizzazione: rimozione sintattica e lossless di spazi, trattini e parentesi (es. `+39 000 0000001` $\rightarrow$ `+390000000001`).
   - **Distinzione fondamentale**: questa trasformazione è una *canonicalizzazione sintattica dei separatori* e **NON** costituisce una validazione formale ITU-T E.164 (non viene verificata la conformità del piano di numerazione né impiegata la libreria `phonenumbers`). Nessun prefisso nazionale viene mai dedotto o aggiunto in caso di assenza.
2. **JID WhatsApp (`actor_type='jid'`)**:
   - Riconosciuti dalla presenza del carattere `@` (es. `+390000000001@s.whatsapp.net` o `00000000001-0000000000@g.us`).
   - Normalizzazione: scomposizione deterministica e lossless in `jid_local` e `jid_domain`.
   - **Nessuna Entity Resolution**: la parte locale non viene interpretata automaticamente come identità telefonica canonica; l'associazione con numeri telefonici è demandata alla fase di Entity Resolution su base di evidenza.
3. **Alias di gruppo (`actor_type='alias'`)**:
   - Valori come `group_participant_A` NON sono numeri di telefono. Vengono preservati intatti come alias testuali.
4. **Identificatori chat (`actor_type='chat_id'`)**:
   - Valori come `chat_1` vengono marcati esplicitamente come identificatori di sessione/chat.

---

## 5. Semantica di `is_deleted` Cross-Source

Il campo relativo allo stato di cancellazione viene ricondotto al tipo opzionale `bool | None`, preservando sempre il valore grezzo originale in `raw_deleted`:

| Sorgente | Campo e Tipo Grezzo | Valori Osservati | Normalizzato (`is_deleted`) | Significato Semantico Sorgente |
|---|---|---|---|---|
| **WhatsApp `msgstore.db`** | `deleted: int` | `0` (490), `1` (14) | `0` $\rightarrow$ `False`, `1` $\rightarrow$ `True` | Flag interno SQLite indicante eliminazione del messaggio per l'utente |
| **Cellebrite CSV** | `Deleted: str` | `"False"` (233), `"True"` (69) | `"False"` $\rightarrow$ `False`, `"True"` $\rightarrow$ `True` | Flag di estrazione forense indicante recupero o stato eliminato |
| **Cellebrite JSON** | `metadata.deleted: bool` | `false` (97), `true` (3) | `false` $\rightarrow$ `False`, `true` $\rightarrow$ `True` | Booleano nativo nei metadati del messaggio estratto |
| **Cellebrite XML** | `Deleted: str` | `"false"` (49), `"true"` (1) | `"false"` $\rightarrow$ `False`, `"true"` $\rightarrow$ `True` | Tag XML associato all'estrazione UFDR |
| **WhatsApp `wa.db`** | Assente | Nessuno | `None` (`raw_deleted=None`) | I contatti non possiedono stato di eliminazione |

Qualsiasi valore non interpretabile o assente produce `is_deleted = None`, senza presumere la non-eliminazione.

---

## 6. Tassonomia dei Message Type

### Matrice di Evidenza dei Tipi Osservati

| Sorgente | Raw Field / Type | Valori Osservati e Conteggi | Candidate Canonical Type | Evidenza / Note |
|---|---|---|---|---|
| **`msgstore.db`** | `media_wa_type: int` | `0` (389)<br>`1` (72)<br>`2` (31)<br>`3` (12) | `TEXT`<br>`IMAGE`<br>`AUDIO`<br>`VIDEO` | Codici interi standard documentati del database WhatsApp Android |
| **`messages.csv`** | `MessageType: str` | `"Text"` (277)<br>`"Image"` (25) | `TEXT`<br>`IMAGE` | Stringhe estratte da Cellebrite UFED Reader |
| **`messages.json`** | `type: str` | `"text"` (58)<br>`"image"` (28)<br>`"audio"` (14) | `TEXT`<br>`IMAGE`<br>`AUDIO` | Proprietà tipologica dell'array JSON Cellebrite |
| **`report.xml`** | Nessun tag `Type` esplicito | 50 elementi sotto `<InstantMessages>` | `TEXT` (`raw_message_type=None`) | Messaggi istantanei con contenuto testuale nel tag `<Body>` |
| **`wa.db`** | N/A (rubrica) | Record contatti | `UNKNOWN` (`raw_message_type=None`) | Non sono messaggi |
| **Qualsiasi sorgente** | Valore sconosciuto o non mappato | Non osservato | `UNKNOWN` | Fallback conservativo: `raw_message_type` preservato |

Enum canonico di riferimento:
```python
class CanonicalMessageType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    LOCATION = "LOCATION"
    CONTACT = "CONTACT"
    CALL = "CALL"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"
```

---

## 7. Streaming Lazy ed Efficienza di Memoria

In perfetta continuità con i layer di Import e Validazione, il normalizzatore espone:
```python
def normalize_stream(self, stream: Iterable[ValidationResult]) -> Iterator[NormalizedRecord]:
    for val_result in stream:
        yield self.normalize(val_result)
```
- **Politica di memoria**: elaborazione lazy/incrementale tramite generatore;
- **Nessuna materializzazione intenzionale** dell'intero stream in strutture dati monolitiche;
- L'impronta di memoria è principalmente determinata dal record corrente in elaborazione, dagli oggetti derivati e dai buffer/runtime sottostanti.
