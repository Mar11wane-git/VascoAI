"""User profile space: personal info, preferences, avatar, security, sessions, activity and usage stats."""
import base64
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from backend import db
from backend.auth import (
    default_avatar,
    get_user_row,
    log_activity,
    public_user,
    set_password,
    verify_password,
)

# field -> max length (text fields of user_profiles)
TEXT_FIELDS = {
    "first_name": 80, "last_name": 80, "headline": 140, "bio": 1000, "phone": 32,
    "country": 80, "city": 80, "job_title": 120, "company": 120, "website": 255,
    "github": 80, "linkedin": 255, "twitter": 80, "timezone": 64,
}
CHOICES = {
    "gender": {"", "homme", "femme", "autre"},
    "language": {"fr", "darija", "ar", "en"},
    "cover_theme": {"nebula", "ocean", "aurora", "sunset", "forest", "midnight", "rose"},
    "pref_theme": {"dark", "light"},
    "pref_voice": {"male", "female"},
}
BOOL_FIELDS = {"pref_auto_tts", "pref_web_search"}
MAX_INTERESTS = 12
MAX_AVATAR_BYTES = 600 * 1024
AVATAR_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")

# Fields that count towards the "profile completion" score.
COMPLETION_FIELDS = [
    ("avatar", "Photo de profil"), ("first_name", "Prénom"), ("last_name", "Nom"),
    ("headline", "Titre"), ("bio", "Bio"), ("phone", "Téléphone"), ("birth_date", "Date de naissance"),
    ("city", "Ville"), ("country", "Pays"), ("job_title", "Poste"), ("interests", "Centres d'intérêt"),
]


def _clean_text(value: Any, max_len: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[:max_len]


def _parse_interests(raw: Any) -> List[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    return [str(x) for x in (raw or []) if str(x).strip()][:MAX_INTERESTS]


def _completion(user_row: Dict[str, Any], prof: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    for key, label in COMPLETION_FIELDS:
        if key == "avatar":
            filled = (user_row.get("avatar") or "").startswith(("data:", "https://lh3", "https://avatars"))
        elif key == "interests":
            filled = bool(prof.get("interests"))
        else:
            filled = bool(prof.get(key))
        if not filled:
            missing.append(label)
    total = len(COMPLETION_FIELDS)
    return {"percent": round(100 * (total - len(missing)) / total), "missing": missing}


def get_profile(user_id: str) -> Dict[str, Any]:
    user_row = get_user_row(user_id)
    if not user_row:
        raise ValueError("Utilisateur introuvable.")
    db.execute("INSERT IGNORE INTO user_profiles (user_id) VALUES (%s)", (user_id,))
    db.execute("INSERT IGNORE INTO user_stats (user_id) VALUES (%s)", (user_id,))
    p = db.query_one("SELECT * FROM user_profiles WHERE user_id=%s", (user_id,)) or {}

    profile = {k: p.get(k) or "" for k in TEXT_FIELDS}
    profile["bio"] = p.get("bio") or ""
    profile["birth_date"] = db.to_iso_date(p.get("birth_date"))
    for k in CHOICES:
        profile[k] = p.get(k) or ""
    for k in BOOL_FIELDS:
        profile[k] = bool(p.get(k))
    profile["interests"] = _parse_interests(p.get("interests"))
    profile["system_prompt"] = p.get("system_prompt") or ""
    profile["updated_at"] = db.to_ts(p.get("updated_at"))

    return {
        "user": {
            **public_user(user_row),
            "last_login_at": db.to_ts(user_row.get("last_login_at")),
            "has_password": bool(user_row.get("password_hash")),
        },
        "profile": profile,
        "completion": _completion(user_row, profile),
        "stats": get_stats(user_id),
    }


def update_profile(user_id: str, data: Dict[str, Any], ip: str = "") -> Dict[str, Any]:
    sets: Dict[str, Any] = {}
    for key, max_len in TEXT_FIELDS.items():
        if key in data:
            sets[key] = _clean_text(data[key], max_len)
    for key, allowed in CHOICES.items():
        if key in data:
            val = str(data[key] or "").strip().lower()
            if val not in allowed:
                raise ValueError(f"Valeur invalide pour « {key} ».")
            sets[key] = val
    for key in BOOL_FIELDS:
        if key in data:
            sets[key] = 1 if data[key] else 0
    if "birth_date" in data:
        raw = str(data["birth_date"] or "").strip()
        if raw:
            try:
                bd = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Date de naissance invalide (format AAAA-MM-JJ).")
            if bd > date.today() or bd.year < 1900:
                raise ValueError("Date de naissance hors limites.")
            sets["birth_date"] = bd
        else:
            sets["birth_date"] = None
    if "interests" in data:
        tags = []
        for t in data["interests"] or []:
            t = _clean_text(t, 32)
            if t and t.lower() not in {x.lower() for x in tags}:
                tags.append(t)
        sets["interests"] = json.dumps(tags[:MAX_INTERESTS], ensure_ascii=False)
    if "system_prompt" in data:
        sets["system_prompt"] = _clean_text(data["system_prompt"], 4000)
    if "website" in sets and sets["website"] and not re.match(r"^https?://", sets["website"]):
        sets["website"] = "https://" + sets["website"]
    if "phone" in sets and sets["phone"] and not re.fullmatch(r"[+0-9 ().-]{6,32}", sets["phone"]):
        raise ValueError("Numéro de téléphone invalide.")

    if sets:
        cols = ", ".join(f"`{k}`=%s" for k in sets)
        db.execute(f"UPDATE user_profiles SET {cols} WHERE user_id=%s", (*sets.values(), user_id))

    # Display name / username live on the users table.
    user_sets: Dict[str, Any] = {}
    if "name" in data:
        name = _clean_text(data["name"], 120)
        if len(name) < 2:
            raise ValueError("Le nom affiché doit contenir au moins 2 caractères.")
        user_sets["name"] = name
    if "username" in data:
        uname = _clean_text(data["username"], 40).lstrip("@")
        if uname and not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", uname):
            raise ValueError("Nom d'utilisateur : 3 à 40 caractères (lettres, chiffres, _ . -).")
        if uname and db.query_one("SELECT id FROM users WHERE username=%s AND id<>%s", (uname, user_id)):
            raise ValueError("Ce nom d'utilisateur est déjà pris.")
        user_sets["username"] = uname
    if user_sets:
        cols = ", ".join(f"`{k}`=%s" for k in user_sets)
        db.execute(f"UPDATE users SET {cols} WHERE id=%s", (*user_sets.values(), user_id))

    only_prefs = all(k.startswith("pref_") or k == "system_prompt" for k in list(sets) + list(user_sets))
    if (sets or user_sets) and not only_prefs:
        log_activity(user_id, "profile_update", "Profil mis à jour", ip)
    return get_profile(user_id)


def update_avatar(user_id: str, data_url: str, ip: str = "") -> Dict[str, Any]:
    if not data_url:
        row = get_user_row(user_id)
        db.execute("UPDATE users SET avatar=%s WHERE id=%s", (default_avatar(row["name"]), user_id))
        log_activity(user_id, "avatar", "Photo de profil supprimée", ip)
        return get_profile(user_id)
    m = AVATAR_RE.match(data_url.strip())
    if not m:
        raise ValueError("Format d'image non pris en charge (PNG, JPEG ou WebP).")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise ValueError("Image corrompue.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise ValueError("Image trop lourde (600 Ko maximum).")
    db.execute("UPDATE users SET avatar=%s WHERE id=%s", (data_url.strip(), user_id))
    log_activity(user_id, "avatar", "Nouvelle photo de profil", ip)
    return get_profile(user_id)


def change_password(user_id: str, current: str, new: str, keep_token: str, ip: str = "") -> int:
    """Changes the password and signs out every other session. Returns how many were closed."""
    row = get_user_row(user_id)
    if row.get("password_hash") and not verify_password(row, current or ""):
        raise ValueError("Mot de passe actuel incorrect.")
    if current and current == new:
        raise ValueError("Le nouveau mot de passe doit être différent de l'ancien.")
    set_password(user_id, new)
    closed = db.execute("DELETE FROM sessions WHERE user_id=%s AND token<>%s", (user_id, keep_token))
    log_activity(user_id, "password", "Mot de passe modifié", ip)
    return closed


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def list_sessions(user_id: str, current_token: str) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT token, user_agent, ip_address, login_method, created_at, last_seen_at FROM sessions "
        "WHERE user_id=%s ORDER BY last_seen_at DESC LIMIT 20",
        (user_id,),
    )
    return [
        {
            "id": _session_id(r["token"]),
            "current": r["token"] == current_token,
            "user_agent": r["user_agent"],
            "ip": r["ip_address"],
            "method": r["login_method"],
            "created_at": db.to_ts(r["created_at"]),
            "last_seen_at": db.to_ts(r["last_seen_at"]),
        }
        for r in rows
    ]


def revoke_session(user_id: str, session_id: str, current_token: str) -> bool:
    for r in db.query("SELECT token FROM sessions WHERE user_id=%s", (user_id,)):
        if _session_id(r["token"]) == session_id:
            if r["token"] == current_token:
                raise ValueError("Utilisez « Se déconnecter » pour fermer la session actuelle.")
            db.execute("DELETE FROM sessions WHERE token=%s", (r["token"],))
            return True
    return False


def revoke_other_sessions(user_id: str, current_token: str) -> int:
    return db.execute("DELETE FROM sessions WHERE user_id=%s AND token<>%s", (user_id, current_token))


def list_activity(user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT kind, label, ip_address, created_at FROM user_activity WHERE user_id=%s "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (user_id, limit),
    )
    return [{"kind": r["kind"], "label": r["label"], "ip": r["ip_address"], "at": db.to_ts(r["created_at"])} for r in rows]


def get_stats(user_id: str) -> Dict[str, Any]:
    s = db.query_one("SELECT * FROM user_stats WHERE user_id=%s", (user_id,)) or {}
    start = date.today() - timedelta(days=13)
    rows = db.query(
        "SELECT day, messages FROM user_daily_usage WHERE user_id=%s AND day >= %s",
        (user_id, start),
    )
    by_day = {r["day"]: r["messages"] for r in rows}
    days = [start + timedelta(days=i) for i in range(14)]
    return {
        "messages_sent": s.get("messages_sent", 0),
        "tool_calls": s.get("tool_calls", 0),
        "voice_turns": s.get("voice_turns", 0),
        "files_uploaded": s.get("files_uploaded", 0),
        "images_generated": s.get("images_generated", 0),
        "last_active_at": db.to_ts(s.get("last_active_at")),
        "daily": [{"day": d.isoformat(), "messages": by_day.get(d, 0)} for d in days],
    }


def record_usage(user_id: str, messages: int = 0, tool_calls: int = 0, voice_turns: int = 0,
                 files: int = 0, images: int = 0) -> None:
    """Increments usage counters; never raises (stats must not break a chat)."""
    try:
        db.execute(
            """INSERT INTO user_stats (user_id, messages_sent, tool_calls, voice_turns, files_uploaded,
                                       images_generated, last_active_at)
               VALUES (%s,%s,%s,%s,%s,%s,NOW())
               ON DUPLICATE KEY UPDATE messages_sent=messages_sent+VALUES(messages_sent),
                   tool_calls=tool_calls+VALUES(tool_calls), voice_turns=voice_turns+VALUES(voice_turns),
                   files_uploaded=files_uploaded+VALUES(files_uploaded),
                   images_generated=images_generated+VALUES(images_generated), last_active_at=NOW()""",
            (user_id, messages, tool_calls, voice_turns, files, images),
        )
        if messages or voice_turns:
            db.execute(
                """INSERT INTO user_daily_usage (user_id, day, messages) VALUES (%s, CURDATE(), %s)
                   ON DUPLICATE KEY UPDATE messages=messages+VALUES(messages)""",
                (user_id, messages + voice_turns),
            )
    except db.DatabaseError:
        pass


def delete_account(user_id: str, password: str, confirm: str) -> None:
    row = get_user_row(user_id)
    if not row:
        return
    if row.get("password_hash"):
        if not verify_password(row, password or ""):
            raise ValueError("Mot de passe incorrect.")
    elif (confirm or "").strip().upper() != "SUPPRIMER":
        raise ValueError("Tapez SUPPRIMER pour confirmer.")
    db.execute("DELETE FROM users WHERE id=%s", (user_id,))
