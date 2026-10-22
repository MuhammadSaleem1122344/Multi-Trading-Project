# Multi-Agent Autonomous Reasoning Stock Trading System — Backend

A modular FastAPI backend for a **paper-trading** stock system. It uses multiple specialized agents:
- Market Data Agent
- Technical Analysis Agent
- Fundamental/News-style Research Agent
- Risk Management Agent
- Portfolio Agent
- Decision/Orchestrator Agent
- Paper Execution Agent

## Safety
This backend is configured for **paper trading only**. It does not place real brokerage orders.

## Run
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open:
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

## Main API
- `GET /api/market/{symbol}`
- `POST /api/analysis/run`
- `POST /api/trading/order`
- `GET /api/portfolio`
- `GET /api/orders`
- `GET /api/trades`
- `GET /api/transactions`
- `GET /api/decisions`
- `POST /api/agents/decision`
- `POST /api/data` (store any JSON value)
- `GET /api/data?limit=100` (show latest submitted data)
- `GET /api/data/{record_id}` (show one submitted record)

The database records portfolios, positions, orders, executed trades (including
realized P&L), cash transactions, and agent decisions. New tables are created
automatically when the app starts. The app also applies additive migrations to
older trading databases, preserving existing orders and positions.

Example:
```json
{
  "symbol": "AAPL",
  "market": {
    "price": 190.0,
    "change_pct": 1.2,
    "history": [182.0, 184.5, 187.0, 190.0],
    "volume": 1200000,
    "pe_ratio": 28.0,
    "eps_growth_pct": 12.0
  },
  "capital": 100000,
  "risk_per_trade": 0.01
}
```

For a live crypto analysis, you can omit `market` and the backend fetches the
latest ticker for the symbol:
```json
{
  "symbol": "SOL/USDT",
  "capital": 100000,
  "risk_per_trade": 0.01
}
```
The `market` field must otherwise be a JSON object containing market data, not
a symbol string. For compatibility, sending `"market": "SOL/USDT"` is treated
as a request to fetch that symbol's live ticker; if both `symbol` and `market`
are strings, they must match. Live ticker lookup currently uses Binance and
therefore supports its crypto trading pairs.

Post arbitrary JSON data to `/api/data`; each record is saved in
`app/data/posted_data.json` and returned with an ID and timestamp. Retrieve the
latest records from `/api/data` or a single record from `/api/data/{record_id}`.
