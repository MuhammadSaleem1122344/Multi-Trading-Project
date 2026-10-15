"""API schemas (Pydantic v2) for the trading firm.

DecisionResponse accepts the dict returned by DecisionAgent.decide() /
TradingFirm.analyze() directly: DecisionResponse(**decision) or
DecisionResponse.model_validate(decision).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,19}$")
T = TypeVar("T")


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Intent(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    CLOSE_LONG = "CLOSE_LONG"
    HOLD_LONG = "HOLD_LONG"
    STAY_FLAT = "STAY_FLAT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class Profile(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


def _clean_symbol(v: str) -> str:
    v = v.strip().upper()
    if not _SYMBOL.match(v):
        raise ValueError("symbol may contain letters, digits, . - / (max 20 chars)")
    return v


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore", use_enum_values=True)


# ------------------------------------------------------------------- requests
class AnalysisRequest(ORMModel):
    symbol: str
    capital: float = Field(100_000, gt=0, le=1e9)
    risk_per_trade: float | None = Field(None, gt=0, le=0.10,
                                         description="Fraction of capital. None = the AI decides")
    profile: Profile = Profile.MODERATE
    asset_type: AssetType | None = None

    _sym = field_validator("symbol")(_clean_symbol)


class OrderRequest(ORMModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0, le=1e9)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(None, gt=0)
    stop_loss: float | None = Field(None, gt=0)
    take_profit: float | None = Field(None, gt=0)

    _sym = field_validator("symbol")(_clean_symbol)

    @model_validator(mode="after")
    def _check(self) -> "OrderRequest":
        if self.order_type == OrderType.LIMIT.value and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        sl, tp = self.stop_loss, self.take_profit
        if sl and tp:
            ok = sl < tp if self.side == Side.BUY.value else sl > tp
            if not ok:
                raise ValueError("stop_loss and take_profit are on the wrong sides for this order")
        return self


class BacktestRequest(ORMModel):
    symbols: list[str] = Field(min_length=1, max_length=10)
    bars: int = Field(300, ge=100, le=2000)
    warmup: int = Field(60, ge=35, le=500)
    capital: float = Field(10_000, gt=0)
    profile: Profile = Profile.MODERATE
    source: Literal["synthetic", "yahoo"] = "synthetic"

    @field_validator("symbols")
    @classmethod
    def _syms(cls, v: list[str]) -> list[str]:
        return [_clean_symbol(s) for s in v]


# ------------------------------------------------------------------ responses
class ComponentScore(ORMModel):
    name: str
    score: float
    weight: float | None = None
    detail: str = ""


class TechnicalResponse(ORMModel):
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(0, ge=0, le=1)
    regime: str = "UNKNOWN"
    reasons: list[str] = []
    indicators: dict[str, Any] = {}


class ResearchResponse(ORMModel):
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(0, ge=0, le=1)
    reasons: list[str] = []
    sources_used: list[str] = []


class RiskResponse(ORMModel):
    risk_level: RiskLevel
    risk_score: float = Field(ge=0, le=100)
    risk_label: str = ""
    veto: bool = False
    risk_per_trade: float | None = None
    risk_per_trade_mode: Literal["AI", "manual"] | None = None
    risk_budget: float | None = None
    suggested_quantity: float | None = None
    stop_distance: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    max_position_pct: float | None = None
    var_95_1day: float | None = None
    components: list[ComponentScore] = []
    warnings: list[str] = []


class DecisionResponse(ORMModel):
    symbol: str
    action: Action
    intent: Intent | None = None
    confidence: float = Field(ge=0, le=1)
    score: float = Field(ge=-1, le=1)
    reasoning: str
    price: float | None = None
    quantity: float | None = None
    position_size_pct: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    flags: list[str] = []
    technical: TechnicalResponse | None = None
    research: ResearchResponse | None = None
    risk: RiskResponse | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _lift_agents(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("agents"), dict):
            data = {**data, **data["agents"]}      # agents.technical -> technical
        return data


class MarketResponse(ORMModel):
    symbol: str
    price: float
    change_pct: float
    volume: float
    rsi: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    macd_hist: float | None = None
    atr_pct: float | None = None
    regime: str | None = None
    asset_type: AssetType | None = None
    history: list[float] | None = None


class OrderResponse(ORMModel):
    id: int
    symbol: str
    side: Side
    order_type: OrderType = OrderType.MARKET
    quantity: float
    price: float
    status: OrderStatus
    stop_loss: float | None = None
    take_profit: float | None = None
    fee: float = 0.0
    pnl: float | None = None
    created_at: datetime


class PositionResponse(ORMModel):
    symbol: str
    qty: float
    entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    stop_loss: float | None = None
    take_profit: float | None = None


class PortfolioResponse(ORMModel):
    equity: float
    cash: float
    return_pct: float
    realized_pnl: float = 0.0
    closed_trades: int = 0
    win_rate_pct: float | None = None
    positions: list[PositionResponse] = []


class BacktestResponse(ORMModel):
    bars_tested: int
    total_return_pct: float
    buy_and_hold_pct: float
    max_drawdown_pct: float
    sharpe: float
    closed_trades: int
    win_rate_pct: float | None = None
    profit_factor: float | None = None
    exit_reasons: dict[str, int] = {}


class Page(ORMModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None