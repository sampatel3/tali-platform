# Reasoning orchestrator capability

Replaces (not extends) `orchestrator` when active. Routes sub-agents by
uncertainty, plans workflow, admits OOD. Requires `drift_monitor`.
Risk: medium. Rollback-safe: returning None from the stub falls back to
v1.

## Status
Scaffold only — stub returns None. The dispatch shim in
`app.agent_runtime.orchestrator` checks this capability and falls
through to v1 when it returns None.
