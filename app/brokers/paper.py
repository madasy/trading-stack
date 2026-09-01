"""Paper broker: fills instantly at the current Kraken ticker price (public API, no keys)."""
import ccxt
from .base import Broker, Fill

class PaperBroker(Broker):
    name = "paper"

    def __init__(self):
        self.ex = ccxt.kraken({"enableRateLimit": True})

    def last_price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def market_buy(self, symbol, qty):
        return Fill(self.last_price(symbol), qty, None)

    def market_sell(self, symbol, qty):
        return Fill(self.last_price(symbol), qty, None)
