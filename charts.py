"""Render OHLCV candles as a dark-themed PNG for Discord."""
import io
import asyncio
import matplotlib
matplotlib.use("Agg")  # headless — no display needed on the server
import mplfinance as mpf
import pandas as pd

from data.dexpaprika import get_chart_data
from config import CHART_INTERVAL, CHART_CANDLES


# Dark theme matching RUSTI's navy/amber palette
RUSTI_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#2ECC71", down="#E74C3C",
        edge="inherit", wick="inherit", volume="in",
    ),
    facecolor="#0B1220",
    figcolor="#0B1220",
    gridcolor="#1E2A3A",
    gridstyle="--",
)


# Serialize concurrent chart renders — matplotlib has global state and is not
# thread-safe across simultaneous renders. A single /chart call is fine; two
# simultaneous /chart calls are not, so we queue them.
_chart_lock = asyncio.Lock()


def _render_sync(df: pd.DataFrame, title: str, buf: io.BytesIO) -> None:
    """Synchronous matplotlib render. Called via run_in_executor so the
    asyncio event loop stays responsive while the PNG is drawn."""
    mpf.plot(
        df,
        type="candle",
        style=RUSTI_STYLE,
        volume=True,
        title=title,
        ylabel="Price (USD)",
        ylabel_lower="Volume",
        figsize=(10, 5.5),
        tight_layout=True,
        savefig=dict(fname=buf, dpi=120, bbox_inches="tight"),
    )


async def make_candlestick_png(token_address: str,
                               interval: str = None,
                               candles: int = None) -> tuple[io.BytesIO, str] | None:
    interval = interval or CHART_INTERVAL
    candles = candles or CHART_CANDLES

    # DexPaprika returns (candles, token_info) in one call
    result = await get_chart_data(token_address, interval=interval)
    if not result:
        return None
    raw, meta = result
    if not raw:
        return None

    symbol = meta.get("symbol") or token_address[:6]

    df = pd.DataFrame(raw)
    needed = {"time_open", "open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return None

    df["time"] = pd.to_datetime(df["time_open"])
    df = df.rename(columns={
        "open": "Open", "high": "High",
        "low": "Low", "close": "Close", "volume": "Volume",
    })
    df = df.set_index("time")[["Open", "High", "Low", "Close", "Volume"]].astype(float)

    if df.empty:
        return None

    buf = io.BytesIO()
    title = f"\n{symbol}  ·  {interval}  ·  RUSTI"

    # Run the blocking matplotlib render in a thread so the event loop keeps
    # serving other interactions (this was the cause of the 10062 timeouts).
    loop = asyncio.get_running_loop()
    async with _chart_lock:
        try:
            await loop.run_in_executor(None, _render_sync, df, title, buf)
        except Exception as e:
            print(f"[charts] render failed: {type(e).__name__}: {e}")
            return None

    buf.seek(0)
    return buf, f"{symbol}_{interval}_chart.png"