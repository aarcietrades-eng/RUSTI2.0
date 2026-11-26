"""Web search via Serper.dev — Google SERP proxy with a generous free tier."""
import aiohttp
import asyncio
import re

from config import SERPER_API_KEY

SERPER_URL = "https://google.serper.dev/search"
_TIMEOUT = aiohttp.ClientTimeout(total=12)

# Rough "does this message want current information?" heuristic
_INFO_SEEK_RE = re.compile(
    r"\b("
    r"latest|current|today|yesterday|this\s+week|this\s+month|"
    r"news|trending|happening|update|recent|new|"
    r"what\s+is|what's|who\s+is|how\s+does|why\s+is|"
    r"explain|tell\s+me\s+about|find|search|look\s+up|"
    r"price|market\s+cap|launch|airdrop|narrative"
    r")\b",
    re.IGNORECASE,
)


def query_looks_informational(text: str) -> bool:
    """True if the message likely benefits from a live web search."""
    return bool(_INFO_SEEK_RE.search(text or ""))


def _format_results(items: list[dict], max_items: int = 5) -> str:
    lines = []
    for i, r in enumerate(items[:max_items], 1):
        title = (r.get("title") or "").strip()
        link = (r.get("link") or "").strip()
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")
        if not title or not link:
            continue
        lines.append(f"{i}. **{title}**\n   {link}\n   {snippet}")
    return "\n\n".join(lines) if lines else ""


async def search_web(query: str, num_results: int = 5) -> str:
    """Return a formatted string of top Google results, or '' on any failure."""
    if not SERPER_API_KEY or not query.strip():
        return ""
    payload = {"q": query.strip(), "num": num_results}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(SERPER_URL, json=payload, headers=headers) as r:
                if r.status != 200:
                    return ""
                data = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return ""
    organic = (data or {}).get("organic") or []
    return _format_results(organic, max_items=num_results)