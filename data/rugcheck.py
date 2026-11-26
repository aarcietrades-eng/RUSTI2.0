import aiohttp
import asyncio


async def get_rugcheck_report(contract_address: str):
    """Fetch full security report from RugCheck.

    Returns None on any failure — never raises.
    """
    url = f"https://api.rugcheck.xyz/v1/tokens/{contract_address}/report"

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    return data