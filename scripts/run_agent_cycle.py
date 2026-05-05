"""Run one autonomous agent cycle against a role from the command line.

Used for QA / debugging without going through Celery + the API.

Usage:
    cd taali-platform
    PYTHONPATH=backend python scripts/run_agent_cycle.py --role-id 42
    PYTHONPATH=backend python scripts/run_agent_cycle.py --role-id 42 --application-id 1234

The script does NOT enable agentic mode for you — it bypasses the
``agentic_mode_enabled`` gate so you can dry-run against a role that
hasn't been switched on yet. It still respects the paused state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _resolve_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.normpath(os.path.join(here, "..", "backend"))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one agent cycle.")
    parser.add_argument("--role-id", type=int, required=True)
    parser.add_argument("--application-id", type=int, default=None)
    parser.add_argument(
        "--trigger",
        type=str,
        default="manual",
        choices=("manual", "event", "cron"),
    )
    args = parser.parse_args()

    _resolve_paths()

    from app.agent_runtime.orchestrator import run_cycle
    from app.models.role import Role
    from app.platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == args.role_id).first()
        if role is None:
            print(f"role {args.role_id} not found", file=sys.stderr)
            return 1
        if role.agent_paused_at is not None:
            print(
                f"role {args.role_id} agent is paused: {role.agent_paused_reason or 'unspecified'}",
                file=sys.stderr,
            )
            return 2

        run = run_cycle(
            db,
            role=role,
            trigger=args.trigger,
            application_id=args.application_id,
        )
        db.commit()
        print(
            json.dumps(
                {
                    "agent_run_id": int(run.id),
                    "status": str(run.status),
                    "input_tokens": int(run.input_tokens or 0),
                    "output_tokens": int(run.output_tokens or 0),
                    "decisions_emitted": int(run.decisions_emitted or 0),
                    "tools_called": run.tools_called,
                    "error": run.error,
                },
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
