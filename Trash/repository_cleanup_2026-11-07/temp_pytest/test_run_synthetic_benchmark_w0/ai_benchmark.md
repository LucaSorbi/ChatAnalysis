# Benchmark Sperimentale AI Locale — Topic Detection Multilingue

**Modalità**: `FAKE_HARNESS_VALIDATION`  
**Modello**: `qwen-2.5-1.5b` (Famiglia: `QWEN`, Quant: `Q4_K_M`)  
**Contesto Runtime Benchmark**: N/D  
**Capacità Massima Modello (Max Context)**: N/D  
**Data e Ora (UTC)**: 2026-10-26T22:55:37.445739+00:00  
**Avviso**: Campione sperimentale pilota non generalizzabile statisticamente. I risultati ottenuti con Fake client non costituiscono inferenza reale sui pesi.  

## 1. Metriche per Strategia Linguistica (Confronto Primario su Latenza End-to-End)

| Strategia | Query Totali | Completate | Completion Rate | Accuracy | Precision | Recall | F1 Score | Incerti | Fallimenti Output | Latenza E2E Amortizzata (s) | Latenza E2E Cold (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DIRECT_MULTILINGUAL** | 8 | 8 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 0 | 0.0 | 0.0 |

## 2. Note Metodologiche ed Error Taxonomy
- Le metriche decisionali (Accuracy, Precision, Recall, F1) sono formalmente calcolate sulle query valide completate affiancate dal Completion Rate.
- **Metodologia di Latenza**: Per la strategia TRANSLATE_FIRST, la traduzione dello scenario è eseguita una sola volta e riutilizzata per le query dello scenario. Il confronto primario ('Latenza E2E Amortizzata') ripartisce equamente la latenza di traduzione tra le query. La 'Latenza E2E Cold' riflette invece il caso peggiore di singola query con traduzione dedicata.