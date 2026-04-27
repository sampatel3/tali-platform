"""One-off scoring eval: AWS Data Engineer (role #58) vs. a known-good hire CV.

Loads role #58's spec + structured criteria from the database (read-only),
extracts text from a CV PDF using the same PyPDF2 path as production,
and runs the V4 scoring pipeline against them with no DB writes and no
cache lookup. Prints the breakdown so we can decide whether the rubric
or prompt needs tuning.

Usage::

    cd backend
    ANTHROPIC_API_KEY=... .venv/bin/python scripts/score_known_good_aws_de.py \\
        --role-id 58 \\
        --cv "/Users/sampatel/Downloads/Data Engineer - Rachit Bhargava.pdf"

Requires: ANTHROPIC_API_KEY, DATABASE_URL.

Cost: one V4 scoring call (~3000 output tokens on Haiku ≈ $0.01).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def main() -> int:
    _bootstrap_path()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-id", type=int, default=58)
    parser.add_argument(
        "--cv",
        type=Path,
        default=Path("/Users/sampatel/Downloads/Data Engineer - Rachit Bhargava.pdf"),
    )
    parser.add_argument("--model", type=str, default=None, help="Override scoring model")
    parser.add_argument("--dump-json", action="store_true", help="Also print full match_details JSON")
    args = parser.parse_args()

    from app.models.role import Role
    from app.platform.config import settings
    from app.platform.database import SessionLocal
    from app.services.cv_score_orchestrator import _criteria_payload
    from app.services.document_service import extract_text_from_pdf
    from app.services.fit_matching_service import calculate_cv_job_match_v4_sync
    from app.services.spec_normalizer import normalize_spec

    if not settings.ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    if not args.cv.exists():
        print(f"ERROR: CV file not found: {args.cv}", file=sys.stderr)
        return 2

    cv_bytes = args.cv.read_bytes()
    cv_text = extract_text_from_pdf(cv_bytes)
    if not cv_text.strip():
        print("ERROR: PDF text extraction returned empty string.", file=sys.stderr)
        return 2
    print(f"CV chars extracted: {len(cv_text)}")

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == args.role_id).one_or_none()
        if role is None:
            print(f"ERROR: role id={args.role_id} not found.", file=sys.stderr)
            return 2

        job_spec_text = (role.job_spec_text or "").strip()
        if not job_spec_text:
            print(f"ERROR: role {args.role_id} has empty job_spec_text.", file=sys.stderr)
            return 2

        criteria = _criteria_payload(role)
        spec = normalize_spec(job_spec_text)

        print(f"Role: id={role.id} title={role.title!r}")
        print(f"  job_spec_text chars: {len(job_spec_text)}")
        print(f"  spec.description chars: {len(spec.description)}")
        print(f"  spec.requirements chars: {len(spec.requirements)}")
        print(f"  structured criteria: {len(criteria)}")
        for c in criteria:
            tag = "MUST" if c["must_have"] else "nice"
            print(f"    [{tag}] #{c['id']} {c['text'][:120]}")

        if not criteria:
            print(
                "WARNING: role has no structured criteria; v4 path expects criteria. "
                "Falling through anyway to surface what V4 returns.",
                file=sys.stderr,
            )

        resolved_model = (args.model or settings.resolved_claude_scoring_model or "").strip()
        print(f"\nRunning cv_match_v4 (model={resolved_model})…")

        result = calculate_cv_job_match_v4_sync(
            cv_text=cv_text,
            role_criteria=criteria,
            spec_description=spec.description,
            spec_requirements=spec.requirements,
            api_key=settings.ANTHROPIC_API_KEY,
            model=resolved_model,
        )
    finally:
        db.close()  # never committed — read-only session

    md = result.get("match_details") or {}
    print("\n" + "=" * 72)
    print(f"FINAL SCORE (capped):    {result.get('cv_job_match_score')}")
    print(f"Recommendation:          {md.get('recommendation')}")
    print(f"Model overall (uncapped): {md.get('model_overall_score_100')}")
    print(f"Skills score:            {md.get('model_skills_score_100')}")
    print(f"Experience score:        {md.get('model_experience_score_100')}")
    print(f"Requirements score:      {md.get('model_requirements_score_100')}")
    print(f"Must-have blocked:       {md.get('must_have_blocked')}")
    coverage = md.get("requirements_coverage") or {}
    print(f"Requirements coverage:   {json.dumps(coverage, indent=2)}")

    print("\n--- Per-criterion assessment ---")
    for a in md.get("requirements_assessment") or []:
        status = a.get("status")
        cv_quote = (a.get("cv_quote") or "").strip()
        cid = a.get("criterion_id")
        blocker = "BLOCKER " if a.get("blocker") else ""
        risk = a.get("risk_level") or ""
        ev_type = a.get("evidence_type") or ""
        print(f"  [{status:14}] {blocker}#{cid} risk={risk} type={ev_type}")
        if cv_quote:
            print(f"      quote: {cv_quote[:200]}")
        rec = a.get("screening_recommendation")
        if rec:
            print(f"      rec:   {rec}")

    print("\n--- Matching skills ---")
    for s in md.get("matching_skills") or []:
        print(f"  + {s}")
    print("\n--- Missing skills ---")
    for s in md.get("missing_skills") or []:
        print(f"  - {s}")
    print("\n--- Concerns ---")
    for s in md.get("concerns") or []:
        print(f"  ! {s}")
    print("\n--- Experience highlights ---")
    for s in md.get("experience_highlights") or []:
        print(f"  * {s}")

    summary = md.get("summary")
    if summary:
        print(f"\nSummary:\n  {summary}")

    if args.dump_json:
        print("\n--- Raw match_details JSON ---")
        print(json.dumps(md, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
