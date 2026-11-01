"""Intraday Live Dashboard (NSE) — scanner, buying/selling pressure, option chain, Dhan trading.

Run:  streamlit run app.py
Secrets (.streamlit/secrets.toml or the host's secrets box):
    APP_PASSWORD = "..."          # required, protects the app
    DHAN_CLIENT_ID = "..."        # optional, can also be typed in the app
    DHAN_ACCESS_TOKEN = "..."     # optional, 24-hour token from web.dhan.co
"""
from datetime import datetime
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import freedata
import signals
from dhan import Dhan, DhanError
from brokers import BROKERS

st.set_page_config(page_title="Intraday Live", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
HERE = os.path.dirname(__file__)
IST = signals.IST
INDICES = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442}


# ---------------- password gate ----------------
def secret(k, default=""):
    try:
        return st.secrets.get(k, default)
    except Exception:
        return os.environ.get(k, default)


def gate():
    pw = secret("APP_PASSWORD")
    if not pw:
        st.error("Set APP_PASSWORD in the app secrets first.")
        st.stop()
    if st.session_state.get("ok"):
        return
    st.title("📈 Intraday Live")
    p = st.text_input("Password", type="password")
    if st.button("Enter", type="primary", width="stretch"):
        if p == pw:
            st.session_state.ok = True
            st.rerun()
        st.error("Wrong password")
    st.stop()


gate()


@st.cache_data(ttl=24 * 3600)
def universe():
    return pd.read_csv(os.path.join(HERE, "data", "universe.csv"))


@st.cache_data(ttl=6 * 3600, show_spinner="Loading 20-day average volumes…")
def avg_vol(symbols):
    return freedata.avg_volume(list(symbols))


@st.cache_data(ttl=55, show_spinner="Fetching prices…")
def free_snapshot(symbols):
    return freedata.snapshot(list(symbols))


def dhan_client():
    cid = st.session_state.get("cid") or secret("DHAN_CLIENT_ID")
    tok = st.session_state.get("tok") or secret("DHAN_ACCESS_TOKEN")
    return Dhan(cid, tok) if cid and tok else None


def dhan_snapshot(dh, u):
    q = dh.quotes(u.security_id.tolist())
    rows = []
    for _, r in u.iterrows():
        x = q.get(str(r.security_id))
        if not x:
            continue
        o = x.get("ohlc", {})
        bq, sq = x.get("buy_quantity", 0) or 0, x.get("sell_quantity", 0) or 0
        rows.append({
            "symbol": r.symbol, "ltp": x.get("last_price"),
            "prev_close": (x.get("last_price") or 0) - (x.get("net_change") or 0),
            "open": o.get("open"), "high": o.get("high"), "low": o.get("low"),
            "volume": x.get("volume", 0), "vwap": x.get("average_price") or np.nan,
            "buyer_pct": bq / (bq + sq) * 100 if bq + sq else np.nan,
            "buy_qty": bq, "sell_qty": sq,
            "upper_ckt": x.get("upper_circuit_limit"), "lower_ckt": x.get("lower_circuit_limit"),
            "last_bar": x.get("last_trade_time", ""),
        })
    return pd.DataFrame(rows)


# ---------------- sidebar: settings ----------------
with st.sidebar:
    st.header("⚙️ Settings")
    mode = st.radio("Data", ["Free (Yahoo, ~delayed)", "Dhan Live"], index=1 if dhan_client() else 0)
    with st.expander("🔑 Dhan connect", expanded=not dhan_client()):
        st.text_input("Client ID", key="cid", value=secret("DHAN_CLIENT_ID"))
        st.text_input("Access token (24 h)", key="tok", type="password", value=secret("DHAN_ACCESS_TOKEN"))
        st.caption("web.dhan.co → My Profile → Access DhanHQ APIs → Generate token. Stays only in this session.")
    uni_choice = st.selectbox("Stocks", ["NIFTY 200", "NIFTY 500"], index=0)
    min_price = st.number_input("Min price ₹", 0, 100000, 50)
    min_turn = st.number_input("Min turnover ₹ Cr", 0.0, 10000.0, 5.0)
    refresh = st.select_slider("Auto refresh (sec)", [0, 10, 15, 30, 60, 120], value=30)
    st.subheader("🛡️ Risk rules")
    st.session_state.max_loss = st.number_input("Max loss per trade ₹", 100, 1000000, 1000)
    st.session_state.max_orders = st.number_input("Max orders per day", 1, 200, 10)
    st.session_state.confirm = st.toggle("Ask confirm before every order", True)
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

u_all = universe()
u = u_all[u_all.nifty200] if uni_choice == "NIFTY 200" else u_all
if mode.startswith("Free") and len(u) > 250:
    st.sidebar.warning("Free mode is slow on 500 stocks; first load takes ~1 min.")


def load_board():
    frac = signals.session_fraction()
    if mode == "Dhan Live":
        dh = dhan_client()
        if not dh:
            st.warning("Dhan connect karein (sidebar ⚙️) ya Free mode chunein.")
            st.stop()
        try:
            snap = dhan_snapshot(dh, u)
        except DhanError as e:
            st.error(f"{e}. Token naya banayein ya Data API subscription check karein. Abhi Free mode data dikh raha hai.")
            snap = free_snapshot(tuple(u.symbol))
    else:
        snap = free_snapshot(tuple(u.symbol))
    if snap.empty:
        st.error("Koi data nahi mila. Thodi der baad refresh karein.")
        st.stop()
    av = avg_vol(tuple(u.symbol))
    d = signals.enrich(snap, av, frac).merge(u[["symbol", "name", "sector", "security_id"]], on="symbol", how="left")
    d = d[(d.ltp >= min_price) & (d.turnover_cr >= min_turn)]
    return d


COLS = {
    "symbol": st.column_config.TextColumn("Stock"),
    "ltp": st.column_config.NumberColumn("LTP ₹", format="%.2f"),
    "chg_pct": st.column_config.NumberColumn("Chg %", format="%+.2f"),
    "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
    "buyer_pct": st.column_config.ProgressColumn("Buyers %", min_value=0, max_value=100, format="%d%%"),
    "rvol": st.column_config.NumberColumn("RVol ×", format="%.1f"),
    "vs_vwap_pct": st.column_config.NumberColumn("vs VWAP %", format="%+.2f"),
    "range_pos": st.column_config.ProgressColumn("Day range", min_value=0, max_value=100, format="%d"),
    "turnover_cr": st.column_config.NumberColumn("₹ Cr", format="%.0f"),
    "gap_pct": st.column_config.NumberColumn("Gap %", format="%+.2f"),
}
SHOW = ["symbol", "signal", "ltp", "chg_pct", "score", "buyer_pct", "rvol", "vs_vwap_pct", "range_pos", "turnover_cr"]


def table(df, n=15, cols=SHOW, key=None):
    st.dataframe(df[cols].head(n), column_config=COLS, hide_index=True, width="stretch", key=key)


# ---------------- header ----------------
now = datetime.now(IST)
st.markdown(f"### 📈 Intraday Live  <small>{now:%d %b %Y, %H:%M:%S} IST · "
            f"{'🟢 Market open' if signals.market_open(now) else '⚫ Market closed (last session)'} · {mode}</small>",
            unsafe_allow_html=True)

tab_scan, tab_chart, tab_oc, tab_trade, tab_calc = st.tabs(["🔥 Scanner", "📊 Chart", "🧮 Option chain", "💼 Trade", "🧠 Plan"])


# ---------------- scanner (auto refresh) ----------------
@st.fragment(run_every=refresh if refresh else None)
def scanner():
    d = load_board()
    st.session_state.board = d
    up, dn, fl = signals.breadth(d)
    c = st.columns(4)
    c[0].metric("Advance / Decline", f"{up} / {dn}", f"{up - dn:+d}")
    c[1].metric("🚀 Strong buying", int((d.signal == "🚀 Strong buying").sum()))
    c[2].metric("🩸 Heavy selling", int((d.signal == "🩸 Heavy selling").sum()))
    c[3].metric("Data time", str(d.last_bar.dropna().max()) if "last_bar" in d else "-")

    # what is new since last refresh
    prev = st.session_state.get("prev_strong", set())
    strong = set(d[d.signal == "🚀 Strong buying"].symbol)
    new = strong - prev
    st.session_state.prev_strong = strong
    if new and prev:
        st.toast("🆕 Naye strong buying: " + ", ".join(sorted(new)), icon="🚀")

    s1, s2, s3, s4, s5, s6 = st.tabs(["🚀 Bhaagne wale", "🩸 Girne wale", "🟢 Sabse zyada buying", "🔴 Sabse zyada selling", "🌋 Volume shock", "🗺️ Sectors"])
    with s1:
        st.caption("Score high + volume high + VWAP ke upar + day high ke paas. Rule match, guarantee nahi.")
        table(d[d.chg_pct > 0].sort_values(["score", "rvol"], ascending=False), key="t1")
    with s2:
        st.caption("Score low + volume high + VWAP ke neeche + day low ke paas.")
        table(d[d.chg_pct < 0].sort_values(["score", "rvol"], ascending=[True, False]), key="t2")
    with s3:
        st.caption("Dhan mode: pending buy qty ka share. Free mode: green candles ka volume share.")
        cols = SHOW + (["buy_qty", "sell_qty"] if "buy_qty" in d else [])
        table(d.sort_values("buyer_pct", ascending=False), cols=cols, key="t3")
    with s4:
        cols = SHOW + (["buy_qty", "sell_qty"] if "buy_qty" in d else [])
        table(d.sort_values("buyer_pct", ascending=True), cols=cols, key="t4")
    with s5:
        st.caption("Aaj ka volume, 20 din ke average se kitna guna (time ke hisaab se adjusted).")
        table(d.sort_values("rvol", ascending=False), key="t5")
    with s6:
        sv = signals.sector_view(d)
        fig = go.Figure(go.Bar(x=sv.avg_chg, y=sv.sector, orientation="h",
                               marker_color=np.where(sv.avg_chg >= 0, "#16a34a", "#dc2626")))
        fig.update_layout(height=520, margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(autorange="reversed"), xaxis_title="Avg chg %")
        st.plotly_chart(fig, width="stretch")

    with st.expander("🔎 All stocks (search / sort)"):
        q = st.text_input("Search", key="q").upper()
        dd = d[d.symbol.str.contains(q) | d.name.str.upper().str.contains(q)] if q else d
        table(dd.sort_values("score", ascending=False), n=600, cols=SHOW + ["gap_pct", "sector"], key="tall")


with tab_scan:
    scanner()


# ---------------- chart ----------------
with tab_chart:
    syms = sorted(universe().symbol)
    board = st.session_state.get("board")
    default = board.sort_values("score", ascending=False).symbol.iloc[0] if board is not None and len(board) else "RELIANCE"
    sym = st.selectbox("Stock", syms, index=syms.index(default) if default in syms else 0)
    tf = st.radio("Timeframe", ["1m", "5m", "15m"], index=1, horizontal=True)
    c = freedata.candles(sym, interval=tf, period="5d" if tf != "1m" else "1d")
    if c.empty:
        st.info("Chart data nahi mila.")
    else:
        fig = go.Figure()
        fig.add_candlestick(x=c.index, open=c.Open, high=c.High, low=c.Low, close=c.Close, name=sym)
        fig.add_scatter(x=c.index, y=c.VWAP, name="VWAP", line=dict(color="#f59e0b", width=1.5))
        orb = c.between_time("09:15", "09:29")
        if len(orb):
            fig.add_hline(y=orb.High.max(), line_dash="dot", line_color="#16a34a", annotation_text="ORB high")
            fig.add_hline(y=orb.Low.min(), line_dash="dot", line_color="#dc2626", annotation_text="ORB low")
        fig.update_layout(height=480, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Yahoo candles, last bar {c.index[-1]:%d %b %H:%M} IST. Dotted = first 15-min range.")
    if board is not None and sym in set(board.symbol):
        r = board[board.symbol == sym].iloc[0]
        m = st.columns(4)
        m[0].metric("LTP", f"₹{r.ltp:,.2f}", f"{r.chg_pct:+.2f}%")
        m[1].metric("Score", int(r.score), r.signal)
        m[2].metric("Buyers", f"{r.buyer_pct:.0f}%" if pd.notna(r.buyer_pct) else "-")
        m[3].metric("RVol", f"{r.rvol:.1f}×" if pd.notna(r.rvol) else "-")
    if mode == "Dhan Live" and dhan_client():
        sid = int(universe().set_index("symbol").security_id[sym])
        try:
            x = dhan_client().quotes([sid]).get(str(sid), {})
            dep = x.get("depth", {})
            dd = pd.DataFrame({"Bid qty": [b["quantity"] for b in dep.get("buy", [])], "Bid": [b["price"] for b in dep.get("buy", [])],
                               "Ask": [a["price"] for a in dep.get("sell", [])], "Ask qty": [a["quantity"] for a in dep.get("sell", [])]})
            st.subheader("Market depth (top 5)")
            st.dataframe(dd, hide_index=True, width="stretch")
        except DhanError as e:
            st.warning(str(e))


# ---------------- option chain ----------------
with tab_oc:
    dh = dhan_client()
    if not dh:
        st.info("Option chain ke liye Dhan connect karein (Data API subscription chahiye).")
    else:
        idx = st.radio("Index", list(INDICES), horizontal=True)
        try:
            exps = dh.expiry_list(INDICES[idx])
            exp = st.selectbox("Expiry", exps[:8])

            @st.fragment(run_every=max(refresh, 15) if refresh else None)
            def oc_view():
                view, stt = signals.option_chain_table(dh.option_chain(INDICES[idx], exp))
                if view is None:
                    st.info("Chain khaali hai.")
                    return
                m = st.columns(5)
                m[0].metric("Spot", f"{stt['spot']:,.2f}")
                m[1].metric("PCR", stt["pcr"], "Bullish" if stt["pcr"] > 1.2 else ("Bearish" if stt["pcr"] < 0.8 else "Neutral"))
                m[2].metric("Support (max PE OI)", f"{stt['support']:,.0f}")
                m[3].metric("Resistance (max CE OI)", f"{stt['resistance']:,.0f}")
                m[4].metric("Max pain", f"{stt['max_pain']:,.0f}")
                show = ["CE chg OI", "CE OI", "CE IV", "CE LTP", "strike", "ATM", "PE LTP", "PE IV", "PE OI", "PE chg OI"]
                st.dataframe(view[show], hide_index=True, width="stretch",
                             column_config={"CE OI": st.column_config.ProgressColumn("CE OI", min_value=0, max_value=int(view["CE OI"].max() or 1), format="%d"),
                                            "PE OI": st.column_config.ProgressColumn("PE OI", min_value=0, max_value=int(view["PE OI"].max() or 1), format="%d")})
                st.caption(f"Updated {datetime.now(IST):%H:%M:%S} IST · Dhan option chain")
            oc_view()
        except DhanError as e:
            st.error(str(e))


# ---------------- trade ----------------
with tab_trade:
    bname = st.radio("Broker", list(BROKERS), horizontal=True, key="bname")
    spec = BROKERS[bname]
    with st.expander(f"🔑 {bname} connect", expanded=not st.session_state.get(f"b_{bname}")):
        vals = []
        for i, (label, pw) in enumerate(spec["fields"]):
            default = secret("DHAN_CLIENT_ID") if bname == "Dhan" and i == 0 else (secret("DHAN_ACCESS_TOKEN") if bname == "Dhan" else "")
            vals.append(st.text_input(label, value=default, type="password" if pw else "default", key=f"f_{bname}_{i}"))
        st.caption(spec["help"] + ". Sirf is session mein rehta hai.")
    br = spec["cls"](*vals) if all(vals) else None
    st.session_state[f"b_{bname}"] = bool(br)
    if not br:
        st.info(f"{bname} ki details daalein, phir yahan se order lagayein.")
    else:
        st.caption("Orders aapke broker account mein jaate hain. SEBI rule: server ka static IP broker mein whitelisted hona chahiye.")
        try:
            st.metric("Available margin", f"₹{br.funds():,.2f}")
        except Exception as e:
            st.warning(str(e))

        u2 = universe().set_index("symbol")
        board = st.session_state.get("board")
        syms = sorted(u2.index)
        best = board.sort_values("score", ascending=False).symbol.iloc[0] if board is not None and len(board) else syms[0]
        c1, c2 = st.columns(2)
        sym = c1.selectbox("Stock", syms, key="tsym", index=syms.index(best) if best in syms else 0)
        side = c2.radio("Side", ["BUY", "SELL"], horizontal=True)
        ltp = float(board.set_index("symbol").ltp.get(sym, 0)) if board is not None else 0.0
        c3, c4, c5 = st.columns(3)
        product = c3.selectbox("Product", ["INTRADAY", "CNC"])
        otype = c4.selectbox("Order type", ["MARKET", "LIMIT", "SL", "SL-M"])
        qty = c5.number_input("Qty", 1, 100000, 1)
        c6, c7, c8 = st.columns(3)
        price = c6.number_input("Price ₹", 0.0, 1e6, round(ltp, 2), step=0.05)
        trig = c7.number_input("Trigger ₹", 0.0, 1e6, 0.0, step=0.05)
        sl = c8.number_input("My stop-loss ₹", 0.0, 1e6, round(ltp * (0.99 if side == "BUY" else 1.01), 2), step=0.05)
        add_sl = st.checkbox("Entry ke baad SL-M order bhi lagao (my stop-loss pe)", True)

        entry = price if otype in ("LIMIT", "SL") else ltp
        risk = abs(entry - sl) * qty
        st.write(f"Order value ≈ **₹{entry * qty:,.0f}** · Risk if SL hits ≈ **₹{risk:,.0f}** (limit ₹{st.session_state.max_loss:,})")
        sent = st.session_state.setdefault("sent", 0)
        blocked = risk > st.session_state.max_loss or sent >= st.session_state.max_orders
        if risk > st.session_state.max_loss:
            st.error("🛡️ Risk rule: loss limit se zyada. Qty kam karein ya SL paas rakhein.")
        if sent >= st.session_state.max_orders:
            st.error("🛡️ Aaj ke max orders ho gaye.")
        ok = st.checkbox(f"Haan, {side} {qty} {sym} @ {otype} bhejna hai") if st.session_state.confirm else True
        if st.button(f"{'🟢' if side == 'BUY' else '🔴'} Place {side} on {bname}", type="primary", width="stretch", disabled=blocked or not ok):
            row = u2.loc[sym].to_dict() | {"symbol": sym}
            try:
                oid = br.place(row, side, qty, product, otype, price, trig)
                st.session_state.sent += 1
                st.success(f"Order {oid} bheja gaya")
                if add_sl and sl > 0:
                    oid2 = br.place(row, "SELL" if side == "BUY" else "BUY", qty, product, "SL-M", 0, sl)
                    st.success(f"SL order {oid2} @ trigger ₹{sl}")
            except Exception as e:
                st.error(str(e))

        st.divider()
        st.subheader("Positions")
        try:
            pos = br.positions()
            if not pos:
                st.caption("Koi open position nahi.")
            else:
                pdf = pd.DataFrame(pos)
                st.dataframe(pdf.drop(columns=["key"]), hide_index=True, width="stretch")
                st.metric("Day P&L", f"₹{pd.to_numeric(pdf.pnl, errors='coerce').sum():,.2f}")
                for i, p in enumerate(pos):
                    if p["qty"] and st.button(f"❌ Exit {p['symbol']} ({p['qty']})", key=f"x{i}{p['symbol']}"):
                        try:
                            st.success(f"Exit order {br.exit(p)}")
                        except Exception as e:
                            st.error(str(e))
        except Exception as e:
            st.warning(str(e))

        st.subheader("Today's orders")
        try:
            od = br.orders()
            if not od:
                st.caption("Aaj koi order nahi.")
            else:
                st.dataframe(pd.DataFrame(od), hide_index=True, width="stretch")
                for o in od:
                    if o["status"] in ("PENDING", "TRANSIT") and st.button(f"Cancel {o['id']} {o['symbol']}", key=f"c{o['id']}"):
                        try:
                            br.cancel(o["id"])
                            st.success("Cancelled")
                        except Exception as e:
                            st.error(str(e))
        except Exception as e:
            st.warning(str(e))


# ---------------- plan / position size ----------------
with tab_calc:
    st.subheader("🧠 Trade plan calculator")
    c = st.columns(3)
    e = c[0].number_input("Entry ₹", 0.0, 1e6, 100.0)
    s = c[1].number_input("Stop-loss ₹", 0.0, 1e6, 98.0)
    rr = c[2].number_input("Reward : Risk", 0.5, 10.0, 2.0, 0.5)
    cap = st.number_input("Capital ₹", 0, 100000000, 50000)
    lev = st.number_input("Intraday leverage ×", 1.0, 5.0, 5.0, 0.5)
    per = abs(e - s)
    if per > 0:
        q = int(min(st.session_state.max_loss // per, (cap * lev) // max(e, 0.01)))
        tgt = e + rr * per if e > s else e - rr * per
        st.success(f"Qty **{q}** · Target **₹{tgt:,.2f}** · Max loss **₹{q * per:,.0f}** · Profit at target **₹{q * per * rr:,.0f}** · Margin ≈ ₹{q * e / lev:,.0f}")
    st.caption("Education aur tooling. Koi bhi score ya signal guarantee nahi hai; apna stop-loss hamesha lagayein.")
