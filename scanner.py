import json, time, threading, urllib.request, urllib.parse, datetime as dt
import concurrent.futures as cf

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SYMS = [s.strip() for s in open(__file__.replace("scanner.py", "symbols.txt")) if s.strip()]

def fetch(sym):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}.NS?interval=5m&range=5d"
    for _ in range(2):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15)
            res = json.load(r)["chart"]["result"]
            return sym, res[0] if res else None
        except Exception:
            time.sleep(0.5)
    return sym, None

def split_days(r):
    if not r or "timestamp" not in r:
        return {}
    q = r["indicators"]["quote"][0]
    d = {}
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):
            continue
        T = dt.datetime.fromtimestamp(t, IST)
        d.setdefault(T.date(), []).append([T.strftime("%H:%M"), o, h, l, c, v or 0])
    return d

def order_blocks(b, L=3):
    res = []; lh = ll = None; hb = lb = True
    avg = sum(x[2] - x[3] for x in b) / len(b)
    for i in range(len(b)):
        if i >= 2 * L:
            p = i - L
            win = b[p - L:p + L + 1]
            if b[p][2] == max(x[2] for x in win): lh, hb = b[p][2], False
            if b[p][3] == min(x[3] for x in win): ll, lb = b[p][3], False
        c = b[i][4]
        w = b[max(0, i - 2):i + 1]
        strong = (max(x[2] for x in w) - min(x[3] for x in w)) > avg * 1.5
        if not hb and lh is not None and c > lh:
            hb = True
            if strong:
                for j in range(i - 1, max(-1, i - 8), -1):
                    if b[j][4] < b[j][1]:
                        res.append(["bull", b[j][2], b[j][3], j]); break
        if not lb and ll is not None and c < ll:
            lb = True
            if strong:
                for j in range(i - 1, max(-1, i - 8), -1):
                    if b[j][4] > b[j][1]:
                        res.append(["bear", b[j][2], b[j][3], j]); break
        res = [o for o in res if not ((o[0] == "bull" and c < o[2]) or (o[0] == "bear" and c > o[1]))]
    return res

def make_plan(r, side):
    c = r["price"]; zs = [o for o in r["obs"] if o[0] == side]
    if side == "bull":
        zs = [o for o in zs if o[2] <= c]
        if not zs: return None
        z = max(zs, key=lambda o: o[1]); entry = z[1]; sl = z[2] * 0.999; risk = entry - sl
        liq = sorted(x for x in (r["dayH"], r["pdh"]) if x > entry + risk)
        tp1 = liq[0] if liq else entry + 1.5 * risk; tp2 = max(entry + 2 * risk, tp1 + 0.5 * risk)
    else:
        zs = [o for o in zs if o[1] >= c]
        if not zs: return None
        z = min(zs, key=lambda o: o[2]); entry = z[2]; sl = z[1] * 1.001; risk = sl - entry
        liq = sorted((x for x in (r["dayL"], r["pdl"]) if x < entry - risk), reverse=True)
        tp1 = liq[0] if liq else entry - 1.5 * risk; tp2 = min(entry - 2 * risk, tp1 - 0.5 * risk)
    if risk <= 0: return None
    R = lambda x: round(x, 2)
    return dict(zoneTop=R(z[1]), zoneBot=R(z[2]), zoneTime=r["bars"][z[3]][0], entry=R(entry), sl=R(sl),
                tp1=R(tp1), tp2=R(tp2), rr=round(abs(tp1 - entry) / risk, 1))

def scan():
    raw = {}
    with cf.ThreadPoolExecutor(16) as ex:
        for s, r in ex.map(fetch, SYMS):
            if r: raw[s] = r
    stocks = {}
    for s, r in raw.items():
        d = split_days(r); ks = sorted(d)
        if len(ks) >= 2: stocks[s] = (d[ks[-1]], d[ks[-2]], ks[-1])
    if not stocks:
        raise RuntimeError("no data")
    day = max(v[2] for v in stocks.values())
    stocks = {s: v for s, v in stocks.items() if v[2] == day and len(v[0]) >= 4}
    rows = []; flow = {}
    for s, (b, pb, _) in stocks.items():
        pc = pb[-1][4]; c = b[-1][4]; pct = (c - pc) / pc * 100
        flow[s] = [round((x[4] - pc) / pc * 100, 2) for x in b]
        vol = sum(x[5] for x in b); pvol = sum(x[5] for x in pb[:len(b)]) or 1
        hi = max(x[2] for x in b); lo = min(x[3] for x in b); rng = hi - lo
        loc = (c - lo) / rng if rng else .5
        volx = vol / pvol
        score = pct * (0.5 + (loc if pct > 0 else 1 - loc)) * min(2, volx) ** 0.5
        rows.append(dict(sym=s, price=round(c, 2), pct=round(pct, 2), score=score, volx=round(volx, 2),
                         dayH=hi, dayL=lo, pdh=max(x[2] for x in pb), pdl=min(x[3] for x in pb), obs=order_blocks(b), bars=b))
    cands = []
    for r in rows:
        side = "bull" if r["pct"] > 0 else "bear"
        p = make_plan(r, side)
        if not p: continue
        q = abs(r["pct"]) * min(p["rr"], 3) * min(r["volx"], 3) ** 0.5
        cands.append((q, r, side, p))
    cands.sort(key=lambda x: -x[0])
    picks = []
    for q, r, side, p in cands[:2]:
        picks.append(dict(side="BUY" if side == "bull" else "SELL", sym=r["sym"], price=r["price"], pct=r["pct"], volx=r["volx"],
                          dayH=round(r["dayH"], 2), dayL=round(r["dayL"], 2), pdh=round(r["pdh"], 2), pdl=round(r["pdl"], 2),
                          quality=round(q, 1), plan=p,
                          candles=[[x[0], round(x[1], 2), round(x[2], 2), round(x[3], 2), round(x[4], 2)] for x in r["bars"]]))
    srt = sorted(rows, key=lambda r: -r["score"])
    nb = sum(r["pct"] > 0.3 for r in rows); ns = sum(r["pct"] < -0.3 for r in rows)
    longest = max(stocks.values(), key=lambda v: len(v[0]))[0]
    now = dt.datetime.now(IST)
    last_bar = longest[-1][0]
    live = (day == now.date() and dt.time(9, 15) <= now.time() <= dt.time(15, 35))
    return dict(session=str(day), lastBar=last_bar, updated=now.strftime("%d %b %Y, %I:%M:%S %p"), live=live,
                count=len(rows), bullish=nb, bearish=ns, sideways=len(rows) - nb - ns,
                times=[x[0] for x in longest], flow=flow, picks=picks,
                flowTop=dict(bull=[r["sym"] for r in srt[:5]], bear=[r["sym"] for r in srt[::-1][:5]]))

_cache = {"t": 0, "data": None, "err": None}
_lock = threading.Lock()

def get_data(max_age=90):
    with _lock:
        if _cache["data"] is None or time.time() - _cache["t"] > max_age:
            try:
                _cache["data"] = scan(); _cache["t"] = time.time(); _cache["err"] = None
            except Exception as e:
                _cache["err"] = str(e)
                if _cache["data"] is None: raise
        return _cache["data"]
