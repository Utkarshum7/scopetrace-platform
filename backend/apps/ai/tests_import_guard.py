"""
Phase 7a -- import guard: vendor AI SDKs (anthropic, openai) must only ever
be imported from apps/ai/providers/{anthropic,openai}.py.

Implemented as a test, not a ruff rule. pyproject.toml documents ruff's
current rule set (E4/E7/E9/F) as a deliberate, minimal choice -- "broadening
the rule set is a reasonable future increment, not something to do
speculatively now" -- so adding flake8-tidy-imports (TID) project-wide for
one guard would cut against that stated philosophy. A source-scanning test
is self-contained to apps.ai, needs no new lint dependency, and matches how
this codebase already enforces structural invariants elsewhere (e.g.
EmissionRecordQuerySet.delete()/update() being blocked is proven by a test,
not by a linter).

Why this matters: apps.ai.services.gateway.invoke_ai() is the sole
enforcement point for cost/egress/audit (Phase 7's I4/I5 invariants -- see
docs/AI_ARCHITECTURE.md). If a vendor SDK could be imported and called
directly from anywhere else in the codebase, that enforcement could be
silently bypassed. This test makes that structurally impossible to do
unnoticed: any new `import anthropic` / `import openai` outside
providers/anthropic.py / providers/openai.py fails CI immediately.
"""
import ast
from pathlib import Path

from django.test import SimpleTestCase

_BANNED_MODULES = {"anthropic", "openai"}
_ALLOWED_FILES = {"providers/anthropic.py", "providers/openai.py"}
# The guard's actual promise is about APPLICATION code paths (no caller can
# bypass the gateway by importing a vendor SDK directly) -- test files that
# mock provider internals legitimately need the real SDK's exception types
# (e.g. anthropic.APIConnectionError) to simulate a realistic failure, so
# this file is exempt on the same principle _ALLOWED_FILES exists for.
_ALLOWED_TEST_FILES = {"tests_providers_vendor.py"}


class VendorSDKImportGuardTests(SimpleTestCase):
    def test_no_vendor_sdk_imports_outside_providers(self):
        ai_root = Path(__file__).resolve().parent
        violations = []

        for path in ai_root.rglob("*.py"):
            rel = path.relative_to(ai_root).as_posix()
            if rel in _ALLOWED_FILES or rel in _ALLOWED_TEST_FILES:
                continue
            if "/migrations/" in f"/{rel}":
                continue

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # node.level > 0 is a relative import (`from .anthropic
                    # import X`, a local providers/anthropic.py submodule) --
                    # only an absolute `from anthropic import X` (level 0)
                    # can possibly be the vendor package.
                    if node.level > 0:
                        continue
                    modules = [node.module.split(".")[0]] if node.module else []
                else:
                    continue

                banned_hit = _BANNED_MODULES.intersection(modules)
                if banned_hit:
                    violations.append(f"{rel}:{node.lineno} imports {sorted(banned_hit)}")

        self.assertEqual(
            violations, [],
            "Vendor AI SDK imports found outside apps/ai/providers/{anthropic,openai}.py:\n"
            + "\n".join(violations),
        )

    def test_allowed_files_actually_exist(self):
        # Guards against the allowlist silently going stale (e.g. a rename)
        # and the guard test above passing vacuously because it's scanning
        # nothing.
        ai_root = Path(__file__).resolve().parent
        for rel in _ALLOWED_FILES:
            self.assertTrue((ai_root / rel).exists(), f"expected {rel} to exist")
