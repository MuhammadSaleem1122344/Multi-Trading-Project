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

The database records portfolios, positions, orders, executed trades (including
realized P&L), cash transactions, and agent decisions. New tables are created
automatically when the app starts; existing order history is left unchanged.

Example:
```json
{
  "symbol": "AAPL",
  "capital": 100000,
  "risk_per_trade": 0.01
}
```
