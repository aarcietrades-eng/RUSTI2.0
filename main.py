import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import aiohttp
from collections import OrderedDict
from datetime import datetime
from charts import make_candlestick_png
from visuals import get_token_image_url, find_social_posts

from config import (
    DISCORD_TOKEN, ALERT_CHANNEL_ID,
    RESEARCH_CHANNEL_ID, RESEARCH_INTERVAL_HOURS, GROQ_MODEL,
)
from database import (
    init_db, get_previous_analyses,
    add_watch, remove_watch, get_watches_for_channel,
    get_active_watches, update_watch_state, deactivate_watch,
    save_knowledge_entry, get_recent_knowledge,
)
from analysis import analyze_token, deterministic_verdict, client
from conversation import should_handle_message, handle_conversation
from views import ReportView
from risk import score_rugcheck, holder_concentration
from data.dexscreener import get_token_data
from data.rugcheck import get_rugcheck_report
from data.helius import get_token_holders
from watch_logic import detect_changes, should_alert, embed_color, worst_severity
from web_search import search_web
from research import run_research_cycle, RESEARCH_QUERIES
from prompts import RUSTI_RESEARCH_PROMPT

# ---- Alert tuning (launch alerts, unchanged) ----
ALERT_MIN_LIQUIDITY    = 5_000
ALERT_MAX_RISK_SCORE   = 60
ALERT_MAX_TOP10_PCT    = 70
ALERT_MAX_PER_CYCLE    = 20
ALERT_SEEN_CAP         = 5_000

_seen: "OrderedDict[str, None]" = OrderedDict()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def setup_hook():
    await init_db()
    check_new_launches.start()
    recheck_watches.start()
    autonomous_research.start()


@bot.event
async def on_ready():
    print(f"RUSTI is online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"[sync] failed: {e}")


# ---------------------------------------------------------------------------
# on_message — routes natural-language chat to conversation.py
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    if should_handle_message(message, bot):
        try:
            await handle_conversation(message, bot)
        except Exception as e:
            print(f"[chat] error: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------
@app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
@bot.tree.command(name="analyze", description="Analyze a memecoin by contract address")
@app_commands.describe(
    contract_address="The token contract address",
    chain="Blockchain (default: solana)",
)
async def analyze(interaction: discord.Interaction, contract_address: str, chain: str = "solana"):
    await interaction.response.defer(thinking=True)
    try:
        report = await analyze_token(
            contract_address=contract_address.strip(),
            chain=chain.lower(),
            requested_by=str(interaction.user),
        )
        embed = discord.Embed(
            title="RUSTI Analysis Complete",
            description=report[:1500] + ("..." if len(report) > 1500 else ""),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Click the button below for the full detailed report")
        await interaction.followup.send(embed=embed, view=ReportView(report))
    except Exception as e:
        await interaction.followup.send(f"**Error while analyzing:** `{type(e).__name__}: {e}`")


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------
@bot.tree.command(name="history", description="Show previous analyses for a contract")
@app_commands.describe(contract_address="The token contract address")
async def history(interaction: discord.Interaction, contract_address: str):
    await interaction.response.defer()
    previous = await get_previous_analyses(contract_address.strip(), limit=5)
    if not previous:
        await interaction.followup.send("No previous analyses found for this contract.")
        return
    embed = discord.Embed(
        title=f"Previous Analyses — {contract_address[:8]}...",
        color=discord.Color.greyple(),
    )
    for i, row in enumerate(previous, 1):
        ts = (row.get("created_at") or "")[:16] or "unknown"
        summary = row.get("summary") or "(no summary)"
        value = summary[:200] + ("..." if len(summary) > 200 else "")
        embed.add_field(
            name=f"{i}. {ts} | {row.get('verdict') or 'N/A'}",
            value=value, inline=False,
        )
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /ping  — FIXED: defer() first so the 3-second window closes immediately
# ---------------------------------------------------------------------------
@bot.tree.command(name="ping", description="Check whether RUSTI is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=False)
    await interaction.followup.send(
        f"🟢 RUSTI online · gateway latency: {round(bot.latency * 1000)} ms",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /watch — subscribe this channel to risk-change alerts
# ---------------------------------------------------------------------------
@bot.tree.command(name="watch", description="Subscribe this channel to alerts when a token's risk changes")
@app_commands.describe(
    contract_address="The token contract address",
    chain="Blockchain (default: solana)",
)
async def watch(interaction: discord.Interaction, contract_address: str, chain: str = "solana"):
    await interaction.response.defer(thinking=True)
    ca = contract_address.strip().lower()
    chain = chain.lower()

    dex = await get_token_data(ca, chain)
    rug = await get_rugcheck_report(ca)
    holders = await get_token_holders(ca)

    if dex is None and rug is None:
        await interaction.followup.send(
            "**Cannot watch this token — no data from DexScreener or RugCheck.**\n"
            "Check the contract address and chain."
        )
        return

    risk = score_rugcheck(rug)
    conc = holder_concentration(holders)
    snapshot = {
        "risk_score":     risk["score"],
        "risk_band":      risk["band"],
        "rugged":         risk["rugged"],
        "top10_pct":      conc["top10_pct"],
        "liquidity_usd":  (dex or {}).get("liquidity_usd"),
        "verdict":        deterministic_verdict(risk, conc),
    }

    added = await add_watch(
        contract_address=ca, chain=chain,
        channel_id=interaction.channel_id, guild_id=interaction.guild_id,
        requested_by=str(interaction.user), snapshot=snapshot,
    )

    if not added:
        await interaction.followup.send(
            f"Already watching `{ca}` in this channel. "
            f"Use `/watches` to list active watches."
        )
        return

    symbol = (dex or {}).get("symbol") or "?"
    embed = discord.Embed(
        title=f"👁 Now watching {symbol}",
        description=(
            f"`{ca}`\n\n"
            f"Baseline: risk **{risk['band']}** ({risk['score']}) · "
            f"top-10 **{conc['top10_pct']}%** · verdict **{snapshot['verdict']}**"
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Alerts fire on material risk changes. Use /unwatch to stop.")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /unwatch  — FIXED: defer() first
# ---------------------------------------------------------------------------
@bot.tree.command(name="unwatch", description="Stop watching a token in this channel")
@app_commands.describe(contract_address="The token contract address")
async def unwatch(interaction: discord.Interaction, contract_address: str):
    await interaction.response.defer()
    removed = await remove_watch(contract_address.strip().lower(), interaction.channel_id)
    if removed:
        await interaction.followup.send(
            f"Stopped watching `{contract_address}` in this channel."
        )
    else:
        await interaction.followup.send(
            f"No active watch for `{contract_address}` in this channel.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /watches  — FIXED: defer() first
# ---------------------------------------------------------------------------
@bot.tree.command(name="watches", description="List active watches in this channel")
async def watches(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    rows = await get_watches_for_channel(interaction.channel_id)
    if not rows:
        await interaction.followup.send(
            "No active watches in this channel. Use `/watch` to add one.",
            ephemeral=True,
        )
        return
    embed = discord.Embed(
        title=f"Active Watches ({len(rows)})",
        color=discord.Color.blurple(),
    )
    for r in rows[:10]:
        ca_short = r["contract_address"][:8]
        score = r.get("last_risk_score")
        band = r.get("last_risk_band") or "?"
        checked = (r.get("last_checked_at") or "")[:16] or "never"
        embed.add_field(
            name=f"{ca_short}… · {band}({score})",
            value=f"Last checked: {checked}",
            inline=False,
        )
    if len(rows) > 10:
        embed.set_footer(text=f"Showing 10 of {len(rows)}. Others omitted.")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /chart — generate and post a candlestick PNG
# ---------------------------------------------------------------------------
@bot.tree.command(name="chart", description="Post a candlestick chart for a token")
@app_commands.describe(
    contract_address="The token contract address",
    interval="Candle interval: 1m, 5m, 15m, 1H, 4H, 1D",
)
async def chart(interaction: discord.Interaction,
                contract_address: str,
                interval: str = "15m"):
    await interaction.response.defer(thinking=True)
    result = await make_candlestick_png(
        contract_address.strip(), interval=interval
    )
    if not result:
        await interaction.followup.send(
            "**Could not build a chart.** No OHLCV data available for this token "
            "— check the contract address or your data provider key."
        )
        return
    buf, filename = result
    file = discord.File(buf, filename=filename)
    embed = discord.Embed(
        title=f"📈 {filename.replace('_chart.png', '')}",
        description=f"`{contract_address}`",
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{filename}")
    await interaction.followup.send(embed=embed, file=file)


# ---------------------------------------------------------------------------
# /visuals — token icon + recent social posts
# ---------------------------------------------------------------------------
@bot.tree.command(name="visuals", description="Show token icon and recent social posts")
@app_commands.describe(contract_address="The token contract address")
async def visuals(interaction: discord.Interaction, contract_address: str):
    await interaction.response.defer(thinking=True)
    ca = contract_address.strip()
    icon, posts = await asyncio.gather(
        get_token_image_url(ca),
        find_social_posts(ca[:6], limit=3),
    )
    embed = discord.Embed(
        title="🖼 Token Visuals & Social",
        description=f"`{ca}`",
        color=discord.Color.teal(),
    )
    if icon:
        embed.set_thumbnail(url=icon)
    if posts:
        embed.add_field(name="Recent posts", value=posts[:1800], inline=False)
    else:
        embed.add_field(
            name="Recent posts",
            value="No social results found (Serper key may be missing, or the "
                  "symbol is too new to appear in search).",
            inline=False,
        )
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /research — run a manual research pass or view recent knowledge
# ---------------------------------------------------------------------------
@bot.tree.command(
    name="research",
    description="Run a research cycle now, or view recent knowledge",
)
@app_commands.describe(
    query="Optional specific query to research instead of the default rotation",
)
async def research(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer(thinking=True)

    if not query:
        recent = await get_recent_knowledge(limit=5)
        if not recent:
            await interaction.followup.send(
                "No research yet. Run `/research query:...` or wait for the "
                "autonomous task to complete its first cycle."
            )
            return
        embed = discord.Embed(
            title="📚 Recent Research",
            color=discord.Color.teal(),
        )
        for r in recent:
            topic = (r.get("topic") or "")[:60]
            when = (r.get("created_at") or "")[:10]
            body = (r.get("summary") or "")[:300]
            if len(r.get("summary") or "") > 300:
                body += "..."
            embed.add_field(name=f"{when} · {topic}", value=body, inline=False)
        await interaction.followup.send(embed=embed)
        return

    raw = await search_web(query, num_results=6)
    if not raw:
        await interaction.followup.send(
            "Search returned nothing — check the query or your Serper API key."
        )
        return

    try:
        resp = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": RUSTI_RESEARCH_PROMPT},
                {"role": "user", "content": (
                    f"Query: {query}\n\nResults:\n{raw}\n\n"
                    "Write a 300-word intelligence brief."
                )},
            ],
            max_tokens=600,
            temperature=0.3,
        )
        summary = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        await interaction.followup.send(
            f"**Research failed:** `{type(e).__name__}: {e}`"
        )
        return

    if not summary:
        await interaction.followup.send("No summary generated — try a more specific query.")
        return

    await save_knowledge_entry(
        topic=query, summary=summary,
        source_urls=raw[:800],
        created_at=datetime.utcnow().isoformat(),
    )

    await interaction.followup.send(
        f"📚 **Research: {query}**\n\n{summary[:1800]}"
    )


# ---------------------------------------------------------------------------
# Global slash-command error handler  — FIXED: survives 10062
# ---------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    print(f"[command error] {type(error).__name__}: {error}")

    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Slow down — try again in **{error.retry_after:.1f}s**."
    else:
        msg = f"**Command failed:** `{type(error).__name__}: {error}`"

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except discord.NotFound:
        # 10062: interaction token expired before we could reply.
        # Happens when the event loop stalled >3s. Nothing we can do.
        print("[error handler] interaction expired (10062), suppressed")
    except discord.HTTPException as e:
        print(f"[error handler] couldn't respond: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Launch alerts — poll recent token profiles
# ---------------------------------------------------------------------------
def _remember(ca: str) -> None:
    _seen[ca] = None
    _seen.move_to_end(ca)
    while len(_seen) > ALERT_SEEN_CAP:
        _seen.popitem(last=False)


@tasks.loop(minutes=5)
async def check_new_launches():
    if ALERT_CHANNEL_ID == 0:
        return
    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if channel is None:
        print(f"[alerts] channel {ALERT_CHANNEL_ID} not found")
        return
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get("https://api.dexscreener.com/token-profiles/latest/v1") as r:
                if r.status != 200:
                    return
                data = await r.json()
    except Exception as e:
        print(f"[alerts] fetch failed: {e}")
        return

    profiles = data if isinstance(data, list) else data.get("data", []) or []
    processed = 0
    for profile in profiles[:ALERT_MAX_PER_CYCLE]:
        ca = profile.get("tokenAddress")
        chain = profile.get("chainId", "solana")
        if not ca or chain != "solana" or ca in _seen:
            continue
        dex = await get_token_data(ca, chain)
        if not dex or (dex.get("liquidity_usd") or 0) < ALERT_MIN_LIQUIDITY:
            _remember(ca); continue
        rug = await get_rugcheck_report(ca)
        risk = score_rugcheck(rug)
        if risk["rugged"] or (risk["score"] or 0) > ALERT_MAX_RISK_SCORE:
            _remember(ca); continue
        holders = await get_token_holders(ca)
        conc = holder_concentration(holders)
        if (conc["top10_pct"] or 0) > ALERT_MAX_TOP10_PCT:
            _remember(ca); continue

        embed = discord.Embed(
            title=f"🔔 New candidate: {dex.get('symbol') or '?'}",
            description=(
                f"**{dex.get('name') or 'Unknown'}**\n`{ca}`\n\n"
                f"Liquidity: **${dex.get('liquidity_usd') or 0:,.0f}**\n"
                f"Market Cap: **${(dex.get('market_cap') or 0):,.0f}**\n"
                f"Risk: **{risk['band']}** (score {risk['score']})\n"
                f"Top-10 holders: **{conc['top10_pct']}%**"
            ),
            color=discord.Color.green(),
        )
        if dex.get("url"):
            embed.add_field(name="Chart", value=dex["url"], inline=False)
        embed.set_footer(text="Use /analyze for the full RUSTI report")
        await channel.send(embed=embed)
        _remember(ca)
        processed += 1
    if processed:
        print(f"[alerts] posted {processed} new candidate(s)")


@check_new_launches.before_loop
async def before_check_new_launches():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# recheck_watches — re-evaluate every active watch, alert on change
# ---------------------------------------------------------------------------
@tasks.loop(minutes=10)
async def recheck_watches():
    watch_rows = await get_active_watches()
    if not watch_rows:
        return
    print(f"[watch] rechecking {len(watch_rows)} watch(es)")

    for w in watch_rows:
        channel = bot.get_channel(w["channel_id"])
        if channel is None:
            await deactivate_watch(w["id"])
            continue

        try:
            dex = await get_token_data(w["contract_address"], w["chain"])
            rug = await get_rugcheck_report(w["contract_address"])
            holders = await get_token_holders(w["contract_address"])
        except Exception as e:
            print(f"[watch] fetch failed for {w['contract_address'][:8]}: {e}")
            continue

        if dex is None and rug is None:
            continue

        risk = score_rugcheck(rug)
        conc = holder_concentration(holders)
        new_snapshot = {
            "risk_score":    risk["score"],
            "risk_band":     risk["band"],
            "rugged":        risk["rugged"],
            "top10_pct":     conc["top10_pct"],
            "liquidity_usd": (dex or {}).get("liquidity_usd"),
            "verdict":       deterministic_verdict(risk, conc),
        }

        changes = detect_changes(w, new_snapshot)
        alert_fired = False

        if should_alert(changes, w):
            severity = worst_severity(changes)
            symbol = (dex or {}).get("symbol") or w["contract_address"][:8]
            body = "\n".join(
                f"**{title}**" + (f" — {detail}" if detail else "")
                for _, title, detail in changes
            )
            embed = discord.Embed(
                title=f"👀 Watch update: {symbol}",
                description=f"`{w['contract_address']}`\n\n{body}",
                color=discord.Color(embed_color(severity)),
            )
            embed.set_footer(text="Use /unwatch to unsubscribe")
            try:
                await channel.send(embed=embed)
                alert_fired = True
                print(f"[watch] alerted on {symbol}: {len(changes)} change(s)")
            except discord.Forbidden:
                await deactivate_watch(w["id"])
                continue

        await update_watch_state(w["id"], new_snapshot, alert_fired=alert_fired)
        await asyncio.sleep(0.3)


@recheck_watches.before_loop
async def before_recheck_watches():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# autonomous_research — periodic web research, stored in knowledge table
# ---------------------------------------------------------------------------
_research_index = 0


@tasks.loop(hours=RESEARCH_INTERVAL_HOURS)
async def autonomous_research():
    global _research_index
    channel = bot.get_channel(RESEARCH_CHANNEL_ID) if RESEARCH_CHANNEL_ID else None
    try:
        summary = await run_research_cycle(_research_index, post_channel=channel)
    except Exception as e:
        print(f"[research] cycle {_research_index} crashed: {type(e).__name__}: {e}")
        _research_index += 1
        return

    if summary:
        topic = RESEARCH_QUERIES[_research_index % len(RESEARCH_QUERIES)]
        print(f"[research] cycle {_research_index} complete: {topic}")
    else:
        print(f"[research] cycle {_research_index} produced no summary")
    _research_index += 1


@autonomous_research.before_loop
async def before_research():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
bot.setup_hook = setup_hook

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)