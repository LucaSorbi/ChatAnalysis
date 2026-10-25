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

import time
from typing import Any, Mapping

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
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
    generate_evidence_alias_maps,
    serialize_document_for_llm,
)
from ai.structured import (
    TOPIC_DETECTION_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA,
    validate_topic_detection_payload,
    validate_topic_discovery_payload,
)
from ai.token_budget import (
    DEFAULT_SAFETY_MARGIN_TOKENS,
    calculate_dynamic_output_budget,
    calculate_token_budget_plan,
    estimate_request_input_tokens,
)

PROMPT_VERSION_DETECTION = "topic_detection_v1"
PROMPT_VERSION_DETECTION_OPERATIONAL = "topic_detection_operational_v2"
PROMPT_VERSION_DISCOVERY = "topic_discovery_v1"
PROMPT_VERSION_DISCOVERY_OPERATIONAL = "topic_discovery_operational_v2"

PROGRAMMING_ERRORS = (
    NameError,
    AttributeError,
    ImportError,
    SyntaxError,
    UnboundLocalError,
    TypeError,
    KeyError,
    IndexError,
    AssertionError,
)

OPERATIONAL_SEMANTIC_DIRECTIVE = """
OPERATIONAL SEMANTIC & MULTIMODAL INTERPRETATION DIRECTIVE:
1. OBJECTIVE THEMATIC & CONCEPTUAL EVALUATION:
   - In LABEL-ONLY mode (where no separate description is provided), interpret the genuine thematic and conceptual scope of the topic label across Italian and any other languages present in the evidence.
   - The requested topic may be expressed semantically rather than through literal keyword overlap.
   - Do NOT require the literal words of the topic title to appear verbatim: recognize valid synonyms, paraphrases, contextual equivalents, and domain-appropriate terminology when directly supported by the evidence.
   - You may return PRESENT even when the exact words used in the topic label do not appear literally in the evidence.
   - Crucially, do NOT confuse mere thematic proximity, incidental mentions, or background context with sufficient proof of the topic.
2. BALANCED FORENSIC TRI-STATE DISCIPLINE:
   - PRESENT: Return 'PRESENT' if and only if the forensic evidence contains clear, direct, and verifiable proof that the topic was actively discussed. Every 'PRESENT' decision MUST cite at least one specific, genuinely supporting evidence_id. PRESENT still requires at least one valid evidence_id.
   - ABSENT: Return 'ABSENT' whenever the topic is not discussed, mentioned, or evidenced in the conversation. When decision is 'ABSENT', 'evidence_ids' MUST be an empty array [].
   - UNCERTAIN: Return 'UNCERTAIN' if the evidence is fragmentary, ambiguous, tangential, or merely hints at related elements without providing solid proof. Do NOT force 'PRESENT' on ambiguous data.
   - Do NOT bias your judgment toward any verdict. Treat PRESENT, ABSENT, and UNCERTAIN with equal objectivity.
3. STRICT EVIDENCE GROUNDING:
   - Cite ONLY exact evidence_ids present in the provided evidence block.
   - NEVER fabricate, extrapolate, or invent evidence IDs.
   - Do not infer beyond the supplied evidence.
4. MULTIMODAL SOURCES:
   - Evaluate the meaning of ALL evidence sources, including:
     - message text
     - audio transcriptions
     - image OCR text
     - image vision descriptions
"""

OPERATIONAL_MULTIMODAL_DIRECTIVE = OPERATIONAL_SEMANTIC_DIRECTIVE


class TopicDetectionAnalyzer:
    """
    Analizzatore per la verifica mirata di un singolo topic su un ConversationEvidenceDocument.
    """

    def __init__(
        self,
        client: BaseLlmClient,
        default_model_id: str | None = None,
        operational_mode: bool = False,
    ) -> None:
        self.client = client
        self.default_model_id = default_model_id
        self.operational_mode = operational_mode

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
        operational_mode: bool | None = None,
        context_length: int = 8192,
        use_evidence_aliases: bool | None = None,
        on_retry_callback: Any = None,
    ) -> TopicDetectionResult:
        """
        Esegue la detection del topic specificato.

        In modalità operativa (is_operational=True):
        - Gli alias deterministici sono abilitati di default (coerente con Discovery).
        - Un retry con direttiva correttiva viene tentato se il modello cita
          evidence_id inesistenti (AiInvalidEvidenceCitationError), fino a 2 tentativi totali.
        """
        target_model = model_id or self.default_model_id
        valid_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}
        is_operational = self.operational_mode if operational_mode is None else operational_mode
        prompt_version = (
            PROMPT_VERSION_DETECTION_OPERATIONAL
            if is_operational
            else PROMPT_VERSION_DETECTION
        )

        # Simmetria con Discovery: in modalità operativa, gli alias sono abilitati di default
        use_aliases = is_operational if use_evidence_aliases is None else use_evidence_aliases
        id_to_alias: dict[str, str] = {}
        alias_to_id: dict[str, str] = {}
        if use_aliases:
            id_to_alias, alias_to_id = generate_evidence_alias_maps(document)

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
                    "prompt_version": prompt_version,
                    "strategy": strategy.value,
                    "model": target_model or "none",
                    "requested_model": target_model,
                    "operational_mode": is_operational,
                    "raw_response": None,
                    "evidence_citation_protocol": "deterministic_alias_v1" if use_aliases else "direct_evidence_id",
                    "evidence_aliases_used": bool(use_aliases),
                },
            )

        is_label_only = getattr(topic, "is_label_only", False) or (topic.label.strip().casefold() == topic.description.strip().casefold())

        rule_4_cite = (
            "4. CITE ONLY EXACT evidence_ids that are present in the evidence block using their assigned aliases (e.g. 'E001', 'E002'). DO NOT fabricate or invent IDs."
            if use_aliases
            else "4. CITE ONLY EXACT evidence_ids that are present in the evidence block. DO NOT fabricate or invent IDs."
        )

        # Costruzione del system prompt con direttiva anti-injection e specifiche di task
        if is_operational:
            if is_label_only:
                topic_spec = f"""TOPIC ID: {topic.topic_id}
TOPIC LABEL (TITLE ONLY): {topic.label}
SPECIFIC DESCRIPTION: None provided (LABEL-ONLY query). Evaluate the semantic, conceptual, and contextual scope of '{topic.label}'."""
            else:
                topic_spec = f"""TOPIC ID: {topic.topic_id}
TOPIC LABEL: {topic.label}
DESCRIPTION: {topic.description}"""

            system_content = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: SPECIFIC TOPIC DETECTION (Version: {prompt_version})
You must determine if the following topic is discussed or present in the forensic evidence:
{topic_spec}

OUTPUT RULES:
1. Return decision 'PRESENT' if and only if the topic is clearly discussed or evidenced in the conversation.
   You MUST cite at least one exact evidence_id in 'evidence_ids'.
2. Return decision 'ABSENT' if the topic is NOT present in the conversation evidence. 'evidence_ids' MUST be an empty array [].
3. Return decision 'UNCERTAIN' if the evidence is ambiguous, partial, fragmentary, or insufficient to decide. You may cite ambiguous evidence_ids.
{rule_4_cite}
5. Provide a short, objective rationale explaining the decision in Italian. DO NOT provide lengthy chain-of-thought.
"""
            system_content += f"{OPERATIONAL_SEMANTIC_DIRECTIVE}\n"
        else:
            system_content = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: SPECIFIC TOPIC DETECTION (Version: {prompt_version})
You must determine if the following topic is discussed or present in the forensic evidence:
TOPIC ID: {topic.topic_id}
LABEL: {topic.label}
DESCRIPTION: {topic.description}

OUTPUT RULES:
1. Return decision 'PRESENT' if and only if the topic is clearly discussed or evidenced in the conversation.
   You MUST cite at least one exact evidence_id in 'evidence_ids'.
2. Return decision 'ABSENT' if the topic is NOT present. 'evidence_ids' MUST be an empty array [].
3. Return decision 'UNCERTAIN' if the evidence is ambiguous, partial, or insufficient to decide. You may cite ambiguous evidence_ids.
{rule_4_cite}
5. Provide a short, objective rationale explaining the decision in Italian. DO NOT provide lengthy chain-of-thought.
"""

        if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
            system_content += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Understand the original language but respond with rationale in Italian.\n"
        elif strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        user_content = serialize_document_for_llm(
            document,
            strategy=strategy,
            translation=translation,
            evidence_id_map=id_to_alias if use_aliases else None,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        if is_operational and context_length > 0:
            eff_out = max_tokens or 512
            plan = calculate_token_budget_plan(
                context_length=context_length,
                messages=messages,
                reserved_output_tokens=eff_out,
                schema=TOPIC_DETECTION_SCHEMA,
            )
            if not plan.fits_in_context:
                raise AnalysisInputTooLargeError(
                    f"La richiesta di Topic Detection supera la finestra di contesto ({plan.total_budget_needed} > {context_length} token stimati). "
                    f"Il blocco necessita di ulteriore suddivisione."
                )

        # Retry loop per Detection operativa: fino a 2 tentativi con direttiva correttiva
        # per AiInvalidEvidenceCitationError (simmetrico a Discovery)
        max_attempts = 2 if is_operational else 1
        last_error: Exception | None = None
        response = None
        attempt_used = 0
        cum_prompt_tokens: int | None = None
        cum_completion_tokens: int | None = None
        cum_total_tokens: int | None = None
        cum_latency_seconds: float = 0.0

        for attempt in range(max_attempts):
            attempt_used = attempt
            try:
                response = self.client.chat_completion(
                    messages=messages,
                    model_id=target_model,
                    schema=TOPIC_DETECTION_SCHEMA,
                    temperature=temperature,
                    seed=seed,
                    timeout_seconds=timeout_seconds,
                    max_tokens=max_tokens,
                )
                if response:
                    if getattr(response, "prompt_tokens", None) is not None:
                        cum_prompt_tokens = (cum_prompt_tokens or 0) + response.prompt_tokens
                    if getattr(response, "completion_tokens", None) is not None:
                        cum_completion_tokens = (cum_completion_tokens or 0) + response.completion_tokens
                    if getattr(response, "total_tokens", None) is not None:
                        cum_total_tokens = (cum_total_tokens or 0) + response.total_tokens
                    if getattr(response, "latency_seconds", None) is not None:
                        cum_latency_seconds += (response.latency_seconds or 0.0)

                # Verifica Model Mismatch
                if target_model and response.model != target_model:
                    is_match = False
                    if is_operational:
                        from ai.lmstudio import is_equivalent_operational_model
                        is_match = is_equivalent_operational_model(target_model, response.model, client=self.client)

                    if not is_match:
                        raise AiModelMismatchError(
                            f"Model mismatch in Topic Detection: richiesto model_id '{target_model}', "
                            f"ma il backend ha restituito '{response.model}'."
                        )

                decision, evidence_ids, rationale = validate_topic_detection_payload(
                    raw_text=response.content,
                    valid_evidence_ids=valid_evidence_ids,
                    alias_map=alias_to_id if use_aliases else None,
                )
                last_error = None
                break

            except Exception as exc:
                last_error = exc
                if attempt + 1 >= max_attempts or not is_operational:
                    try:
                        setattr(exc, "attempts", attempt_used + 1)
                        setattr(exc, "retries", attempt_used)
                        setattr(exc, "prompt_tokens", cum_prompt_tokens)
                        setattr(exc, "completion_tokens", cum_completion_tokens)
                        setattr(exc, "total_tokens", cum_total_tokens)
                        setattr(exc, "latency_seconds", cum_latency_seconds)
                    except Exception:
                        pass
                    raise

                if isinstance(exc, (AiModelMismatchError, *PROGRAMMING_ERRORS)):
                    raise

                if isinstance(exc, AiBackendRequestError):
                    cat = getattr(exc, "error_category", None)
                    code = getattr(exc, "status_code", None)
                    # CONTEXT_OVERFLOW non può essere risolto nel singolo chunk senza suddivisione (demandata al service)
                    if cat == "CONTEXT_OVERFLOW":
                        raise
                    # HTTP 400 permanente (parametri non validi o schema rifiutato) o qualsiasi 4xx non-429 fallisce chiuso
                    if code and 400 <= code < 500 and code != 429:
                        raise
                    if cat in ("HTTP_INVALID_PARAMETERS", "INVALID_PARAMETERS", "HTTP_SCHEMA_REJECTED", "UNSUPPORTED_SCHEMA"):
                        raise
                    safe_reason = f"backend_request_retry: status={code or 'transient'}"
                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, safe_reason)
                        except Exception:
                            pass
                    time.sleep(0.5)
                    continue

                if isinstance(exc, AiBackendTimeoutError):
                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, "backend_timeout_retry")
                        except Exception:
                            pass
                    time.sleep(0.5)
                    continue

                if isinstance(exc, AiInvalidEvidenceCitationError):
                    invalid_list = getattr(exc, "invalid_evidence_ids", ())
                    invalid_str = ", ".join(f"'{x}'" for x in invalid_list) if invalid_list else "non conformi"
                    if use_aliases:
                        valid_keys = sorted(alias_to_id.keys()) if alias_to_id else []
                        valid_all = ", ".join(f"'{k}'" for k in valid_keys)
                        correction_msg = (
                            f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence_id values: [{invalid_str}]. "
                            f"Strict forensic requirement: You MUST cite ONLY exact alias identifiers assigned to the evidence block of THIS specific chunk. "
                            f"Available valid aliases in this chunk ({len(valid_keys)} total): [{valid_all}]. "
                            "All listed aliases are valid. Do NOT invent IDs, do NOT cite numbers not in this block, do NOT use IDs from other conversations. "
                            "Re-evaluate and return the corrected JSON matching the schema."
                        )
                    else:
                        valid_keys = sorted(valid_evidence_ids)
                        valid_all = ", ".join(f"'{k}'" for k in valid_keys)
                        correction_msg = (
                            f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence_id values: [{invalid_str}]. "
                            f"Strict forensic requirement: You MUST cite ONLY exact evidence_id strings that appear literally in this chunk. "
                            f"Available valid evidence IDs in this chunk ({len(valid_keys)} total): [{valid_all}]. "
                            "All listed evidence IDs are valid. Do NOT invent IDs, do NOT cite IDs from other blocks or conversations. "
                            "Re-evaluate and return the corrected JSON matching the schema."
                        )

                    # Candidato A: Retry contestuale con cronologia assistente
                    candidate_msgs: list[dict[str, str]] = list(messages)
                    if response and getattr(response, "content", None):
                        candidate_msgs.append({"role": "assistant", "content": response.content})
                    candidate_msgs.append({"role": "user", "content": correction_msg})

                    # Ricalcolo preventivo del budget token input
                    reserved_out = max_tokens or 512
                    cand_plan = calculate_token_budget_plan(
                        context_length=context_length,
                        messages=candidate_msgs,
                        reserved_output_tokens=reserved_out,
                        schema=TOPIC_DETECTION_SCHEMA,
                    )

                    if cand_plan.fits_in_context and cand_plan.available_headroom >= 256:
                        messages = candidate_msgs
                    else:
                        # Candidato B: Strategia compatta (single-turn) per prevenire context overflow
                        orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                        compact_msgs: list[dict[str, str]] = [
                            messages[0],
                            {
                                "role": "user",
                                "content": f"{orig_user_content}\n\n[DIRETTIVA CORRETTIVA DI PRECISIONE FORENSE]\n{correction_msg}",
                            },
                        ]
                        compact_plan = calculate_token_budget_plan(
                            context_length=context_length,
                            messages=compact_msgs,
                            reserved_output_tokens=reserved_out,
                            schema=TOPIC_DETECTION_SCHEMA,
                        )
                        if compact_plan.fits_in_context and compact_plan.available_headroom >= 256:
                            messages = compact_msgs
                        else:
                            raise AnalysisInputTooLargeError(
                                f"Impossibile eseguire il retry correttivo per citazioni in Topic Detection: "
                                f"il prompt richiede {compact_plan.estimated_input_tokens} token "
                                f"su context_length di {context_length}."
                            )

                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, "invalid_evidence_citation")
                        except Exception:
                            pass
                    continue

                if isinstance(exc, AiStructuredOutputError):
                    struct_msg = (
                        "CORRECTION DIRECTIVE: Your previous output was not valid JSON conforming to the schema. "
                        "Please provide ONLY valid JSON with keys 'decision', 'evidence_ids', and 'rationale'."
                    )
                    candidate_msgs = list(messages)
                    if response and getattr(response, "content", None):
                        candidate_msgs.append({"role": "assistant", "content": response.content})
                    candidate_msgs.append({"role": "user", "content": struct_msg})

                    reserved_out = max_tokens or 512
                    cand_plan = calculate_token_budget_plan(
                        context_length=context_length,
                        messages=candidate_msgs,
                        reserved_output_tokens=reserved_out,
                        schema=TOPIC_DETECTION_SCHEMA,
                    )

                    if cand_plan.fits_in_context and cand_plan.available_headroom >= 256:
                        messages = candidate_msgs
                    else:
                        orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                        compact_msgs = [
                            messages[0],
                            {
                                "role": "user",
                                "content": f"{orig_user_content}\n\n[DIRETTIVA FORMATO JSON]\n{struct_msg}",
                            },
                        ]
                        compact_plan = calculate_token_budget_plan(
                            context_length=context_length,
                            messages=compact_msgs,
                            reserved_output_tokens=reserved_out,
                            schema=TOPIC_DETECTION_SCHEMA,
                        )
                        if compact_plan.fits_in_context and compact_plan.available_headroom >= 256:
                            messages = compact_msgs
                        else:
                            raise AnalysisInputTooLargeError(
                                f"Impossibile eseguire il retry per JSON malformato in Topic Detection: "
                                f"il prompt richiede {compact_plan.estimated_input_tokens} token "
                                f"su context_length di {context_length}."
                            )

                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, "structured_output_retry")
                        except Exception:
                            pass
                    continue

                raise

        if last_error is not None:
            try:
                setattr(last_error, "attempts", attempt_used + 1)
                setattr(last_error, "retries", attempt_used)
                setattr(last_error, "prompt_tokens", cum_prompt_tokens)
                setattr(last_error, "completion_tokens", cum_completion_tokens)
                setattr(last_error, "total_tokens", cum_total_tokens)
                setattr(last_error, "latency_seconds", cum_latency_seconds)
            except Exception:
                pass
            raise last_error

        raw_resp = getattr(response, "raw_response", {}) if response else {}
        finish_reason = (
            raw_resp.get("finish_reason")
            or (raw_resp.get("choices", [{}])[0].get("finish_reason") if isinstance(raw_resp.get("choices"), list) and raw_resp.get("choices") else None)
        )

        metadata: dict[str, Any] = {
            "prompt_version": prompt_version,
            "strategy": strategy.value,
            "model": response.model,
            "requested_model": target_model,
            "latency_seconds": cum_latency_seconds if cum_latency_seconds > 0 else (response.latency_seconds if response else 0.0),
            "prompt_tokens": cum_prompt_tokens if cum_prompt_tokens is not None else (response.prompt_tokens if response else None),
            "completion_tokens": cum_completion_tokens if cum_completion_tokens is not None else (response.completion_tokens if response else None),
            "total_tokens": cum_total_tokens if cum_total_tokens is not None else (response.total_tokens if response else None),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
            "operational_mode": is_operational,
            "query_mode": "LABEL_ONLY" if is_label_only else "LABEL_AND_DESCRIPTION",
            "is_label_only": is_label_only,
            "raw_response": response.content,
            "finish_reason": finish_reason,
            "evidence_citation_protocol": "deterministic_alias_v1" if use_aliases else "direct_evidence_id",
            "evidence_aliases_used": bool(use_aliases),
            "retries": attempt_used,
            "attempts": attempt_used + 1,
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

    def __init__(
        self,
        client: BaseLlmClient,
        default_model_id: str | None = None,
        operational_mode: bool = False,
    ) -> None:
        self.client = client
        self.default_model_id = default_model_id
        self.operational_mode = operational_mode

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
        operational_mode: bool | None = None,
        context_length: int = 8192,
        on_retry_callback: Any = None,
        use_evidence_aliases: bool | None = None,
    ) -> TopicDiscoveryResult:
        """
        Esegue la scoperta non supervisionata di temi emergenti nel documento.
        """
        target_model = model_id or self.default_model_id
        valid_evidence_ids = {s.evidence_id for s in document.all_evidence_sections}

        is_op = self.operational_mode if operational_mode is None else operational_mode
        prompt_version = (
            PROMPT_VERSION_DISCOVERY_OPERATIONAL
            if is_op
            else PROMPT_VERSION_DISCOVERY
        )

        use_aliases = is_op if use_evidence_aliases is None else use_evidence_aliases
        id_to_alias: dict[str, str] = {}
        alias_to_id: dict[str, str] = {}
        if use_aliases:
            id_to_alias, alias_to_id = generate_evidence_alias_maps(document)

        if not valid_evidence_ids:
            return TopicDiscoveryResult(
                topics=(),
                provenance_document_id=document.document_id,
                metadata={
                    "empty_document": True,
                    "prompt_version": PROMPT_VERSION_DISCOVERY,
                    "strategy": strategy.value,
                    "model": target_model or "none",
                    "evidence_citation_protocol": "deterministic_alias_v1" if use_aliases else "direct_evidence_id",
                    "evidence_aliases_used": bool(use_aliases),
                },
            )

        current_topics = max_topics

        cite_rule = (
            "3. Cite the exact 'evidence_ids' supporting each topic using ONLY the assigned alias identifiers shown in the evidence block (e.g. 'E001', 'E002'). Each topic must cite at least one valid evidence_id from this block."
            if use_aliases
            else "3. Cite the exact 'evidence_ids' supporting each topic. Each topic must cite at least one valid evidence_id."
        )

        def _make_sys_prompt(num_topics: int) -> str:
            sp = f"""{SYSTEM_SECURITY_DIRECTIVE}
TASK: OPEN TOPIC DISCOVERY (Version: {prompt_version})
Analyze the forensic conversation evidence and discover the main topics or subjects discussed.
Find up to {num_topics} distinct topics.

ETHICAL FORENSIC CONSTRAINTS:
- DO NOT infer personal identities, real names, or demographic profiles.
- DO NOT generate psychological or character assessments.
- Describe ONLY objective subjects, factual events, activities, or matters discussed in the text.

OUTPUT RULES:
1. Provide a concise 'label' for each topic (in Italian).
2. Provide a 'short_description' explaining the topic objectively (in Italian).
{cite_rule}
4. Return an array of topics matching schema, with at most {num_topics} elements.
"""
            if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
                sp += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Analyze all languages and output labels/descriptions in Italian.\n"
            return sp

        if strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        user_content = serialize_document_for_llm(
            document,
            strategy=strategy,
            translation=translation,
            evidence_id_map=id_to_alias if use_aliases else None,
        )

        messages = [
            {"role": "system", "content": _make_sys_prompt(current_topics)},
            {"role": "user", "content": user_content},
        ]

        # Stima preventiva input tokens e verifica budget rispetto a context_length
        input_est = estimate_request_input_tokens(messages, schema=TOPIC_DISCOVERY_SCHEMA)
        if is_op and context_length > 0:
            plan = calculate_token_budget_plan(
                context_length=context_length,
                messages=messages,
                reserved_output_tokens=max_tokens or 1024,
                schema=TOPIC_DISCOVERY_SCHEMA,
            )
            if not plan.fits_in_context:
                if plan.available_headroom < 256:
                    raise AnalysisInputTooLargeError(
                        f"Il chunk di Topic Discovery è troppo esteso per la finestra di contesto "
                        f"(stima input: {plan.estimated_input_tokens} token su {context_length} disponibili). "
                        f"Il blocco necessita di ulteriore suddivisione."
                    )
                max_tokens = min(max_tokens or 1024, plan.available_headroom)

        max_attempts = 2 if is_op else 1
        current_max_tokens = max_tokens
        last_error: Exception | None = None
        response = None
        attempt_used = 0
        finish_reason = None
        cum_prompt_tokens: int | None = None
        cum_completion_tokens: int | None = None
        cum_total_tokens: int | None = None
        cum_latency_seconds: float = 0.0

        for attempt in range(max_attempts):
            attempt_used = attempt
            try:
                response = self.client.chat_completion(
                    messages=messages,
                    model_id=target_model,
                    schema=TOPIC_DISCOVERY_SCHEMA,
                    temperature=temperature,
                    seed=seed,
                    timeout_seconds=timeout_seconds,
                    max_tokens=current_max_tokens,
                )
                if response:
                    if getattr(response, "prompt_tokens", None) is not None:
                        cum_prompt_tokens = (cum_prompt_tokens or 0) + response.prompt_tokens
                    if getattr(response, "completion_tokens", None) is not None:
                        cum_completion_tokens = (cum_completion_tokens or 0) + response.completion_tokens
                    if getattr(response, "total_tokens", None) is not None:
                        cum_total_tokens = (cum_total_tokens or 0) + response.total_tokens
                    if getattr(response, "latency_seconds", None) is not None:
                        cum_latency_seconds += (response.latency_seconds or 0.0)

                if target_model and response.model != target_model:
                    is_match = False
                    if is_op:
                        from ai.lmstudio import is_equivalent_operational_model
                        is_match = is_equivalent_operational_model(target_model, response.model, client=self.client)

                    if not is_match:
                        raise AiModelMismatchError(
                            f"Model mismatch in Topic Discovery: richiesto model_id '{target_model}', "
                            f"ma il backend ha restituito '{response.model}'."
                        )

                raw_resp = getattr(response, "raw_response", {})
                finish_reason = raw_resp.get("finish_reason") if isinstance(raw_resp, Mapping) else None

                # Rilevamento troncamento per superamento limite token
                if finish_reason == "length":
                    raise AiStructuredOutputError(
                        f"Topic Discovery fallita: risposta del modello troncata per limite token "
                        f"(finish_reason='length', max_tokens={current_max_tokens})."
                    )

                discovered = validate_topic_discovery_payload(
                    raw_text=response.content,
                    valid_evidence_ids=valid_evidence_ids,
                    max_topics=current_topics,
                    alias_map=alias_to_id if use_aliases else None,
                )
                last_error = None
                break

            except Exception as exc:
                last_error = exc
                if attempt + 1 >= max_attempts or not is_op:
                    try:
                        setattr(exc, "attempts", attempt_used + 1)
                        setattr(exc, "retries", attempt_used)
                        setattr(exc, "prompt_tokens", cum_prompt_tokens)
                        setattr(exc, "completion_tokens", cum_completion_tokens)
                        setattr(exc, "total_tokens", cum_total_tokens)
                        setattr(exc, "latency_seconds", cum_latency_seconds)
                    except Exception:
                        pass
                    raise

                if isinstance(exc, (AiModelMismatchError, *PROGRAMMING_ERRORS)):
                    raise

                if isinstance(exc, AiBackendRequestError):
                    cat = getattr(exc, "error_category", None)
                    code = getattr(exc, "status_code", None)
                    if cat == "CONTEXT_OVERFLOW":
                        raise
                    if code and 400 <= code < 500 and code != 429:
                        raise
                    if cat in ("HTTP_INVALID_PARAMETERS", "INVALID_PARAMETERS", "HTTP_SCHEMA_REJECTED", "UNSUPPORTED_SCHEMA"):
                        raise
                    safe_reason = f"backend_request_retry: status={code or 'transient'}"
                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, safe_reason)
                        except Exception:
                            pass
                    time.sleep(0.5)
                    continue

                if isinstance(exc, AiBackendTimeoutError):
                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, "backend_timeout_retry")
                        except Exception:
                            pass
                    time.sleep(0.5)
                    continue

                if isinstance(exc, AiInvalidEvidenceCitationError):
                    invalid_list = getattr(exc, "invalid_evidence_ids", ())
                    invalid_str = ", ".join(f"'{x}'" for x in invalid_list) if invalid_list else "non conformi"
                    if use_aliases:
                        valid_keys = sorted(alias_to_id.keys()) if alias_to_id else []
                        valid_all = ", ".join(f"'{k}'" for k in valid_keys)
                        correction_msg = (
                            f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence_id values: [{invalid_str}]. "
                            f"Strict forensic requirement: You MUST cite ONLY exact alias identifiers assigned to the evidence block of THIS specific chunk. "
                            f"Available valid aliases in this block ({len(valid_keys)} total): [{valid_all}]. "
                            "All listed aliases are valid. Do NOT invent IDs, do NOT cite numbers not in this block, do NOT use IDs from other conversations. "
                            "Re-evaluate the topics and return the corrected JSON matching the schema."
                        )
                    else:
                        valid_keys = sorted(valid_evidence_ids)
                        valid_all = ", ".join(f"'{k}'" for k in valid_keys)
                        correction_msg = (
                            f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence_id values: [{invalid_str}]. "
                            f"Strict forensic requirement: You MUST cite ONLY exact evidence_id strings that appear literally in this chunk. "
                            f"Available valid evidence IDs in this chunk ({len(valid_keys)} total): [{valid_all}]. "
                            "All listed evidence IDs are valid. Do NOT invent IDs, do NOT cite IDs from other blocks or conversations. "
                            "Re-evaluate the topics and return the corrected JSON matching the schema."
                        )
                    # Candidato A: Retry contestuale con cronologia assistente
                    candidate_msgs = list(messages)
                    if response and getattr(response, "content", None):
                        candidate_msgs.append({"role": "assistant", "content": response.content})
                    candidate_msgs.append({"role": "user", "content": correction_msg})

                    # Ricalcolo preventivo del budget token input
                    reserved_out = max(256, min(current_max_tokens or 1024, current_topics * 120 + 256))
                    cand_plan = calculate_token_budget_plan(
                        context_length=context_length,
                        messages=candidate_msgs,
                        reserved_output_tokens=reserved_out,
                        schema=TOPIC_DISCOVERY_SCHEMA,
                    )

                    if cand_plan.fits_in_context and cand_plan.available_headroom >= 256:
                        messages = candidate_msgs
                        input_est = cand_plan.estimated_input_tokens
                        current_max_tokens = min(current_max_tokens or 1024, cand_plan.available_headroom)
                    else:
                        # Candidato B: Strategia alternativa compatta (single-turn) per prevenire context overflow / HTTP 400
                        orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                        compact_msgs = [
                            messages[0],
                            {
                                "role": "user",
                                "content": f"{orig_user_content}\n\n[DIRETTIVA CORRETTIVA DI PRECISIONE FORENSE]\n{correction_msg}",
                            },
                        ]
                        compact_plan = calculate_token_budget_plan(
                            context_length=context_length,
                            messages=compact_msgs,
                            reserved_output_tokens=reserved_out,
                            schema=TOPIC_DISCOVERY_SCHEMA,
                        )
                        if compact_plan.fits_in_context and compact_plan.available_headroom >= 256:
                            messages = compact_msgs
                            input_est = compact_plan.estimated_input_tokens
                            current_max_tokens = min(current_max_tokens or 1024, compact_plan.available_headroom)
                        else:
                            raise AnalysisInputTooLargeError(
                                f"Impossibile eseguire il retry correttivo per citazioni: "
                                f"il prompt con correzione richiede {compact_plan.estimated_input_tokens} token "
                                f"a fronte di un context_length di {context_length}. Blocco fallito per context overflow."
                            )

                    if on_retry_callback:
                        try:
                            on_retry_callback(attempt + 1, "invalid_evidence_citation")
                        except Exception:
                            pass
                    continue

                if isinstance(exc, AiStructuredOutputError):
                    err_msg = str(exc)
                    if finish_reason == "length" or "Unterminated string" in err_msg or "JSONDecodeError" in err_msg:
                        headroom = max(0, context_length - input_est - DEFAULT_SAFETY_MARGIN_TOKENS)
                        if headroom > (current_max_tokens or 1024):
                            current_max_tokens = min(int((current_max_tokens or 1024) * 1.5), headroom)
                        else:
                            current_topics = max(2, current_topics // 2)
                            messages[0]["content"] = _make_sys_prompt(current_topics)
                            input_est = estimate_request_input_tokens(messages, schema=TOPIC_DISCOVERY_SCHEMA)
                            current_max_tokens = calculate_dynamic_output_budget(
                                context_length=context_length,
                                estimated_prompt_tokens=input_est,
                                desired_output_tokens=current_topics * 120 + 256,
                                min_output_tokens=256,
                                safety_margin=DEFAULT_SAFETY_MARGIN_TOKENS,
                            )
                        if on_retry_callback:
                            try:
                                on_retry_callback(attempt + 1, "incomplete_json" if finish_reason != "length" else "finish_reason=length")
                            except Exception:
                                pass
                        continue
                    else:
                        struct_msg = (
                            f"CORRECTION DIRECTIVE: Your previous output was not valid JSON conforming to the schema. "
                            f"Please provide ONLY valid JSON with a 'topics' array (max {current_topics} items), "
                            f"each containing 'label', 'short_description', and 'evidence_ids'."
                        )
                        candidate_msgs = list(messages)
                        if response and getattr(response, "content", None):
                            candidate_msgs.append({"role": "assistant", "content": response.content})
                        candidate_msgs.append({"role": "user", "content": struct_msg})

                        reserved_out = max(256, min(current_max_tokens or 1024, current_topics * 120 + 256))
                        cand_plan = calculate_token_budget_plan(
                            context_length=context_length,
                            messages=candidate_msgs,
                            reserved_output_tokens=reserved_out,
                            schema=TOPIC_DISCOVERY_SCHEMA,
                        )
                        if cand_plan.fits_in_context and cand_plan.available_headroom >= 256:
                            messages = candidate_msgs
                            input_est = cand_plan.estimated_input_tokens
                            current_max_tokens = min(current_max_tokens or 1024, cand_plan.available_headroom)
                        else:
                            orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                            compact_msgs = [
                                messages[0],
                                {
                                    "role": "user",
                                    "content": f"{orig_user_content}\n\n[DIRETTIVA FORMATO JSON]\n{struct_msg}",
                                },
                            ]
                            compact_plan = calculate_token_budget_plan(
                                context_length=context_length,
                                messages=compact_msgs,
                                reserved_output_tokens=reserved_out,
                                schema=TOPIC_DISCOVERY_SCHEMA,
                            )
                            if compact_plan.fits_in_context and compact_plan.available_headroom >= 256:
                                messages = compact_msgs
                                input_est = compact_plan.estimated_input_tokens
                                current_max_tokens = min(current_max_tokens or 1024, compact_plan.available_headroom)
                            else:
                                raise AnalysisInputTooLargeError(
                                    f"Impossibile eseguire il retry per JSON malformato in Topic Discovery: "
                                    f"il prompt richiede {compact_plan.estimated_input_tokens} token "
                                    f"su context_length di {context_length}."
                                )
                        if on_retry_callback:
                            try:
                                on_retry_callback(attempt + 1, "structured_output_retry")
                            except Exception:
                                pass
                        continue

                raise

        if last_error is not None:
            try:
                setattr(last_error, "attempts", attempt_used + 1)
                setattr(last_error, "retries", attempt_used)
                setattr(last_error, "prompt_tokens", cum_prompt_tokens)
                setattr(last_error, "completion_tokens", cum_completion_tokens)
                setattr(last_error, "total_tokens", cum_total_tokens)
                setattr(last_error, "latency_seconds", cum_latency_seconds)
            except Exception:
                pass
            raise last_error

        metadata: dict[str, Any] = {
            "prompt_version": prompt_version,
            "strategy": strategy.value,
            "model": response.model if response else target_model,
            "latency_seconds": cum_latency_seconds if cum_latency_seconds > 0 else (response.latency_seconds if response else 0.0),
            "prompt_tokens": cum_prompt_tokens if cum_prompt_tokens is not None else (response.prompt_tokens if response else None),
            "completion_tokens": cum_completion_tokens if cum_completion_tokens is not None else (response.completion_tokens if response else None),
            "total_tokens": cum_total_tokens if cum_total_tokens is not None else (response.total_tokens if response else None),
            "max_tokens": current_max_tokens,
            "max_topics": current_topics,
            "context_length": context_length,
            "temperature": temperature,
            "seed": seed,
            "retries": attempt_used,
            "attempts": attempt_used + 1,
            "finish_reason": finish_reason,
            "http_status": 200,
            "operational_mode": is_op,
            "evidence_citation_protocol": "deterministic_alias_v1" if use_aliases else "direct_evidence_id",
            "evidence_aliases_used": bool(use_aliases),
        }

        return TopicDiscoveryResult(
            topics=discovered,
            provenance_document_id=document.document_id,
            metadata=metadata,
        )
