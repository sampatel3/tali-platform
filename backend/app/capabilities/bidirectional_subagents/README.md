# Bidirectional sub-agents capability

Extends `pre_screen`, `cv_scoring`, `assessment_scoring`, `graph_priors`
with artifact-requesting, counterfactual-proposing, self-explanation
behaviour. Requires `reasoning_orchestrator`. Risk: medium.

## Status
Scaffold only — `enrich()` is a pass-through. The hook point is each
sub-agent's run loop: call `enrich(ctx, sub_agent=self.name, raw_output=...)`
before returning the result.
