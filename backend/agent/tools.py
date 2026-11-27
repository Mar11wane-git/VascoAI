"""
Vasco agent tool registry.

Every tool declares a JSON schema (sent to the model) and an async handler.
Arguments produced by a model are untrusted: `execute_tool` validates them
against the schema before the handler runs, and each handler enforces its own
safety limits (timeouts, output caps, network restrictions).
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import ipaddress
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from backend.images import image_url
from backend.web_search import fetch_google_news_rss, fetch_wikipedia_fast

# An honest bot User-Agent with a contact URL, as Wikimedia's robot policy requires
# (generic or spoofed browser UAs get HTTP 403). Override with AGENT_USER_AGENT.
USER_AGENT = os.getenv(
    "AGENT_USER_AGENT",
    f"VascoAgent/4.0 (self-hosted AI assistant; +{os.getenv('APP_URL', 'http://localhost:8000')})",
)

MAX_TOOL_OUTPUT_CHARS = 12_000


def code_execution_enabled() -> bool:
    return os.getenv("AGENT_CODE_EXECUTION", "false").strip().lower() in ("1", "true", "yes", "on")


# =====================================================================
# Data structures
# =====================================================================

@dataclass
class ToolOutput:
    content: str                      # what the model reads
    ok: bool = True
    summary: str = ""                 # one line for the UI
    preview: str = ""                 # short excerpt for the UI's expandable panel
    sources: List[Dict[str, Any]] = field(default_factory=list)
    image_url: Optional[str] = None
    video: Optional[Dict[str, Any]] = None  # reel spec rendered by the browser (create_video)


@dataclass
class ToolSpec:
    name: str
    label: str                        # human label for the UI
    icon: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Awaitable[ToolOutput]]


class ToolArgumentError(ValueError):
    pass


def _validate_args(spec: ToolSpec, args: Any) -> Dict[str, Any]:
    if not isinstance(args, dict):
        raise ToolArgumentError("arguments must be a JSON object")
    props = spec.parameters.get("properties", {})
    for req in spec.parameters.get("required", []):
        if req not in args or args[req] in (None, ""):
            raise ToolArgumentError(f"missing required argument '{req}'")
    clean: Dict[str, Any] = {}
    type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    required = set(spec.parameters.get("required", []))
    for key, value in args.items():
        if key not in props:
            continue  # ignore unknown keys rather than failing the whole call
        schema = props[key]
        expected = type_map.get(schema.get("type"))
        try:
            if expected and not isinstance(value, expected):
                # Models sometimes send numbers as strings; coerce the safe cases.
                if schema["type"] == "integer":
                    value = int(float(value))
                elif schema["type"] == "number":
                    value = float(value)
                else:
                    raise TypeError
            if "enum" in schema and value not in schema["enum"]:
                raise ValueError
        except (TypeError, ValueError):
            if key in required:
                hint = f"one of {schema['enum']}" if "enum" in schema else f"of type {schema.get('type')}"
                raise ToolArgumentError(f"argument '{key}' must be {hint}")
            continue  # invalid optional argument: fall back to its default
        clean[key] = value
    return clean


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... output truncated, {len(text) - limit} more characters]"


# =====================================================================
# web_search
# =====================================================================

def _ddgs_search(query: str, max_results: int, news: bool) -> List[Dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    try:
        ddgs = DDGS(timeout=8)
        if news:
            rows = ddgs.news(query, max_results=max_results, timelimit="w")
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url") or r.get("href", ""),
                    "snippet": f"[{r.get('date', '')}] {r.get('body', '')}".strip(),
                    "source": r.get("source") or urllib.parse.urlparse(r.get("url", "")).netloc,
                    "type": "news",
                }
                for r in rows
            ]
        rows = ddgs.text(query, max_results=max_results)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": urllib.parse.urlparse(r.get("href", "")).netloc.removeprefix("www."),
                "type": "web",
            }
            for r in rows
        ]
    except Exception:
        return []


_MONTHS = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|"
    "january|february|march|april|may|june|july|august|september|october|november|december"
)
_DATE_NOISE = re.compile(
    rf"\b\d{{1,2}}(er)?\s+({_MONTHS})(\s+\d{{4}})?\b|\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b|"
    r"\baujourd['’]?hui\b|\btoday\b|\bce jour\b|\bdu jour\b|\bاليوم\b|\blyoum\b|"
    # generic "news" words: kind='news' already implies them, and they match unrelated titles
    r"\b(les\s+)?(dernières|derniers|dernieres|latest|recent|récentes|recentes)\b|"
    r"\b(actualités|actualites|actualité|actualite|nouvelles|news|infos|headlines|أخبار|اخبار|akhbar)\b|"
    r"\b(au|du|en|sur|on|in|about)\s*$",
    re.IGNORECASE,
)


def _clean_news_query(query: str) -> str:
    """Exact dates and 'today' in a news query make engines return almost nothing:
    recency is handled by the time filters instead."""
    cleaned = re.sub(r"\s{2,}", " ", _DATE_NOISE.sub(" ", query)).strip(" ,.-")
    cleaned = re.sub(r"^(au|du|de|en|sur|on|in|about|for)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or query


async def tool_web_search(args: Dict[str, Any]) -> ToolOutput:
    query = args["query"].strip()
    max_results = max(1, min(int(args.get("max_results", 6)), 10))
    kind = args.get("kind", "web")
    news = kind == "news"
    if news:
        query = _clean_news_query(query)

    tasks = [asyncio.to_thread(_ddgs_search, query, max_results, news)]
    if news:
        recent = f"{query} when:3d"  # Google News operator: last 3 days
        tasks.append(fetch_google_news_rss(recent, edition="MA_FR", max_items=4))
        tasks.append(fetch_google_news_rss(recent, edition="FR", max_items=3))
        tasks.append(fetch_google_news_rss(recent, edition="US", max_items=3))
    else:
        tasks.append(fetch_wikipedia_fast(query, lang="fr", max_items=2))
        tasks.append(fetch_wikipedia_fast(query, lang="en", max_items=1))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results: List[Dict[str, Any]] = []
    seen = set()
    for lst in gathered:
        if not isinstance(lst, list):
            continue
        for item in lst:
            key = (item.get("url") or item.get("title", "")).lower()
            if not item.get("title") or key in seen:
                continue
            seen.add(key)
            results.append(item)
    results = results[: max_results + 2]

    if not results:
        return ToolOutput(
            content=f"No results found for '{query}'. Try rephrasing the query or using different keywords.",
            ok=True,
            summary="Aucun résultat",
        )

    lines = [f"Search results for: {query}  (retrieved {dt.datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n    URL: {r['url']}\n    {(r.get('snippet') or '')[:350]}")
    lines.append("\nCite the sources you use inline as markdown links.")
    return ToolOutput(
        content="\n".join(lines),
        summary=f"{len(results)} résultat(s)",
        preview="\n".join(f"• {r['title']} — {r.get('source', '')}" for r in results),
        sources=[{k: r.get(k) for k in ("title", "url", "source", "snippet", "type")} for r in results],
    )


# =====================================================================
# fetch_url (with SSRF protection)
# =====================================================================

MAX_FETCH_BYTES = 3 * 1024 * 1024


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified
        ):
            return False
    return True


def _html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "form", "aside"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    lines = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "th", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if len(text) < 2:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append(f"\n## {text}")
        elif el.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    body = "\n".join(lines).strip() or main.get_text("\n", strip=True)
    return title, body


async def tool_fetch_url(args: Dict[str, Any]) -> ToolOutput:
    url = args["url"].strip()
    max_chars = max(1000, min(int(args.get("max_chars", 10000)), 20000))

    async with httpx.AsyncClient(
        timeout=12.0, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"}
    ) as client:
        current = url
        for _hop in range(6):
            parsed = urllib.parse.urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return ToolOutput(content=f"Refused: only public http(s) URLs are allowed ({current}).", ok=False,
                                  summary="URL refusée")
            if not await asyncio.to_thread(_is_public_host, parsed.hostname):
                return ToolOutput(content=f"Refused: {parsed.hostname} is not a public internet host.", ok=False,
                                  summary="Hôte non autorisé")
            async with client.stream("GET", current) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    current = urllib.parse.urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    return ToolOutput(content=f"HTTP {resp.status_code} when fetching {current}.", ok=False,
                                      summary=f"HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "").lower()
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_FETCH_BYTES:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = resp.encoding or "utf-8"
            break
        else:
            return ToolOutput(content="Too many redirects.", ok=False, summary="Trop de redirections")

    host = urllib.parse.urlparse(current).netloc.removeprefix("www.")
    if "application/pdf" in ctype or current.lower().endswith(".pdf"):
        import io
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
            title = host
        except Exception as e:
            return ToolOutput(content=f"Could not read PDF: {e}", ok=False, summary="PDF illisible")
    elif "html" in ctype or not ctype:
        title, text = _html_to_text(raw.decode(encoding, errors="replace"))
    elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
        title, text = host, raw.decode(encoding, errors="replace")
    else:
        return ToolOutput(content=f"Unsupported content type '{ctype}' at {current}.", ok=False,
                          summary="Type non supporté")

    text = text.strip()
    if not text:
        return ToolOutput(content=f"The page {current} has no readable text (it may require JavaScript).",
                          ok=False, summary="Page vide")
    full_len = len(text)
    body = _truncate(text, max_chars)
    return ToolOutput(
        content=f"Title: {title}\nURL: {current}\n\n{body}",
        summary=f"{title[:70] or host} · {full_len:,} caractères".replace(",", " "),
        preview=text[:600],
        sources=[{"title": title or host, "url": current, "source": host, "type": "page"}],
    )


# =====================================================================
# calculator (safe AST evaluation — never eval())
# =====================================================================

_MATH_FUNCS = {
    name: getattr(math, name)
    for name in (
        "sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh", "floor", "ceil", "factorial", "gcd", "lcm", "degrees", "radians",
        "hypot", "comb", "perm", "isqrt", "fabs", "trunc",
    )
}
_MATH_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum})
_MATH_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
    ast.Pow: None,
}


def _safe_eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _MATH_CONSTS:
        return _MATH_CONSTS[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_safe_eval(e) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _safe_eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 1000 or (isinstance(left, (int, float)) and abs(left) > 1e100):
                raise ValueError("exponent too large")
            return left ** right
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MATH_FUNCS:
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        call_args = [_safe_eval(a) for a in node.args]
        if node.func.id == "factorial" and call_args and call_args[0] > 5000:
            raise ValueError("factorial argument too large")
        return _MATH_FUNCS[node.func.id](*call_args)
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


_NUM = r"(\d+(?:\.\d+)?)"


def _percent_to_arithmetic(expr: str) -> str:
    """Models often write percentages with '%', which Python reads as modulo.
    - '17% of 2349.5' / '17 % de 2349.5' / '17%*x'  -> (17/100)*...
    - '17 % 2349.5' where 17 < 2349.5: modulo would just return 17 (pointless), so it's a percentage
    - a trailing or standalone '17%'                -> (17/100)
    A real modulo ('2349 % 17') is left untouched."""
    expr = re.sub(rf"{_NUM}\s*%\s*(?:of|de|du|des|d')\s*", r"(\1/100)*", expr, flags=re.IGNORECASE)

    def maybe_percent(m: "re.Match[str]") -> str:
        a, b = m.group(1), m.group(2)
        return f"({a}/100*{b})" if float(a) < float(b) else m.group(0)

    expr = re.sub(rf"(?<![\w.]){_NUM}\s*%\s*{_NUM}(?![\w.(])", maybe_percent, expr)
    expr = re.sub(rf"{_NUM}%(?!\s*[\d(])", r"(\1/100)", expr)
    return expr.replace(" ", "")


async def tool_calculator(args: Dict[str, Any]) -> ToolOutput:
    original = args["expression"].strip()
    expr = _percent_to_arithmetic(original.replace("^", "**").replace("×", "*").replace("÷", "/"))
    if len(expr) > 500:
        return ToolOutput(content="Expression too long (max 500 characters).", ok=False, summary="Trop long")
    try:
        value = _safe_eval(ast.parse(expr, mode="eval"))
    except ZeroDivisionError:
        return ToolOutput(content="Error: division by zero.", ok=False, summary="Division par zéro")
    except Exception as e:
        return ToolOutput(content=f"Error evaluating '{expr}': {e}. Use run_python for anything beyond arithmetic.",
                          ok=False, summary="Expression invalide")
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    shown = f"{original}  (interprété comme {expr})" if expr != original.replace(" ", "") and "%" in original else expr
    return ToolOutput(content=f"{shown} = {value}", summary=f"= {value}"[:80], preview=f"{shown} = {value}")


# =====================================================================
# get_datetime
# =====================================================================

async def tool_get_datetime(args: Dict[str, Any]) -> ToolOutput:
    tz_name = (args.get("timezone") or "").strip()
    now_utc = dt.datetime.now(dt.timezone.utc)
    lines = [f"UTC: {now_utc.strftime('%A %Y-%m-%d %H:%M:%S')}"]
    local = dt.datetime.now().astimezone()
    lines.append(f"Server local time: {local.strftime('%A %Y-%m-%d %H:%M:%S %Z')}")
    summary = local.strftime("%d/%m/%Y %H:%M")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            zoned = now_utc.astimezone(ZoneInfo(tz_name))
            lines.append(f"{tz_name}: {zoned.strftime('%A %Y-%m-%d %H:%M:%S %Z')}")
            summary = f"{tz_name} · {zoned.strftime('%d/%m/%Y %H:%M')}"
        except Exception:
            lines.append(f"(Unknown timezone '{tz_name}'; use an IANA name like 'Africa/Casablanca'.)")
    return ToolOutput(content="\n".join(lines), summary=summary, preview="\n".join(lines))


# =====================================================================
# generate_image
# =====================================================================

async def tool_generate_image(args: Dict[str, Any]) -> ToolOutput:
    prompt = args["prompt"].strip()[:1000]
    width = max(256, min(int(args.get("width", 1024)), 1536))
    height = max(256, min(int(args.get("height", 1024)), 1536))
    seed = random.randint(1, 10_000_000)
    url = image_url(prompt, width, height, seed)
    return ToolOutput(
        content=(
            f"Image generated ({width}x{height}). Show it to the user by including exactly this markdown "
            f"in your answer:\n\n![{prompt[:80]}]({url})"
        ),
        summary=f"{width}×{height}",
        preview=prompt,
        image_url=url,
    )


# =====================================================================
# create_video : AI reel (images + voice-over + captions), assembled in the browser
# =====================================================================

VIDEO_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (1024, 1024)}
MAX_VIDEO_SCENES = 8


async def tool_create_video(args: Dict[str, Any]) -> ToolOutput:
    raw = args.get("scenes")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, list):
        return ToolOutput(content="Invalid 'scenes': give a list of {narration, image_prompt} objects.",
                          ok=False, summary="Scènes invalides")
    scenes = []
    for s in raw[:MAX_VIDEO_SCENES]:
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or "").strip()[:400]
        image_prompt = str(s.get("image_prompt") or "").strip()[:500]
        if narration and image_prompt:
            scenes.append((narration, image_prompt))
    if len(scenes) < 2:
        return ToolOutput(content="A video needs at least 2 scenes, each with 'narration' and 'image_prompt'.",
                          ok=False, summary="Pas assez de scènes")

    aspect = args.get("aspect") if args.get("aspect") in VIDEO_SIZES else "9:16"
    width, height = VIDEO_SIZES[aspect]
    character = str(args.get("character") or "").strip()[:300]
    style = str(args.get("style") or "").strip()[:200] or "vibrant 3D animated movie style, cinematic lighting"
    seed = random.randint(1, 10_000_000)  # one seed for the whole video keeps the look consistent
    title = str(args.get("title") or "").strip()[:80]
    video = {
        "title": title,
        "aspect": aspect,
        "width": width,
        "height": height,
        "voice": args.get("voice") if args.get("voice") in ("male", "female") else "male",
        "scenes": [
            {"narration": n, "image": image_url(", ".join(x for x in (p, character, style, "highly detailed") if x),
                                                width, height, seed + i)}
            for i, (n, p) in enumerate(scenes)
        ],
    }
    return ToolOutput(
        content=(
            f"The video \"{title or 'sans titre'}\" ({len(scenes)} scenes, {aspect}) is now shown to the user in a "
            "video player with voice-over and captions, with buttons to play it and to download it as a video file. "
            "Do NOT add image markdown or links. Reply in one or two short sentences in the user's language: say it "
            "is ready, that they can press play and download it (to post on Instagram/TikTok), and offer to change it."
        ),
        summary=f"{len(scenes)} scènes · {aspect}",
        preview="\n".join(f"{i + 1}. {n}" for i, (n, _) in enumerate(scenes)),
        video=video,
    )


# =====================================================================
# run_python (opt-in, isolated subprocess)
# =====================================================================

def _run_python_sync(code: str, timeout_s: int) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vasco_py_") as workdir:
        script = os.path.join(workdir, "main.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)
        env = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # required on Windows
            "TEMP": workdir,
            "TMP": workdir,
        }
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", script],
                cwd=workdir, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_s,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {
                "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode,
                "timed_out": False, "elapsed": time.perf_counter() - started,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "stdout": (e.stdout or "") if isinstance(e.stdout, str) else "",
                "stderr": f"Timed out after {timeout_s}s", "code": -1, "timed_out": True,
                "elapsed": float(timeout_s),
            }


async def tool_run_python(args: Dict[str, Any]) -> ToolOutput:
    code = args["code"]
    if len(code) > 30_000:
        return ToolOutput(content="Code too long (max 30,000 characters).", ok=False, summary="Code trop long")
    timeout_s = int(os.getenv("AGENT_CODE_TIMEOUT", "20"))
    res = await asyncio.to_thread(_run_python_sync, code, timeout_s)
    stdout = _truncate(res["stdout"].strip(), 8000)
    stderr = _truncate(res["stderr"].strip(), 4000)
    parts = [f"exit_code: {res['code']}"]
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if not stdout and not stderr:
        parts.append("(no output — use print() to display results)")
    ok = res["code"] == 0
    return ToolOutput(
        content="\n\n".join(parts),
        ok=ok,
        summary=("Exécuté" if ok else ("Délai dépassé" if res["timed_out"] else f"Erreur (code {res['code']})"))
        + f" · {res['elapsed']:.1f}s",
        preview=(stdout or stderr)[:1500],
    )


# =====================================================================
# Registry
# =====================================================================

TOOLS: Dict[str, ToolSpec] = {
    "web_search": ToolSpec(
        name="web_search",
        label="Recherche Web",
        icon="🔎",
        description=(
            "Search the live web. Use it for anything current (news, prices, scores, weather, releases, "
            "people, events) or any fact you are not certain about. Returns titles, URLs and snippets. "
            "Run several focused searches for multi-part questions rather than one vague one. "
            "For news, use kind='news' and do NOT put dates or 'today' in the query: news results are "
            "already limited to the last few days and each shows its publication date."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Concise search query, in the most useful language for the topic."},
                "kind": {"type": "string", "enum": ["web", "news"], "description": "'news' for recent events and headlines, 'web' otherwise."},
                "max_results": {"type": "integer", "description": "Number of results (1-10, default 6)."},
            },
            "required": ["query"],
        },
        handler=tool_web_search,
    ),
    "fetch_url": ToolSpec(
        name="fetch_url",
        label="Lecture de page",
        icon="📖",
        description=(
            "Download a public web page or PDF and return its readable text. Use it after web_search to read "
            "a source in full, or when the user gives a URL."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
                "max_chars": {"type": "integer", "description": "Maximum characters to return (1000-20000, default 10000)."},
            },
            "required": ["url"],
        },
        handler=tool_fetch_url,
    ),
    "calculator": ToolSpec(
        name="calculator",
        label="Calculatrice",
        icon="🧮",
        description=(
            "Evaluate an exact arithmetic expression, like '17/100*2349.5' for 17% of 2349.5 "
            "(+ - * / ** parentheses sqrt log sin cos factorial pi e). "
            "Always use it instead of computing numbers mentally."
        ),
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. '(1234*5678)/3' or 'sqrt(2)*pi'"}},
            "required": ["expression"],
        },
        handler=tool_calculator,
    ),
    "get_datetime": ToolSpec(
        name="get_datetime",
        label="Date & heure",
        icon="🕒",
        description="Get the current date and time, optionally in a given IANA timezone (e.g. 'Africa/Casablanca').",
        parameters={
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "Optional IANA timezone name."}},
            "required": [],
        },
        handler=tool_get_datetime,
    ),
    "generate_image": ToolSpec(
        name="generate_image",
        label="Génération d'image",
        icon="🎨",
        description=(
            "Create an image from a detailed text description. Write the prompt in English, describing subject, "
            "style, lighting and composition. Include the returned markdown in your answer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed English description of the image."},
                "width": {"type": "integer", "description": "256-1536, default 1024."},
                "height": {"type": "integer", "description": "256-1536, default 1024."},
            },
            "required": ["prompt"],
        },
        handler=tool_generate_image,
    ),
    "create_video": ToolSpec(
        name="create_video",
        label="Création de vidéo",
        icon="🎬",
        description=(
            "Create a short video (an AI reel for Instagram/TikTok/YouTube Shorts): AI images for each scene, "
            "a voice-over reading the narration, animated captions and motion. Use it whenever the user asks for a "
            "video, reel, short, clip or animated story. Write a fun, punchy script: 3-6 scenes, each narration one or "
            "two short sentences in the user's language (Moroccan Darija in Arabic script if they write Darija). "
            "Describe the recurring character once in 'character' (English, precise looks/clothes) so it stays "
            "the same in every scene; each image_prompt (English) describes only that scene's action and setting."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title shown at the start."},
                "scenes": {
                    "type": "array",
                    "description": "2-8 scenes in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "narration": {"type": "string", "description": "Voice-over text for this scene, in the user's language."},
                            "image_prompt": {"type": "string", "description": "English description of this scene's image."},
                        },
                        "required": ["narration", "image_prompt"],
                    },
                },
                "character": {"type": "string", "description": "English description of the main character(s), repeated in every image."},
                "style": {"type": "string", "description": "English visual style, e.g. '3D Pixar cartoon, vibrant colors'."},
                "aspect": {"type": "string", "enum": ["9:16", "16:9", "1:1"], "description": "9:16 (default) for Instagram/TikTok, 16:9 for YouTube."},
                "voice": {"type": "string", "enum": ["male", "female"], "description": "Narrator voice."},
            },
            "required": ["scenes"],
        },
        handler=tool_create_video,
    ),
    "run_python": ToolSpec(
        name="run_python",
        label="Exécution Python",
        icon="🐍",
        description=(
            "Run a self-contained Python 3 script in an isolated sandbox and get stdout/stderr. Use it for data "
            "analysis, precise computations, simulations, parsing text, or verifying code. Only the standard "
            "library is guaranteed. No network access is guaranteed. Always print() the results you need."
        ),
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Complete Python script."}},
            "required": ["code"],
        },
        handler=tool_run_python,
    ),
}


def available_tools(web: bool = True, images: bool = True) -> List[ToolSpec]:
    names = ["calculator", "get_datetime"]
    if web:
        names += ["web_search", "fetch_url"]
    if images:
        names += ["generate_image", "create_video"]
    if code_execution_enabled():
        names.append("run_python")
    return [TOOLS[n] for n in names]


def tools_catalog() -> List[Dict[str, Any]]:
    enabled = {t.name for t in available_tools()}
    return [
        {"name": t.name, "label": t.label, "icon": t.icon, "enabled": t.name in enabled}
        for t in TOOLS.values()
    ]


async def execute_tool(name: str, raw_args: Any, allowed: List[str], timeout_s: float = 45.0) -> ToolOutput:
    """Runs a tool call. `allowed` is enforced here, not only in the list shown to the model,
    so a hallucinated call to a disabled tool (e.g. run_python) can never execute."""
    spec = TOOLS.get(name)
    if spec is None or name not in allowed:
        return ToolOutput(content=f"Error: tool '{name}' is not available. Available tools: {', '.join(allowed)}.",
                          ok=False, summary="Outil indisponible")
    try:
        if isinstance(raw_args, str):
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        args = _validate_args(spec, raw_args)
    except (json.JSONDecodeError, ToolArgumentError) as e:
        return ToolOutput(content=f"Invalid arguments for {name}: {e}. Fix the arguments and call the tool again.",
                          ok=False, summary="Arguments invalides")
    try:
        return await asyncio.wait_for(spec.handler(args), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ToolOutput(content=f"Tool {name} timed out after {timeout_s:.0f}s.", ok=False, summary="Délai dépassé")
    except httpx.HTTPError as e:
        return ToolOutput(content=f"Network error in {name}: {e}", ok=False, summary="Erreur réseau")
    except Exception as e:  # never let a tool crash the agent loop
        return ToolOutput(content=f"Tool {name} failed: {type(e).__name__}: {e}", ok=False, summary="Échec")
