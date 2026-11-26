import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

_alert = os.getenv("ALERT_CHANNEL_ID", "0")
ALERT_CHANNEL_ID = int(_alert) if _alert.strip() else 0

# Optional: set this to a Discord channel ID to have RUSTI respond to every
# message in that channel (in addition to @mentions, replies, and DMs).
# Set to 0 to disable channel-wide chat mode.
_chat = os.getenv("CHAT_CHANNEL_ID", "0")
CHAT_CHANNEL_ID = int(_chat) if _chat.strip() else 0
# ---- Web search + research ----
SERPER_API_KEY = os.getenv("SERPER_API_KEY")  # optional — search disabled if absent

# Optional: channel where autonomous research summaries are posted for review.
# Set to 0 to store research silently without posting.
_research = os.getenv("RESEARCH_CHANNEL_ID", "0")
RESEARCH_CHANNEL_ID = int(_research) if _research.strip() else 0

# How often the autonomous research task runs, in hours
RESEARCH_INTERVAL_HOURS = int(os.getenv("RESEARCH_INTERVAL_HOURS", "6"))

# Enable/disable web search in chat (useful to toggle without code changes)
_ws = os.getenv("ENABLE_WEB_SEARCH", "true").strip().lower()
ENABLE_WEB_SEARCH = _ws in ("1", "true", "yes", "on")

# Fail fast if required keys are missing
_missing = [
    name for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("GROQ_API_KEY", GROQ_API_KEY),
    ] if not val
]
if _missing:
    raise RuntimeError(
        "Missing required environment variable(s): " + ", ".join(_missing)
        + ". Check your .env file."
    )

# Groq model to use. See Step 5 for guidance.
GROQ_MODEL = "openai/gpt-oss-20b"

# Optional defaults
DEFAULT_CHAIN = "solana"
# ---- Visual / real-time data ----
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")          # free tier: 30K CU/mo, 1 rps
HELIUS_WEBHOOK_SECRET = os.getenv("HELIUS_WEBHOOK_SECRET")  # optional
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# Chart defaults
CHART_INTERVAL = os.getenv("CHART_INTERVAL", "15m")     # 1m/5m/15m/1H/4H/1D
CHART_CANDLES = int(os.getenv("CHART_CANDLES", "60"))
# DexPaprika — keyless works, free key extends OHLCV history to 7 days
DEXPAPRIKA_API_KEY = os.getenv("DEXPAPRIKA_API_KEY")