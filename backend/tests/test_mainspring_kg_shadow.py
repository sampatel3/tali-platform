"""KG-query convergence shadow comparator (ADR-0010 cut #5).

Behind a flag, every GraphRAG prior synthesis is also checked against
mainspring's vendored ``KnowledgeGraphBackend`` Protocol and a structural
conformance diff is logged. These lock: no-op when off; the compared vs
shape_gap statuses; that mainspring's stub store is recorded (never called);
and that it never raises (must not affect the live prior).
"""
from __future__ import annotations

import logging

from app.platform.config import settings
from app.services.mainspring_kg_shadow import (
    _TaliGraphReadAdapter,
    _conforms_to_backend_protocol,
    shadow_compare_priors,
)
from vendor.mainspring_kg.base import KnowledgeGraphBackend, Priors

_SHADOW_EVENTS = lambda caplog: [
    r for r in caplog.records if getattr(r, "event", None) == "mainspring_kg_shadow"
]


def _statuses(caplog):
    return [getattr(r, "status", None) for r in _SHADOW_EVENTS(caplog)]


def test_vendored_interface_is_orm_free_and_importable():
    # The vendored seam must construct its read shapes with zero ORM/Session.
    p = Priors.empty(case_id=42)
    assert p.case_id == 42 and p.p_advance == 0.0 and p.confidence == 0.0


def test_tali_adapter_structurally_satisfies_the_protocol():
    # The thin adapter over tali's read surface must satisfy the runtime_checkable
    # Protocol — this is the interface-conformance signal the shadow keys off.
    assert isinstance(_TaliGraphReadAdapter(), KnowledgeGraphBackend)
    assert _conforms_to_backend_protocol() is True


def test_shadow_is_noop_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_KG_SHADOW", False, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.kg.shadow"):
        shadow_compare_priors(
            case_id=1, brand_id=7,
            tali_prior={"p_advance": 0.6, "confidence": 0.5, "neighbour_count": 3},
        )
    assert _SHADOW_EVENTS(caplog) == []


def test_shadow_logs_mainspring_stub_and_never_calls_the_store(caplog, monkeypatch):
    # The production GraphitiBackend is a NotImplementedError stub; the shadow
    # must record mainspring_stub and NOT call it.
    monkeypatch.setattr(settings, "MAINSPRING_KG_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.kg.shadow"):
        shadow_compare_priors(
            case_id=1, brand_id=7,
            tali_prior={"p_advance": 0.6, "confidence": 0.5, "neighbour_count": 3},
        )
    assert "mainspring_stub" in _statuses(caplog)


def test_shadow_logs_compared_when_prior_maps_onto_priors_shape(caplog, monkeypatch):
    # A tali prior carrying every mainspring Priors field maps cleanly → compared.
    monkeypatch.setattr(settings, "MAINSPRING_KG_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.kg.shadow"):
        shadow_compare_priors(
            case_id=1, brand_id=7,
            tali_prior={
                "p_advance": 0.6,
                "p_positive": 0.55,
                "confidence": 0.5,
                "neighbour_count": 3,
            },
        )
    evs = _SHADOW_EVENTS(caplog)
    compared = [e for e in evs if getattr(e, "status", None) == "compared"]
    assert compared, f"expected a compared event, got {_statuses(caplog)}"
    assert compared[0].conforms_protocol is True
    assert compared[0].mappable is True
    assert compared[0].missing_fields == []


def test_shadow_flags_shape_gap_when_prior_misses_a_priors_field(caplog, monkeypatch):
    """Tali's real GraphRAG synthesis has no ``p_positive`` (it only emits
    p_advance + confidence) → logs 'shape_gap' with the missing field, a
    schema-translation gap to close, not a misleading conformance pass."""
    monkeypatch.setattr(settings, "MAINSPRING_KG_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.kg.shadow"):
        shadow_compare_priors(
            case_id=1, brand_id=7,
            tali_prior={"p_advance": 0.6, "confidence": 0.5, "neighbour_count": 3},
        )
    evs = _SHADOW_EVENTS(caplog)
    gaps = [e for e in evs if getattr(e, "status", None) == "shape_gap"]
    assert gaps, f"expected a shape_gap event, got {_statuses(caplog)}"
    assert "p_positive" in gaps[0].missing_fields


def test_shadow_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_KG_SHADOW", True, raising=False)
    # Garbage that would break shape inspection must be swallowed, never propagated.
    shadow_compare_priors(case_id=None, brand_id=None, tali_prior="not-a-dict")
    shadow_compare_priors(case_id="x", brand_id=object(), tali_prior=None)
