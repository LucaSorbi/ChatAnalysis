"""
ai/translation.py
-----------------
Modulo per la strategia multilingue TRANSLATE_FIRST e la traduzione strutturata delle evidenze.

Principi architetturali:
1. TRASFORMAZIONE PURAMENTE DERIVATA:
   La traduzione NON muta in alcun modo TextEvidenceSection o UnifiedMessage.
   Produce un EvidenceTranslationResult separato e tipizzato.
2. CITAZIONI SEMPRE RIFERITE ALLE EVIDENZE ORIGINALI:
   Anche quando l'analisi LLM utilizza i testi tradotti per comprendere il contesto,
   gli evidence_id citati nei risultati finali continuano a riferirsi alle evidenze originarie.
3. OUTPUT STRUTTURATO E VALIDATO:
   La risposta JSON della traduzione viene validata: nessuna omissione, nessuna duplicazione,
   nessun ID inventato, preservazione della lingua originale e rigetto di traduzioni vuote indebite.
4. PROMPT VERSIONING E MODEL INTEGRITY:
   Registrato con prompt_version 'evidence_translation_v1'. Supporta model_id esplicito con verifica mismatch.
"""
from __future__ import annotations

from typing import Any

from ai.backend import (
    AiModelMismatchError,
    BaseLocalLlmClient,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    EvidenceTranslationItem,
    EvidenceTranslationResult,
)
from ai.serializer import (
    SYSTEM_SECURITY_DIRECTIVE,
    serialize_document_for_llm,
)
from ai.structured import (
    TRANSLATION_SCHEMA,
    validate_translation_payload,
)

PROMPT_VERSION_TRANSLATION = "evidence_translation_v1"


class EvidenceTranslator:
    """
    Traduttore strutturato per eseguire la pre-traduzione delle sezioni di evidenza verso una lingua target.
    """

    def __init__(
        self,
        client: BaseLocalLlmClient,
        default_target_language: str = "it",
        default_model_id: str | None = None,
    ) -> None:
        self.client = client
        self.default_target_language = default_target_language
        self.default_model_id = default_model_id

    def translate_document(
        self,
        document: ConversationEvidenceDocument,
        model_id: str | None = None,
        target_language: str | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> EvidenceTranslationResult:
        """
        Traduce tutte le sezioni testuali del documento nella lingua target specificata (default italiano).
        """
        target_model = model_id or self.default_model_id
        target = target_language or self.default_target_language
        sections = document.all_evidence_sections
        sections_map = {s.evidence_id: s for s in sections}

        if not sections_map:
            return EvidenceTranslationResult(
                translations=(),
                provenance_document_id=document.document_id,
                target_language=target,
                metadata={
                    "empty_document": True,
                    "prompt_version": PROMPT_VERSION_TRANSLATION,
                    "model": target_model or "none",
                },
            )

        system_content = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: FORENSIC EVIDENCE FAITHFUL TRANSLATION (Version: {PROMPT_VERSION_TRANSLATION})
Translate each evidence section into target language: '{target}'.

RULES:
1. Translate faithfully preserving forensic precision, tone, and factual entities (names, numbers, addresses).
2. If a section is ALREADY in the target language ('{target}'), keep it unchanged or minimally standardized.
3. DO NOT omit any evidence_id.
4. DO NOT invent or fabricate any evidence_id.
5. Return structured JSON with 'translations' array matching each evidence_id exactly once.
"""
        user_content = serialize_document_for_llm(
            document,
            strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        response = self.client.chat_completion(
            messages=messages,
            model_id=target_model,
            schema=TRANSLATION_SCHEMA,
            temperature=temperature,
            seed=seed,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )

        if target_model and response.model != target_model:
            raise AiModelMismatchError(
                f"Model mismatch in Translation: richiesto model_id '{target_model}', "
                f"ma il backend ha restituito '{response.model}'."
            )

        translations = validate_translation_payload(
            raw_text=response.content,
            expected_sections=sections_map,
            target_language=target,
        )

        metadata: dict[str, Any] = {
            "prompt_version": PROMPT_VERSION_TRANSLATION,
            "target_language": target,
            "model": response.model,
            "latency_seconds": response.latency_seconds,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
        }

        return EvidenceTranslationResult(
            translations=translations,
            provenance_document_id=document.document_id,
            target_language=target,
            metadata=metadata,
        )
