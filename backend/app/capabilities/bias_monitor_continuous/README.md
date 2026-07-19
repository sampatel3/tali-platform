# Continuous bias monitor compatibility API

This package preserves the historical import path only. The registry marks
`bias_monitor_continuous` unavailable until the per-organisation flag is wired
to scheduling and alert delivery. Calling the compatibility API fails closed;
the separate promotion-gate and aggregate adverse-impact monitors are
unchanged.
