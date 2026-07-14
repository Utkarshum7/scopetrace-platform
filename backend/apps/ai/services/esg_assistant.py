"""
Phase 7e -- the esg_assistant capability's own service. Synchronous, not
Celery-queued: unlike anomaly_detection/factor_recommendation/
validation_assistance (all fire-and-forget enrichment of an already-
committed pipeline event), asking a question is a live, user-initiated
request expecting an in-band answer -- the same way invoke_ai() itself
already behaves when called directly. This capability is not in the
ingest -> calculate pipeline at all, so there is no existing workflow
for it to slow down. See ADR 0012.

Read-only with respect to governed data: this module has no write path to
EmissionRecord/EmissionCalculation/EmissionFactor anywhere in this file --
apps.ai.services.esg_context_builder does the only reading, and this
module only ever writes AIConversationMessage rows.
"""
import logging

from apps.ai.models import AIConversationMessage, AIInteraction
from apps.ai.services.esg_context_builder import build_context
from apps.ai.services.gateway import invoke_ai

logger = logging.getLogger(__name__)

ESG_ASSISTANT_SCHEMA_VERSION = 2


def ask_esg_assistant(conversation, question, *, actor=None) -> AIConversationMessage | None:
    """Records the human's question as a USER message, retrieves context
    for `conversation.organization`, calls invoke_ai(), and -- on success
    -- persists the answer as an immutable ASSISTANT message. Returns the
    ASSISTANT message, or None if the gateway call didn't succeed (AI
    disabled, over budget, egress blocked, schema invalid, provider
    error). The USER message is persisted regardless -- the human really
    did ask the question, and a chat transcript that silently drops
    questions on a failed answer would be confusing, not safe. I6:
    fail-safe, not fail-open, for the ANSWER only.
    """
    AIConversationMessage.objects.create(
        organization=conversation.organization,
        conversation=conversation,
        role=AIConversationMessage.Role.USER,
        content=question,
    )

    context = build_context(conversation.organization)

    logger.info(
        "ask_esg_assistant: org=%s conversation=%s question=%r -> invoking gateway",
        conversation.organization_id, conversation.id, question[:80],
    )
    result = invoke_ai(
        organization=conversation.organization,
        actor=actor,
        capability="esg_assistant",
        prompt_name="esg_assistant",
        template_vars={"question": question, "context": context},
        response_schema_id="esg_assistant",
        response_schema_version=ESG_ASSISTANT_SCHEMA_VERSION,
        context_provenance=[str(conversation.id)],
    )
    logger.info(
        "ask_esg_assistant: org=%s conversation=%s outcome=%s parsed=%s",
        conversation.organization_id, conversation.id, result.outcome,
        result.parsed is not None,
    )

    if result.outcome != AIInteraction.Outcome.OK or result.parsed is None:
        logger.info(
            "ask_esg_assistant: returning None (outcome=%s, parsed=%s) -- "
            "assistant_message will be null",
            result.outcome, result.parsed is not None,
        )
        return None

    return AIConversationMessage.objects.create(
        organization=conversation.organization,
        conversation=conversation,
        interaction_id=result.interaction_id,
        role=AIConversationMessage.Role.ASSISTANT,
        content=result.parsed["answer"],
        citations=result.parsed["citations"],
        confidence=result.parsed["confidence"],
        unsupported_claim=result.parsed["unsupported_claim"],
        retrieved_context=context,
    )
