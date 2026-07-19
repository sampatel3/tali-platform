"""Regression coverage for the shared SQLite pytest isolation harness."""

from sqlalchemy import inspect

from app.models.agent_conversation import (
    AgentConversation,
    AgentConversationMessage,
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
)
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.platform.database import Base
from tests.conftest import (
    TestingSessionLocal,
    _clear_database_rows,
    _ensure_schema,
    engine,
)


def _seed_conversation_message(db, *, slug: str) -> AgentConversationMessage:
    org = Organization(name=slug, slug=slug)
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Isolation role")
    db.add(role)
    db.flush()
    conversation = AgentConversation(
        organization_id=org.id,
        role_id=role.id,
        title="Isolation conversation",
    )
    db.add(conversation)
    db.flush()
    message = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=org.id,
        role_id=role.id,
        author_role=AUTHOR_ROLE_USER,
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": "hello"}],
        text="hello",
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def test_row_cleanup_clears_fk_graph_and_resets_sqlite_sequence(db):
    first = _seed_conversation_message(db, slug="isolation-first")
    assert first.id == 1

    # The normal fixture invokes this after closing its session. Exercise the
    # helper directly so row deletion and sequence reset are both explicit.
    db.close()
    _clear_database_rows()

    with TestingSessionLocal() as fresh:
        assert fresh.query(Organization).count() == 0
        assert fresh.query(Role).count() == 0
        assert fresh.query(AgentConversation).count() == 0
        assert fresh.query(AgentConversationMessage).count() == 0
        second = _seed_conversation_message(fresh, slug="isolation-second")
        assert second.id == 1


def test_schema_guard_recovers_from_specialised_schema_rebuild(db):
    db.close()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # Leave the core organizations table intact: the guard must detect a
        # partial rebuild, not merely rely on one well-known sentinel table.
        conn.exec_driver_sql("DROP TABLE roles")
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()

    assert "organizations" in inspect(engine).get_table_names()
    assert "roles" not in inspect(engine).get_table_names()
    _ensure_schema()
    assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())


def test_bigint_pk_emulation_advances_past_explicit_fixture_ids(db):
    org = Organization(name="Big PK org", slug="big-pk-org")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Big PK role")
    db.add(role)
    db.flush()

    explicit = AgentRun(
        id=9_000_000_000_000_000,
        organization_id=org.id,
        role_id=role.id,
        trigger="manual",
        status="succeeded",
    )
    implicit = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="cron",
        status="succeeded",
    )
    db.add_all([explicit, implicit])
    db.flush()

    assert implicit.id == explicit.id + 1
