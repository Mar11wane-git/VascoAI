def _save(client, user, conv_id, title="Salam", messages=None, created_at=1000):
    messages = messages if messages is not None else [
        {"role": "user", "content": "salam"},
        {"role": "assistant", "content": "wa 3alaykum salam", "parts": [{"type": "text", "text": "wa 3alaykum salam"}]},
    ]
    return client.put(f"/api/conversations/{conv_id}", headers=user["headers"],
                      json={"title": title, "messages": messages, "created_at": created_at})


def _list(client, user):
    r = client.get("/api/conversations", headers=user["headers"])
    assert r.status_code == 200
    return r.json()


def test_save_and_list(client, user):
    assert _list(client, user) == {"conversations": [], "deleted": []}
    r = _save(client, user, "conv_1")
    assert r.status_code == 200 and r.json()["updatedAt"] > 0
    data = _list(client, user)
    assert [c["id"] for c in data["conversations"]] == ["conv_1"]
    conv = data["conversations"][0]
    assert conv["title"] == "Salam"
    assert conv["createdAt"] == 1000
    assert conv["messages"][1]["parts"][0]["text"] == "wa 3alaykum salam"


def test_update_replaces_content_and_orders_newest_first(client, user):
    _save(client, user, "conv_a", title="A")
    _save(client, user, "conv_b", title="B")
    _save(client, user, "conv_a", title="A2", messages=[{"role": "user", "content": "jdid"}])
    convs = _list(client, user)["conversations"]
    assert [c["id"] for c in convs] == ["conv_a", "conv_b"]
    assert convs[0]["title"] == "A2" and convs[0]["messages"] == [{"role": "user", "content": "jdid"}]


def test_delete_leaves_tombstone(client, user):
    _save(client, user, "conv_x")
    assert client.delete("/api/conversations/conv_x", headers=user["headers"]).status_code == 200
    data = _list(client, user)
    assert data["conversations"] == [] and data["deleted"] == ["conv_x"]
    # Saving again (e.g. edited offline on another device) brings it back.
    _save(client, user, "conv_x")
    assert [c["id"] for c in _list(client, user)["conversations"]] == ["conv_x"]


def test_users_cannot_see_each_other(client, user, other_user):
    _save(client, user, "conv_private", title="Secret")
    assert _list(client, other_user)["conversations"] == []
    # Same id from another account is a different conversation, not an overwrite.
    _save(client, other_user, "conv_private", title="Other")
    assert _list(client, user)["conversations"][0]["title"] == "Secret"
    client.delete("/api/conversations/conv_private", headers=other_user["headers"])
    assert _list(client, user)["conversations"][0]["title"] == "Secret"


def test_invalid_id_is_rejected(client, user):
    assert _save(client, user, "bad id!").status_code in (400, 404)
    assert _save(client, user, "x" * 65).status_code == 400


def test_oversized_conversation_is_rejected(client, user):
    big = [{"role": "user", "content": "a" * 2_100_000}]
    assert _save(client, user, "conv_big", messages=big).status_code == 400


def test_title_defaults_and_is_trimmed(client, user):
    _save(client, user, "conv_t", title="   ")
    _save(client, user, "conv_long", title="t" * 500)
    titles = {c["id"]: c["title"] for c in _list(client, user)["conversations"]}
    assert titles["conv_t"] == "Discussion"
    assert len(titles["conv_long"]) == 200
