"""Advanced Risk Management Agent.

The agent decides *by itself* how risky an asset (stock or coin) is right now,
on a 0-100 scale, from many signals: volatility, drawdown, RSI extremes,
sudden-move shock, price stretch, liquidity, beta and upcoming events.
Then it sizes the trade (risk per trade, quantity, stop-loss, take-profit, VaR).

Backward compatible with the old output: risk_level, risk_budget,
suggested_quantity, stop_distance. DecisionAgent also understands
`max_position_pct` and `veto`.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- indicators
def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(d, 0) for d in deltas[:period]) / period
    avg_loss = sum(max(-d, 0) for d in deltas[:period]) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def compute_atr(closes: list[float], highs: list[float] | None = None,
                lows: list[float] | None = None, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    use_hl = bool(highs and lows and len(highs) == len(lows) == len(closes))
    trs = []
    for i in range(1, len(closes)):
        if use_hl:
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                           abs(lows[i] - closes[i - 1])))
        else:                                   # close-only approximation
            trs.append(abs(closes[i] - closes[i - 1]))
    return sum(trs[-period:]) / period


def daily_volatility(closes: list[float], window: int = 30) -> float | None:
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))][-window:]
    return statistics.stdev(rets) if len(rets) >= 5 else None


# --------------------------------------------------------------------- config
def _scale(x: float, lo: float, hi: float) -> float:
    """Linear map: lo -> 0, hi -> 100 (clamped)."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100.0))


@dataclass(frozen=True)
class RiskConfig:
    weights: dict[str, float] = field(default_factory=lambda: {
        "volatility": 0.30, "drawdown": 0.18, "rsi": 0.15, "shock": 0.12,
        "stretch": 0.10, "liquidity": 0.10, "beta": 0.05,
    })
    low_max: float = 35.0          # score < 35  -> LOW
    high_min: float = 65.0         # score >= 65 -> HIGH
    veto_score: float = 90.0       # score >= 90 -> tell DecisionAgent not to trade

    # (calm, wild) ranges that map to 0..100, per asset type
    daily_vol_range: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "stock": (0.008, 0.035), "crypto": (0.015, 0.06)})
    drawdown_range: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "stock": (0.02, 0.30), "crypto": (0.05, 0.50)})
    stretch_range: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "stock": (0.02, 0.15), "crypto": (0.05, 0.30)})
    fallback_stop_pct: dict[str, float] = field(default_factory=lambda: {
        "stock": 0.02, "crypto": 0.05})
    min_stop_pct: dict[str, float] = field(default_factory=lambda: {
        "stock": 0.01, "crypto": 0.02})

    profile_base_risk: dict[str, float] = field(default_factory=lambda: {
        "conservative": 0.005, "moderate": 0.010, "aggressive": 0.020})
    position_cap: dict[str, float] = field(default_factory=lambda: {
        "LOW": 0.10, "MEDIUM": 0.06, "HIGH": 0.02})   # same caps as DecisionAgent

    atr_stop_mult: float = 2.0
    reward_ratio: float = 2.0
    var_z_95: float = 1.645


@dataclass
class Component:
    name: str
    score: float          # 0 (safe) .. 100 (dangerous)
    weight: float
    detail: str


# ---------------------------------------------------------------------- agent
class RiskAgent:
    name = "Risk Management Agent"

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def assess(self, market: dict, capital: float, risk_per_trade: float | None = None,
               side: str = "BUY", asset_type: str | None = None,
               profile: str = "moderate") -> dict:
        cfg = self.config
        price = self._positive(market.get("price"), "price")
        capital = self._positive(capital, "capital")
        asset = (asset_type or market.get("asset_type") or "stock").lower()
        if asset not in cfg.daily_vol_range:
            raise ValueError(f"asset_type must be one of {list(cfg.daily_vol_range)}")
        if profile not in cfg.profile_base_risk:
            raise ValueError(f"profile must be one of {list(cfg.profile_base_risk)}")
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")

        closes = [float(x) for x in market.get("history", []) if x and math.isfinite(float(x))]
        vol, vol_src = self._volatility(market, closes, price)
        rsi = market.get("rsi", compute_rsi(closes))
        atr = market.get("atr") or compute_atr(closes, market.get("highs"), market.get("lows"))

        comps = [c for c in (
            self._c_volatility(vol, vol_src, asset),
            self._c_drawdown(price, closes, asset),
            self._c_rsi(rsi),
            self._c_shock(market.get("change_pct"), vol),
            self._c_stretch(price, closes, asset),
            self._c_liquidity(market.get("volume"), market.get("avg_volume")),
            self._c_beta(market.get("beta")),
        ) if c is not None]

        if comps:
            wsum = sum(c.weight for c in comps)
            score = sum(c.score * c.weight for c in comps) / wsum
            # one dangerous factor must not be diluted by calm ones
            score = 0.65 * score + 0.35 * max(c.score for c in comps)
            vol_comp = next((c for c in comps if c.name == "volatility"), None)
            if vol_comp:                      # wild price swings always dominate
                score = max(score, 0.85 * vol_comp.score)
        else:
            score = 50.0
        warnings: list[str] = []

        days = market.get("earnings_in_days")
        if isinstance(days, (int, float)) and 0 <= days <= 3:
            score = min(100.0, score + 12)
            warnings.append(f"Earnings/event in {int(days)} day(s): gap risk is high.")
        score = round(score, 1)

        level = "LOW" if score < cfg.low_max else "HIGH" if score >= cfg.high_min else "MEDIUM"

        # ---- position sizing ------------------------------------------------
        base = cfg.profile_base_risk[profile]
        ai_rpt = base * max(0.25, min(1.25, 1.25 - score / 100))
        rpt = ai_rpt if risk_per_trade is None else float(risk_per_trade)
        if not 0 < rpt <= 0.10:
            raise ValueError("risk_per_trade must be between 0 and 0.10 (10%)")
        if risk_per_trade is not None and rpt > ai_rpt * 1.5:
            warnings.append(f"Your risk per trade ({rpt:.2%}) is much higher than the "
                            f"AI-suggested {ai_rpt:.2%} for this risk level.")

        risk_budget = capital * rpt
        if atr:
            stop_distance = max(cfg.atr_stop_mult * atr, price * cfg.min_stop_pct[asset])
        else:
            stop_distance = price * cfg.fallback_stop_pct[asset]
        stop_distance = min(max(stop_distance, 0.01), price * 0.5)

        qty_by_risk = risk_budget / stop_distance
        cap_pct = cfg.position_cap[level]
        qty_by_cap = capital * cap_pct / price
        quantity = min(qty_by_risk, qty_by_cap)
        limited_by = "risk budget" if qty_by_risk <= qty_by_cap else "position cap"
        decimals = 6 if asset == "crypto" else 4
        quantity = round(quantity, decimals)

        position_value = quantity * price
        direction = 1 if side == "BUY" else -1
        stop_loss = price - direction * stop_distance
        take_profit = price + direction * stop_distance * cfg.reward_ratio
        var_95 = cfg.var_z_95 * vol * position_value if vol else None

        # ---- warnings & reasons --------------------------------------------
        if rsi is not None and rsi > 70:
            warnings.append(f"RSI {rsi:.0f}: overbought, pullback risk.")
        elif rsi is not None and rsi < 30:
            warnings.append(f"RSI {rsi:.0f}: oversold, can keep falling or bounce sharply.")
        vol_ratio = None
        if market.get("volume") and market.get("avg_volume"):
            vol_ratio = market["volume"] / market["avg_volume"]
            if vol_ratio < 0.5:
                warnings.append("Volume is very thin: harder to exit at your stop price.")
        veto = score >= cfg.veto_score
        if veto:
            warnings.append("Extreme risk: new trades are vetoed.")

        reasons = [f"Risk score {score}/100 -> {level}."]
        for c in sorted(comps, key=lambda c: c.score, reverse=True)[:3]:
            reasons.append(f"{c.name}: {c.score:.0f}/100 ({c.detail})")

        return {
            "risk_level": level,
            "risk_score": score,
            "risk_label": self._label(score),
            "veto": veto,
            "asset_type": asset,
            "profile": profile,
            "components": [{"name": c.name, "score": round(c.score, 1),
                           "weight": round(c.weight, 3), "detail": c.detail} for c in comps],
            "ai_suggested_risk_per_trade": round(ai_rpt, 5),
            "risk_per_trade": round(rpt, 5),
            "risk_per_trade_mode": "AI" if risk_per_trade is None else "manual",
            "risk_budget": round(risk_budget, 2),
            "stop_distance": round(stop_distance, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "risk_reward": cfg.reward_ratio,
            "suggested_quantity": quantity,
            "position_value": round(position_value, 2),
            "position_pct": round(position_value / capital, 4),
            "max_position_pct": cap_pct,
            "limited_by": limited_by,
            "actual_risk": round(quantity * stop_distance, 2),
            "daily_volatility": round(vol, 5) if vol else None,
            "var_95_1day": round(var_95, 2) if var_95 else None,
            "rsi": round(rsi, 1) if rsi is not None else None,
            "warnings": warnings,
            "reasons": reasons,
        }

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _positive(value: Any, label: str) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a number") from exc
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"{label} must be greater than 0")
        return v

    @staticmethod
    def _label(score: float) -> str:
        for limit, name in ((20, "Very Low"), (35, "Low"), (65, "Medium"), (80, "High")):
            if score < limit:
                return name
        return "Extreme"

    @staticmethod
    def _volatility(market: dict, closes: list[float], price: float):
        if market.get("volatility"):
            return float(market["volatility"]), "given"
        v = daily_volatility(closes)
        if v:
            return v, "price history"
        if market.get("atr"):
            return float(market["atr"]) / price * 0.8, "ATR estimate"
        return None, "unknown"

    def _c_volatility(self, vol, src, asset):
        if vol is None:
            return None
        lo, hi = self.config.daily_vol_range[asset]
        return Component("volatility", _scale(vol, lo, hi), self.config.weights["volatility"],
                         f"daily moves ~{vol:.2%} from {src}")

    def _c_drawdown(self, price, closes, asset):
        if len(closes) < 10:
            return None
        dd = max(0.0, 1 - price / max(closes))
        lo, hi = self.config.drawdown_range[asset]
        return Component("drawdown", _scale(dd, lo, hi), self.config.weights["drawdown"],
                         f"{dd:.1%} below recent high")

    def _c_rsi(self, rsi):
        if rsi is None:
            return None
        return Component("rsi", _scale(abs(rsi - 50), 5, 35), self.config.weights["rsi"],
                         f"RSI {rsi:.0f}")

    def _c_shock(self, change_pct, vol):
        if change_pct is None or not vol:
            return None
        z = abs(change_pct / 100) / vol
        return Component("shock", _scale(z, 1, 4), self.config.weights["shock"],
                         f"today's move is {z:.1f}x normal")

    def _c_stretch(self, price, closes, asset):
        if len(closes) < 20:
            return None
        sma = sum(closes[-20:]) / 20
        dev = abs(price / sma - 1)
        lo, hi = self.config.stretch_range[asset]
        return Component("stretch", _scale(dev, lo, hi), self.config.weights["stretch"],
                         f"{dev:.1%} away from 20-day average")

    def _c_liquidity(self, volume, avg_volume):
        if not volume or not avg_volume:
            return None
        ratio = volume / avg_volume
        thin = _scale(1 - ratio, 0.0, 0.8)
        spike = _scale(ratio, 2.0, 5.0) * 0.6
        return Component("liquidity", max(thin, spike), self.config.weights["liquidity"],
                         f"volume {ratio:.1f}x average")

    def _c_beta(self, beta):
        if beta is None:
            return None
        return Component("beta", _scale(float(beta), 0.8, 2.0), self.config.weights["beta"],
                         f"beta {float(beta):.2f}")


if __name__ == "__main__":
    import json
    agent = RiskAgent()
    print(json.dumps(agent.assess({"price": 100, "rsi": 72}, capital=10_000), indent=2))