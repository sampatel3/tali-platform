# CLAUDE.md — working in the north-star repo

This repo *is* the North Star: the architecture reference that keeps agents aligned
across the platform. Treat it as load-bearing.

## Golden rules for changes here

1. **The model is the source of truth.** Edit `architecture/model/model.yaml` first;
   then refresh affected views in `architecture/model/views/`; then record *why* in an
   ADR. Don't change a view without changing the model it derives from.
2. **Architectural changes need an ADR.** Adding/removing a boundary, invariant,
   container, or component → add or supersede an ADR in `architecture/decisions/`.
   Never silently change `NORTH_STAR.md`.
3. **Keep references resolving.** `scripts/validate_model.py` must pass: ids unique,
   every `repo`/`container`/`adr` reference resolves, every ADR is in the index.
4. **No drift.** `scripts/check_drift.py` must pass: any `implementation.paths` you add
   must point at code that actually exists in a verifiable repo.
5. **Keep it dependency-light.** Scripts are stdlib + PyYAML only. Don't add a build
   toolchain or framework (see ADR-0006) without an ADR justifying it.
6. **Resync after content changes.** Changing `NORTH_STAR.md`, `model.yaml`, or any ADR
   changes the digest; run `python agent/sync_north_star.py` so consuming repos update.

## Before you finish

```bash
python scripts/validate_model.py && python scripts/check_drift.py && python agent/sync_north_star.py --print
```

All three must succeed. CI (`.github/workflows/north-star-ci.yml`) runs the same.
