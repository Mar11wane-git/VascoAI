def test_register_then_login(client, user):
    r = client.post("/api/auth/login", json={"email": user["email"], "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["user"]["id"] == user["user"]["id"]


def test_wrong_password_is_rejected(client, user):
    r = client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-password"})
    assert r.status_code == 400


def test_duplicate_email_is_rejected(client, user):
    r = client.post("/api/auth/register", json={"name": "X", "email": user["email"], "password": "secret123"})
    assert r.status_code == 400


def test_token_identifies_user(client, user):
    r = client.post("/api/auth/me", json={"token": user["token"]})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == user["email"]


def test_protected_routes_need_a_token(client):
    assert client.get("/api/profile").status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/profile", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_logout_invalidates_token(client):
    from tests.conftest import _register

    u = _register(client)
    assert client.post("/api/auth/logout", json={"token": u["token"]}).status_code == 200
    assert client.get("/api/profile", headers=u["headers"]).status_code == 401
