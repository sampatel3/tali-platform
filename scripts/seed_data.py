"""Compatibility entry point for the retired all-in-one demo-data seeder.

This file intentionally performs no database imports or writes. The historical
script targeted a removed model layout and embedded an unsafe demo login.
Use one of the supported, scoped and reviewable seed commands named below.
"""

from __future__ import annotations

import sys


GUIDANCE = """The legacy all-in-one demo seeder is unavailable and made no changes.

Supported scoped commands:
  python scripts/seed_tasks_db.py
  cd backend && python -m app.scripts.seed_deeplight_experiments --help
  cd backend && python -m scripts.seed_two_stage_ab --help
"""


class LegacySeederUnavailableError(RuntimeError):
    """Raised for programmatic callers of the retired ``seed`` function."""


def seed() -> None:
    """Fail before opening a database connection or creating any record."""

    raise LegacySeederUnavailableError(GUIDANCE.strip())


def main() -> int:
    print(GUIDANCE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
