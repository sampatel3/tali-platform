"""Golden-case eval harness for the decision policy.

Run via ``python -m app.decision_policy.evals.run_evals`` or import
``run_all`` from a CI script. Each case in ``golden_cases.yaml`` has
a synthetic ``DecisionInputs`` and an ``expected.decision_type`` —
the harness asserts the engine still produces it under the bootstrap
policy.

Wiring to CI is non-blocking for MVP per Phase 3 spec.
"""

from .run_evals import run_all

__all__ = ["run_all"]
