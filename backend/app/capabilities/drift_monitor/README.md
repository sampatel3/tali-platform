# Drift & OOD monitor capability

Detects distribution shift and out-of-distribution candidates / roles.
No dependencies. Risk: low. Rollback-safe. Required by
`reasoning_orchestrator` (so OOD admission has a signal source) and
`online_learning` (so updates respect drift).
