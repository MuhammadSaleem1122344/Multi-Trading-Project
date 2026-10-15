"""Paper-trading service (services/paper_trading.py).

Improvements over the first version
- Decimal money (no float rounding drift), fees and slippage
- Fee-inclusive cost basis, realized P&L net of fees
- Rollback on any error, row locks (PostgreSQL) against double-spend races
- Idempotency key (a repeated click or retry never creates a second order)
- Stop-loss / take-profit stored on the position + check_exits()
- Real portfolio snapshot: equity, unrealized P&L, win rate, profit factor, fees
- Trade history, cash ledger and equity curve queries for the UI
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from sqlalchemy.orm import Session

from ..models import CashTransaction,  Order, Portfolio, Position, Trade


class TradingError(ValueError):
    """A business-rule rejection (not enough cash, not enough shares, ...)."""


def _d(x: Any) -> D:
    return x if isinstance(x, D) else D(str(x))


def get_or_create_portfolio(db: Session, initial_cash: float) -> Portfolio:
    portfolio = db.query(Portfolio).first()
    if not portfolio:
        cash = _d(initial_cash)
        portfolio = Portfolio(cash=cash, initial_cash=cash)
        db.add(portfolio)
        db.flush()
        db.add(CashTransaction(portfolio_id=portfolio.id, transaction_type="DEPOSIT",
                               amount=cash, balance_after=cash))
        db.commit()
        db.refresh(portfolio)
    return portfolio


def execute_paper_order(db: Session, symbol: str, side: str, quantity: float, price: float,
                        initial_cash: float = 100_000, *, fee_pct: float = 0.001,
                        slippage_pct: float = 0.0005, stop_loss: float | None = None,
                        take_profit: float | None = None, client_order_id: str | None = None,
                        reason: str = "MANUAL", decision: dict | None = None) -> Order:
    side, symbol = side.upper(), symbol.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise TradingError("side must be BUY or SELL")
    qty, px = _d(quantity), _d(price)
    if qty <= 0 or px <= 0:
        raise TradingError("quantity and price must be greater than zero")

    try:
        if client_order_id:
            dup = db.query(Order).filter(Order.client_order_id == client_order_id).first()
            if dup:
                return dup                                   # same request again: no new order
        pf = get_or_create_portfolio(db, initial_cash)
        pf = db.query(Portfolio).filter(Portfolio.id == pf.id).with_for_update().one()
        pos = (db.query(Position).filter(Position.portfolio_id == pf.id, Position.symbol == symbol)
               .with_for_update().first())

        slip = _d(slippage_pct)
        fill = px * (1 + slip) if side == "BUY" else px * (1 - slip)
        gross = qty * fill
        fee = gross * _d(fee_pct)

        if side == "BUY":
            cost = gross + fee
            if pf.cash < cost:
                raise TradingError(f"Insufficient paper-trading cash: need {cost:.2f}, have {pf.cash:.2f}")
            if pos is None:
                pos = Position(portfolio_id=pf.id, symbol=symbol, quantity=D(0), avg_price=D(0))
                db.add(pos)
            new_qty = pos.quantity + qty
            pos.avg_price = (pos.quantity * pos.avg_price + cost) / new_qty
            pos.quantity = new_qty
            if stop_loss is not None:
                pos.stop_loss = _d(stop_loss)
            if take_profit is not None:
                pos.take_profit = _d(take_profit)
            pf.cash -= cost
            realized, cash_delta = D(0), -cost
        else:
            if pos is None or pos.quantity < qty:
                raise TradingError(f"Insufficient paper position in {symbol}")
            proceeds = gross - fee
            realized = proceeds - pos.avg_price * qty
            pos.quantity -= qty
            if pos.quantity == 0:
                pos.avg_price, pos.stop_loss, pos.take_profit = D(0), None, None
            pf.cash += proceeds
            cash_delta = proceeds

        order = Order(portfolio_id=pf.id, client_order_id=client_order_id, symbol=symbol, side=side,
                      order_type="MARKET", quantity=qty, price=fill, fee=fee, status="FILLED",
                      stop_loss=pos.stop_loss, take_profit=pos.take_profit, reason=reason,
                      decision=decision)
        db.add(order)
        db.flush()
        db.add(Trade(order_id=order.id, symbol=symbol, side=side, quantity=qty, price=fill,
                     gross_amount=gross, fee=fee, realized_pnl=realized))
        db.add(CashTransaction(portfolio_id=pf.id, order_id=order.id, transaction_type=side,
                               amount=cash_delta, balance_after=pf.cash))
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


def check_exits(db: Session, prices: dict[str, float]) -> list[Order]:
    """Sell any position whose stop-loss or take-profit was reached."""
    closed: list[Order] = []
    for pos in db.query(Position).filter(Position.quantity > 0).all():
        if pos.symbol not in prices:
            continue
        px = _d(prices[pos.symbol])
        if pos.stop_loss is not None and px <= pos.stop_loss:
            reason, exit_px = "STOP_LOSS", pos.stop_loss
        elif pos.take_profit is not None and px >= pos.take_profit:
            reason, exit_px = "TAKE_PROFIT", pos.take_profit
        else:
            continue
        closed.append(execute_paper_order(db, pos.symbol, "SELL", pos.quantity, exit_px,
                                          reason=reason))
    return closed


def portfolio_snapshot(db: Session, prices: dict[str, float] | None = None,
                       record: bool = False) -> dict[str, Any]:
    pf = db.query(Portfolio).first()
    if not pf:
        return {"equity": 0.0, "cash": 0.0, "return_pct": 0.0, "realized_pnl": 0.0,
                "closed_trades": 0, "win_rate_pct": None, "positions": []}
    prices = prices or {}
    positions, market_value = [], D(0)
    for p in db.query(Position).filter(Position.quantity > 0).all():
        cur = _d(prices.get(p.symbol, p.avg_price))
        value, cost = p.quantity * cur, p.quantity * p.avg_price
        pnl = value - cost
        market_value += value
        positions.append({
            "symbol": p.symbol, "qty": float(p.quantity), "entry_price": float(p.avg_price),
            "current_price": float(cur), "market_value": float(value),
            "unrealized_pnl": float(pnl),
            "unrealized_pnl_pct": float(pnl / cost * 100) if cost else 0.0,
            "stop_loss": float(p.stop_loss) if p.stop_loss is not None else None,
            "take_profit": float(p.take_profit) if p.take_profit is not None else None})

    equity = pf.cash + market_value
    trades = db.query(Trade).all()
    sells = [t for t in trades if t.side == "SELL"]
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in sells if t.realized_pnl <= 0]
    if record:
        db.add(EquitySnapshot(portfolio_id=pf.id, equity=equity, cash=pf.cash))
        db.commit()
    return {
        "equity": float(equity), "cash": float(pf.cash),
        "return_pct": float((equity / pf.initial_cash - 1) * 100),
        "realized_pnl": float(sum((t.realized_pnl for t in sells), D(0))),
        "closed_trades": len(sells),
        "win_rate_pct": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "profit_factor": float(sum(wins, D(0)) / abs(sum(losses, D(0)))) if losses and sum(losses, D(0)) != 0 else None,
        "fees_paid": float(sum((t.fee or D(0) for t in trades), D(0))),
        "best_trade": float(max(wins)) if wins else None,
        "worst_trade": float(min(losses)) if losses else None,
        "positions": positions,
    }


def _page(db: Session, model, order_col, limit: int, offset: int, **filters):
    q = db.query(model)
    for k, v in filters.items():
        if v is not None:
            q = q.filter(getattr(model, k) == v)
    return q.order_by(order_col.desc()).offset(offset).limit(limit).all(), q.count()


def trade_history(db: Session, limit: int = 50, offset: int = 0, symbol: str | None = None):
    rows, total = _page(db, Trade, Trade.id, limit, offset, symbol=symbol.upper() if symbol else None)
    return [{"id": t.id, "symbol": t.symbol, "side": t.side, "quantity": float(t.quantity),
             "price": float(t.price), "gross_amount": float(t.gross_amount), "fee": float(t.fee or 0),
             "realized_pnl": float(t.realized_pnl) if t.side == "SELL" else None,
             "created_at": t.created_at} for t in rows], total


def cash_ledger(db: Session, limit: int = 50, offset: int = 0):
    rows, total = _page(db, CashTransaction, CashTransaction.id, limit, offset)
    return [{"id": c.id, "transaction_type": c.transaction_type, "amount": float(c.amount),
             "balance_after": float(c.balance_after), "created_at": c.created_at} for c in rows], total


def equity_curve(db: Session, limit: int = 200) -> list[dict]:
    rows = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).limit(limit).all()
    return [{"equity": float(r.equity), "cash": float(r.cash), "created_at": r.created_at}
            for r in reversed(rows)]