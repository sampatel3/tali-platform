### You are legacy (`tali-platform`)

This repo predates the substrate/brand split. It is the **source** capabilities are
drained *from*, never a place to grow new architecture:

- **Don't extend the monolith.** New cross-cutting capability belongs in `mainspring`;
  new brand surface belongs in a brand repo. Add here only what's needed to keep the
  legacy product running.
- **When you touch a capability, prefer migrating it.** Moving the metered client, the
  decision engine, or billing into `mainspring` is the intended direction. When you
  do, update the North Star model's `implementation` mapping to the new home.
- **You are the drift checker's verifiable anchor today.** Many North Star
  `implementation` paths still point here (that's expected). Keep them honest: if you
  move or rename one of those files, update `model.yaml` in the same change or
  `check_drift.py` will fail.
- **The invariants still hold here.** Metering, per-org decision serialization, and
  per-brand migrations are not waived just because this is legacy.
