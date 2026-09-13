"""
tests/unit/test_search_normalization.py
---------------------------------------
Test unitari per la normalizzazione derivata e la tokenizzazione Unicode:
- Unicode NFKC + casefold
- Preservazione rigorosa degli accenti (nessuna rimozione automatica: caffè != caffe)
- Case-insensitivity corretta su tutti i caratteri Unicode
- Nessuno stemming / lemmatizzazione
- Gestione di punteggiatura, newline e caratteri speciali
- Tokenizzazione whole-term Unicode: distinzione tra token interi e sottostringhe parziali
"""
import pytest

from search.normalization import (
    normalize_for_search,
    tokenize_query_terms,
    tokenize_terms,
)


@pytest.mark.unit
class TestSearchNormalization:

    def test_casefold_ascii_and_unicode(self):
        assert normalize_for_search("ROMA") == "roma"
        assert normalize_for_search("Roma Capitale") == "roma capitale"
        assert normalize_for_search("STRASSE") == "strasse"
        # German sharp s (casefold)
        assert normalize_for_search("Straße") == "strasse"

    def test_accent_preservation_italian(self):
        # Gli accenti devono essere rigorosamente conservati
        assert normalize_for_search("caffè") == "caffè"
        assert normalize_for_search("CAFFÈ") == "caffè"
        assert normalize_for_search("perché") == "perché"
        assert normalize_for_search("PERCHÉ") == "perché"
        assert normalize_for_search("città") == "città"
        assert normalize_for_search("CITTÀ") == "città"
        assert normalize_for_search("caffè") != "caffe"

    def test_accent_preservation_spanish_and_others(self):
        # Spagnolo
        assert normalize_for_search("mañana") == "mañana"
        assert normalize_for_search("MAÑANA") == "mañana"
        assert normalize_for_search("señor") == "señor"
        assert normalize_for_search("SEÑOR") == "señor"
        # Francese / Portoghese
        assert normalize_for_search("ça va") == "ça va"
        assert normalize_for_search("ÇÀ VA") == "çà va"
        # Tedesco
        assert normalize_for_search("München") == "münchen"
        assert normalize_for_search("MÜNCHEN") == "münchen"

    def test_nfkc_canonical_equivalence(self):
        composed = "café"
        decomposed = "cafe\u0301"
        assert normalize_for_search(composed) == normalize_for_search(decomposed)

    def test_punctuation_and_newlines_retained_in_normalized_text(self):
        raw = "Prima riga.\nSeconda riga, con (parentesi) e #hashtag!"
        norm = normalize_for_search(raw)
        assert "\n" in norm
        assert "." in norm
        assert "," in norm
        assert "(" in norm
        assert "#" in norm

    # --- Tokenization & Whole-Term Semantics (Point E) ---

    def test_tokenize_terms_extracts_whole_tokens(self):
        text = "La partita è terminata. C'è l'art. 5 del codice civile!"
        tokens = tokenize_terms(text)
        assert "partita" in tokens
        assert "art" in tokens
        assert "5" in tokens
        assert "è" in tokens
        assert "terminata" in tokens
        # 'art' non deve essere una sottostringa di 'partita': sono due token distinti
        assert "partita" != "art"

    def test_partial_word_false_positive_elimination(self):
        # Test specifico richiesto: 'art' vs 'partita'
        tokens_partita = tokenize_terms("Oggi c'è una grande partita di calcio.")
        assert "partita" in tokens_partita
        assert "art" not in tokens_partita

        tokens_art = tokenize_terms("In conformità con l'art. 21 della Costituzione.")
        assert "art" in tokens_art
        assert "21" in tokens_art

    def test_tokenize_terms_accents_and_multilingual(self):
        # Italiano con accenti
        it_tokens = tokenize_terms("Caffè, città, perché e longevità.")
        assert "caffè" in it_tokens
        assert "caffe" not in it_tokens
        assert "città" in it_tokens
        assert "perché" in it_tokens

        # Spagnolo
        es_tokens = tokenize_terms("¿Cuándo viene el señor Rodríguez por la mañana?")
        assert "cuándo" in es_tokens
        assert "señor" in es_tokens
        assert "rodríguez" in es_tokens
        assert "mañana" in es_tokens

        # Inglese
        en_tokens = tokenize_terms("First-class confidential agreement: signed on 2026/09/13.")
        assert "first" in en_tokens
        assert "class" in en_tokens
        assert "confidential" in en_tokens
        assert "agreement" in en_tokens
        assert "signed" in en_tokens
        assert "2026" in en_tokens

    def test_tokenize_terms_with_newlines_and_complex_punctuation(self):
        raw = "Line1: alpha, beta;\nLine2: [gamma] (delta)... \n\tLine3: 100% #omega!"
        tokens = tokenize_terms(raw)
        assert tokens == ("line1", "alpha", "beta", "line2", "gamma", "delta", "line3", "100", "omega")

    def test_tokenize_query_terms_alias(self):
        assert tokenize_query_terms("caffè e città") == ("caffè", "e", "città")
        assert tokenize_query_terms("") == ()
        assert tokenize_query_terms("   \t\n  ") == ()
