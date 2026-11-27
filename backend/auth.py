import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from typing import Dict, Any, Optional
import httpx
from dotenv import load_dotenv

from backend import db

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(ENV_PATH)

SESSION_MAX_IDLE_DAYS = 60
MIN_PASSWORD_LENGTH = 6

# Built-in Google OAuth 2.0 Desktop/Loopback Client for immediate real Google Sign-In on localhost
DEFAULT_GOOGLE_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
DEFAULT_GOOGLE_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
# Public address of the app (e.g. https://vasco.up.railway.app): OAuth providers redirect back to it.
APP_URL = (os.getenv("APP_URL", "").strip() or "http://localhost:8000").rstrip("/")
IS_LOCAL = APP_URL.startswith(("http://localhost", "http://127.0.0.1"))
GOOGLE_REDIRECT_URI = f"{APP_URL}/api/auth/google/callback"

# Built-in GitHub OAuth 2.0 Public Client ID for official GitHub Device Flow (https://github.com/login/device)
DEFAULT_GITHUB_DEVICE_CLIENT_ID = "178c6fc778ccc68e1d6a"
GITHUB_REDIRECT_URI = f"{APP_URL}/api/auth/github/callback"


def can_manage_server_keys(user: Dict[str, Any]) -> bool:
    """Server API keys are shared by every user: only admins may change them once the app is public."""
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if admins:
        return (user.get("email") or "").lower() in admins
    return IS_LOCAL

# Completed OAuth sign-ins, polled by the login page (survives COOP popup opener severing)
COMPLETED_OAUTH_STATES = db.OAuthStateStore()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()


def verify_password(user_row: Dict[str, Any], password: str) -> bool:
    expected = user_row.get("password_hash") or ""
    if not expected:
        return False
    return hmac.compare_digest(_hash_password(password, user_row.get("salt") or ""), expected)


def set_password(user_id: str, new_password: str) -> None:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")
    salt = secrets.token_hex(16)
    db.execute(
        "UPDATE users SET salt=%s, password_hash=%s WHERE id=%s",
        (salt, _hash_password(new_password, salt), user_id),
    )


def default_avatar(name: str, bg: str = "7c3aed") -> str:
    return f"https://ui-avatars.com/api/?name={urllib.parse.quote(name)}&background={bg}&color=fff&bold=true"


def public_user(row: Dict[str, Any]) -> Dict[str, Any]:
    """The user fields safe to send to the browser."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "provider": row.get("provider") or "email",
        "avatar": row.get("avatar") or default_avatar(row["name"]),
        "username": row.get("username") or "",
        "created_at": db.to_ts(row.get("created_at")) or int(time.time()),
    }


def log_activity(user_id: str, kind: str, label: str = "", ip: str = "") -> None:
    try:
        db.execute(
            "INSERT INTO user_activity (user_id, kind, label, ip_address) VALUES (%s,%s,%s,%s)",
            (user_id, kind, label[:255], ip[:64]),
        )
    except db.DatabaseError:
        pass


def get_user_row(user_id: str) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM users WHERE id=%s", (user_id,))


def _insert_user(user_id: str, email: str, name: str, provider: str, avatar: str,
                 username: str = "", provider_uid: str = "", salt: str = "", pwd_hash: str = "") -> None:
    first, _, last = name.partition(" ")
    db.execute(
        """INSERT INTO users (id, email, name, username, provider, provider_uid, avatar, salt, password_hash)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (user_id, email, name[:120], username[:80], provider, provider_uid[:64], avatar, salt, pwd_hash),
    )
    db.execute(
        "INSERT IGNORE INTO user_profiles (user_id, first_name, last_name) VALUES (%s,%s,%s)",
        (user_id, first[:80], last.strip()[:80]),
    )
    db.execute("INSERT IGNORE INTO user_stats (user_id) VALUES (%s)", (user_id,))


def create_session(user: Dict[str, Any], login_method: str = "", user_agent: str = "", ip: str = "") -> Dict[str, Any]:
    token = secrets.token_urlsafe(32)
    method = login_method or user.get("provider") or "email"
    db.execute(
        "INSERT INTO sessions (token, user_id, user_agent, ip_address, login_method) VALUES (%s,%s,%s,%s,%s)",
        (token, user["id"], (user_agent or "")[:255], (ip or "")[:64], method),
    )
    db.execute("UPDATE users SET last_login_at=NOW() WHERE id=%s", (user["id"],))
    log_activity(user["id"], "login", f"Connexion via {method.capitalize()}", ip)
    row = get_user_row(user["id"]) or user
    return {"token": token, "user": public_user(row)}


def register_email_user(name: str, email: str, password: str, user_agent: str = "", ip: str = "") -> Dict[str, Any]:
    email_clean = email.strip().lower()
    name_clean = name.strip() or email_clean.split("@")[0].capitalize()
    if not email_clean or "@" not in email_clean or "." not in email_clean.split("@")[-1]:
        raise ValueError("Adresse e-mail invalide.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")
    if db.query_one("SELECT id FROM users WHERE email=%s", (email_clean,)):
        raise ValueError("Un compte existe déjà avec cette adresse e-mail. Connectez-vous.")

    salt = secrets.token_hex(16)
    user_id = f"usr_{secrets.token_hex(8)}"
    _insert_user(user_id, email_clean, name_clean, "email", default_avatar(name_clean),
                 salt=salt, pwd_hash=_hash_password(password, salt))
    log_activity(user_id, "register", "Création du compte", ip)
    return create_session({"id": user_id, "provider": "email"}, "email", user_agent, ip)


def login_email_user(email: str, password: str, user_agent: str = "", ip: str = "") -> Dict[str, Any]:
    email_clean = email.strip().lower()
    user = db.query_one("SELECT * FROM users WHERE email=%s", (email_clean,))
    if not user:
        raise ValueError("Aucun compte trouvé avec cet e-mail.")
    if user.get("provider") in ("google", "github") and not user.get("password_hash"):
        raise ValueError(f"Ce compte a été créé via {user['provider'].capitalize()}. Utilisez l'icône {user['provider'].capitalize()}.")
    if not verify_password(user, password):
        raise ValueError("Mot de passe incorrect.")
    return create_session(user, "email", user_agent, ip)


def oauth_upsert_user(
    provider: str,
    email: str,
    name: str,
    avatar: str = "",
    username: str = "",
    provider_uid: str = "",
) -> Dict[str, Any]:
    """Creates or updates a real user authenticated via Google OAuth 2.0 or GitHub OAuth 2.0."""
    email_clean = email.strip().lower()
    name_clean = name.strip() or username.strip() or email_clean.split("@")[0].capitalize()
    existing = db.query_one("SELECT * FROM users WHERE email=%s", (email_clean,))

    if existing:
        # A photo the user uploaded themselves (data URL) is kept over the provider's picture.
        old_avatar = existing.get("avatar") or ""
        keep_avatar = old_avatar.startswith("data:") or not avatar
        db.execute(
            "UPDATE users SET provider=%s, avatar=%s, username=%s, provider_uid=%s WHERE id=%s",
            (provider, old_avatar if keep_avatar else avatar,
             username[:80] or existing.get("username") or "",
             provider_uid[:64] or existing.get("provider_uid") or "", existing["id"]),
        )
        return create_session(existing, provider)

    bg_color = "4285F4" if provider == "google" else "24292F"
    user_id = f"usr_{secrets.token_hex(8)}"
    _insert_user(user_id, email_clean, name_clean, provider, avatar or default_avatar(name_clean, bg_color),
                 username=username, provider_uid=provider_uid)
    log_activity(user_id, "register", f"Création du compte via {provider.capitalize()}")
    return create_session({"id": user_id, "provider": provider}, provider)


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = db.query_one(
        """SELECT u.*, s.last_seen_at AS session_seen FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token=%s AND s.last_seen_at > NOW() - INTERVAL %s DAY""",
        (token, SESSION_MAX_IDLE_DAYS),
    )
    if not row:
        return None
    db.execute(
        "UPDATE sessions SET last_seen_at=NOW() WHERE token=%s AND last_seen_at < NOW() - INTERVAL 5 MINUTE",
        (token,),
    )
    return public_user(row)


def logout_token(token: str) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token=%s", (token,))


# =====================================================================
# REAL GOOGLE OAUTH 2.0 (https://accounts.google.com/o/oauth2/v2/auth)
# =====================================================================

GOOGLE_NOT_CONFIGURED = (
    "La connexion Google n'est pas encore configurée sur ce serveur : "
    "ajoutez GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET (votre propre client OAuth Google)."
)
GITHUB_NOT_CONFIGURED = (
    "La connexion GitHub n'est pas encore configurée sur ce serveur : "
    "ajoutez GITHUB_CLIENT_ID et GITHUB_CLIENT_SECRET (votre propre OAuth App GitHub)."
)


def get_google_oauth_credentials() -> tuple[str, str]:
    """The app's own Google OAuth client. The built-in loopback client only works on localhost."""
    load_dotenv(ENV_PATH, override=True)
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if cid and csec:
        return cid, csec
    if IS_LOCAL:
        return DEFAULT_GOOGLE_CLIENT_ID, DEFAULT_GOOGLE_CLIENT_SECRET
    raise ValueError(GOOGLE_NOT_CONFIGURED)


def build_google_authorize_url(state: str) -> str:
    cid, _ = get_google_oauth_credentials()
    params = {
        "client_id": cid,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def _decode_jwt_payload(jwt_token: str) -> Dict[str, Any]:
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


async def exchange_google_code_for_session(code: str, state: str = "") -> Dict[str, Any]:
    cid, csec = get_google_oauth_credentials()
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cid,
                "client_secret": csec,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise ValueError(f"Erreur Google OAuth Token ({token_resp.status_code}): {token_resp.text}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        id_token = token_data.get("id_token", "")

        profile: Dict[str, Any] = {}
        if access_token:
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_resp.status_code == 200:
                profile = userinfo_resp.json()

        if not profile.get("email") and id_token:
            profile = _decode_jwt_payload(id_token)

        email = profile.get("email", "")
        if not email:
            raise ValueError("Impossible de récupérer l'adresse e-mail depuis le compte Google.")

        name = profile.get("name") or profile.get("given_name") or email.split("@")[0]
        picture = profile.get("picture", "")
        sub = str(profile.get("sub", ""))

        session = oauth_upsert_user(
            provider="google",
            email=email,
            name=name,
            avatar=picture,
            provider_uid=sub,
        )
        if state:
            COMPLETED_OAUTH_STATES[state] = {
                "status": "completed",
                "token": session["token"],
                "user": session["user"],
                "timestamp": time.time(),
            }
        return session


# =====================================================================
# REAL GITHUB OAUTH 2.0 (Web Redirect + Official GitHub Device Flow)
# =====================================================================

def get_github_web_credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH, override=True)
    cid = os.getenv("GITHUB_CLIENT_ID", "").strip()
    csec = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    return cid, csec


def build_github_authorize_url(state: str) -> Optional[str]:
    cid, csec = get_github_web_credentials()
    if not cid or not csec:
        return None
    params = {
        "client_id": cid,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
    }
    return f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"


async def fetch_github_user_with_token(access_token: str, state: str = "") -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Vasco-AI-Studio",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        u_resp = await client.get("https://api.github.com/user", headers=headers)
        if u_resp.status_code != 200:
            raise ValueError(f"Impossible de lire le profil GitHub ({u_resp.status_code}).")
        u_data = u_resp.json()

        login = u_data.get("login", "")
        name = u_data.get("name") or login
        avatar_url = u_data.get("avatar_url", "")
        gh_id = str(u_data.get("id", ""))
        email = u_data.get("email") or ""

        if not email:
            try:
                e_resp = await client.get("https://api.github.com/user/emails", headers=headers)
                if e_resp.status_code == 200:
                    emails = e_resp.json()
                    if isinstance(emails, list) and emails:
                        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
                        if not primary:
                            primary = next((e for e in emails if e.get("verified")), emails[0])
                        email = primary.get("email", "")
            except Exception:
                pass

        if not email:
            email = f"{login}@users.noreply.github.com"

        session = oauth_upsert_user(
            provider="github",
            email=email,
            name=name,
            avatar=avatar_url,
            username=login,
            provider_uid=gh_id,
        )
        if state:
            COMPLETED_OAUTH_STATES[state] = {
                "status": "completed",
                "token": session["token"],
                "user": session["user"],
                "timestamp": time.time(),
            }
        return session


async def exchange_github_code_for_session(code: str, state: str = "") -> Dict[str, Any]:
    cid, csec = get_github_web_credentials()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": cid,
                "client_secret": csec,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
        )
        data = resp.json()
        access_token = data.get("access_token", "")
        if not access_token:
            raise ValueError(f"Erreur GitHub OAuth: {data.get('error_description') or data}")
        return await fetch_github_user_with_token(access_token, state=state)


async def start_github_device_flow() -> Dict[str, Any]:
    """Starts an official GitHub OAuth 2.0 Device Flow against https://github.com/login/device."""
    load_dotenv(ENV_PATH, override=True)
    cid = os.getenv("GITHUB_CLIENT_ID", "").strip()
    if not cid:
        if not IS_LOCAL:
            raise ValueError(GITHUB_NOT_CONFIGURED)
        cid = DEFAULT_GITHUB_DEVICE_CLIENT_ID
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json"},
            data={
                "client_id": cid,
                "scope": "read:user user:email",
            },
        )
        if resp.status_code != 200:
            raise ValueError(f"Erreur GitHub Device Flow ({resp.status_code}): {resp.text}")
        data = resp.json()
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
            "expires_in": data.get("expires_in", 900),
            "interval": data.get("interval", 5),
        }


async def poll_github_device_flow(device_code: str) -> Dict[str, Any]:
    """Polls GitHub OAuth token endpoint until the user authorizes the device on github.com."""
    load_dotenv(ENV_PATH, override=True)
    cid = os.getenv("GITHUB_CLIENT_ID", "").strip() or DEFAULT_GITHUB_DEVICE_CLIENT_ID
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": cid,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        data = resp.json()
        if "access_token" in data and data["access_token"]:
            session = await fetch_github_user_with_token(data["access_token"])
            return {
                "status": "completed",
                "token": session["token"],
                "user": session["user"],
            }

        err = data.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return {"status": "pending", "error": err, "interval": data.get("interval", 5)}
        raise ValueError(data.get("error_description") or f"Erreur GitHub OAuth: {err}")
