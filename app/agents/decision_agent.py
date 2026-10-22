
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class DecisionConfig:
    """All tunable numbers live here, nothing is hard-coded in the logic."""

    technical_weight: float = 0.65
    research_weight: float = 0.35
    # (technical, research) weights per market regime reported by the technical agent
    regime_weights: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "TRENDING": (0.70, 0.30),
        "SIDEWAYS": (0.55, 0.45),
        "VOLATILE": (0.60, 0.40),
    })

    buy_threshold: float = 0.30
    sell_threshold: float = -0.30
    hysteresis: float = 0.05          # stops BUY/SELL flip-flopping near the threshold

    risk_score_multiplier: dict[RiskLevel, float] = field(default_factory=lambda: {
        RiskLevel.LOW: 1.00, RiskLevel.MEDIUM: 0.85, RiskLevel.HIGH: 0.60,
    })
    risk_position_cap: dict[RiskLevel, float] = field(default_factory=lambda: {
        RiskLevel.LOW: 0.10, RiskLevel.MEDIUM: 0.06, RiskLevel.HIGH: 0.02,
    })  # max fraction of portfolio per trade

    conflict_threshold: float = 0.20   # both agents must be at least this strong to count as conflict
    conflict_penalty: float = 0.70
    min_trade_confidence: float = 0.60  # below this a BUY becomes HOLD
    full_size_score: float = 0.60       # |score| at which the full suggested size is used

    atr_stop_mult: float = 2.0         # fallback levels if the risk agent gives none
    atr_target_mult: float = 3.0
    history_limit: int = 1000

    def __post_init__(self) -> None:
        if self.technical_weight <= 0 or self.research_weight <= 0:
            raise ValueError("weights must be positive")
        if not self.sell_threshold < 0 < self.buy_threshold:
            raise ValueError("need sell_threshold < 0 < buy_threshold")


class DecisionAgent:
    name = "Decision Orchestrator Agent"

    def __init__(self, config: DecisionConfig | None = None) -> None:
        self.config = config or DecisionConfig()
        self._last_signal: dict[str, Action] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=self.config.history_limit)

    def reset(self) -> None:
        self._last_signal.clear()
        self.history.clear()

    # ------------------------------------------------------------------ public
    def decide(self, symbol: str, technical: dict, research: dict, risk: dict,
               position: dict | None = None) -> dict:
        cfg = self.config
        tech_score = self._score(technical, "technical")
        res_score = self._score(research, "research")
        tech_conf = self._confidence_in(technical)
        res_conf = self._confidence_in(research)
        risk_level = self._risk_level(risk)
        held = self._held_qty(position)
        flags: list[str] = []

        # 1) regime-aware, confidence-weighted score
        regime = str(technical.get("regime", "")).upper()
        tw, rw = cfg.regime_weights.get(regime, (cfg.technical_weight, cfg.research_weight))
        wc_t, wc_r = tw * tech_conf, rw * res_conf
        if wc_t + wc_r > 0:
            avg = (tech_score * wc_t + res_score * wc_r) / (wc_t + wc_r)
            score = avg * math.sqrt((wc_t + wc_r) / (tw + rw))   # unsure agents shrink the score
        else:
            score = 0.0

        # 2) disagreement between agents -> be cautious
        conflict = (
            tech_score * res_score < 0
            and min(abs(tech_score), abs(res_score)) >= cfg.conflict_threshold
        )
        if conflict:
            score *= cfg.conflict_penalty
            flags.append("AGENT_CONFLICT")

        # 3) risk adjustment
        score *= cfg.risk_score_multiplier[risk_level]
        score = max(-1.0, min(1.0, score))

        # 4) signal with hysteresis
        signal = self._signal_from_score(symbol, score)

        # 5) confidence
        confidence = min(0.99, 0.50 + abs(score) * 0.50)
        if conflict:
            confidence *= 0.90
        if signal is Action.BUY and confidence < cfg.min_trade_confidence:
            flags.append("LOW_CONFIDENCE_DOWNGRADE")
            signal = Action.HOLD

        # 6) risk veto blocks *new* positions only
        if risk.get("veto") is True and signal is Action.BUY:
            flags.append("RISK_VETO")
            signal = Action.HOLD

        self._last_signal[symbol] = signal

        # 7) map signal + current position -> final action / intent
        action, intent = self._apply_position(signal, held, flags)

        quantity = self._quantity(action, intent, score, risk, held)
        position_pct = self._position_size(intent, score, risk_level, risk)
        levels = self._price_levels(intent, technical, risk)

        result = {
            "symbol": symbol,
            "action": action.value,
            "intent": intent.value,
            "signal": signal.value,
            "confidence": round(confidence, 4),
            "score": round(score, 4),
            "quantity": quantity,
            "position_size_pct": position_pct,
            "stop_loss": levels["stop_loss"],
            "take_profit": levels["take_profit"],
            "flags": flags,
            "factors": {
                "technical": round(tech_score, 4),
                "research": round(res_score, 4),
                "risk_level": risk_level.value,
                "regime": regime or "UNKNOWN",
                "weights": {"technical": tw, "research": rw},
                "conflict": conflict,
            },
            "reasoning": self._explain(tech_score, res_score, risk_level, score, regime,
                                       intent, flags, technical, research),
            "risk": risk,
            "timestamp": time.time(),
        }
        self.history.append(result)
        logger.info("%s -> %s/%s (score=%.2f, conf=%.2f, flags=%s)",
                    symbol, action.value, intent.value, score, confidence, flags)
        return result

    # --------------------------------------------------------------- internals
    @staticmethod
    def _score(agent_output: dict, label: str) -> float:
        try:
            value = float(agent_output["score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{label} agent must provide a numeric 'score'") from exc
        if not math.isfinite(value):
            raise ValueError(f"{label} score is not a finite number")
        return max(-1.0, min(1.0, value))

    @staticmethod
    def _confidence_in(agent_output: dict) -> float:
        """Optional agent self-confidence in [0, 1]; defaults to 1."""
        try:
            return max(0.0, min(1.0, float(agent_output.get("confidence", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _risk_level(risk: dict) -> RiskLevel:
        try:
            return RiskLevel(str(risk.get("risk_level", "MEDIUM")).upper())
        except ValueError:
            logger.warning("Unknown risk level %r, treating as HIGH", risk.get("risk_level"))
            return RiskLevel.HIGH   # fail safe

    @staticmethod
    def _held_qty(position: dict | None) -> float:
        if not position:
            return 0.0
        try:
            return max(0.0, float(position.get("qty", 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _signal_from_score(self, symbol: str, score: float) -> Action:
        cfg = self.config
        last = self._last_signal.get(symbol)
        buy_t, sell_t = cfg.buy_threshold, cfg.sell_threshold
        if last is Action.BUY:           # already leaning BUY: harder to leave that zone
            buy_t -= cfg.hysteresis
        elif last is Action.SELL:
            sell_t += cfg.hysteresis
        if score >= buy_t:
            return Action.BUY
        if score <= sell_t:
            return Action.SELL
        return Action.HOLD

    @staticmethod
    def _apply_position(signal: Action, held: float, flags: list[str]) -> tuple[Action, Intent]:
        if signal is Action.BUY:
            if held > 0:
                flags.append("ALREADY_LONG")
                return Action.HOLD, Intent.HOLD_LONG
            return Action.BUY, Intent.OPEN_LONG
        if signal is Action.SELL:
            if held > 0:
                return Action.SELL, Intent.CLOSE_LONG
            flags.append("SELL_IGNORED_FLAT")      # long-only: we do not short
            return Action.HOLD, Intent.STAY_FLAT
        return Action.HOLD, (Intent.HOLD_LONG if held > 0 else Intent.STAY_FLAT)

    def _quantity(self, action: Action, intent: Intent, score: float,
                  risk: dict, held: float) -> float | None:
        if intent is Intent.CLOSE_LONG:
            return held
        if intent is not Intent.OPEN_LONG:
            return None
        suggested = risk.get("suggested_quantity")
        if not isinstance(suggested, (int, float)) or suggested <= 0:
            return None
        conviction = min(1.0, abs(score) / self.config.full_size_score)
        return round(float(suggested) * conviction, 6)

    def _position_size(self, intent: Intent, score: float,
                       level: RiskLevel, risk: dict) -> float:
        if intent is not Intent.OPEN_LONG:
            return 0.0
        cap = self.config.risk_position_cap[level]
        if isinstance(risk.get("max_position_pct"), (int, float)):
            cap = min(cap, float(risk["max_position_pct"]))
        return round(cap * min(1.0, abs(score) / self.config.full_size_score), 4)

    def _price_levels(self, intent: Intent, technical: dict, risk: dict) -> dict[str, float | None]:
        none = {"stop_loss": None, "take_profit": None}
        if intent is not Intent.OPEN_LONG:
            return none
        sl, tp = risk.get("stop_loss"), risk.get("take_profit")
        if isinstance(sl, (int, float)) and isinstance(tp, (int, float)) \
                and risk.get("side", "BUY") == "BUY":
            return {"stop_loss": float(sl), "take_profit": float(tp)}
        price, atr = technical.get("price"), technical.get("atr")
        if not price or not atr:
            return none
        cfg = self.config
        return {"stop_loss": round(price - cfg.atr_stop_mult * atr, 4),
                "take_profit": round(price + cfg.atr_target_mult * atr, 4)}

    @staticmethod
    def _explain(tech: float, res: float, level: RiskLevel, score: float, regime: str,
                 intent: Intent, flags: list[str], technical: dict, research: dict) -> str:
        head = (f"Technical={tech:+.2f}; research={res:+.2f}; risk={level.value}; "
                f"regime={regime or 'UNKNOWN'}; combined={score:+.2f}; plan={intent.value}.")
        if flags:
            head += f" Flags: {', '.join(flags)}."
        reasons = " ".join(list(technical.get("reasons", [])) + list(research.get("reasons", [])))
        return f"{head} {reasons}".strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = DecisionAgent()
    print(agent.decide(
        "AAPL",
        technical={"score": 0.7, "confidence": 0.8, "price": 190.0, "atr": 3.2,
                   "regime": "TRENDING", "reasons": ["RSI recovering", "price above 50 EMA"]},
        research={"score": 0.30, "confidence": 0.6, "reasons": ["Positive earnings outlook"]},
        risk={"risk_level": "MEDIUM", "max_position_pct": 0.05, "suggested_quantity": 12.0},
    ))