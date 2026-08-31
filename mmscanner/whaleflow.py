"""
Whale Flow (style sun-flow) — Net USD Flow d'un token sur 24h / 7j / 30j,
ventilé par catégorie de wallet : Whale / Shark / Dolphin / Fish.

Net USD Flow > 0  = inflow net (accumulation)
Net USD Flow < 0  = outflow net (distribution)

Approximation : USD ≈ |token_delta| × prix courant. Sur 30j c'est indicatif
(le prix bouge), mais le signe et les ordres de grandeur restent lisibles.
"""
import time
from typing import Optional

import config
from . import helius_tx, sources_dex

# seuils de catégorie par volume brut tradé (USD) sur la fenêtre 30j
TIERS = [("Whale", 50_000), ("Shark", 10_000), ("Dolphin", 1_000), ("Fish", 0)]
TIER_EMOJI = {"Whale": "🐋", "Shark": "🦈", "Dolphin": "🐬", "Fish": "🐟"}


def _tier(gross_usd: float) -> str:
    for name, floor in TIERS:
        if gross_usd >= floor:
            return name
    return "Fish"


def compute(mint: str, price_usd: Optional[float] = None,
            max_tx: int = 800, log=print) -> dict:
    if not config.HELIUS_API_KEY:
        return {"available": False, "reason": "Pas de clé Helius"}

    if price_usd is None:
        d = sources_dex.enrich(mint)
        price_usd = d["price_usd"] if d else 0.0
    if not price_usd:
        return {"available": False, "reason": "Prix introuvable"}

    swaps = helius_tx.fetch_swaps(mint, max_tx=max_tx, max_age_days=30)
    if not swaps:
        return {"available": False, "reason": "Aucun swap récupéré"}

    now = time.time()
    windows = {"24h": now - 86400, "7d": now - 7 * 86400, "30d": now - 30 * 86400}

    wallets = {}  # wallet -> {gross, 24h, 7d, 30d, buys, sells}
    for tx in swaps:
        w, delta, ts = helius_tx.parse_swap(tx, mint)
        if not w or delta == 0:
            continue
        usd = abs(delta) * price_usd
        rec = wallets.setdefault(w, {"gross": 0.0, "24h": 0.0, "7d": 0.0,
                                     "30d": 0.0, "buys": 0.0, "sells": 0.0})
        rec["gross"] += usd
        signed = usd if delta > 0 else -usd
        if delta > 0:
            rec["buys"] += usd
        else:
            rec["sells"] += usd
        for wname, cutoff in windows.items():
            if ts >= cutoff:
                rec[wname] += signed

    if not wallets:
        return {"available": False, "reason":
                "Helius ne décode pas les swaps de cet AMM (Meteora DBC / pump AMM récents) — "
                "flux par tier indisponible. Utilise le panneau Flux (buy/sell DexScreener)."}

    tiers = {t[0]: {"24h": 0.0, "7d": 0.0, "30d": 0.0, "count": 0,
                    "buys": 0.0, "sells": 0.0} for t in TIERS}
    for w, rec in wallets.items():
        tname = _tier(rec["gross"])
        for win in ("24h", "7d", "30d"):
            tiers[tname][win] += rec[win]
        tiers[tname]["count"] += 1
        tiers[tname]["buys"] += rec["buys"]
        tiers[tname]["sells"] += rec["sells"]

    totals = {win: sum(r[win] for r in wallets.values())
              for win in ("24h", "7d", "30d")}

    return {
        "available": True,
        "price": price_usd,
        "wallets": len(wallets),
        "tiers": tiers,
        "totals": totals,
        "emoji": TIER_EMOJI,
    }
