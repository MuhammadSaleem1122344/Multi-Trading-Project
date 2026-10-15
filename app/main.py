from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .models import CashTransaction, Decision, Order, Trade
from .schemas import AnalysisRequest, OrderRequest

from .services.trading import execute_paper_order, portfolio_snapshot, get_or_create_portfolio
from .agents.technical_agent import TechnicalAgent
from .agents.research_agent import ResearchAgent
from .agents.risk_agent import RiskAgent
from .agents.decision_agent import DecisionAgent

from .services.market_data import get_live_watchlist



Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Multi-agent autonomous stock analysis and paper-trading backend."
)

technical_agent = TechnicalAgent()
research_agent = ResearchAgent()
risk_agent = RiskAgent()
decision_agent = DecisionAgent()

@app.get("/health")
def health():
    return {"status": "ok", "paper_trading": settings.paper_trading}

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
        technical = technical_agent.analyze(request.market)
        research = research_agent.analyze(request.market)
        risk = risk_agent.analyze(request.market)
        decision = decision_agent.decide(
            request.symbol, technical, research, risk
        )
        return {
            "symbol": request.symbol,
            "technical": technical,
            "research": research,
            "risk": risk,
            "decision": decision,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agents/decision")
def agent_decision(request: AnalysisRequest):
    try:
        technical = technical_agent.analyze(request.market)
        research = research_agent.analyze(request.market)
        risk = risk_agent.analyze(request.market)
        decision = decision_agent.decide(
            request.symbol, technical, research, risk
        )
        return {
            "symbol": request.symbol,
            "technical": technical,
            "research": research,
            "risk": risk,
            "decision": decision,
        }
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
