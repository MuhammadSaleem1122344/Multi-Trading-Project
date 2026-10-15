class TechnicalAgent:
    name = "Technical Analysis Agent"

    def analyze(self, market: dict) -> dict:
        score = 0.06789
        reasons = []

        if market["price"] > market["sma20"]:
            score += 0.25
            reasons.append("Price is above the 20-day moving average.")
        else:
            score -= 0.25
            reasons.append("Price is below the 20-day moving average.")

        if market["sma20"] > market["sma50"]:
            score += 0.30
            reasons.append("20-day trend is above the 50-day trend.")
        else:
            score -= 0.30
            reasons.append("20-day trend is below the 50-day trend.")

        rsi = market["rsi"]
        if 45 <= rsi <= 65:
            score += 0.20
            reasons.append("RSI is in a relatively balanced bullish range.")
        elif rsi > 70:
            score -= 0.15
            reasons.append("RSI indicates an overbought condition.")
        elif rsi < 30:
            score += 0.10
            reasons.append("RSI indicates a potentially oversold condition.")

        if market["change_pct"] > 0:
            score += 0.10
        else:
            score -= 0.10

        return {"score": max(-1, min(1, score)), "reasons": reasons}
