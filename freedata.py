"""Free mode data from Yahoo Finance (unofficial, can lag a few minutes)."""
import warnings
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


def _y(sym):
    return sym + ".NS"


def avg_volume(symbols, days=20) -> pd.Series:
    d = yf.download([_y(s) for s in symbols], period="2mo", interval="1d", group_by="ticker",
                    progress=False, threads=True, auto_adjust=False)
    out = {}
    for s in symbols:
        try:
            v = d[_y(s)]["Volume"].dropna()
            v = v[v > 0]
            out[s] = float(v.iloc[-(days + 1):-1].mean()) if len(v) > 5 else np.nan
        except Exception:
            out[s] = np.nan
    return pd.Series(out)


def snapshot(symbols) -> pd.DataFrame:
    """Latest session per symbol from 5-minute bars: ltp, open/high/low, volume, VWAP, buyer share proxy."""
    tick = [_y(s) for s in symbols]
    intra = yf.download(tick, period="5d", interval="5m", group_by="ticker", progress=False,
                        threads=True, auto_adjust=False, prepost=False)
    rows = []
    for s, t in zip(symbols, tick):
        try:
            b = intra[t].dropna(subset=["Close"])
            if b.empty:
                continue
            b.index = b.index.tz_convert("Asia/Kolkata")
            days = sorted(set(b.index.date))
            today = b[b.index.date == days[-1]]
            prev = b[b.index.date == days[-2]] if len(days) > 1 else None
            vol = today["Volume"].fillna(0)
            tp = (today["High"] + today["Low"] + today["Close"]) / 3
            up_vol = vol[today["Close"] >= today["Open"]].sum()
            rows.append({
                "symbol": s,
                "ltp": float(today["Close"].iloc[-1]),
                "prev_close": float(prev["Close"].iloc[-1]) if prev is not None and len(prev) else np.nan,
                "open": float(today["Open"].iloc[0]),
                "high": float(today["High"].max()),
                "low": float(today["Low"].min()),
                "volume": float(vol.sum()),
                "vwap": float((tp * vol).sum() / vol.sum()) if vol.sum() > 0 else np.nan,
                "buyer_pct": float(up_vol / vol.sum() * 100) if vol.sum() > 0 else np.nan,
                "last_bar": today.index[-1].strftime("%d %b %H:%M"),
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


def candles(symbol, interval="5m", period="1d") -> pd.DataFrame:
    b = yf.download(_y(symbol), period=period, interval=interval, progress=False, auto_adjust=False)
    if b.empty:
        return b
    if isinstance(b.columns, pd.MultiIndex):
        b.columns = b.columns.get_level_values(0)
    b.index = b.index.tz_convert("Asia/Kolkata")
    last = b.index[-1].date()
    b = b[b.index.date == last] if interval.endswith("m") else b
    tp = (b["High"] + b["Low"] + b["Close"]) / 3
    b["VWAP"] = (tp * b["Volume"]).cumsum() / b["Volume"].cumsum().replace(0, np.nan)
    return b
