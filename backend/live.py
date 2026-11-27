"""
Real-time voice conversation through the Gemini Live API (speech in, speech out).

The browser never sees the API key: it talks to /api/voice/live (WebSocket) and this module
relays to Gemini Live, runs Vasco's tools when the model asks for them, and forwards:

  browser -> server   binary frames : PCM 16-bit mono 16 kHz microphone audio
                      text frames   : {"type": "start", ...} first, then {"type": "text", "text": ...}
  server  -> browser  binary frames : PCM 16-bit mono 24 kHz speech from Vasco
                      text frames   : ready | user_text | vasco_text | tool | interrupted | turn_complete
                                      | reconnect | error
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect

from backend.agent.llm import _gemini_schema
from backend.agent.tools import TOOLS, execute_tool
from backend.ai_providers import ENV_PATH

LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
# Fastest first (~0.5 s to the first word in tests); the others are fallbacks when a quota is hit.
LIVE_MODELS = ("gemini-3.8-live", "gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-latest")
VOICES = {"male": "Orus", "female": "Kore"}
LIVE_TOOLS = ("web_search", "fetch_url", "get_datetime", "calculator")

LIVE_SYSTEM = """نتا فاسكو، مساعد ذكي ومغربي، كتهضر مع المستخدم فمكالمة صوتية مباشرة.

كيفاش كتهضر:
- إلا هضر معاك بالدارجة، جاوب بالدارجة المغربية ديال الزنقة، بلكنة مغربية طبيعية، بحال شي صاحب مغربي ولد البلاد (كازا/الرباط). ماشي بالفصحى ومَاشي بالمشرقية.
- قول « شنو » ماشي « ماذا »، « بغيت » ماشي « أريد »، « دابا » ماشي « الآن »، « كاين » ماشي « يوجد »، « مزيان » ماشي « جيد »، « بزاف » ماشي « كثيراً »، « غادي » ماشي « سوف »، « فين » ماشي « أين »، « علاش » ماشي « لماذا »، « واخا » ماشي « حسناً ».
- الكلمات اللي كيقولهوم المغاربة بالفرنسية (l'ordinateur، le wifi، la voiture، le rendez-vous...) قولهم بالفرنسية عادي.
- إلا هضر معاك بالفرنسية جاوب بالفرنسية، بالإنجليزية جاوب بالإنجليزية، بالفصحى جاوب بالفصحى. تبّع اللغة ديالو ديما.
- جاوب قصير وطبيعي بحال فالتيليفون: جملة ولا جوج، وكتر غير إلا طلب منك يشرح. ما تقراش ليستات ولا رموز.
- كون دافي، ضحوك شوية، ومباشر. إلا ما فهمتيش مزيان، سولو بلطف.
- تا تعرف الجنس ديالو، هضر معاه بالمذكر (« خويا »، « تقدر »).

المعلومات:
- اليوم هو {today}.
- للأخبار، الطقس، الأثمنة، الماتشات ولا أي حاجة جديدة: استعمل الأداة web_search (kind='news' للأخبار)، ومن بعد لخّص النتيجة فجملة ولا جوج بالدارجة.
- للحسابات استعمل calculator. ما تخترعش أرقام ولا أخبار.
"""


def _key() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def live_available() -> bool:
    return bool(_key())


def _system_prompt(custom: str, history: List[Dict[str, Any]]) -> str:
    today = dt.datetime.now().strftime("%d/%m/%Y")
    prompt = LIVE_SYSTEM.replace("{today}", today)
    if custom.strip():
        prompt += f"\nتعليمات من المستخدم:\n{custom.strip()}\n"
    recent = [m for m in history if m.get("content")][-10:]
    if recent:
        lines = []
        for m in recent:
            who = "المستخدم" if m.get("role") == "user" else "فاسكو"
            lines.append(f"{who}: {str(m['content'])[:300]}")
        prompt += "\nآخر ما تقال فهاد المحادثة (باش تكمّل معاه):\n" + "\n".join(lines) + "\n"
    return prompt


def _setup(model: str, voice: str, system: str, web: bool, handle: Optional[str] = None) -> Dict[str, Any]:
    names = [n for n in LIVE_TOOLS if web or n not in ("web_search", "fetch_url")]
    return {"setup": {
        # Resumable session + sliding context window: the conversation survives Gemini's connection
        # limits (goAway) and our own reconnects, with its memory intact.
        "sessionResumption": {"handle": handle} if handle else {},
        "contextWindowCompression": {"slidingWindow": {}},
        "model": f"models/{model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICES.get(voice, "Orus")}}},
        },
        "systemInstruction": {"parts": [{"text": system}]},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {"automaticActivityDetection": {"silenceDurationMs": 550, "prefixPaddingMs": 200}},
        "tools": [{"functionDeclarations": [
            {"name": n, "description": TOOLS[n].description, "parameters": _gemini_schema(TOOLS[n].parameters)}
            for n in names
        ]}],
    }}


async def _connect(voice: str, system: str, web: bool, handle: Optional[str] = None,
                   models: Optional[tuple] = None):
    """Open a Live session on the first model that accepts it (quota / availability fallbacks)."""
    key = _key()
    last = "clé Gemini manquante"
    if not key:
        raise RuntimeError(last)
    for model in models or LIVE_MODELS:
        try:
            ws = await websockets.connect(f"{LIVE_URL}?key={key}", max_size=None, open_timeout=10,
                                          ping_interval=20, ping_timeout=20)
            await ws.send(json.dumps(_setup(model, voice, system, web, handle)))
            first = json.loads(await asyncio.wait_for(ws.recv(), 12))
            if "setupComplete" in first:
                return ws, model
            last = str(first)[:200]
            await ws.close()
        except Exception as e:  # quota (1011), timeout, unknown model...
            last = str(e)[:200]
    raise RuntimeError(f"Gemini Live indisponible : {last}")


# ---------------------------------------------------------------------------
# Direct mode: the browser talks to Gemini Live itself with a short-lived, single-use token whose
# setup (model, Vasco's persona, voice, tools) is locked server-side. The voice then streams straight
# from Google to the phone — much faster than through our relay — and the API key never leaves here.
# ---------------------------------------------------------------------------
TOKEN_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
CONSTRAINED_URL = ("wss://generativelanguage.googleapis.com/ws/"
                   "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained")


async def create_live_token(*, voice: str, custom: str, history: List[Dict[str, Any]], web: bool,
                            resume: Optional[str]) -> Dict[str, Any]:
    key = _key()
    if not key:
        raise RuntimeError("clé Gemini manquante")
    model = LIVE_MODELS[0]
    setup = _setup(model, voice, _system_prompt(custom, history), web, resume or None)["setup"]
    now = dt.datetime.now(dt.timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    body = {"uses": 1, "expireTime": iso(now + dt.timedelta(minutes=30)),
            "newSessionExpireTime": iso(now + dt.timedelta(minutes=2)), "bidiGenerateContentSetup": setup}
    last = ""
    async with httpx.AsyncClient(timeout=15) as http:
        for _ in range(3):  # Google sometimes drops the first connection
            try:
                r = await http.post(TOKEN_URL, headers={"x-goog-api-key": key}, json=body)
            except httpx.HTTPError as e:
                last = type(e).__name__
                await asyncio.sleep(0.3)
                continue
            if r.status_code == 200 and r.json().get("name"):
                return {"token": r.json()["name"], "url": CONSTRAINED_URL, "model": model,
                        "tools": {n: {"label": TOOLS[n].label, "icon": TOOLS[n].icon} for n in LIVE_TOOLS}}
            last = f"HTTP {r.status_code} {r.text[:150]}"
            if r.status_code < 500:
                break
    raise RuntimeError(f"jeton Gemini Live indisponible ({last})")


async def run_live_tool(name: str, args: Dict[str, Any], web: bool) -> str:
    allowed = [n for n in LIVE_TOOLS if web or n not in ("web_search", "fetch_url")]
    out = await execute_tool(name, args or {}, allowed, timeout_s=12.0)
    return out.content[:6000]


async def handle_live(client: WebSocket, user: Dict[str, Any]) -> None:
    try:
        start = json.loads(await asyncio.wait_for(client.receive_text(), 15))
    except Exception:
        await client.close(code=4400)
        return
    voice = start.get("voice", "male")
    web = bool(start.get("web_search", True))
    system = _system_prompt(str(start.get("system_prompt") or ""), start.get("history") or [])
    resume = str(start.get("resume") or "") or None
    try:
        upstream, model = await _connect(voice, system, web, resume)
    except RuntimeError as e:
        if not resume:
            await client.send_text(json.dumps({"type": "error", "message": str(e)}))
            await client.close()
            return
        try:  # expired handle: start a fresh session (the history is in the system prompt anyway)
            upstream, model = await _connect(voice, system, web)
        except RuntimeError as e2:
            await client.send_text(json.dumps({"type": "error", "message": str(e2)}))
            await client.close()
            return
    await client.send_text(json.dumps({"type": "ready", "model": model, "voice": VOICES.get(voice, "Orus")}))
    allowed = [n for n in LIVE_TOOLS if web or n not in ("web_search", "fetch_url")]
    started = time.monotonic()
    # Shared between the tasks: the current Gemini connection can be swapped under them.
    st: Dict[str, Any] = {"up": upstream, "model": model, "handle": resume, "client_gone": False, "swaps": 0}

    async def send_up(payload: Dict[str, Any]) -> None:
        try:
            await st["up"].send(json.dumps(payload))
        except websockets.ConnectionClosed:
            pass  # being reconnected: this chunk is dropped, the next ones go to the new session

    async def browser_to_gemini():
        while True:
            msg = await client.receive()
            if msg.get("type") == "websocket.disconnect":
                st["client_gone"] = True
                return
            if msg.get("bytes"):
                await send_up({"realtimeInput": {"audio": {
                    "data": base64.b64encode(msg["bytes"]).decode("ascii"), "mimeType": "audio/pcm;rate=16000"}}})
            elif msg.get("text"):
                data = json.loads(msg["text"])
                if data.get("type") == "text" and data.get("text"):
                    await send_up({"clientContent": {
                        "turns": [{"role": "user", "parts": [{"text": data["text"]}]}], "turnComplete": True}})
                elif data.get("type") == "ping":
                    await client.send_text(json.dumps({"type": "pong"}))
                elif data.get("type") == "stop":
                    st["client_gone"] = True
                    return

    async def run_tools(calls: List[Dict[str, Any]]):
        responses = []
        for fc in calls:
            name = fc.get("name", "")
            spec = TOOLS.get(name)
            await client.send_text(json.dumps({"type": "tool", "name": name, "label": spec.label if spec else name,
                                               "icon": spec.icon if spec else "🛠️", "args": fc.get("args") or {}}))
            out = await execute_tool(name, fc.get("args") or {}, allowed, timeout_s=12.0)
            responses.append({"id": fc.get("id"), "name": name, "response": {"result": out.content[:6000]}})
        await send_up({"toolResponse": {"functionResponses": responses}})

    async def pump(up) -> str:
        """Forward one Gemini connection to the browser. Returns why it ended."""
        turn = {"first": 0.0, "last": 0.0, "audio": 0, "msgs": 0, "send_s": 0.0}
        try:
            async for raw in up:
                msg = json.loads(raw)
                turn["msgs"] += 1
                if "sessionResumptionUpdate" in msg:
                    upd = msg["sessionResumptionUpdate"]
                    if upd.get("resumable") and upd.get("newHandle"):
                        st["handle"] = upd["newHandle"]
                        await client.send_text(json.dumps({"type": "session", "handle": st["handle"]}))
                    continue
                if "toolCall" in msg:
                    asyncio.create_task(run_tools(msg["toolCall"].get("functionCalls") or []))
                    continue
                if "goAway" in msg:
                    return "goAway"
                sc: Optional[Dict[str, Any]] = msg.get("serverContent")
                if not sc:
                    continue
                for part in (sc.get("modelTurn") or {}).get("parts") or []:
                    data = (part.get("inlineData") or {}).get("data")
                    if data:
                        now = time.monotonic()
                        turn["first"] = turn["first"] or now
                        turn["last"] = now
                        pcm = base64.b64decode(data)
                        turn["audio"] += len(pcm)
                        t_send = time.monotonic()
                        await client.send_bytes(pcm)
                        turn["send_s"] += time.monotonic() - t_send
                if (sc.get("inputTranscription") or {}).get("text"):
                    await client.send_text(json.dumps({"type": "user_text", "text": sc["inputTranscription"]["text"]}))
                if (sc.get("outputTranscription") or {}).get("text"):
                    await client.send_text(json.dumps({"type": "vasco_text", "text": sc["outputTranscription"]["text"]}))
                if sc.get("interrupted"):
                    await client.send_text(json.dumps({"type": "interrupted"}))
                if sc.get("turnComplete"):
                    await client.send_text(json.dumps({"type": "turn_complete"}))
                    if turn["audio"]:
                        secs = turn["audio"] / 48000
                        span = turn["last"] - turn["first"]
                        print(f"[Vasco] live turn: {secs:.1f}s audio from Gemini in {span:.1f}s "
                              f"(x{secs / max(span, 0.01):.2f} real time), {turn['msgs']} msgs, "
                              f"browser send {turn['send_s']:.2f}s, model={st['model']}")
                    turn = {"first": 0.0, "last": 0.0, "audio": 0, "msgs": 0, "send_s": 0.0}
            return f"closed {up.close_code} {up.close_reason!r}"
        except websockets.ConnectionClosed as e:
            return f"closed {e.code} {e.reason!r}"

    async def gemini_to_browser():
        while True:
            why = await pump(st["up"])
            if st["client_gone"]:
                return
            print(f"[Vasco] live: Gemini session ended ({why[:200]}) after {time.monotonic() - started:.0f}s "
                  f"model={st['model']} — reconnecting (resumable={bool(st['handle'])})")
            old = st["up"]
            new = None
            for attempt in range(4):
                try:
                    new, mdl = await _connect(voice, system, web, st["handle"])
                    break
                except RuntimeError as e:
                    print(f"[Vasco] live: reconnect #{attempt + 1} failed: {str(e)[:200]}")
                    if attempt == 1:
                        st["handle"] = None  # the handle may be what is refused: start fresh
                    await asyncio.sleep(0.4 * (attempt + 1))
            if new is None:
                await client.send_text(json.dumps({"type": "error",
                                                   "message": "Connexion à Gemini perdue. Touchez la sphère."}))
                return
            st["up"], st["model"] = new, mdl
            st["swaps"] += 1
            with contextlib.suppress(Exception):
                await old.close()

    tasks = [asyncio.create_task(browser_to_gemini()), asyncio.create_task(gemini_to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, websockets.ConnectionClosed)):
                print(f"[Vasco] live: relay error {type(exc).__name__}: {str(exc)[:300]}")
                try:
                    await client.send_text(json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}))
                except Exception:
                    pass
    finally:
        print(f"[Vasco] live: session closed after {time.monotonic() - started:.0f}s, "
              f"{st['swaps']} transparent reconnect(s), client_gone={st['client_gone']}")
        for t in tasks:
            t.cancel()
        try:
            await st["up"].close()
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass
