"""Public endpoints and the image proxy's guard (no network calls)."""


def test_models_endpoint_lists_free_engine(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["providers"]]
    assert "vasco_free" in ids


def test_local_engines_hidden_when_ollama_unreachable(client, monkeypatch):
    # On Vercel there is no Ollama: its models must not be offered (they can only fail).
    from backend import main

    async def unreachable():
        return None

    monkeypatch.setattr(main, "_installed_ollama_models", unreachable)
    monkeypatch.delenv("VASCO_LLM_URL", raising=False)
    ids = [p["id"] for p in client.get("/api/models").json()["providers"]]
    assert "ollama" not in ids and "vasco_llm" not in ids


def test_home_models_listed_when_installed(client, monkeypatch):
    from backend import main

    async def installed():
        return [{"id": "mr1:v1", "name": "mr1:v1", "badge": "Local", "vision": False, "tools": True},
                {"id": "qwen3:4b", "name": "qwen3:4b", "badge": "Local", "vision": False, "tools": True}]

    monkeypatch.setattr(main, "_installed_ollama_models", installed)
    monkeypatch.delenv("VASCO_LLM_URL", raising=False)
    providers = {p["id"]: p for p in client.get("/api/models").json()["providers"]}
    assert [m["name"] for m in providers["vasco_llm"]["models"]] == ["MR1 v1"]
    assert "ollama" in providers


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
