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

Idempotency note (Phase 7.5 H2): idempotency_key makes a redelivered/
duplicate call safe under Celery's at-least-once (ACKS_LATE) redelivery.
  - No duplicate OK row (Finding 1): a partial UniqueConstraint on
    (organization, idempotency_key) WHERE outcome=OK makes a second OK row
    structurally impossible; if a concurrent duplicate wins the race, this
    call catches the IntegrityError and replays the winner instead.
  - Identical replay (Finding 3): a replayed call returns the SAME parsed
    body and interaction_id as the original, reconstructed from the persisted
    raw response (AIInteraction.response_text, stored only for idempotent
    calls) re-validated against the original schema. This lets a capability
    service (e.g. anomaly_detection -> AIAnnotation) recover after a crash
    BETWEEN the gateway's OK write and its own downstream persistence -- the
    failure mode the pre-7.5 "returns no parsed body" contract lost silently.
Calls WITHOUT an idempotency_key keep the hashes-only privacy contract and
never short-circuit.
"""
import hashlib
import time
from dataclasses import dataclass

from django.db import IntegrityError

from apps.ai.models import AIInteraction
from apps.ai.prompts.registry import render_prompt
from apps.ai.providers.base import LLMProviderError, LLMRequest
from apps.ai.providers.factory import get_llm_provider
from apps.ai.schemas import get_schema, validate_response
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
        prior = _prior_ok_interaction(organization, idempotency_key)
        if prior is not None:
            # Phase 7g: a short-circuited call writes no new AIInteraction
            # row (see this module's own docstring), so it's otherwise
            # invisible to any AIInteraction-based metric -- this counter
            # is the only trace it leaves.
            from apps.ai.services.cache_metrics import record_cache_hit

            record_cache_hit()
            return _replay_result(prior)

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
    parsed, schema_valid = validate_response(response.text, schema)
    outcome = AIInteraction.Outcome.OK if schema_valid else AIInteraction.Outcome.SCHEMA_INVALID
    cost_usd = estimate_cost_usd(policy.provider, response.model_id, response.input_tokens, response.output_tokens)

    try:
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
            # Persist the raw response ONLY for idempotent calls, so a redelivery
            # can replay the identical result (Finding 3). Non-idempotent calls
            # stay hashes-only. Empty string (not the text) when there's no key.
            response_text=response.text if idempotency_key else "",
            schema_valid=schema_valid, input_tokens=response.input_tokens,
            output_tokens=response.output_tokens, cost_usd=cost_usd, latency_ms=response.latency_ms,
            return_result=False,
        )
    except IntegrityError:
        # Phase 7.5 (H2, Finding 1): a concurrent duplicate won the race and
        # already wrote the OK row for this (org, idempotency_key) -- the
        # partial UniqueConstraint refused this second one. Both paid the
        # provider (that waste is what the Finding 2 lock removes), but the
        # caller must still get a single consistent result: replay the winner.
        winner = _prior_ok_interaction(organization, idempotency_key)
        if winner is not None:
            return _replay_result(winner)
        raise

    return AIGatewayResult(
        outcome=outcome,
        interaction_id=str(interaction.id),
        parsed=parsed if schema_valid else None,
        raw_text=response.text,
        error_detail=interaction.error_detail,
    )


def _prior_ok_interaction(organization, idempotency_key):
    """The most recent prior OK interaction for this (org, idempotency_key),
    or None. The single query behind both the pre-lock fast-path short-circuit
    and the in-lock re-check (Phase 7.5 H2)."""
    return (
        AIInteraction.objects.filter(
            organization=organization,
            idempotency_key=idempotency_key,
            outcome=AIInteraction.Outcome.OK,
        )
        .order_by("-created_at")
        .first()
    )


def _replay_result(prior) -> AIGatewayResult:
    """Reconstruct the ORIGINAL result of a prior OK call from its persisted
    row, so a redelivered/duplicate idempotent call returns the identical
    parsed body instead of parsed=None (Phase 7.5 H2, Finding 3).

    Re-validates the persisted raw response against the same schema the
    original call used (schema id/version are recorded in `parameters`), so
    replay goes through the exact same validation path -- it never trusts a
    stored 'parsed' blob. A prior row with no persisted response_text (a
    non-idempotent call, or one written before this field existed) degrades
    gracefully to parsed=None, exactly the pre-7.5 behavior.
    """
    parsed = None
    if prior.response_text:
        try:
            schema = get_schema(
                prior.parameters.get("response_schema_id", ""),
                prior.parameters.get("response_schema_version", 0),
            )
            parsed, _ = validate_response(prior.response_text, schema)
        except KeyError:
            parsed = None
    return AIGatewayResult(
        outcome=prior.outcome,
        interaction_id=str(prior.id),
        parsed=parsed,
        raw_text=prior.response_text,
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
    response_text="",
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
        response_text=response_text,
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
