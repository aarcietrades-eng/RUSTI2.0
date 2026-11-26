"""Token imagery and social-post discovery for RUSTI."""
import aiohttp
import asyncio
from urllib.parse import quote

from config import SERPER_API_KEY
from web_search import search_web

_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def get_token_image_url(token_address: str, chain: str = "solana") -> str | None:
    """Fetch token icon URL from DexScreener token profiles."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get("https://api.dexscreener.com/token-profiles/latest/v1") as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    profiles = data if isinstance(data, list) else data.get("data", []) or []
    for p in profiles:
        if (p.get("tokenAddress") or "").lower() == token_address.lower():
            icon = p.get("icon")
            if icon:
                return icon

    # Fallback: pair endpoint
    pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{token_address}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(pair_url) as r:
                if r.status != 200:
                    return None
                d = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    pairs = (d or {}).get("pairs") or []
    if pairs:
        info = pairs[0].get("info") or {}
        return info.get("imageUrl")
    return None


async def find_social_posts(symbol: str, limit: int = 5) -> str:
    """Search X and Reddit for recent posts mentioning the symbol."""
    if not SERPER_API_KEY or not symbol:
        return ""
    queries = [
        f'site:x.com "{symbol}" solana',
        f'site:reddit.com/r/solana "{symbol}"',
    ]
    blocks = []
    for q in queries:
        raw = await search_web(q, num_results=limit)
        if raw:
            blocks.append(raw)
    return "\n\n".join(blocks)