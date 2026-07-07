"""
invoke_ai() -- the SOLE enforcement point for every AI call in this codebase
(Phase 7's I4/I5/I6 invariants, see docs/AI_ARCHITECTURE.md). Every call
flows through, in order: idempotency short-circuit -> policy resolution ->
budget check -> egress/provider-allowed check -> schema lookup -> redact +
render prompt -> provider call -> response schema validation -> AIInteraction
write. No caller constructs a provider, calls apps.ai.prompts.registry, or
writes AIInteraction directly -- this module is the only code that composes
those pieces, and apps.ai.tests_import_guard structurally prevents a caller
from reaching a vendor SDK around it.

Phase 7's I1/I2 invariants (advisory-only, no governed-data mutation) are
enforced by ABSENCE, not by a check here: this module has no import of, and
no write path to, apps.ingestion.models.EmissionRecord or
apps.carbon.models.EmissionCalculation. There is no function in this file
that could mutate a governed business field even if a caller wanted it to.
A future feature milestone (7b+) that wants an AI suggestion to become a
real business-data change must go through the EXISTING governed workflow
(apps.ingestion.services.workflow / the calculation recalculation path),
never through this gateway.

Idempotency note: idempotency_key prevents a redelivered/duplicate call from
re-billing the provider (a real cost/audit safeguard for Celery's at-least-
once redelivery, matching this codebase's ACKS_LATE contract). It is NOT a
data cache -- a replayed call returns the prior outcome and interaction_id
but no parsed body (this module deliberately never persists raw response
text; see AIInteraction's own docstring for why). A feature milestone that
needs to recover a prior result's *data* on redelivery should look it up in
its own table (e.g. an AISuggestion row keyed by the same idempotency_key),
not rely on this gateway to hand it back.
"""
import hashlib
import json
import time
from dataclasses import dataclass

import jsonschema

from apps.ai.models import AIInteraction
from apps.ai.prompts.registry import render_prompt
from apps.ai.providers.base import LLMProviderError, LLMRequest
from apps.ai.providers.factory import get_llm_provider
from apps.ai.schemas import get_schema
from apps.ai.services.cost import check_budget, estimate_cost_usd
from apps.ai.services.egress import AIEgressBlocked, enforce_provider_allowed, redact_template_vars
from apps.ai.services.policy import resolve_policy

GATEWAY_VERSION = "1"


@dataclass
class AIGatewayResult:
    outcome: str
    interaction_id: str | None
    parsed: dict | None = None
    raw_text: str = ""
    error_detail: str = ""


def invoke_ai(
    *,
    organization,
    actor=None,
    capability: str,
    prompt_name: str,
    template_vars: dict,
    response_schema_id: str,
    response_schema_version: int,
    context_provenance: list | None = None,
    temperature: float = 0.0,
    top_p: float | None = None,
    max_tokens: int = 1024,
    seed: int | None = None,
    stop: list[str] | None = None,
    idempotency_key: str = "",
) -> AIGatewayResult:
    context_provenance = list(context_provenance or [])

    # 1. Idempotency short-circuit.
    if idempotency_key:
        prior = (
            AIInteraction.objects.filter(
                organization=organization,
                idempotency_key=idempotency_key,
                outcome=AIInteraction.Outcome.OK,
            )
            .order_by("-created_at")
            .first()
        )
        if prior is not None:
            return AIGatewayResult(outcome=prior.outcome, interaction_id=str(prior.id))

    # 2. Policy resolution -- global kill switch + per-tenant opt-in.
    policy = resolve_policy(organization)
    parameters = _build_parameters(
        policy.model, temperature, top_p, max_tokens, seed, stop,
        response_schema_id, response_schema_version,
    )

    if not policy.ai_enabled:
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.AI_DISABLED,
            error_detail="AI is disabled (globally or for this organization).",
        )

    # 3. Budget check -- before any provider call, so a refused call never costs anything.
    budget = check_budget(organization, policy.monthly_budget_usd)
    if not budget.ok:
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.BUDGET_EXCEEDED,
            error_detail=f"Monthly budget exceeded: ${budget.spent_usd} spent of ${budget.budget_usd}.",
        )

    # 4. Egress enforcement -- is this provider even reachable under this tier?
    try:
        enforce_provider_allowed(policy.provider, policy.egress_tier)
    except AIEgressBlocked as exc:
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.EGRESS_BLOCKED,
            error_detail=str(exc),
        )

    # 5. Resolve the response schema up front -- before spending any real
    # provider cost on a call whose response could never be validated anyway.
    try:
        schema = get_schema(response_schema_id, response_schema_version)
    except KeyError as exc:
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.ERROR,
            error_detail=str(exc),
        )

    # 6. Redact template_vars (REDACTED tier) BEFORE rendering, so recorded
    # hashes reflect what was actually sent.
    redaction = redact_template_vars(template_vars, policy.egress_tier)
    rendered = render_prompt(
        prompt_name, redaction.values,
        response_schema_id=response_schema_id, response_schema_version=response_schema_version,
    )

    # 7. Construct the provider (cheap, no network -- see each adapter's
    # docstring) and call it.
    try:
        provider = get_llm_provider(provider_name=policy.provider)
    except Exception as exc:  # noqa: BLE001 - ImproperlyConfigured or similar, reported uniformly
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.ERROR,
            error_detail=f"Provider construction failed: {exc}",
            prompt_version=rendered.prompt_version, prompt_template_hash=rendered.template_hash,
            rendered_input_hash=rendered.rendered_input_hash, redaction_applied=redaction.redacted,
        )

    request = LLMRequest(
        prompt=rendered.text, model=policy.model, temperature=temperature,
        top_p=top_p, max_tokens=max_tokens, seed=seed, stop=stop,
    )

    started = time.monotonic()
    try:
        response = provider.complete(request)
    except LLMProviderError as exc:
        return _write_and_return(
            organization=organization, actor=actor, capability=capability,
            provider=policy.provider, model_id=policy.model, parameters=parameters,
            context_provenance=context_provenance, egress_tier=policy.egress_tier,
            idempotency_key=idempotency_key, outcome=AIInteraction.Outcome.ERROR,
            error_detail=str(exc), latency_ms=int((time.monotonic() - started) * 1000),
            prompt_version=rendered.prompt_version, prompt_template_hash=rendered.template_hash,
            rendered_input_hash=rendered.rendered_input_hash, redaction_applied=redaction.redacted,
        )

    # 8. Schema-validate the response -- I1/I6: no un-validated response is
    # ever usable for anything.
    parsed = None
    schema_valid = False
    try:
        candidate = json.loads(response.text)
        jsonschema.validate(candidate, schema)
        parsed = candidate
        schema_valid = True
    except (json.JSONDecodeError, jsonschema.ValidationError):
        schema_valid = False

    outcome = AIInteraction.Outcome.OK if schema_valid else AIInteraction.Outcome.SCHEMA_INVALID
    cost_usd = estimate_cost_usd(policy.provider, response.model_id, response.input_tokens, response.output_tokens)

    interaction = _write_and_return(
        organization=organization, actor=actor, capability=capability,
        provider=policy.provider, model_id=response.model_id, model_snapshot=response.model_snapshot,
        provider_request_id=response.provider_request_id, parameters=parameters,
        context_provenance=context_provenance, egress_tier=policy.egress_tier,
        idempotency_key=idempotency_key, outcome=outcome,
        error_detail="" if schema_valid else "Response failed schema validation.",
        prompt_version=rendered.prompt_version, prompt_template_hash=rendered.template_hash,
        rendered_input_hash=rendered.rendered_input_hash, redaction_applied=redaction.redacted,
        response_hash=hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
        schema_valid=schema_valid, input_tokens=response.input_tokens,
        output_tokens=response.output_tokens, cost_usd=cost_usd, latency_ms=response.latency_ms,
        return_result=False,
    )

    return AIGatewayResult(
        outcome=outcome,
        interaction_id=str(interaction.id),
        parsed=parsed if schema_valid else None,
        raw_text=response.text,
        error_detail=interaction.error_detail,
    )


def _build_parameters(model, temperature, top_p, max_tokens, seed, stop, response_schema_id, response_schema_version) -> dict:
    return {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "seed": seed,
        "stop": stop,
        "response_schema_id": response_schema_id,
        "response_schema_version": response_schema_version,
    }


def _write_and_return(
    *,
    organization,
    actor,
    capability,
    provider,
    model_id,
    parameters,
    context_provenance,
    egress_tier,
    idempotency_key,
    outcome,
    error_detail,
    model_snapshot="",
    provider_request_id="",
    prompt_version=None,
    prompt_template_hash="",
    rendered_input_hash="",
    redaction_applied=False,
    response_hash="",
    schema_valid=None,
    input_tokens=None,
    output_tokens=None,
    cost_usd=None,
    latency_ms=None,
    return_result=True,
):
    """Writes exactly one AIInteraction row -- the single place this happens
    across the entire gateway flow, whether the call was refused before
    reaching a provider or completed all the way through. Returns an
    AIGatewayResult for early-exit call sites (return_result=True); the
    final success/schema-invalid path builds its own richer result and
    passes return_result=False to get the raw model instance back instead.
    """
    interaction = AIInteraction.objects.create(
        organization=organization,
        actor=actor,
        capability=capability,
        provider=provider,
        model_id=model_id,
        model_snapshot=model_snapshot,
        provider_request_id=provider_request_id,
        prompt_version=prompt_version,
        prompt_template_hash=prompt_template_hash,
        rendered_input_hash=rendered_input_hash,
        context_provenance=context_provenance,
        parameters=parameters,
        response_hash=response_hash,
        schema_valid=schema_valid,
        outcome=outcome,
        error_detail=error_detail,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        egress_tier_applied=egress_tier,
        redaction_applied=redaction_applied,
        idempotency_key=idempotency_key,
        gateway_version=GATEWAY_VERSION,
    )
    if not return_result:
        return interaction
    return AIGatewayResult(outcome=outcome, interaction_id=str(interaction.id), error_detail=error_detail)
