import base64
import json
import os

import httpx
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header, WebSocket, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, RedirectResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from backend.file_processor import process_uploaded_file
from backend.web_search import (
    perform_web_search,
    format_search_results_for_prompt,
    should_run_live_search,
)
from backend.darija import synthesize_speech_mp3, detect_conversation_language
from backend.voice import VoiceError, server_stt_available, transcribe_audio, voice_reply
from backend.live import handle_live, live_available
from backend.auth import (
    register_email_user,
    login_email_user,
    oauth_upsert_user,
    get_user_by_token,
    logout_token,
    build_google_authorize_url,
    exchange_google_code_for_session,
    build_github_authorize_url,
    exchange_github_code_for_session,
    start_github_device_flow,
    poll_github_device_flow,
    can_manage_server_keys,
    COMPLETED_OAUTH_STATES,
)
from backend.ai_providers import (
    PROVIDERS_CATALOG,
    get_api_keys_status,
    save_api_keys,
    generate_vasco_response,
)
from backend.agent.runner import run_agent
from backend.agent.tools import tools_catalog, code_execution_enabled
from backend import db
from backend import profile as profiles


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await run_in_threadpool(db.init_db)
    cfg = db._config()
    print(f"[Vasco] Base MySQL prête : {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}")
    yield


app = FastAPI(
    title="Vasco AI Agent API",
    description="Agent IA autonome multimodal : outils, recherche web, streaming temps réel (Darija, Français, Anglais, Arabe)",
    version="4.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


def bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def require_user(token: str = Depends(bearer_token)) -> Dict[str, Any]:
    """Resolves the session from an `Authorization: Bearer <token>` header."""
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expirée ou invalide. Reconnectez-vous.")
    return user


def client_info(request: Request) -> Dict[str, str]:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return {
        "user_agent": request.headers.get("user-agent", ""),
        "ip": forwarded or (request.client.host if request.client else ""),
    }


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    provider: str = "vasco_free"
    model: str = "openai"
    messages: List[ChatMessage]
    attachments: List[Dict[str, Any]] = []
    deep_think: bool = False
    web_search: bool = True
    image_gen: bool = False
    voice_mode: bool = False
    system_prompt: str = ""


class AgentRequest(BaseModel):
    provider: str = "vasco_free"
    model: str = "openai"
    messages: List[ChatMessage]
    attachments: List[Dict[str, Any]] = []
    deep_think: bool = False
    web_search: bool = True
    image_gen: bool = False
    system_prompt: str = ""


class TTSRequest(BaseModel):
    text: str
    voice: str = "male"


class VoiceReplyRequest(BaseModel):
    provider: str = "vasco_free"
    model: str = "openai"
    messages: List[ChatMessage]
    system_prompt: str = ""
    web_search: bool = True
    voice: str = "male"
    speak: bool = True  # False: return the text at once, the client fetches /api/tts itself


class AuthRegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthTokenRequest(BaseModel):
    token: str


class GitHubDevicePollRequest(BaseModel):
    device_code: str


class ApiKeysUpdateRequest(BaseModel):
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    POLLINATIONS_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: Optional[str] = None


def _render_oauth_popup_html(session: Optional[Dict[str, Any]] = None, error: str = "") -> HTMLResponse:
    """Renders an HTML page inside the OAuth popup that notifies the parent React window and closes itself."""
    if error:
        payload_json = json.dumps({"type": "VASCO_OAUTH_ERROR", "error": error})
        title = "Erreur d'authentification"
        subtitle = error
        color = "#ef4444"
    else:
        payload_json = json.dumps({
            "type": "VASCO_OAUTH_SUCCESS",
            "token": session["token"],
            "user": session["user"],
        })
        title = f"Connecté en tant que {session['user']['name']}"
        subtitle = f"Compte vérifié ({session['user']['email']}). Redirection vers Vasco..."
        color = "#10b981"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <title>Vasco OAuth 2.0</title>
  <style>
    body {{
      margin: 0; height: 100vh; display: flex; align-items: center; justify-content: center;
      background: #0b0d13; color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      text-align: center;
    }}
    .box {{
      background: #12151f; border: 1px solid rgba(255,255,255,0.1); border-radius: 20px;
      padding: 32px 28px; max-width: 380px; box-shadow: 0 20px 50px rgba(0,0,0,0.6);
    }}
    .dot {{
      width: 48px; height: 48px; border-radius: 50%; background: {color}22; color: {color};
      display: flex; align-items: center; justify-content: center; font-size: 24px; margin: 0 auto 14px;
      border: 1px solid {color}55;
    }}
    h2 {{ font-size: 1.15rem; margin: 0 0 8px; }}
    p {{ font-size: 0.85rem; color: #9ca3af; margin: 0; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="dot">{'✕' if error else '✓'}</div>
    <h2>{title}</h2>
    <p>{subtitle}</p>
  </div>
  <script>
    (function() {{
      const data = {payload_json};
      try {{
        if (data.type === "VASCO_OAUTH_SUCCESS") {{
          localStorage.setItem("vasco_auth_token", data.token);
          localStorage.setItem("vasco_auth_user", JSON.stringify(data.user));
        }}
      }} catch (e) {{}}
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(data, "*");
        }}
      }} catch (e) {{}}
      setTimeout(function() {{
        window.close();
        if (!window.closed && data.type === "VASCO_OAUTH_SUCCESS") {{
          window.location.href = "/?oauth_token=" + encodeURIComponent(data.token);
        }}
      }}, 500);
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


async def _installed_ollama_models() -> Optional[List[Dict[str, Any]]]:
    """Lists the models actually installed in the local Ollama (None if Ollama isn't running)."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            tags = resp.json().get("models", [])
    except Exception:
        return None
    models = []
    for t in tags:
        name = t.get("name", "")
        caps = t.get("capabilities") or []
        size_gb = (t.get("size") or 0) / 1e9
        models.append({
            "id": name,
            "name": f"{name} (Local)",
            "badge": f"Installé • {size_gb:.1f} Go" + (" • Vision" if "vision" in caps else ""),
            "vision": "vision" in caps or any(v in name for v in ("llava", "vision", "vl")),
            "reasoning": "thinking" in caps,
            "tools": "tools" in caps if caps else True,
        })
    return models


@app.get("/api/models")
async def list_models():
    """Returns all supported AI providers, models, and current API key status.
    The Ollama entry lists the models really installed on this machine when Ollama is running."""
    providers = [dict(p) for p in PROVIDERS_CATALOG]
    installed = await _installed_ollama_models()
    for p in providers:
        if p["id"] == "ollama":
            p["running"] = installed is not None
            if installed:
                p["models"] = installed
    return {
        "providers": providers,
        "keys_status": get_api_keys_status(),
    }


@app.post("/api/auth/register")
def api_register(req: AuthRegisterRequest, request: Request):
    try:
        return register_email_user(req.name, req.email, req.password, **client_info(request))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.post("/api/auth/login")
def api_login(req: AuthLoginRequest, request: Request):
    try:
        return login_email_user(req.email, req.password, **client_info(request))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# =====================================================================
# REAL GOOGLE OAUTH 2.0 ENDPOINTS
# =====================================================================

@app.get("/api/auth/google/login")
async def api_google_login(state: str = ""):
    """Redirects user to official Google OAuth 2.0 account chooser (accounts.google.com)."""
    url = build_google_authorize_url(state=state)
    return RedirectResponse(url=url)


@app.get("/api/auth/google/callback")
async def api_google_callback(code: Optional[str] = None, state: str = "", error: Optional[str] = None):
    """Handles callback from Google OAuth 2.0, exchanges code for real Google profile, and creates session."""
    if error or not code:
        err_msg = error or "Connexion Google annulée."
        if state:
            COMPLETED_OAUTH_STATES[state] = {"status": "error", "error": err_msg}
        return _render_oauth_popup_html(error=err_msg)
    try:
        session = await exchange_google_code_for_session(code=code, state=state)
        return _render_oauth_popup_html(session=session)
    except Exception as e:
        if state:
            COMPLETED_OAUTH_STATES[state] = {"status": "error", "error": str(e)}
        return _render_oauth_popup_html(error=str(e))


# =====================================================================
# REAL GITHUB OAUTH 2.0 ENDPOINTS (Web Flow + Official Device Flow)
# =====================================================================

@app.get("/api/auth/github/login")
async def api_github_login(state: str = ""):
    """Redirects to GitHub OAuth 2.0 authorize page if GITHUB_CLIENT_ID & SECRET are configured."""
    url = build_github_authorize_url(state=state)
    if not url:
        raise HTTPException(status_code=400, detail="Utilisez /api/auth/github/device/start pour le flux OAuth GitHub.")
    return RedirectResponse(url=url)


@app.get("/api/auth/github/callback")
async def api_github_callback(code: Optional[str] = None, state: str = "", error: Optional[str] = None):
    if error or not code:
        err_msg = error or "Connexion GitHub annulée."
        if state:
            COMPLETED_OAUTH_STATES[state] = {"status": "error", "error": err_msg}
        return _render_oauth_popup_html(error=err_msg)
    try:
        session = await exchange_github_code_for_session(code=code, state=state)
        return _render_oauth_popup_html(session=session)
    except Exception as e:
        if state:
            COMPLETED_OAUTH_STATES[state] = {"status": "error", "error": str(e)}
        return _render_oauth_popup_html(error=str(e))


@app.post("/api/auth/github/device/start")
async def api_github_device_start(state: str = ""):
    """Starts a real GitHub OAuth 2.0 authentication (Web redirect if configured, or Official GitHub Device Flow)."""
    web_url = build_github_authorize_url(state=state)
    if web_url:
        return {"mode": "web_redirect", "authorize_url": f"/api/auth/github/login?state={state}"}
    try:
        device_info = await start_github_device_flow()
        return {"mode": "device_flow", **device_info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/github/device/poll")
async def api_github_device_poll(req: GitHubDevicePollRequest):
    """Polls GitHub OAuth 2.0 until user authorizes on github.com/login/device."""
    try:
        return await poll_github_device_flow(req.device_code)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auth/oauth/status")
async def api_oauth_state_status(state: str):
    """Polled by the React frontend to detect when the Google or GitHub OAuth popup finishes."""
    if not state or state not in COMPLETED_OAUTH_STATES:
        return {"status": "pending"}
    return COMPLETED_OAUTH_STATES[state]


@app.post("/api/auth/me")
def api_auth_me(req: AuthTokenRequest):
    user = get_user_by_token(req.token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expirée ou invalide.")
    return {"user": user}


@app.post("/api/auth/logout")
def api_auth_logout(req: AuthTokenRequest):
    logout_token(req.token)
    return {"status": "ok"}


# =====================================================================
# PROFILE SPACE (MySQL)
# =====================================================================

class ProfileUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None
    bio: Optional[str] = None
    phone: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    website: Optional[str] = None
    github: Optional[str] = None
    linkedin: Optional[str] = None
    twitter: Optional[str] = None
    interests: Optional[List[str]] = None
    cover_theme: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    pref_theme: Optional[str] = None
    pref_voice: Optional[str] = None
    pref_auto_tts: Optional[bool] = None
    pref_web_search: Optional[bool] = None
    system_prompt: Optional[str] = None


class AvatarRequest(BaseModel):
    data_url: str = ""


class PasswordChangeRequest(BaseModel):
    current_password: str = ""
    new_password: str


class DeleteAccountRequest(BaseModel):
    password: str = ""
    confirm: str = ""


def _profile_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except db.DatabaseError as de:
        raise HTTPException(status_code=503, detail=str(de))


@app.get("/api/profile")
def api_profile(user: Dict[str, Any] = Depends(require_user)):
    return _profile_call(profiles.get_profile, user["id"])


@app.put("/api/profile")
def api_profile_update(req: ProfileUpdateRequest, request: Request, user: Dict[str, Any] = Depends(require_user)):
    data = req.model_dump(exclude_none=True)
    return _profile_call(profiles.update_profile, user["id"], data, client_info(request)["ip"])


@app.post("/api/profile/avatar")
def api_profile_avatar(req: AvatarRequest, request: Request, user: Dict[str, Any] = Depends(require_user)):
    return _profile_call(profiles.update_avatar, user["id"], req.data_url, client_info(request)["ip"])


@app.post("/api/profile/password")
def api_profile_password(req: PasswordChangeRequest, request: Request,
                         user: Dict[str, Any] = Depends(require_user), token: str = Depends(bearer_token)):
    closed = _profile_call(profiles.change_password, user["id"], req.current_password, req.new_password,
                           token, client_info(request)["ip"])
    return {"status": "ok", "sessions_closed": closed}


@app.get("/api/profile/sessions")
def api_profile_sessions(user: Dict[str, Any] = Depends(require_user), token: str = Depends(bearer_token)):
    return {"sessions": _profile_call(profiles.list_sessions, user["id"], token)}


@app.delete("/api/profile/sessions/{session_id}")
def api_profile_revoke_session(session_id: str, user: Dict[str, Any] = Depends(require_user),
                               token: str = Depends(bearer_token)):
    if not _profile_call(profiles.revoke_session, user["id"], session_id, token):
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"status": "ok"}


@app.delete("/api/profile/sessions")
def api_profile_revoke_others(user: Dict[str, Any] = Depends(require_user), token: str = Depends(bearer_token)):
    return {"status": "ok", "closed": _profile_call(profiles.revoke_other_sessions, user["id"], token)}


@app.get("/api/profile/activity")
def api_profile_activity(user: Dict[str, Any] = Depends(require_user)):
    return {"activity": _profile_call(profiles.list_activity, user["id"])}


@app.post("/api/profile/delete")
def api_profile_delete(req: DeleteAccountRequest, user: Dict[str, Any] = Depends(require_user)):
    _profile_call(profiles.delete_account, user["id"], req.password, req.confirm)
    return {"status": "deleted"}


@app.post("/api/settings/keys")
async def update_keys(payload: ApiKeysUpdateRequest, user: Dict[str, Any] = Depends(require_user)):
    """Updates and persists API keys in .env."""
    if not can_manage_server_keys(user):
        raise HTTPException(
            status_code=403,
            detail="Seul l'administrateur du serveur peut modifier les clés API (variable ADMIN_EMAILS).",
        )
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    new_status = save_api_keys(updates)
    return {
        "status": "ok",
        "keys_status": new_status,
    }


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), user: Dict[str, Any] = Depends(require_user)):
    """
    Processes one or more uploaded files (Photos/Images, PDF, DOCX, CSV, Code, Text)
    and returns extracted content + thumbnails for the prompt bar.
    """
    processed_results = []
    for file in files:
        raw_bytes = await file.read()
        result = await process_uploaded_file(
            filename=file.filename or "fichier",
            content=raw_bytes,
            content_type=file.content_type or "",
        )
        processed_results.append(result)
    ok_count = sum(1 for r in processed_results if r.get("category") != "error")
    if ok_count:
        await run_in_threadpool(profiles.record_usage, user["id"], files=ok_count)
    return {"files": processed_results}


@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest, user: Dict[str, Any] = Depends(require_user)):
    """
    Generates high-definition Neural Voice MP3, automatically detecting language
    (Moroccan Darija/Arabic ar-MA-JamalNeural, French fr-FR-HenriNeural, English en-US-GuyNeural).
    """
    try:
        audio_bytes, _ = await synthesize_speech_mp3(text=req.text, gender=req.voice)
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Texte vide pour la synthèse vocale.")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------- Voice mode -----------------------------
MAX_VOICE_AUDIO_BYTES = 12 * 1024 * 1024


@app.get("/api/voice/config")
async def voice_config():
    """Whether the server can transcribe speech itself (needs a Gemini key)."""
    return {"server_stt": server_stt_available(), "live": live_available()}


@app.websocket("/api/voice/live")
async def voice_live(websocket: WebSocket, token: str = ""):
    """Real-time speech-to-speech conversation (Gemini Live), relayed so the API key stays server-side."""
    user = get_user_by_token(token)
    await websocket.accept()
    if not user:
        await websocket.send_text(json.dumps({"type": "error", "message": "Session expirée : reconnectez-vous."}))
        await websocket.close(code=4401)
        return
    await handle_live(websocket, user)


@app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...), user: Dict[str, Any] = Depends(require_user)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Enregistrement vide.")
    if len(data) > MAX_VOICE_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Enregistrement trop long.")
    try:
        return await transcribe_audio(data, audio.content_type or "audio/wav")
    except VoiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/voice/reply")
async def voice_reply_endpoint(req: VoiceReplyRequest, user: Dict[str, Any] = Depends(require_user)):
    """Spoken-style agent answer + neural speech, in the user's language (Darija-aware)."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="Aucun message fourni.")
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    darija = bool(detect_conversation_language(messages).get("is_darija"))
    result = await voice_reply(provider=req.provider, model=req.model, messages=messages,
                               custom_system_prompt=req.system_prompt, web_search=req.web_search, darija=darija)
    if result["error"] and not result["reply"]:
        raise HTTPException(status_code=502, detail=result["error"])
    await run_in_threadpool(profiles.record_usage, user["id"], voice_turns=1)
    audio_data_url, lang = None, "ar-MA" if darija else "fr-FR"
    if req.speak and result["reply"]:
        try:
            mp3, lang = await synthesize_speech_mp3(text=result["reply"], gender=req.voice)
            if mp3:
                audio_data_url = "data:audio/mpeg;base64," + base64.b64encode(mp3).decode("ascii")
        except Exception:
            audio_data_url = None  # the browser falls back to its own speech synthesis
    return {
        "reply": result["reply"],
        "display": result["display"],
        "sources": result["sources"],
        "audio_data_url": audio_data_url,
        "lang": lang,
    }


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, user: Dict[str, Any] = Depends(require_user)):
    """
    Main chat endpoint for Vasco.
    Supports multi-turn history, file/image attachments, live web/news/wiki search,
    Moroccan Darija mode, and DeepThink reasoning.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="Aucun message fourni.")

    last_user_msg = req.messages[-1].content.strip()
    sources: List[Dict[str, Any]] = []
    web_context = ""

    # 1. Perform fast multi-source live search when enabled and not a casual greeting
    if not req.image_gen and should_run_live_search(last_user_msg, req.web_search):
        if req.voice_mode:
            voice_news_triggers = ("aujourd", "lyoum", "اليوم", "أخبار", "اخبار", "akhbar", "match", "score", "météo", "meteo", "2026", "دابا")
            if any(t in last_user_msg.lower() for t in voice_news_triggers):
                sources = await perform_web_search(last_user_msg, max_results=4)
                web_context = format_search_results_for_prompt(sources)
        elif req.web_search:
            sources = await perform_web_search(last_user_msg, max_results=5)
            web_context = format_search_results_for_prompt(sources)

    # 2. Generate response via selected AI provider
    try:
        msg_dicts = [{"role": m.role, "content": m.content} for m in req.messages]
        result = await generate_vasco_response(
            provider=req.provider,
            model=req.model,
            messages=msg_dicts,
            attachments=req.attachments,
            deep_think=req.deep_think,
            web_search_context=web_context,
            custom_system_prompt=req.system_prompt,
            image_generation_mode=req.image_gen,
            voice_mode=req.voice_mode,
        )
        await run_in_threadpool(
            profiles.record_usage, user["id"], messages=1, images=1 if req.image_gen else 0
        )

        audio_data_url = None
        detected_lang = "fr-FR"
        if req.voice_mode and result.get("reply"):
            try:
                mp3_bytes, detected_lang = await synthesize_speech_mp3(text=result["reply"])
                if mp3_bytes:
                    b64_mp3 = base64.b64encode(mp3_bytes).decode("utf-8")
                    audio_data_url = f"data:audio/mpeg;base64,{b64_mp3}"
            except Exception:
                audio_data_url = None

        return {
            "reply": result["reply"],
            "reasoning": result.get("reasoning"),
            "sources": sources,
            "provider_used": result.get("provider_used", req.provider),
            "model_used": result.get("model_used", req.model),
            "audio_data_url": audio_data_url,
            "detected_lang": detected_lang,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agent/tools")
async def agent_tools():
    """Lists the agent's tools and whether each one is enabled on this server."""
    return {"tools": tools_catalog(), "code_execution": code_execution_enabled()}


@app.post("/api/agent/stream")
async def agent_stream(req: AgentRequest, user: Dict[str, Any] = Depends(require_user)):
    """
    Runs the autonomous agent and streams every step as Server-Sent Events:
    reasoning, tool calls and results, answer tokens, then a final `done` event.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="Aucun message fourni.")

    async def event_source():
        tool_calls = images = 0
        async for event in run_agent(
            provider=req.provider,
            model=req.model,
            messages=[m.model_dump() for m in req.messages],
            attachments=req.attachments,
            deep_think=req.deep_think,
            web_search=req.web_search,
            image_mode=req.image_gen,
            custom_system_prompt=req.system_prompt,
        ):
            if event.get("type") == "tool_end" and event.get("ok") and event.get("image_url"):
                images += 1
            if event.get("type") == "done":
                tool_calls = int(event.get("tool_calls") or 0)
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        await run_in_threadpool(
            profiles.record_usage, user["id"], messages=1, tool_calls=tool_calls, images=images
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# Mount static files and root index.html
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Vasco AI Backend is running."}
