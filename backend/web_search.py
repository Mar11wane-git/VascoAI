import asyncio
import os
import datetime
import re
import urllib.parse
from typing import List, Dict, Any
import httpx
from bs4 import BeautifulSoup

# Shared persistent HTTP client for ultra-fast connection pooling
_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=3.0,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            headers={
                # Honest bot UA with contact URL (Wikimedia rejects spoofed browser UAs with 403).
                "User-Agent": os.getenv(
                    "AGENT_USER_AGENT",
                    f"VascoAgent/4.0 (self-hosted AI assistant; +{os.getenv('APP_URL', 'http://localhost:8000')})",
                ),
                "Accept-Language": "fr-FR,fr;q=0.9,ar-MA;q=0.8,en-US;q=0.7",
            },
        )
    return _HTTP_CLIENT


STOP_WORDS = {
    "avec", "qui", "va", "jouer", "joue", "aujourd'hui", "aujourdhui", "ce", "soir",
    "demain", "hier", "en", "le", "la", "les", "de", "du", "des", "un", "une", "et",
    "ou", "est", "sont", "que", "quoi", "quel", "quelle", "quels", "quelles", "contre",
    "dans", "sur", "pour", "par", "plus", "moins", "comme", "comment", "pourquoi",
    "quand", "combien", "dis", "moi", "peux", "tu", "me", "dire", "savoir", "veux",
    "aimerais", "stp", "svp", "merci", "bonjour", "salut", "vasco", "donne", "fais",
    # Darija stop words
    "salam", "slm", "khouya", "khoya", "khti", "wach", "wash", "chno", "chnou",
    "achno", "kifach", "kifash", "3lach", "dial", "dyal", "bghit", "3afak", "goli",
    "goliya", "had", "hadchi", "daba", "شنو", "أشنو", "كيفاش", "علاش", "واش",
    "ديال", "خويا", "عافاك", "بغيت", "قوليا", "دابا"
}

# Normalize Darija Latin terms into standard search keywords for better search coverage
DARIJA_SEARCH_MAP = {
    "lmaghrib": "Maroc",
    "maghrib": "Maroc",
    "akhbar": "actualités",
    "lakhbar": "actualités",
    "lyoum": "aujourd'hui",
    "lyouma": "aujourd'hui",
    "ta9s": "météo",
    "ljaw": "météo",
    "kora": "football",
    "mountakhab": "équipe du Maroc football",
    "lmountakhab": "équipe du Maroc football",
    "wydad": "Wydad AC",
    "raja": "Raja Club Athletic",
    "dirham": "MAD dirham marocain",
}

CASUAL_GREETINGS = {
    "bonjour", "salut", "hello", "hi", "salam", "slm", "salam alikoum",
    "salam khouya", "labas", "cv", "ca va", "ça va", "merci", "chokran",
    "ok", "wakha", "d'accord", "test", "سلام", "السلام عليكم", "لاباس", "شكرا"
}


def should_run_live_search(query: str, web_toggle_enabled: bool = True) -> bool:
    """
    Determines whether to run live multi-source search.
    Skips pure greetings, short chit-chat, or pure coding tasks so those respond in <1s,
    and runs live search for all informational, factual, news, or current-events queries!
    """
    q_clean = re.sub(r"[^\w\s\u0600-\u06FF]", "", query.lower()).strip()
    if not q_clean or q_clean in CASUAL_GREETINGS or len(q_clean) < 4:
        return False

    # Skip web search for pure coding requests unless explicitly asking for latest docs
    code_starters = ("écris un code", "ecris un code", "crée une fonction", "cree une application", "corrige ce code")
    if any(q_clean.startswith(cs) for cs in code_starters):
        return False

    return web_toggle_enabled or True


def extract_clean_keywords(raw_query: str) -> str:
    """
    Extracts core entities and topic keywords from French, English, Arabic, or Moroccan Darija.
    """
    cleaned = re.sub(r"['’`]", " ", raw_query)
    cleaned = re.sub(r"[^\w\s\-\u0600-\u06FF]", " ", cleaned)
    words = cleaned.split()

    kept = []
    for w in words:
        w_low = w.lower()
        if w_low in DARIJA_SEARCH_MAP:
            kept.append(DARIJA_SEARCH_MAP[w_low])
            continue
        if w_low in STOP_WORDS or len(w_low) <= 1:
            continue
        if w_low == "leagues":
            w = "league"
        kept.append(w)

    if not kept:
        return raw_query.strip()
    return " ".join(kept[:7])


async def fetch_google_news_rss(keywords: str, edition: str = "FR", max_items: int = 4) -> List[Dict[str, Any]]:
    """
    Fetches live breaking news from Google News RSS (FR, MA, AR-MA, or US).
    Responds in ~0.5-0.9s with zero rate-limiting.
    """
    client = get_http_client()
    q_encoded = urllib.parse.quote(keywords)

    if edition == "MA_FR":
        url = f"https://news.google.com/rss/search?q={q_encoded}&hl=fr&gl=MA&ceid=MA:fr"
    elif edition == "MA_AR":
        url = f"https://news.google.com/rss/search?q={q_encoded}&hl=ar&gl=MA&ceid=MA:ar"
    elif edition == "US":
        url = f"https://news.google.com/rss/search?q={q_encoded}&hl=en-US&gl=US&ceid=US:en"
    else:
        url = f"https://news.google.com/rss/search?q={q_encoded}&hl=fr&gl=FR&ceid=FR:fr"

    results: List[Dict[str, Any]] = []
    try:
        resp = await client.get(url, timeout=2.2)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "xml")
            for item in soup.find_all("item")[:max_items]:
                title = item.title.get_text(strip=True) if item.title else ""
                link = item.link.get_text(strip=True) if item.link else ""
                pub_date = item.pubDate.get_text(strip=True) if item.pubDate else ""
                source_tag = item.find("source")
                source_name = source_tag.get_text(strip=True) if source_tag else "Actualités"

                desc_raw = item.description.get_text() if item.description else ""
                desc_clean = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True)

                if title:
                    results.append({
                        "title": title,
                        "url": link or f"https://www.google.com/search?q={q_encoded}",
                        "snippet": f"[{pub_date}] {desc_clean}" if pub_date else desc_clean,
                        "source": source_name,
                        "type": "news",
                    })
    except Exception:
        pass
    return results


async def fetch_wikipedia_fast(keywords: str, lang: str = "fr", max_items: int = 2) -> List[Dict[str, Any]]:
    """Fetches encyclopedic knowledge & facts from Wikipedia REST API in ~0.4s."""
    client = get_http_client()
    results: List[Dict[str, Any]] = []
    try:
        wiki_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": keywords,
            "utf8": "1",
            "format": "json",
            "srlimit": max_items,
        }
        resp = await client.get(wiki_url, params=params, timeout=1.8)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet_html = item.get("snippet", "")
                snippet = BeautifulSoup(snippet_html, "html.parser").get_text(" ", strip=True)
                page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                if title and snippet:
                    results.append({
                        "title": f"{title} (Wikipédia)",
                        "url": page_url,
                        "snippet": snippet,
                        "source": f"{lang}.wikipedia.org",
                        "type": "wiki",
                    })
    except Exception:
        pass
    return results


async def perform_web_search(query: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """
    Runs multi-source live search (Google News Maroc/Monde + Wikipedia Encyclopédie)
    in parallel in ~0.8 to 1.4 seconds so Vasco has up-to-date answers on ANY topic.
    """
    keywords = extract_clean_keywords(query)
    if not keywords:
        return []

    has_arabic = bool(re.search(r"[\u0600-\u06FF]", keywords))

    tasks = [
        fetch_google_news_rss(keywords, edition="MA_FR", max_items=3),
        fetch_google_news_rss(keywords, edition="MA_AR" if has_arabic else "FR", max_items=3),
        fetch_wikipedia_fast(keywords, lang="ar" if has_arabic else "fr", max_items=2),
    ]

    combined: List[Dict[str, Any]] = []
    seen_titles = set()

    try:
        done_lists = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=1.9,
        )
        for lst in done_lists:
            if isinstance(lst, list):
                for item in lst:
                    t_key = item["title"][:45].lower()
                    if t_key not in seen_titles:
                        seen_titles.add(t_key)
                        combined.append(item)
    except Exception:
        pass

    return combined[:max_results]


def format_search_results_for_prompt(results: List[Dict[str, Any]]) -> str:
    """Formats live web, news, and encyclopedia results into a compact, fast real-time briefing."""
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    if not results:
        return f"[DATE ET HEURE ACTUELLES : {now_str}]"

    lines = [
        f"=== 🔴 DONNÉES EN DIRECT & CONNAISSANCES À JOUR ({now_str}) ===",
        "Utilise OBLIGATOIREMENT ces informations récentes pour répondre vite et avec exactitude (ne dis JAMAIS que tes données s'arrêtent en 2023 ou 2024) :",
    ]
    for idx, r in enumerate(results, 1):
        short_snippet = (r.get("snippet") or "")[:220]
        lines.append(f"[{idx}] ({r['source']}) {r['title']} — {short_snippet}")
    lines.append("================================================================")
    return "\n".join(lines)
