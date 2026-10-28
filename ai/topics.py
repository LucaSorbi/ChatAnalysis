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
    COMPACT_SYSTEM_SECURITY_DIRECTIVE,
    SERIALIZATION_VERSION_COMPACT_JSONL,
    SYSTEM_SECURITY_DIRECTIVE,
    generate_evidence_alias_maps,
    serialize_document_compact_jsonl,
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
PROMPT_VERSION_DISCOVERY_OPERATIONAL = "topic_discovery_operational_v3"

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
   - If the evidence clearly describes the concept represented by the topic, return PRESENT even when the exact words used in the topic label do not appear literally in the evidence.
   - Do NOT require the literal words of the topic title to appear verbatim if valid synonyms, paraphrases, or domain-appropriate equivalents are directly supported by the evidence.
   - Do NOT return PRESENT unless there is concrete, direct evidence in the conversation text. Mere casual chat, greetings, or tangential background context are STRICTLY ABSENT.
2. BALANCED FORENSIC TRI-STATE DISCIPLINE:
   - PRESENT: Return 'PRESENT' if and only if the forensic evidence contains clear, direct, and verifiable proof that the topic was actively discussed. Every 'PRESENT' decision MUST cite at least one specific, genuinely supporting evidence_id. PRESENT still requires at least one valid evidence_id.
   - ABSENT: Return 'ABSENT' whenever the topic is not directly discussed, mentioned, or evidenced in the conversation. When decision is 'ABSENT', 'evidence_ids' MUST be an empty array [].
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
            id_to_alias, base_alias_to_id = generate_evidence_alias_maps(document)
            alias_to_id = dict(base_alias_to_id)
            if is_operational:
                prefix = "E"
                for sec in document.all_evidence_sections:
                    if sec.source_record_id and str(sec.source_record_id).strip().isdigit():
                        rec_alias = f"{prefix}{int(sec.source_record_id):03d}"
                        if rec_alias not in alias_to_id:
                            alias_to_id[rec_alias] = sec.evidence_id

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

            system_content = f"""{COMPACT_SYSTEM_SECURITY_DIRECTIVE}
TASK: SPECIFIC TOPIC DETECTION (Version: {prompt_version})
You must determine if the following topic is discussed or present in the forensic evidence:
{topic_spec}

DECISION RULES:
1. DEFAULT: If the target topic is NOT discussed in the conversation, return 'ABSENT' and 'evidence_ids': []. Do NOT make far-fetched connections.
2. If the target topic is clearly discussed in the conversation, return 'PRESENT' and cite exact evidence aliases in 'evidence_ids'.
3. If ambiguous or fragmentary, return 'UNCERTAIN'.
{rule_4_cite}
- CRITICAL INVARIANT: You can NEVER output 'PRESENT' with an empty 'evidence_ids' list. If there is no specific evidence to cite, the decision MUST be 'ABSENT'.
- CRITICAL INVARIANT: When decision is 'ABSENT', 'evidence_ids' MUST be [] (empty array). NEVER list inspected messages when decision is 'ABSENT'.
- Placeholders like '<Media omessi>' contain no textual content and MUST NEVER be cited or considered as evidence of any topic. Do NOT speculate about what omitted media might contain.
- Provide a short, objective rationale in Italian explaining the decision (at least one sentence). DO NOT leave rationale empty.

CONCRETE EXAMPLES:
- If target is 'Mutui e acquisto casa' and text says 'Ci vediamo alle 18 per correre':
  Output: {{"decision": "ABSENT", "evidence_ids": [], "rationale": "I messaggi parlano di corsa; non viene menzionato alcun mutuo o compravendita immobiliare."}}
- If target is 'Sport e corsa' and text says 'Ci vediamo alle 18 per correre':
  Output: {{"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "I partecipanti si accordano per andare a correre."}}
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
    Do NOT make far-fetched, speculative connections.
3. Return decision 'UNCERTAIN' if the evidence is ambiguous, partial, or insufficient to decide. You may cite ambiguous evidence_ids.
{rule_4_cite}
5. Provide a short, objective rationale explaining the decision in Italian (at least one sentence). DO NOT leave rationale empty.
"""

        if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
            system_content += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Understand the original language but respond with rationale in Italian.\n"
        elif strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        is_live_lms_client = self.client.__class__.__name__ in ("LmStudioClient", "AuditingLmStudioClient", "RecordingLlmClient")
        if is_operational and is_live_lms_client:
            user_content, compact_id_to_alias, compact_alias_to_id = serialize_document_compact_jsonl(
                document,
                strategy=strategy,
                translation=translation,
                evidence_id_map=id_to_alias if use_aliases else None,
            )
            if not id_to_alias and use_aliases:
                id_to_alias = compact_id_to_alias
                alias_to_id = compact_alias_to_id
        else:
            user_content = serialize_document_for_llm(
                document,
                strategy=strategy,
                translation=translation,
                evidence_id_map=id_to_alias if use_aliases else None,
            )

        topic_desc_block = f"\nDescription: {topic.description}" if topic.description else ""
        user_message_content = (
            f"=== TARGET TOPIC TO DETECT ===\n"
            f"Label: '{topic.label}'{topic_desc_block}\n"
            f"===============================\n\n"
            f"{user_content}\n\n"
            f"=== TASK EVALUATION ===\n"
            f"Carefully evaluate whether the specific target topic '{topic.label}' is explicitly discussed in the evidence above.\n"
            f"- If the conversation does not explicitly discuss '{topic.label}', return decision 'ABSENT' with 'evidence_ids': [].\n"
            f"- Return 'PRESENT' ONLY if explicit written text directly discusses '{topic.label}'.\n"
            "- CRITICAL: Do NOT infer this topic from unrelated discussions (e.g. casual outings or sports).\n"
            "- CRITICAL: Do NOT cite '<Media omessi>' or infer the topic from omitted media.\n"
            "Provide concise 'rationale' in Italian. Return ONLY a valid JSON object matching the schema."
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message_content},
        ]

        if is_operational and context_length > 0:
            eff_out = min(max_tokens, 512) if max_tokens else 512
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
        # e fallback GBNF automatico (simmetrico e resiliente alle limitazioni dei backend locali)
        max_attempts = 2 if is_operational else 1
        current_schema = TOPIC_DETECTION_SCHEMA
        current_schema_strict = True
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
                call_kwargs = {
                    "messages": messages,
                    "model_id": target_model,
                    "schema": current_schema,
                    "temperature": temperature,
                    "seed": seed,
                    "timeout_seconds": timeout_seconds,
                    "max_tokens": min(max_tokens, 512) if max_tokens else 512,
                }
                if hasattr(self.client, "chat_completion"):
                    import inspect
                    sig = inspect.signature(self.client.chat_completion)
                    if "schema_strict" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                        call_kwargs["schema_strict"] = current_schema_strict
                response = self.client.chat_completion(**call_kwargs)
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

                try:
                    decision, evidence_ids, rationale = validate_topic_detection_payload(
                        raw_text=response.content,
                        valid_evidence_ids=valid_evidence_ids,
                        alias_map=alias_to_id if use_aliases else None,
                    )
                except AiStructuredOutputError as str_err:
                    err_low = str(str_err).lower()
                    if is_operational and ("elementi duplicati" in err_low or "deve essere vuoto" in err_low):
                        import json
                        from ai.structured import parse_and_validate_json
                        data = parse_and_validate_json(response.content)
                        raw_eids = data.get("evidence_ids", [])
                        if "elementi duplicati" in err_low and isinstance(raw_eids, list):
                            data["evidence_ids"] = list(dict.fromkeys(raw_eids))
                            decision, evidence_ids, rationale = validate_topic_detection_payload(
                                raw_text=json.dumps(data),
                                valid_evidence_ids=valid_evidence_ids,
                                alias_map=alias_to_id if use_aliases else None,
                            )
                        elif (
                            "deve essere vuoto" in err_low
                            and data.get("decision") == "ABSENT"
                            and isinstance(raw_eids, list)
                            and len(raw_eids) >= 2
                            and len(document.bundles) >= 2
                        ):
                            data["evidence_ids"] = []
                            decision, evidence_ids, rationale = validate_topic_detection_payload(
                                raw_text=json.dumps(data),
                                valid_evidence_ids=valid_evidence_ids,
                                alias_map=alias_to_id if use_aliases else None,
                            )
                        else:
                            raise
                    else:
                        raise

                if is_operational and decision == TopicDecision.PRESENT:
                    cited_secs = [s for s in document.all_evidence_sections if s.evidence_id in evidence_ids]
                    has_substantive_text = any(
                        s.text and s.text.strip() not in ("<Media omessi>", "<media omessi>", "<audio omesso>")
                        for s in cited_secs
                    )
                    if not has_substantive_text:
                        raise AiInvalidEvidenceCitationError(
                            "Le evidenze citate consistono esclusivamente in placeholder di media omessi privi di testo (<Media omessi>). "
                            "I placeholder multimediali omessi non possono costituire prova di un topic. "
                            "Se non vi è discussione testuale esplicita, la decisione deve essere ABSENT con evidence_ids vuota.",
                            invalid_evidence_ids=tuple(evidence_ids),
                        )

                last_error = None
                break

            except Exception as exc:
                last_error = exc
                if response is not None and getattr(response, "raw_response", None) and isinstance(response.raw_response, Mapping):
                    if not getattr(exc, "finish_reason", None):
                        setattr(exc, "finish_reason", response.raw_response.get("finish_reason"))
                if getattr(response, "prompt_tokens", None) is not None and not getattr(exc, "prompt_tokens", None):
                    setattr(exc, "prompt_tokens", response.prompt_tokens)
                if getattr(response, "completion_tokens", None) is not None and not getattr(exc, "completion_tokens", None):
                    setattr(exc, "completion_tokens", response.completion_tokens)

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
                    if cat == "INFERENCE_BACKEND_ERROR":
                        if len(document.bundles) > 8:
                            raise
                        # GBNF crash fallback su blocchi piccoli: su retry aggiriamo il parser GBNF C++ di llama.cpp
                        # usando una direttiva esplicita di formato JSON validata rigorosamente lato Python
                        current_schema = None
                        orig_u = messages[1].get("content", "") if len(messages) > 1 else ""
                        messages = [
                            messages[0],
                            {
                                "role": "user",
                                "content": (
                                    f"{orig_u}\n\n"
                                    "[CRITICAL FORMAT DIRECTIVE - LM STUDIO COMPATIBILITY]\n"
                                    "Output ONLY a valid JSON object starting with { and ending with } conforming to schema:\n"
                                    '{"decision": "PRESENT"|"ABSENT"|"UNCERTAIN", "evidence_ids": ["alias"], "rationale": "string"}\n'
                                    "Do NOT output markdown fences, greetings, or text outside the JSON object."
                                ),
                            },
                        ]
                    elif code and 400 <= code < 500 and code != 429:
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
                    if "placeholder" in str(exc).lower() or "<media omessi>" in str(exc).lower():
                        correction_msg = (
                            f"CORRECTION DIRECTIVE: {str(exc)} "
                            "Strict forensic requirement: Omitted media placeholders contain NO text and CANNOT be used as proof of any topic. "
                            "If the conversation does not explicitly discuss the topic in written text, decision MUST be 'ABSENT' with 'evidence_ids': []. "
                            "Re-evaluate and return the corrected JSON matching the schema."
                        )
                    else:
                        invalid_list = getattr(exc, "invalid_evidence_ids", ())
                        invalid_str = ", ".join(f"'{x}'" for x in invalid_list) if invalid_list else "non conformi"
                        if use_aliases:
                            valid_keys = sorted(alias_to_id.keys()) if alias_to_id else []
                            valid_summary = ", ".join(f"'{k}'" for k in valid_keys)
                            correction_msg = (
                                f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence alias values: [{invalid_str}]. "
                                f"Strict forensic requirement: You MUST cite ONLY exact alias identifiers assigned to THIS chunk ({valid_summary}). "
                                "Do NOT invent IDs, do NOT cite numbers not in this block, do NOT append extra text or metadata to alias strings. "
                                "Re-evaluate and return the corrected JSON matching the schema."
                            )
                        else:
                            valid_keys = sorted(valid_evidence_ids)
                            valid_summary = ", ".join(f"'{k}'" for k in valid_keys)
                            correction_msg = (
                                f"CORRECTION DIRECTIVE: In your previous response, you cited invalid evidence_id values: [{invalid_str}]. "
                                f"Strict forensic requirement: You MUST cite ONLY exact evidence_id strings that appear literally in this chunk ({valid_summary}). "
                                "Do NOT invent IDs, do NOT cite IDs from other blocks or conversations. "
                                "Re-evaluate and return the corrected JSON matching the schema."
                            )

                    orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                    compact_msgs: list[dict[str, str]] = [
                        messages[0],
                        {
                            "role": "user",
                            "content": f"{orig_user_content}\n\n[DIRETTIVA CORRETTIVA DI PRECISIONE FORENSE]\n{correction_msg}",
                        },
                    ]
                    reserved_out = min(max_tokens or 384, 512)
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
                        f"CORRECTION DIRECTIVE: {str(exc)}. "
                        "Strict forensic requirement: If the topic is not directly discussed or evidenced in the conversation, "
                        "decision MUST be 'ABSENT' with 'evidence_ids': []. "
                        "If decision is 'PRESENT', you MUST cite at least one valid evidence alias ID in 'evidence_ids'. "
                        "Provide a concise rationale in Italian and return ONLY valid JSON matching the schema."
                    )
                    orig_user_content = messages[1].get("content", "") if len(messages) > 1 else ""
                    compact_msgs = [
                        messages[0],
                        {
                            "role": "user",
                            "content": f"{orig_user_content}\n\n[DIRETTIVA FORMATO JSON]\n{struct_msg}",
                        },
                    ]
                    reserved_out = min(max_tokens or 384, 512)
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
        timeout_seconds: float = 120.0,
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
            id_to_alias, base_alias_to_id = generate_evidence_alias_maps(document)
            alias_to_id = dict(base_alias_to_id)
            if is_op:
                prefix = "E"
                for sec in document.all_evidence_sections:
                    if sec.source_record_id and str(sec.source_record_id).strip().isdigit():
                        rec_alias = f"{prefix}{int(sec.source_record_id):03d}"
                        if rec_alias not in alias_to_id:
                            alias_to_id[rec_alias] = sec.evidence_id

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

        cite_item = (
            '- "evidence_ids": list of distinct evidence IDs (e.g. ["E001", "E002"], each ID at most once, NO duplicates) from the provided evidence.'
            if use_aliases
            else '- "evidence_ids": list of distinct evidence IDs (each ID at most once, NO duplicates) from the provided evidence.'
        )

        def _make_sys_prompt(num_topics: int) -> str:
            available_aliases_note = ""
            if use_aliases and id_to_alias:
                valid_alias_list = sorted(id_to_alias.values())
                available_aliases_note = (
                    f"\nAvailable evidence IDs in this block: {', '.join(valid_alias_list)}. "
                    f"Cite ONLY the values from 'evidence_id' (e.g. {valid_alias_list[0]}). "
                    f"Do NOT cite 'source_record_id' or numbers not in this evidence list."
                )
            sp = f"""TASK: OPEN TOPIC DISCOVERY (Version: {prompt_version})
You are an expert conversation analyst.
Analyze the provided messages and extract all distinct topics, subjects, and activities discussed.

Instructions:
1. Identify all distinct topics, subjects, and activities discussed in the messages.
2. For each topic, output:
   - "label": title of the topic in Italian (e.g. 'Organizzazione viaggio', 'Preparazione esami', 'Attività sportiva')
   - "short_description": description of what is discussed in Italian
   {cite_item}
3. Every evidence_id cited must be an ID from the provided evidence. Each topic must cite at least one valid evidence_id.{available_aliases_note}
4. Output a JSON object with key "topics" containing the list of discovered topics (at most {num_topics} elements).
"""
            if strategy == AnalysisLanguageStrategy.DIRECT_MULTILINGUAL:
                sp += "\nMULTILINGUAL DIRECTIVE: The evidence may contain multiple languages. Analyze all languages and output labels/descriptions in Italian.\n"
            return sp

        if strategy == AnalysisLanguageStrategy.TRANSLATE_FIRST:
            if translation is None:
                raise ValueError("Per la strategia TRANSLATE_FIRST è obbligatorio fornire un EvidenceTranslationResult.")
            verify_translated_document_size(document, translation, max_characters=8000)

        if is_op:
            user_content, compact_id_to_alias, compact_alias_to_id = serialize_document_compact_jsonl(
                document,
                strategy=strategy,
                translation=translation,
                evidence_id_map=id_to_alias if use_aliases else None,
            )
            if not id_to_alias and use_aliases:
                id_to_alias = compact_id_to_alias
                alias_to_id = compact_alias_to_id
        else:
            user_content = serialize_document_for_llm(
                document,
                strategy=strategy,
                translation=translation,
                evidence_id_map=id_to_alias if use_aliases else None,
            )

        user_message_content = (
            f"{user_content}\n\n"
            f"Based on the conversation evidence above, extract up to {current_topics} main topics and activities discussed.\n"
            "Return ONLY a valid JSON object matching the schema."
        )

        messages = [
            {"role": "system", "content": _make_sys_prompt(current_topics)},
            {"role": "user", "content": user_message_content},
        ]

        # Stima preventiva input tokens e verifica budget rispetto a context_length
        if max_tokens is None:
            max_tokens = 1024

        input_est = estimate_request_input_tokens(messages, schema=TOPIC_DISCOVERY_SCHEMA)
        if is_op and context_length > 0:
            plan = calculate_token_budget_plan(
                context_length=context_length,
                messages=messages,
                reserved_output_tokens=max_tokens,
                schema=TOPIC_DISCOVERY_SCHEMA,
            )
            if not plan.fits_in_context:
                if plan.available_headroom < 128:
                    raise AnalysisInputTooLargeError(
                        f"Il chunk di Topic Discovery è troppo esteso per la finestra di contesto "
                        f"(stima input: {plan.estimated_input_tokens} token su {context_length} disponibili). "
                        f"Il blocco necessita di ulteriore suddivisione."
                    )
                max_tokens = min(max_tokens, plan.available_headroom)

        max_attempts = 2 if is_op else 1
        current_max_tokens = max_tokens
        current_schema = TOPIC_DISCOVERY_SCHEMA
        current_schema_strict = True
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
                call_kwargs = {
                    "messages": messages,
                    "model_id": target_model,
                    "schema": current_schema,
                    "temperature": temperature,
                    "seed": seed,
                    "timeout_seconds": timeout_seconds,
                    "max_tokens": current_max_tokens,
                }
                if hasattr(self.client, "chat_completion"):
                    import inspect
                    sig = inspect.signature(self.client.chat_completion)
                    if "schema_strict" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                        call_kwargs["schema_strict"] = current_schema_strict
                response = self.client.chat_completion(**call_kwargs)
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

                substantive_sec_count = sum(
                    1 for s in document.all_evidence_sections
                    if s.text and s.text.strip() and s.text.strip() != "<Media omessi>" and "crittografati end-to-end" not in s.text
                )
                if len(discovered) == 0 and is_op and substantive_sec_count >= 3 and attempt + 1 < max_attempts:
                    if on_retry_callback:
                        try:
                            on_retry_callback("empty_topic_outcome", attempt + 1)
                        except Exception:
                            pass
                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": '{\n  "topics": []\n}'},
                        {
                            "role": "user",
                            "content": (
                                "CORRECTION DIRECTIVE: No topics were identified, but substantive conversation messages are present in this block. "
                                "Identify what the participants are discussing (e.g. plans, activities, meetings, sports, studies, or subjects) "
                                "and return the distinct topics in the JSON array matching the schema."
                            ),
                        },
                    ]
                    continue

                last_error = None
                break

            except Exception as exc:
                last_error = exc
                if response is not None and getattr(response, "raw_response", None) and isinstance(response.raw_response, Mapping):
                    if not getattr(exc, "finish_reason", None):
                        setattr(exc, "finish_reason", response.raw_response.get("finish_reason"))
                if getattr(response, "prompt_tokens", None) is not None and not getattr(exc, "prompt_tokens", None):
                    setattr(exc, "prompt_tokens", response.prompt_tokens)
                if getattr(response, "completion_tokens", None) is not None and not getattr(exc, "completion_tokens", None):
                    setattr(exc, "completion_tokens", response.completion_tokens)

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
                    if cat == "INFERENCE_BACKEND_ERROR":
                        if len(document.bundles) > 3:
                            # Su blocchi più grandi rilanciamo affinché lo scheduler di microchunking possa bisezionare
                            raise
                        # Su sotto-blocchi minimi (<= 3 messaggi) tentiamo un retry vincolato con budget compatto e seed incrementato
                        current_topics = max(2, min(current_topics, 2))
                        current_max_tokens = min(current_max_tokens or 384, 384)
                        seed = (seed + 1) if seed is not None else 43
                        orig_u = messages[1].get("content", "") if len(messages) > 1 else ""
                        messages = [
                            {"role": "system", "content": _make_sys_prompt(current_topics)},
                            {
                                "role": "user",
                                "content": (
                                    f"{orig_u}\n\n"
                                    "IMPORTANT: Extract at most 2 concise topics. Keep each short_description under 15 words "
                                    "and list each evidence_id AT MOST ONCE (no duplicates)."
                                ),
                            },
                        ]
                        if on_retry_callback:
                            try:
                                on_retry_callback(attempt + 1, "gbnf_compact_retry")
                            except Exception:
                                pass
                        time.sleep(0.3)
                        continue
                    elif code and 400 <= code < 500 and code != 429:
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
                    if "degenerazione ciclica" in err_msg or "loop di attrattore" in err_msg or "carattere ripetuto" in err_msg:
                        if len(document.bundles) > 3:
                            # Rilancia immediatamente per consentire la bisezione adattiva del microchunk
                            raise
                        current_topics = max(2, min(current_topics, 2))
                        current_max_tokens = min(current_max_tokens or 384, 384)
                        seed = (seed + 1) if seed is not None else 43
                        orig_u = messages[1].get("content", "") if len(messages) > 1 else ""
                        messages = [
                            {"role": "system", "content": _make_sys_prompt(current_topics)},
                            {
                                "role": "user",
                                "content": (
                                    f"{orig_u}\n\n"
                                    "IMPORTANT: Extract at most 2 concise topics. Keep each short_description under 15 words "
                                    "and list each evidence_id AT MOST ONCE (no duplicates)."
                                ),
                            },
                        ]
                        if on_retry_callback:
                            try:
                                on_retry_callback(attempt + 1, "degenerate_compact_retry")
                            except Exception:
                                pass
                        continue

                    obs_f = getattr(exc, "finish_reason", None) or finish_reason
                    if obs_f == "length" or "Unterminated string" in err_msg or "JSONDecodeError" in err_msg or "finish_reason=length" in err_msg:
                        headroom = max(0, context_length - input_est - DEFAULT_SAFETY_MARGIN_TOKENS)
                        if headroom >= int((current_max_tokens or 1024) * 1.5):
                            current_max_tokens = int((current_max_tokens or 1024) * 1.5)
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
                            f"CORRECTION DIRECTIVE: Your previous output had an issue: {err_msg}. "
                            f"Please provide ONLY valid JSON with a 'topics' array (max {current_topics} items), "
                            f"each containing 'label', 'short_description', and 'evidence_ids' (distinct IDs, with NO duplicates within each topic)."
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
            "serialization_format": SERIALIZATION_VERSION_COMPACT_JSONL if is_op else "json_verbose_v1",
            "evidence_citation_protocol": "deterministic_alias_v1" if use_aliases else "direct_evidence_id",
            "evidence_aliases_used": bool(use_aliases),
        }

        return TopicDiscoveryResult(
            topics=discovered,
            provenance_document_id=document.document_id,
            metadata=metadata,
        )
