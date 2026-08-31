"""Indicateurs de pure price action : RSI (Wilder) et niveaux de Fibonacci."""
from typing import List, Optional, Tuple


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """RSI de Wilder sur la dernière valeur. Retourne None si pas assez de données."""
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    # première moyenne
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    # lissage de Wilder sur le reste
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def rsi_zone(value: Optional[float], overbought: float = 70, oversold: float = 40) -> str:
    """Classe le RSI en zone lisible."""
    if value is None:
        return "n/a"
    if value >= 80:
        return "extreme"
    if value >= overbought:
        return "overbought"
    if value <= 20:
        return "capitulation"
    if value <= oversold:
        return "oversold"
    return "neutral"


def find_swing(candles: List[list], lookback: int = 24) -> Tuple[Optional[float], Optional[float]]:
    """
    À partir d'une liste OHLCV [ts, o, h, l, c, v] (ordre chronologique croissant),
    trouve la dernière jambe : le swing_high le plus récent et le swing_low qui le précède.
    Retourne (swing_low, swing_high) en prix.
    """
    if not candles:
        return None, None
    window = candles[-lookback:] if len(candles) > lookback else candles
    highs = [c[2] for c in window]
    lows = [c[3] for c in window]
    hi_idx = highs.index(max(highs))
    swing_high = highs[hi_idx]
    # le plus bas AVANT le sommet = base de la jambe (fallback : min global)
    pre = lows[:hi_idx + 1] or lows
    swing_low = min(pre)
    return swing_low, swing_high


def fib_levels(swing_low: float, swing_high: float) -> dict:
    """Retracements de Fibonacci d'une jambe haussière (support en pullback)."""
    span = swing_high - swing_low
    return {
        "0.5":   swing_high - span * 0.5,
        "0.618": swing_high - span * 0.618,
        "0.65":  swing_high - span * 0.65,
        "0.786": swing_high - span * 0.786,
    }
