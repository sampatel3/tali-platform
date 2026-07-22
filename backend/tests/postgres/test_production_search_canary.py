"""Real PostgreSQL proof for the exact production-search canary contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.candidate_search import parser, runner
from app.main import app
from app.platform.database import get_db
from app.scripts import provision_search_canary as provisioner


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "qa" / "prod_candidate_search_canary.py"
SPEC = importlib.util.spec_from_file_location(
    "postgres_prod_candidate_search_canary",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary
SPEC.loader.exec_module(canary)


def _params(role_id: int, *, search: bool) -> dict[str, object]:
    params: dict[str, object] = {
        "role_id": role_id,
        "application_outcome": "all",
        "view": "list",
        "rerank": "false",
        "provider_mode": "forbid",
        "include_stage_counts": "false",
        "include_cv_text": "false",
        "limit": 50,
        "offset": 0,
    }
    if search:
        params.update(
            assessment_status="completed",
            nl_query=canary.CANARY_QUERY,
        )
    return params


def test_grounded_fixture_matches_real_canonical_postgres_route(
    postgres_search_db,
    monkeypatch,
) -> None:
    """Run parser -> JSONB/assessment SQL -> retrieval -> HTTP assertions."""

    role, token = provisioner.provision(postgres_search_db)
    role_id = int(role.id)
    postgres_search_db.flush()
    release_sha = "a" * 40
    monkeypatch.setattr(
        "app.domains.assessments_runtime.application_search_support.runtime_release_sha",
        lambda: release_sha,
    )
    monkeypatch.setattr(
        parser,
        "routed_messages_client",
        lambda _execution: (_ for _ in ()).throw(
            AssertionError("model parser must not resolve")
        ),
    )
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("GraphDB/embedding retrieval must not run")
        ),
    )

    def override_db():
        yield postgres_search_db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            inventory_response = client.get(
                "/api/v1/applications",
                params=_params(role_id, search=False),
                headers=headers,
            )
            search_response = client.get(
                "/api/v1/applications",
                params=_params(role_id, search=True),
                headers=headers,
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    assert inventory_response.status_code == 200, inventory_response.text
    assert search_response.status_code == 200, search_response.text
    config = canary.CanaryConfig(
        base_url="https://api.example.test",
        expected_sha=release_sha,
        token=token,
        role_id=role_id,
    )
    inventory_payload = inventory_response.json()
    canary._assert_inventory(config, inventory_payload)
    canary._assert_truth(
        config,
        search_response.json(),
        inventory_payload=inventory_payload,
    )
