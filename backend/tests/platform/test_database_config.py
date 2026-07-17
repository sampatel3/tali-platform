from types import SimpleNamespace

import pytest

from app.platform import database
from app.scripts import railway_start, railway_worker_start


def test_runtime_database_url_uses_private_service_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://public-host.example:5432/taali",
    )
    config = SimpleNamespace(
        DATABASE_URL="postgresql://postgres.railway.internal:5432/taali"
    )

    assert database._runtime_database_url(config) == config.DATABASE_URL


@pytest.mark.parametrize(
    "bootstrap",
    (railway_start, railway_worker_start),
)
def test_railway_bootstrap_uses_private_service_url(monkeypatch, bootstrap):
    """Service readiness must not depend on the external Postgres endpoint."""

    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://public-host.example:5432/taali",
    )
    monkeypatch.setattr(
        bootstrap.settings,
        "DATABASE_URL",
        "postgresql://postgres.railway.internal:5432/taali",
    )

    assert bootstrap._database_url() == bootstrap.settings.DATABASE_URL


def test_postgres_pool_is_bounded_and_configurable(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_POOL_SIZE", 7)
    monkeypatch.setattr(database.settings, "DATABASE_MAX_OVERFLOW", 2)
    monkeypatch.setattr(database.settings, "DATABASE_POOL_TIMEOUT_SECONDS", 12)
    monkeypatch.setattr(database.settings, "DATABASE_POOL_RECYCLE_SECONDS", 900)

    assert database._postgres_engine_kwargs() == {
        "pool_pre_ping": True,
        "pool_size": 7,
        "max_overflow": 2,
        "pool_timeout": 12,
        "pool_recycle": 900,
        "pool_use_lifo": True,
    }


def test_postgres_pool_rejects_zero_or_negative_capacity(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_POOL_SIZE", 0)
    monkeypatch.setattr(database.settings, "DATABASE_MAX_OVERFLOW", -10)
    monkeypatch.setattr(database.settings, "DATABASE_POOL_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(database.settings, "DATABASE_POOL_RECYCLE_SECONDS", 1)

    options = database._postgres_engine_kwargs()
    assert options["pool_size"] == 1
    assert options["max_overflow"] == 0
    assert options["pool_timeout"] == 1
    assert options["pool_recycle"] == 60


def test_workspace_lock_pool_defaults_to_full_existing_app_capacity(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_POOL_SIZE", 7)
    monkeypatch.setattr(database.settings, "DATABASE_MAX_OVERFLOW", 2)
    monkeypatch.setattr(database.settings, "DATABASE_WORKSPACE_LOCK_POOL_SIZE", 0)

    options = database._workspace_lock_engine_kwargs()

    assert options["pool_size"] == 9
    assert options["max_overflow"] == 0


def test_workspace_lock_pool_accepts_a_positive_operator_cap(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_POOL_SIZE", 7)
    monkeypatch.setattr(database.settings, "DATABASE_MAX_OVERFLOW", 2)
    monkeypatch.setattr(database.settings, "DATABASE_WORKSPACE_LOCK_POOL_SIZE", 3)

    options = database._workspace_lock_engine_kwargs()

    assert options["pool_size"] == 3
    assert options["max_overflow"] == 0
