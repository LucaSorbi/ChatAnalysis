# Verifica di consistenza semantica indipendente (Discovery → Detection associata)

Guida operativa e scientifica. Livello di verifica del codice: test sintetici (MOCK, client HTTP finto). Nessun dato reale.

## 1. Qual è il pulsante giusto

Nella scheda **Topic Discovery**, ogni topic scoperto ha il pulsante **«🔬 Verifica con Topic Detection»**.
È l'UNICO percorso che crea una verifica *associata*: la Detection riceve **etichetta + descrizione** del topic
validato e **nessuna** evidenza della Discovery (né ID, né suggerimenti). Solo DOPO la risposta del modello il
confronto usa gli evidence ID della Discovery.

La **Detection manuale** (Area 1, «Esegui Topic Detection») è un'altra cosa: nasce da testo digitato, è `MANUAL_DETECTION`,
non viene mai collegata a una Discovery per coincidenza di etichetta e il suo export storico resta invariato
(`consistency_association_flag = NO_MATCH` significa solo «nessun ConsistencyResult associato in sessione»).

## 2. Come leggere l'esito

| Esito | Significato | Cosa NON significa |
|---|---|---|
| `CONSISTENT` | Detection `PRESENT` con almeno un'evidenza in comune con la Discovery | Non è prova di verità: è accordo fra due inferenze dello stesso modello |
| `PARTIALLY_CONSISTENT` | Detection `PRESENT` ma su evidenze diverse (overlap vuoto) | Non è un errore |
| `DISCREPANCY_ABSENT` | La Discovery ha identificato il topic; la Detection indipendente non lo ha rilevato | Non è un errore di esecuzione né una prova che l'argomento sia assente |
| `DISCREPANCY_UNCERTAIN` | La Detection è rimasta incerta | Giudizio limitato, non una conferma |

`NO_MATCH` (flag di associazione nell'export della Detection manuale) **non** è `DISCREPANCY_ABSENT`: il primo dice che
nessun risultato di verifica è collegato a quella Detection, il secondo è un esito del confronto fra due inferenze.
Un errore del modello/HTTP è un terzo caso, distinto, e non produce alcun esito di consistenza.

## 3. Esportazione

Nel riquadro «Risultato Verifica Indipendente» compare **«⬇️ Esporta verifica JSON (SAFE_REDACTED)»** per tutti e
quattro gli esiti, senza passare dall'Area 1. Il rapporto è autonomo (`report_type = SEMANTIC_CONSISTENCY_VERIFICATION`,
`verification_contract_version = semantic_verification_export_v1`), è costruito in `analysis/export.py`
(`build_semantic_verification_export_report`) dagli oggetti autorevoli della run, validato prima del download, e non
invoca mai il modello. Contiene: identità e timestamp delle due run, link per identificativi espliciti
(`EXACT_UNIQUE_MATCH`), documento pseudonimizzato con fingerprint dello snapshot e conteggi per tipo di sezione
(originale/STT/OCR), query (`LABEL_AND_DESCRIPTION`, presenza della descrizione, nessuna evidenza di Discovery
inviata al modello), completezza e copertura di Discovery e Detection, origine d'esecuzione della Detection
(`COMPUTED_THIS_RUN`, `RESTORED_FROM_CHECKPOINT`, `MIXED`, `UNDETERMINED`), decisione ed evidenze di entrambe le
inferenze con overlap (anche vuoto), esito del comparatore, limiti scientifici e controlli minimi di audit.
In SAFE_REDACTED label, descrizione e motivazioni sono oscurate; mai testo di messaggi, trascrizioni, OCR, percorsi.
Il nome file non contiene testo del topic né nomi sorgente.

Se il rapporto non è dimostrabile (sessione persa, run non trovata, collegamento incoerente, controllo privacy
fallito) l'esportazione è bloccata con un messaggio comprensibile e il risultato di sessione NON viene cancellato.

## 4. Identità e snapshot

* Il topic è identificato da run di Discovery + contenuto validato (label, descrizione, evidence ID), mai dalla sola label.
* La Discovery registra la fingerprint del documento analizzato (testo, ordine, evidence ID e sezioni STT/OCR/Vision).
  Se il documento attivo è diverso (reimport, OCR/STT rieseguiti) la verifica è **bloccata prima di ogni chiamata al
  modello**. Per run storiche senza fingerprint l'esito è `UNVERIFIABLE`: la verifica è consentita ma l'equivalenza non
  è dimostrata e il rapporto lo dichiara (`DOCUMENT_SNAPSHOT_EQUIVALENCE_NOT_PROVEN`).
* Discovery `PARTIAL`: verificabile (decisione di prodotto: il topic esiste ed è stato validato) ma sempre segnalata,
  con copertura x/y nella UI e nel rapporto (`DISCOVERY_SOURCE_NOT_COMPLETE`); la verifica non estende la copertura.

## 5. Checkpoint e indipendenza

Una Detection identica a una già calcolata viene ripristinata da checkpoint senza richieste al modello. La UI e il
rapporto lo dichiarano (`RESTORED_FROM_CHECKPOINT`, `DETECTION_NOT_FULLY_COMPUTED_THIS_RUN`): il risultato scientifico
è lo stesso, ma NON è una nuova inferenza. L'attestazione delle Detection resta `UNATTESTED` (limite noto, dichiarato,
non nascosto).

## 6. Durata e limiti

* I risultati di verifica vivono nella sessione Streamlit (nessun nuovo database): vanno esportati prima di chiudere/ricaricare.
* L'accordo fra inferenze dello stesso modello non è prova di verità; ABSENT non prova l'assenza.
* Nessun criterio di accettazione si basa su «il modello restituisce CONSISTENT».

## 7. Collaudo sintetico (Windows, PowerShell)

```powershell
cd <CARTELLA_DI_TEST>
.\.venv313\Scripts\Activate.ps1
python -m pytest tests/unit/test_semantic_verification_report.py tests/unit/test_consistency_verification_audit.py -q -p no:cacheprovider
python -m pytest tests/unit/test_apptest_flows.py -q -p no:cacheprovider
```

Poi, in interfaccia, con il dataset sintetico di demo: Discovery → «Verifica con Topic Detection» → verificare che
nel riquadro compaiano badge, esito, origine d'esecuzione e il pulsante di esportazione; scaricare il JSON e controllare
`report_type`, `comparison.association.status = EXACT_UNIQUE_MATCH`, `detection.query.query_mode = LABEL_AND_DESCRIPTION`,
`detection.query.discovery_evidence_ids_supplied_to_model = false`, assenza di testo della conversazione.
