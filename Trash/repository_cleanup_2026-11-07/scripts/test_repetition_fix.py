import re

def detect_degenerate_repetition(text: str, threshold: int = 15) -> str | None:
    if not text:
        return None
    # 1. Carattere non-spazio singolo ripetuto
    m_char = re.search(r'([^\s])\1{' + str(threshold - 1) + r',}', text)
    if m_char:
        return f"Ripetizione ciclica anomala del carattere {repr(m_char.group(1))} ({len(m_char.group(0))} volte)"
    # Tabulazioni ripetute
    m_tab = re.search(r'(\t)\1{11,}', text)
    if m_tab:
        return f"Ripetizione ciclica di caratteri tabulari ({len(m_tab.group(0))} volte)"
    # 2. Pattern di 2-12 caratteri (inclusi spazi) ripetuto >= 6 volte consecutive
    m_pat = re.search(r'(.{2,12})\1{5,}', text)
    if m_pat:
        pat_repr = repr(m_pat.group(1))
        return f"Loop di attrattore su sequenza {pat_repr} ({len(m_pat.group(0)) // len(m_pat.group(1))} ripetizioni)"
    return None

sample = "dell'incontro invernese a destinazione a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a  a"
print("Result on sample:", detect_degenerate_repetition(sample))
normal = '{"topics": [{"label": "Attività sportiva", "short_description": "Discussione su corsa e orari", "evidence_ids": ["E001", "E002"]}]}'
print("Result on normal:", detect_degenerate_repetition(normal))
