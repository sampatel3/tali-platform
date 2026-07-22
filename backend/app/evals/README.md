# AI eval registry

`registry.json` is the merge gate for every metered `Feature`. It keeps the
default PR check deterministic and free of provider charges.

## Coverage tiers

- `grounded_truth`: an independent expected result exists. Register the exact
  pytest node(s) in `test_cases` and state the bounded claim they prove in
  `ground_truth_scope`.
- `behavioral` or `contract`: deterministic behavior is tested, but semantic
  correctness is not claimed. A `critical` feature also needs a
  `reviewed_semantic_exemption` explaining the known gap.
- `infrastructure_exemption`: the feature is transport/infrastructure and its
  semantic truth is evaluated by a calling feature. State why.

When adding or changing an AI feature, update its risk and coverage in the same
PR, add the smallest deterministic oracle that would catch the defect, then run:

```bash
cd backend
python scripts/check_ai_eval_coverage.py
python -m pytest -q tests/evals/test_ai_eval_registry.py
```

CI executes the registered targets with model, embedding, and sandbox provider
credentials blank, including Anthropic/Claude, OpenAI, Voyage, and E2B. Live,
model-graded, or paid evals are deliberately outside this gate and must not be
added or run without explicit approval.
