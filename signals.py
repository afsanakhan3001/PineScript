"""Turns raw quotes into the scanner table: change %, relative volume,
buyer share, VWAP position, range position and a 0-100 momentum score.

These are rules, not predictions. A high score means price, volume and
order-book pressure all point up right now; it can reverse any minute.
"""
from datetime import datetime, time as dtime
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = dtime(9, 15), dtime(15, 30)


def session_fraction(now=None):
    """How much of the 375-minute NSE session has passed (0.05..1)."""
    now = now or datetime.now(IST)
    if not market_open(now):  # closed: the board shows a full finished session
        return 1.0
    mins = (now.hour * 60 + now.minute) - (9 * 60 + 15)
    return float(min(max(mins / 375.0, 0.05), 1.0))


def market_open(now=None):
    now = now or datetime.now(IST)
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def enrich(df: pd.DataFrame, avg_vol: pd.Series, frac: float) -> pd.DataFrame:
    """df needs: symbol, ltp, prev_close, open, high, low, volume, vwap, buyer_pct (0-100 or NaN)."""
    d = df.copy()
    d["chg_pct"] = (d["ltp"] / d["prev_close"] - 1) * 100
    d["gap_pct"] = (d["open"] / d["prev_close"] - 1) * 100
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["range_pos"] = ((d["ltp"] - d["low"]) / rng * 100).clip(0, 100)
    d["vs_vwap_pct"] = (d["ltp"] / d["vwap"] - 1) * 100
    av = d["symbol"].map(avg_vol)
    d["rvol"] = d["volume"] / (av * frac)
    d["turnover_cr"] = d["ltp"] * d["volume"] / 1e7

    # --- momentum score 0..100 (50 = neutral) ---
    chg = np.tanh(d["chg_pct"] / 3.0)                      # -1..1
    vw = np.tanh(d["vs_vwap_pct"].fillna(0) / 1.0)
    rp = (d["range_pos"].fillna(50) - 50) / 50             # -1..1
    bp = ((d["buyer_pct"].fillna(50) - 50) / 50).clip(-1, 1)
    direction = 0.35 * chg + 0.25 * vw + 0.2 * rp + 0.2 * bp
    power = np.clip(np.log1p(d["rvol"].fillna(1).clip(0, 20)) / np.log(6), 0.4, 1.6)
    d["score"] = (50 + 50 * np.clip(direction * power, -1, 1)).round(0)

    def tag(r):
        if r.score >= 75 and r.rvol >= 1.5:
            return "🚀 Strong buying"
        if r.score >= 65:
            return "🟢 Up momentum"
        if r.score <= 25 and r.rvol >= 1.5:
            return "🩸 Heavy selling"
        if r.score <= 35:
            return "🔴 Down momentum"
        return "⚪ Sideways"

    d["signal"] = d.apply(tag, axis=1)
    return d


def sector_view(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("sector").agg(avg_chg=("chg_pct", "mean"), stocks=("symbol", "count"),
                                 up=("chg_pct", lambda s: int((s > 0).sum())),
                                 turnover_cr=("turnover_cr", "sum"))
    return g.sort_values("avg_chg", ascending=False).reset_index()


def breadth(d: pd.DataFrame):
    return int((d.chg_pct > 0).sum()), int((d.chg_pct < 0).sum()), int((d.chg_pct == 0).sum())


def option_chain_table(oc: dict, width: int = 10):
    """Flatten Dhan option chain to a table around ATM plus PCR, support, resistance, max pain."""
    spot = oc.get("last_price")
    rows = []
    for k, v in (oc.get("oc") or {}).items():
        ce, pe = v.get("ce") or {}, v.get("pe") or {}
        rows.append({
            "strike": float(k),
            "CE OI": ce.get("oi", 0), "CE chg OI": ce.get("oi", 0) - ce.get("previous_oi", 0),
            "CE LTP": ce.get("last_price"), "CE IV": ce.get("implied_volatility"), "CE id": ce.get("security_id"),
            "PE LTP": pe.get("last_price"), "PE IV": pe.get("implied_volatility"),
            "PE chg OI": pe.get("oi", 0) - pe.get("previous_oi", 0), "PE OI": pe.get("oi", 0), "PE id": pe.get("security_id"),
        })
    if not rows:
        return None, {}
    t = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    pcr = t["PE OI"].sum() / max(t["CE OI"].sum(), 1)
    strikes = t["strike"].values
    pain = [((np.clip(s - strikes, 0, None) * t["CE OI"]).sum() + (np.clip(strikes - s, 0, None) * t["PE OI"]).sum()) for s in strikes]
    stats = {
        "spot": spot, "pcr": float(round(pcr, 2)),
        "resistance": float(t.loc[t["CE OI"].idxmax(), "strike"]),
        "support": float(t.loc[t["PE OI"].idxmax(), "strike"]),
        "max_pain": float(strikes[int(np.argmin(pain))]),
    }
    atm = int((t["strike"] - (spot or 0)).abs().idxmin())
    view = t.iloc[max(atm - width, 0): atm + width + 1].copy()
    view["ATM"] = np.where(view.index == atm, "◀ ATM", "")
    return view, stats
