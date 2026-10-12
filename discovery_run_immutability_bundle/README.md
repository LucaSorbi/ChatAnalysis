# ChatAnalysis — Forensic Multimodal Topic Analyzer

Piattaforma forense accademica e investigativa per l'acquisizione, normalizzazione, indicizzazione e analisi tematica assistita da LLM locali di chat WhatsApp e Cellebrite con catena di custodia e conformità forense deterministica.

---

## Architettura e Moduli Core

- **`importer/`**: Ingestion e streaming parsing per formati WhatsApp (`msgstore.db`, `wa.db`, export testuali e ZIP) e Cellebrite (CSV, XML, JSON). Protezione da attacchi Zip Slip, XXE, DTD entity expansion e decodifica sicura.
- **`validation/`**: Validazione strutturale e integrità referenziale dei record grezzi (`RawRecord`).
- **`normalization/`**: Normalizzazione deterministica in `NormalizedRecord` con gestione temporale ISO-8601 UTC.
- **`entity_resolution/`**: Risoluzione delle identità, canonicalizzazione E.164 dei numeri di telefono e indicizzazione dei partecipanti.
- **`unified/`**: Modello canonico `UnifiedMessage` con provenance immutabile verso i record grezzi.
- **`multimodal/`**: Trascrizione audio locale (`faster-whisper`), OCR (`pytesseract`/`Pillow`), descrizioni visuali e `MediaAssetRegistry` scoped alla sessione.
- **`search/`**: Motore di ricerca full-text e BM25 per sezioni di evidenza forense con indicizzazione RAM ONE-TIME per conversazione.
- **`ai/`**: Integrazione controllata con LLM locali su LM Studio (Qwen2.5 7B, Llama 3.1 8B, DeepSeek-R1-Distill) vincolata a loopback security e contratti di binding positivi.
- **`analysis/`**: Servizi di Topic Detection, Topic Discovery euristico e Prova del Nove (`verify_discovered_topic` con `ConsistencyService`).
- **`ui/`**: Interfaccia Streamlit disaccoppiata e reattiva con isolamento delle sessioni e ciclo di vita transazionale dei dataset.

---

## Requisiti di Sistema

- Python `>= 3.12` (raccomandato 3.12 o 3.14)
- LM Studio (per inferenza locale su endpoint loopback `http://127.0.0.1:1234`)
- Dipendenze di sistema opzionali:
  - Tesseract OCR (per pipeline OCR locale)
  - ffmpeg (per decodifica audio multimodale)

---

## Installazione

In un ambiente virtuale isolato:

```bash
python -m venv .venv
# Attivazione su Windows:
.venv\Scripts\activate
# Attivazione su Linux / macOS:
source .venv/bin/activate

# Installazione del pacchetto in modalità editabile con dipendenze base:
pip install -e .

# Installazione con supporto per UI Streamlit:
pip install -e ".[ui]"

# Installazione con tutti i componenti multimodali e test:
pip install -e ".[ui,image,stt,ocr]"
pip install pytest pytest-cov
```

---

## Esecuzione dell'Applicazione

Per avviare la Web UI forense di ChatAnalysis:

```bash
streamlit run app.py
```

L'interfaccia si aprirà su `http://localhost:8501`.

---

## Esecuzione dei Test di Validazione

Per eseguire l'intera suite di test automatizzati:

```bash
pytest
```

Per eseguire test mirati:

```bash
pytest tests/unit/test_lmstudio_model_loading.py
pytest tests/unit/test_media_registry_session_isolation.py
pytest tests/unit/test_discovery_provenance_service.py
```

---

## Benchmark Scientifico

Lo script riproducibile `run_benchmark.sh` definisce i protocolli sperimentali congelati (`DIRECT`, `TRANSLATE_FIRST`) sui 3 modelli e 3 contesti:

```bash
bash run_benchmark.sh
```

---

## Licenza

Progetto sviluppato per tesi universitaria in Informatica Forense. Licenza MIT.
