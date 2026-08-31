"""
Le score /12 MikeMike. Chaque critère = 1 point, chacun adossé à un pilier de la
méthode (voir le cours PDF). La grade (A+/A/A-…) est dérivée du score.

Sans clé Helius, les 2 critères wallet (holders + smart-money) valent 0 :
la grade plafonne alors ~A- (10/12), ce qui montre concrètement l'apport des wallets.
"""
from typing import List, Dict
import config
from .model import Pair


def _crit(name: str, ok: bool, detail: str) -> Dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def score_pair(p: Pair) -> None:
    """Remplit p.criteria, p.score, p.grade (mutation en place)."""
    c: List[Dict] = []

    # ── VOLUME / ATTENTION (le King) ──────────────────────
    c.append(_crit(
        "Liquidité ≥ 50k", p.liquidity_usd >= config.MIN_LIQUIDITY_USD,
        f"{p.liquidity_usd:,.0f}$"))

    c.append(_crit(
        "Volume 24h ≥ 500k", p.vol_h24 >= config.VOL24_GOOD,
        f"{p.vol_h24:,.0f}$" + ("  🔥 MONSTER" if p.vol_h24 >= config.VOL24_MONSTER else "")))

    turnover = (p.vol_h24 / p.market_cap) if p.market_cap else 0
    c.append(_crit(
        "Turnover Vol/MC ≥ 0.5", turnover >= config.TURNOVER_MIN,
        f"{turnover:.2f}x"))

    c.append(_crit(
        "Volume 1h ≥ 100k", p.vol_h1 >= config.VOLH1_ATTENTION,
        f"{p.vol_h1:,.0f}$"))

    # ── STRUCTURE / PRICE ACTION ──────────────────────────
    buy_pressure = p.buys_h1 >= p.sells_h1 and (p.buys_h1 + p.sells_h1) > 0
    c.append(_crit(
        "Buy pressure (buys ≥ sells 1h)", buy_pressure,
        f"{p.buys_h1}/{p.sells_h1}"))

    # pas un dump à fort volume : on refuse chute forte 1h avec volume 5m gonflé
    dumping = p.chg_h1 <= -8 and p.vol_m5 > 0.02 * (p.vol_h1 or 1)
    c.append(_crit(
        "Pas de dump high-volume", not dumping,
        "OK" if not dumping else f"{p.chg_h1:.1f}% 1h à fort vol"))

    holds_floor = (p.chg_h6 is not None and p.chg_h6 >= -20) and (p.chg_h1 > -8)
    c.append(_crit(
        "Tient son floor", holds_floor,
        f"6h {p.chg_h6:+.0f}% / 1h {p.chg_h1:+.0f}%"))

    actionable = p.phase in ("Early", "Retest", "Running", "Compressing")
    c.append(_crit(
        "Phase actionnable", actionable, p.phase))

    # ── RSI ───────────────────────────────────────────────
    rsi_ok = True
    rsi_detail = "n/a"
    if p.rsi_1h is not None:
        rsi_detail = f"1h {p.rsi_1h}"
        # room to run (pas overbought) OU setup de bounce (oversold)
        rsi_ok = (p.rsi_1h < config.RSI_OVERBOUGHT) or (p.rsi_1h <= config.RSI_OVERSOLD)
    c.append(_crit("RSI 1h favorable", rsi_ok, rsi_detail))

    # ── SÉCURITÉ / WALLETS ────────────────────────────────
    if p.wallets_available and p.top10_pct is not None:
        dist_ok = (p.top_holder_pct or 0) <= config.TOP_HOLDER_MAX and \
                  (p.top10_pct or 0) <= config.TOP10_MAX
        c.append(_crit(
            "Distribution holders saine", dist_ok,
            f"top1 {(p.top_holder_pct or 0)*100:.0f}% / top10 {(p.top10_pct or 0)*100:.0f}%"))
    else:
        c.append(_crit("Distribution holders saine", False, "n/a — ajoute une clé Helius"))

    if p.wallets_available:
        smart_ok = p.smart_holders >= 1
        c.append(_crit(
            "Smart-money présent", smart_ok,
            (f"{p.smart_holders} wallet(s): " + ", ".join(p.smart_names[:3]))
            if smart_ok else "aucun wallet suivi"))
    else:
        c.append(_crit("Smart-money présent", False, "n/a — ajoute une clé Helius"))

    # ── ÂGE & SURVIE ──────────────────────────────────────
    age_ok = (p.age_hours is not None
              and config.MIN_AGE_HOURS <= p.age_hours <= config.MAX_AGE_HOURS)
    c.append(_crit(
        "Âge & survie", age_ok,
        f"{p.age_hours:.1f}h" if p.age_hours is not None else "?"))

    p.criteria = c
    p.max_score = len(c)
    p.score = sum(1 for x in c if x["ok"])
    p.grade = config.grade_from_score(p.score)
