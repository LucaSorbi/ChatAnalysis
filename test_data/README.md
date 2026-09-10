# Dataset di Test e Sviluppo (`test_data/`)

Questo archivio contiene **esclusivamente dati sintetici** appositamente generati e sanitizzati per finalità di sviluppo, test unitari, test di integrazione e stesura della tesi accademica.

## Avvertenze e Conformità Forense

1. **Dataset Interamente Sintetico**: Tutti i database SQLite (`whatsapp_export/msgstore.db`, `whatsapp_export/wa.db`), le esportazioni Cellebrite (`cellebrite_export/messages.csv`, `cellebrite_export/messages.json`, `cellebrite_export/report.xml`) e i file multimediali associati (`WhatsApp Audio/`, `WhatsApp Images/`, `WhatsApp Video/`) sono fittizi e privi di qualsiasi informazione reale.
2. **Nessuna Acquisizione Forense Reale**: Il dataset non deriva da alcuna estrazione o repertamento reale, non contiene dati personali di individui viventi o deceduti, né comunicazioni protette da riservatezza o segreto istruttorio.
3. **Divieto Assoluto di Dati Reali**: È fatto divieto assoluto di sostituire i file sintetici con copie di database o estrazioni forensi reali all'interno del repository Git.
4. **Destinazione d'Uso**: Esclusivamente ricerca accademica, sviluppo software e testing automatico.

## Convenzioni di Sanitizzazione Adottate

A seguito del Privacy Hardening (Fase A), per rendere immediatamente e inequivocabilmente evidente la natura sintetica dei dati anche a verifiche automatizzate o audit di terzi, sono state applicate le seguenti convenzioni:

- **Nomi e Display Name**: Sostituiti con identificatori espliciti `Contatto_001`, `Contatto_002`, `Contatto_003`, `Contatto_004`.
- **Numerazioni Telefoniche**: Sostituite con sequenze artificiali basate su pattern di zeri:
  - `+39 000 0000001` (era `+39 333 1234567`)
  - `+39 000 0000002` (era `+39 347 9876543`)
  - `+39 000 0000003` (era `+39 320 5554433`)
  - `+1 000 0000004` (era `+1 212 5550101`)
- **JID WhatsApp**:
  - Contatti: `+390000000001@s.whatsapp.net`, `+390000000002@s.whatsapp.net`, `+390000000003@s.whatsapp.net`, `+10000000004@s.whatsapp.net`
  - Gruppo: `00000000001-0000000000@g.us`
- **Soggetto Gruppo**: `Gruppo_Sintetico_01` (era `Amici del gruppo`).
- **Alias Gruppo**: `group_participant_A` (invariato, già esplicitamente sintetico).
- **Testo Conversazionale**: Frasi colloquiali o realistiche sostituite con stringhe palesemente artificiali numerate (`Messaggio di test sintetico 01`, ..., `Messaggio di test sintetico 18`, `Messaggio sintetico con emoji 💊`), preservando stringhe vuote, emoji e URL sintetici di test (`https://example.org/synthetic-test-group`).
- **Identificatori Hardware**: IMEI in report XML impostato a sequenza artificiale `000000000000001`.
- **Email**: Nessun indirizzo email reale è presente nel dataset.
