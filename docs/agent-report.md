# Report Ufficiale di Sviluppo: Multimodal Processing Gate (Fase 1: Media Resolution & Local Audio STT)

**Data e Ora**: 2026-09-10  
**Repository**: `c:\Users\lucas\Desktop\Tesi`  
**Autore**: Agente di Sviluppo Software  
**Stato della Pipeline**: DATI ORIGINALI → IMPORTER → RawRecord → VALIDAZIONE → NORMALIZZAZIONE → ENTITY RESOLUTION → UnifiedMessage → MediaResolver → ResolvedMediaAsset → AudioTranscriber → AudioTranscriptionResult  
**Gate**: MULTIMODAL PROCESSING FOUNDATION GATE (SUPERATO)

---

## 1. Baseline di Partenza e Preflight Gate

### FATTO VERIFICATO
- **Esito Preflight Suite Iniziale**: La suite di test preesistente contava 645 test raccolti: 644 superati, 0 falliti, 0 errori e 1 saltato per vincolo di privilegi symlink su piattaforma Windows (`test_immutable_symlink_attack_rejected`).
- **Verifica Immutabilità Core**: Il modulo `core/immutability.py` espone la funzione `freeze_structural(obj, memo=None)`. È stato verificato che tale funzione gestisce correttamente tipi primitivi, dizionari, liste, tuple, set, dataclass congelate (`frozen=True`) e istanze con `__dict__`, bloccando mutazioni di secondo livello attraverso `MappingProxyType` e tuple immutabili.
- **Riuso di `freeze_structural`**: I nuovi modelli multimodali (`ResolvedMediaAsset`, `AudioTranscriptSegment`, `AudioTranscriptionResult`) e i modelli di normalizzazione (`NormalizedRecord`, `UnifiedMessage`) riutilizzano direttamente `core.immutability.freeze_structural`, garantendo coerenza strutturale senza duplicazioni di codice.

### DECISIONE ARCHITETTURALE
- È stato confermato il principio di **non-mutazione del `UnifiedMessage`**: il modello `UnifiedMessage` non viene modificato per accogliere le trascrizioni audio nel suo campo `text_content`. Il campo `text_content` preserva fedelmente il testo originale dell'estrazione (o `None` per soli allegati). Le trascrizioni STT costituiscono un'entità di arricchimento separata (`AudioTranscriptionResult`), correlata univocamente mediante `message_id`, `source_name` e `source_record_id`.

---

## 2. Profilazione Fisica dei Media Asset del Repository

Prima di implementare il resolver, è stata condotta una profilazione esaustiva e diretta dei dati grezzi memorizzati sul disco in `test_data/`:

### FATTO VERIFICATO
1. **WhatsApp (`msgstore.db` + cartella `whatsapp_export/`)**:
   - Tabella `messages`: 504 messaggi totali (389 `TEXT`, 72 `IMAGE`, 31 `AUDIO`, 12 `VIDEO`).
   - Colonna `messages.media_url`: valorizzata in 115 record.
   - Tabella `media_refs`: 115 record contenenti `message_row_id` e percorsi POSIX relativi (es. `WhatsApp Images/IMG_00003.jpg`, `WhatsApp Audio/AUD_00015.opus`).
   - Il legame `messages._id == media_refs.message_row_id` è una relazione 1-a-1 deterministica.
   - Cartella reale `test_data/whatsapp_export/`: contiene 20 file JPEG (`IMG_00000.jpg` - `IMG_00019.jpg`), 10 file audio Opus (`AUD_00000.opus` - `AUD_00009.opus` di 282 byte ciascuno), 5 file video MP4 (`VID_00000.mp4` - `VID_00004.mp4`).
   - Risoluzione effettiva sui 115 riferimenti di `msgstore.db`: esattamente 2 file esistono sul filesystem (`WhatsApp Images/IMG_00003.jpg` e `WhatsApp Images/IMG_00012.jpg`). I restanti 113 puntano a indici numerici superiori non inclusi nel dump sintetico (`MISSING`).
   - I 10 file `.opus` presenti sul disco hanno nomi sintetici (`AUD_00000.opus` - `AUD_00009.opus`), mentre i riferimenti in `media_refs` partono da `AUD_00015.opus`.
2. **Cellebrite CSV (`messages.csv`)**:
   - 302 messaggi totali (277 `Text`, 25 `Image`).
   - Colonna `Attachments`: contiene 25 riferimenti con separatore backslash (`Files\IMG_0012.jpg` ... `Files\IMG_0025.jpg`).
   - La cartella `Files\` non è presente nella cartella del test data: tutti i 25 riferimenti risultano correttamente catalogati come `MISSING`.
3. **Cellebrite JSON (`messages.json`)**:
   - 100 messaggi totali (58 `text`, 28 `image`, 14 `audio`).
   - Campo `content.media_path`: `null` in tutti i 100 record.
   - Esito di risoluzione: 100 record `NO_REFERENCE`. Questo conferma sul piano forense che la presenza del tipo dichiarato `audio` non implica automaticamente l'esistenza di un riferimento a file.
4. **Cellebrite XML (`messages.xml`)**:
   - 50 messaggi totali. Nessun tag o attributo media presente: 50 record `NO_REFERENCE`.
5. **Riepilogo Risoluzione Globale su 956 Messaggi della Pipeline**:
   - `RESOLVED`: 2 messaggi (immagini WhatsApp).
   - `MISSING`: 138 messaggi (113 WhatsApp + 25 Cellebrite CSV).
   - `NO_REFERENCE`: 816 messaggi.
   - Totale: 956 messaggi processati senza eccezioni.

---

## 3. Architettura Media Asset Resolution (`multimodal.resolver`)

### DECISIONE ARCHITETTURALE
- **Sandboxing e Path Security (`allowed_roots`)**:
  La classe `MediaResolver` impone che ogni risoluzione avvenga rigorosamente entro un perimetro di directory consentite (`allowed_roots`). Tutti i percorsi vengono normalizzati tramite `Path.resolve()`, neutralizzando sequenze malevole di *path traversal* (`../`, link simbolici verso l'esterno, drive arbitrari). Qualsiasi tentativo di violazione del perimetro produce `MediaResolutionStatus.OUTSIDE_ALLOWED_ROOT`.
- **Politica per i Riferimenti Remoti (`REMOTE_REFERENCE`)**:
  In ambito forense, il fetching automatico di URL remoti via HTTP/HTTPS/FTP è una violazione delle procedure operative standard (rischio di alterazione remota, leak dell'indirizzo IP del perito o violazione probatoria). Il resolver riconosce le referenze remote e assegna lo stato `MediaResolutionStatus.REMOTE_REFERENCE` **senza effettuare alcuna chiamata di rete**.
- **Risoluzione Relativa al Contesto di Acquisizione**:
  I database forensi registrano percorsi relativi alla radice dell'archivio o alla cartella del database stesso. Il resolver tenta determinata la risoluzione:
  1. Relativa al percorso genitore del file sorgente (`source_path.parent`);
  2. Relativa a ciascuna delle `allowed_roots`.
- **Collegamento Intra-Source WhatsApp via `MediaResolutionContext`**:
  È stata introdotta la classe `MediaResolutionContext` che mappa preventivamente i `message_row_id` di `media_refs` verso i relativi path di file. In questo modo, il resolver recupera l'allegato anche quando il record di messaggio non espone un percorso inline nel corpo del messaggio.
- **Calcolo SHA-256 a Blocchi (Chunked)**:
  La funzione `compute_sha256_chunked(file_path, chunk_size=65536)` esegue letture a blocchi di 64 KB, garantendo che file audio o video di grandi dimensioni vengano hashificati senza causare picchi di allocazione della memoria RAM.

---

## 4. Architettura Audio Processing e Speech-to-Text (`multimodal.transcriber`)

### DECISIONE ARCHITETTURALE
- **Astrazione Modulare (`BaseAudioTranscriber`)**:
  È stato definito un contratto astratto mediante `BaseAudioTranscriber` che espone il metodo `transcribe(asset: ResolvedMediaAsset) -> AudioTranscriptionResult`.
- **Backend Sintetico per Test (`FakeAudioTranscriber`)**:
  Per garantire test veloci, ripetibili e totalmente indipendenti dalla disponibilità di GPU o download di modelli neurali, è stato implementato `FakeAudioTranscriber`. Supporta simulazione di segmenti temporali, confidenze variabili e simulazione di guasti decodificatore (`simulate_failure=True`).
- **Backend Reale `faster-whisper` (`FasterWhisperTranscriber`)**:
  - **Lazy Loading**: Il modello neurale non viene caricato all'inizializzazione del transcriber, ma solo alla prima richiesta di trascrizione (`load_model()`).
  - **Configurazione Hardware Dedicata per CPU**:
    L'ambiente host non dispone di GPU NVIDIA accessibile (`nvidia-smi` non presente). Il backend è configurato esplicitamente per `device="cpu"`, `compute_type="int8"` (quantizzazione a 8 bit intero ottimizzata per processori x86_64) e `cpu_threads=4`.
  - **Rigorosa Trascrizione nella Lingua Originale (`task="transcribe"`)**:
    La direttiva di trascrizione specifica tassativamente `task="transcribe"`. È espressamente vietata la traduzione automatica in inglese (`task="translate"`), preservando l'integrità verbale della testimonianza o dell'intercettazione nella sua lingua sorgente.
  - **Tracciamento Metadati Linguistici**:
    Vengono estratti e registrati sia il codice linguistico identificato dal modello (`detected_language`) sia la probabilità associata (`language_probability`).
  - **Preservazione dei Segmenti Temporali**:
    I segmenti emessi dal modello vengono preservati nella loro sequenza temporale nativa (`start_seconds`, `end_seconds`, `text`, `confidence`), incapsulati in tuple immutabili `AudioTranscriptSegment`.
  - **Gestione Errori per-file**:
    File audio corrotti o non decodificabili producono un `AudioTranscriptionResult` con stato `FAILED` e dettaglio nel campo `error_message`, consentendo alla pipeline di procedere senza interruzioni di batch.

---

## 5. Indagine e Risoluzione Dipendenze Runtime Windows

### FATTO VERIFICATO
- Durante l'attivazione iniziale di `faster-whisper` in ambiente Python 3.14 (Windows 11 x64), l'importazione di `ctranslate2` sollevava un'eccezione binaria:
  ```text
  FileNotFoundError: Could not find module '...\ctranslate2.dll' (or one of its dependencies).
  ```
- Un'ispezione della tabella delle importazioni PE di `ctranslate2.dll` ha rivelato che la DLL dipende da `MSVCP140.dll` (Microsoft Visual C++ Runtime 2015-2022).
- Il sistema operativo dell'host non presentava `MSVCP140.dll` nella directory `C:\Windows\System32`, e l'installazione di Python 3.14 distribuiva unicamente `vcruntime140.dll`, omettendo la libreria runtime C++ standard.
- È stata localizzata una copia compatibile di `MSVCP140.dll` a 64 bit già presente sul sistema in una sottodirectory locale di Microsoft Teams x64, ed è stata copiata all'interno della directory del package `.venv\Lib\site-packages\ctranslate2\` e in `.venv\Scripts\`.
- **Esito**: Sia `ctranslate2` (v4.8.2) che `faster-whisper` (v1.2.1) si importano ed eseguono correttamente senza errori di linking.

---

## 6. Smoke Test Whisper e Politica di Esecuzione

### FATTO VERIFICATO
- È stato creato il modulo dedicato `tests/smoke/test_whisper_smoke.py`, marcato con il marker `@pytest.mark.smoke`.
- In `pyproject.toml`, l'opzione `addopts` è stata configurata con `-m "not smoke"`:
  ```toml
  addopts = [
      "-v",
      "--tb=short",
      "-m",
      "not smoke",
  ]
  ```
- In questo modo, l'esecuzione ordinaria di `pytest` esclude automaticamente i test di fumo, impedendo download indesiderati di modelli neurali da Hugging Face durante i test di routine o CI.
- Esecuzione mirata tramite `pytest tests/smoke/test_whisper_smoke.py -m smoke`:
  - `test_faster_whisper_backend_available`: **PASSED** (conferma disponibilità di `ctranslate2` e `faster-whisper`);
  - `test_faster_whisper_instantiation`: **PASSED** (conferma inizializzazione parametri `device="cpu"`, `compute_type="int8"`, lazy loading attivo);
  - `test_faster_whisper_real_inference`: **SKIPPED** (salvaguardia attiva; si attiva unicamente impostando la variabile d'ambiente `RUN_REAL_WHISPER=1`).

---

## 7. Esito della Suite Completa di Test (Gate Acceptance)

### FATTO VERIFICATO
Esecuzione della suite completa di test del repository tramite `.venv\Scripts\python.exe -m pytest`:

```text
================ 672 passed, 1 skipped, 3 deselected in 23.11s ================
```

- **Totale test raccolti**: 676
- **Test superati**: 672 (100% dei test attivi)
- **Test saltati**: 1 (`test_immutable_symlink_attack_rejected`, vincolo privilegi symlink utente Windows non elevato)
- **Test deselezionati**: 3 (smoke test esclusi da configurazione predefinita)
- **Errori o fallimenti**: **0**

### Dettaglio dei Nuovi Test Multimodali Aggiunti
1. `tests/unit/test_media_resolver.py` (12 test):
   - Risoluzione messaggi senza media (`NO_REFERENCE`);
   - Rifiuto e blocco tentativi di path traversal (`OUTSIDE_ALLOWED_ROOT`);
   - Identificazione referenze remote senza chiamate di rete (`REMOTE_REFERENCE`);
   - File inesistenti (`MISSING`);
   - Calcolo SHA-256 chunked e verifica mime/size su file reali temporanei;
   - Collegamento intra-source con `MediaResolutionContext`;
   - Immutabilità delle istanze `ResolvedMediaAsset`.
2. `tests/unit/test_audio_transcriber.py` (13 test):
   - Contratto astratto `BaseAudioTranscriber`;
   - Trascrizione sintetica e segmentazione temporale con `FakeAudioTranscriber`;
   - Gestione asset non-audio (`NO_AUDIO`);
   - Gestione simulazione guasto (`FAILED`);
   - Inizializzazione e verifica lazy loading di `FasterWhisperTranscriber`;
   - Immutabilità delle istanze `AudioTranscriptSegment` e `AudioTranscriptionResult`.
3. `tests/integration/test_multimodal_pipeline_integration.py` (3 test):
   - Esecuzione end-to-end della pipeline multimodale su tutti i 956 messaggi unificati del dataset reale;
   - Verifica preservazione dell'immutabilità dei messaggi `UnifiedMessage`;
   - Verifica combinata `MediaResolver` + `FakeAudioTranscriber` con aggregazione corretta dei risultati.

---

## 8. Debito Tecnico e Problemi Aperti

### PROBLEMA APERTO
1. **Asset Audio Fisici non Allineati ai Riferimenti WhatsApp nel Dataset Sintetico**:
   Nel dataset di test sintetico `test_data/whatsapp_export/`, i 10 file audio `.opus` presenti sul disco si chiamano `AUD_00000.opus` - `AUD_00009.opus`, mentre i puntatori nella tabella `media_refs` di `msgstore.db` partono da `AUD_00015.opus`. Di conseguenza, tutti i 31 messaggi audio di `msgstore.db` vengono catalogati come `MISSING`. Per eseguire un test STT su tracce reali di questo dataset occorrerà fornire file audio con nomi corrispondenti ai record o mappare esplicitamente una fixture.
2. **File di Test Opus Troncati (282 byte)**:
   I 10 file `.opus` attualmente archiviati in `test_data/whatsapp_export/WhatsApp Audio/` hanno una dimensione di soli 282 byte (header del container Ogg Opus privo di frame audio vocali decodificabili). Un decoder audio reale solleverebbe un errore di fine stream anticipato. In futuro, per test audio end-to-end con voce reale, occorrerà predisporre un campione audio WAV/Opus sintetizzato valido.
3. **Simboli Symlink su Windows**:
   Il test `test_immutable_symlink_attack_rejected` rimane saltato (`SKIPPED`) quando eseguito da terminale standard senza privilegi di amministratore / Developer Mode di Windows. Questo comportamento è noto e documentato.

---

## 9. Prossimo Passo Suggerito (Non Implementato)

### VALUTAZIONE
Con il superamento del Gate Multimodale (Fase 1: Risoluzione Media e Audio STT), la pipeline dispone ora di una solida infrastruttura per la gestione e validazione di file multimediali su disco.
Il passo logico successivo consigliato per la roadmap di sviluppo è:

**Fase 2 Multimodal: Estrazione Testo da Immagini (OCR & Document Text)**
- Integrazione di un'interfaccia astratta `BaseImageTextExtractor` (con backend sintetico per test e backend reale basato su Tesseract o EasyOCR);
- Selezione mirata dei soli `ResolvedMediaAsset` con stato `RESOLVED` e `media_kind == MediaKind.IMAGE`;
- Modellazione di una struttura dati immutabile `ImageOcrResult` (bounding box, testo estratto, confidenza, lingua rilevata);
- Preservazione dell'immutabilità del `UnifiedMessage` senza sovrascrittura di `text_content`.

*Nota rigorosa: Nessuna linea di codice relativa a OCR, Vision, LLM o Streamlit è stata implementata in questa sessione.*
