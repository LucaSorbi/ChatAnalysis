# Validation Layer — Design Document

**Fase**: 4 — Layer di Validazione  
**Data**: 2026-09-10  
**Stato**: Foundation implementata e validata (`ValidationSeverity`, `ValidationIssue`, `ValidationResult`, `BaseValidator`, `RecordValidator`)  

---

## 1. Responsabilità del Layer di Validazione

Il layer di Validazione si colloca immediatamente a valle dell'Importer Layer e a monte della Normalizzazione:

```
DATI ORIGINALI
  ↓
IMPORTER LAYER
  ↓
RawRecord (immutabile, source-faithful)
  ↓
VALIDATION LAYER  ← questo layer
  ↓
RawRecord inalterato + ValidationResult
  ↓
(futuro) NORMALIZZAZIONE
  ↓
(futuro) ENTITY RESOLUTION
  ↓
(futuro) UnifiedMessage
  ↓
(futuro) ANALISI AI
```

### 1.1 Principio Fondamentale: VALIDAZIONE ≠ NORMALIZZAZIONE

Il layer di Validazione ha un'unica responsabilità:
> **Osservare un `RawRecord`, rilevare anomalie o caratteristiche strutturali, descriverle mediante un `ValidationResult` diagnostico e restituire il `RawRecord` originale strettamente invariato.**

### 1.2 Cosa il Layer di Validazione NON FA (Divieti Assoluti)
- **NON modifica** il `RawRecord` fornito in ingresso.
- **NON corregge** valori anomali o mancanti (es. non sostituisce `""` con `None` o data fittizia).
- **NON normalizza** tipi o formati di dati (nessuna conversione UTC, nessuna formattazione telefonica E.164).
- **NON scarta silenziosamente** i record (nessun record viene rimosso dal flusso dei dati forensi).
- **NON effettua Entity Resolution** (non risolve JID, né alias `group_participant_A`).
- **NON crea `UnifiedMessage`** o modelli canonici di dominio.
- **NON effettua I/O sul filesystem** o chiamate di rete.

---

## 2. Distinzione Fondamentale: Importer Error vs Validation Issue

È essenziale distinguere formalmente tra errori a livello di Importer e anomalie rilevate dal Validator:

| Proprietà | Importer Error | Validation Issue |
|---|---|---|
| **Definizione** | La sorgente **non può essere letta o rappresentata fedelmente** in un `RawRecord`. | Il `RawRecord` è stato **estratto fedelmente**, ma contiene un'anomalia, incompletezza o caratteristica notevole. |
| **Esempi tipici** | File mancante, schema SQLite incompatibile, CSV con header corrotto, XML malformato o violazione DTD/XXE, JSON sintatticamente non parsabile. | Timestamp vuoto nel CSV, campo opzionale assente nel JSON, assenza strutturale di `ChatId` nel report XML, JID non ancora risolto, timestamp non positivo. |
| **Azione dell'architettura** | Solleva eccezione (`FileNotFoundError`, `ValueError`). Interrompe l'importazione o rifiuta il file via `can_import()`. | Genera un `ValidationIssue` incapsulato nel `ValidationResult`. Il record prosegue intatto nella pipeline. |
| **Stato del RawRecord** | Nessun `RawRecord` emesso per il dato corrotto. | `RawRecord` emesso integro e associato al suo `ValidationResult`. |

---

## 3. Modelli di Validazione (`validation/models.py`)

### 3.1 `ValidationSeverity` (Enum)
Definisce i tre livelli discreti di gravità dell'anomalia:
- **`ERROR`**: anomalia tale da rendere il record non valido per le successive trasformazioni senza una decisione esplicita (es. corruzione o incoerenza interna grave).
- **`WARNING`**: record ancora rappresentabile e utilizzabile ma contenente un dato problematico o incompleto (es. timestamp vuoto in un messaggio CSV Cellebrite).
- **`INFO`**: informazione diagnostica eccezionale realmente utile. Non viene utilizzata per descrivere caratteristiche normali e costanti di ogni record di una sorgente.

### 3.2 `ValidationIssue` (Dataclass Immutabile)
Oggetto immutabile (`frozen=True`) che descrive formalmente una singola anomalia:
- `code: str`: codice machine-readable stabile (es. `"CSV_EMPTY_TIMESTAMP"`, `"GENERIC_EMPTY_RAW_FIELDS"`).
- `severity: ValidationSeverity`: livello di gravità (`INFO`, `WARNING`, `ERROR`).
- `message: str`: descrizione esplicativa human-readable in italiano.
- `source_name: str`: sorgente di provenienza del record.
- `source_record_id: str`: ID nativo del record.
- `field_path: str | None`: eventuale percorso o nome del campo coinvolto (es. `"TimeStamp"`, `"raw_fields.timestamp"`).

### 3.3 `ValidationResult` (Dataclass Immutabile)
Incapsula il legame tra il record originale e le anomalie riscontrate:
- `record: RawRecord`: riferimento al `RawRecord` originale non modificato.
- `issues: tuple[ValidationIssue, ...]`: tupla congelata di anomalie riscontrate.
- **Proprietà helper**:
  - `is_valid -> bool`: `True` se non è presente alcuna issue con severità `ERROR`.
  - `has_errors -> bool`: `True` se esiste almeno un `ERROR`.
  - `has_warnings -> bool`: `True` se esiste almeno un `WARNING`.
  - `has_issues -> bool`: `True` se la lista delle issue non è vuota.
  - `issues_by_severity(severity) -> tuple[ValidationIssue, ...]`: filtro immutabile per livello di severità.

---

## 4. Contratto del Validatore (`validation/base.py`)

L'interfaccia astratta `BaseValidator` garantisce un contratto uniforme ed essenziale:

```python
class BaseValidator(ABC):
    @abstractmethod
    def validate(self, record: RawRecord) -> ValidationResult:
        """Osserva un singolo RawRecord e restituisce il ValidationResult corrispondente."""
        ...

    def validate_stream(self, records: Iterator[RawRecord]) -> Iterator[ValidationResult]:
        """Elabora un generatore/iteratore di RawRecord in streaming incrementale (lazy)."""
        for record in records:
            yield self.validate(record)
```

### 4.1 Validazione Incrementale e Streaming
- `validate_stream()` non converte mai l'iteratore in una lista in memoria.
- Non impiega librerie pesanti di analisi tabulare (es. Pandas o DataFrame).
- Il consumo di memoria rimane unicamente proporzionale al singolo record correntemente validato.

---

## 5. Regole Minime Implementate (`validation/validator.py`)

Il modulo `RecordValidator` applica un primo nucleo minimale di regole basate sui dataset realmente osservati:

### 5.1 Regole Generiche (`RawRecord`)
- **`GENERIC_EMPTY_RAW_FIELDS`** (`WARNING`): segnalato se il dizionario `raw_fields` è completamente vuoto.

### 5.2 Regole Cellebrite CSV
- **`CSV_EMPTY_TIMESTAMP`** (`WARNING`): segnalato quando `TimeStamp` è stringa vuota o None (caso realmente presente nel dataset `test_data/cellebrite_export/messages.csv`).
  - Il record **non viene corretto**, non viene forzato a data odierna o epoch 0, e **non viene scartato**.

### 5.3 Regole Cellebrite JSON
- **`JSON_MISSING_TIMESTAMP`** (`WARNING`): segnalato quando la chiave `timestamp` è assente o vuota.
- Struttura reale: lo schema verificato di `messages.json` contiene `id`, `chat_id`, `sender`, `timestamp`, `type`, `content` (con `text` e `media_path`) e `metadata` (con `deleted`, `forwarded`, `starred`). Non esistono campi `participants` né `attachment.path`.

### 5.4 Regole Cellebrite XML (UFDR)
- Caratteristica del formato: l'assenza del tag `ChatId` nel report XML Cellebrite UFDR è una caratteristica strutturale invariante del formato osservato; **NON viene emessa come issue per-record** per evitare centinaia di segnalazioni ridondanti su record validi. Il raggruppamento delle conversazioni è demandato alla successiva Entity Resolution.
- **`XML_EMPTY_TIMESTAMP`** (`WARNING`): segnalato se il tag `Timestamp` (con o senza prefisso Clark notation di namespace) è assente o privo di valore.

### 5.5 Regole WhatsApp (`msgstore.db` e `wa.db`)
- **JID non risolti** (`123456789@s.whatsapp.net`, `group@g.us`): considerati normali e validi; la loro risoluzione a contatti o nomi è demandata alla successiva fase di Entity Resolution.
- **`MSGSTORE_ANOMALOUS_TIMESTAMP`** (`WARNING`): segnalato se il timestamp intero è minore o uguale a zero (osservato valore anomalo -1000 nel database di test).
- **`wa.db` status nullo**: `status is None` è un valore osservato e valido, non costituisce errore.
- **`WA_DB_EMPTY_JID`** (`WARNING`): segnalato solo se il campo obbligatorio `jid` del contatto è assente o vuoto.

---

## 6. Questioni Deliberatamente Rinviate

In conformità ai principi di separazione dei compiti della pipeline, le seguenti problematiche rimangono **espressamente irrisolte** a questo livello:

### 6.1 Rinviate alla NORMALIZZAZIONE
1. **Timezone e formati di timestamp**: i formati eterogenei (stringa grezza senza timezone nel CSV, ISO-8601 con offset in JSON e XML, timestamp millisecondi Unix in msgstore.db) rimangono intatti nei `raw_fields`.
2. **Normalizzazione numerica telefonica (E.164)**: numeri locali, formati con spazi (`+39 000 0000001`) o prefissi internazionali grezzi rimangono inalterati.
3. **Semantica di cancellazione (`Deleted`)**: `"False"` (CSV), `False` booleano (JSON), `"false"` (XML) e flag numerici SQLite rimangono intatti.
4. **Tassonomia unificata dei tipi di messaggio**: i valori originali (`"SMS"`, `"MMS"`, `"text"`, `"audio"`, `media_wa_type=0`) non vengono mappati in un vocabolario controllato comune.

### 6.2 Rinviate alla ENTITY RESOLUTION
1. **Mapping `ChatId` → `JID`**: le chat Cellebrite (`chat_1`, `chat_2`) non vengono associate alle conversazioni WhatsApp.
2. **Risoluzione `group_participant_A`**: l'alias mittente non viene risolto o collegato alla rubrica `wa.db`.
3. **Cross-source linkage**: nessun collegamento incrociato tra `msgstore.db` e le esportazioni Cellebrite.
4. **Verifica fisica dei percorsi multimediali**: la corrispondenza e l'integrità fisica dei file su disco indicati in `media_reference` o `raw_fields` appartiene alle fasi di media processing.
