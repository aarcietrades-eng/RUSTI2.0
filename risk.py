# risk.py
from typing import Optional

RISK_BANDS = [(30, "LOW"), (60, "MEDIUM"), (101, "HIGH")]

def band(score: float) -> str:
    for cutoff, label in RISK_BANDS:
        if score < cutoff:
            return label
    return "HIGH"

def score_rugcheck(report: Optional[dict]) -> dict:
    """Extract a clean, numeric risk snapshot from a RugCheck report."""
    if not report:
        return {"score": None, "band": "UNKNOWN", "rugged": None,
                "danger_flags": [], "warn_flags": []}

    score = report.get("score_normalised")
    if score is None:
        score = report.get("score")  # fall back to raw score
        score = min(float(score), 100) if score is not None else None

    flags = report.get("risks") or []
    danger = [f.get("name") for f in flags if (f.get("level") or "").lower() == "danger"]
    warn   = [f.get("name") for f in flags if (f.get("level") or "").lower() == "warn"]

    return {
        "score": round(float(score), 1) if score is not None else None,
        "band": band(score) if score is not None else "UNKNOWN",
        "rugged": report.get("rugged"),
        "danger_flags": danger,
        "warn_flags": warn,
    }

def holder_concentration(holders: Optional[list]) -> dict:
    """Helius getTokenLargestAccounts returns up to 20 accounts (aggregated by owner)."""
    if not holders:
        return {"top1_pct": None, "top10_pct": None, "holder_count": 0}

    amounts = []
    for h in holders:
        ui = h.get("uiAmount")
        if ui:
            amounts.append(float(ui))
    total = sum(amounts) or 1.0
    top1  = (amounts[0] / total * 100) if amounts else None
    top10 = (sum(amounts[:10]) / total * 100) if len(amounts) >= 10 else None

    return {
        "top1_pct": round(top1, 2) if top1 is not None else None,
        "top10_pct": round(top10, 2) if top10 is not None else None,
        "holder_count": len(amounts),
    }