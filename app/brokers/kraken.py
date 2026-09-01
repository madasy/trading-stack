"""Live Kraken spot broker via ccxt. Only enabled when LIVE_CONFIRM=I_UNDERSTAND."""
import ccxt
from .. import config
from .base import Broker, Fill

class KrakenBroker(Broker):
    name = "kraken"

    def __init__(self):
        if config.LIVE_CONFIRM != "I_UNDERSTAND":
            raise RuntimeError("Refusing to start live broker: set LIVE_CONFIRM=I_UNDERSTAND")
        self.ex = ccxt.kraken({
            "apiKey": config.KRAKEN_API_KEY,
            "secret": config.KRAKEN_API_SECRET,
            "enableRateLimit": True,
        })
        self.ex.load_markets()

    def last_price(self, symbol):
        return float(self.ex.fetch_ticker(symbol)["last"])

    def _order(self, symbol, side, qty):
        qty = float(self.ex.amount_to_precision(symbol, qty))
        o = self.ex.create_order(symbol, "market", side, qty)
        price = o.get("average") or o.get("price") or self.last_price(symbol)
        filled = o.get("filled") or qty
        return Fill(float(price), float(filled), str(o.get("id")))

    def market_buy(self, symbol, qty):
        return self._order(symbol, "buy", qty)

    def market_sell(self, symbol, qty):
        return self._order(symbol, "sell", qty)
