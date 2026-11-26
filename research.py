"""Autonomous research: periodically search for emerging crypto/NFT narratives,
summarise with the LLM, and store the summary for later retrieval."""
import asyncio
from datetime import datetime

from config import GROQ_MODEL
from prompts import RUSTI_RESEARCH_PROMPT
from analysis import client  # reuse the AsyncOpenAI client pointed at Groq
from web_search import search_web
from database import save_knowledge_entry

# Rotating query pool — one is picked per cycle
RESEARCH_QUERIES = [
    "new Solana memecoin trends this week",
    "emerging crypto narratives 2026",
    "Solana NFT market update",
    "Pump.fun new token launches today",
    "Solana DeFi yield opportunities",
    "crypto memecoin scam patterns 2026",
    "new Solana projects launching soon",
]


async def run_research_cycle(query_index: int = 0, post_channel=None) -> str | None:
    """Run one research pass. Returns the summary on success, None otherwise."""
    query = RESEARCH_QUERIES[query_index % len(RESEARCH_QUERIES)]
    raw = await search_web(query, num_results=6)
    if not raw:
        return None

    prompt = (
        f"Search query: {query}\n\n"
        f"Raw search results:\n{raw}\n\n"
        "Write a concise intelligence brief (max 400 words) covering:\n"
        "- What is new or notable in this area right now\n"
        "- Any specific tokens, projects, or narratives worth watching\n"
        "- Any red flags or scam patterns visible in the results\n"
        "Be factual. Do not invent details not present in the results. "
        "End with a one-line source list (just the domains)."
    )

    try:
        resp = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": RUSTI_RESEARCH_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=700,
            temperature=0.3,
        )
        summary = (resp.choices[0].message.content or "").strip()
    except Exception:
        return None

    if not summary:
        return None

    await save_knowledge_entry(
        topic=query,
        summary=summary,
        source_urls=raw[:800],
        created_at=datetime.utcnow().isoformat(),
    )

    if post_channel is not None:
        try:
            await post_channel.send(
                f"📚 **Research update — {query}**\n\n{summary[:1800]}"
            )
        except Exception:
            pass  # channel gone or no permission — knowledge still saved

    return summary