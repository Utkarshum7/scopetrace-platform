"""
Egress policy enforcement -- two independent jobs:

1. enforce_provider_allowed(): is the resolved provider even reachable under
   this tenant's egress tier? NO_EGRESS tenants may only use zero-egress
   providers (today: 'echo' and 'replay' -- a real self-hosted/BYO adapter
   is a documented, deferred Phase 7a seam per the finalized Phase 7
   design, not yet a concrete provider).
2. redact_template_vars(): under the REDACTED tier (the platform default),
   scrub common PII-shaped patterns from tenant-derived template_vars
   *before* they are rendered into a prompt -- so the hash recorded on
   AIInteraction reflects what was actually sent, not a pre-redaction value.

RAW is an explicit opt-in that skips redaction entirely; it does not affect
provider reachability.
"""
import re
from dataclasses import dataclass

# A real self-hosted/BYO model, or the 'echo'/'replay' dev+eval providers,
# are the only providers that make zero external network calls.
# Anthropic/OpenAI are never in this set, by construction (they exist
# specifically to call an external vendor API).
ZERO_EGRESS_PROVIDERS = frozenset({"echo", "replay"})

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Phone numbers, account/reference numbers, etc. -- deliberately broad
# (6+ consecutive digits) rather than format-specific, matching this
# milestone's "advisory, best-effort" posture: false positives (redacting a
# harmless long number) are an acceptable cost for a lower false-negative
# rate on an intentionally simple pattern set, since nothing in Phase 7a
# actually sends real tenant content through this path yet (see this
# module's tests for exact coverage; a real feature milestone (7b+) that
# needs richer redaction should extend this, not replace it).
_LONG_DIGIT_RE = re.compile(r"\b\d{6,}\b")


class AIEgressBlocked(Exception):
    """Raised when the resolved provider is not permitted under the tenant's egress tier."""


def enforce_provider_allowed(provider: str, egress_tier: str) -> None:
    if egress_tier == "NO_EGRESS" and provider not in ZERO_EGRESS_PROVIDERS:
        raise AIEgressBlocked(
            f"Egress tier NO_EGRESS blocks provider {provider!r}; only "
            f"{sorted(ZERO_EGRESS_PROVIDERS)} are permitted."
        )


@dataclass(frozen=True)
class RedactionResult:
    values: dict
    redacted: bool


def redact_template_vars(template_vars: dict, egress_tier: str) -> RedactionResult:
    if egress_tier != "REDACTED":
        return RedactionResult(values=template_vars, redacted=False)

    redacted_any = False
    result = {}
    for key, value in template_vars.items():
        if isinstance(value, str):
            scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
            scrubbed = _LONG_DIGIT_RE.sub("[REDACTED_NUMBER]", scrubbed)
            if scrubbed != value:
                redacted_any = True
            result[key] = scrubbed
        else:
            result[key] = value
    return RedactionResult(values=result, redacted=redacted_any)
