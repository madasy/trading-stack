from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Fill:
    price: float
    qty: float
    order_id: str | None

class Broker(ABC):
    name: str

    @abstractmethod
    def market_buy(self, symbol: str, qty: float) -> Fill: ...

    @abstractmethod
    def market_sell(self, symbol: str, qty: float) -> Fill: ...

    @abstractmethod
    def last_price(self, symbol: str) -> float: ...
