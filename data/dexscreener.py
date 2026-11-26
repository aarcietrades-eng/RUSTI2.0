import aiohttp
import asyncio


async def get_token_data(contract_address: str, chain: str = "solana"):
    """Fetch token pairs and basic market data from DexScreener.

    Returns None on any failure — never raises. Callers must handle None.
    """
    url = f"https://api.dexscreener.com/token-pairs/v1/{chain}/{contract_address}"

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        # ValueError covers JSON decode errors
        return None

    # Guard against unexpected response shapes (dict error body, etc.)
    if not isinstance(data, list) or not data:
        return None

    # Take the highest liquidity pair, using `or {}` to survive null fields
    try:
        pair = max(
            data,
            key=lambda x: ((x.get("liquidity") or {}).get("usd") or 0),
        )
    except (TypeError, ValueError):
        return None

    liquidity = pair.get("liquidity") or {}
    volume    = pair.get("volume") or {}
    price_chg = pair.get("priceChange") or {}
    info      = pair.get("info") or {}
    base      = pair.get("baseToken") or {}

    return {
        "name":             base.get("name"),
        "symbol":           base.get("symbol"),
        "price_usd":        pair.get("priceUsd"),
        "market_cap":       pair.get("marketCap"),
        "fdv":              pair.get("fdv"),
        "liquidity_usd":    liquidity.get("usd"),
        "volume_24h":       volume.get("h24"),
        "price_change_24h": price_chg.get("h24"),
        "pair_created_at":  pair.get("pairCreatedAt"),
        "dex":              pair.get("dexId"),
        "url":              pair.get("url"),
        "socials":          info.get("socials") or [],
        "websites":         info.get("websites") or [],
    }