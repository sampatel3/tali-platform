#!/usr/bin/env python3
"""Fail unless the database's Alembic revisions belong to this release tree.

This guard deliberately queries ``alembic_version`` without asking Alembic to
resolve the revision first. That lets it report a useful, secret-free error
when production contains a revision that was deployed from another branch and
is absent from the release being rolled out.
"""

from __future__ import annotations

import argparse
import ast
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


DEFAULT_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


class ProvenanceError(RuntimeError):
    """A safe-to-display release provenance failure."""


@dataclass(frozen=True)
class MigrationGraph:
    parents: dict[str, tuple[str, ...]]
    paths: dict[str, Path]
    head: str
    reachable: frozenset[str]


def _literal_assignment(module: ast.Module, name: str, path: Path) -> object:
    value_node: ast.expr | None = None
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            ):
                value_node = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            value_node = statement.value
    if value_node is None:
        raise ProvenanceError(f"migration {path.name!r} has no literal {name} assignment")
    try:
        return ast.literal_eval(value_node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ProvenanceError(
            f"migration {path.name!r} has a non-literal {name} assignment"
        ) from exc


def load_migration_graph(versions_dir: Path) -> MigrationGraph:
    if not versions_dir.is_dir():
        raise ProvenanceError(f"migration versions directory does not exist: {versions_dir}")

    parents: dict[str, tuple[str, ...]] = {}
    paths: dict[str, Path] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise ProvenanceError(f"could not parse migration {path.name!r}") from exc

        revision = _literal_assignment(module, "revision", path)
        down_revision = _literal_assignment(module, "down_revision", path)
        if not isinstance(revision, str) or not revision.strip():
            raise ProvenanceError(f"migration {path.name!r} has an invalid revision")
        if revision in parents:
            raise ProvenanceError(
                f"duplicate Alembic revision {revision!r} in "
                f"{paths[revision].name!r} and {path.name!r}"
            )

        if down_revision is None:
            revision_parents: tuple[str, ...] = ()
        elif isinstance(down_revision, str) and down_revision:
            revision_parents = (down_revision,)
        elif isinstance(down_revision, (tuple, list)) and all(
            isinstance(parent, str) and parent for parent in down_revision
        ):
            revision_parents = tuple(down_revision)
        else:
            raise ProvenanceError(f"migration {path.name!r} has an invalid down_revision")

        parents[revision] = revision_parents
        paths[revision] = path

    if not parents:
        raise ProvenanceError("release tree contains no Alembic migrations")

    referenced = {parent for revision_parents in parents.values() for parent in revision_parents}
    dangling = sorted(referenced - parents.keys())
    if dangling:
        raise ProvenanceError(
            "release migrations reference absent down_revision(s): " + ", ".join(dangling)
        )

    heads = sorted(parents.keys() - referenced)
    if len(heads) != 1:
        raise ProvenanceError(
            f"release tree must have exactly one Alembic head; found {len(heads)}: "
            + ", ".join(heads)
        )

    reachable: set[str] = set()
    stack = [heads[0]]
    while stack:
        revision = stack.pop()
        if revision in reachable:
            continue
        reachable.add(revision)
        stack.extend(parents[revision])
    unreachable = sorted(parents.keys() - reachable)
    if unreachable:
        raise ProvenanceError(
            "release tree contains revisions unreachable from its head: "
            + ", ".join(unreachable)
        )

    return MigrationGraph(
        parents=parents,
        paths=paths,
        head=heads[0],
        reachable=frozenset(reachable),
    )


def _sqlite_database_path(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme != "sqlite" or parsed.netloc not in {"", "localhost"}:
        raise ProvenanceError("unsupported SQLite database URL")
    raw_path = unquote(parsed.path)
    if raw_path == "/:memory:":
        return ":memory:"
    # SQLAlchemy-style sqlite:///relative.db has one leading slash, while
    # sqlite:////absolute.db has two. Preserve the latter as an absolute path.
    if raw_path.startswith("//"):
        return raw_path[1:]
    if raw_path.startswith("/"):
        return raw_path[1:]
    if raw_path:
        return raw_path
    raise ProvenanceError("SQLite database URL has no database path")


def query_database_revisions(database_url: str) -> tuple[str, ...]:
    scheme = urlsplit(database_url).scheme.lower()
    try:
        if scheme == "sqlite":
            connection = sqlite3.connect(_sqlite_database_path(database_url), timeout=10)
        elif scheme in {"postgres", "postgresql"}:
            import psycopg2  # type: ignore[import-not-found]

            connection = psycopg2.connect(database_url, connect_timeout=10)
        else:
            raise ProvenanceError("database URL must use PostgreSQL or SQLite")
    except ProvenanceError:
        raise
    except ImportError as exc:
        raise ProvenanceError(
            "psycopg2 is required to inspect the production PostgreSQL database"
        ) from exc
    except Exception as exc:
        raise ProvenanceError(
            f"could not connect to the migration database ({type(exc).__name__})"
        ) from exc

    try:
        cursor = connection.cursor()
        try:
            if scheme == "sqlite":
                cursor.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
                table_names = {str(row[0]) for row in cursor.fetchall()}
            else:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog', 'information_schema')"
                )
                table_names = {str(row[0]) for row in cursor.fetchall()}

            if "alembic_version" not in table_names:
                if table_names:
                    raise ProvenanceError(
                        "database has user tables but no alembic_version table; "
                        "refusing to infer migration state"
                    )
                # A genuinely empty database is the only safe implicit base.
                return ()

            cursor.execute("SELECT version_num FROM alembic_version")
            rows: Iterable[tuple[object, ...]] = cursor.fetchall()
        except ProvenanceError:
            raise
        finally:
            cursor.close()
    except ProvenanceError:
        raise
    except Exception as exc:
        # Driver exception strings can include connection details. Keep the
        # release error useful without ever echoing a URL or credential.
        raise ProvenanceError(
            "could not query the database's alembic_version table "
            f"({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()

    revisions = tuple(sorted({str(row[0]).strip() for row in rows if row and row[0]}))
    if not revisions:
        raise ProvenanceError("database alembic_version table contains no current revision")
    return revisions


def assert_database_provenance(
    *, database_url: str, versions_dir: Path = DEFAULT_VERSIONS_DIR
) -> tuple[tuple[str, ...], str]:
    graph = load_migration_graph(versions_dir)
    database_revisions = query_database_revisions(database_url)
    absent = sorted(set(database_revisions) - graph.parents.keys())
    if absent:
        raise ProvenanceError(
            "database revision(s) are absent from this exact release tree: "
            + ", ".join(absent)
        )
    unreachable = sorted(set(database_revisions) - graph.reachable)
    if unreachable:
        raise ProvenanceError(
            "database revision(s) are unreachable from this release head: "
            + ", ".join(unreachable)
        )
    return database_revisions, graph.head


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--versions-dir",
        type=Path,
        default=DEFAULT_VERSIONS_DIR,
        help="Alembic versions directory from the exact release tree",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    database_url = (
        os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL") or ""
    ).strip()
    if not database_url:
        print(
            "error: DATABASE_PUBLIC_URL or DATABASE_URL is required for provenance checking.",
            file=sys.stderr,
        )
        return 1
    try:
        revisions, release_head = assert_database_provenance(
            database_url=database_url, versions_dir=args.versions_dir.resolve()
        )
    except ProvenanceError as exc:
        print(f"error: production migration provenance check failed: {exc}", file=sys.stderr)
        return 1
    if revisions:
        print(
            "Production migration provenance verified: database revision(s) "
            f"{', '.join(revisions)} are reachable from release head {release_head}."
        )
    else:
        print(
            "Production migration provenance verified: database is empty at "
            f"Alembic base and can upgrade to release head {release_head}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
