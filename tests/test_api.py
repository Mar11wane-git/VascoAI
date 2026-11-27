"""Public endpoints and the image proxy's guard (no network calls)."""


def test_models_endpoint_lists_free_engine(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["providers"]]
    assert "vasco_free" in ids


def test_agent_tools_catalog(client):
    r = client.get("/api/agent/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert {"calculator", "web_search", "create_video"} <= names


def test_image_proxy_rejects_unsigned_links(client):
    r = client.get("/api/image", params={"p": "anything", "w": 512, "h": 512, "s": 1, "sig": "forged"})
    assert r.status_code == 403


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text or "id=\"root\"" in r.text
