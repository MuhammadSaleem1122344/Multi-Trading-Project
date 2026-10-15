from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from .database import Base

class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(Integer, primary_key=True)
    cash = Column(Float, default=100000.0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), unique=True, index=True)
    quantity = Column(Float, default=0)
    avg_price = Column(Float, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), index=True)
    side = Column(String(10))
    quantity = Column(Float)
    price = Column(Float)
    status = Column(String(20), default="FILLED")
    created_at = Column(DateTime, default=datetime.utcnow)

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    symbol = Column(String(20), index=True, nullable=False)
    side = Column(String(10), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    gross_amount = Column(Float, nullable=False)
    realized_pnl = Column(Float, default=0, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
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
