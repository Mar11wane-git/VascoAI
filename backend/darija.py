import asyncio
import os
import re
from typing import Dict, Tuple, List, Any
import edge_tts
import httpx

# Common Moroccan Darija words in Latin/Arabizi script (including phonetic transcriptions from voice)
DARIJA_LATIN_KEYWORDS = {
    "salam", "slm", "labas", "cv", "kifach", "kifash", "3lach", "3lash", "alach",
    "wach", "wash", "ouach", "chno", "chnou", "ashno", "achno", "chkoun", "shkoun",
    "daba", "bzzaf", "bezzaf", "bzaf", "meziane", "mzyan", "mezyan", "mezyane",
    "khoya", "khouya", "khti", "dial", "dyal", "diali", "dyali", "dialk", "dyalk",
    "wakha", "okha", "ouakha", "machi", "bghit", "bghina", "lmaghrib", "maghrib",
    "chokran", "shokran", "barak", "llah", "inchallah", "inchaallah", "hadchi",
    "fin", "fuqach", "foqach", "ch7al", "sh7al", "chhal", "7it", "hit", "9ra",
    "5dam", "khdam", "khedma", "flous", "flouss", "drari", "bnt", "weld", "dar",
    "mdina", "zwin", "zwina", "khayb", "s3ib", "sahel", "3afak", "afak", "3awn",
    "3awni", "fhem", "fhemtini", "gol", "goli", "goliya", "9oli", "kayn", "kayna",
    "makaynch", "hdar", "hder", "nhdar", "m3aya", "m3ak", "maak", "maaya", "bhal",
    "haka", "fach", "nbrek", "ndwi", "itl3", "yb9a", "ytjawb", "dyalek", "dialek"
}

# Common Moroccan Darija words in Arabic script
DARIJA_ARABIC_KEYWORDS = {
    "ديال", "دابا", "بزاف", "مزيان", "خويا", "ختي", "واخا", "بغيت",
    "هادشي", "ماشي", "شحال", "فوقاش", "فين", "علاش", "كيفاش", "واش",
    "شنو", "أشنو", "شكون", "المغرب", "كاين", "كاينة", "ماكاينش", "عافاك",
    "عاون", "عاوني", "قوليا", "زوين", "زوينة", "صعيب", "ساهل", "لاباس",
    "معايا", "معاك", "بحال", "هكا", "فاش", "ندوي", "نهضر", "كيهضر"
}

ENGLISH_COMMON_WORDS = {
    "the", "what", "how", "why", "when", "where", "who", "which", "please",
    "can", "could", "would", "should", "tell", "explain", "about", "with",
    "from", "have", "has", "this", "that", "these", "those", "hello", "thanks"
}

ARABIZI_TO_ARABIC_MAP: Dict[str, str] = {
    "salam": "سلام",
    "slm": "سلام",
    "alikoum": "عليكم",
    "3likoum": "عليكم",
    "labas": "لاباس",
    "3lik": "عليك",
    "khouya": "خويا",
    "khoya": "خويا",
    "khti": "ختي",
    "kifach": "كيفاش",
    "kifash": "كيفاش",
    "3lach": "علاش",
    "alach": "علاش",
    "wach": "واش",
    "chno": "شنو",
    "chnou": "شنو",
    "chkoun": "شكون",
    "daba": "دابا",
    "bzzaf": "بزاف",
    "bezzaf": "بزاف",
    "bzaf": "بزاف",
    "mzyan": "مزيان",
    "meziane": "مزيان",
    "mezyan": "مزيان",
    "dial": "ديال",
    "dyal": "ديال",
    "dyali": "ديالي",
    "dyalk": "ديالك",
    "wakha": "واخا",
    "machi": "ماشي",
    "bghit": "بغيت",
    "lmaghrib": "المغرب",
    "chokran": "شكرا",
    "hadchi": "هادشي",
    "fin": "فين",
    "ch7al": "شحال",
    "chhal": "شحال",
    "7it": "حيت",
    "zwin": "زوين",
    "zwina": "زوينة",
    "3afak": "عافاك",
    "kayn": "كاين",
    "makaynch": "ماكاينش",
    "inchallah": "إن شاء الله",
    "vasco": "فاسكو",
}

AUTO_MULTILINGUAL_AND_DARIJA_PROMPT = """
🌍 DÉTECTION AUTOMATIQUE DE LA LANGUE (DARIJA MAROCAINE / ARABE / FRANÇAIS / ANGLAIS) :
- Détecte TOUJOURS automatiquement la langue utilisée par l'utilisateur dans son dernier message et réponds dans cette MÊME langue sans jamais demander quelle langue choisir :
  1. Si l'utilisateur parle ou écrit en **Darija marocaine** (que ce soit en lettres arabes "سلام خويا كيفاش..." ou en lettres latines/Arabizi "salam khouya kifach / bghit / chno..."), réponds naturellement en **Darija marocaine authentique** (الدارجة المغربية).
  2. Si l'utilisateur parle ou écrit en **Français**, réponds en **Français**.
  3. Si l'utilisateur parle ou écrit en **Arabe standard**, réponds en **Arabe**.
  4. Si l'utilisateur parle ou écrit en **Anglais**, réponds en **Anglais**.
"""

DARIJA_DETECTED_PROMPT = """
🇲🇦 L'utilisateur s'exprime en **Darija marocaine (الدارجة المغربية)** :
- Réponds-lui directement et naturellement en **Darija marocaine** (comme un Marocain cultivé et chaleureux : "خويا", "مرحبا", "دابا", "مزيان", "بزاف", "هادشي", "ديال", "واخا", "كيفاش", "علاش").
- C'est de la darija, PAS de l'arabe standard (fusha) : « شنو » et pas « ماذا »، « بغيت » et pas « أريد »، « دابا » et pas « الآن »، « كاين » et pas « يوجد »، « مزيان » et pas « جيد »، « بزاف » et pas « كثيرا »، « غادي » et pas « سوف »، « ماشي » et pas « ليس »، « فين » et pas « أين ».
- Écris en caractères arabes (الدارجة بالحروف العربية), même si l'utilisateur écrit en Arabizi (« wach », « 3afak ») : tu le comprends parfaitement, et ta réponse sera lue correctement par la voix marocaine.
- Les termes techniques peuvent rester en français si c'est ce que dirait un Marocain (ex. « l'ordinateur », « le code »).
- Tant que l'utilisateur n'a pas indiqué son genre, adresse-toi à lui au masculin générique (« تقدر »، « بغيتي »، « خويا »), jamais au féminin par supposition.
"""


def detect_darija(text: str) -> Dict[str, Any]:
    """
    Automatically detects whether the input text is in Moroccan Darija (Arabic or Latin/Arabizi),
    Standard Arabic, English, or French.
    """
    if not text:
        return {"is_darija": False, "is_arabic_script": False, "lang": "fr-FR"}

    has_arabic_chars = bool(re.search(r"[\u0600-\u06FF]", text))
    words = set(re.findall(r"[\w\u0600-\u06FF]+", text.lower()))

    if has_arabic_chars:
        is_dar_ar = any(kw in text for kw in DARIJA_ARABIC_KEYWORDS)
        return {
            "is_darija": True,  # Use Moroccan Arabic / Darija voice & style for Arabic script too
            "is_darija_strict": is_dar_ar,
            "is_arabic_script": True,
            "lang": "ar-MA",
        }

    # Check Latin Darija / Arabizi (words with 3, 7, 9, 5 inside letters or Darija vocabulary)
    darija_word_matches = len(words.intersection(DARIJA_LATIN_KEYWORDS))
    has_arabizi_numbers = bool(re.search(r"[a-z]+[37952][a-z]*|[37952][a-z]{2,}", text.lower()))

    is_dar_lat = (
        (darija_word_matches >= 2)
        or (darija_word_matches >= 1 and has_arabizi_numbers)
        or (darija_word_matches == 1 and len(words) <= 4)
    )
    if is_dar_lat:
        return {
            "is_darija": True,
            "is_darija_strict": True,
            "is_arabic_script": False,
            "lang": "ar-MA",
        }

    # Check English vs French
    en_matches = len(words.intersection(ENGLISH_COMMON_WORDS))
    if en_matches >= 2:
        return {
            "is_darija": False,
            "is_arabic_script": False,
            "lang": "en-US",
        }

    return {
        "is_darija": False,
        "is_arabic_script": False,
        "lang": "fr-FR",
    }


def detect_conversation_language(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyzes the latest user message (and recent conversation context for very short replies like 'oui'/'واخا')
    to automatically determine the conversation language without any manual selector.
    """
    if not messages:
        return {"is_darija": False, "is_arabic_script": False, "lang": "fr-FR"}

    last_msg = messages[-1].get("content", "").strip()
    det = detect_darija(last_msg)

    # If the latest message is very short (1-2 words) and ambiguous, check previous message in conversation
    if not det["is_darija"] and det["lang"] == "fr-FR" and len(last_msg.split()) <= 2 and len(messages) >= 2:
        prev_msg = messages[-2].get("content", "")
        prev_det = detect_darija(prev_msg)
        if prev_det["is_darija"]:
            return prev_det

    return det


# =====================================================================
# Text-to-speech (Microsoft Edge neural voices)
# =====================================================================
VOICES = {
    "male": {"ar": "ar-MA-JamalNeural", "fr": "fr-FR-HenriNeural", "en": "en-US-AndrewNeural"},
    "female": {"ar": "ar-MA-MounaNeural", "fr": "fr-FR-DeniseNeural", "en": "en-US-AvaNeural"},
}
LANG_CODES = {"ar": "ar-MA", "fr": "fr-FR", "en": "en-US"}
TTS_MAX_CHARS = 1400

_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")
_EMOJI = re.compile(r"[\U0001F000-\U0001FFFF☀-➿️‍]")


def clean_text_for_speech(raw_text: str) -> str:
    t = re.sub(r"```[\s\S]*?```", " ", raw_text or "")
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"\|", " , ", t)
    t = _EMOJI.sub(" ", t)
    t = re.sub(r"[*_#>~]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > TTS_MAX_CHARS:
        cut = t[:TTS_MAX_CHARS]
        end = max(cut.rfind("."), cut.rfind("؟"), cut.rfind("!"), cut.rfind("?"), cut.rfind("،"))
        t = cut[: end + 1] if end > TTS_MAX_CHARS * 0.6 else cut
    return t


def _latin_lang(text: str) -> str:
    words = set(re.findall(r"[a-z']+", text.lower()))
    return "en" if len(words & ENGLISH_COMMON_WORDS) >= 2 else "fr"


def split_by_voice(text: str) -> List[Tuple[str, str]]:
    """Split mixed Darija/French text into (lang, chunk) runs so each part gets a native voice.
    Short Latin bits inside Arabic (e.g. 'ok', 'wifi') stay with the Moroccan voice."""
    tokens = text.split()
    runs: List[List[Any]] = []  # [lang, [tokens]]
    for tok in tokens:
        kind = "ar" if _ARABIC.search(tok) else ("lat" if _LATIN.search(tok) else None)
        if kind is None or (runs and runs[-1][0] == kind):
            if runs:
                runs[-1][1].append(tok)
            else:
                runs.append([None, [tok]])
            continue
        if runs and runs[-1][0] is None:
            runs[-1][0] = kind
            runs[-1][1].append(tok)
        else:
            runs.append([kind, [tok]])

    has_arabic = any(r[0] == "ar" for r in runs)
    out: List[Tuple[str, str]] = []
    for kind, toks in runs:
        chunk = " ".join(toks)
        if kind == "lat":
            letters = len(_LATIN.findall(chunk))
            lang = "ar" if (has_arabic and len(toks) == 1 and letters <= 3) else _latin_lang(chunk)
        else:
            lang = "ar" if kind == "ar" or has_arabic else _latin_lang(chunk)
        if out and out[-1][0] == lang:
            out[-1] = (lang, out[-1][1] + " " + chunk)
        else:
            out.append((lang, chunk))
    return out


async def arabizi_to_arabic(text: str) -> str:
    """Darija written in Latin letters ('wach 3andek...') is read badly by any voice:
    rewrite it in Arabic script with Gemini, or fall back to the small word map."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        prompt = ("Réécris ce texte en darija marocaine écrite en lettres arabes, mot pour mot, sans le traduire "
                  "ni le changer (garde les mots français en lettres latines). Réponds uniquement avec le texte.\n\n" + text)
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0, "maxOutputTokens": 2048,
                                        "thinkingConfig": {"thinkingLevel": "minimal"}}}
        for model in ("gemini-flash-lite-latest", "gemini-3.1-flash-lite"):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
                    r = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        json=payload, headers={"x-goog-api-key": key})
                if r.status_code == 200:
                    parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                    out = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                    if _ARABIC.search(out):
                        return out
            except httpx.HTTPError:
                continue
    tokens = text.split()
    return " ".join(ARABIZI_TO_ARABIC_MAP.get(t.lower().strip(".,!?"), t) for t in tokens)


async def _tts_chunk(text: str, voice: str, rate: str) -> bytes:
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            audio.extend(chunk["data"])
    return bytes(audio)


async def synthesize_speech_mp3(text: str, gender: str = "male") -> Tuple[bytes, str]:
    """
    Neural speech (MP3) with a native voice per language: Moroccan voice (Jamal / Mouna) for
    Darija and Arabic, French voice for French parts, English voice for English.
    Returns (mp3_bytes, main_lang_code).
    """
    voices = VOICES.get(gender, VOICES["male"])
    clean = clean_text_for_speech(text)
    if not clean:
        return b"", "fr-FR"

    if not _ARABIC.search(clean) and detect_darija(clean)["is_darija"]:
        clean = await arabizi_to_arabic(clean)

    segments = [(lang, chunk) for lang, chunk in split_by_voice(clean) if chunk.strip()]
    if not segments:
        return b"", "fr-FR"
    main_lang = max(segments, key=lambda s: len(s[1]))[0]

    # Each voice returns the same MP3 format (24 kHz mono), so the parts can be joined directly.
    parts = await asyncio.gather(*[
        _tts_chunk(chunk, voices[lang], "+4%" if lang == "ar" else "+6%") for lang, chunk in segments
    ])
    return b"".join(parts), LANG_CODES[main_lang]
