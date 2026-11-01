# Intraday Live (NSE)

Phone-friendly Streamlit dashboard: live scanner (bhaagne wale / girne wale), buying vs selling pressure, volume shock, sector heatmap, 1m/5m/15m charts with VWAP and opening range, NIFTY/BANKNIFTY option chain (PCR, support, resistance, max pain), and Dhan order ticket with risk rules, SL-M auto order, positions, exit and cancel.

## Modes
- **Free**: Yahoo Finance data, a few minutes delayed. No account needed.
- **Dhan Live**: DhanHQ Data API (₹499 + tax / month). Paste Client ID + 24-hour access token in ⚙️ Settings.

## Orders
Dhan (SEBI rule) only accepts API orders from a whitelisted static IP. Host the app on a server with a static IP and add that IP in web.dhan.co → Access DhanHQ APIs → Static IP.

## Secrets
```
APP_PASSWORD = "choose-one"
DHAN_CLIENT_ID = ""      # optional
DHAN_ACCESS_TOKEN = ""   # optional
```
Signals are rules, not advice. Always use a stop-loss.
