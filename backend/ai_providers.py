import asyncio
import datetime
import os
import random
import re
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
import httpx
from dotenv import load_dotenv, set_key
from backend.darija import (
    detect_conversation_language,
    AUTO_MULTILINGUAL_AND_DARIJA_PROMPT,
    DARIJA_DETECTED_PROMPT,
)

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(ENV_PATH)

# Persistent high-speed HTTP client (eliminates TCP/TLS handshake latency on repeated requests)
_SHARED_CLIENT: Optional[httpx.AsyncClient] = None


def get_shared_client() -> httpx.AsyncClient:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None or _SHARED_CLIENT.is_closed:
        _SHARED_CLIENT = httpx.AsyncClient(
            timeout=35.0,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
        )
    return _SHARED_CLIENT


def get_default_system_prompt() -> str:
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        f"Tu es **Vasco**, un agent IA universel ultra-rapide et connecté au Web en temps réel (Date actuelle : {now_str}).\n"
        f"- Réponds de manière directe, précise, bien structurée et dans la langue de l'utilisateur.\n"
        f"- Lorsque des [DONNÉES EN DIRECT DU WEB & ACTUALITÉS DU JOUR] sont fournies dans le message, utilise-les OBLIGATOIREMENT pour donner les résultats, scores, adversaires, dates et faits actuels. Ne dis JAMAIS que tes données s'arrêtent en 2023 ou 2024.\n"
        f"- Analyse avec précision les photos, PDF, documents Word et codes joints."
    )


DEEP_THINK_INSTRUCTION = """
MODE RAISONNEMENT APPROFONDI (DeepThink) ACTIVÉ :
Réfléchis étape par étape entre les balises <think> et </think> au début de ta réponse, puis écris ta réponse finale claire après </think>.
"""

def _m(id, name, badge, vision=False, reasoning=False, tools=True):
    return {"id": id, "name": name, "badge": badge, "vision": vision, "reasoning": reasoning, "tools": tools}


PROVIDERS_CATALOG = [
    {
        "id": "vasco_free",
        "name": "⚡ Vasco Cloud (Gratuit • Sans Clé)",
        "requires_key": False,
        "env_key": None,
        "models": [
            _m("openai", "Vasco Agent (GPT-OSS)", "Agent • Outils • Gratuit", reasoning=True),
        ],
    },
    {
        "id": "openai",
        "name": "🟢 ChatGPT (OpenAI)",
        "requires_key": True,
        "env_key": "OPENAI_API_KEY",
        "models": [
            _m("gpt-5", "GPT-5", "Flagship • Vision", vision=True, reasoning=True),
            _m("gpt-5-mini", "GPT-5 mini", "Rapide • Vision", vision=True, reasoning=True),
            _m("gpt-4.1", "GPT-4.1", "Polyvalent • Vision", vision=True),
        ],
    },
    {
        "id": "gemini",
        "name": "✨ Google Gemini",
        "requires_key": True,
        "env_key": "GEMINI_API_KEY",
        "models": [
            _m("gemini-3.5-flash", "Gemini 3.5 Flash", "Recommandé • Rapide • Vision", vision=True, reasoning=True),
            _m("gemini-3.8-flash", "Gemini 3.8 Flash", "Agent • Vision • Souvent saturé", vision=True, reasoning=True),
            _m("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "Gratuit • Toujours dispo", vision=True),
            _m("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Raisonnement • Compte payant", vision=True, reasoning=True),
        ],
    },
    {
        "id": "ollama",
        "name": "🦙 Ollama (IA Locale)",
        "requires_key": False,
        "env_key": "OLLAMA_BASE_URL",
        "models": [
            _m("qwen3:4b", "Qwen 3 4B (Local)", "Local • Outils • Gratuit", reasoning=True),
            _m("llama3.2", "Llama 3.2 (Local)", "100% Local"),
            _m("llava", "LLaVA Vision (Local)", "Vision locale", vision=True, tools=False),
        ],
    },
]


def get_api_keys_status() -> Dict[str, Any]:
    """Returns which API keys are currently configured (masked)."""
    load_dotenv(ENV_PATH, override=True)
    keys = [
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "POLLINATIONS_API_KEY",
    ]
    status = {}
    for k in keys:
        val = os.getenv(k, "").strip()
        status[k] = {
            "configured": bool(val),
            "masked": (val[:4] + "..." + val[-4:]) if len(val) > 8 else ("Configurée" if val else "")
        }
    status["OLLAMA_BASE_URL"] = {
        "configured": True,
        "value": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    }
    return status


def save_api_keys(keys_dict: Dict[str, str]) -> Dict[str, Any]:
    """Saves provided API keys to the .env file and updates os.environ."""
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("# Configuration Vasco AI\n")

    allowed = {
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "POLLINATIONS_API_KEY",
        "OLLAMA_BASE_URL",
    }
    for k, v in keys_dict.items():
        if k in allowed and v is not None:
            clean_v = v.strip()
            os.environ[k] = clean_v
            set_key(ENV_PATH, k, clean_v)

    return get_api_keys_status()


def extract_think_tags(text: str) -> Tuple[Optional[str], str]:
    """Extracts <think>...</think> blocks from a model response if present."""
    if not text:
        return None, ""
    pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(text)
    if matches:
        reasoning = "\n\n".join(m.strip() for m in matches if m.strip())
        cleaned = pattern.sub("", text).strip()
        return reasoning, cleaned
    return None, text.strip()


def build_attachment_text_context(attachments: List[Dict[str, Any]]) -> str:
    """Builds a structured text representation of attachments."""
    if not attachments:
        return ""
    blocks = []
    for idx, att in enumerate(attachments, 1):
        fname = att.get("filename", f"fichier_{idx}")
        cat = att.get("category", "text")
        summary = att.get("summary", "")
        extracted = att.get("extracted_text", "")
        if cat == "image":
            blocks.append(f"🖼️ [Pièce jointe #{idx} - Image : {fname} ({summary})]\n{extracted}")
        else:
            blocks.append(
                f"📄 === CONTENU DU FICHIER JOINT #{idx} : {fname} ({summary}) ===\n"
                f"{extracted}\n"
                f"=== FIN DU FICHIER {fname} ==="
            )
    return "\n\n".join(blocks)


async def call_gemini(
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
    api_key: str,
) -> Tuple[Optional[str], str]:
    """Calls Google Gemini REST API with native multimodal support."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    contents = []
    for i, msg in enumerate(messages):
        role = "user" if msg.get("role") == "user" else "model"
        parts: List[Dict[str, Any]] = []
        text_content = msg.get("content", "")

        if i == len(messages) - 1 and role == "user" and attachments:
            doc_context = build_attachment_text_context([a for a in attachments if a.get("category") != "image"])
            if doc_context:
                text_content = f"{doc_context}\n\n{text_content}"

            for att in attachments:
                if att.get("category") == "image" and att.get("base64"):
                    parts.append({
                        "inline_data": {
                            "mime_type": att.get("mime_type", "image/png"),
                            "data": att["base64"]
                        }
                    })

        if text_content:
            parts.append({"text": text_content})
        if parts:
            contents.append({"role": role, "parts": parts})

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        }
    }

    client = get_shared_client()
    resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        err_detail = resp.text
        try:
            err_detail = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            pass
        raise RuntimeError(f"Erreur API Gemini ({resp.status_code}) : {err_detail}")

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Aucune réponse générée par Gemini.")

    parts = candidates[0].get("content", {}).get("parts", [])
    raw_text = "\n".join(p.get("text", "") for p in parts if "text" in p)
    return extract_think_tags(raw_text)


async def call_openai_compatible(
    endpoint_url: str,
    api_key: Optional[str],
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
    extra_headers: Optional[Dict[str, str]] = None,
    supports_vision: bool = True,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], str]:
    """
    Universal caller for OpenAI, DeepSeek, OpenRouter, Ollama, and Pollinations.
    Uses persistent connection pooling for minimal latency.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    # Keep only last 8 messages to minimize input token latency
    recent_messages = messages[-8:] if len(messages) > 8 else messages

    formatted_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    for i, msg in enumerate(recent_messages):
        role = msg.get("role", "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        text_content = msg.get("content", "")

        if i == len(recent_messages) - 1 and role == "user" and attachments:
            non_image_atts = [a for a in attachments if a.get("category") != "image"]
            image_atts = [a for a in attachments if a.get("category") == "image" and a.get("data_url")]

            if not supports_vision and image_atts:
                non_image_atts.extend(image_atts)
                image_atts = []

            doc_context = build_attachment_text_context(non_image_atts)
            if doc_context:
                text_content = f"{doc_context}\n\n{text_content}"

            if image_atts and supports_vision:
                content_blocks: List[Dict[str, Any]] = [
                    {"type": "text", "text": text_content or "Analyse cette image en détail."}
                ]
                for img_att in image_atts:
                    content_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": img_att["data_url"]}
                    })
                formatted_messages.append({"role": "user", "content": content_blocks})
                continue

        formatted_messages.append({"role": role, "content": text_content})

    payload: Dict[str, Any] = {
        "model": model,
        "messages": formatted_messages,
    }
    if not model.startswith("o3") and not model.startswith("o1") and "pollinations.ai" not in endpoint_url:
        payload["temperature"] = 0.7
    if extra_body:
        payload.update(extra_body)

    client = get_shared_client()
    resp = await client.post(endpoint_url, headers=headers, json=payload)
    if resp.status_code != 200:
        err_msg = resp.text
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            pass
        raise RuntimeError(f"Erreur API ({resp.status_code}) : {err_msg}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("Réponse vide du modèle.")

    msg_obj = choices[0].get("message", {})
    raw_content = msg_obj.get("content") or ""
    native_reasoning = msg_obj.get("reasoning_content") or msg_obj.get("reasoning")

    tag_reasoning, cleaned_content = extract_think_tags(raw_content)
    final_reasoning = native_reasoning or tag_reasoning
    return final_reasoning, cleaned_content


async def call_anthropic(
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
    api_key: str,
) -> Tuple[Optional[str], str]:
    """Calls Anthropic Claude Messages API with native image & document support."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    claude_messages = []
    for i, msg in enumerate(messages):
        role = "user" if msg.get("role") == "user" else "assistant"
        text_content = msg.get("content", "")

        if i == len(messages) - 1 and role == "user" and attachments:
            doc_context = build_attachment_text_context([a for a in attachments if a.get("category") != "image"])
            if doc_context:
                text_content = f"{doc_context}\n\n{text_content}"

            content_blocks: List[Dict[str, Any]] = []
            for att in attachments:
                if att.get("category") == "image" and att.get("base64"):
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.get("mime_type", "image/png"),
                            "data": att["base64"]
                        }
                    })
            content_blocks.append({
                "type": "text",
                "text": text_content or "Analyse les éléments joints."
            })
            claude_messages.append({"role": role, "content": content_blocks})
        else:
            if text_content.strip():
                claude_messages.append({"role": role, "content": text_content})

    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": claude_messages,
    }

    client = get_shared_client()
    resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        err_msg = resp.text
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            pass
        raise RuntimeError(f"Erreur API Claude ({resp.status_code}) : {err_msg}")

    data = resp.json()
    blocks = data.get("content", [])
    thinking_parts = []
    text_parts = []
    for b in blocks:
        if b.get("type") == "thinking":
            thinking_parts.append(b.get("thinking", ""))
        elif b.get("type") == "text":
            text_parts.append(b.get("text", ""))

    raw_text = "\n".join(text_parts)
    tag_reasoning, cleaned_text = extract_think_tags(raw_text)
    final_reasoning = "\n\n".join(thinking_parts) if thinking_parts else tag_reasoning
    return final_reasoning, cleaned_text


async def _call_yqcloud_fast(
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
) -> Tuple[Optional[str], str]:
    """
    Ultra-fast zero-rate-limit free cloud engine (StepFun / Binjie gateway).
    Responds in ~2 seconds with no cooldown between messages.
    """
    recent = messages[-6:] if len(messages) > 6 else messages
    conv_lines = []
    for i, m in enumerate(recent):
        role_label = "Utilisateur" if m.get("role") == "user" else "Vasco"
        txt = m.get("content", "")
        if i == len(recent) - 1 and m.get("role") == "user" and attachments:
            att_ctx = build_attachment_text_context(attachments)
            if att_ctx:
                txt = f"{att_ctx}\n\n{txt}"
        conv_lines.append(f"{role_label}: {txt}")

    full_prompt = "\n\n".join(conv_lines)
    headers = {
        "origin": "https://chat9.yqcloud.top",
        "referer": "https://chat9.yqcloud.top/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "content-type": "application/json",
    }
    payload = {
        "prompt": full_prompt,
        "userId": f"#/chat/{random.randint(1000000000000, 9999999999999)}",
        "network": False,
        "system": system_prompt,
        "withoutContext": False,
        "stream": False,
    }
    client = get_shared_client()
    resp = await client.post(
        "https://api.binjie.fun/api/generateStream",
        headers=headers,
        json=payload,
        timeout=12.0,
    )
    if resp.status_code == 200 and resp.text.strip():
        return extract_think_tags(resp.text)
    raise RuntimeError(f"Yqcloud status {resp.status_code}")


async def _call_pollinations_fast(
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
    deep_think: bool = False,
) -> Tuple[Optional[str], str]:
    """
    Calls Pollinations OpenAI-compatible gateway with reasoning_effort='low' by default
    so responses arrive in ~1.2s when not in DeepThink mode.
    """
    valid_model = model if model in ("openai", "openai-fast", "ovh-reasoning", "gpt-oss-20b") else "openai-fast"
    effort = "high" if (deep_think or valid_model == "ovh-reasoning") else "low"
    reasoning, reply = await call_openai_compatible(
        endpoint_url="https://text.pollinations.ai/openai",
        api_key=None,
        model=valid_model,
        messages=messages,
        attachments=attachments,
        system_prompt=system_prompt,
        supports_vision=False,
        extra_body={"reasoning_effort": effort},
    )
    if not reply or not reply.strip():
        raise RuntimeError("Empty reply from Pollinations")
    show_reasoning = deep_think or (valid_model == "ovh-reasoning")
    return (reasoning if show_reasoning else None), reply


async def call_vasco_free_cloud(
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    system_prompt: str,
    deep_think: bool = False,
) -> Tuple[Optional[str], str]:
    """
    Races Pollinations Turbo (reasoning_effort=low) and Yqcloud Turbo in parallel!
    Whichever engine finishes first with a valid response wins immediately (1–2s latency,
    zero 45-second rate-limit delay!).
    """
    task_poll = asyncio.create_task(
        _call_pollinations_fast(model, messages, attachments, system_prompt, deep_think)
    )
    task_yq = asyncio.create_task(
        _call_yqcloud_fast(messages, attachments, system_prompt)
    )

    pending = {task_poll, task_yq}
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for d in done:
            try:
                reasoning, reply = d.result()
                if reply and reply.strip():
                    # Cancel remaining slower task immediately
                    for p in pending:
                        p.cancel()
                    return reasoning, reply
            except Exception:
                continue

    raise RuntimeError("Les moteurs gratuits sont momentanément occupés. Réessayez dans un instant.")


async def generate_vasco_response(
    provider: str,
    model: str,
    messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    deep_think: bool = False,
    web_search_context: str = "",
    custom_system_prompt: str = "",
    image_generation_mode: bool = False,
    voice_mode: bool = False,
) -> Dict[str, Any]:
    """
    Main orchestrator that routes the user's request to the selected AI provider & model,
    automatically detecting language (Moroccan Darija, Arabic, French, English) from the conversation.
    """
    load_dotenv(ENV_PATH, override=True)

    # Handle direct AI Image Generation mode if toggled
    if image_generation_mode and messages:
        last_prompt = messages[-1].get("content", "").strip()
        encoded_prompt = urllib.parse.quote(last_prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&enhance=true"
        reply = (
            f"🎨 **Voici l'image générée par Vasco pour :** *« {last_prompt} »*\n\n"
            f"![Image générée par Vasco]({img_url})\n\n"
            f"[📥 Télécharger l'image en haute définition]({img_url})"
        )
        return {
            "reply": reply,
            "reasoning": "Génération d'image IA haute définition (1024×1024) à partir de votre description.",
            "provider_used": provider,
            "model_used": model,
            "detected_lang": "fr-FR",
        }

    lang_info = detect_conversation_language(messages)
    use_darija = bool(lang_info.get("is_darija"))
    detected_lang = str(lang_info.get("lang", "fr-FR"))

    # Build System Prompt with 100% automatic language adaptation
    sys_prompt = custom_system_prompt.strip() if custom_system_prompt.strip() else get_default_system_prompt()
    sys_prompt += f"\n\n{AUTO_MULTILINGUAL_AND_DARIJA_PROMPT}"
    if use_darija:
        sys_prompt += f"\n\n{DARIJA_DETECTED_PROMPT}"

    if voice_mode:
        if use_darija:
            sys_prompt += (
                "\n\n🎙️ MODE VOCAL LIVE : راك كتهضر مع المستخدم بالصوت مباشرة (بحال مكالمة صوتية). "
                "جاوب بالدارجة المغربية بالحروف العربية فقط، بجواب قصير بزاف، طبيعي ومباشر (جملة أو جوج جمل ماكسيموم) بلا نجوم وبلا رموز Markdown."
            )
        else:
            sys_prompt += (
                "\n\n🎙️ MODE VOCAL LIVE : Tu dialogues à l'oral en temps réel avec l'utilisateur. "
                "Réponds dans la même langue que l'utilisateur de façon très concise, fluide et naturelle (1 à 3 phrases courtes maximum), sans formatage Markdown."
            )
    if deep_think and model not in ("deepseek-reasoner", "o3-mini") and not voice_mode:
        sys_prompt += f"\n\n{DEEP_THINK_INSTRUCTION}"

    # Inject live Web & News context and automatic language instruction into the latest user message
    enriched_messages = [dict(m) for m in messages]
    if enriched_messages:
        last_user_content = enriched_messages[-1].get("content", "")
        if voice_mode and use_darija:
            lang_note = "\n[جاوب دابا بالدارجة المغربية بالحروف العربية فجملة أو جوج جمل قصار وطبيعيين بحال مكالمة صوتية]"
        elif use_darija:
            lang_note = "\n[ملاحظة مهمة: جاوب بالدارجة المغربية بطريقة طبيعية، واضحة ومفهومة]"
        elif voice_mode:
            lang_note = "\n[Réponds dans la langue de ce message en 1 ou 2 phrases courtes et naturelles pour une conversation vocale en direct]"
        else:
            lang_note = ""

        if web_search_context:
            enriched_messages[-1]["content"] = (
                f"{web_search_context}\n\n"
                f"QUESTION DE L'UTILISATEUR : {last_user_content}{lang_note}\n"
                f"(Réponds directement, rapidement et précisément dans la langue de l'utilisateur en te basant sur les données en direct ci-dessus.)"
            )
        elif lang_note:
            enriched_messages[-1]["content"] = f"{last_user_content}\n{lang_note}"

    # Route by provider
    if provider == "vasco_free":
        target_model = "ovh-reasoning" if (deep_think and model == "openai") else model
        reasoning, reply = await call_vasco_free_cloud(
            model=target_model,
            messages=enriched_messages,
            attachments=attachments,
            system_prompt=sys_prompt,
            deep_think=deep_think,
        )
        return {
            "reply": reply,
            "reasoning": reasoning,
            "provider_used": "Vasco Turbo",
            "model_used": target_model,
        }

    elif provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Clé API Google Gemini manquante. Cliquez sur '🔑 Clés API' en haut à droite pour l'ajouter (ou choisissez '⚡ Vasco Cloud Turbo').")
        reasoning, reply = await call_gemini(model, enriched_messages, attachments, sys_prompt, api_key)
        return {"reply": reply, "reasoning": reasoning, "provider_used": "Google Gemini", "model_used": model}

    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Clé API OpenAI manquante. Cliquez sur '🔑 Clés API' pour l'ajouter (ou utilisez '⚡ Vasco Cloud Turbo').")
        supports_vision = model in ("gpt-4o", "gpt-4o-mini")
        reasoning, reply = await call_openai_compatible(
            endpoint_url="https://api.openai.com/v1/chat/completions",
            api_key=api_key,
            model=model,
            messages=enriched_messages,
            attachments=attachments,
            system_prompt=sys_prompt,
            supports_vision=supports_vision,
        )
        return {"reply": reply, "reasoning": reasoning, "provider_used": "OpenAI", "model_used": model}

    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Clé API Anthropic Claude manquante. Cliquez sur '🔑 Clés API' pour l'ajouter (ou utilisez '⚡ Vasco Cloud Turbo').")
        reasoning, reply = await call_anthropic(model, enriched_messages, attachments, sys_prompt, api_key)
        return {"reply": reply, "reasoning": reasoning, "provider_used": "Anthropic Claude", "model_used": model}

    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Clé API DeepSeek manquante. Cliquez sur '🔑 Clés API' pour l'ajouter (ou utilisez '⚡ Vasco Cloud Turbo').")
        actual_model = "deepseek-reasoner" if deep_think else model
        reasoning, reply = await call_openai_compatible(
            endpoint_url="https://api.deepseek.com/chat/completions",
            api_key=api_key,
            model=actual_model,
            messages=enriched_messages,
            attachments=attachments,
            system_prompt=sys_prompt,
            supports_vision=False,
        )
        return {"reply": reply, "reasoning": reasoning, "provider_used": "DeepSeek", "model_used": actual_model}

    elif provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Clé API OpenRouter manquante. Cliquez sur '🔑 Clés API' pour l'ajouter (ou utilisez '⚡ Vasco Cloud Turbo').")
        supports_vision = any(v in model for v in ("gemini", "gpt-4o", "claude", "vision", "vl"))
        reasoning, reply = await call_openai_compatible(
            endpoint_url="https://openrouter.ai/api/v1/chat/completions",
            api_key=api_key,
            model=model,
            messages=enriched_messages,
            attachments=attachments,
            system_prompt=sys_prompt,
            extra_headers={
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "Vasco AI Assistant",
            },
            supports_vision=supports_vision,
        )
        return {"reply": reply, "reasoning": reasoning, "provider_used": "OpenRouter", "model_used": model}

    elif provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
        supports_vision = "llava" in model or "vision" in model
        reasoning, reply = await call_openai_compatible(
            endpoint_url=f"{base_url}/v1/chat/completions",
            api_key="ollama",
            model=model,
            messages=enriched_messages,
            attachments=attachments,
            system_prompt=sys_prompt,
            supports_vision=supports_vision,
        )
        return {"reply": reply, "reasoning": reasoning, "provider_used": "Ollama Local", "model_used": model}

    else:
        raise ValueError(f"Fournisseur IA inconnu : {provider}")
