# Online learning capability

Within-minutes policy updates from outcomes and overrides, gated by the
same safety bar as nightly. Extends `policy_fitter`. Requires
`causal_policy`, `drift_monitor`, `bias_monitor_continuous`. **Risk:
high — requires `compliance` sign-off before any rollout.**

Rollback-safe: nightly batch is always the fallback writer.
