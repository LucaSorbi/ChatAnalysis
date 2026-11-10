# Preliminary Cloud E2E Benchmark Report — Synthetic Dataset (Protocol V3)

> **Tipo Benchmark:** TECHNICAL VALIDATION BENCHMARK
> **Protocol Version:** 3
> **Disclaimer Scientifico:** Questo benchmark sintetico è uno strumento di validazione tecnica E2E preliminare su 6 casi controllati. Non costituisce una valutazione statistica generale del modello.

## 1. Configurazione dell'Esperimento

- **Protocol Version:** `3`
- **Timestamp UTC:** `2026-10-26T22:55:39.662188+00:00`
- **Provider:** `OpenAI-compatible`
- **Endpoint:** `https://api.openai.com/v1`
- **Modello Richiesto:** `gpt-4o`
- **Modello Restituito:** `N/A`
- **Modalità JSON:** `json_schema`
- **Timeout:** `60.0s` | **Max Tokens:** `1024`
- **Temperature:** `0.0` | **Seed:** `null` (UNSUPPORTED_OMITTED)
- **Pacing Preventivo (Interval):** `15.0s`
- **Limiti Provider Osservati:** `5 RPM` | `250000 TPM` | `20 RPD`
- **Automatic Retry:** `NO (One request = One attempt)`
- **Dataset SHA-256:** `2E49A9C62D89125D...` (VERIFICATO)
- **Ground Truth SHA-256:** `377FB95B7690BA50...` (VERIFICATO)

## 2. Metriche di Esecuzione e Topic Detection

### 2.1 Esecuzione Chiamate e Inferenze
- **Cloud API Requests Sent:** 7
- **LLM Inferences Completed:** 0
- **Model Outputs Received:** 0
- **Detection Completion Rate:** 0.0%

### 2.2 Pacing Telemetria Preventivo (Protocol V3)
- **Intervallo Minimo Configurato:** `15.0s`
- **Richieste con Pacing:** 7
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

### 2.3 Metriche Decisionali

| Metrica | Valore |
|---|---|
| Casi Totali Valutati | 6 |
| Casi Completati con Successo | 0 |
| Casi Falliti (Backend/Execution) | 6 |
| Detection Completion Rate | 0.0% |
| Decisioni Corrette (Casi Completati) | 0 / 0 |
| Accuratezza Decisionale (Casi Completati) | N/A |
| PRESENT (Corretti/Completati/Totale) | 0 / 0 (4 totali) |
| ABSENT (Corretti/Completati/Totale) | 0 / 0 (1 totali) |
| UNCERTAIN (Corretti/Completati/Totale) | 0 / 0 (1 totali) |
| Citazioni Evidenze Invalide | 0 |
| Errori Structured Output | 0 |
| Errori Backend / Rete | 6 |
| Errori Model Mismatch | 0 |
| Latenza Media Inferenza Riuscita | 0.0s |

## 3. Dettaglio Risultati per Caso

| Caso | Strategia | Atteso | Ottenuto | Esito | Latenza | Token Totali | Status |
|------|-----------|--------|----------|-------|---------|--------------|--------|
| CASE_1_EXPLICIT_PRESENT | DIRECT_MULTILINGUAL | PRESENT | - | FAILED | N/A | N/A | FAILED |
| CASE_2_IMPLICIT_PRESENT | DIRECT_MULTILINGUAL | PRESENT | - | FAILED | N/A | N/A | FAILED |
| CASE_3_AMBIGUOUS_UNCERTAIN | DIRECT_MULTILINGUAL | UNCERTAIN | - | FAILED | N/A | N/A | FAILED |
| CASE_4_ABSENT | DIRECT_MULTILINGUAL | ABSENT | - | FAILED | N/A | N/A | FAILED |
| CASE_5_MULTILINGUAL_DIRECT | DIRECT_MULTILINGUAL | PRESENT | - | FAILED | N/A | N/A | FAILED |
| CASE_5_MULTILINGUAL_TRANSLATE_FIRST | TRANSLATE_FIRST | PRESENT | - | FAILED | N/A | N/A | FAILED |
| CASE_6_TOPIC_DISCOVERY | DIRECT_MULTILINGUAL | Discovery | 0 topics scoperti | QUALITATIVE | N/A | N/A | FAILED |

## 4. Dettaglio Topic Discovery (Caso 6 — Valutazione Qualitativa Post-Hoc)

Topic Discovery non riuscito. Errore: `AiBackendRequestError` — `Simulated 400 Bad Request`

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