from openai import AsyncOpenAI, RateLimitError, APIStatusError, BadRequestError
import asyncio
import json
import re

from config import GROQ_API_KEY, GROQ_MODEL
from prompts import RUSTI_SYSTEM_PROMPT
from data.dexscreener import get_token_data
from data.rugcheck import get_rugcheck_report
from data.helius import get_token_holders
from database import save_analysis, get_previous_analyses
from risk import score_rugcheck, holder_concentration

# Groq exposes an OpenAI-compatible API, so the official `openai` SDK works
# unchanged — only the base_url and api_key differ.
client = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=30.0,  # prevent hangs on network issues
)


async def _call_groq(user_message: str, retries: int = 2) -> str:
    """Call Groq with retry on transient failures (rate limits, 5xx)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": RUSTI_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=2000,
                temperature=0.2,  # low temperature for consistent analysis
            )
            return resp.choices[0].message.content
        except RateLimitError as e:
            last_exc = e
            if attempt == retries:
                raise
            await asyncio.sleep(2 ** attempt)
        except APIStatusError as e:
            last_exc = e
            if e.status_code >= 500 and attempt < retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    raise last_exc  # unreachable; keeps type checkers happy


def _extract_verdict(report: str, fallback: str) -> tuple[str, int]:
    """Parse the model's verdict + confidence, falling back to the deterministic
    pre-verdict if the output can't be parsed cleanly."""
    verdict = None
    for candidate in ("BUY", "AVOID", "HIGH-RISK WATCH"):
        # Matches:  Verdict: BUY   ·   **Verdict**: **BUY**   ·   Verdict**: BUY
        if re.search(rf"verdict\W*:\W*{re.escape(candidate)}\b", report, re.IGNORECASE):
            verdict = candidate
            break
    if verdict is None:
        verdict = fallback

    confidence = 50
    m = re.search(r"confidence\W*:\W*(\d{1,3})", report, re.IGNORECASE)
    if m:
        confidence = max(0, min(100, int(m.group(1))))

    return verdict, confidence


def _pre_verdict(risk: dict, conc: dict) -> str:
    """Deterministic conservative verdict from raw on-chain signals.
    This is the authoritative fallback if the model's output is unparseable,
    and the anchor that the model is asked to confirm or override."""
    if risk["rugged"]:
        return "AVOID"
    if risk["band"] == "HIGH":
        return "AVOID"
    if risk["band"] == "MEDIUM":
        return "HIGH-RISK WATCH"
    if conc["top10_pct"] is not None and conc["top10_pct"] > 70:
        return "HIGH-RISK WATCH"
    if (
        risk["band"] == "LOW"
        and conc["top10_pct"] is not None
        and conc["top10_pct"] < 50
    ):
        return "BUY"
    return "HIGH-RISK WATCH"


def deterministic_verdict(risk: dict, conc: dict) -> str:
    """Public alias for _pre_verdict.

    Used by the watch loop (main.recheck_watches) to compute a verdict from
    on-chain state alone, without spending an LLM call. Keeping this as a
    separate name means the watch path never accidentally pulls in the
    private helper's internals if they ever diverge.
    """
    return _pre_verdict(risk, conc)


async def analyze_token(
    contract_address: str,
    chain: str = "solana",
    requested_by: str = "unknown",
) -> str:
    # 1. Gather data (every fetcher returns None on failure — it never raises)
    dex_data = await get_token_data(contract_address, chain)
    rug_data = await get_rugcheck_report(contract_address)
    holders = await get_token_holders(contract_address)
    previous = await get_previous_analyses(contract_address, limit=3)

    # 2. Insufficient-data guard — do not call the model if we have nothing
    if dex_data is None and rug_data is None:
        return (
            "**RUSTI could not analyze this token.**\n"
            "Neither DexScreener nor RugCheck returned data for this contract.\n"
            "Possible causes: the token has no DEX pair yet, the contract address "
            "is incorrect, or it is on a chain RUSTI does not support.\n\n"
            "---\n"
            "⚠️ This is not financial advice. Memecoins are extremely high risk. "
            "Always do your own research."
        )

    # 3. Deterministic risk engine
    risk = score_rugcheck(rug_data)
    conc = holder_concentration(holders)
    pre_verdict = _pre_verdict(risk, conc)

    # 4. Trim previous analyses so we don't burn tokens on 3 × 2000-char reports
    previous_trim = [
        {k: v for k, v in p.items() if k != "full_report"} for p in previous
    ]

    # 5. Build context for the model
    context = {
        "contract_address": contract_address,
        "chain": chain,
        "dexscreener": dex_data,
        "rugcheck_raw": rug_data,
        "risk_snapshot": risk,
        "holder_concentration": conc,
        "previous_analyses": previous_trim,
        "deterministic_pre_verdict": pre_verdict,
    }

    user_message = f"""Analyze this token thoroughly using the data below.

Contract Address: {contract_address}
Chain: {chain}

=== DETERMINISTIC RISK ENGINE (computed before you see this) ===
- RugCheck score: {risk['score']} (band: {risk['band']})
- Rugged flag: {risk['rugged']}
- Danger flags: {risk['danger_flags']}
- Warn flags: {risk['warn_flags']}
- Top-1 holder: {conc['top1_pct']}%
- Top-10 holder concentration: {conc['top10_pct']}%
- Pre-verdict: {pre_verdict}

Your job is to confirm, refine, or explicitly override this pre-verdict with reasoning.
If you disagree with the pre-verdict, state clearly why.

=== RAW DATA ===
{json.dumps(context, indent=2, default=str)}
"""

    # 6. Call the model (with retry)
    try:
        full_report = await _call_groq(user_message)
    except BadRequestError as e:
        # Groq 400s usually mean the prompt exceeded the model context window
        # or a parameter was rejected.
        return (
            "**RUSTI request was rejected by Groq.**\n"
            f"`{e}`\n\n"
            "This usually means the prompt exceeded the model's context window "
            "or a parameter was invalid."
        )
    except RateLimitError:
        return (
            "**RUSTI is rate-limited by Groq.**\n"
            "The free tier allows 30 requests per minute. "
            "Wait a moment and try `/analyze` again."
        )
    except Exception as e:
        return (
            "**RUSTI encountered an upstream error while generating the report.**\n"
            f"`{type(e).__name__}: {e}`\n\n"
            "Data was collected successfully — try `/analyze` again in a moment."
        )

    # 7. Parse structured fields (fall back to deterministic pre-verdict)
    verdict, confidence = _extract_verdict(full_report, pre_verdict)

    # 8. Persist
    token_name = (dex_data or {}).get("name") or "Unknown"
    token_symbol = (dex_data or {}).get("symbol") or "UNKNOWN"

    # Encode the risk signals into the summary header so /history shows them
    # without requiring a DB schema change.
    meta_tag = (
        f"[{verdict} · {confidence}% · "
        f"risk={risk['band']}({risk['score']}) · "
        f"top10={conc['top10_pct']}%] "
    )
    body = full_report[:300] + "..." if len(full_report) > 300 else full_report
    summary = meta_tag + body

    await save_analysis(
        contract_address=contract_address,
        chain=chain,
        token_name=token_name,
        token_symbol=token_symbol,
        verdict=verdict,
        confidence=confidence,
        summary=summary,
        full_report=full_report,
        requested_by=requested_by,
    )

    return full_report