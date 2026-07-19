from app.platform.database_url import runtime_database_url


def test_runtime_database_url_prefers_private_service_connection(monkeypatch):
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "replica-1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://private-user:private-pass@postgres.railway.internal:5432/app",
    )
    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://public-user:public-pass@proxy.example.test:17842/app",
    )

    selected = runtime_database_url("postgresql://configured@localhost/app")

    assert selected == (
        "postgresql://private-user:private-pass@postgres.railway.internal:5432/app"
    )


def test_runtime_database_url_prefers_public_connection_for_railway_run(monkeypatch):
    monkeypatch.delenv("RAILWAY_REPLICA_ID", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://private-user:private-pass@postgres.railway.internal:5432/app",
    )
    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://public-user:public-pass@proxy.example.test:17842/app",
    )

    assert runtime_database_url("") == (
        "postgresql://public-user:public-pass@proxy.example.test:17842/app"
    )


def test_runtime_database_url_falls_back_to_private_when_public_is_absent(monkeypatch):
    monkeypatch.delenv("RAILWAY_REPLICA_ID", raising=False)
    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://private-user:private-pass@postgres.railway.internal:5432/app",
    )

    assert runtime_database_url("") == (
        "postgresql://private-user:private-pass@postgres.railway.internal:5432/app"
    )
