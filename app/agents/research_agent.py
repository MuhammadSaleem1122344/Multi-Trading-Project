"""Advanced, pluggable Research Agent.

Output is compatible with DecisionAgent:
    {"score": -1..1, "confidence": 0..1, "reasons": [...], ...}

Every data source is a *provider*. Add or remove providers freely; a failing
provider is logged and skipped, it never crashes the agent. Network code is
NOT inside this file: you pass in callables (news_fetcher, llm), so you can
plug any API (NewsAPI, Finnhub, Alpha Vantage, SEC, OpenAI, Claude, ...).
"""
from __future__ import annotations

import json
import hashlib
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Signal:
    name: str
    score: float                  # -1 (very bearish) .. +1 (very bullish)
    confidence: float             # 0 .. 1
    reasons: list[str] = field(default_factory=list)


class SignalProvider(ABC):
    name: str = "provider"
    weight: float = 1.0

    @abstractmethod
    def fetch(self, symbol: str, market: dict) -> Signal | None:
        """Return a Signal, or None when this provider has no data."""


# --------------------------------------------------------------------------- #
# 1. Momentum (works offline from the market dict)
# --------------------------------------------------------------------------- #
class MomentumProvider(SignalProvider):
    name, weight = "momentum", 0.25

    def fetch(self, symbol: str, market: dict) -> Signal | None:
        change = market.get("change_pct")
        if change is None:
            return None
        score = math.tanh(float(change) / 3.0)          # smooth, saturates near +-1
        conf = 0.5
        reasons = [f"Daily change {change:+.2f}% -> momentum {score:+.2f}."]
        vol, avg_vol = market.get("volume"), market.get("avg_volume")
        if vol and avg_vol:
            ratio = vol / avg_vol
            if ratio > 1.5:
                conf = 0.7
                reasons.append(f"Volume is {ratio:.1f}x average, move is more reliable.")
            elif ratio < 0.6:
                conf = 0.35
                reasons.append("Volume is thin, move is less reliable.")
        return Signal(self.name, score, conf, reasons)


# --------------------------------------------------------------------------- #
# 2. News sentiment (lexicon baseline, optional LLM upgrade)
# --------------------------------------------------------------------------- #
_POS = {"beat", "beats", "surge", "surges", "growth", "record", "upgrade", "upgraded",
        "profit", "strong", "rally", "gain", "gains", "partnership", "approval",
        "buyback", "outperform", "raises", "bullish", "expands"}
_NEG = {"miss", "misses", "plunge", "plunges", "lawsuit", "downgrade", "downgraded",
        "loss", "losses", "weak", "fraud", "probe", "recall", "layoffs", "bankruptcy",
        "cuts", "bearish", "fine", "investigation", "decline", "warning"}
_NEGATORS = {"no", "not", "never", "without", "fails", "failed"}


def lexicon_sentiment(text: str) -> float:
    words = re.findall(r"[a-z']+", text.lower())
    total = 0.0
    for i, w in enumerate(words):
        s = 1.0 if w in _POS else -1.0 if w in _NEG else 0.0
        if s and i > 0 and words[i - 1] in _NEGATORS:
            s = -s
        total += s
    return _clamp(total / 2.0)       # 2 net words ~ full strength


class NewsSentimentProvider(SignalProvider):
    """news_fetcher(symbol) -> list[str | {"title": str, "ts": epoch_seconds}]
    llm_scorer(headline) -> float in [-1, 1]   (optional, overrides the lexicon)"""
    name, weight = "news", 0.35

    def __init__(self, news_fetcher: Callable[[str], list],
                 llm_scorer: Callable[[str], float] | None = None,
                 half_life_hours: float = 24.0, max_items: int = 20) -> None:
        self.news_fetcher = news_fetcher
        self.llm_scorer = llm_scorer
        self.half_life = half_life_hours * 3600
        self.max_items = max_items

    def fetch(self, symbol: str, market: dict) -> Signal | None:
        items = (self.news_fetcher(symbol) or [])[: self.max_items]
        if not items:
            return None
        now = time.time()
        num = den = 0.0
        best: tuple[float, str] = (0.0, "")
        for item in items:
            title, ts = (item, None) if isinstance(item, str) else (item.get("title", ""), item.get("ts"))
            if not title:
                continue
            try:
                s = _clamp(float(self.llm_scorer(title))) if self.llm_scorer else lexicon_sentiment(title)
            except Exception:                                   # one bad headline is fine
                s = lexicon_sentiment(title)
            age = max(0.0, now - ts) if ts else 0.0
            w = 0.5 ** (age / self.half_life)                   # recent news counts more
            num, den = num + s * w, den + w
            if abs(s) * w > abs(best[0]):
                best = (s * w, title)
        if den == 0:
            return None
        score = num / den
        conf = min(0.9, 0.3 + 0.07 * len(items))                # more headlines -> more trust
        reasons = [f"News sentiment {score:+.2f} from {len(items)} headlines."]
        if best[1]:
            reasons.append(f"Key headline: {best[1]}")
        return Signal(self.name, score, conf, reasons)


# --------------------------------------------------------------------------- #
# 3. Fundamentals (from market dict or your fundamentals API)
# --------------------------------------------------------------------------- #
class FundamentalsProvider(SignalProvider):
    """Uses optional keys: pe_ratio, sector_pe, eps_growth_pct, debt_to_equity, profit_margin_pct."""
    name, weight = "fundamentals", 0.20

    def fetch(self, symbol: str, market: dict) -> Signal | None:
        parts: list[tuple[float, str]] = []
        pe, sector_pe = market.get("pe_ratio"), market.get("sector_pe")
        if pe and sector_pe:
            rel = (sector_pe - pe) / sector_pe                  # cheaper than sector -> positive
            parts.append((_clamp(rel * 2), f"P/E {pe:.1f} vs sector {sector_pe:.1f}."))
        g = market.get("eps_growth_pct")
        if g is not None:
            parts.append((math.tanh(g / 20.0), f"EPS growth {g:+.1f}%."))
        de = market.get("debt_to_equity")
        if de is not None:
            parts.append((_clamp(1.0 - de), f"Debt/equity {de:.2f}."))
        pm = market.get("profit_margin_pct")
        if pm is not None:
            parts.append((math.tanh((pm - 10) / 15.0), f"Profit margin {pm:.1f}%."))
        if not parts:
            return None
        score = sum(s for s, _ in parts) / len(parts)
        return Signal(self.name, score, min(0.8, 0.3 + 0.1 * len(parts)), [r for _, r in parts])


# --------------------------------------------------------------------------- #
# 4. Earnings surprise
# --------------------------------------------------------------------------- #
class EarningsProvider(SignalProvider):
    """Uses optional key: earnings_surprise_pct (actual vs estimate)."""
    name, weight = "earnings", 0.20

    def fetch(self, symbol: str, market: dict) -> Signal | None:
        surprise = market.get("earnings_surprise_pct")
        if surprise is None:
            return None
        score = math.tanh(surprise / 8.0)
        return Signal(self.name, score, 0.75, [f"Last earnings surprise {surprise:+.1f}%."])


# --------------------------------------------------------------------------- #
# 5. LLM analyst (any LLM: you pass a function prompt -> str)
# --------------------------------------------------------------------------- #
class LLMAnalystProvider(SignalProvider):
    """llm(prompt) -> str. The model must answer JSON:
    {"score": -1..1, "confidence": 0..1, "reason": "..."}"""
    name, weight = "llm_analyst", 0.30

    def __init__(self, llm: Callable[[str], str],
                 context_fetcher: Callable[[str], str] | None = None) -> None:
        self.llm = llm
        self.context_fetcher = context_fetcher     # e.g. SEC filing / news summary text

    def fetch(self, symbol: str, market: dict) -> Signal | None:
        context = self.context_fetcher(symbol) if self.context_fetcher else ""
        prompt = (
            f"You are an equity research analyst. Stock: {symbol}.\n"
            f"Market data: {json.dumps(market, default=str)}\n"
            f"Context:\n{context[:4000]}\n\n"
            'Reply ONLY with JSON: {"score": <-1..1>, "confidence": <0..1>, "reason": "<one sentence>"}'
        )
        raw = self.llm(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        return Signal(self.name, _clamp(float(data["score"])),
                      max(0.0, min(0.8, float(data.get("confidence", 0.5)))),  # cap LLM trust
                      [f"LLM view: {data.get('reason', 'n/a')}"])


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class ResearchAgent:
    name = "Research Agent"

    def __init__(self, providers: list[SignalProvider] | None = None,
                 timeout_s: float = 10.0, cache_ttl_s: float = 300.0) -> None:
        self.providers = providers if providers is not None else [
            MomentumProvider(), FundamentalsProvider(), EarningsProvider()]
        self.timeout_s = timeout_s
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, dict]] = {}

    def analyze(self, market: dict) -> dict:
        symbol = str(market.get("symbol", "UNKNOWN"))
        market_fingerprint = hashlib.sha256(
            json.dumps(market, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        cache_key = f"{symbol}:{market_fingerprint}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self.cache_ttl_s:
            return cached[1]

        signals = self._collect(symbol, market)
        if not signals:
            return {"score": 0.0, "confidence": 0.0, "components": {},
                    "reasons": ["No research data available."], "sources_used": []}

        by_name = {p.name: p for p in self.providers}
        wsum = sum(by_name[s.name].weight for s in signals)
        score = sum(by_name[s.name].weight * s.confidence * s.score for s in signals) / wsum

        # confidence: how sure the sources are, how much of the planned coverage we got,
        # and a penalty when the sources disagree with each other
        mean_conf = sum(by_name[s.name].weight * s.confidence for s in signals) / wsum
        coverage = wsum / sum(p.weight for p in self.providers)
        mean_s = sum(s.score for s in signals) / len(signals)
        spread = math.sqrt(sum((s.score - mean_s) ** 2 for s in signals) / len(signals))
        confidence = _clamp(mean_conf * (0.5 + 0.5 * coverage) * (1 - min(0.5, spread)), 0.0, 1.0)

        reasons = [f"Research score {score:+.2f} from {len(signals)} source(s) "
                   f"(confidence {confidence:.2f})."]
        for s in signals:
            reasons.extend(s.reasons)
        if spread > 0.4:
            reasons.append("Warning: research sources disagree with each other.")

        result = {
            "score": round(_clamp(score), 4),
            "confidence": round(confidence, 4),
            "reasons": reasons,
            "components": {s.name: {"score": round(s.score, 4), "confidence": round(s.confidence, 4)}
                           for s in signals},
            "sources_used": [s.name for s in signals],
        }
        self._cache[cache_key] = (time.time(), result)
        return result

    def _collect(self, symbol: str, market: dict) -> list[Signal]:
        signals: list[Signal] = []
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as pool:
            futures = {pool.submit(p.fetch, symbol, market): p for p in self.providers}
            for fut, provider in futures.items():
                try:
                    sig = fut.result(timeout=self.timeout_s)
                except FutureTimeout:
                    logger.warning("%s timed out for %s", provider.name, symbol)
                    continue
                except Exception:
                    logger.exception("%s failed for %s", provider.name, symbol)
                    continue
                if sig is not None:
                    sig.score = _clamp(sig.score)
                    sig.confidence = _clamp(sig.confidence, 0.0, 1.0)
                    signals.append(sig)
        return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Demo with fake data sources: replace these lambdas with real APIs.
    fake_news = lambda sym: [
        {"title": f"{sym} beats earnings estimates, raises guidance", "ts": time.time() - 3600},
        {"title": f"Analyst upgrade for {sym} after strong growth", "ts": time.time() - 7200},
        {"title": f"{sym} faces regulatory probe", "ts": time.time() - 86400},
    ]
    agent = ResearchAgent([
        MomentumProvider(),
        NewsSentimentProvider(fake_news),
        FundamentalsProvider(),
        EarningsProvider(),
    ])
    out = agent.analyze({
        "symbol": "AAPL", "change_pct": 1.8, "volume": 90e6, "avg_volume": 55e6,
        "pe_ratio": 27, "sector_pe": 31, "eps_growth_pct": 12, "earnings_surprise_pct": 4.5,
    })
    print(json.dumps(out, indent=2))