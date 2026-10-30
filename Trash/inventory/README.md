# Inventari SHA-256 — cosa provano e cosa NO

- `INVENTORY_BEFORE.csv`: tutti i 1001 file tracciati prima del riordino (stato `93c106d`), con SHA-256, dimensione e numero di riferimenti
  (codice/test/packaging; documenti).
- `INVENTORY_AFTER.csv`: file tracciati al commit `4e30039` (stato dopo il riordino). **Non include sé stesso** e **non viene
  aggiornato dai commit successivi**: è un'istantanea, non un registro vivo.

Limiti dell'autoverifica (da non presentare come garanzie):
1. Un file che elenca gli hash di un insieme non può provare la propria integrità: chi lo modifica può modificare anche gli hash.
   L'unica prova indipendente è git stesso: `git diff -M --name-status 93c106d 4e30039` mostra 57 voci `R100` (rinomina con contenuto identico).
2. `Trash/MANIFEST.csv` e questi inventari sono stati generati dallo stesso processo che ha eseguito gli spostamenti; i test in
   `tests/unit/test_trash_archive_integrity.py` rileggono i file reali e ricalcolano gli SHA-256, ma non sostituiscono la verifica con git.
3. Gli inventari non dicono nulla sulla *sensibilità* dei contenuti, né sui dati già presenti nella cronologia git.
4. I conteggi di riferimenti sono ricerche testuali per nome file: possono includere falsi positivi e mancare riferimenti costruiti dinamicamente.
