
import ccxt

exchange = ccxt.binance({
    "enableRateLimit": True,
})

WATCHLIST = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
]


def get_live_watchlist():
    results = []

    for symbol in WATCHLIST:
        ticker = exchange.fetch_ticker(symbol)

        results.append({
            "symbol": symbol,
            "price": ticker.get("last"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "high_24h": ticker.get("high"),
            "low_24h": ticker.get("low"),
            "volume_24h": ticker.get("baseVolume"),
            "timestamp": ticker.get("timestamp"),
            "datetime": ticker.get("datetime"),
            "decision": "buy" if ticker.get("change") > 0 else "sell",
        })

    return {
        "source": "exchange_market_data",
        "count": len(results),
        "coins": results,
        "timestamp": exchange.milliseconds(),
        "datetime": exchange.iso8601(exchange.milliseconds()),
    }
