import aiosqlite
from datetime import datetime

DB_PATH = "rusti_memory.db"

# How many messages of history to keep per (channel, user) pair
CONV_KEEP = 20

# How many research entries to keep per topic
KNOWLEDGE_KEEP_PER_TOPIC = 10


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address TEXT NOT NULL,
                chain TEXT,
                token_name TEXT,
                token_symbol TEXT,
                verdict TEXT,
                confidence INTEGER,
                summary TEXT,
                full_report TEXT,
                requested_by TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_contract ON analyses(contract_address)
        """)
        # watches table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'solana',
                channel_id INTEGER NOT NULL,
                guild_id INTEGER,
                requested_by TEXT,
                created_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_alert_at TEXT,
                last_risk_score REAL,
                last_risk_band TEXT,
                last_verdict TEXT,
                last_top10_pct REAL,
                last_liquidity_usd REAL,
                last_rugged INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(contract_address, channel_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_watch_channel ON watches(channel_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_watch_active ON watches(active)
        """)
        # conversation history for natural-language chat
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_lookup
            ON conversations(channel_id, user_id, created_at)
        """)
        # NEW: autonomous research knowledge store
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_urls TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_topic
            ON knowledge(topic, created_at)
        """)
        await db.commit()


# ---- existing analyses helpers ----
async def save_analysis(contract_address: str, chain: str, token_name: str,
                        token_symbol: str, verdict: str, confidence: int,
                        summary: str, full_report: str, requested_by: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO analyses
            (contract_address, chain, token_name, token_symbol, verdict, confidence,
             summary, full_report, requested_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            contract_address.lower(), chain, token_name, token_symbol,
            verdict, confidence, summary, full_report, requested_by,
            datetime.utcnow().isoformat(),
        ))
        await db.commit()


async def get_previous_analyses(contract_address: str, limit: int = 5):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM analyses
            WHERE contract_address = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (contract_address.lower(), limit))
        return [dict(r) for r in await cur.fetchall()]


# ---- watches helpers ----
async def add_watch(contract_address: str, chain: str, channel_id: int,
                    guild_id, requested_by: str, snapshot: dict) -> bool:
    """Returns True if newly added, False if already watching this CA in this channel."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
                INSERT INTO watches
                (contract_address, chain, channel_id, guild_id, requested_by,
                 created_at, last_checked_at, last_risk_score, last_risk_band,
                 last_verdict, last_top10_pct, last_liquidity_usd, last_rugged, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                contract_address.lower(), chain, channel_id, guild_id, requested_by,
                datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                snapshot.get("risk_score"), snapshot.get("risk_band"),
                snapshot.get("verdict"), snapshot.get("top10_pct"),
                snapshot.get("liquidity_usd"),
                1 if snapshot.get("rugged") else 0,
            ))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_watch(contract_address: str, channel_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            DELETE FROM watches WHERE contract_address = ? AND channel_id = ?
        """, (contract_address.lower(), channel_id))
        await db.commit()
        return cur.rowcount > 0


async def get_watches_for_channel(channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM watches WHERE channel_id = ? AND active = 1
            ORDER BY created_at DESC
        """, (channel_id,))
        return [dict(r) for r in await cur.fetchall()]


async def get_active_watches():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM watches WHERE active = 1")
        return [dict(r) for r in await cur.fetchall()]


async def update_watch_state(watch_id: int, snapshot: dict, alert_fired: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        fields = [
            "last_checked_at = ?", "last_risk_score = ?", "last_risk_band = ?",
            "last_verdict = ?", "last_top10_pct = ?", "last_liquidity_usd = ?",
            "last_rugged = ?",
        ]
        params = [
            datetime.utcnow().isoformat(),
            snapshot.get("risk_score"), snapshot.get("risk_band"),
            snapshot.get("verdict"), snapshot.get("top10_pct"),
            snapshot.get("liquidity_usd"),
            1 if snapshot.get("rugged") else 0,
        ]
        if alert_fired:
            fields.append("last_alert_at = ?")
            params.append(datetime.utcnow().isoformat())
        params.append(watch_id)
        await db.execute(
            f"UPDATE watches SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()


async def deactivate_watch(watch_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE watches SET active = 0 WHERE id = ?", (watch_id,))
        await db.commit()


# ---- conversation helpers ----
async def append_conversation_message(channel_id: int, user_id: int,
                                      role: str, content: str):
    """Store one message and trim this pair's history to the last CONV_KEEP rows."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO conversations
            (channel_id, user_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (channel_id, user_id, role, content, datetime.utcnow().isoformat()))
        # Keep only the newest CONV_KEEP rows for this (channel, user) pair
        await db.execute("""
            DELETE FROM conversations
            WHERE channel_id = ? AND user_id = ?
              AND id NOT IN (
                  SELECT id FROM conversations
                  WHERE channel_id = ? AND user_id = ?
                  ORDER BY created_at DESC
                  LIMIT ?
              )
        """, (channel_id, user_id, channel_id, user_id, CONV_KEEP))
        await db.commit()


async def get_conversation_history(channel_id: int, user_id: int,
                                   limit: int = 6):
    """Most recent `limit` messages for this (channel, user), oldest first.

    Returns a list of {"role": ..., "content": ...} dicts, ready to feed
    straight into an OpenAI-compatible chat completions call.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT role, content FROM conversations
            WHERE channel_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (channel_id, user_id, limit))
        rows = [dict(r) for r in await cur.fetchall()]
        return list(reversed(rows))  # oldest first


async def clear_conversation_history(channel_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM conversations WHERE channel_id = ? AND user_id = ?
        """, (channel_id, user_id))
        await db.commit()


# ---- knowledge helpers (NEW) ----
async def save_knowledge_entry(topic: str, summary: str,
                               source_urls: str, created_at: str):
    """Store a research summary and trim to the newest KNOWLEDGE_KEEP_PER_TOPIC
    rows for that topic. Called by research.py after each successful cycle."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO knowledge (topic, summary, source_urls, created_at)
            VALUES (?, ?, ?, ?)
        """, (topic, summary, source_urls, created_at))
        # Keep only the newest N entries per topic
        await db.execute("""
            DELETE FROM knowledge
            WHERE topic = ?
              AND id NOT IN (
                  SELECT id FROM knowledge WHERE topic = ?
                  ORDER BY created_at DESC LIMIT ?
              )
        """, (topic, topic, KNOWLEDGE_KEEP_PER_TOPIC))
        await db.commit()


async def get_recent_knowledge(limit: int = 5):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT * FROM knowledge
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in await cur.fetchall()]


async def search_knowledge(keywords: str, limit: int = 3):
    """Naive keyword match against topic + summary. Good enough for a few
    hundred rows; swap for FTS5 later if the table grows large."""
    words = [w for w in (keywords or "").lower().split() if len(w) > 3][:6]
    if not words:
        return []
    like_clauses = " OR ".join(
        ["lower(topic) LIKE ? OR lower(summary) LIKE ?" for _ in words]
    )
    params = []
    for w in words:
        params.extend([f"%{w}%", f"%{w}%"])
    params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM knowledge WHERE {like_clauses} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]