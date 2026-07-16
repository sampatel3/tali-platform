"""ask_recruiter.answer write-back: column updates + RoleIntent + chips.

The agent asks a config-gap question, the recruiter answers it, and the
answer is promoted into canonical role state so the Agent settings tab
reflects what the recruiter just told the agent. These tests pin that
write-back behavior per ``kind``.
"""

from __future__ import annotations


from app.actions import ask_recruiter
from app.actions.types import Actor
from app.agent_runtime.role_intent import fetch_active_intent
from app.models.agent_run import AgentRun
from app.models.role_change_event import RoleChangeEvent
from app.models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from app.models.role_intent import RoleIntent
from app.models.user import User

from .conftest import make_world


def _agent_actor(db, role) -> Actor:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return Actor.agent(int(run.id))


def _recruiter_actor(db, organization_id: int) -> tuple[Actor, User]:
    user = User(
        organization_id=organization_id,
        email=f"u-{id(db)}@x.test",
        full_name="U",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        role="owner",
    )
    db.add(user)
    db.flush()
    return Actor.recruiter(user), user


# ---------------------------------------------------------------------------
# threshold_ambiguous → role.score_threshold
# ---------------------------------------------------------------------------


def test_threshold_answer_overwrites_existing_column(db):
    """Approving the agent's proposed threshold writes role.score_threshold
    even when the column was already set (the previous 'effective only when
    null' behavior silently discarded the recruiter's approval)."""
    org, role, _, _ = make_world(db)
    role.score_threshold = 55  # pre-existing value the recruiter wants to override
    db.flush()
    starting_version = int(role.version or 1)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="threshold_ambiguous",
        prompt="Use 30 as the bar?",
    )
    rec, rec_user = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "30"},
    )
    db.refresh(role)
    assert role.score_threshold == 30
    assert role.version == starting_version + 1
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.actor_user_id == rec_user.id
    assert event.action == "needs_input_score_threshold_updated"
    assert event.from_version == starting_version
    assert event.to_version == starting_version + 1
    assert event.changes["score_threshold"] == {"before": 55, "after": 30}


def test_threshold_answer_clamps_and_ignores_garbage(db):
    org, role, _, _ = make_world(db)
    role.score_threshold = 50
    db.flush()
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="threshold_ambiguous",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "around fifty"},
    )
    db.refresh(role)
    # Garbage value leaves the column untouched.
    assert role.score_threshold == 50


def test_threshold_answer_clamps_to_0_100(db):
    org, role, _, _ = make_world(db)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="threshold_ambiguous",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "150"},
    )
    db.refresh(role)
    assert role.score_threshold == 100


# ---------------------------------------------------------------------------
# monthly_budget_missing → role.monthly_usd_budget_cents
# ---------------------------------------------------------------------------


def test_budget_answer_dollars_writes_cents(db):
    org, role, _, _ = make_world(db)
    role.monthly_usd_budget_cents = None
    db.flush()
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="monthly_budget_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "$75"},
    )
    db.refresh(role)
    assert role.monthly_usd_budget_cents == 7500


def test_budget_answer_overwrites_existing(db):
    """Like threshold: the new answer wins even if a value was already set."""
    org, role, _, _ = make_world(db)
    role.monthly_usd_budget_cents = 5000  # $50
    db.flush()
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="monthly_budget_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "100"},
    )
    db.refresh(role)
    assert role.monthly_usd_budget_cents == 10000


def test_budget_answer_large_dollar_amount_is_dollars_not_cents(db):
    """A budget over $1000 must be read as dollars. The old heuristic
    ("large number is cents") stored $2,000/mo as 2000 cents = $20."""
    org, role, _, _ = make_world(db)
    role.monthly_usd_budget_cents = None
    db.flush()
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="monthly_budget_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "$2,000"},
    )
    db.refresh(role)
    assert role.monthly_usd_budget_cents == 200_000


# ---------------------------------------------------------------------------
# intent_slot_missing → RoleIntent + chips
# ---------------------------------------------------------------------------


def _stub_chip_parser(monkeypatch, chips):
    """Patch out the LLM call so tests don't hit Claude."""
    from app.services.intent_chip_parser import ParsedChip

    def _fake(db, *, organization_id, role, answer_text, agent_question=None, existing_chip_texts=None):
        return [ParsedChip(bucket=b, text=t) for b, t in chips]

    # The action imports `parse_intent_text_to_chips` inside the helper at
    # call time, so patching the source module is what takes effect.
    import app.services.intent_chip_parser as parser_mod

    monkeypatch.setattr(parser_mod, "parse_intent_text_to_chips", _fake)
    return _fake


def test_intent_answer_authors_role_intent_and_chips(db, monkeypatch):
    org, role, _, _ = make_world(db)
    starting_version = int(role.version or 1)
    _stub_chip_parser(
        monkeypatch,
        [
            ("must", "5+ years backend Python"),
            ("preferred", "Postgres at scale"),
            ("constraint", "US time zones"),
        ],
    )
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="What are the must-haves?",
    )
    rec, rec_user = _recruiter_actor(db, int(org.id))
    answer_text = (
        "5+ years backend Python, Postgres at scale, US time zones, "
        "remote-friendly"
    )
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": answer_text},
    )

    # RoleIntent v1 was authored with the answer as free_text.
    intent = fetch_active_intent(db, role_id=int(role.id))
    assert intent is not None
    assert intent.version == 1
    assert answer_text in (intent.free_text or "")
    assert intent.authored_by_user_id == int(rec_user.id)

    # Three chips were added as recruiter-source chips.
    chips = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == int(role.id),
            RoleCriterion.deleted_at.is_(None),
            RoleCriterion.source == CRITERION_SOURCE_RECRUITER,
        )
        .order_by(RoleCriterion.ordering)
        .all()
    )
    # We can't assume there were no pre-existing chips, so just check
    # that ours show up.
    chip_texts = {(c.bucket, c.text) for c in chips}
    assert ("must", "5+ years backend Python") in chip_texts
    assert ("preferred", "Postgres at scale") in chip_texts
    assert ("constraint", "US time zones") in chip_texts

    db.refresh(role)
    assert role.version == starting_version + 1
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.actor_user_id == rec_user.id
    assert event.action == "needs_input_intent_criteria_updated"
    assert event.from_version == starting_version
    assert event.to_version == starting_version + 1
    assert event.changes == {}


def test_intent_answer_appends_to_existing_free_text(db, monkeypatch):
    """A second intent answer adds a new RoleIntent version and preserves
    the prior free_text rather than clobbering it."""
    org, role, _, _ = make_world(db)
    _stub_chip_parser(monkeypatch, [])
    agent = _agent_actor(db, role)
    rec, _ = _recruiter_actor(db, int(org.id))

    # First answer
    row1 = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="Must-haves?",
    )
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row1.id),
        expected_version=int(role.version or 1),
        response={"value": "First answer about seniority"},
    )

    # Second answer (re-asked later in a different cycle)
    row2 = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_clarification",
        prompt="Missing location signal?",
    )
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row2.id),
        expected_version=int(role.version or 1),
        response={"value": "Second answer about location"},
    )

    # Active version is v2 and includes both answers.
    intent = fetch_active_intent(db, role_id=int(role.id))
    assert intent is not None
    assert intent.version == 2
    assert "First answer about seniority" in (intent.free_text or "")
    assert "Second answer about location" in (intent.free_text or "")

    # Two RoleIntent rows total — prior version was superseded with valid_to set.
    versions = (
        db.query(RoleIntent)
        .filter(RoleIntent.role_id == int(role.id))
        .order_by(RoleIntent.version)
        .all()
    )
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].valid_to is not None
    assert versions[1].valid_to is None


def test_intent_answer_empty_value_is_a_noop(db, monkeypatch):
    org, role, _, _ = make_world(db)
    _stub_chip_parser(monkeypatch, [("must", "x")])
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "   "},
    )
    # Empty trimmed value → no RoleIntent and no chips written.
    assert fetch_active_intent(db, role_id=int(role.id)) is None
    chip_count = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == int(role.id),
            RoleCriterion.deleted_at.is_(None),
        )
        .count()
    )
    assert chip_count == 0


def test_chip_parser_failure_does_not_block_intent_write(db, monkeypatch):
    """If the LLM chip parse throws, the answer still resolves and the
    RoleIntent free_text still lands. The recruiter is never blocked by
    a flaky LLM call."""
    import app.services.intent_chip_parser as parser_mod

    def _explode(*a, **kw):
        raise RuntimeError("simulated LLM outage")

    monkeypatch.setattr(parser_mod, "parse_intent_text_to_chips", _explode)

    org, role, _, _ = make_world(db)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    answered = ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "Python, AWS"},
    )
    # Answer resolved cleanly even though the chip parser blew up.
    assert answered.resolved_at is not None
    # The RoleIntent author call runs *before* the parser inside
    # _writeback_intent and flushes its row, so the free-text version is
    # persisted regardless of the parser outage.
    intent = fetch_active_intent(db, role_id=int(role.id))
    assert intent is not None
    assert "Python, AWS" in (intent.free_text or "")
    # No chips were added because the parser raised before returning any.
    chip_count = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == int(role.id),
            RoleCriterion.deleted_at.is_(None),
        )
        .count()
    )
    assert chip_count == 0
