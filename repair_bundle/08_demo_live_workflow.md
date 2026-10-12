# WORKFLOW DEMO: RISULTATI LIVE VS REFERENCE

## Separazione Architetturale
- Al caricamento del dataset dimostrativo sintetico via `load_demo_dataset()`:
  - `KEY_DETECTION_RESULTS` = `()` (vuoto)
  - `KEY_DISCOVERY_RESULTS` = `()` (vuoto)
  - I risultati storici precalcolati sono confinati in `KEY_REFERENCE_DETECTION_RESULTS` e `KEY_REFERENCE_DISCOVERY_RESULTS`.
- L'utente deve cliccare esplicitamente:
  - **"🚀 Esegui Topic Discovery (LIVE)"** per avviare una reale scansione aperta del testo.
  - **"🔬 Verifica con Topic Detection"** su una delle card scoperte per avviare una seconda inferenza indipendente (senza hint sugli evidence IDs scoperti).
- Il calcolo della consistenza viene quindi operato da `ConsistencyService`.
