"""
ai/token_budget.py
------------------
Modulo per la stima conservativa del consumo di token, il calcolo del budget di contesto
e la prevenzione preventiva del context overflow per modelli linguistici locali (LM Studio).

Principi architetturali:
1. STIMA CONSERVATIVA DICHIARATA:
   In assenza di caricamento pesi/tokenizer BPE identico a runtime, il calcolo adotta una
   stima dichiaratamente conservativa e verificabile (rapporto caratteri/parole prudenziale per
   italiano, formati multilingue e serializzazione JSON).
   Nessun budget stimato viene presentato come esatto.
2. VERIFICA PROMPT + OUTPUT + MARGINE:
   Prima di ogni richiesta viene verificato che:
   estimated_prompt_tokens + reserved_output_tokens + safety_margin <= context_length
3. HEADROOM ESPLICITO PER RETRY:
   Calcola l'effettivo spazio residuo per evitare che retry a seguito di troncamenti
   saturino la finestra di contesto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Mapping, Sequence


DEFAULT_SAFETY_MARGIN_TOKENS: int = 256
DEFAULT_CHAT_TEMPLATE_OVERHEAD_TOKENS: int = 32
DEFAULT_MESSAGE_FRAME_OVERHEAD_TOKENS: int = 6


@dataclass(frozen=True)
class TokenBudgetPlan:
    """
    Rappresenta il piano di allocazione token per una richiesta LLM.
    """
    context_length: int
    estimated_input_tokens: int
    reserved_output_tokens: int
    safety_margin: int
    total_budget_needed: int
    available_headroom: int
    fits_in_context: bool
    is_exact_count: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_length": self.context_length,
            "estimated_input_tokens": self.estimated_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "safety_margin": self.safety_margin,
            "total_budget_needed": self.total_budget_needed,
            "available_headroom": self.available_headroom,
            "fits_in_context": self.fits_in_context,
            "is_exact_count": self.is_exact_count,
            "details": dict(self.details),
        }


def estimate_text_tokens(text: str) -> int:
    """
    Stima conservativa e verificabile del numero di token per un testo.
    Per testi in lingua italiana con punteggiatura, caratteri accentati e strutture JSON,
    adotta il massimo tra la stima a caratteri (~2.8 char/token) e la stima a parole (~1.35 tok/word),
    con un floor di sicurezza.
    """
    if not text:
        return 0
    char_len = len(text)
    word_count = len(text.split())
    # Stima prudenziale: 2.8 caratteri per token per testi IT/multilingue con escape JSON
    char_est = math.ceil(char_len / 2.8)
    word_est = math.ceil(word_count * 1.35)
    return max(char_est, word_est) + 4


def estimate_schema_tokens(schema: Mapping[str, Any] | None) -> int:
    """
    Stima i token occupati dallo schema JSON strutturato (grammatica / response_format).
    """
    if not schema:
        return 0
    raw_json = json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
    # Lo schema JSON viene processato dal grammar parser del backend
    return max(64, math.ceil(len(raw_json) / 3.0) + 16)


def estimate_request_input_tokens(
    messages: Sequence[Mapping[str, str]],
    schema: Mapping[str, Any] | None = None,
    chat_template_overhead: int = DEFAULT_CHAT_TEMPLATE_OVERHEAD_TOKENS,
) -> int:
    """
    Calcola la stima complessiva dei token di input per un payload di chat completion:
    - System prompt
    - User prompt (messaggi ed evidenze serializzate)
    - Overhead frame di ciascun messaggio
    - Overhead template di chat
    - Overhead schema JSON strutturato
    """
    total = chat_template_overhead
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")
        total += DEFAULT_MESSAGE_FRAME_OVERHEAD_TOKENS
        total += math.ceil(len(role) / 3.0)
        total += estimate_text_tokens(content)

    if schema:
        total += estimate_schema_tokens(schema)

    return total


def calculate_token_budget_plan(
    context_length: int,
    messages: Sequence[Mapping[str, str]],
    reserved_output_tokens: int,
    schema: Mapping[str, Any] | None = None,
    safety_margin: int = DEFAULT_SAFETY_MARGIN_TOKENS,
) -> TokenBudgetPlan:
    """
    Costruisce e verifica il piano del budget di token prima dell'invio della richiesta.
    """
    input_est = estimate_request_input_tokens(messages, schema=schema)
    total_needed = input_est + reserved_output_tokens + safety_margin
    headroom = max(0, context_length - input_est - safety_margin)
    fits = total_needed <= context_length

    details = {
        "estimated_prompt_tokens": input_est,
        "reserved_output_tokens": reserved_output_tokens,
        "safety_margin": safety_margin,
        "has_schema": schema is not None,
    }

    return TokenBudgetPlan(
        context_length=context_length,
        estimated_input_tokens=input_est,
        reserved_output_tokens=reserved_output_tokens,
        safety_margin=safety_margin,
        total_budget_needed=total_needed,
        available_headroom=headroom,
        fits_in_context=fits,
        is_exact_count=False,
        details=details,
    )


def calculate_dynamic_output_budget(
    context_length: int,
    estimated_prompt_tokens: int,
    desired_output_tokens: int,
    min_output_tokens: int = 512,
    safety_margin: int = DEFAULT_SAFETY_MARGIN_TOKENS,
    raise_on_insufficient: bool = False,
) -> int:
    """
    Calcola in modo sicuro il budget di output effettivo, vincolato all'occupazione
    reale del prompt e al margine di sicurezza.
    Garantisce tassativamente:
    1. Il valore restituito non supera MAI l'headroom disponibile (available_headroom);
    2. Se available_headroom < min_output_tokens, lo spazio residuo è insufficiente:
       restituisce 0 (valore non utilizzabile) oppure solleva AnalysisInputTooLargeError
       se richiesto da raise_on_insufficient;
    3. Quando lo spazio è sufficiente (available_headroom >= min_output_tokens),
       restituisce un valore nell'intervallo [min_output_tokens, available_headroom]
       adeguato a desired_output_tokens, senza mai eccedere l'headroom.
    """
    available_headroom = max(0, context_length - estimated_prompt_tokens - safety_margin)
    if available_headroom <= 0 or available_headroom < min_output_tokens:
        if raise_on_insufficient:
            from ai.backend import AnalysisInputTooLargeError
            raise AnalysisInputTooLargeError(
                f"Headroom disponibile ({available_headroom} token) inferiore al minimo "
                f"richiesto ({min_output_tokens} token) per context_length={context_length}."
            )
        return 0

    target = max(min_output_tokens, desired_output_tokens)
    return min(available_headroom, target)
