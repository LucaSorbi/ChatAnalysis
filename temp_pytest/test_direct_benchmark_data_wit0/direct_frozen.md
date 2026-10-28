# Benchmark Sperimentale AI Locale — Topic Detection Multilingue

**Modalità**: `REAL_MODEL_BENCHMARK`  
**Modello**: `qwen2.5-7b-instruct` (Famiglia: `QWEN`, Quant: `Q4_K_M` (4-bit))  
**Contesto Runtime Benchmark**: 8192 token  
**Capacità Massima Modello (Max Context)**: N/D  
**Data e Ora (UTC)**: 2026-10-08T12:00:00Z  
**Avviso**: Test notice  

## 1. Metriche per Strategia Linguistica (Confronto Primario su Latenza End-to-End)

| Strategia | Query Totali | Completate | Completion Rate | Accuracy | Precision | Recall | F1 Score | Incerti | Fallimenti Output | Latenza E2E Amortizzata (s) | Latenza E2E Cold (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |

## 2. Note Metodologiche ed Error Taxonomy
- Le metriche decisionali (Accuracy, Precision, Recall, F1) sono formalmente calcolate sulle query valide completate affiancate dal Completion Rate.
- **Metodologia di Latenza**: Per la strategia TRANSLATE_FIRST, la traduzione dello scenario è eseguita una sola volta e riutilizzata per le query dello scenario. Il confronto primario ('Latenza E2E Amortizzata') ripartisce equamente la latenza di traduzione tra le query. La 'Latenza E2E Cold' riflette invece il caso peggiore di singola query con traduzione dedicata.