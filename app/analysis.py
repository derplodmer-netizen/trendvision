"""Technical-analysis signal on the latest ticks: EMA trend, RSI and short-term momentum.

The output is an honest description of what the indicators show — not a promise of the outcome.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MIN_TICKS = 40


@dataclass
class Signal:
    symbol: str
    direction: str          # "buy" | "sell"
    score: float            # -1..1, sign = direction, magnitude = indicator agreement
    ema_fast: float
    ema_slow: float
    rsi: float
    momentum: float         # relative change over the momentum window
    description: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strength"] = round(abs(self.score) * 100)
        return d


def ema(values: list[float], period: int) -> float:
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = losses = 0.0
    for a, b in zip(values[:period], values[1 : period + 1]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for a, b in zip(values[period:], values[period + 1 :]):
        d = b - a
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def analyze(symbol: str, prices: list[float]) -> Signal | None:
    if len(prices) < MIN_TICKS:
        return None
    window = prices[-240:]
    ef, es = ema(window, 8), ema(window, 21)
    r = rsi(window, 14)
    look = min(20, len(window) - 1)
    mom = (window[-1] - window[-1 - look]) / window[-1 - look] if window[-1 - look] else 0.0

    # Votes in [-1, 1]
    span = max(window) - min(window) or 1e-12
    trend_vote = max(-1.0, min(1.0, (ef - es) / span * 4))
    # RSI: trend-following in 45..70 / 30..55, mean-reversion at extremes
    if r >= 75:
        rsi_vote = -0.8
    elif r <= 25:
        rsi_vote = 0.8
    else:
        rsi_vote = max(-1.0, min(1.0, (r - 50) / 25))
    mom_vote = max(-1.0, min(1.0, mom / (span / window[-1] or 1e-12) * 2)) if window[-1] else 0.0

    score = 0.45 * trend_vote + 0.30 * rsi_vote + 0.25 * mom_vote
    if score == 0:
        score = 0.01 if window[-1] >= window[0] else -0.01
    direction = "buy" if score > 0 else "sell"
    return Signal(
        symbol=symbol,
        direction=direction,
        score=round(score, 4),
        ema_fast=ef,
        ema_slow=es,
        rsi=round(r, 1),
        momentum=mom,
        description=describe(symbol, direction, ef, es, r, mom, abs(score)),
    )


def describe(symbol: str, direction: str, ef: float, es: float, r: float, mom: float, strength: float) -> str:
    trend = "выше" if ef > es else "ниже"
    trend_word = "восходящий" if ef > es else "нисходящий"
    if r >= 70:
        rsi_txt = f"RSI {r:.0f} — зона перекупленности"
    elif r <= 30:
        rsi_txt = f"RSI {r:.0f} — зона перепроданности"
    else:
        rsi_txt = f"RSI {r:.0f} — нейтральная зона"
    mom_txt = "растёт" if mom > 0 else "снижается" if mom < 0 else "стоит на месте"
    side = "покупку (вверх)" if direction == "buy" else "продажу (вниз)"
    agree = "сильное" if strength > 0.6 else "умеренное" if strength > 0.3 else "слабое"
    return (
        f"{symbol}: быстрая EMA {trend} медленной — краткосрочный {trend_word} тренд; {rsi_txt}; "
        f"цена за последние тики {mom_txt}. Согласие индикаторов {agree} — сигнал на {side}. "
        f"Индикаторы описывают прошлое движение и не гарантируют результат."
    )
