"""
ai/topics.py
------------
Motori per la rilevazione mirata di topic (Topic Detection) e la scoperta aperta di argomenti (Topic Discovery).

Principi architetturali (Fasi A, B, I, J, L, M):
1. TOPIC DETECTION SPECIFICO:
   Verifica rigorosa con verdetto tri-stato (PRESENT, ABSENT, UNCERTAIN).
   Citazioni vincolanti e verificate: per PRESENT è obbligatoria almeno un'evidenza valida; per ABSENT è rigorosamente vuota.
2. OPEN TOPIC DISCOVERY:
   Estrazione di temi emergenti con cap massimo configurabile (default 10).
   Nessun troncamento silente: se il modello restituisce più di max_topics, viene sollevato errore di validazione.
3. CONTRATTI DI STRATEGIA LINGUISTICA (TRANSLATE_FIRST vs DIRECT_MULTILINGUAL):
   Sotto TRANSLATE_FIRST è obbligatorio un EvidenceTranslationResult valido; sotto DIRECT_MULTILINGUAL è vietato.
4. MODEL INTEGRITY (FASE A):
   Se specificato un model_id atteso, viene verificata la corrispondenza con response.model (solleva AiModelMismatchError).
"""
from __future__ import annotations

from typing import Any, Mapping

from ai.backend import (
    AiModelMismatchError,
    BaseLlmClient,
)
from ai.chunking import verify_translated_document_size
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    EvidenceTranslationResult,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.serializer import (
    SYSTEM_SECURITY_DIRECTIVE,
    serialize_document_for_llm,
)
from ai.structured import (
    TOPIC_DETECTION_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA,
    validate_topic_detection_payload,
    validate_topic_discovery_payload,
)

PROMPT_VERSION_DETECTION = "topic_detection_v1"
PROMPT_VERSION_DISCOVERY = "topic_discovery_v1"


class TopicDetectionAnalyzer:
    """
    Analizzatore per la verifica mirata di un singolo topic su un ConversationEvidenceDocument.
    """

    def __init__(self, client: BaseLlmClient, default_model_id: str | None = None) -> None:
        self.client = client
        self.default_model_id = default_model_id

    def detect_topic(
        self,
        document: ConversationEvidenceDocument,
        topic: TopicQuery,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        translation: EvidenceTranslationResult | None = None,
        model_id: str | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> TopicDetectionResult:
        """
        Esegue la detection del topic specificato.
        """
        target_model = model_id or self.default_model_id
        valid_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}

        # Gestione documenti privi di evidenza testuale
        if not valid_evidence_ids:
            return TopicDetectionResult(
                topic=topic,
                decision=TopicDecision.ABSENT,
                evidence_ids=(),
                rationale="Nessuna evidenza testuale disponibile nel documento di conversazione.",
                provenance_document_id=document.document_id,
                metadata={
                    "empty_document": True,
                    "prompt_version": PROMPT_VERSION_DETECTION,
                    "strategy": strategy.value,
                    "model": target_model or "none",
                },
            )

        # Costruzione del system prompt con direttiva anti-injection e specifiche di task
        system_content = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: SPECIFIC TOPIC DETECTION (Version: {PROMPT_VERSION_DETECTION})
You must determine if the following topic is discussed or present in the forensic evidence:
TOPIC ID: {topic.topic_id}
LABEL: {topic.label}
DESCRIPTION: {topic.description}

OUTPUT RULES:
1. Return decision 'PRESENT' if and only if the topic is clearly discussed or evidenced in the conversation.
   You MUST cite at least one exact evidence_id in 'evidence_ids'.
2. Return decision 'ABSENT' if the topic is NOT present. 'evidence_ids' MUST be an empty array [].
3. Return decision 'UNCERTAIN' if the evidence is ambiguous, partial, or insufficient to decide. You may cite ambiguous evidence_ids.
4. CITE ONLY EXACT evidence_ids that are present in the evidence block. DO NOT fabricate or invent IDs.
5. Provide a short, objective rationale explaining the decision in Italian. DO NOT provide lengthy chain-of-thought.
"""

        if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
            system_content += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Understand the original language but respond with rationale in Italian.\n"
        elif strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        user_content = serialize_document_for_llm(document, strategy=strategy, translation=translation)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        response = self.client.chat_completion(
            messages=messages,
            model_id=target_model,
            schema=TOPIC_DETECTION_SCHEMA,
            temperature=temperature,
            seed=seed,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )

        # Verifica Model Mismatch
        if target_model and response.model != target_model:
            raise AiModelMismatchError(
                f"Model mismatch in Topic Detection: richiesto model_id '{target_model}', "
                f"ma il backend ha restituito '{response.model}'."
            )

        decision, evidence_ids, rationale = validate_topic_detection_payload(
            raw_text=response.content,
            valid_evidence_ids=valid_evidence_ids,
        )

        metadata: dict[str, Any] = {
            "prompt_version": PROMPT_VERSION_DETECTION,
            "strategy": strategy.value,
            "model": response.model,
            "latency_seconds": response.latency_seconds,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
        }

        return TopicDetectionResult(
            topic=topic,
            decision=decision,
            evidence_ids=evidence_ids,
            rationale=rationale,
            provenance_document_id=document.document_id,
            metadata=metadata,
        )


class TopicDiscoveryAnalyzer:
    """
    Analizzatore per l'Open Topic Discovery su un ConversationEvidenceDocument.
    """

    def __init__(self, client: BaseLlmClient, default_model_id: str | None = None) -> None:
        self.client = client
        self.default_model_id = default_model_id

    def discover_topics(
        self,
        document: ConversationEvidenceDocument,
        strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
        translation: EvidenceTranslationResult | None = None,
        model_id: str | None = None,
        max_topics: int = 10,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> TopicDiscoveryResult:
        """
        Esegue la scoperta non supervisionata di temi emergenti nel documento.
        """
        target_model = model_id or self.default_model_id
        valid_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}

        if not valid_evidence_ids:
            return TopicDiscoveryResult(
                topics=(),
                provenance_document_id=document.document_id,
                metadata={
                    "empty_document": True,
                    "prompt_version": PROMPT_VERSION_DISCOVERY,
                    "strategy": strategy.value,
                    "model": target_model or "none",
                },
            )

        system_content = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: OPEN TOPIC DISCOVERY (Version: {PROMPT_VERSION_DISCOVERY})
Analyze the forensic conversation evidence and discover the main topics or subjects discussed.
Find up to {max_topics} distinct topics.

ETHICAL FORENSIC CONSTRAINTS:
- DO NOT infer personal identities, real names, or demographic profiles.
- DO NOT generate psychological or character assessments.
- Describe ONLY objective subjects, factual events, activities, or matters discussed in the text.

OUTPUT RULES:
1. Provide a concise 'label' for each topic (in Italian).
2. Provide a 'short_description' explaining the topic objectively (in Italian).
3. Cite the exact 'evidence_ids' supporting each topic. Each topic must cite at least one valid evidence_id.
4. Return an array of topics matching schema, with at most {max_topics} elements.
"""

        if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
            system_content += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Analyze all languages and output labels/descriptions in Italian.\n"
        elif strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        user_content = serialize_document_for_llm(document, strategy=strategy, translation=translation)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        response = self.client.chat_completion(
            messages=messages,
            model_id=target_model,
            schema=TOPIC_DISCOVERY_SCHEMA,
            temperature=temperature,
            seed=seed,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )

        if target_model and response.model != target_model:
            raise AiModelMismatchError(
                f"Model mismatch in Topic Discovery: richiesto model_id '{target_model}', "
                f"ma il backend ha restituito '{response.model}'."
            )

        discovered = validate_topic_discovery_payload(
            raw_text=response.content,
            valid_evidence_ids=valid_evidence_ids,
            max_topics=max_topics,
        )

        metadata: dict[str, Any] = {
            "prompt_version": PROMPT_VERSION_DISCOVERY,
            "strategy": strategy.value,
            "model": response.model,
            "latency_seconds": response.latency_seconds,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
            "max_tokens": max_tokens,
            "max_topics": max_topics,
            "temperature": temperature,
            "seed": seed,
        }

        return TopicDiscoveryResult(
            topics=discovered,
            provenance_document_id=document.document_id,
            metadata=metadata,
        )
