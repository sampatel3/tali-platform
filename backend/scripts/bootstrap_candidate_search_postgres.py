"""Build the disposable PostgreSQL schema used by candidate-search CI tests.

The production Alembic lineage starts by altering a pre-existing platform
schema, so ``alembic upgrade head`` cannot initialize an empty database. This
test-only bootstrap creates the current ORM schema, runs the actual
candidate-search index migration from its parent revision, then stamps the
schema at the repository head. It refuses every database name except the
explicit CI test database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


EXPECTED_DATABASE = "taali_search_test"
SEARCH_PARENT_REVISION = "159_add_sister_roles"
SEARCH_REVISION = "160_add_candidate_search_indexes"


def _test_database_url() -> str:
    raw_url = (os.getenv("TALI_SEARCH_TEST_DATABASE_URL") or "").strip()
    if not raw_url:
        raise SystemExit(
            "TALI_SEARCH_TEST_DATABASE_URL is required for the PostgreSQL "
            "candidate-search gate"
        )
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or url.database != EXPECTED_DATABASE:
        raise SystemExit(
            "refusing to bootstrap outside the explicit PostgreSQL database "
            f"{EXPECTED_DATABASE!r}"
        )
    return raw_url


def main() -> None:
    raw_url = _test_database_url()
    # Configure application imports before they construct module-level engines.
    # The value has already passed the exact test-database guard.
    os.environ["DATABASE_URL"] = raw_url

    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    import app.models  # noqa: F401
    from app.platform.database import Base, engine

    Base.metadata.create_all(bind=engine)
    engine.dispose()

    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    command.stamp(config, SEARCH_PARENT_REVISION)
    command.upgrade(config, SEARCH_REVISION)
    command.stamp(config, "head")


if __name__ == "__main__":
    main()
