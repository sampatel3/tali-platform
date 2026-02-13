"""API tests for scoring metadata contract."""

from tests.conftest import auth_headers


def test_scoring_metadata_contract(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/scoring/metadata", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "categories" in payload
    assert "metrics" in payload
    assert "communication" in payload["categories"]
    assert payload["categories"]["communication"]["metrics"]
    assert payload["metrics"]["tone_score"]["description"]
