"""Chat history stored per user, so conversations follow the user across devices.

The browser keeps its own copy (localStorage) and syncs: it uploads a conversation whenever it
changes and merges the server's list when the user signs in. Deletions leave a tombstone row
(deleted=1, no data) for a while so other devices also drop the conversation."""
import json
import re
import time
from typing import Any, Dict, List

from backend import db

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_DATA_CHARS = 2_000_000       # one conversation's JSON (attachment previews are trimmed by the client)
MAX_CONVERSATIONS = 500          # per user; the oldest are dropped beyond this
TOMBSTONE_DAYS = 60


def _now_ms() -> int:
    return int(time.time() * 1000)


def _check_id(conv_id: str) -> str:
    if not ID_RE.match(conv_id or ""):
        raise ValueError("Identifiant de discussion invalide.")
    return conv_id


def list_conversations(user_id: str) -> Dict[str, Any]:
    rows = db.query(
        "SELECT id, title, data, created_ms, updated_ms, deleted FROM conversations "
        "WHERE user_id=%s ORDER BY updated_ms DESC",
        (user_id,),
    )
    convs: List[Dict[str, Any]] = []
    deleted: List[str] = []
    for r in rows:
        if r["deleted"]:
            deleted.append(r["id"])
            continue
        try:
            messages = json.loads(r["data"] or "[]")
        except ValueError:
            messages = []
        convs.append({
            "id": r["id"], "title": r["title"], "messages": messages,
            "createdAt": int(r["created_ms"] or 0), "updatedAt": int(r["updated_ms"] or 0),
        })
    return {"conversations": convs, "deleted": deleted}


def save_conversation(user_id: str, conv_id: str, title: str, messages: List[Any], created_ms: int = 0) -> int:
    _check_id(conv_id)
    if not isinstance(messages, list):
        raise ValueError("messages doit être une liste.")
    data = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    if len(data) > MAX_DATA_CHARS:
        raise ValueError("Discussion trop volumineuse pour être synchronisée.")
    title = (title or "").strip()[:200] or "Discussion"
    now = _now_ms()
    created_ms = int(created_ms or now)
    params = (user_id, conv_id, title, data, created_ms, now)
    if db.is_postgres():
        db.execute(
            "INSERT INTO conversations (user_id, id, title, data, created_ms, updated_ms, deleted) "
            "VALUES (%s,%s,%s,%s,%s,%s,0) ON CONFLICT (user_id, id) DO UPDATE SET "
            "title=EXCLUDED.title, data=EXCLUDED.data, updated_ms=EXCLUDED.updated_ms, deleted=0",
            params,
        )
    else:
        db.execute(
            "INSERT INTO conversations (user_id, id, title, data, created_ms, updated_ms, deleted) "
            "VALUES (%s,%s,%s,%s,%s,%s,0) ON DUPLICATE KEY UPDATE "
            "title=VALUES(title), data=VALUES(data), updated_ms=VALUES(updated_ms), deleted=0",
            params,
        )
    _prune(user_id)
    return now


def delete_conversation(user_id: str, conv_id: str) -> None:
    _check_id(conv_id)
    now = _now_ms()
    if db.is_postgres():
        db.execute(
            "INSERT INTO conversations (user_id, id, data, updated_ms, deleted) VALUES (%s,%s,NULL,%s,1) "
            "ON CONFLICT (user_id, id) DO UPDATE SET data=NULL, updated_ms=EXCLUDED.updated_ms, deleted=1",
            (user_id, conv_id, now),
        )
    else:
        db.execute(
            "INSERT INTO conversations (user_id, id, data, updated_ms, deleted) VALUES (%s,%s,NULL,%s,1) "
            "ON DUPLICATE KEY UPDATE data=NULL, updated_ms=VALUES(updated_ms), deleted=1",
            (user_id, conv_id, now),
        )


def _prune(user_id: str) -> None:
    """Drops old tombstones and anything beyond MAX_CONVERSATIONS (oldest first)."""
    cutoff = _now_ms() - TOMBSTONE_DAYS * 86_400_000
    db.execute("DELETE FROM conversations WHERE user_id=%s AND deleted=1 AND updated_ms < %s", (user_id, cutoff))
    row = db.query_one("SELECT COUNT(*) AS n FROM conversations WHERE user_id=%s AND deleted=0", (user_id,))
    extra = int(row["n"] if row else 0) - MAX_CONVERSATIONS
    if extra > 0:
        old = db.query(
            "SELECT id FROM conversations WHERE user_id=%s AND deleted=0 ORDER BY updated_ms ASC LIMIT %s",
            (user_id, extra),
        )
        for r in old:
            db.execute("DELETE FROM conversations WHERE user_id=%s AND id=%s", (user_id, r["id"]))
