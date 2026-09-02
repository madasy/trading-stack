import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

@dataclass
class Signal:
    id: int | None
    candle_ts: str          # ISO timestamp of the closed candle
    symbol: str
    kind: str               # entry | exit
    side: str               # buy | sell
    price: float
    stop: float | None
    qty: float | None
    reason: str
    context: dict | None = None   # entry: indicator snapshot at signal time; exit: excursion while open

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "Signal":
        return cls(**json.loads(s))

@dataclass
class Decision:
    signal_id: int
    decision: str           # yes | no | auto
    decided_by: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "Decision":
        return cls(**json.loads(s))

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
