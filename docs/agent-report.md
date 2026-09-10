# Report Ufficiale di Sviluppo: Entity Resolution Acceptance Audit & Unified Model Foundation

**Data e Ora**: 2026-09-10  
**Repository**: `c:\Users\lucas\Desktop\Tesi`  
**Autore**: Agente di Sviluppo Software  
**Stato della Pipeline**: DATI ORIGINALI → IMPORTER → RawRecord → VALIDAZIONE → NORMALIZZAZIONE → ENTITY RESOLUTION → UnifiedMessage (Foundation completata e convalidata)

---

## 1. Fase di Lavoro
- **DECISIONE ARCHITETTURALE**: Questa fase ha completato con successo due passaggi cardine della pipeline forense:
  1. **FASE A — Entity Resolution Acceptance Audit**:
     - Audit rigoroso della semantica dei JID WhatsApp (`msgstore.db` e `wa.db`): isolamento strutturale tra JID individuali (`@s.whatsapp.net`) e JID di gruppo (`@g.us`);
     - Riconoscimento formale del JID di gruppo come identificatore di `Chat`/gruppo, con categorico divieto di fusione con numeri telefonici o promozione a partecipante persona;
     - Formalizzazione del proprietario del dispositivo come `LOCAL_USER` (`actor_type="local_user"`, `is_local_user=True`) senza attribuzione speculativa di recapiti telefonici o nomi inventati;
     - Implementazione della semantica di direzione in `msgstore.db` (`key_from_me == 1` $\rightarrow$ mittente `LOCAL_USER`; `key_from_me == 0` $\rightarrow$ destinatario `LOCAL_USER`, mittente desunto da `remote_resource` o JID peer);
     - Estensione dei modelli con `candidate_identifier` (mantenendo `canonical_identifier` per piena retrocompatibilità), nuovi livelli e tipi di evidenza (`GROUP_JID_EXACT`, `LOCAL_USER_EXACT`);
     - Filtraggio dei record strutturali (`chat`, `media_ref`) affinché non producano entità persona spurie;
     - Deduplicazione candidata non distruttiva (`DuplicateCandidate` raggruppa senza cancellare alcun record).
  2. **FASE B — Unified Model Foundation (`unified/`)**:
     - Progettazione e implementazione dei modelli canonici immutabili: `Participant`, `Chat`, `UnifiedMessage` ([unified/models.py](file:///c:/Users/lucas/Desktop/Tesi/unified/models.py));
     - Costruttore di modello unificato `UnifiedModelBuilder` ([unified/builder.py](file:///c:/Users/lucas/Desktop/Tesi/unified/builder.py)) con supporto sia streaming lazy (`build_stream`) che materializzato (`build_all`);
     - Identificatori deterministici standardizzati `unified:{source_name}:{source_record_id}`;
     - Salvaguardia totale dell'integrità temporale: i timestamp naive (`NAIVE_UNKNOWN`) non vengono forzati a UTC né fusi in una timeline universale non documentata;
     - Conservazione integrale di tutti i 956 record messaggio presenti nelle 5 sorgenti sintetiche;
     - Documentazione architetturale formale in [docs/unified-model-design.md](file:///c:/Users/lucas/Desktop/Tesi/docs/unified-model-design.md).

---

## 2. Baseline Iniziale
- **FATTO VERIFICATO**: All'avvio della fase corrente, lo stato del repository registrava:
  - 5 importer concreti operativi e read-only (`WhatsAppMsgstoreImporter`, `WhatsAppWaDbImporter`, `CellebriteCsvImporter`, `CellebriteJsonImporter`, `CellebriteXmlImporter`);
  - Layer di validazione `validation/` con severity rigorosa;
  - Layer di normalizzazione `normalization/` formalizzato;
  - Layer di entity resolution `entity_resolution/` iniziale;
  - Suite pytest precedente: 567 test raccolti, 566 passed, 0 failed, 0 errors, 1 skipped.

---

## 3. FASE A — Entity Resolution Acceptance Audit & Hardening

### 3.1 Audit Semantico JID WhatsApp e Direzione Messaggi (A1)
- **FATTO VERIFICATO**: Ispezione su `test_data/whatsapp_export/msgstore.db` e `wa.db`:
  - **JID Individuali (`@s.whatsapp.net`)**: rappresentano contatti individuali (es. `+390000000001@s.whatsapp.net`, `+390000000002@s.whatsapp.net`). Possono essere correlati deterministicamente al numero di telefono se identico alla local part numerica (`EvidenceType.PHONE_JID_LOCAL`).
  - **JID di Gruppo (`@g.us`)**: nel dataset è presente il JID `00000000001-0000000000@g.us` associato al gruppo `Gruppo_Sintetico_01`. Rappresenta una `Chat`, **NON una persona**. È stata introdotta l'evidenza `EvidenceType.GROUP_JID_EXACT` con `entity_type="group"`. È categoricamente impedito qualsiasi tentativo di estrarre una persona o collegarlo a numeri di telefono.
  - **Proprietario del Dispositivo (`LOCAL_USER`)**:
    - Quando `key_from_me == 1`: il mittente è il proprietario del dispositivo, normalizzato come `NormalizedActor(raw_value="LOCAL_USER", actor_type="local_user")`. Se la chat è 1-to-1, il destinatario è `key_remote_jid`; se è di gruppo, il destinatario è `None`.
    - Quando `key_from_me == 0`: il destinatario è `LOCAL_USER`. Il mittente reale è estratto da `remote_resource` (es. `+39 000 0000001`, `+39 000 0000002`, `group_participant_A`) oppure, in assenza di esso nelle chat 1-to-1, da `key_remote_jid`.
    - `LOCAL_USER` viene risolto in una `CandidateEntity` dedicata con `candidate_identifier="LOCAL_USER"`, `entity_type="local_user"`, ed evidenza `LOCAL_USER_EXACT`.

### 3.2 Filtraggio Record Non-Messaggio (A2)
- **FATTO VERIFICATO**:
  - `msgstore.db` contiene 18 record `chat` (tabella `chat_list`) e 100 record `media_ref` (tabella `messages` con `media_url` privo di corpo o di contesto).
  - Sia `RecordNormalizer` che `DeterministicEntityResolver` ignorano esplicitamente i record con `record_type in ("chat", "media_ref")` durante l'estrazione degli attori persona, impedendo la creazione di entità spurie.
  - I record `chat` vengono invece valorizzati per estrarre l'attributo `chat_id` e memorizzare il titolo (`subject`) delle conversazioni.

### 3.3 Deduplicazione Non-Distruttiva (A3)
- **FATTO VERIFICATO**:
  - `DuplicateCandidate` associa coppie o gruppi di messaggi con medesimo testo normalizzato e timestamp compatibile (es. messaggi sintetici condivisi tra WhatsApp e Cellebrite).
  - **Nessun record viene eliminato né sovrascritto**: i record d'origine rimangono TUTTI preservati.

### 3.4 Esito Gate A
- **FATTO VERIFICATO**: Esecuzione pytest dopo l'audit di Entity Resolution:
  - Risultato: **566 passed, 1 skipped in 17.10s, 0 failed, 0 errors**. Gate A superato con successo.

---

## 4. FASE B — Unified Model Foundation (`unified/`)

### 4.1 Architettura e Obiettivi
È stato implementato il nuovo package [unified/](file:///c:/Users/lucas/Desktop/Tesi/unified/):
- **Principio vincolante**: costruire il modello dati finale preservando la completa catena di custodia e provenance verso `NormalizedRecord`, `RawRecord` e file originale.
- **Nessuna perdita d'informazione**: nessun messaggio scartato per presunta duplicazione.
- **Integrità temporale assoluta**: i timestamp con offset ignoto rimangono `NAIVE_UNKNOWN`.

### 4.2 Modelli Dati ([unified/models.py](file:///c:/Users/lucas/Desktop/Tesi/unified/models.py))
- **`Participant`**:
  - Dataclass congelata (`frozen=True`) con `participant_id`, `identifier`, `display_name`, `entity_candidate_id`, `is_local_user`, e `metadata` (`MappingProxyType`).
  - Se associato a `LOCAL_USER`, presenta `is_local_user=True` e `identifier="LOCAL_USER"`.
  - Se collegato a un'entità risolta, `entity_candidate_id` punta a `CandidateEntity.candidate_id` e `display_name` eredita il nome di rubrica (es. `"Contatto_001"`).
- **`Chat`**:
  - Dataclass congelata (`frozen=True`) con `chat_id`, `chat_type` (`"direct"`, `"group"`, `"unknown"`), `title`, `participants` (tupla immutabile), `source_name`, e `metadata`.
  - Distingue rigorosamente le chat 1-to-1 (`"direct"`) dai gruppi (`"group"`, es. `@g.us`).
- **`UnifiedMessage`**:
  - Dataclass congelata (`frozen=True`) con `message_id` deterministico `unified:{source_name}:{source_record_id}`.
  - Piena provenance: campo obbligatorio `provenance_record: NormalizedRecord`.
  - Mantiene `timestamp: NormalizedTimestamp` con il suo stato invariato (`KNOWN_UTC`, `NAIVE_UNKNOWN`, `ABSENT`).
  - Mantiene `duplicate_candidate_ids: tuple[str, ...]` che traccia in modo trasparente l'appartenenza a cluster di duplicati senza rimuovere il messaggio.

### 4.3 Costruttore del Modello Unificato ([unified/builder.py](file:///c:/Users/lucas/Desktop/Tesi/unified/builder.py))
- Implementata la classe `UnifiedModelBuilder`:
  - **Indicizzazione deterministica**: indicizza in memoria le `CandidateEntity` e i `DuplicateCandidate` di `ResolutionResult` per lookup immediato $O(1)$.
  - **Pre-scansione titoli chat**: analizza i record descrittori `chat` (es. `chat_list`) arricchendo i gruppi con il rispettivo `subject` (`"Gruppo_Sintetico_01"`).
  - **Filtro record messaggi**: scarta record ausiliari non di tipo messaggio (`contact`, `chat`, `media_ref`) senza creare `UnifiedMessage` spuri.
  - **Modalità streaming (`build_stream`)**: yield lazy incrementale dei messaggi.
  - **Modalità materializzata (`build_all`)**: restituisce la lista completa dei messaggi unificati.

---

## 5. Metriche di Pipeline e Conteggi Verificati

| Sorgente | File Sorgente | Record Raw Totali | Record Non-Messaggio | Record Messaggio | UnifiedMessage Generati | Stato Timestamp | Note Forensi |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **WhatsApp `msgstore.db`** | `test_data/whatsapp_export/msgstore.db` | 622 | 118 (18 chat, 100 media) | 504 | **504** | `KNOWN_UTC` | `LOCAL_USER` tracciato; gruppo sintetico con titolo |
| **WhatsApp `wa.db`** | `test_data/whatsapp_export/wa.db` | 4 | 4 (contatti) | 0 | **0** | N/A | Contatti usati per Entity Resolution |
| **Cellebrite CSV** | `test_data/cellebrite_export/messages.csv` | 302 | 0 | 302 | **302** | `NAIVE_UNKNOWN` | Nessuna assunzione UTC forzata; 1 timestamp vuoto (`ABSENT`) |
| **Cellebrite JSON** | `test_data/cellebrite_export/messages.json` | 100 | 0 | 100 | **100** | `KNOWN_UTC` | Offset `+00:00` esplicito |
| **Cellebrite XML** | `test_data/cellebrite_export/report.xml` | 50 | 0 | 50 | **50** | `KNOWN_UTC` | Offset `+00:00` esplicito |
| **TOTALE** | - | **1078** | **122** | **956** | **956** | - | **0 messaggi persi, 100% provenance preservata** |

---

## 6. File Modificati e Creati

### File Modificati
- [normalization/models.py](file:///c:/Users/lucas/Desktop/Tesi/normalization/models.py): aggiunto `"local_user"` a `actor_type` in `NormalizedActor`; aggiunto campo opzionale `chat_id` a `NormalizedRecord`.
- [normalization/normalizer.py](file:///c:/Users/lucas/Desktop/Tesi/normalization/normalizer.py): implementata la semantica di direzione `key_from_me` per `msgstore_db`, estrazione `chat_id`, gestione `LOCAL_USER`.
- [entity_resolution/models.py](file:///c:/Users/lucas/Desktop/Tesi/entity_resolution/models.py): aggiunti `GROUP_JID_EXACT`, `LOCAL_USER_EXACT` a `EvidenceType`; aggiunti ruoli e tipi estesi a `EntityReference`; introdotto `candidate_identifier` con `@property canonical_identifier` in `CandidateEntity`.
- [entity_resolution/resolver.py](file:///c:/Users/lucas/Desktop/Tesi/entity_resolution/resolver.py): filtraggio record non-persona, isolamento JID gruppo `@g.us`, risoluzione deterministica di `LOCAL_USER`.
- [tests/unit/test_entity_resolver.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_entity_resolver.py): aggiunti test unitari per gruppo JID, `LOCAL_USER` e filtraggio `chat`/`media_ref`.
- [tests/integration/test_entity_resolution_integration.py](file:///c:/Users/lucas/Desktop/Tesi/tests/integration/test_entity_resolution_integration.py): aggiunto test di integrazione per isolamento gruppo JID e presenza `LOCAL_USER`.
- [pyproject.toml](file:///c:/Users/lucas/Desktop/Tesi/pyproject.toml): aggiunto `"unified*"` a `tool.setuptools.packages.find.include`.
- [docs/agent-report.md](file:///c:/Users/lucas/Desktop/Tesi/docs/agent-report.md): aggiornamento integrale ed esclusivo dello stato e delle metriche.

### File Creati
- [unified/__init__.py](file:///c:/Users/lucas/Desktop/Tesi/unified/__init__.py): package export per `Chat`, `Participant`, `UnifiedMessage`, `UnifiedModelBuilder`.
- [unified/models.py](file:///c:/Users/lucas/Desktop/Tesi/unified/models.py): modelli immutabili `Participant`, `Chat`, `UnifiedMessage`.
- [unified/builder.py](file:///c:/Users/lucas/Desktop/Tesi/unified/builder.py): costruttore deterministico `UnifiedModelBuilder`.
- [docs/unified-model-design.md](file:///c:/Users/lucas/Desktop/Tesi/docs/unified-model-design.md): specifica architetturale e documentazione del modello unificato.
- [tests/unit/test_unified_models.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_unified_models.py): test unitari su immutabilità, validazione e tipi di `Participant`, `Chat`, `UnifiedMessage`.
- [tests/unit/test_unified_builder.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_unified_builder.py): test unitari su ID deterministico, preservazione fusi orari, linking duplicati, risoluzione partecipanti e streaming.
- [tests/integration/test_unified_pipeline_integration.py](file:///c:/Users/lucas/Desktop/Tesi/tests/integration/test_unified_pipeline_integration.py): 7 test di integrazione end-to-end su tutte le 5 sorgenti (956 messaggi unificati convalidati).

---

## 7. Risultati della Suite di Test Finale (Gate C)

- **FATTO VERIFICATO**:
  - Comando eseguito: `.venv\Scripts\python.exe -m pytest`
  - Totale test raccolti (`collected`): **595**
  - Superati (`passed`): **594**
  - Falliti (`failed`): **0**
  - Errori (`errors`): **0**
  - Saltati (`skipped`): **1** (`tests/unit/test_symlink_safety.py::TestVerifySafety::test_symlink_pointing_outside_rejected` su Windows per policy standard sui permessi symlink non privilegiati)
  - Avvisi (`warnings`): **0**
  - Durata: **16.90s**
- **DICHIARAZIONE FORMALE**: La suite pytest è completamente verde. Nessun warning, 0 errori, 0 fallimenti.

---

## 8. Rischi Tecnici e Technical Debt Osservato

1. **Timeline Eterogenee per Visualizzazione/Analisi**:
   - Poiché 301 messaggi di Cellebrite CSV hanno stato temporale `NAIVE_UNKNOWN`, qualsiasi ordinamento temporale globale futuro richiederà all'utente o all'analista di specificare esplicitamente un fuso orario di riferimento per l'acquisizione, anziché forzare arbitrariamente UTC a livello di pipeline.
2. **Pseudonimi ed Alias Non Risolti**:
   - Gli alias come `group_participant_A` e gli identificatori `chat_1` rimangono confinati come entità unresolved o partecipanti pseudonimizzati. I futuri moduli di analisi semantica potranno analizzare correlazioni contestuali senza violare la certezza deterministica del modello di base.
3. **Memoria di Risoluzione per Dataset Forensi Massivi**:
   - Gli indici in memoria di `UnifiedModelBuilder` e `DeterministicEntityResolver` scalano $O(U)$ con $U$ identificatori unici. Per dataset con milioni di contatti o messaggi, sarà opportuno predisporre un'opzione di persistenza temporanea su SQLite.

---

## 9. Prossimo Passo Suggerito (SENZA IMPLEMENTARLO)

- **Fase successiva della pipeline**: **ANALISI AI & EXPORT / VISUALIZZAZIONE FORENSE**.
  - Potenziali obiettivi della fase successiva:
    1. Ingestion dei 956 `UnifiedMessage` per estrazione semantica ed analisi tematica tramite modelli locali (LM Studio / Ollama);
    2. Moduli di interrogazione temporale e ricerca conversazionale strutturata;
    3. Interfaccia utente (es. Streamlit) per la revisione interattiva della catena di custodia e dei cluster di messaggi correlati.
  - **DICHIARAZIONE DI CONFINAMENTO**: Nessuna componente AI, LLM, embedding, multimodalità o Streamlit è stata implementata in questa fase.
