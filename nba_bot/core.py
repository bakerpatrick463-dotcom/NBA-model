from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

MODEL = "opportunity-demo-v1"
POLICY = "paper-v1-ev05-one-per-game"
RULES = "full-game-including-overtime; DNP void; integer equality push"


def now():
    return datetime.now(timezone.utc)


def iso(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc).isoformat()


def dt(value):
    return datetime.fromisoformat(iso(value))


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def american_to_decimal(value):
    if not math.isfinite(value) or abs(value) < 100:
        raise ValueError("American odds must be <= -100 or >= 100")
    return 1 + (value / 100 if value > 0 else 100 / abs(value))


def profit(status, odds, risk=1.0):
    if not math.isfinite(odds) or odds <= 1 or risk != 1:
        raise ValueError("Valid decimal odds and exactly one unit required")
    if status == "win":
        return risk * (odds - 1)
    if status == "loss":
        return -risk
    if status in ("push", "void"):
        return 0.0
    raise ValueError("Not a settled status")


@dataclass(frozen=True)
class Quote:
    event: str
    start: str
    subject: str
    market: str
    side: str
    line: float
    odds: float
    book: str
    region: str
    quoted: str
    collected: str
    simulated: bool
    rules: str = RULES
    period: str = "full_game"
    status: str = "active"
    commission: float = 0.0
    product: str = "sportsbook"
    event_name: str = ""
    book_name: str = ""

    def validate(self, at):
        if self.market not in {"points", "assists", "rebounds", "threes", "team_total", "total", "spread"}:
            raise ValueError("Unsupported market")
        if self.side not in ({"home", "away"} if self.market == "spread" else {"over", "under"}):
            raise ValueError("Invalid side")
        if not all(math.isfinite(x) for x in (self.line, self.odds, self.commission)):
            raise ValueError("Nonfinite quote")
        if self.odds <= 1 or not 0 <= self.commission < 1:
            raise ValueError("Invalid price or commission")
        if self.period != "full_game" or self.status != "active" or self.product != "sportsbook":
            raise ValueError("Unsupported period, unavailable quote, or non-sportsbook product")
        if not all((self.event, self.subject, self.book, self.region, self.rules)):
            raise ValueError("Missing canonical identity or settlement rules")
        at, quoted, collected, start = map(dt, (at, self.quoted, self.collected, self.start))
        if not quoted <= collected <= at < start:
            raise ValueError("Quote or selection is outside eligible timestamp order")
        if (at - quoted).total_seconds() > 120 or (at - collected).total_seconds() > 120:
            raise ValueError("Stale quote")

    @property
    def id(self):
        return identity(asdict(self))


def poisson_cdf(k, mean):
    if k < 0:
        return 0.0
    mass = math.exp(-mean)
    total = mass
    for i in range(1, k + 1):
        mass *= mean / i
        total += mass
    return min(1.0, total)


def probabilities(q, mean, sd=None):
    """Discrete count baseline; Gaussian with continuity correction for scores/margins."""
    threshold = -q.line if q.market == "spread" else q.line
    if sd is None:
        if mean <= 0 or mean > 200:
            raise ValueError("Unsupported Poisson mean")
        cdf = lambda k: poisson_cdf(k, mean)
    else:
        if sd <= 0:
            raise ValueError("Positive spread required")
        cdf = lambda k: 0.5 * (1 + math.erf((k + 0.5 - mean) / (sd * math.sqrt(2))))
    below = cdf(math.ceil(threshold) - 1)
    through = cdf(math.floor(threshold))
    push = max(0.0, through - below) if threshold.is_integer() else 0.0
    above = max(0.0, 1 - through)
    # Spread forecasts use the selected team's margin distribution.
    win, loss = (above, below) if q.side in ("over", "home", "away") else (below, above)
    return win, loss, push


def value(q, p):
    win, loss, _ = p
    effective = 1 + (q.odds - 1) * (1 - q.commission)
    ev = win * (effective - 1) - loss
    minimum = 1 + loss / (win * (1 - q.commission)) if win else None
    return ev, minimum


def allocate_minutes(weights):
    """Constrained proportional allocation; absent players have weight zero."""
    if any(not math.isfinite(v) or v < 0 for v in weights.values()) or sum(v > 0 for v in weights.values()) < 5:
        raise ValueError("At least five active players and nonnegative weights required")
    result = {k: 0.0 for k in weights}
    remaining = 240.0
    active = {k: v for k, v in weights.items() if v > 0}
    while active:
        total = sum(active.values())
        capped = [k for k, v in active.items() if remaining * v / total > 48]
        if not capped:
            for k, v in active.items():
                result[k] = remaining * v / total
            break
        for k in capped:
            result[k] = 48.0
            remaining -= 48
            del active[k]
    return result
