# Preliminary Cloud E2E Benchmark Report — Synthetic Dataset (Protocol V3)

> **Tipo Benchmark:** TECHNICAL VALIDATION BENCHMARK
> **Protocol Version:** 3
> **Disclaimer Scientifico:** Questo benchmark sintetico è uno strumento di validazione tecnica E2E preliminare su 6 casi controllati. Non costituisce una valutazione statistica generale del modello.

## 1. Configurazione dell'Esperimento

- **Protocol Version:** `3`
- **Timestamp UTC:** `2026-10-26T22:55:39.232599+00:00`
- **Provider:** `OpenAI-compatible`
- **Endpoint:** `https://api.openai.com/v1`
- **Modello Richiesto:** `gpt-4o`
- **Modello Restituito:** `gpt-4o`
- **Modalità JSON:** `json_schema`
- **Timeout:** `60.0s` | **Max Tokens:** `1024`
- **Temperature:** `0.0` | **Seed:** `42`
- **Pacing Preventivo (Interval):** `15.0s`
- **Limiti Provider Osservati:** `5 RPM` | `250000 TPM` | `20 RPD`
- **Automatic Retry:** `NO (One request = One attempt)`
- **Dataset SHA-256:** `2E49A9C62D89125D...` (VERIFICATO)
- **Ground Truth SHA-256:** `377FB95B7690BA50...` (VERIFICATO)

## 2. Metriche di Esecuzione e Topic Detection

### 2.1 Esecuzione Chiamate e Inferenze
- **Cloud API Requests Sent:** 8
- **LLM Inferences Completed:** 8
- **Model Outputs Received:** 8
- **Detection Completion Rate:** 100.0%

### 2.2 Pacing Telemetria Preventivo (Protocol V3)
- **Intervallo Minimo Configurato:** `15.0s`
- **Richieste con Pacing:** 8
- **Delta Minimo Osservato Inizio Richieste:** 0.0s
- **Delta Medio Osservato Inizio Richieste:** 0.0s
- **Policy di Retry:** `NESSUNA (Automatic Retry: False, Selective Retry: False)`

| Seq | Offset Inizio (s) | Delta da Richiesta Precedente (s) |
|---|---|---|
| 1 | 0.000s | N/A (prima richiesta) |
| 2 | 0.000s | 0.000s |
| 3 | 0.000s | 0.000s |
| 4 | 0.000s | 0.000s |
| 5 | 0.000s | 0.000s |
| 6 | 0.000s | 0.000s |
| 7 | 0.000s | 0.000s |
| 8 | 0.000s | 0.000s |

### 2.3 Metriche Decisionali

| Metrica | Valore |
|---|---|
| Casi Totali Valutati | 6 |
| Casi Completati con Successo | 6 |
| Casi Falliti (Backend/Execution) | 0 |
| Detection Completion Rate | 100.0% |
| Decisioni Corrette (Casi Completati) | 6 / 6 |
| Accuratezza Decisionale (Casi Completati) | 100.0% |
| PRESENT (Corretti/Completati/Totale) | 4 / 4 (4 totali) |
| ABSENT (Corretti/Completati/Totale) | 1 / 1 (1 totali) |
| UNCERTAIN (Corretti/Completati/Totale) | 1 / 1 (1 totali) |
| Citazioni Evidenze Invalide | 0 |
| Errori Structured Output | 0 |
| Errori Backend / Rete | 0 |
| Errori Model Mismatch | 0 |
| Latenza Media Inferenza Riuscita | 0.123s |

## 3. Dettaglio Risultati per Caso

| Caso | Strategia | Atteso | Ottenuto | Esito | Latenza | Token Totali | Status |
|------|-----------|--------|----------|-------|---------|--------------|--------|
| CASE_1_EXPLICIT_PRESENT | DIRECT_MULTILINGUAL | PRESENT | PRESENT | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_2_IMPLICIT_PRESENT | DIRECT_MULTILINGUAL | PRESENT | PRESENT | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_3_AMBIGUOUS_UNCERTAIN | DIRECT_MULTILINGUAL | UNCERTAIN | UNCERTAIN | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_4_ABSENT | DIRECT_MULTILINGUAL | ABSENT | ABSENT | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_5_MULTILINGUAL_DIRECT | DIRECT_MULTILINGUAL | PRESENT | PRESENT | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_5_MULTILINGUAL_TRANSLATE_FIRST | TRANSLATE_FIRST | PRESENT | PRESENT | CORRETTO | 0.123s | 75 | SUCCESS |
| CASE_6_TOPIC_DISCOVERY | DIRECT_MULTILINGUAL | Discovery | 2 topics scoperti | QUALITATIVE | 0.123s | 75 | SUCCESS |

## 4. Dettaglio Topic Discovery (Caso 6 — Valutazione Qualitativa Post-Hoc)

### Topic Estratti Autonomamente dal Modello:
1. **Condominio**: Discussione su spese e assemblea (Evidenze: `['unified:cellebrite_json:ce2e_c6_001::ORIGINAL_TEXT']`)
2. **Calcetto**: Partita di calcetto settimanale (Evidenze: `['unified:cellebrite_json:ce2e_c6_002::ORIGINAL_TEXT']`)

### Topic Attesi Post-Hoc (per confronto qualitativo umano):
1. **Riunione di Condominio** (Keywords: condominio, riunione, amministratore, spese, assemblea)
2. **Calcetto e Attività Sportive** (Keywords: calcetto, partita, giovedì, campo, squadra)
3. **Ristorazione e Cucina** (Keywords: ristorante, sushi, giapponese, cena, prenotare)
4. **Libri e Lettura** (Keywords: libro, romanzo, thriller, libreria, leggere)
5. **Animali Domestici e Cure Veterinarie** (Keywords: cane, veterinario, vaccino, visita, clinica)
6. **Eventi Musicali e Concerti** (Keywords: concerto, jazz, biglietti, festival, musica)

## 5. Criteri di Interpretazione e Distinzione Errori

- **HTTP 429 (Too Many Requests) / HTTP 503 (Service Unavailable):** Classificati come **Execution Failure** dovuti ai vincoli di quota (5 RPM) o disponibilità del provider, **NON** come errori decisionali del modello.
- **Citazioni Evidenze Invalide / Structured Output Error:** Classificati come **Model Format / Compliance Failure**.
- **Decisione Dissonante da Ground Truth:** Classificata come **Model Decision Error**.
- **Nessun Retry:** Ogni richiesta viene eseguita esattamente una volta; fallimenti 429 o 503 non vengono ripetuti.

---
**Dichiarazioni di Sicurezza:**
- Nessun dato reale o personale è stato elaborato (SYNTHETIC DATA ONLY).
- Nessuna API key o credenziale è stata salvata in chiaro nei report.
- Il percorso finale della tesi rimane su hardware locale (LM Studio + Qwen/Llama/DeepSeek).