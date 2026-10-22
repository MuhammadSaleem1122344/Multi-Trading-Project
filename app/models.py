from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text
from .database import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True)
    cash = Column(Numeric(28, 10), default=100000.0)
    initial_cash = Column(Numeric(28, 10), nullable=False, default=100000.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    symbol = Column(String(20), index=True)
    quantity = Column(Numeric(28, 10), default=0)
    avg_price = Column(Numeric(28, 10), default=0)
    stop_loss = Column(Numeric(28, 10), nullable=True)
    take_profit = Column(Numeric(28, 10), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    client_order_id = Column(String(100), nullable=True, unique=True)
    symbol = Column(String(20), index=True)
    side = Column(String(10))
    quantity = Column(Numeric(28, 10))
    price = Column(Numeric(28, 10))
    order_type = Column(String(10), nullable=False, default="MARKET")
    fee = Column(Numeric(28, 10), nullable=False, default=0)
    status = Column(String(20), default="FILLED")
    stop_loss = Column(Numeric(28, 10), nullable=True)
    take_profit = Column(Numeric(28, 10), nullable=True)
    reason = Column(String(50), nullable=False, default="MANUAL")
    decision = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    symbol = Column(String(20), index=True, nullable=False)
    side = Column(String(10), nullable=False)
    quantity = Column(Numeric(28, 10), nullable=False)
    price = Column(Numeric(28, 10), nullable=False)
    gross_amount = Column(Numeric(28, 10), nullable=False)
    fee = Column(Numeric(28, 10), nullable=False, default=0)
    realized_pnl = Column(Numeric(28, 10), default=0, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Numeric(28, 10), nullable=False)
    balance_after = Column(Numeric(28, 10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), index=True)
    action = Column(String(10))
    confidence = Column(Float)
    score = Column(Float)
    reasoning = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    equity = Column(Numeric(28, 10), nullable=False)
    cash = Column(Numeric(28, 10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
