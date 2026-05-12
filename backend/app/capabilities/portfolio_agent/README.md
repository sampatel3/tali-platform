# Portfolio capability

Adds cohort-level reasoning to the policy engine: team shape, pipeline
balance, candidate-set composition. Extends `policy_engine`. No
dependencies. Risk: low. Rollback-safe.

## Status
Scaffold only — `contribute()` returns flat zero features when the flag
is on. Production rollout begins by replacing those zeros with computed
shape signals.

## Toggle
```sql
UPDATE capability_flags SET enabled = true
WHERE capability = 'portfolio_agent' AND organization_id = $1;
```
