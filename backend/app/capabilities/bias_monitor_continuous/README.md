# Continuous bias monitor capability

Extends `promotion_gate`. v1's gated audit becomes a meta-agent
watching every decision continuously. Required by `online_learning`.
Risk: low. Rollback-safe — the gated audit is the unchanged fallback.
