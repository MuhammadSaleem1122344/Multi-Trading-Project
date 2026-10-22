from typing import Any

from fastapi import FastAPI, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db, migrate_legacy_schema
from .models import CashTransaction, Decision, Order, Trade
from .schemas import AnalysisRequest, OrderRequest

from .services.trading import execute_paper_order, portfolio_snapshot, get_or_create_portfolio
from .agents.technical_agent import TechnicalAgent
from .agents.research_agent import ResearchAgent
from .agents.risk_agent import RiskAgent
from .agents.decision_agent import DecisionAgent

from .services.market_data import get_crypto_prices, get_live_watchlist, get_market_data
from .services.json_store import data_store



Base.metadata.create_all(bind=engine)
migrate_legacy_schema()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Multi-agent autonomous stock analysis and paper-trading backend."
)

technical_agent = TechnicalAgent()
research_agent = ResearchAgent()
risk_agent = RiskAgent()
decision_agent = DecisionAgent()


def _run_analysis(request: AnalysisRequest):
    supplied_market = request.market or {}
    latest_market = get_market_data(request.symbol) if not supplied_market else {}
    market = {**latest_market, **supplied_market, "symbol": request.symbol}
    profile = getattr(request.profile, "value", request.profile)
    asset_type = getattr(request.asset_type, "value", request.asset_type)
    if asset_type is None:
        asset_type = market.get("asset_type")
    if asset_type is None and "/" in request.symbol:
        asset_type = "crypto"
    technical = technical_agent.analyze(market)
    research = research_agent.analyze(market)
    risk = risk_agent.assess(
        market,
        capital=request.capital,
        risk_per_trade=request.risk_per_trade,
        asset_type=asset_type,
        profile=profile,
    )
    decision = decision_agent.decide(request.symbol, technical, research, risk)
    return {
        "symbol": request.symbol,
        "technical": technical,
        "research": research,
        "risk": risk,
        "decision": decision,
    }

@app.get("/health")
def health():
    return {"status": "ok", "paper_trading": settings.paper_trading}

@app.post("/api/data", status_code=201)
def post_data(payload: Any = Body(...)):
    try:
        return data_store.add(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.get("/api/data")
def list_data(limit: int = Query(100, ge=1, le=500)):
    try:
        records = data_store.list(limit)
        return {"count": len(records), "items": records}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.get("/api/data/{record_id}")
def get_data(record_id: str):
    try:
        record = data_store.get(record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Data record not found")
    return record

@app.get("/api/market/watchlist")
def market_watchlist():
    try:
        return get_live_watchlist()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Market data provider error: {e}",
        )

@app.get("/api/crypto/prices")
def crypto_prices():
    try:
        return get_crypto_prices()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Crypto price provider error: {e}")
@app.post("/api/analysis/decision")
def analyze_decision(request: AnalysisRequest):
    try:
        return _run_analysis(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agents/decision")
def agent_decision(request: AnalysisRequest):
    try:
        return _run_analysis(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/trading/order")
def place_order(request: OrderRequest, db: Session = Depends(get_db)):
    try:
        if not settings.paper_trading:
            raise ValueError("Live trading is disabled by design in this backend.")

        market_data = get_market_data(request.symbol)
        order = execute_paper_order(
            db,
            request.symbol,
            request.side,
            request.quantity,
            market_data["price"],
            settings.initial_cash,
        )
        return {
            "id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "status": order.status,
            "created_at": order.created_at,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



@app.get("/api/orders")
def orders(db: Session = Depends(get_db)):
    rows = db.query(Order).order_by(Order.created_at.desc()).limit(100).all()
    return [
        {
            "id": x.id,
            "symbol": x.symbol,
            "side": x.side,
            "quantity": x.quantity,
            "price": x.price,
            "status": x.status,
            "created_at": x.created_at,
        }
        for x in rows
    ]

@app.get("/api/trades")
def trades(db: Session = Depends(get_db)):
    rows = db.query(Trade).order_by(Trade.executed_at.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "order_id": row.order_id,
            "symbol": row.symbol,
            "side": row.side,
            "quantity": row.quantity,
            "price": row.price,
            "gross_amount": row.gross_amount,
            "realized_pnl": row.realized_pnl,
            "executed_at": row.executed_at,
        }
        for row in rows
    ]

@app.get("/api/transactions")
def transactions(db: Session = Depends(get_db)):
    rows = db.query(CashTransaction).order_by(CashTransaction.created_at.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "portfolio_id": row.portfolio_id,
            "order_id": row.order_id,
            "transaction_type": row.transaction_type,
            "amount": row.amount,
            "balance_after": row.balance_after,
            "created_at": row.created_at,
        }
        for row in rows
    ]

@app.get("/api/decisions")
def decisions(db: Session = Depends(get_db)):
    rows = db.query(Decision).order_by(Decision.created_at.desc()).limit(100).all()
    return [
        {
            "id": x.id,
            "symbol": x.symbol,
            "action": x.action,
            "confidence": x.confidence,
            "score": x.score,
            "reasoning": x.reasoning,
            "created_at": x.created_at,
        }
        for x in rows
    ]

@app.post("/api/portfolio/initialize")
def initialize_portfolio(db: Session = Depends(get_db)):
    p = get_or_create_portfolio(db, settings.initial_cash)
    return {"id": p.id, "cash": p.cash}
