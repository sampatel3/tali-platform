"""Exact deployed-revision metadata used by post-deploy verification."""

from app.platform.release import runtime_release_sha


def test_runtime_release_sha_prefers_railway_and_requires_full_sha(monkeypatch):
    railway_sha = "a" * 40
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", railway_sha.upper())
    monkeypatch.setenv("TALI_RELEASE_SHA", "b" * 40)
    assert runtime_release_sha() == railway_sha

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "short")
    assert runtime_release_sha() == "b" * 40

    monkeypatch.setenv("TALI_RELEASE_SHA", "not-a-sha")
    assert runtime_release_sha() is None


def test_health_exposes_runtime_release_sha(client, monkeypatch):
    release_sha = "c" * 40
    monkeypatch.setattr("app.main.runtime_release_sha", lambda: release_sha)

    payload = client.get("/health").json()

    assert payload["deployment"] == {"commit_sha": release_sha}
