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
import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional

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


def _setup(model: str, voice: str, system: str, web: bool) -> Dict[str, Any]:
    names = [n for n in LIVE_TOOLS if web or n not in ("web_search", "fetch_url")]
    return {"setup": {
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


async def _connect(voice: str, system: str, web: bool):
    """Open a Live session on the first model that accepts it (quota / availability fallbacks)."""
    key = _key()
    last = "clé Gemini manquante"
    if not key:
        raise RuntimeError(last)
    for model in LIVE_MODELS:
        try:
            ws = await websockets.connect(f"{LIVE_URL}?key={key}", max_size=None, open_timeout=10)
            await ws.send(json.dumps(_setup(model, voice, system, web)))
            first = json.loads(await asyncio.wait_for(ws.recv(), 12))
            if "setupComplete" in first:
                return ws, model
            last = str(first)[:200]
            await ws.close()
        except Exception as e:  # quota (1011), timeout, unknown model...
            last = str(e)[:200]
    raise RuntimeError(f"Gemini Live indisponible : {last}")


async def handle_live(client: WebSocket, user: Dict[str, Any]) -> None:
    try:
        start = json.loads(await asyncio.wait_for(client.receive_text(), 15))
    except Exception:
        await client.close(code=4400)
        return
    voice = start.get("voice", "male")
    web = bool(start.get("web_search", True))
    system = _system_prompt(str(start.get("system_prompt") or ""), start.get("history") or [])
    try:
        upstream, model = await _connect(voice, system, web)
    except RuntimeError as e:
        await client.send_text(json.dumps({"type": "error", "message": str(e)}))
        await client.close()
        return
    await client.send_text(json.dumps({"type": "ready", "model": model, "voice": VOICES.get(voice, "Orus")}))
    allowed = [n for n in LIVE_TOOLS if web or n not in ("web_search", "fetch_url")]

    async def browser_to_gemini():
        while True:
            msg = await client.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                await upstream.send(json.dumps({"realtimeInput": {"audio": {
                    "data": base64.b64encode(msg["bytes"]).decode("ascii"), "mimeType": "audio/pcm;rate=16000"}}}))
            elif msg.get("text"):
                data = json.loads(msg["text"])
                if data.get("type") == "text" and data.get("text"):
                    await upstream.send(json.dumps({"clientContent": {
                        "turns": [{"role": "user", "parts": [{"text": data["text"]}]}], "turnComplete": True}}))
                elif data.get("type") == "stop":
                    return

    async def run_tools(calls: List[Dict[str, Any]]):
        responses = []
        for fc in calls:
            name = fc.get("name", "")
            spec = TOOLS.get(name)
            await client.send_text(json.dumps({"type": "tool", "name": name, "label": spec.label if spec else name,
                                               "icon": spec.icon if spec else "🛠️", "args": fc.get("args") or {}}))
            out = await execute_tool(name, fc.get("args") or {}, allowed, timeout_s=20.0)
            responses.append({"id": fc.get("id"), "name": name, "response": {"result": out.content[:6000]}})
        await upstream.send(json.dumps({"toolResponse": {"functionResponses": responses}}))

    async def gemini_to_browser():
        async for raw in upstream:
            msg = json.loads(raw)
            if "toolCall" in msg:
                asyncio.create_task(run_tools(msg["toolCall"].get("functionCalls") or []))
                continue
            if "goAway" in msg:
                await client.send_text(json.dumps({"type": "reconnect"}))
                continue
            sc: Optional[Dict[str, Any]] = msg.get("serverContent")
            if not sc:
                continue
            for part in (sc.get("modelTurn") or {}).get("parts") or []:
                data = (part.get("inlineData") or {}).get("data")
                if data:
                    await client.send_bytes(base64.b64decode(data))
            if (sc.get("inputTranscription") or {}).get("text"):
                await client.send_text(json.dumps({"type": "user_text", "text": sc["inputTranscription"]["text"]}))
            if (sc.get("outputTranscription") or {}).get("text"):
                await client.send_text(json.dumps({"type": "vasco_text", "text": sc["outputTranscription"]["text"]}))
            if sc.get("interrupted"):
                await client.send_text(json.dumps({"type": "interrupted"}))
            if sc.get("turnComplete"):
                await client.send_text(json.dumps({"type": "turn_complete"}))

    tasks = [asyncio.create_task(browser_to_gemini()), asyncio.create_task(gemini_to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, websockets.ConnectionClosed)):
                try:
                    await client.send_text(json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}))
                except Exception:
                    pass
    finally:
        for t in tasks:
            t.cancel()
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass
