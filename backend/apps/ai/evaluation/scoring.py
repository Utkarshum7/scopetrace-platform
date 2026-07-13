"""
Phase 7a.5 -- deterministic (Tier 1) scoring functions. Every function here
is pure and side-effect-free: given an actual and an expected structured
response, return a float in [0.0, 1.0]. No provider call, no randomness, no
LLM judge (that's apps.ai.evaluation.judge, a separate, explicitly-gated
Tier 2 framework).

These exist independently of EvaluationRunner's own happy-path (where a
ReplayProvider trivially returns the fixture's own expected_response, so
the score is always 1.0) -- see tests_scoring.py, which exercises each
function directly against deliberately-mismatched inputs. That's what
proves the scoring math itself is correct, not just that the runner can
call it.
"""


def score_exact_match(actual: dict, expected: dict) -> float:
    """1.0 if actual deep-equals expected, else 0.0. The strictest, and
    default, scorer -- appropriate for a deterministic capability whose
    golden fixture defines the one correct answer (e.g. foundation.selftest)."""
    return 1.0 if actual == expected else 0.0


def score_field_overlap(actual: dict, expected: dict) -> float:
    """Fraction of expected's top-level keys present in actual with an
    equal value. Partial credit for a response that got some fields right
    -- useful for capabilities where an exact match on every field
    (including free-text explanation/rationale strings) is an unreasonably
    strict bar, but getting the structured decision fields (e.g.
    `is_anomalous`, `confidence`) right is what actually matters.

    An empty `expected` scores 1.0 (vacuously — nothing to match), never a
    ZeroDivisionError.
    """
    if not expected:
        return 1.0
    matched = sum(1 for key, value in expected.items() if actual.get(key) == value)
    return matched / len(expected)


def score_required_fields_present(actual: dict, required_fields: list[str]) -> float:
    """Fraction of required_fields present (with any non-None value) in
    actual. Doesn't check VALUE correctness at all -- a coarse "did the
    response even attempt to answer" signal, useful as a floor check
    before a finer-grained scorer runs."""
    if not required_fields:
        return 1.0
    present = sum(1 for field in required_fields if actual.get(field) is not None)
    return present / len(required_fields)
