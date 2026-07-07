"""
Prompt rendering + versioning. Every render_prompt() call resolves (or
registers) an AIPromptVersion row for the exact template content used, so
every AIInteraction can point at the precise prompt version that produced
it -- the reproducibility guarantee this milestone exists to provide (see
docs/AI_ARCHITECTURE.md).

Templates are loaded from apps/ai/prompts/templates/<name>.txt and rendered
with Python's string.Template (safe_substitute), NOT f-strings/.format() or
a full templating engine (Jinja2). Once real features exist (Phase 7b+),
template_vars will carry tenant-derived content; string.Template's
$-placeholder syntax has no expression evaluation or attribute-traversal
surface for that content to exploit, unlike Jinja2 or .format() (which
allows `{obj.__class__...}`-style traversal). safe_substitute() (not
substitute()) additionally never raises on a missing var -- an unfilled
placeholder renders literally rather than crashing a gateway call over a
template/caller mismatch (fail-safe, matching invariant I6).
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path
from string import Template

from apps.ai.models import AIPromptVersion

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass
class RenderedPrompt:
    text: str
    prompt_version: AIPromptVersion
    template_hash: str
    rendered_input_hash: str


def _load_template_text(name: str) -> str:
    path = _TEMPLATES_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"No prompt template file registered for {name!r} at {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(
    name: str,
    template_vars: dict,
    *,
    response_schema_id: str,
    response_schema_version: int,
) -> RenderedPrompt:
    """Loads templates/<name>.txt, registers (or reuses) its AIPromptVersion,
    and renders it against template_vars.

    Note: (name, template_hash) is the identity AIPromptVersion.register()
    keys on -- if this exact template content was already registered under
    `name` with a *different* (response_schema_id, response_schema_version)
    than passed here, the EXISTING row's schema pairing wins silently (the
    template content, not the caller's current arguments, is authoritative
    for "what schema was this actually validated against historically").
    Not exercised by any real caller in Phase 7a (schemas.py has exactly one
    schema); a feature milestone that changes a prompt's expected response
    shape should bump the template content (a new template_hash), not just
    the schema id/version passed at the call site.
    """
    template_text = _load_template_text(name)
    template_hash = hashlib.sha256(template_text.encode("utf-8")).hexdigest()

    prompt_version, _created = AIPromptVersion.register(
        name=name,
        template_text=template_text,
        template_hash=template_hash,
        response_schema_id=response_schema_id,
        response_schema_version=response_schema_version,
    )

    rendered_text = Template(template_text).safe_substitute(**template_vars)
    rendered_input_hash = hashlib.sha256(rendered_text.encode("utf-8")).hexdigest()

    return RenderedPrompt(
        text=rendered_text,
        prompt_version=prompt_version,
        template_hash=template_hash,
        rendered_input_hash=rendered_input_hash,
    )
