"""DexPaprika REST client — OHLCV candles and token data for Solana.

Keyless tier: 10,000 requests/day, OHLCV limited to last 24h at 1h/6h/12h/24h.
Free key: 7 days at 10m and longer intervals (1m/5m are paid).
"""
import aiohttp
import asyncio
from datetime import datetime, timedelta

from config import DEXPAPRIKA_API_KEY  # add this to config.py

_BASE = "https://api.dexpaprika.com"
_TIMEOUT = aiohttp.ClientTimeout(total=12)


def _headers():
    h = {"Accept": "application/json", "User-Agent": "RUSTI/1.0"}
    if DEXPAPRIKA_API_KEY:
        h["Authorization"] = f"Bearer {DEXPAPRIKA_API_KEY}"
    return h


async def _get(path: str) -> dict | list | None:
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(f"{_BASE}{path}", headers=_headers()) as r:
                if r.status != 200:
                    return None
                return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


async def get_token_info(token_address: str, network: str = "solana") -> dict | None:
    """Token name, symbol, price, liquidity, volume, FDV."""
    data = await _get(f"/networks/{network}/tokens/{token_address}")
    if not isinstance(data, dict):
        return None
    summary = data.get("summary") or {}
    return {
        "name": data.get("name"),
        "symbol": data.get("symbol"),
        "price_usd": summary.get("price_usd"),
        "liquidity_usd": summary.get("liquidity_usd"),
        "volume_24h": summary.get("volume_usd_24h"),
        "fdv": summary.get("fdv"),
        "decimals": data.get("decimals"),
    }


async def get_token_pools(token_address: str, network: str = "solana") -> list[dict] | None:
    """Return pools for a token, sorted by liquidity (highest first)."""
    data = await _get(f"/networks/{network}/tokens/{token_address}/pools")
    if isinstance(data, dict):
        pools = data.get("pools") or data.get("data") or []
    elif isinstance(data, list):
        pools = data
    else:
        return None
    # Sort by liquidity descending so [0] is the best pool
    try:
        pools.sort(
            key=lambda p: (p.get("liquidity_usd") or 0),
            reverse=True,
        )
    except (AttributeError, TypeError):
        pass
    return pools


async def get_pool_ohlcv(pool_address: str, network: str = "solana",
                         interval: str = "1h", limit: int = 100) -> list[dict] | None:
    """OHLCV candles, oldest first. interval ∈ 1h,6h,12h,24h (keyless)
    or 10m,1h,6h,12h,24h,7d (free key)."""
    # Use relative start=-24h for keyless compatibility
    data = await _get(
        f"/networks/{network}/pools/{pool_address}/ohlcv"
        f"?start=-24h&interval={interval}&limit={limit}"
    )
    if not isinstance(data, list):
        return None
    return data


async def get_chart_data(token_address: str, network: str = "solana",
                         interval: str = "1h") -> tuple[list[dict], dict] | None:
    """Convenience: resolve token → best pool → OHLCV.
    Returns (candles, token_info) or None."""
    info = await get_token_info(token_address, network)
    if not info:
        return None
    pools = await get_token_pools(token_address, network)
    if not pools:
        return None
    best = pools[0]
    pool_addr = best.get("address") or best.get("pool_address")
    if not pool_addr:
        return None
    candles = await get_pool_ohlcv(pool_addr, network, interval=interval, limit=100)
    if not candles:
        return None
    return candles, info