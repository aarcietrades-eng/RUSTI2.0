"""Natural-language conversation handler for RUSTI.

Handles @mentions, replies to RUSTI, DMs, and messages in the configured
chat channel. Detects contract addresses, routes to /analyze-style analysis
when intent is explicit, and otherwise runs a conversational LLM call with
per-user history, live web search, and knowledge-base retrieval.
"""
import re
import time
from collections import deque

import discord

from config import CHAT_CHANNEL_ID, GROQ_MODEL, ENABLE_WEB_SEARCH
from prompts import RUSTI_CHAT_PROMPT
from analysis import analyze_token, client  # reuse the AsyncOpenAI client
from views import ReportView
from database import (
    append_conversation_message,
    get_conversation_history,
    search_knowledge,
)
from web_search import search_web, query_looks_informational

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
CHAT_HISTORY_TURNS = 6           # messages of context per (channel, user)
CHAT_MAX_LEN = 1_900             # Discord hard limit is 2000; stay under
CHAT_RATE_WINDOW = 60            # seconds
CHAT_RATE_MAX = 5                # messages per window per user
KB_HITS = 3                      # knowledge-base entries injected per reply
WEB_RESULTS = 5                  # Serper results fetched per reply

# Solana base58, 32-44 chars, no 0/O/I/l
_SOLANA_CA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# Strong intent keywords that justify running an analysis immediately
_ANALYZE_INTENT_RE = re.compile(
    r"\b("
    r"analyz(e|ing|er)|analys(e|ing|er)|analysis|"
    r"check|checking|review|reviewing|scan|scanning|"
    r"audit|auditing|evaluate|evaluating|assess|assessing|"
    r"rug|rugpull|rugged|rugging|"
    r"safe|safety|"
    r"thoughts\s+on|what\s+do\s+you\s+think|"
    r"look\s+at"
    r")\b",
    re.IGNORECASE,
)

# Chart-intent keywords
_CHART_INTENT_RE = re.compile(
    r"\b(chart|candle|candlestick|graph|plot|visual)\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Per-user rate limiter (sliding window)
# ---------------------------------------------------------------------------
_chat_history: dict[int, deque] = {}


def _rate_limit_ok(user_id: int) -> bool:
    now = time.time()
    bucket = _chat_history.setdefault(user_id, deque())
    while bucket and now - bucket[0] > CHAT_RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= CHAT_RATE_MAX:
        return False
    bucket.append(now)
    return True


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------
def extract_contract_addresses(text: str) -> list[str]:
    return _SOLANA_CA_RE.findall(text or "")


def has_analyze_intent(text: str) -> bool:
    return bool(_ANALYZE_INTENT_RE.search(text or ""))


def strip_mentions(text: str) -> str:
    """Remove user and role mentions so they don't confuse the model."""
    text = re.sub(r"<@!?\d+>", "", text)
    text = re.sub(r"<@&\d+>", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Trigger detection — decides whether RUSTI should even look at a message
# ---------------------------------------------------------------------------
def should_handle_message(message: discord.Message, bot) -> bool:
    if message.author.bot:
        return False
    if not message.content or not message.content.strip():
        return False
    if len(message.content) > 2_000:
        return False

    # DMs
    if isinstance(message.channel, discord.DMChannel):
        return True

    # @-mention
    if bot.user and bot.user in message.mentions:
        return True

    # Reply to a RUSTI message
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        ref = message.reference.resolved
        if ref.author and bot.user and ref.author.id == bot.user.id:
            return True

    # Configured chat channel
    if CHAT_CHANNEL_ID and message.channel.id == CHAT_CHANNEL_ID:
        return True

    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def handle_conversation(message: discord.Message, bot) -> None:
    if not _rate_limit_ok(message.author.id):
        await message.reply(
            f"⏳ Slow down — I can reply up to {CHAT_RATE_MAX} times per minute. "
            "Try again shortly.",
            mention_author=False,
        )
        return

    cleaned = strip_mentions(message.content)

    if not cleaned:
        await message.reply(
            "Hi! I'm RUSTI. Ask me about Solana memecoins, NFTs, or crypto concepts — "
            "or paste a token contract address and I'll offer to analyze it. "
            "Type `/` to see my commands.",
            mention_author=False,
        )
        return

    cas = extract_contract_addresses(cleaned)
    wants_analysis = has_analyze_intent(cleaned)
    wants_chart = bool(_CHART_INTENT_RE.search(cleaned))

    if cas and wants_chart:
        await _handle_chart_request(message, cas[0])
    elif cas and wants_analysis:
        await _handle_analysis_request(message, cas[0])
    elif cas:
        await _offer_analysis(message, cas[0])
    else:
        await _handle_chat(message, cleaned, bot)


# ---------------------------------------------------------------------------
# Path 1 — pure conversation (web search + knowledge base)
# ---------------------------------------------------------------------------
async def _handle_chat(message: discord.Message, cleaned: str, bot) -> None:
    history = await get_conversation_history(
        message.channel.id, message.author.id, limit=CHAT_HISTORY_TURNS
    )

    web_context = ""
    if ENABLE_WEB_SEARCH and query_looks_informational(cleaned):
        async with message.channel.typing():
            web_context = await search_web(cleaned, num_results=WEB_RESULTS)

    kb_hits = await search_knowledge(cleaned, limit=KB_HITS)
    kb_context = ""
    if kb_hits:
        kb_context = "\n\n---\nRECENT RESEARCH NOTES:\n" + "\n\n".join(
            f"[{(h.get('created_at') or '')[:10]}] {h['topic']}: "
            f"{(h.get('summary') or '')[:500]}"
            for h in kb_hits
        )

    messages = [{"role": "system", "content": RUSTI_CHAT_PROMPT}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    if message.reference and isinstance(message.reference.resolved, discord.Message):
        ref = message.reference.resolved
        if ref.author and bot.user and ref.author.id == bot.user.id and ref.content:
            messages.append({"role": "assistant", "content": ref.content[:800]})

    user_content = cleaned
    if web_context:
        user_content += (
            "\n\n=== LIVE WEB RESULTS ===\n"
            f"{web_context}\n\n"
            "Use the above results to answer. Cite source domains inline where "
            "relevant. If the results are insufficient, say so."
        )
    if kb_context:
        user_content += kb_context

    messages.append({"role": "user", "content": user_content})

    async with message.channel.typing():
        try:
            resp = await client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                max_tokens=700,
                temperature=0.5,
            )
            answer = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            await message.reply(
                f"**RUSTI couldn't generate a reply.**\n`{type(e).__name__}: {e}`",
                mention_author=False,
            )
            return

    if not answer:
        answer = "(no response generated)"

    await message.reply(answer[:CHAT_MAX_LEN], mention_author=False)

    await append_conversation_message(
        message.channel.id, message.author.id, "user", cleaned
    )
    await append_conversation_message(
        message.channel.id, message.author.id, "assistant", answer
    )


# ---------------------------------------------------------------------------
# Path 2 — explicit analysis request
# ---------------------------------------------------------------------------
async def _handle_analysis_request(message: discord.Message, ca: str) -> None:
    status = await message.reply(
        f"🔍 Running a full RUSTI analysis on `{ca[:8]}…` — one moment.",
        mention_author=False,
    )

    try:
        report = await analyze_token(
            contract_address=ca,
            chain="solana",
            requested_by=str(message.author),
        )
    except Exception as e:
        await status.edit(
            content=f"**Analysis failed:** `{type(e).__name__}: {e}`"
        )
        return

    looks_like_report = (
        "⚠️ This is not financial advice" in report
        or "**EXECUTIVE SUMMARY**" in report.upper()
    )
    if not looks_like_report:
        await status.edit(content=report)
        return

    embed = discord.Embed(
        title="RUSTI Analysis Complete",
        description=report[:1500] + ("..." if len(report) > 1500 else ""),
        color=discord.Color.blue(),
    )
    embed.set_footer(
        text=f"Requested in chat by {message.author.display_name} · "
             "Click the button for the full report"
    )
    await status.edit(content=None, embed=embed, view=ReportView(report))

    await append_conversation_message(
        message.channel.id, message.author.id, "user",
        f"Analyze {ca}",
    )
    await append_conversation_message(
        message.channel.id, message.author.id, "assistant",
        f"[Analysis of {ca} posted — summary: {report[:400]}...]",
    )


# ---------------------------------------------------------------------------
# Path 3 — CA found, no explicit intent → offer a button
# ---------------------------------------------------------------------------
class AnalyzeProposalView(discord.ui.View):
    def __init__(self, ca: str, requester_id: int):
        super().__init__(timeout=300)
        self.ca = ca
        self.requester_id = requester_id

    @discord.ui.button(label="🔍 Analyze this token", style=discord.ButtonStyle.primary)
    async def analyze_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who pasted the address can trigger this analysis.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        report = await analyze_token(
            contract_address=self.ca,
            chain="solana",
            requested_by=str(interaction.user),
        )
        looks_like_report = (
            "⚠️ This is not financial advice" in report
            or "**EXECUTIVE SUMMARY**" in report.upper()
        )
        if not looks_like_report:
            await interaction.followup.send(report)
            return
        embed = discord.Embed(
            title="RUSTI Analysis Complete",
            description=report[:1500] + ("..." if len(report) > 1500 else ""),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Click the button below for the full detailed report")
        await interaction.followup.send(embed=embed, view=ReportView(report))


async def _offer_analysis(message: discord.Message, ca: str) -> None:
    view = AnalyzeProposalView(ca, message.author.id)
    await message.reply(
        f"I see a contract address (`{ca[:8]}…`). Want me to run a full "
        f"RUSTI analysis on it?",
        view=view,
        mention_author=False,
    )


# ---------------------------------------------------------------------------
# Path 4 — chat-detected chart request
# ---------------------------------------------------------------------------
async def _handle_chart_request(message: discord.Message, ca: str) -> None:
    """Chat-detected chart request. Generates and posts a PNG."""
    try:
        from charts import make_candlestick_png
    except ImportError as e:
        await message.reply(
            f"**Chart generation is unavailable.**\n`{e}`",
            mention_author=False,
        )
        return

    status = await message.reply(
        f"📈 Building chart for `{ca[:8]}…`", mention_author=False
    )

    result = await make_candlestick_png(ca)
    if not result:
        await status.edit(content=(
            f"**Could not build a chart for `{ca[:8]}…`** — no OHLCV data "
            "for this token, or the data provider key is missing."
        ))
        return

    buf, filename = result
    try:
        await status.delete()
    except Exception:
        pass  # message may already be deleted
    await message.reply(
        file=discord.File(buf, filename=filename),
        mention_author=False,
    )