import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from typing import Dict, Any, Optional
import httpx
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
USERS_DB_PATH = os.path.join(os.path.dirname(__file__), "users_db.json")
load_dotenv(ENV_PATH)

# Built-in Google OAuth 2.0 Desktop/Loopback Client for immediate real Google Sign-In on localhost
DEFAULT_GOOGLE_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
DEFAULT_GOOGLE_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
GOOGLE_REDIRECT_URI = "http://localhost:8000/api/auth/google/callback"

# Built-in GitHub OAuth 2.0 Public Client ID for official GitHub Device Flow (https://github.com/login/device)
DEFAULT_GITHUB_DEVICE_CLIENT_ID = "178c6fc778ccc68e1d6a"
GITHUB_REDIRECT_URI = "http://localhost:8000/api/auth/github/callback"

# In-memory store for completed OAuth state sessions (survives COOP popup opener severing)
COMPLETED_OAUTH_STATES: Dict[str, Dict[str, Any]] = {}


def _load_db() -> Dict[str, Any]:
    if not os.path.exists(USERS_DB_PATH):
        return {"users": {}, "sessions": {}}
    try:
        with open(USERS_DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "users" not in data:
                data["users"] = {}
            if "sessions" not in data:
                data["sessions"] = {}
            return data
    except Exception:
        return {"users": {}, "sessions": {}}


def _save_db(data: Dict[str, Any]) -> None:
    with open(USERS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()


def create_session(user: Dict[str, Any]) -> Dict[str, Any]:
    db = _load_db()
    token = secrets.token_urlsafe(32)
    safe_user = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "provider": user.get("provider", "email"),
        "avatar": user.get("avatar", ""),
        "username": user.get("username", ""),
        "created_at": user.get("created_at", int(time.time())),
    }
    db["sessions"][token] = {
        "user_id": user["id"],
        "created_at": int(time.time()),
    }
    _save_db(db)
    return {"token": token, "user": safe_user}


def register_email_user(name: str, email: str, password: str) -> Dict[str, Any]:
    email_clean = email.strip().lower()
    name_clean = name.strip() or email_clean.split("@")[0].capitalize()
    if not email_clean or "@" not in email_clean:
        raise ValueError("Adresse e-mail invalide.")
    if len(password) < 4:
        raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")

    db = _load_db()
    if email_clean in db["users"]:
        raise ValueError("Un compte existe déjà avec cette adresse e-mail. Connectez-vous.")

    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    user_id = f"usr_{secrets.token_hex(8)}"
    avatar_url = f"https://ui-avatars.com/api/?name={urllib.parse.quote(name_clean)}&background=2563eb&color=fff&bold=true"

    user_obj = {
        "id": user_id,
        "name": name_clean,
        "email": email_clean,
        "provider": "email",
        "avatar": avatar_url,
        "salt": salt,
        "password_hash": pwd_hash,
        "created_at": int(time.time()),
    }
    db["users"][email_clean] = user_obj
    _save_db(db)
    return create_session(user_obj)


def login_email_user(email: str, password: str) -> Dict[str, Any]:
    email_clean = email.strip().lower()
    db = _load_db()
    user = db["users"].get(email_clean)
    if not user:
        raise ValueError("Aucun compte trouvé avec cet e-mail.")
    if user.get("provider") in ("google", "github") and not user.get("password_hash"):
        raise ValueError(f"Ce compte a été créé via {user['provider'].capitalize()}. Utilisez l'icône {user['provider'].capitalize()}.")

    salt = user.get("salt", "")
    expected_hash = user.get("password_hash", "")
    if _hash_password(password, salt) != expected_hash:
        raise ValueError("Mot de passe incorrect.")

    return create_session(user)


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
    db = _load_db()

    bg_color = "4285F4" if provider == "google" else "24292F"
    default_avatar = f"https://ui-avatars.com/api/?name={urllib.parse.quote(name_clean)}&background={bg_color}&color=fff&bold=true"

    if email_clean in db["users"]:
        user_obj = db["users"][email_clean]
        user_obj["name"] = name_clean or user_obj.get("name")
        user_obj["provider"] = provider
        if avatar:
            user_obj["avatar"] = avatar
        if username:
            user_obj["username"] = username
        if provider_uid:
            user_obj["provider_uid"] = provider_uid
    else:
        user_obj = {
            "id": f"usr_{secrets.token_hex(8)}",
            "name": name_clean,
            "email": email_clean,
            "provider": provider,
            "avatar": avatar or default_avatar,
            "username": username,
            "provider_uid": provider_uid,
            "created_at": int(time.time()),
        }
        db["users"][email_clean] = user_obj

    _save_db(db)
    return create_session(user_obj)


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    db = _load_db()
    sess = db["sessions"].get(token)
    if not sess:
        return None
    uid = sess.get("user_id")
    for u in db["users"].values():
        if u.get("id") == uid:
            return {
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "provider": u.get("provider", "email"),
                "avatar": u.get("avatar", ""),
                "username": u.get("username", ""),
            }
    return None


def logout_token(token: str) -> None:
    if not token:
        return
    db = _load_db()
    if token in db["sessions"]:
        del db["sessions"][token]
        _save_db(db)


# =====================================================================
# REAL GOOGLE OAUTH 2.0 (https://accounts.google.com/o/oauth2/v2/auth)
# =====================================================================

def get_google_oauth_credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH, override=True)
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip() or DEFAULT_GOOGLE_CLIENT_ID
    csec = os.getenv("GOOGLE_CLIENT_SECRET", "").strip() or DEFAULT_GOOGLE_CLIENT_SECRET
    return cid, csec


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
    cid = os.getenv("GITHUB_CLIENT_ID", "").strip() or DEFAULT_GITHUB_DEVICE_CLIENT_ID
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
