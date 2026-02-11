from tests.conftest import verify_user

def _register_and_login(client):
    """Helper: register a user and return auth headers."""
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
        "organization_name": "Test Org",
    })
    verify_user("test@example.com")
    login_resp = client.post("/api/v1/auth/login", data={
        "username": "test@example.com",
        "password": "testpass123",
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def _create_task(client, headers):
    """Helper: create a task."""
    resp = client.post("/api/v1/tasks", json={
        "name": "Test Task",
        "description": "A test task",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('hello')",
        "test_code": "assert True",
    }, headers=headers)
    return resp.json()

def test_create_assessment(client):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    
    response = client.post("/api/v1/assessments", json={
        "candidate_email": "candidate@example.com",
        "candidate_name": "Jane Doe",
        "task_id": task["id"],
        "duration_minutes": 30,
    }, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending"
    assert "token" in data

def test_list_assessments(client):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    
    client.post("/api/v1/assessments", json={
        "candidate_email": "c1@example.com",
        "candidate_name": "Candidate 1",
        "task_id": task["id"],
    }, headers=headers)
    
    response = client.get("/api/v1/assessments", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert payload["total"] == 1
    assert len(payload["items"]) == 1

def test_list_assessments_no_auth(client):
    response = client.get("/api/v1/assessments")
    assert response.status_code == 401

def test_create_task(client):
    headers = _register_and_login(client)
    response = client.post("/api/v1/tasks", json={
        "name": "Test Task",
        "description": "A test task",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('hello')",
        "test_code": "assert True",
    }, headers=headers)
    assert response.status_code == 201
    assert response.json()["name"] == "Test Task"

def test_list_tasks(client):
    headers = _register_and_login(client)
    _create_task(client, headers)
    response = client.get("/api/v1/tasks", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) >= 1
