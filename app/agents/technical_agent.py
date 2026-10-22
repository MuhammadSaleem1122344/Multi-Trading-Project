import math


class TechnicalAgent:
    name = "Technical Analysis Agent"

    def analyze(self, market: dict) -> dict:
        closes = self._history(market.get("history", []))
        price = self._number(market.get("price"), "price") if market.get("price") is not None else (
            closes[-1] if closes else None
        )
        if price is None or price <= 0:
            raise ValueError("market data must include a positive price or price history")

        sma20 = self._number(market.get("sma20"), "sma20") if market.get("sma20") is not None else self._average(closes, 20, price)
        sma50 = self._number(market.get("sma50"), "sma50") if market.get("sma50") is not None else self._average(closes, 50, price)
        rsi = self._number(market.get("rsi"), "rsi") if market.get("rsi") is not None else self._rsi(closes)
        change_pct = market.get("change_pct")
        if change_pct is None:
            change_pct = (closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else 0.0
        change_pct = self._number(change_pct, "change_pct")

        score = 0.0
        reasons = []
        if price > sma20:
            score += 0.25
            reasons.append("Price is above the 20-day moving average.")
        else:
            score -= 0.25
            reasons.append("Price is below the 20-day moving average.")

        if sma20 > sma50:
            score += 0.30
            reasons.append("20-day trend is above the 50-day trend.")
        else:
            score -= 0.30
            reasons.append("20-day trend is below the 50-day trend.")

        if 45 <= rsi <= 65:
            score += 0.20
            reasons.append("RSI is in a relatively balanced bullish range.")
        elif rsi > 70:
            score -= 0.15
            reasons.append("RSI indicates an overbought condition.")
        elif rsi < 30:
            score += 0.10
            reasons.append("RSI indicates a potentially oversold condition.")

        if change_pct > 0:
            score += 0.10
        else:
            score -= 0.10

        changes = [abs(closes[index] - closes[index - 1]) for index in range(1, len(closes))]
        atr = market.get("atr")
        if atr is None and changes:
            atr = sum(changes[-14:]) / min(14, len(changes))
        regime = "VOLATILE" if atr is not None and atr / price >= 0.04 else (
            "TRENDING" if abs(sma20 / sma50 - 1) >= 0.02 else "SIDEWAYS"
        )
        confidence = min(0.95, 0.45 + min(len(closes), 50) / 100 + abs(score) * 0.15)
        return {
            "score": round(max(-1, min(1, score)), 4),
            "confidence": round(confidence, 4),
            "price": price,
            "atr": atr,
            "regime": regime,
            "reasons": reasons,
            "indicators": {
                "sma20": sma20,
                "sma50": sma50,
                "rsi": rsi,
                "change_pct": change_pct,
                "history_points": len(closes),
            },
        }

    @staticmethod
    def _number(value, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must be finite")
        return number

    @classmethod
    def _history(cls, values) -> list[float]:
        if not isinstance(values, (list, tuple)):
            return []
        return [value for item in values if (value := cls._number(item, "history value")) > 0]

    @staticmethod
    def _average(values: list[float], period: int, fallback: float) -> float:
        sample = values[-period:]
        return sum(sample) / len(sample) if sample else fallback

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float:
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        changes = changes[-period:]
        if not changes:
            return 50.0
        gains = sum(max(change, 0) for change in changes) / len(changes)
        losses = sum(max(-change, 0) for change in changes) / len(changes)
        if losses == 0:
            return 100.0 if gains else 50.0
        return 100 - 100 / (1 + gains / losses)
