from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE_GUARD = ROOT / "scripts" / "release" / "assert_canonical_source.sh"
PROVENANCE_CHECKER = ROOT / "backend" / "scripts" / "check_alembic_provenance.py"
SINGLE_HEAD_CHECKER = ROOT / "backend" / "scripts" / "check_alembic_single_head.py"


def _run(*args: str | Path, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> str:
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-m", message, cwd=repo)
    return _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()


def _make_release_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    repo = tmp_path / "release"
    _run("git", "init", "--bare", remote, cwd=tmp_path)
    _run("git", "init", "-b", "main", repo, cwd=tmp_path)
    _run("git", "config", "user.email", "release-test@example.test", cwd=repo)
    _run("git", "config", "user.name", "Release Test", cwd=repo)
    guard = repo / "scripts" / "release" / SOURCE_GUARD.name
    guard.parent.mkdir(parents=True)
    shutil.copy2(SOURCE_GUARD, guard)
    (repo / "release.txt").write_text("one\n", encoding="utf-8")
    _commit(repo, "initial main")
    _run("git", "remote", "add", "origin", remote, cwd=repo)
    _run("git", "push", "-u", "origin", "main", cwd=repo)
    return repo, guard


def test_source_guard_rejects_dirty_feature_and_stale_releases(tmp_path: Path):
    repo, guard = _make_release_repo(tmp_path)
    initial_sha = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()

    clean = _run("bash", guard, cwd=repo, check=False)
    assert clean.returncode == 0, clean.stderr

    (repo / "dirty.txt").write_text("not committed\n", encoding="utf-8")
    dirty = _run("bash", guard, cwd=repo, check=False)
    assert dirty.returncode != 0
    assert "clean worktree" in dirty.stderr
    (repo / "dirty.txt").unlink()

    _run("git", "switch", "-c", "feature", cwd=repo)
    (repo / "release.txt").write_text("feature\n", encoding="utf-8")
    feature_sha = _commit(repo, "feature commit")
    feature = _run("bash", guard, cwd=repo, check=False)
    assert feature.returncode != 0
    assert "branch or stale commit" in feature.stderr
    spoofed_coordinator = _run(
        "bash", guard, "--expected-sha", feature_sha, cwd=repo, check=False
    )
    assert spoofed_coordinator.returncode != 0
    assert "attestation" in spoofed_coordinator.stderr
    spoofed_env = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; railway_assert_release_source "$2" production',
            "release-guard-test",
            str(ROOT / "scripts" / "railway" / "lib.sh"),
            str(repo),
        ],
        cwd=repo,
        env={**os.environ, "TALI_COORDINATED_RELEASE_SHA": feature_sha},
        check=False,
        capture_output=True,
        text=True,
    )
    assert spoofed_env.returncode != 0
    assert "attestation" in spoofed_env.stderr

    _run("git", "switch", "main", cwd=repo)
    (repo / "release.txt").write_text("two\n", encoding="utf-8")
    _commit(repo, "new main")
    _run("git", "push", "origin", "main", cwd=repo)
    _run("git", "reset", "--hard", initial_sha, cwd=repo)

    stale = _run("bash", guard, cwd=repo, check=False)
    assert stale.returncode != 0
    assert "branch or stale commit" in stale.stderr

    # A rollout attested while main was current must finish its kickoff SHA if
    # main advances, rather than leaving production services on mixed commits.
    token = "release-test-token"
    attestation = tmp_path / "release-attestation"
    attestation.write_text(
        f"{token}\n{initial_sha}\n{repo.resolve()}\n", encoding="utf-8"
    )
    pinned = subprocess.run(
        ["bash", str(guard), "--expected-sha", initial_sha],
        cwd=repo,
        env={
            **os.environ,
            "TALI_COORDINATED_RELEASE_ATTESTATION": str(attestation),
            "TALI_COORDINATED_RELEASE_TOKEN": token,
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert pinned.returncode == 0, pinned.stderr


def test_coordinator_creates_and_cleans_process_attestation(tmp_path: Path):
    repo, guard = _make_release_repo(tmp_path)
    release_sha = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    command = """
set -euo pipefail
source "$1"
railway_begin_coordinated_release "$2" "$3"
attestation_path="$TALI_COORDINATED_RELEASE_ATTESTATION"
test -f "$attestation_path"
bash "$4" --expected-sha "$3"
railway_end_coordinated_release
test ! -e "$attestation_path"
"""

    result = subprocess.run(
        [
            "bash",
            "-c",
            command,
            "release-attestation-test",
            str(ROOT / "scripts" / "railway" / "lib.sh"),
            str(repo),
            release_sha,
            str(guard),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _write_migration(path: Path, revision: str, down_revision: str | None) -> None:
    path.write_text(
        f"revision = {revision!r}\n"
        f"down_revision = {down_revision!r}\n"
        "branch_labels = None\n"
        "depends_on = None\n",
        encoding="utf-8",
    )


def _create_version_database(path: Path, revision: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(255))")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.commit()
    finally:
        connection.close()


def _run_provenance(database: Path, versions: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_PUBLIC_URL": "",
        "DATABASE_URL": "sqlite:///" + str(database),
    }
    return subprocess.run(
        ["python3", str(PROVENANCE_CHECKER), "--versions-dir", str(versions)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_database_provenance_accepts_reachable_revision_and_rejects_branch_revision(
    tmp_path: Path,
):
    versions = tmp_path / "versions"
    versions.mkdir()
    _write_migration(versions / "001_base.py", "001", None)
    _write_migration(versions / "002_head.py", "002", "001")
    database = tmp_path / "production.sqlite3"
    _create_version_database(database, "001")

    reachable = _run_provenance(database, versions)
    assert reachable.returncode == 0, reachable.stderr
    assert "reachable from release head 002" in reachable.stdout

    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE alembic_version SET version_num = 'feature_only_003'")
        connection.commit()
    finally:
        connection.close()

    missing = _run_provenance(database, versions)
    assert missing.returncode != 0
    assert "absent from this exact release tree: feature_only_003" in missing.stderr
    assert str(database) not in missing.stdout + missing.stderr


def test_database_provenance_fails_closed_on_multiple_release_heads(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _write_migration(versions / "001_base.py", "001", None)
    _write_migration(versions / "002_head.py", "002", "001")
    _write_migration(versions / "feature_head.py", "feature_head", "001")
    database = tmp_path / "production.sqlite3"
    _create_version_database(database, "001")

    result = _run_provenance(database, versions)
    assert result.returncode != 0
    assert "exactly one Alembic head" in result.stderr


def test_database_provenance_allows_only_a_genuinely_empty_greenfield_database(
    tmp_path: Path,
):
    versions = tmp_path / "versions"
    versions.mkdir()
    _write_migration(versions / "001_base.py", "001", None)
    database = tmp_path / "greenfield.sqlite3"
    sqlite3.connect(database).close()

    empty = _run_provenance(database, versions)
    assert empty.returncode == 0, empty.stderr
    assert "database is empty at Alembic base" in empty.stdout

    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    unstamped = _run_provenance(database, versions)
    assert unstamped.returncode != 0
    assert "user tables but no alembic_version" in unstamped.stderr


def test_database_provenance_never_echoes_database_credentials(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _write_migration(versions / "001_base.py", "001", None)
    secret = "do-not-print-this-password"
    database_url = f"unsupported://release-user:{secret}@database.example.test/app"
    env = {
        **os.environ,
        "DATABASE_PUBLIC_URL": database_url,
        "DATABASE_URL": database_url,
    }

    result = subprocess.run(
        ["python3", str(PROVENANCE_CHECKER), "--versions-dir", str(versions)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "database URL must use PostgreSQL or SQLite" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert database_url not in result.stdout + result.stderr


def test_single_head_gate_rejects_duplicate_revision_ids(tmp_path: Path):
    backend = tmp_path / "backend"
    scripts = backend / "scripts"
    versions = backend / "alembic" / "versions"
    scripts.mkdir(parents=True)
    versions.mkdir(parents=True)
    shutil.copy2(SINGLE_HEAD_CHECKER, scripts / SINGLE_HEAD_CHECKER.name)
    _write_migration(versions / "001_first.py", "001", None)
    _write_migration(versions / "001_second.py", "001", None)

    result = _run("python3", scripts / SINGLE_HEAD_CHECKER.name, cwd=backend, check=False)

    assert result.returncode != 0
    assert "duplicate revision '001'" in result.stderr


def test_provenance_gate_precedes_every_production_mutation():
    prepare = (ROOT / "scripts" / "railway" / "prepare_production.sh").read_text()
    worker = (ROOT / "scripts" / "railway" / "deploy_worker.sh").read_text()
    backend = (ROOT / "scripts" / "railway" / "deploy_backend.sh").read_text()

    for script in (prepare, worker, backend):
        assert "railway_assert_release_source" in script
        assert "railway_assert_canonical_backend_dir" in script
    assert prepare.index("railway_assert_database_provenance_from_variables_file") < prepare.index(
        "railway variable set"
    )
    assert prepare.index("check_alembic_provenance.py") < prepare.index(
        '"alembic", "upgrade", "head"'
    )
    assert worker.index("railway_assert_production_database_provenance") < worker.index(
        "railway variable set"
    )
    assert backend.index("railway_assert_production_database_provenance") < backend.index(
        "railway up"
    )
