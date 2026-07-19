"""Route tests for the sourcing-assist endpoints (LLM mocked).

Covers response structure, the fail-open path, that the metering kwarg reaches
the mocked client, org-scoping (foreign role 404), and the profile_text length
cap (422).
"""
from app.llm.structured import StructuredResult
from app.services import sourcing_assist_service as svc
from app.services.sourcing_assist_service import (
    _OutreachDraft,
    _RefinedAlternate,
    _SearchExpansion,
)
from tests.conftest import auth_headers


def _patch_client(monkeypatch):
    """Avoid building a real Anthropic client — the mocked ``generate_structured``
    never touches it, but the service still resolves one."""
    monkeypatch.setattr(svc, "get_metered_client", lambda **kw: object())


def _make_role_with_must_have(client, headers, *, name="Senior Data Engineer"):
    role = client.post("/api/v1/roles", json={"name": name}, headers=headers).json()
    resp = client.post(
        f"/api/v1/roles/{role['id']}/criteria",
        json={
            "text": "Apache Spark",
            "bucket": "must",
            "expected_version": role["version"],
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return role


# ---- sourcing-searches -----------------------------------------------------


def test_sourcing_searches_returns_deterministic_plus_refined(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    captured = {}

    def fake_generate_structured(*args, **kwargs):
        captured["metering"] = kwargs.get("metering")
        return StructuredResult(
            value=_SearchExpansion(
                title_synonyms=["Analytics Engineer"],
                refined=[
                    _RefinedAlternate(
                        label="Broader",
                        xray='site:linkedin.com/in "Data Engineer"',
                        boolean='"Data Engineer" AND "Spark"',
                    )
                ],
            ),
            ok=True,
        )

    _patch_client(monkeypatch)
    monkeypatch.setattr(svc, "generate_structured", fake_generate_structured)

    resp = client.post(f"/api/v1/roles/{role['id']}/sourcing-searches", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["deterministic"]["xray"].startswith("site:linkedin.com/in")
    assert '"Apache Spark"' in body["deterministic"]["xray"]
    assert body["deterministic"]["boolean"] == '"Senior Data Engineer" AND "Apache Spark"'
    assert body["title_synonyms"] == ["Analytics Engineer"]
    assert body["refined"][0]["label"] == "Broader"
    assert "warning" not in body

    # Metering kwarg present with the sourcing_search feature + entity scope.
    metering = captured["metering"]
    assert metering.feature == "sourcing_search"
    assert metering.entity_id == f"role:{role['id']}"
    assert metering.role_id == role["id"]


def test_sourcing_searches_fail_open_on_llm_error(client, monkeypatch, caplog):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)
    secret_marker = "private-search-validation-detail-must-not-log"

    _patch_client(monkeypatch)
    monkeypatch.setattr(
        svc,
        "generate_structured",
        lambda *a, **k: StructuredResult(
            value=None,
            ok=False,
            error_reason=f"validation_failed_after_retry: {secret_marker}",
        ),
    )

    resp = client.post(f"/api/v1/roles/{role['id']}/sourcing-searches", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Deterministic block always present; refined empty; warning surfaced.
    assert body["deterministic"]["boolean"]
    assert body["refined"] == []
    assert body["title_synonyms"] == []
    assert body["warning"]
    assert secret_marker not in resp.text
    assert secret_marker not in caplog.text
    assert "sourcing_search_expansion:validation_failed" in caplog.text


def test_sourcing_searches_foreign_role_404(client, monkeypatch):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    role = _make_role_with_must_have(client, headers_a)

    monkeypatch.setattr(
        svc, "generate_structured", lambda *a, **k: StructuredResult(value=_SearchExpansion(), ok=True)
    )

    resp = client.post(f"/api/v1/roles/{role['id']}/sourcing-searches", headers=headers_b)
    assert resp.status_code == 404, resp.text


# ---- outreach-draft --------------------------------------------------------


def test_outreach_draft_returns_body_and_warnings(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    captured = {}

    def fake_generate_structured(*args, **kwargs):
        captured["metering"] = kwargs.get("metering")
        return StructuredResult(
            value=_OutreachDraft(subject=None, body="Hi — your Spark work stood out.", warnings=[]),
            ok=True,
        )

    _patch_client(monkeypatch)
    monkeypatch.setattr(svc, "generate_structured", fake_generate_structured)

    resp = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        json={"profile_text": "Data engineer with 5 years of Apache Spark experience."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] is None  # linkedin default → no subject
    assert "Spark" in body["body"]
    assert body["warnings"] == []
    assert captured["metering"].feature == "sourcing_outreach_draft"
    assert captured["metering"].entity_id == f"role:{role['id']}"


def test_outreach_draft_email_channel_keeps_subject(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    _patch_client(monkeypatch)
    monkeypatch.setattr(
        svc,
        "generate_structured",
        lambda *a, **k: StructuredResult(
            value=_OutreachDraft(subject="A Spark role for you", body="Body here.", warnings=[]),
            ok=True,
        ),
    )

    resp = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        json={
            "profile_text": "Spark engineer.",
            "channel": "email",
            "tone": "direct",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["subject"] == "A Spark role for you"


def test_outreach_draft_failure_never_logs_structured_detail(
    client,
    monkeypatch,
    caplog,
):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)
    secret_marker = "private-outreach-validation-detail-must-not-log"
    _patch_client(monkeypatch)
    monkeypatch.setattr(
        svc,
        "generate_structured",
        lambda *args, **kwargs: StructuredResult(
            value=None,
            ok=False,
            error_reason=f"validation_failed_after_retry: {secret_marker}",
        ),
    )

    response = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        json={"profile_text": "Spark engineer."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["body"] == ""
    assert secret_marker not in response.text
    assert secret_marker not in caplog.text
    assert "sourcing_outreach_draft:validation_failed" in caplog.text


def test_outreach_draft_profile_text_length_cap_422(client):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    resp = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        json={"profile_text": "x" * 8001},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_outreach_draft_foreign_role_404(client, monkeypatch):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    role = _make_role_with_must_have(client, headers_a)

    monkeypatch.setattr(
        svc,
        "generate_structured",
        lambda *a, **k: StructuredResult(value=_OutreachDraft(body="x"), ok=True),
    )

    resp = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        json={"profile_text": "Spark engineer."},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text


# ---- budget gate + client-init fail-open -----------------------------------


def test_sourcing_searches_budget_exhausted_fails_open(client, monkeypatch):
    """A spent role budget must not 500 or spend — deterministic strings + warning."""
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    monkeypatch.setattr(svc, "can_spend_on_role", lambda db, *, role: False)

    def _boom(*a, **k):  # any LLM attempt is a bug
        raise AssertionError("LLM must not be called when the budget gate blocks")

    monkeypatch.setattr(svc, "generate_structured", _boom)

    resp = client.post(f"/api/v1/roles/{role['id']}/sourcing-searches", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deterministic"]["boolean"]
    assert body["refined"] == []
    assert "budget" in body["warning"].lower()


def test_sourcing_searches_client_init_failure_fails_open(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    def _raise(**k):
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    monkeypatch.setattr(svc, "get_metered_client", _raise)

    resp = client.post(f"/api/v1/roles/{role['id']}/sourcing-searches", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deterministic"]["xray"]
    assert body["warning"]


def test_outreach_draft_budget_exhausted_402(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _make_role_with_must_have(client, headers)

    import app.domains.outreach.sourcing_assist_routes as routes_mod

    monkeypatch.setattr(routes_mod, "can_spend_on_role", lambda db, *, role: False)

    resp = client.post(
        f"/api/v1/roles/{role['id']}/outreach-draft",
        headers=headers,
        json={"profile_text": "Senior engineer, 8 years Python.", "tone": "warm", "channel": "linkedin"},
    )
    assert resp.status_code == 402, resp.text


def test_outreach_draft_service_fail_open_paths_return_draft_shape(client, monkeypatch):
    """Direct service callers hitting the budget gate or client-init failure get
    an empty draft + warning — never an UnboundLocalError (regression)."""
    from app.services import sourcing_assist_service as service_mod

    headers, _ = auth_headers(client)
    role_payload = _make_role_with_must_have(client, headers)

    from app.models import Role
    from app.platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_payload["id"]).first()

        monkeypatch.setattr(service_mod, "can_spend_on_role", lambda db, *, role: False)
        out = service_mod.draft_outreach(db, role, profile_text="Engineer, 5y Python.")
        assert out["body"] == "" and out["warnings"]

        monkeypatch.setattr(service_mod, "can_spend_on_role", lambda db, *, role: True)

        def _raise(**k):
            raise RuntimeError("no api key")

        monkeypatch.setattr(service_mod, "get_metered_client", _raise)
        out = service_mod.draft_outreach(db, role, profile_text="Engineer, 5y Python.")
        assert out["body"] == "" and out["warnings"]
    finally:
        db.close()
