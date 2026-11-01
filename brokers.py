"""One order interface for several Indian brokers.

Each broker returns plain dicts so the Trade tab can stay the same:
  funds() -> float available margin
  positions() -> list of {symbol, qty, product, avg, pnl, key}
  orders() -> list of {id, symbol, side, type, qty, price, trigger, status, msg}
  place(sym_row, side, qty, product, otype, price, trigger) -> order id
  cancel(order_id)

product: "INTRADAY" or "CNC"; otype: MARKET | LIMIT | SL | SL-M.
All brokers require a whitelisted static IP for API order placement (SEBI retail algo rules).
"""
import requests
from dhan import Dhan, DhanError


class BrokerError(Exception):
    pass


def _check(r):
    try:
        d = r.json()
    except ValueError:
        raise BrokerError(f"{r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or d.get("status") in ("error", "failed"):
        msg = d.get("message") or d.get("errors") or d.get("data") or d
        raise BrokerError(f"{r.status_code}: {msg}")
    return d.get("data", d)


# ---------------- Dhan ----------------
class DhanBroker:
    name = "Dhan"
    OT = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}

    def __init__(self, client_id, token):
        self.c = Dhan(client_id, token)

    def _w(self, fn, *a):
        try:
            return fn(*a)
        except DhanError as e:
            raise BrokerError(str(e))

    def funds(self):
        f = self._w(self.c.funds)
        return float(f.get("availabelBalance", f.get("availableBalance", 0)) or 0)

    def positions(self):
        out = []
        for p in self._w(self.c.positions) or []:
            out.append({"symbol": p.get("tradingSymbol"), "qty": int(p.get("netQty", 0)), "product": p.get("productType"),
                        "avg": p.get("buyAvg") if p.get("netQty", 0) > 0 else p.get("sellAvg"),
                        "pnl": (p.get("realizedProfit", 0) or 0) + (p.get("unrealizedProfit", 0) or 0),
                        "key": p.get("securityId")})
        return out

    def orders(self):
        return [{"id": o.get("orderId"), "symbol": o.get("tradingSymbol"), "side": o.get("transactionType"),
                 "type": o.get("orderType"), "qty": o.get("quantity"), "price": o.get("price"), "trigger": o.get("triggerPrice"),
                 "status": o.get("orderStatus"), "msg": o.get("omsErrorDescription")} for o in (self._w(self.c.orders) or [])]

    def place(self, row, side, qty, product, otype, price=0, trigger=0):
        r = self._w(self.c.place_order, int(row["security_id"]), side, qty, product, self.OT[otype], price, trigger)
        return r.get("orderId")

    def exit(self, pos):
        r = self._w(self.c.place_order, pos["key"], "SELL" if pos["qty"] > 0 else "BUY", abs(pos["qty"]), pos["product"], "MARKET")
        return r.get("orderId")

    def cancel(self, oid):
        self._w(self.c.cancel_order, oid)


# ---------------- Zerodha Kite Connect ----------------
class ZerodhaBroker:
    name = "Zerodha"
    BASE = "https://api.kite.trade"

    def __init__(self, api_key, access_token):
        self.h = {"X-Kite-Version": "3", "Authorization": f"token {api_key.strip()}:{access_token.strip()}"}

    def _r(self, m, path, data=None):
        return _check(requests.request(m, self.BASE + path, headers=self.h, data=data, timeout=10))

    def funds(self):
        return float(self._r("GET", "/user/margins")["equity"]["available"]["live_balance"])

    def positions(self):
        return [{"symbol": p["tradingsymbol"], "qty": int(p["quantity"]), "product": p["product"], "avg": p["average_price"],
                 "pnl": p["pnl"], "key": (p["tradingsymbol"], p["exchange"])} for p in self._r("GET", "/portfolio/positions")["net"]]

    def orders(self):
        return [{"id": o["order_id"], "symbol": o["tradingsymbol"], "side": o["transaction_type"], "type": o["order_type"],
                 "qty": o["quantity"], "price": o["price"], "trigger": o["trigger_price"],
                 "status": {"OPEN": "PENDING", "TRIGGER PENDING": "PENDING", "COMPLETE": "TRADED"}.get(o["status"], o["status"]),
                 "msg": o.get("status_message")} for o in self._r("GET", "/orders")]

    def _place(self, sym, exch, side, qty, product, otype, price=0, trigger=0):
        d = {"tradingsymbol": sym, "exchange": exch, "transaction_type": side, "order_type": otype, "quantity": int(qty),
             "product": {"INTRADAY": "MIS", "CNC": "CNC"}.get(product, product), "validity": "DAY"}
        if otype in ("LIMIT", "SL"):
            d["price"] = price
        if otype in ("SL", "SL-M"):
            d["trigger_price"] = trigger
        return self._r("POST", "/orders/regular", d)["order_id"]

    def place(self, row, side, qty, product, otype, price=0, trigger=0):
        return self._place(row["symbol"], "NSE", side, qty, product, otype, price, trigger)

    def exit(self, pos):
        sym, exch = pos["key"]
        return self._place(sym, exch, "SELL" if pos["qty"] > 0 else "BUY", abs(pos["qty"]), pos["product"], "MARKET")

    def cancel(self, oid):
        self._r("DELETE", f"/orders/regular/{oid}")


# ---------------- Upstox v2 ----------------
class UpstoxBroker:
    name = "Upstox"
    API = "https://api.upstox.com/v2"
    HFT = "https://api-hft.upstox.com/v2"

    def __init__(self, access_token):
        self.h = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {access_token.strip()}"}

    def _r(self, m, url, json=None, params=None):
        return _check(requests.request(m, url, headers=self.h, json=json, params=params, timeout=10))

    def funds(self):
        return float(self._r("GET", self.API + "/user/get-funds-and-margin", params={"segment": "SEC"})["equity"]["available_margin"])

    def positions(self):
        return [{"symbol": p["tradingsymbol"], "qty": int(p["quantity"]), "product": p["product"], "avg": p.get("average_price"),
                 "pnl": p.get("pnl"), "key": p["instrument_token"]} for p in self._r("GET", self.API + "/portfolio/short-term-positions") or []]

    def orders(self):
        return [{"id": o["order_id"], "symbol": o["tradingsymbol"], "side": o["transaction_type"], "type": o["order_type"],
                 "qty": o["quantity"], "price": o["price"], "trigger": o["trigger_price"],
                 "status": {"open": "PENDING", "trigger pending": "PENDING", "complete": "TRADED"}.get(o["status"], o["status"]),
                 "msg": o.get("status_message")} for o in self._r("GET", self.API + "/order/retrieve-all") or []]

    def _place(self, token, side, qty, product, otype, price=0, trigger=0):
        body = {"quantity": int(qty), "product": "I" if product in ("INTRADAY", "I") else "D", "validity": "DAY",
                "price": price if otype in ("LIMIT", "SL") else 0, "tag": "hark-dash", "instrument_token": token,
                "order_type": otype, "transaction_type": side, "disclosed_quantity": 0,
                "trigger_price": trigger if otype in ("SL", "SL-M") else 0, "is_amo": False}
        return self._r("POST", self.HFT + "/order/place", json=body)["order_id"]

    def place(self, row, side, qty, product, otype, price=0, trigger=0):
        if not isinstance(row.get("isin"), str):
            raise BrokerError("ISIN missing for this stock")
        return self._place(f"NSE_EQ|{row['isin']}", side, qty, product, otype, price, trigger)

    def exit(self, pos):
        return self._place(pos["key"], "SELL" if pos["qty"] > 0 else "BUY", abs(pos["qty"]), pos["product"], "MARKET")

    def cancel(self, oid):
        self._r("DELETE", self.HFT + "/order/cancel", params={"order_id": oid})


BROKERS = {
    "Dhan": {"fields": [("Client ID", False), ("Access token (24 h)", True)], "cls": DhanBroker,
             "help": "web.dhan.co → My Profile → Access DhanHQ APIs → Generate token"},
    "Zerodha": {"fields": [("API key", False), ("Access token (daily)", True)], "cls": ZerodhaBroker,
                "help": "Kite Connect app (developers.kite.trade) → login flow gives a daily access token"},
    "Upstox": {"fields": [("Access token (daily)", True)], "cls": UpstoxBroker,
               "help": "account.upstox.com/developer/apps → create app → generate access token"},
}
