"""
search/normalization.py
-----------------------
Funzioni di normalizzazione derivata deterministica e tokenizzazione Unicode
per il Search Layer.

Definizione formale di "Termine" (Term / Token):
Un termine è una sequenza contigua massimale di caratteri alfanumerici Unicode
(glifi alfabetici di qualsiasi sistema di scrittura con relativi segni diacritici/accenti,
combinati con cifre numeriche), delimitata da spaziature (spazi, tabulazioni, newline)
o segni di interpunzione e simboli speciali.

Principi architetturali:
1. NON-DESTRUCTIVE: Il testo originale della TextEvidenceSection NON viene mai alterato
   né sovrascritto. La normalizzazione produce unicamente una rappresentazione derivata
   in sola lettura per la comparazione.
2. POLICY ESPLICITA:
   - Unicode NFKC (Normal Form KC): compatibilità di caratteri mantenendo lettere e accenti.
   - casefold(): normalizzazione delle maiuscole/minuscole Unicode indipendente dal locale.
3. TOKENIZZAZIONE WHOLE-TERM (senza falsi positivi da sottostringa):
   - regex standard library r'\\w+' con flag re.UNICODE.
   - Ogni parola è isolata come token intero: ad esempio la query 'art' non matcha
     la parola 'partita', ma matcha 'art' in '(art. 5)' o 'art-10'.
4. PRESERVAZIONE FEDELE:
   - Nessuna rimozione automatica degli accenti ('caffè' != 'caffe', 'mañana' != 'manana').
   - Nessuno stemming (nessun troncamento morfologico).
   - Nessuna lemmatizzazione.
   - Nessun fuzzy matching.
"""
from __future__ import annotations

import re
import unicodedata

# Pattern Unicode per termini interi: sequenza contigua di caratteri alfanumerici
_UNICODE_TERM_PATTERN = re.compile(r"\w+", re.UNICODE)


def normalize_for_search(text: str) -> str:
    """
    Produce la rappresentazione derivata locale per la ricerca:
    Unicode NFKC + casefold().

    Parametri:
    ----------
    text : str
        Testo sorgente da normalizzare.

    Restituisce:
    ------------
    str
        Testo normalizzato derivato.
    """
    if not isinstance(text, str):
        return ""
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize_terms(text: str) -> tuple[str, ...]:
    """
    Estrae la sequenza deterministica di termini (token interi) Unicode dal testo.

    Processo:
    1. Normalizzazione Unicode NFKC + casefold().
    2. Identificazione delle sequenze massimali di caratteri alfanumerici Unicode (r'\\w+').
       Separa automaticamente su punteggiatura, parentesi, trattini, newline e spaziature.
    3. Restituisce la tupla immutabile dei termini estratti nel loro ordine naturale.

    Parametri:
    ----------
    text : str
        Testo sorgente da cui estrarre i termini.

    Restituisce:
    ------------
    tuple[str, ...]
        Tupla ordinata di termini interi estratti.
    """
    if not isinstance(text, str):
        return ()
    norm = normalize_for_search(text)
    tokens = _UNICODE_TERM_PATTERN.findall(norm)
    return tuple(tokens)


def tokenize_query_terms(query_text: str) -> tuple[str, ...]:
    """
    Estrae i singoli termini da una query per le modalità ALL_TERMS e ANY_TERM.
    Alias per tokenize_terms garantendo retrocompatibilità e coerenza semantica.
    """
    return tokenize_terms(query_text)
