from tests.conftest import verify_user

def _register_and_login(client):
    client.post("/api/v1/auth/register", json={
        "email": "owner@example.com",
        "password": "testpass123",
        "full_name": "Owner",
        "organization_name": "Org A",
    })
    verify_user("owner@example.com")
    login_resp = client.post("/api/v1/auth/login", data={
        "username": "owner@example.com",
        "password": "testpass123",
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_candidate_crud_flow(client):
    headers = _register_and_login(client)

    create = client.post("/api/v1/candidates/", json={
        "email": "candidate@example.com",
        "full_name": "Candidate One",
        "position": "Backend Engineer",
    }, headers=headers)
    assert create.status_code == 201
    cid = create.json()["id"]

    lst = client.get("/api/v1/candidates/", headers=headers)
    assert lst.status_code == 200
    assert lst.json()["total"] == 1

    get_one = client.get(f"/api/v1/candidates/{cid}", headers=headers)
    assert get_one.status_code == 200
    assert get_one.json()["email"] == "candidate@example.com"

    upd = client.patch(f"/api/v1/candidates/{cid}", json={"position": "Staff Engineer"}, headers=headers)
    assert upd.status_code == 200
    assert upd.json()["position"] == "Staff Engineer"

    delete = client.delete(f"/api/v1/candidates/{cid}", headers=headers)
    assert delete.status_code == 204
