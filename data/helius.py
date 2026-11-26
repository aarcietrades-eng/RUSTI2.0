import aiohttp
import asyncio

from config import HELIUS_API_KEY


async def get_token_holders(contract_address: str, limit: int = 20):
    """Get top token holders using Helius.

    Returns None on any failure — never raises.
    """
    if not HELIUS_API_KEY:
        return None

    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "getTokenLargestAccounts",
        "params": [contract_address],
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    # RPC error shape: {"jsonrpc": "2.0", "error": {...}, "id": "1"}
    if not isinstance(data, dict) or "error" in data:
        return None

    result = data.get("result") or {}
    value  = result.get("value") or []

    if not isinstance(value, list):
        return None

    return value[:limit]