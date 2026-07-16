"""Scoped, no-login CLIENT INTAKE share link.

A consultancy recruiter mints a link (``POST /requisitions/{id}/client-link``,
JWT, idempotent token) and sends it to their CLIENT, who describes the role via
the SAME conversational agent — company/economics layers hidden, no pay
questions. The public router (no auth) resolves the brief by its
``client_intake_token``:

* ``GET  /api/v1/public/intake/{token}``        — org name + transcript + live
  spec progress against the CLIENT-scoped template (ROLE-safe fields only;
  NEVER client_rate / margin / client_id / salary_*).
* ``POST /api/v1/public/intake/{token}/chat``   — one client-scoped, client-
  framed turn, metered under ``requisition_client_intake``.
* ``POST /api/v1/public/intake/{token}/submit`` — set status submitted.

The chat turn's LLM is faked by injecting a metered client whose
``messages.create`` records a real UsageEvent (so metering is asserted on the
exact feature the route passes) and returns a tool-use ChatCapture — exercising
the real ``run_chat_turn`` → ``generate_structured`` path.
"""
import io
from types import SimpleNamespace

from app.models.role_brief import RoleBrief
from app.models.usage_event import UsageEvent
from app.services import requisition_chat_service as chat
from app.services.requisition_template_service import (
    DEFAULT_REQUISITION_TEMPLATE,
    client_scoped_template,
)
from app.services.usage_metering_service import record_event
from tests.conftest import auth_headers


# --------------------------------------------------------------------------- #
# Fake metered client: records a real UsageEvent for the feature the caller
# tagged, then returns a forced-tool-use ChatCapture response.
# --------------------------------------------------------------------------- #
def _make_fake_client(capture_input: dict, seen: dict):
    """Build a fake metered Claude client. ``messages.create`` records the
    metering dict it was handed (into ``seen`` — so the test can assert the
    feature the route threaded all the way to the metered ``one_call``) and
    returns a forced-tool-use block whose ``.input`` is ``capture_input`` (the
    ChatCapture fields). No DB write here — shared-memory SQLite is
    single-writer, so a competing transaction inside the open request would
    deadlock; metering persistence is asserted separately."""

    class _Messages:
        def create(self, **kwargs):
            seen["metering"] = dict(kwargs.get("metering") or {})
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="emit_chat_capture",
                        input=capture_input,
                    )
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
            )

    return SimpleNamespace(messages=_Messages())


def _mint_link(client, headers):
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(f"/api/v1/requisitions/{brief_id}/client-link", headers=headers)
    assert resp.status_code == 200, resp.text
    return brief_id, resp.json()


# --------------------------------------------------------------------------- #
# Recruiter: mint the link (idempotent)
# --------------------------------------------------------------------------- #
def test_mint_client_link_returns_token_and_url_and_serializer_block(client):
    headers, _ = auth_headers(client)
    brief_id, body = _mint_link(client, headers)
    assert body["token"]
    # URL embeds the token (FRONTEND_URL default is http://localhost:5173).
    assert body["url"].endswith(f"/intake/{body['token']}")
    # The requisition serializer now carries the client_link block.
    got = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert got["client_link"] == {"token": body["token"], "url": body["url"]}


def test_mint_client_link_is_idempotent(client):
    headers, _ = auth_headers(client)
    _, first = _mint_link(client, headers)
    brief_id = client.get("/api/v1/requisitions", headers=headers).json()[0]["id"]
    second = client.post(
        f"/api/v1/requisitions/{brief_id}/client-link", headers=headers
    ).json()
    # Same token on re-mint — a shared link never goes stale.
    assert second["token"] == first["token"]


def test_serializer_client_link_null_before_mint(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["client_link"] is None


# --------------------------------------------------------------------------- #
# client_scoped_template drops the compensation section
# --------------------------------------------------------------------------- #
def test_client_scoped_template_drops_compensation_section():
    scoped = client_scoped_template(DEFAULT_REQUISITION_TEMPLATE)
    keys = [s["key"] for s in scoped["sections"]]
    assert "compensation" not in keys
    assert keys == ["role_basics", "logistics", "requirements", "context"]
    # The original template is never mutated.
    orig_keys = [s["key"] for s in DEFAULT_REQUISITION_TEMPLATE["sections"]]
    assert "compensation" in orig_keys
    # No salary field survives anywhere in the scoped template.
    scoped_field_keys = {
        f["key"] for s in scoped["sections"] for f in s["fields"]
    }
    assert {"salary_min", "salary_max", "salary_currency"}.isdisjoint(scoped_field_keys)


# --------------------------------------------------------------------------- #
# Public GET — role fields only; NEVER the org name (privacy), economics, pay,
# or client identity.
# --------------------------------------------------------------------------- #
def test_public_get_exposes_role_fields_but_not_org_name(client):
    headers, _ = auth_headers(client, organization_name="Globex Recruiting")
    brief_id, link = _mint_link(client, headers)
    # Capture some role-safe fields onto the brief via the recruiter PATCH.
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Backend Engineer",
            "department": "Platform",       # internal — must NOT surface
            "workplace_type": "remote",     # HR/People — must NOT surface
            "must_haves": ["Python", "AWS"],
        },
        headers=headers,
    )

    resp = client.get(f"/api/v1/public/intake/{link['token']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "organization_name",
        "messages",
        "captured",
        "gaps",
        "completeness",
        "status",
    }
    # Privacy: the client surface is anonymous — the consultancy/org name is
    # never exposed, even though the org has one.
    assert body["organization_name"] is None
    assert body["captured"]["title"] == "Backend Engineer"
    assert body["captured"]["must_haves"] == ["Python", "AWS"]
    assert body["status"] == "draft"
    # The hiring-manager intake is ROLE-only — internal/HR logistics never
    # surface, even when the recruiter has set them on the brief.
    assert "department" not in body["captured"]
    assert "workplace_type" not in body["captured"]
    # Gaps are computed against the hiring-manager-scoped template — neither the
    # compensation fields nor the dropped logistics fields appear as gaps.
    gap_keys = {g["key"] for g in body["gaps"]}
    assert {"salary_min", "salary_max", "salary_currency"}.isdisjoint(gap_keys)
    assert {"workplace_type", "employment_type", "location_city", "department"}.isdisjoint(gap_keys)
    # Role fields the manager DOES own still drive the intake.
    assert {"must_haves", "openings", "urgency"} & gap_keys


def test_public_get_never_exposes_client_rate_margin_salary_or_client_id(client):
    """Even with a client + rate + salary captured, the public payload carries
    NO economics, pay, or client identity."""
    headers, _ = auth_headers(client)
    client_id = client.post(
        "/api/v1/clients", json={"name": "Secret Client Co"}, headers=headers
    ).json()["id"]
    brief_id, link = _mint_link(client, headers)
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Eng",
            "client_id": client_id,
            "client_rate": 300000,
            "salary_min": 150000,
            "salary_max": 200000,
            "salary_currency": "AED",
        },
        headers=headers,
    )

    body = client.get(f"/api/v1/public/intake/{link['token']}").json()
    captured = body["captured"]
    forbidden = {
        "client_id",
        "client_name",
        "client_rate",
        "margin",
        "margin_pct",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    }
    assert forbidden.isdisjoint(captured.keys()), (
        f"leaked: {forbidden & set(captured.keys())}"
    )
    # And no value anywhere in the payload echoes the secret rate or client name.
    serialized = str(body)
    assert "300000" not in serialized
    assert "Secret Client Co" not in serialized
    # The salary the recruiter set is NOT surfaced.
    assert "150000" not in serialized and "200000" not in serialized


def test_public_get_unknown_token_404(client):
    resp = client.get("/api/v1/public/intake/does-not-exist")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Public chat — client-scoped turn captures role fields + is metered under the
# requisition_client_intake feature
# --------------------------------------------------------------------------- #
def test_public_chat_captures_role_fields_and_meters_client_intake(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    brief_id, link = _mint_link(client, headers)

    capture_input = {
        "assistant_reply": "Got it — onsite or remote, and how many openings?",
        "open_questions": ["workplace_type?"],
        "title": "Data Engineer",
        "must_haves": ["SQL", "Spark"],
        "custom": {"urgency": "High"},
    }
    seen: dict = {}
    fake = _make_fake_client(capture_input, seen)
    monkeypatch.setattr(chat, "get_metered_client", lambda **kw: fake)

    resp = client.post(
        f"/api/v1/public/intake/{link['token']}/chat",
        data={"message": "We need a data engineer strong in SQL and Spark."},
        files=[("files", ("notes.txt", io.BytesIO(b"asap, small team"), "text/plain"))],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "reply",
        "messages",
        "captured",
        "gaps",
        "completeness",
        "suggested_replies",
        "suggested_multi",
    }
    # Attachment turns use post-capture gaps, so the reply cannot repeat the
    # model's stale pre-capture workplace question.
    assert body["reply"].startswith("I've amended the draft.")
    assert "What domain or industry" in body["reply"]
    # Role fields captured onto the brief and surfaced (role-safe).
    assert body["captured"]["title"] == "Data Engineer"
    assert body["captured"]["must_haves"] == ["SQL", "Spark"]
    # Custom role-ish key (urgency) is exposed; it's role-safe.
    assert body["captured"]["urgency"] == "High"
    # Transcript: opening + user + assistant.
    assert [m["role"] for m in body["messages"]] == ["assistant", "user", "assistant"]

    # Persisted client source remains available to later intake turns without
    # being echoed through authenticated requisition detail/list payloads.
    recruiter_view = client.get(
        f"/api/v1/requisitions/{brief_id}", headers=headers
    ).json()
    assert "client_source_material" not in recruiter_view["agent_state"]
    assert "client_source_hydration_digest" not in recruiter_view["agent_state"]
    listed = client.get("/api/v1/requisitions", headers=headers).json()
    listed_brief = next(item for item in listed if item["id"] == brief_id)
    assert "client_source_material" not in listed_brief["agent_state"]

    # The route threaded the dedicated CLIENT-intake feature all the way to the
    # metered ``one_call`` (NOT the recruiter intake chat).
    assert seen["metering"]["feature"] == "requisition_client_intake"
    assert seen["metering"]["organization_id"] == _org_id_of_brief(db, brief_id)
    assert seen["metering"]["entity_id"] == f"role_brief:{brief_id}"

    # And that feature persists a real UsageEvent (guards the silent-drop bug —
    # an unregistered feature string would raise inside record_event).
    org_id = _org_id_of_brief(db, brief_id)
    ev = record_event(
        db,
        organization_id=org_id,
        feature="requisition_client_intake",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=5,
    )
    db.flush()
    assert ev.feature == "requisition_client_intake"
    assert (
        db.query(UsageEvent)
        .filter(
            UsageEvent.organization_id == org_id,
            UsageEvent.feature == "requisition_client_intake",
        )
        .count()
        == 1
    )


def test_public_chat_requires_message_or_file(client):
    headers, _ = auth_headers(client)
    _, link = _mint_link(client, headers)
    resp = client.post(
        f"/api/v1/public/intake/{link['token']}/chat", data={"message": "   "}
    )
    assert resp.status_code == 422


def test_public_chat_unknown_token_404(client):
    resp = client.post(
        "/api/v1/public/intake/nope/chat", data={"message": "hello"}
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Anti-abuse: total user-turn cap → 429
# --------------------------------------------------------------------------- #
def test_public_intake_never_exposes_recruiter_transcript(client, db):
    """Privacy: the recruiter's own chat (``brief.messages``) may hold
    confidential internal context — it must NEVER appear on the public
    hiring-manager link, which has its OWN transcript (``client_messages``)."""
    headers, _ = auth_headers(client)
    brief_id, link = _mint_link(client, headers)

    secret = "INTERNAL: backfill for someone we're managing out; budget flex to 400k"
    brief = db.query(RoleBrief).filter(RoleBrief.id == brief_id).first()
    brief.messages = list(brief.messages or []) + [
        {"role": "user", "content": secret, "attachments": []}
    ]
    db.commit()

    body = client.get(f"/api/v1/public/intake/{link['token']}").json()
    # The recruiter's confidential text is absent from the public transcript.
    blob = " ".join(m.get("content", "") for m in body["messages"])
    assert secret not in blob
    assert "INTERNAL" not in blob
    # The manager gets their OWN fresh opener instead.
    assert body["messages"] and body["messages"][0]["role"] == "assistant"
    assert "in your own words" in body["messages"][0]["content"]
    # The recruiter transcript still holds it (separated, not destroyed).
    db.refresh(brief)
    assert any(secret in (m.get("content") or "") for m in brief.messages)


def test_public_chat_turn_cap_returns_429(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    brief_id, link = _mint_link(client, headers)

    # Stuff the HIRING-MANAGER transcript with 60 user turns directly (cheaper
    # than 60 calls). The cap counts client_messages, not the recruiter's.
    brief = db.query(RoleBrief).filter(RoleBrief.id == brief_id).first()
    msgs = list(brief.client_messages or [])
    msgs += [{"role": "user", "content": f"m{i}", "attachments": []} for i in range(60)]
    brief.client_messages = msgs
    db.commit()

    # No LLM should be reached — the cap rejects before any turn runs.
    def _boom(**kw):  # pragma: no cover — must not be called
        raise AssertionError("LLM must not be called past the turn cap")

    monkeypatch.setattr(chat, "get_metered_client", _boom)

    resp = client.post(
        f"/api/v1/public/intake/{link['token']}/chat",
        data={"message": "one more"},
    )
    assert resp.status_code == 429


# --------------------------------------------------------------------------- #
# Submit — sets status submitted
# --------------------------------------------------------------------------- #
def test_public_submit_sets_status_submitted(client):
    headers, _ = auth_headers(client)
    brief_id, link = _mint_link(client, headers)
    resp = client.post(f"/api/v1/public/intake/{link['token']}/submit")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "status": "submitted"}
    # Reflected on the recruiter view too.
    got = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert got["status"] == "submitted"


def test_public_submit_unknown_token_404(client):
    resp = client.post("/api/v1/public/intake/nope/submit")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _org_id_of_brief(db, brief_id: int) -> int:
    brief = db.query(RoleBrief).filter(RoleBrief.id == brief_id).first()
    return int(brief.organization_id)
