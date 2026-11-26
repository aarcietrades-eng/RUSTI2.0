"""Material-change detection for watched tokens.

The watch loop re-fetches on-chain state every N minutes and asks this module
"did anything that matters change?" The rules below are deliberately
conservative: we only want to ping a Discord channel when a user would actually
care.
"""
from datetime import datetime, timedelta


# ---- Tuning knobs ----
RISK_SCORE_DELTA    = 15    # RugCheck score points
TOP10_DELTA         = 15    # percentage points of top-10 concentration
LIQUIDITY_DROP_PCT  = 50    # percent drop in USD liquidity (rug signal)
ALERT_COOLDOWN_MIN  = 60    # minimum minutes between alerts for the same watch


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _band_rank(band: str) -> float:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "UNKNOWN": 1.5}.get(band or "UNKNOWN", 1.5)


def _verdict_rank(v: str) -> int:
    return {"BUY": 0, "HIGH-RISK WATCH": 1, "AVOID": 2}.get(v or "HIGH-RISK WATCH", 1)


def _within_cooldown(last_alert_at, minutes: int = ALERT_COOLDOWN_MIN) -> bool:
    if not last_alert_at:
        return False
    try:
        last = datetime.fromisoformat(last_alert_at)
    except ValueError:
        return False
    return (datetime.utcnow() - last) < timedelta(minutes=minutes)


def detect_changes(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """Compare the stored watch row against a freshly-computed snapshot.

    Returns a list of (severity, title, detail) tuples.
    severity ∈ {"critical", "high", "medium"}.
    """
    changes: list[tuple[str, str, str]] = []

    # 1. Rugged flag flip → CRITICAL
    old_rugged = bool(old.get("last_rugged"))
    new_rugged = bool(new.get("rugged"))
    if new_rugged and not old_rugged:
        changes.append((
            "critical", "🚨 RUGGED FLAG SET",
            "RugCheck now marks this token as rugged.",
        ))

    # 2. Risk band change
    old_band = old.get("last_risk_band")
    new_band = new.get("risk_band")
    if old_band and new_band and old_band != new_band:
        worse = _band_rank(new_band) > _band_rank(old_band)
        changes.append((
            "high" if worse else "medium",
            f"Risk band {'deteriorated' if worse else 'improved'}",
            f"{old_band} → {new_band}",
        ))

    # 3. Risk score jump
    old_score = _num(old.get("last_risk_score"))
    new_score = _num(new.get("risk_score"))
    if old_score is not None and new_score is not None:
        delta = new_score - old_score
        if abs(delta) >= RISK_SCORE_DELTA:
            changes.append((
                "high" if delta > 0 else "medium",
                f"Risk score {'↑' if delta > 0 else '↓'} {abs(delta):.0f} pts",
                f"{old_score:.1f} → {new_score:.1f}",
            ))

    # 4. Verdict change
    old_v = old.get("last_verdict")
    new_v = new.get("verdict")
    if old_v and new_v and old_v != new_v:
        worse = _verdict_rank(new_v) > _verdict_rank(old_v)
        changes.append((
            "high" if worse else "medium",
            f"Verdict {'↓' if worse else '↑'} {old_v} → {new_v}",
            "",
        ))

    # 5. Concentration jump
    old_top10 = _num(old.get("last_top10_pct"))
    new_top10 = _num(new.get("top10_pct"))
    if old_top10 is not None and new_top10 is not None:
        delta = new_top10 - old_top10
        if abs(delta) >= TOP10_DELTA:
            changes.append((
                "high" if delta > 0 else "medium",
                f"Top-10 holders {'↑' if delta > 0 else '↓'} {abs(delta):.1f}pp",
                f"{old_top10:.1f}% → {new_top10:.1f}%",
            ))

    # 6. Liquidity crash → CRITICAL (classic rug signature)
    old_liq = _num(old.get("last_liquidity_usd"))
    new_liq = _num(new.get("liquidity_usd"))
    if old_liq and new_liq is not None and old_liq > 0:
        drop_pct = (old_liq - new_liq) / old_liq * 100
        if drop_pct >= LIQUIDITY_DROP_PCT:
            changes.append((
                "critical", f"💧 Liquidity dropped {drop_pct:.0f}%",
                f"${old_liq:,.0f} → ${new_liq:,.0f}",
            ))

    return changes


def should_alert(changes, watch_row) -> bool:
    if not changes:
        return False
    if _within_cooldown(watch_row.get("last_alert_at")):
        return False
    return True


def embed_color(severity: str) -> int:
    return {"critical": 0xE74C3C, "high": 0xE67E22, "medium": 0xF1C40F}.get(severity, 0x95A5A6)


def worst_severity(changes) -> str:
    order = {"medium": 0, "high": 1, "critical": 2}
    return max((c[0] for c in changes), key=lambda s: order.get(s, 0))