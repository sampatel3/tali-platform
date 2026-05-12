# Causal-mode toggle

Per §12 of `recruitment_system_architecture.md`. Same place in the
pipeline as the fitted policy model (stage 4); different math when the
toggle is on. Tracks "we advanced X because of Y" as a structured
causal claim and validates against downstream outcomes. No new
component — the flag flips a mode on the policy engine.

## Status
Scaffold only — `decide_causal_mode()` returns None when the flag is on.
Real implementation lands when the policy engine has a causal-claims
evidence channel.
