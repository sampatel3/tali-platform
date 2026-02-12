"""End-to-end tests: multi-org data isolation.

Every test creates two separate org contexts (OrgA / OrgB) and verifies
that resources created by one org are invisible and inaccessible to the other.
"""

import pytest

from tests.conftest import (
    auth_headers,
    create_assessment_via_api,
    create_candidate_via_api,
    create_task_via_api,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_two_orgs(client):
    """Create two fully authenticated org contexts.

    Returns (headers_a, headers_b).
    """
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    return headers_a, headers_b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestMultiOrgIsolation:
    """Verify strict data isolation between organizations."""

    def test_org_a_cannot_see_org_b_tasks(self, client):
        """Create tasks in both orgs → A's list doesn't include B's."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_a = create_task_via_api(client, headers_a, name="Task-A")
        assert task_a.status_code == 201
        task_a_id = task_a.json()["id"]

        task_b = create_task_via_api(client, headers_b, name="Task-B")
        assert task_b.status_code == 201
        task_b_id = task_b.json()["id"]

        list_a = client.get("/api/v1/tasks/", headers=headers_a)
        assert list_a.status_code == 200
        ids_a = [t["id"] for t in list_a.json()]
        assert task_a_id in ids_a
        assert task_b_id not in ids_a

    def test_org_a_cannot_see_org_b_candidates(self, client):
        """Create candidates in both orgs → A can't see B's."""
        headers_a, headers_b = _setup_two_orgs(client)

        cand_a = create_candidate_via_api(client, headers_a, full_name="Cand A")
        assert cand_a.status_code == 201

        cand_b = create_candidate_via_api(client, headers_b, full_name="Cand B")
        assert cand_b.status_code == 201
        cand_b_id = cand_b.json()["id"]

        list_a = client.get("/api/v1/candidates/", headers=headers_a)
        assert list_a.status_code == 200
        ids_a = [c["id"] for c in list_a.json()["items"]]
        assert cand_b_id not in ids_a

    def test_org_a_cannot_see_org_b_assessments(self, client):
        """Create assessments in both orgs → A can't see B's."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_a = create_task_via_api(client, headers_a)
        assert task_a.status_code == 201
        task_b = create_task_via_api(client, headers_b)
        assert task_b.status_code == 201

        assess_a = create_assessment_via_api(client, headers_a, task_a.json()["id"])
        assert assess_a.status_code == 201

        assess_b = create_assessment_via_api(client, headers_b, task_b.json()["id"])
        assert assess_b.status_code == 201
        assess_b_id = assess_b.json()["id"]

        list_a = client.get("/api/v1/assessments/", headers=headers_a)
        assert list_a.status_code == 200
        ids_a = [a["id"] for a in list_a.json()["items"]]
        assert assess_b_id not in ids_a

    def test_org_a_cannot_get_org_b_task_by_id(self, client):
        """A tries to GET B's task → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_b = create_task_via_api(client, headers_b, name="Secret-B-Task")
        assert task_b.status_code == 201
        task_b_id = task_b.json()["id"]

        resp = client.get(f"/api/v1/tasks/{task_b_id}", headers=headers_a)
        assert resp.status_code == 404

    def test_org_a_cannot_get_org_b_candidate_by_id(self, client):
        """A tries to GET B's candidate → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        cand_b = create_candidate_via_api(client, headers_b, full_name="Secret Cand")
        assert cand_b.status_code == 201
        cand_b_id = cand_b.json()["id"]

        resp = client.get(f"/api/v1/candidates/{cand_b_id}", headers=headers_a)
        assert resp.status_code == 404

    def test_org_a_cannot_get_org_b_assessment_by_id(self, client):
        """A tries to GET B's assessment → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_b = create_task_via_api(client, headers_b)
        assert task_b.status_code == 201
        assess_b = create_assessment_via_api(client, headers_b, task_b.json()["id"])
        assert assess_b.status_code == 201
        assess_b_id = assess_b.json()["id"]

        resp = client.get(f"/api/v1/assessments/{assess_b_id}", headers=headers_a)
        assert resp.status_code == 404

    def test_org_a_cannot_delete_org_b_task(self, client):
        """A tries DELETE B's task → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_b = create_task_via_api(client, headers_b)
        assert task_b.status_code == 201
        task_b_id = task_b.json()["id"]

        resp = client.delete(f"/api/v1/tasks/{task_b_id}", headers=headers_a)
        assert resp.status_code == 404

        # Confirm B can still see their task
        check = client.get(f"/api/v1/tasks/{task_b_id}", headers=headers_b)
        assert check.status_code == 200

    def test_org_a_cannot_delete_org_b_candidate(self, client):
        """A tries DELETE B's candidate → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        cand_b = create_candidate_via_api(client, headers_b, full_name="Untouchable")
        assert cand_b.status_code == 201
        cand_b_id = cand_b.json()["id"]

        resp = client.delete(f"/api/v1/candidates/{cand_b_id}", headers=headers_a)
        assert resp.status_code == 404

        # Confirm B still has their candidate
        check = client.get(f"/api/v1/candidates/{cand_b_id}", headers=headers_b)
        assert check.status_code == 200

    def test_org_a_cannot_update_org_b_task(self, client):
        """A tries PATCH B's task → 404."""
        headers_a, headers_b = _setup_two_orgs(client)

        task_b = create_task_via_api(client, headers_b, name="Original Name")
        assert task_b.status_code == 201
        task_b_id = task_b.json()["id"]

        resp = client.patch(
            f"/api/v1/tasks/{task_b_id}",
            json={"name": "Hacked Name"},
            headers=headers_a,
        )
        assert resp.status_code == 404

        # Confirm original name is unchanged
        check = client.get(f"/api/v1/tasks/{task_b_id}", headers=headers_b)
        assert check.status_code == 200
        assert check.json()["name"] == "Original Name"

    def test_analytics_only_own_org(self, client):
        """Both orgs have data → A's analytics don't include B's counts."""
        headers_a, headers_b = _setup_two_orgs(client)

        # Create tasks + assessments in both orgs
        task_a = create_task_via_api(client, headers_a)
        assert task_a.status_code == 201
        task_b = create_task_via_api(client, headers_b)
        assert task_b.status_code == 201

        # Create 2 assessments for org B
        for _ in range(2):
            r = create_assessment_via_api(client, headers_b, task_b.json()["id"])
            assert r.status_code == 201

        # Create 1 assessment for org A
        assess_a = create_assessment_via_api(client, headers_a, task_a.json()["id"])
        assert assess_a.status_code == 201

        analytics_a = client.get("/api/v1/analytics/", headers=headers_a)
        assert analytics_a.status_code == 200
        data_a = analytics_a.json()
        # Org A should only see its own assessment(s)
        assert data_a["total_assessments"] == 1

        analytics_b = client.get("/api/v1/analytics/", headers=headers_b)
        assert analytics_b.status_code == 200
        data_b = analytics_b.json()
        assert data_b["total_assessments"] == 2
