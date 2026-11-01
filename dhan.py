"""Small DhanHQ v2 client: quotes, option chain, orders, positions, funds.

Docs: https://dhanhq.co/docs/v2/
- Data APIs (quote, option chain) need the Dhan Data API subscription.
- Order placement needs your server's static IP whitelisted in Dhan (SEBI rule).
"""
import time
import requests

BASE = "https://api.dhan.co/v2"


class DhanError(Exception):
    pass


class Dhan:
    def __init__(self, client_id: str, access_token: str, timeout: int = 10):
        self.client_id = str(client_id).strip()
        self.token = str(access_token).strip()
        self.timeout = timeout
        self._last_quote = 0.0

    # ---------- low level ----------
    def _headers(self):
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.token,
            "client-id": self.client_id,
        }

    def _req(self, method, path, json=None):
        r = requests.request(method, BASE + path, headers=self._headers(), json=json, timeout=self.timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text}
        if r.status_code >= 400:
            msg = data.get("errorMessage") or data.get("remarks") or data.get("raw")
            if not msg and isinstance(data.get("data"), dict):
                msg = "; ".join(str(v) for v in data["data"].values())
            msg = msg or str(data)
            raise DhanError(f"Dhan {r.status_code}: {msg}")
        return data

    # ---------- data ----------
    def quotes(self, security_ids, segment="NSE_EQ"):
        """Full quote (LTP, OHLC, VWAP, volume, total buy/sell qty, depth) for up to 1000 ids per call."""
        out = {}
        ids = [int(x) for x in security_ids]
        for i in range(0, len(ids), 1000):
            wait = 1.05 - (time.time() - self._last_quote)  # 1 request per second
            if wait > 0:
                time.sleep(wait)
            data = self._req("POST", "/marketfeed/quote", {segment: ids[i:i + 1000]})
            self._last_quote = time.time()
            out.update(data.get("data", {}).get(segment, {}))
        return out

    def expiry_list(self, underlying_id, seg="IDX_I"):
        return self._req("POST", "/optionchain/expirylist", {"UnderlyingScrip": int(underlying_id), "UnderlyingSeg": seg}).get("data", [])

    def option_chain(self, underlying_id, expiry, seg="IDX_I"):
        return self._req("POST", "/optionchain", {"UnderlyingScrip": int(underlying_id), "UnderlyingSeg": seg, "Expiry": expiry}).get("data", {})

    # ---------- account ----------
    def funds(self):
        return self._req("GET", "/fundlimit")

    def positions(self):
        return self._req("GET", "/positions")

    def orders(self):
        return self._req("GET", "/orders")

    def trades(self):
        return self._req("GET", "/trades")

    # ---------- trading ----------
    def place_order(self, security_id, side, qty, product="INTRADAY", order_type="MARKET",
                    price=0.0, trigger=0.0, segment="NSE_EQ", tag="hark-dash"):
        body = {
            "dhanClientId": self.client_id,
            "correlationId": f"{tag}-{int(time.time())}"[:30],
            "transactionType": side,
            "exchangeSegment": segment,
            "productType": product,
            "orderType": order_type,
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(qty),
            "disclosedQuantity": "",
            "price": float(price) if order_type in ("LIMIT", "STOP_LOSS") else "",
            "triggerPrice": float(trigger) if order_type in ("STOP_LOSS", "STOP_LOSS_MARKET") else "",
            "afterMarketOrder": False,
        }
        return self._req("POST", "/orders", body)

    def cancel_order(self, order_id):
        return self._req("DELETE", f"/orders/{order_id}")
