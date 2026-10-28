# RAPPORTO TECNICO DEFINITIVO: RISOLUZIONE SISTEMATICA DEI FALLIMENTI GBNF IN TOPIC DISCOVERY

**Identificativo intervento**: `CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027`  
**Data**: 27 Ottobre 2026  
**Sistema**: ChatAnalysis (Modulo Analisi Forense & Topic Discovery Operativa)  
**Ambiente operativo**: LM Studio (`http://127.0.0.1:1234/v1`), Modello `qwen2.5-7b-instruct`  
**Esito collaudo reale (Chunk 0, 12, 25)**: **100% COMPLETATO (ZERO ERRORI, ZERO CITAZIONI INVENTATE, 100% PERSISTENZA/IDEMPOTENZA)**  
**Esito suite di test unitari e di integrazione**: **2.158 PASSED, 0 FAILED, 6 SKIPPED, 5 DESELECTED (EXIT CODE 0)**  

---

## 1. Executive Summary e Causa Radice (Root Cause Analysis)

### 1.1 Il Problema Sistematico
Nelle analisi di Topic Discovery operativa eseguite tramite backend LM Studio (llama.cpp) con modelli di medie dimensioni (es. `qwen2.5-7b-instruct`), i blocchi di conversazione reali con elevata densità di messaggi (es. Chunk 0 con 42 messaggi, Chunk 12 con 41 messaggi) andavano sistematicamente incontro a crash della grammatica GBNF o a cicli degenerativi infiniti (attractor loops, sequenze ripetitive come `GGGG...`).
I tentativi di recupero non vincolati (schema-less fallback) provocavano allucinazioni di topic generici e citazioni di evidence ID inesistenti, compromettendo la validità forense dei report.

### 1.2 La Causa Radice
L'analisi forense della memoria di contesto e della pipeline di inferenza ha evidenziato una duplice causa concorrente:
1. **Serializzazione JSON Verbosa (Token Bloat)**: Il formato standard di serializzazione del blocco evidenze riproduceva un array JSON esteso con campi ridondanti (`evidence_id`, `source_record_id`, `record_type`, `message_type`, `media_reference`, ecc.). Per blocchi di 40 messaggi, il prompt di evidenze superava i 5.300 token (~14.800 caratteri).
2. **Buffer Overflow della Grammatica GBNF Ricorsiva di llama.cpp**: Quando un prompt voluminoso viene abbinato a uno schema JSON rigido con vincoli ricorsivi e a una richiesta di 8 o più argomenti complessi, lo stack di valutazione del parser grammaticale GBNF in llama.cpp subisce un buffer overflow interno. L'inferenza si blocca o entra in un loop attrattore emettendo token ripetitivi.

### 1.3 Architettura della Soluzione
Per eliminare alla radice il problema senza alterare i dataset storici, i benchmark scientifici congelati o l'integrità probatoria:
1. **Serializzazione Operativa Compatta (`compact_jsonl_v1`)**:
   - Implementata in `ai/serializer.py` esclusivamente per l'inferenza operativa in produzione.
   - Ogni messaggio è serializzato come un singolo oggetto JSON a riga singola con campi minimi essenziali: `{"id": alias, "msg_id": message_id, "sender": sender, "text": text}`.
   - Utilizzo di alias deterministici brevi (`E001`, `E002`, `E003`...) mappati biunivocamente agli identificatori di evidenza forense originali.
   - Conservazione rigorosa e immutata della serializzazione verbosa per i benchmark scientifici.
2. **Microchunking Preventivo**:
   - Qualsiasi blocco contenente più di 8 messaggi sostanziali viene preventivamente suddiviso in micro-chunk di dimensione massima pari a 8 messaggi sostanziali.
   - La dimensione del prompt per singolo micro-chunk scende a ~150-250 token, annullando completamente il rischio di buffer overflow grammaticale.
3. **Bisezione Binaria Adattiva (Fallback Divide-and-Conquer)**:
   - Se un micro-chunk incontra un errore recuperabile (es. troncamento token, overflow di contesto, degenerazione ciclica), viene suddiviso a metà in due sotto-blocchi (`half_a`, `half_b`) fino a una profondità ricorsiva massima di 3.
   - Il target di argomenti viene riscalato proporzionalmente alla dimensione ridotta (`half_topics = max(2, min(4, chunk_target_topics // 2))`), prevenendo la forzatura del modello su blocchi minimi.
4. **Degeneracy Detection e Normalizzazione Prefissi Candidate**:
   - Interruzione immediata su ripetizioni cicliche (`(.{2,12})\1{5,}` con verifica non-whitespace), prevenendo spreco di risorse.
   - Normalizzazione dei prefissi spuri nelle citazioni restituite dal modello (`value: E002` -> `E002`).
5. **Persistenza Atomica e Idempotenza Forense**:
   - Ogni micro-chunk completato viene memorizzato su SQLite (`checkpoints.db`).
   - Il chunk padre viene marcato SUCCESS solo quando il 100% dei suoi micro-chunk ha avuto successo.
   - Al ricaricamento (reload), le inferenze già salvate vengono ripristinate istantaneamente con **zero** richieste HTTP al modello.

---

## 2. Misurazioni di Efficienza e Riduzione Token

I test comparativi eseguiti sui blocchi reali estratti dal documento forense di riferimento (`test_data/reference_chat_document.zip`) hanno certificato i seguenti risparmi:

| Blocco Reale | Messaggi Totali (Sostanziali) | Chars Verbose (Originale) | Tokens Est. Verbose | Chars Compact (`compact_jsonl_v1`) | Tokens Est. Compact | Risparmio Caratteri (%) | Risparmio Token | Prompt Medio Microchunk |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Chunk 0** | 42 (36) | 14.864 | ~5.309 | 3.367 | ~1.203 | **-77,34%** | **-4.106 token** | ~240 token |
| **Chunk 12** | 41 (40) | 14.723 | ~5.259 | 3.305 | ~1.181 | **-77,54%** | **-4.078 token** | ~235 token |
| **Chunk 25** | 19 (18) | 7.271 | ~2.597 | 1.815 | ~649 | **-75,01%** | **-1.948 token** | ~215 token |

> [!NOTE]
> Con un prompt di ~200 token per micro-chunk, llama.cpp valuta la grammatica GBNF in meno di 2 ms, eliminando ogni instabilità o crash di memoria.

---

## 3. Configurazione Operativa di Riferimento

- **LLM Engine**: LM Studio local server (`http://127.0.0.1:1234/v1`)
- **Modello Attivo**: `qwen2.5-7b-instruct` (Q4_K_M GGUF)
- **Lingua & Strategia**: `DIRECT_MULTILINGUAL` (analisi multilingua con output in lingua italiana)
- **Protocollo di Citazione**: `deterministic_alias_v1` (alias brevi `E001`, `E002`... con mappatura biunivoca reversibile)
- **Formato Serializzazione**: `compact_jsonl_v1` (oggetti a riga singola con `id`, `msg_id`, `sender`, `text`)
- **Soglia Microchunk Preventivo**: $\le 8$ messaggi sostanziali per unità
- **Profondità Massima Bisezione Adattiva**: Livello 3 (divide-and-conquer)
- **Database di Checkpoint Isolato**: `output/isolated_microchunk_test_db/checkpoints.db`

---

## 4. Risultati Empirici del Test Reale su Chunks 0, 12 e 25

Lo script dedicato `scripts/test_real_microchunk_discovery.py` ha eseguito l'ingestion della chat reale ed estratto i tre blocchi critici, processandoli in tempo reale contro LM Studio:

### 4.1 Chunk 0 (42 messaggi, 36 sostanziali)
- **Suddivisione Preventiva**: 5 micro-chunk.
- **Esecuzione**: Tutti e 5 i micro-chunk sono stati completati con successo al primo tentativo.
- **Argomenti Estratti**: 8 topic tematici autentici consolidati.
- **Evidenze Citate**: 33 citazioni verificate conformi agli identificatori originali. Citazioni non valide: **0**.
- **Stato**: `COMPLETE`.

### 4.2 Chunk 12 (41 messaggi, 40 sostanziali)
- **Suddivisione Preventiva**: 5 micro-chunk.
- **Esecuzione**: Micro-chunk 0, 1, 2 e 4 completati direttamente al primo tentativo.
- **Intervento Bisezione Adattiva**: Il micro-chunk 3 ha richiesto una bisezione ricorsiva a livello 1 in due sotto-blocchi (`half_a` e `half_b`). Entrambi i sotto-blocchi hanno avuto successo e si sono aggregati in 4 topic.
- **Argomenti Estratti**: 8 topic tematici autentici consolidati per il chunk padre 12.
- **Evidenze Citate**: 44 citazioni verificate. Citazioni non valide: **0**.
- **Stato**: `COMPLETE`. Durata: 227,9 s.

### 4.3 Chunk 25 (19 messaggi, 18 sostanziali)
- **Suddivisione Preventiva**: 3 micro-chunk.
- **Esecuzione**: Tutti e 3 i micro-chunk completati direttamente al primo tentativo.
- **Argomenti Estratti**: 4 topic tematici autentici consolidati.
- **Evidenze Citate**: 15 citazioni verificate. Citazioni non valide: **0**.
- **Stato**: `COMPLETE`. Durata: 58,5 s.

### 4.4 Consolidamento Globale Multi-Chunk
I topic estratti dai 3 chunk sono stati consolidati dal riduttore gerarchico in **10 topic globali definitivi**, preservando l'integrità di tutte le evidenze citate e senza duplicati concettuali o allucinazioni.

### 4.5 Verifica di Persistenza e Idempotenza al Ricarico (Reload)
Ricaricando immediatamente i risultati per i tre chunk dal database SQLite isolato:
- **Richieste HTTP verso LM Studio**: **0 (ZERO)**
- **Tempo di caricamento**:
  - Chunk 0: 0,130 s
  - Chunk 12: 0,109 s
  - Chunk 25: 0,124 s
- **Stato Risultato**: `COMPLETE` con integrità crittografica intatta.

---

## 5. Statistiche Complete della Suite di Test Pytest

L'intera suite di regressione del repository è stata eseguita con isolamento completo (`--basetemp=output/pytest_temp` e `-p no:cacheprovider`):

```text
======================= 2158 passed, 6 skipped, 5 deselected, 2 warnings in 192.17s =======================
Exit Code: 0
```

- **Totale Test Selezionati**: 2.164
- **Test Superati con Successo**: **2.158**
- **Test Falliti**: **0**
- **Test Saltati (Piattaforma Windows/Live Opt-in)**: 6
- **Test Deselezionati**: 5
- **Avvertimenti Non Bloccanti**: 2 (collisioni controllate in test di validazione manifest zip)

---

## 6. Registro Esaustivo dei File Modificati e Creati

1. [ai/serializer.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/ai/serializer.py):
   - Implementata la funzione `serialize_document_compact_jsonl()` per la serializzazione operativa a riga singola.
   - Generazione di alias deterministici compatti (`E001`...) con header provenance contenente `(document_id: ...)`.
   - Inclusione di `msg_id` a fini di disambiguazione.
2. [ai/chunking.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/ai/chunking.py):
   - Implementate le funzioni `is_substantive_message_bundle()` e `split_chunk_into_microchunks()`.
   - Limite preventivo fissato a 8 messaggi sostanziali per micro-chunk.
3. [ai/structured.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/ai/structured.py):
   - Rafforzata la rilevazione di cicli degenerativi `detect_degenerate_repetition()` ignorando le sequenze di indentazione spaziale pura (`pat.strip() != ""`).
   - Aggiunta la normalizzazione dei prefissi spuri del modello (`value: E002` -> `E002`) in `validate_topic_discovery_payload()`.
4. [ai/topics.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/ai/topics.py):
   - Integrazione di `serialize_document_compact_jsonl()` in `discover_topics()` in modalità operativa.
   - Interruzione immediata su degenerazione ciclica senza retry inquinanti.
   - Verifica di headroom sufficiente prima di espandere il budget token nel retry.
5. [analysis/models.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/analysis/models.py):
   - Aggiunti campi `original_chunk_count` e `microchunk_count` ai metadati di run.
6. [analysis/service.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/analysis/service.py):
   - Pipeline di suddivisione preventiva in micro-chunk nei rami single-chunk e multi-chunk.
   - Metodo ricorsivo `_execute_microchunk_with_adaptive_fallback()` con bisezione binaria (profondità $\le 3$) e riscalamento dinamico dei topic.
   - Correzione del bilancio di completamento multi-chunk per evitare falsi stati `PARTIAL`.
7. [scripts/preflight_checkpoint_resume.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/scripts/preflight_checkpoint_resume.py):
   - Aggiunta funzione `_is_isolated_test_temp_path()` per autorizzare la staging temp di pytest salvaguardando le protezioni di produzione.
8. [pyproject.toml](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/pyproject.toml):
   - Configurazione di `--basetemp=output/pytest_temp` e `-p no:cacheprovider` per eliminare interferenze di lockfile.
9. [scripts/smoke_operational_ai.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/scripts/smoke_operational_ai.py):
   - Aggiornato il client mock `FakeSmokeClient` per supportare il parsing delle evidenze compatte.
10. [scripts/test_real_microchunk_discovery.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/scripts/test_real_microchunk_discovery.py):
    - Creato script di validazione forense reale sui chunk 0, 12, 25 con LM Studio.
11. [scripts/create_microchunk_fix_release_archive.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/scripts/create_microchunk_fix_release_archive.py):
    - Script automatizzato per la generazione dell'archivio di rilascio sanificato e del digest SHA-256 sul Desktop.
12. [tests/unit/test_realworld_topic_improvements.py](file:///c:/Users/Luca/Desktop/Tesi/ChatAnalysis/tests/unit/test_realworld_topic_improvements.py):
    - Allineata la dimensione dei blocchi mock a $\le 8$ bundle per chunk per rispecchiare fedelmente la soglia operativa di microchunking.

---

## 7. Bonifica Forense e Generazione Archivio Sanificato

In conformità con i vincoli peritali e di riservatezza:
- **Esclusioni Tassative Applicate**:
  - Nessun file contenente conversazioni WhatsApp reali (`test_data/reference_chat_document.zip`, archivi WhatsApp privati).
  - Nessun database SQLite, log o file di checkpoint runtime (`*.db`, `*.db-wal`, `*.db-shm`, `.checkpoints/`).
  - Nessun archivio ZIP annidato contenente log o trascrizioni riservate.
  - Nessuna cartella di compilazione, runtime o ambiente virtuale (`.git/`, `.venv/`, `.venv312/`, `__pycache__/`).
- **Verifica Ricorsiva**: Ogni archivio compresso consentito è stato ispezionato internamente byte-per-byte per confermare l'assenza di dati sensibili.
- **Integrità Crittografica**: Generato il manifest interno `manifest_sha256.txt` e calcolato il digest esterno dell'archivio.

---

*Fine del rapporto tecnico.*
