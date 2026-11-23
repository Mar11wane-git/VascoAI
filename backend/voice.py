"""
Voice mode: speech-to-text with Gemini (understands Moroccan Darija far better than the
browser's recogniser), a spoken-style agent reply, and Darija-aware text-to-speech.

    transcribe_audio(bytes, mime)  -> {"text": str, "lang": "darija"|"ar"|"fr"|"en"|""}
    voice_reply(...)               -> {"reply": str, "sources": [...], "error": str|None}
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

from backend.agent.runner import run_agent
from backend.ai_providers import ENV_PATH

# Fastest first; an overloaded model can take >10 s just to answer "503", hence the per-try timeout.
STT_MODELS = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash")
STT_TRY_TIMEOUT = 12.0

STT_PROMPT = """Tu es un transcripteur audio expert de la darija marocaine, de l'arabe, du français et de l'anglais.
Transcris EXACTEMENT ce que dit la personne dans cet enregistrement, mot pour mot, sans répondre à la question.

Règles :
- Darija marocaine : écris-la en lettres arabes, telle qu'un Marocain l'écrit (ex. « واش كاين شي جديد اليوم؟ », « بغيت نعرف شحال كيسوا »). Ne la convertis JAMAIS en arabe standard (fusha).
- Mots français ou anglais au milieu de la darija (code-switching) : garde-les en lettres latines (ex. « بغيت نخدم ف l'informatique »).
- Français ou anglais : écris dans cette langue, avec la ponctuation normale.
- Si l'enregistrement ne contient pas de parole compréhensible (silence, bruit, souffle), renvoie un texte vide.

Réponds uniquement en JSON : {"text": "<transcription>", "lang": "darija" | "ar" | "fr" | "en" | ""}"""

VOICE_RULES = """
## MODE VOCAL (conversation orale en direct)
Ta réponse sera LUE À VOIX HAUTE par une voix de synthèse. Écris exactement ce qui doit être dit :
- Réponse courte et naturelle, comme au téléphone : 1 à 3 phrases (jusqu'à 5 si on te demande une explication).
- AUCUN Markdown, aucune liste, aucun titre, aucun emoji, aucun lien, aucune URL, aucun tableau, aucun code.
- Écris les nombres, heures et prix de façon à être bien prononcés (ex. « 25 درهم », « 3 ديال العشية »).
- Si une information récente est nécessaire (actualité, météo, prix, scores), utilise tes outils, puis résume oralement en une ou deux phrases.
"""

VOICE_RULES_DARIJA = """
- L'utilisateur parle en DARIJA MAROCAINE : réponds en vraie darija marocaine, écrite en lettres arabes, comme un ami marocain qui parle (« واخا »، « دابا »، « بزاف »، « مزيان »، « ديال »، « كيفاش »، « شنو »، « هادشي »، « غادي »).
- N'utilise PAS l'arabe standard (fusha) : dis « شنو » et pas « ماذا »، « بغيت » et pas « أريد »، « دابا » et pas « الآن »، « كاين » et pas « يوجد »، « مزيان » et pas « جيد ».
- Les mots techniques peuvent rester en français en lettres latines si c'est ce que dirait un Marocain (ex. « l'ordinateur », « le wifi »).
- Ne commence pas par « وعليكم السلام » si l'utilisateur ne t'a pas salué ; tant que son genre est inconnu, parle-lui au masculin générique.
"""


def _gemini_key() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def server_stt_available() -> bool:
    return bool(_gemini_key())


class VoiceError(RuntimeError):
    pass


async def transcribe_audio(audio: bytes, mime_type: str) -> Dict[str, str]:
    key = _gemini_key()
    if not key:
        raise VoiceError("Aucune clé Gemini : la transcription serveur est indisponible.")
    mime = (mime_type or "audio/wav").split(";")[0].strip() or "audio/wav"
    payload = {
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode("ascii")}},
            {"text": STT_PROMPT},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    last_error = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(STT_TRY_TIMEOUT, connect=8.0)) as client:
        for model in STT_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                resp = await client.post(url, json=payload, headers={"x-goog-api-key": key})
                if resp.status_code == 400 and "thinking" in resp.text.lower():
                    retry = json.loads(json.dumps(payload))
                    retry["generationConfig"].pop("thinkingConfig", None)
                    resp = await client.post(url, json=retry, headers={"x-goog-api-key": key})
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}"
                continue
            if resp.status_code != 200:
                try:
                    last_error = resp.json().get("error", {}).get("message", resp.text)[:200]
                except Exception:
                    last_error = resp.text[:200]
                continue
            parts = ((resp.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            raw = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r"\{[\s\S]*\}", raw)
                data = json.loads(m.group(0)) if m else {"text": raw, "lang": ""}
            text = str(data.get("text") or "").strip()
            return {"text": text, "lang": str(data.get("lang") or "")}
    raise VoiceError(f"Transcription impossible ({last_error or 'erreur inconnue'}).")


def _strip_for_speech(text: str) -> str:
    """Remove what must never be read aloud, in case the model still added some."""
    t = re.sub(r"```[\s\S]*?```", " ", text)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"[*_`#>|~]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


async def voice_reply(*, provider: str, model: str, messages: List[Dict[str, Any]], custom_system_prompt: str,
                      web_search: bool, darija: bool) -> Dict[str, Any]:
    rules = VOICE_RULES + (VOICE_RULES_DARIJA if darija else "")
    custom = f"{custom_system_prompt.strip()}\n{rules}" if custom_system_prompt.strip() else rules
    reply, sources, error = "", [], None
    async for ev in run_agent(provider=provider, model=model, messages=messages, attachments=[],
                              deep_think=False, web_search=web_search, image_mode=False,
                              custom_system_prompt=custom, max_tool_rounds=2, fast=True):
        if ev.get("type") == "done":
            reply = ev.get("content") or ""
            sources = ev.get("sources") or []
        elif ev.get("type") == "error":
            error = ev.get("message") or "Erreur inconnue"
    return {"reply": _strip_for_speech(reply), "display": reply, "sources": sources, "error": error}
