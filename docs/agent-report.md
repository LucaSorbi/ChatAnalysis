# Report Ufficiale di Sviluppo: Hardening Finale e Quinto Importer Forense `CellebriteXmlImporter`

**Data e Ora**: 2026-09-10  
**Repository**: `c:\Users\lucas\Desktop\Tesi`  
**Autore**: Agente di Sviluppo Software  
**Stato della Pipeline**: DATI ORIGINALI → IMPORTER → RawRecord (Fase Ingestion completata per tutte e 5 le sorgenti forensi)

---

## 1. Fase di Lavoro
- **DECISIONE ARCHITETTURALE**: Questa fase ha completato l'hardening della foundation e del JSON importer, la validazione di sicurezza del tooling permanente di sanitizzazione, e l'implementazione integrale del quinto ed ultimo importer forense del layer di ingestion: `CellebriteXmlImporter` per esportazioni Cellebrite UFDR (`test_data/cellebrite_export/report.xml`).

---

## 2. Baseline Iniziale
- **FATTO VERIFICATO**: Lo stato all'inizio della sessione registrava:
  - 4 importer operativi: `WhatsAppMsgstoreImporter`, `WhatsAppWaDbImporter`, `CellebriteCsvImporter`, `CellebriteJsonImporter`;
  - Provenance multilinea CSV basata sulla riga fisica di inizio (`row:<start_line>`);
  - Deep immutability ricorsiva attiva in `RawRecord` per strutture annidate (`MappingProxyType` e `tuple`);
  - Suite pytest precedente: 377 passed, 0 failed, 0 errors.

---

## 3. Hardening JSON `can_import()`
- **FATTO VERIFICATO**: In [importer/cellebrite_json.py](file:///c:/Users/lucas/Desktop/Tesi/importer/cellebrite_json.py), il blocco generico `except Exception: return False` è stato rimosso e sostituito con la gestione ristretta delle sole eccezioni realmente previste per I/O e parsing:
  ```python
  except (OSError, ijson.JSONError, UnicodeDecodeError):
      return False
  ```
- **DECISIONE ARCHITETTURALE**: Nessun bug programmatico (es. `TypeError`, `AttributeError`, errori interni imprevisti) viene mascherato. Un test dedicato (`test_does_not_swallow_unexpected_programming_errors`) in [tests/unit/test_cellebrite_json_importer.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_cellebrite_json_importer.py) verifica con `monkeypatch` che eccezioni inattese sollevino regolarmente errore senza essere soppresse.

---

## 4. Correzione Claim Memoria Streaming
- **VALUTAZIONE**: È stata eliminata la formulazione teorica di complessità spaziale assoluta "$O(1)$", non matematicamente rigorosa in presenza di buffer del parser e record di dimensione variabile.
- **DECISIONE ARCHITETTURALE**: In [docs/importer-design.md](file:///c:/Users/lucas/Desktop/Tesi/docs/importer-design.md) il comportamento della memoria streaming per CSV, JSON e XML è ora descritto oggettivamente:
  - Nessun caricamento dell'intero dataset in memoria;
  - Elaborazione incrementale a generatore (`yield`);
  - Consumo di memoria limitato principalmente al record corrente e ai buffer interni del parser;
  - Consumo non proporzionale al numero complessivo dei record della sorgente.

---

## 5. Test di Sicurezza Sanitizer
- **FATTO VERIFICATO**: È stata creata la suite di test unitari [tests/unit/test_sanitize_dataset.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_sanitize_dataset.py) (10 test) per verificare la funzione di guardia `_verify_safety()` dello script permanente [scripts/sanitize_synthetic_dataset.py](file:///c:/Users/lucas/Desktop/Tesi/scripts/sanitize_synthetic_dataset.py):
  1. Path dentro `test_data/` → consentito;
  2. Path fuori da `test_data/` → bloccato con `ValueError`;
  3. Path con `..` traversal che risolve all'esterno → bloccato;
  4. Verifica basata su `path.resolve().parts` (directory fake con prefissi testuali come `test_data_fake` o `my_test_data` rifiutate);
  5. Symlink puntante all'esterno → risolto all'esterno e bloccato (gracefully skipped se il sistema operativo Windows richiede privilegi elevati);
  6. Tutte le funzioni pubbliche di sanitizzazione (`sanitize_csv`, `sanitize_json`, `sanitize_wa_db`, `sanitize_msgstore_db`, `sanitize_xml`) invocano preventivamente la guardia.
  Tutti i test sono eseguiti su directory temporanee isolate (`tmp_path`) senza mai alterare i file di test reali.

---

## 6. Risultato Gate A
- **FATTO VERIFICATO**:
  - Comando: `.venv\Scripts\python -m pytest`
  - Esito: **387 passed, 1 skipped in 16.21s, 0 failed, 0 errors, 0 warnings**.

---

## 7. Struttura XML Osservata nel Campione Reale
- **FATTO VERIFICATO**: Ispezione diretta di `test_data/cellebrite_export/report.xml`:
  - Dimensione: 10.879 byte (~10 KB);
  - Gerarchia:
    ```xml
    <?xml version='1.0' encoding='utf-8'?>
    <DumpFile type="UFDR" version="2.0">
      <DeviceInfo>
        <Model>Samsung Galaxy S21</Model>
        <OS>Android 13</OS>
        <IMEI>000000000000001</IMEI>
      </DeviceInfo>
      <InstantMessages>
        <Message id="0">
          <Timestamp>2024-04-23T20:30:15+00:00</Timestamp>
          <Sender>+39 000 0000001</Sender>
          <Body>Messaggio di test sintetico 08</Body>
          <Deleted>false</Deleted>
        </Message>
        ...
      </InstantMessages>
    </DumpFile>
    ```

---

## 8. Encoding XML
- **FATTO VERIFICATO**: UTF-8 standard, dichiarato esplicitamente nell'XML declaration `<?xml version='1.0' encoding='utf-8'?>`.

---

## 9. Root Element
- **FATTO VERIFICATO**: `<DumpFile type="UFDR" version="2.0">`. Contiene gli attributi `type="UFDR"` e `version="2.0"`.

---

## 10. Namespace XML
- **FATTO VERIFICATO**: Il file `report.xml` sintetico del dataset corrente non definisce attributi `xmlns` (namespace di default vuoto).
- **DECISIONE ARCHITETTURALE**: L'importer implementa la funzione ausiliaria `_extract_local_tag(tag)` che rimuove in modo trasparente l'URI del namespace (es. `{http://...}Message` → `Message`), consentendo la corretta elaborazione di report UFDR sia con che senza namespace espliciti.

---

## 11. Elementi Messaggio
- **FATTO VERIFICATO**: I record messaggio sono rappresentati dal tag `<Message>` racchiuso all'interno del contenitore `<InstantMessages>`.

---

## 12. Numero dei Messaggi
- **FATTO VERIFICATO**: Esattamente **50 messaggi**, con ID progressivo nativo da `id="0"` a `id="49"`.

---

## 13. Attributi XML
- **FATTO VERIFICATO**: Ciascun elemento `<Message>` possiede l'attributo nativo `id` (es. `id="0"`). Gli attributi dell'elemento vengono estratti e preservati all'interno della chiave `@attributes` e al contempo esposti nella chiave `id` di `raw_fields`.

---

## 14. Child ed Elementi Ripetuti
- **FATTO VERIFICATO**: Ciascun elemento `<Message>` presenta 4 child elements osservati:
  - `<Timestamp>` (testo ISO 8601);
  - `<Sender>` (testo numero internazionale o alias);
  - `<Body>` (testo del messaggio, oppure elemento vuoto auto-chiuso `<Body />` tradotto in `None`);
  - `<Deleted>` (testo stringa `"false"` o `"true"`).
- **DECISIONE ARCHITETTURALE**: La funzione `_xml_element_to_dict` mappa ricorsivamente i figli. Qualora nel documento compaiano tag fratelli ripetuti, essi vengono preservati come lista ordinata (`list`), garantendo che cardinalità e sequenza originaria non vadano disperse.

---

## 15. Decisione di Sicurezza XML (Input Non Fidato)
- **DECISIONE ARCHITETTURALE**: Poiché le acquisizioni forensi sono suscettibili di manipolazioni esterne o artefatti corrotti/malevoli, il file XML è trattato rigorosamente come **input non fidato**.

---

## 16. DTD e DOCTYPE
- **FATTO VERIFICATO**: La presenza di dichiarazioni DTD o direttive `<!DOCTYPE>` è vietata tramite `forbid_dtd=True`. Qualsiasi tentativo di parsing di file contenenti DTD solleva immediatamente `defusedxml.common.DTDForbidden`. In `can_import()` la condizione restituisce `False`, mentre in `import_records()` solleva `ValueError`.

---

## 17. Entities ed External Entities (Billion Laughs / XXE)
- **FATTO VERIFICATO**:
  - Tentativi di espansione ricorsiva delle entità interne (attacchi DoS / Billion Laughs) vengono intercettati e bloccati alla radice;
  - Tentativi di risoluzione di entità esterne (XXE - `SYSTEM "file:///..."` o `SYSTEM "http://..."`) vengono bloccati prima di qualunque risoluzione I/O;
  - Nessun accesso a risorse locali riservate né connessioni alla rete.

---

## 18. Parser Scelto
- **DECISIONE ARCHITETTURALE**: Si è adottato `defusedxml.ElementTree.iterparse(source, events=("end",), forbid_dtd=True)`. Rappresenta la soluzione più sicura, minimale e performante, ufficialmente raccomandata dalla documentazione del linguaggio Python per il parsing sicuro di XML non fidati.

---

## 19. Dipendenza Runtime XML
- **FATTO VERIFICATO**: Aggiunta al manifesto `pyproject.toml` sotto la sezione `dependencies`:
  ```toml
  # cellebrite_xml.py: parsing XML sicuro e streaming contro DTD, XXE, entity expansion
  "defusedxml>=0.7.1",
  ```
  La libreria è installata in `.venv` (versione `0.7.1`, pura Python, 25 KB, zero dipendenze transitive).

---

## 20. Risultato Gate B
- **FATTO VERIFICATO**: Esecuzione completa di pytest post-parser e test di sicurezza:
  - Esito: **433 passed, 1 skipped in 18.87s, 0 failed, 0 errors, 0 warnings**.

---

## 21. File Creati
- **FATTO VERIFICATO**:
  - [importer/cellebrite_xml.py](file:///c:/Users/lucas/Desktop/Tesi/importer/cellebrite_xml.py) — classe `CellebriteXmlImporter`;
  - [scripts/__init__.py](file:///c:/Users/lucas/Desktop/Tesi/scripts/__init__.py) — pacchetto tooling scripts;
  - [tests/unit/test_sanitize_dataset.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_sanitize_dataset.py) — 10 test di sicurezza sanitizer;
  - [tests/unit/test_cellebrite_xml_importer.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_cellebrite_xml_importer.py) — 47 test unitari XML;
  - [tests/integration/test_cellebrite_xml_integration.py](file:///c:/Users/lucas/Desktop/Tesi/tests/integration/test_cellebrite_xml_integration.py) — 26 test di integrazione e discriminazione a 5 vie.

---

## 22. File Modificati
- **FATTO VERIFICATO**:
  - [importer/cellebrite_json.py](file:///c:/Users/lucas/Desktop/Tesi/importer/cellebrite_json.py) — eccezioni specifiche in `can_import()`;
  - [importer/__init__.py](file:///c:/Users/lucas/Desktop/Tesi/importer/__init__.py) — esportazione pubblica di `CellebriteXmlImporter`;
  - [pyproject.toml](file:///c:/Users/lucas/Desktop/Tesi/pyproject.toml) — dichiarazione dipendenza `defusedxml>=0.7.1`;
  - [docs/importer-design.md](file:///c:/Users/lucas/Desktop/Tesi/docs/importer-design.md) — Sezione 10 dedicata a XML, allineamento claim memoria streaming e aggiornamento indici;
  - [tests/unit/test_cellebrite_json_importer.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_cellebrite_json_importer.py) — test di propagazione bug programmatici in `can_import()`.

---

## 23. Strategia `source_record_id`
- **DECISIONE ARCHITETTURALE**:
  1. **Priorità 1**: Identificatore nativo esplicito presente nell'attributo `id` del tag `<Message>` (es. `"0"`, `"1"`, ... `"49"`).
  2. **Priorità 2 (fallback strutturale)**: Qualora l'attributo `id` sia assente o vuoto, fallback deterministico basato sull'indice di sequenza dell'elemento: `f"message:{idx}"` (0-indexed).
  Nessun hash semantico, nessun mapping con identificatori di altre sorgenti.

---

## 24. Rappresentazione `raw_fields`
- **FATTO VERIFICATO**: `raw_fields` è un dizionario fedele che preserva:
  - `@attributes`: dizionario degli attributi XML originari;
  - `id`: valore testuale dell'attributo `id`;
  - `Timestamp`: stringa ISO 8601 originaria;
  - `Sender`: stringa originaria;
  - `Body`: testo grezzo o `None` se elemento auto-chiuso `<Body />`;
  - `Deleted`: stringa originaria `"false"` / `"true"`;
  - Tutte le strutture annidate sono congelate ricorsivamente tramite `_freeze_structural()`.

---

## 25. `record_type`
- **FATTO VERIFICATO**: Rigidantente `"message"` per ciascun elemento `<Message>`.

---

## 26. `can_import()` Strutturale
- **FATTO VERIFICATO**: Ispezione in streaming $O(1)$ limitata all'intestazione del documento:
  - Verifica che la radice sia `<DumpFile type="UFDR">` e che esista il contenitore `<InstantMessages>` o l'elemento `<Message>`;
  - Indipendente dal nome o dall'estensione (riconosce file `.txt` o privi di estensione);
  - Rifiuta SQLite, CSV, JSON, file binari, XML generici e XML malformati;
  - Cattura esclusivamente `(OSError, dET.ParseError, DefusedXmlException, UnicodeDecodeError)`.

---

## 27. Streaming
- **FATTO VERIFICATO**: Il metodo `import_records()` è un generatore Python puro (`Iterator[RawRecord]`):
  - Il chiamante riceve il primo record immediatamente senza attendere la lettura dell'intero report;
  - `isgenerator(gen)` è confermato dai test.

---

## 28. Strategia Rilascio Memoria
- **DECISIONE ARCHITETTURALE**: L'iterazione si aggancia all'evento `"end"` di `defusedxml.ElementTree.iterparse`.
  - Non appena un elemento `<Message>` è completo, i dati vengono estratti nel `RawRecord`;
  - Viene emesso il record via `yield`;
  - Viene invocato immediatamente `elem.clear()` per cancellare riferimenti ai nodi figli, testo e attributi dell'elemento già processato;
  - La memoria occupata rimane circoscritta all'elemento corrente più i buffer del parser.

---

## 29. Tipi XML Preservati
- **FATTO VERIFICATO**: Preservazione deliberata dei tipi sorgente (nessuna coercizione):
  - `<Deleted>false</Deleted>` rimane stringa `"false"` (non `False` booleano);
  - `<Timestamp>` rimane stringa ISO 8601;
  - Matrice comparativa verificata dal test unitario `TestCrossSourceDeletedTypeComparison`:
    - CSV `Deleted`: stringa `"False"` / `"True"`;
    - JSON `metadata.deleted`: booleano nativo `False` / `True`;
    - XML `Deleted`: stringa `"false"` / `"true"`.

---

## 30. `media_reference`
- **FATTO VERIFICATO**: Nel campione reale `report.xml`, i messaggi non contengono nodi o attributi relativi ad allegati: per tutti i 50 messaggi `media_reference` è `None`.
- **DECISIONE ARCHITETTURALE**: L'importer include la logica per intercettare eventuali tag `Attachment`, `Media`, `MediaPath` o attributi `media_path` nel caso di future estrazioni UFDR arricchite, senza comprimere strutture complesse né alterare il contratto di `RawRecord`.

---

## 31. Error Handling
- **DECISIONE ARCHITETTURALE**:
  - Source-level: `FileNotFoundError` per percorsi inesistenti; `ValueError` per sorgenti non UFDR, malformate o con DTD/XXE;
  - Record-level: nessun silent skip, nessun `try...except Exception: pass`. Errori di corruzione o bug programmatici si propagano immediatamente per garantire l'integrità forense della catena di custodia.

---

## 32. Gestione Namespace
- **FATTO VERIFICATO**: Verificato sia su file privi di namespace che su file namespaced (es. `xmlns="http://cellebrite.com/ufdr"`, test `test_namespaced_xml_parsed_correctly`).

---

## 33. Matrice di Discriminazione Cross-Importer a 5 Vie
- **FATTO VERIFICATO**: Verificata la completa ortogonalità tra tutti i 5 importer:
  | Importer | `msgstore.db` | `wa.db` | `messages.csv` | `messages.json` | `report.xml` |
  |---|---|---|---|---|---|
  | `WhatsAppMsgstoreImporter` | **True** | False | False | False | False |
  | `WhatsAppWaDbImporter` | False | **True** | False | False | False |
  | `CellebriteCsvImporter` | False | False | **True** | False | False |
  | `CellebriteJsonImporter` | False | False | False | **True** | False |
  | `CellebriteXmlImporter` | False | False | False | False | **True** |

---

## 34. Questioni Aperte: `ChatId`
- **PROBLEMA APERTO**: In `report.xml` non esiste il tag o attributo `ChatId`. L'associazione del messaggio a una conversazione o a una chat canonica non viene inventata e spetterà interamente alla fase di entity resolution a valle.

---

## 35. Questioni Aperte: `group_participant_A`
- **PROBLEMA APERTO**: La stringa `group_participant_A` è preservata inalterata nel campo `Sender` per tutti i messaggi pertinenti, senza tentativi di risoluzione con contatti reali o numeri telefonici.

---

## 36. Questioni Aperte: Timezone
- **PROBLEMA APERTO**: Le stringhe temporali contengono l'offset `+00:00`. Vengono conservate esattamente come fornite dalla sorgente senza conversione a timestamp Unix o alterazione del fuso orario.

---

## 37. Questioni Aperte: Semantica `deleted`
- **PROBLEMA APERTO**: Preservato il valore sorgente (`"false"` su 49 messaggi, `"true"` sul messaggio id="35"). La normalizzazione verso un booleano canonico o stato unificato appartiene al layer di normalizzazione semantica.

---

## 38. Message Types
- **PROBLEMA APERTO**: In `report.xml` non è presente un tag esplicito `Type` (a differenza di CSV o JSON). I messaggi con `<Body />` vuoto non vengono arbitrariamente classificati come media o eliminati; la loro interpretazione semantica è rimandata.

---

## 39. Test Aggiunti nella Fase Corrente
- **FATTO VERIFICATO**:
  - `tests/unit/test_sanitize_dataset.py`: 10 test di sicurezza per il sanitizer;
  - `tests/unit/test_cellebrite_json_importer.py`: 1 test di non-mascheramento errori in `can_import`;
  - `tests/unit/test_cellebrite_xml_importer.py`: 47 test unitari per `CellebriteXmlImporter` e tipi cross-source;
  - `tests/integration/test_cellebrite_xml_integration.py`: 26 test di integrazione con `report.xml` reale e discriminazione a 5 vie.
  - Totale nuovi test: **84 test**.

---

## 40. Risultato Finale Pytest
- **FATTO VERIFICATO**: L'intera suite di test del repository è stata eseguita con esito verde e privo di errori.

---

## 41. Collected
- **FATTO VERIFICATO**: **461 test** raccolti.

---

## 42. Passed
- **FATTO VERIFICATO**: **460 test** superati.

---

## 43. Failed
- **FATTO VERIFICATO**: **0** test falliti.

---

## 44. Errors
- **FATTO VERIFICATO**: **0** errori.

---

## 45. Skipped
- **FATTO VERIFICATO**: **1** test saltato (`test_symlink_pointing_outside_rejected` in `test_sanitize_dataset.py`, a causa dei privilegi di creazione symlink non disponibili di default per utenti non-admin su Windows; la protezione su percorsi reali e traversal `..` è invece passata regolarmente).

---

## 46. Warnings
- **FATTO VERIFICATO**: **0** warning.

---

## 47. Durata
- **FATTO VERIFICATO**: **41.71 secondi**.

---

## 48. Problemi Aperti
- **PROBLEMA APERTO**:
  1. `DeviceInfo` (Samsung Galaxy S21, Android 13, IMEI `000000000000001`): presente in `report.xml`, è stato correttamente ignorato come record di messaggio. Dovrà essere gestito qualora si desideri un layer di estrazione di metadati del dispositivo/caso forense.
  2. Assenza di identificatori di chat in `report.xml`: la ricostruzione dei thread di conversazione per i dati UFDR XML richiederà euristiche basate sul mittente/destinatario durante la fase di normalizzazione ed entity resolution.

---

## 49. Rischi Tecnici
- **VALUTAZIONE**:
  1. *File XML di dimensioni eterogenee*: la combinazione di `defusedxml.ElementTree.iterparse` e `elem.clear()` protegge la memoria da saturazione anche su estrazioni XML multi-megabyte.
  2. *Formati proprietari Cellebrite UFDR*: UFDR reali possono presentare namespace complessi o tag annidati aggiuntivi (es. geolocalizzazione, allegati multipart); la funzione ricorsiva `_xml_element_to_dict` è già predisposta per mappare qualsiasi sottostruttura senza perdita di informazione.

---

## 50. Technical Debt Realmente Osservato
- **VALUTAZIONE**: Nessun technical debt critico. Il layer di ingestion è ora completo, source-faithful, read-only, streaming e dotato di deep immutability per tutte le 5 sorgenti del progetto. Non si dichiarano linting o type checking automatici come eseguiti in quanto non facenti parte della suite pytest corrente.

---

## 51. Prossimo Passo Suggerito
- **DECISIONE ARCHITETTURALE (NON IMPLEMENTATA)**:
  - Completata la fase di ingestion (`DATI ORIGINALI → IMPORTER → RawRecord`), il prossimo passo architetturale consiste nell'avvio della fase di **VALIDAZIONE e NORMALIZZAZIONE**:
    - Definizione del modulo di validazione strutturale dei `RawRecord`;
    - Progettazione delle regole di normalizzazione deterministica (timestamp UTC canonico, normalizzazione numeri in formato E.164, mappatura tipi messaggio);
    - Rimanendo rigorosamente separati da `UnifiedMessage` ed Entity Resolution fino al superamento dei relativi gate di test.
