# Architettura e Gestione File di Grandi Dimensioni (Fase 27, 28, 29, 37)

## 1. Limite di Upload vs Capacità di Memoria Operativa
- `MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024` (2 GB esatti).
- Rappresenta la dimensione massima del file trasferibile (es. grandi archivi ZIP contenenti allegati video, audio, immagini e transcript testuale).
- **Trasparenza**: Non significa che 2 GB di testo JSON o log vengano materializzati simultaneamente in RAM.

## 2. Pipeline di Ingestion a Flusso Controllato (Bounded-Memory)
1. **Upload Streaming**: I byte in arrivo vengono scritti in chunk da 64 KB su un file temporaneo sicuro in `tempfile.TemporaryDirectory()`, calcolando SHA-256 e dimensione in streaming.
2. **Transcript Streaming**: Il parser WhatsApp (`WhatsAppExportImporter`) utilizza `io.TextIOWrapper` con `errors="strict"` leggendo riga per riga (`_parse_lines_stream`), mantenendo in memoria esclusivamente il buffer del messaggio multilinea corrente.
3. **Validazione e Normalizzazione a Generatore**: `validator.validate()` e `normalizer.normalize_stream()` consumano i record in sequenza tramite iteratori, senza accumulare liste ridondanti di record grezzi non validati.
4. **Isolamento e Pulizia**: A conclusione del ciclo di ingestion o in caso di errore, la directory temporanea viene distrutta automaticamente (`TemporaryDirectory`).

## 3. Risultati dei Test Bounded-Pipeline
I test in `tests/unit/test_large_pipeline_bounded.py` verificano la capacità di ingerire stream di messaggi sequenziali senza perdite di dati e nel pieno rispetto dell'immutabilità del modello di dominio.
