from .. import config
from .base import Broker

def get_broker() -> Broker:
    if config.BROKER == "kraken":
        from .kraken import KrakenBroker
        return KrakenBroker()
    from .paper import PaperBroker
    return PaperBroker()
