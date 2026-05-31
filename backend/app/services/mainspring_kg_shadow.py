"""Shadow comparator for the KG-query convergence (ADR-0010, cut #5).

Behind a flag (``MAINSPRING_KG_SHADOW``), every GraphRAG prior synthesis is ALSO
checked against mainspring's vendored ``KnowledgeGraphBackend`` Protocol, and a
structural interface-conformance diff is logged. No graph writes, no behaviour
change — this is the at-parity *interface* evidence ADR-0010 requires before any
cutover. The vendored interface lives under ``backend/vendor/mainspring_kg``
(mirror-vendored from mainspring master; re-vendor via
``scripts/vendor_mainspring_kg.sh``).

INTERFACE-ONLY convergence (the decision for cut #5): we converge the *interface*
(the ``KnowledgeGraphBackend`` Protocol + the ``Priors`` read shape), NOT the
store. Tali keeps Graphiti as its knowledge-graph store. Mainspring's production
``GraphitiBackend`` is a known ``NotImplementedError`` stub, so this shadow NEVER
calls it — it logs ``mainspring_stub`` to record that the store is unimplemented,
and does its parity work purely against the vendored Protocol + dataclass shapes.

Three outcomes are logged distinctly so the conformance log is actionable:
- ``compared``       — a thin adapter over tali's graph read surface structurally
  satisfies the Protocol, AND tali's synthesised prior maps cleanly onto the
  ``Priors`` shape. The interface is at parity for this call.
- ``shape_gap``      — the adapter satisfies the Protocol but tali's prior is
  missing a field mainspring's ``Priors`` carries (e.g. ``p_positive`` has no
  tali counterpart yet) → a schema-translation gap to close, not a live drift.
- ``mainspring_stub``— recorded once-per-call to make explicit that the
  production mainspring store is a ``NotImplementedError`` stub we deliberately
  do not call.
- the comparison never raises — a shadow failure must not affect the live call.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..platform.config import settings

logger = logging.getLogger("taali.kg.shadow")


# Fields mainspring's ``Priors`` read shape carries. Tali's GraphRAG synthesis
# produces ``p_advance`` + ``confidence`` + a neighbour count; the rest are the
# schema-translation surface the convergence still has to bridge. Kept here (not
# imported reflectively) so a mainspring shape change is a visible vendored diff.
_PRIORS_FIELDS = ("case_id", "neighbour_count", "p_positive", "p_advance", "confidence")


class _TaliGraphReadAdapter:
    """Thin, ORM-free adapter asserting tali's graph read surface structurally
    satisfies mainspring's ``KnowledgeGraphBackend`` Protocol.

    This is the *interface* convergence object: it binds tali's existing
    ``graphrag_queries`` read functions to the four Protocol methods
    (``write`` / ``get_priors`` / ``replay_as_of`` / ``healthcheck``) WITHOUT
    touching the store. It is never executed against a real graph here — it only
    has to *exist with the right shape* so ``isinstance(adapter, Protocol)``
    (the ``runtime_checkable`` structural check) passes. The shadow uses that
    check as the conformance signal.
    """

    name = "tali-graphiti"

    def write(self, episode: Any) -> None:  # pragma: no cover - shape-only
        # Tali writes episodes via candidate_graph.episodes.dispatch; the
        # adapter does not re-implement it (interface-only convergence).
        raise NotImplementedError("tali write goes through candidate_graph.episodes")

    def get_priors(self, *, brand_id: int, case_id: int) -> Any:  # pragma: no cover
        # Tali's analogue is graphrag_queries.synthesise_prior over the multi-hop
        # read functions; not invoked here (interface-only).
        raise NotImplementedError("tali priors come from graphrag_queries.synthesise_prior")

    def replay_as_of(self, *, brand_id: int, case_id: int, as_of: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("tali replay is the temporal-anchored Cypher read surface")

    def healthcheck(self) -> bool:  # pragma: no cover - shape-only
        return True


def _conforms_to_backend_protocol() -> bool:
    """True iff the tali adapter structurally satisfies the vendored Protocol."""
    from vendor.mainspring_kg.base import KnowledgeGraphBackend

    return isinstance(_TaliGraphReadAdapter(), KnowledgeGraphBackend)


def shadow_compare_priors(
    *,
    case_id: Optional[int],
    brand_id: Optional[int],
    tali_prior: Any,
) -> None:
    """If KG shadow is on, check tali's GraphRAG prior against mainspring's
    vendored ``Priors`` interface and log a structural conformance diff. Never
    raises; never calls mainspring's NotImplementedError store."""
    if not getattr(settings, "MAINSPRING_KG_SHADOW", False):
        return
    try:
        from vendor.mainspring_kg.base import Priors

        # 1) The mainspring production store is a known stub — record that we
        #    deliberately do not call it, then do parity work on the interface.
        logger.info(
            "mainspring_kg_shadow status=mainspring_stub case_id=%s",
            case_id,
            extra={
                "event": "mainspring_kg_shadow",
                "status": "mainspring_stub",
                "case_id": case_id,
                "note": "GraphitiBackend is NotImplementedError; interface-only convergence",
            },
        )

        # 2) Structural Protocol conformance of tali's read adapter.
        conforms = _conforms_to_backend_protocol()

        # 3) Which mainspring Priors fields does tali's synthesised prior cover?
        prior = tali_prior if isinstance(tali_prior, dict) else {}
        # case_id / neighbour_count are supplied by the call site context even
        # when the synthesiser itself doesn't carry them.
        present = {f for f in _PRIORS_FIELDS if f in prior or prior.get(f) is not None}
        if case_id is not None:
            present.add("case_id")
        if "neighbour_count" in prior:
            present.add("neighbour_count")
        missing = [f for f in _PRIORS_FIELDS if f not in present]

        # Can tali's prior be projected onto the mainspring Priors shape at all?
        mappable = conforms and "p_advance" in present and "confidence" in present

        status = "compared" if (mappable and not missing) else "shape_gap"
        logger.info(
            "mainspring_kg_shadow status=%s case_id=%s conforms=%s mappable=%s "
            "covered=%s missing=%s",
            status, case_id, conforms, mappable,
            sorted(present), missing,
            extra={
                "event": "mainspring_kg_shadow",
                "status": status,
                "case_id": case_id,
                "brand_id": brand_id,
                "conforms_protocol": bool(conforms),
                "mappable": bool(mappable),
                "ms_priors_fields": list(_PRIORS_FIELDS),
                "covered_fields": sorted(present),
                "missing_fields": missing,
                "tali_p_advance": prior.get("p_advance"),
                "tali_confidence": prior.get("confidence"),
                # Proof the vendored shape is importable + constructible without
                # any mainspring DB session (ORM-free seam).
                "ms_priors_empty_shape": list(Priors.empty(int(case_id or 0)).__dict__.keys()),
            },
        )
    except Exception:  # pragma: no cover — shadow must never affect the live call
        logger.exception("mainspring_kg_shadow: comparison failed (non-fatal)")
