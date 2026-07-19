"""Loader for the bias-audit protected-attribute holdout (TAA-28).

The promotion gate's bias audit (``decision_policy/bias_audit.py``) scores
the candidate model on a held-out slice of decisions, each tagged with the
candidate's protected-attribute *segments* (gender/race/age_band/…). Those
attributes are **deliberately kept out of the production warehouse** (see
``graph_writeback.sensitivity`` and ``config/blocked_edge_attributes.yaml``),
so the holdout cannot be derived from app data — it is a *curated,
compliance-signed* set an operator supplies out of band.

Before this seam, the only caller that ran the gate end-to-end
(``nightly_retune.run_for_all_orgs``) passed **no** examples, so even with
auto-apply enabled the bias audit ran on ``[]`` and the gate fail-closed on
a cold-start vacuum every night — the EEOC audit had no live data path
(AUDIT_03 P3-TALI-01 / TAA-28).

This module is that data path. ``load_audit_examples`` resolves a per-org
holdout from a compliance-curated JSON file:

    config/bias_audit_examples/<org-slug>.json
    config/bias_audit_examples/default.json   (org-agnostic fallback)

Each file is a JSON list of objects shaped like ``AuditExample``::

    [
      {
        "features": {"role_fit": 0.81, "skills_depth": 0.7, ...},
        "label": 1,
        "segments": {"gender": "F", "race": "white", "age_band": "30-39"}
      },
      ...
    ]

When no file exists the loader returns ``[]`` and the gate stays
fail-closed (cold start) — identical to today's safe behaviour. The
difference is the seam is now real: an operator who drops in a signed-off
holdout gets the bias audit run on real data, **without** auto-apply being
enabled by default (that remains opt-in via
``Organization.workspace_settings.decision_policy_auto_apply``).

The file lives under git-tracked ``config/`` exactly like
``bias_audit_thresholds.yaml`` so any change carries a PR co-sign.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence

from ..models.organization import Organization
from .bias_audit import AuditExample

logger = logging.getLogger("taali.decision_policy.audit_examples")

# Sibling of config/bias_audit_thresholds.yaml.
EXAMPLES_DIR = (
    Path(__file__).parent.parent.parent / "config" / "bias_audit_examples"
)


def _coerce_example(raw: Any) -> AuditExample | None:
    """Validate one JSON object into an ``AuditExample`` or return None.

    Defensive: a malformed entry must never crash the nightly job — it is
    skipped with a warning so the rest of the holdout still audits.
    """
    if not isinstance(raw, dict):
        return None
    features = raw.get("features")
    segments = raw.get("segments")
    label = raw.get("label")
    if not isinstance(features, dict) or not isinstance(segments, dict):
        return None
    if label is None:
        return None
    try:
        return AuditExample(
            features={str(k): float(v) for k, v in features.items()},
            label=float(label),
            segments={str(k): str(v) for k, v in segments.items()},
        )
    except (TypeError, ValueError):
        return None


def _read_file(path: Path) -> list[AuditExample]:
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "failed to read configured bias-audit holdout error_type=%s",
            type(exc).__name__,
        )
        return []
    if not isinstance(raw, list):
        logger.warning("configured bias-audit holdout is not a JSON list; ignoring")
        return []
    examples: list[AuditExample] = []
    malformed = 0
    for entry in raw:
        ex = _coerce_example(entry)
        if ex is None:
            malformed += 1
            continue
        examples.append(ex)
    if malformed:
        logger.warning(
            "skipped malformed bias-audit examples count=%d",
            malformed,
        )
    return examples


def load_audit_examples(
    org: Organization,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> Sequence[AuditExample]:
    """Resolve the compliance-curated bias-audit holdout for ``org``.

    Resolution order:
    1. ``<base_dir>/<org.slug>.json`` — org-specific holdout.
    2. ``<base_dir>/default.json``    — org-agnostic fallback.
    3. ``[]`` — no holdout configured; the gate fails closed (cold start).

    Returning ``[]`` is the safe default: ``evaluate_auto_apply`` treats an
    empty holdout as a blocker and withholds activation, so an unconfigured
    org behaves exactly as it does today.
    """
    root = Path(base_dir) if base_dir else EXAMPLES_DIR
    slug = str(getattr(org, "slug", "") or "").strip()
    candidates: list[Path] = []
    if slug:
        candidates.append(root / f"{slug}.json")
    candidates.append(root / "default.json")

    for path in candidates:
        if path.exists():
            examples = _read_file(path)
            logger.info(
                "loaded %d bias-audit example(s) for org=%s from %s",
                len(examples), getattr(org, "id", None), path.name,
            )
            return examples
    return []


__all__ = ["EXAMPLES_DIR", "load_audit_examples"]
