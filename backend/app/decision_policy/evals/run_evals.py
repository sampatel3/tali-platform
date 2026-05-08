"""Golden eval runner.

Builds a fresh in-memory DB, bootstraps an org, and runs each case
through ``engine.evaluate``. Prints a summary; returns non-zero exit
code when any case mismatches.

Usage:
    cd backend && ./.venv/bin/python -m app.decision_policy.evals.run_evals
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _ensure_sqlite_env() -> None:
    os.environ.setdefault(
        "DATABASE_URL", "sqlite:///file:eval-decision-policy?mode=memory&cache=shared"
    )
    os.environ.setdefault("MVP_DISABLE_WORKABLE", "true")
    os.environ.setdefault("MVP_DISABLE_STRIPE", "true")
    os.environ.setdefault("MVP_DISABLE_CELERY", "true")


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str


def _load_cases() -> list[dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover — pyyaml is in requirements
        raise SystemExit(
            "PyYAML is required for the eval harness; install pyyaml"
        ) from exc

    path = Path(__file__).with_name("golden_cases.yaml")
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    return body.get("cases") or []


def _matches_expected(verdict, expected: dict[str, Any]) -> tuple[bool, str]:
    if "decision_type" in expected:
        if verdict.decision_type != expected["decision_type"]:
            return False, (
                f"decision_type expected={expected['decision_type']!r} "
                f"got={verdict.decision_type!r}"
            )
    if "decision_type_in" in expected:
        if verdict.decision_type not in expected["decision_type_in"]:
            return False, (
                f"decision_type expected one of {expected['decision_type_in']} "
                f"got={verdict.decision_type!r}"
            )
    if "decision_point" in expected:
        if verdict.decision_point != expected["decision_point"]:
            return False, (
                f"decision_point expected={expected['decision_point']!r} "
                f"got={verdict.decision_point!r}"
            )
    if "skipped_due_to_manual" in expected:
        if bool(verdict.skipped_due_to_manual) != bool(
            expected["skipped_due_to_manual"]
        ):
            return False, (
                f"skipped_due_to_manual expected={expected['skipped_due_to_manual']} "
                f"got={verdict.skipped_due_to_manual}"
            )
    return True, "ok"


def run_all() -> int:
    _ensure_sqlite_env()

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from app.decision_policy.bootstrap import bootstrap_org
    from app.decision_policy.engine import (
        DecisionInputs,
        ManualAction,
        evaluate,
    )
    from app.models.decision_policy import DecisionPolicy
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.rubric_revision import RubricRevision
    from app.platform.database import Base

    counters = {"rubric_revisions": 0, "decision_policies": 0}

    def _assign(mapper, conn, target):
        name = target.__table__.name
        if target.id is None and name in counters:
            counters[name] += 1
            target.id = counters[name]

    event.listen(RubricRevision, "before_insert", _assign)
    event.listen(DecisionPolicy, "before_insert", _assign)

    engine = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org = Organization(name="Eval Org", slug="eval-org", default_score_threshold=65)
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    bootstrap_org(db, organization_id=int(org.id))

    cases = _load_cases()
    results: list[CaseResult] = []
    for case in cases:
        name = case.get("name") or "unnamed"
        body = case.get("inputs") or {}
        manual = [
            ManualAction(kind=m.get("kind", ""), timestamp_iso="2026-05-08T10:00:00Z")
            for m in (body.get("manual_actions") or [])
        ]
        inputs = DecisionInputs(
            application_id=1,
            role_id=int(role.id),
            organization_id=int(org.id),
            scores=body.get("scores") or {},
            graph_priors=body.get("graph_priors") or {},
            intent=body.get("intent") or {},
            flags=body.get("flags") or {},
            manual_actions=manual,
        )
        verdict = evaluate(inputs, db=db)
        ok, detail = _matches_expected(verdict, case.get("expected") or {})
        results.append(CaseResult(name=name, ok=ok, detail=detail))

    db.close()

    failures = [r for r in results if not r.ok]
    print(f"Ran {len(results)} cases — {len(failures)} failure(s)")
    for r in results:
        marker = "✓" if r.ok else "✗"
        print(f"  {marker} {r.name}: {r.detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_all())
