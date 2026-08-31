"""
Checker on-demand : on donne un contract address (mint), il renvoie l'analyse
complète (grade, phase, intel MikeMike, RSI, wallets) + le whale flow.
Utilisé par la barre de recherche du dashboard.
"""
import time
from typing import Optional, List

import config
from .model import Pair
from .indicators import rsi, find_swing
from . import sources_gecko as gecko
from . import sources_dex as dex
from . import sources_helius as helius
from . import holder_flow
from . import discover_wallets
from .phases import detect_phase, build_intel
from .scoring import score_pair


def check(mint: str, smart_wallets: Optional[List[str]] = None,
          with_flow: bool = True, log=print):
    """Retourne (Pair, flow_dict) ou (None, {'error':...})."""
    mint = mint.strip()
    d = dex.enrich(mint)
    if not d:
        return None, {"error": "Token introuvable sur DexScreener (mauvais mint ?)"}

    # la chaine vient de DexScreener : la recherche marche sur les 4 chaines
    p = Pair(chain=d.get("chain") or "solana", name=d["name"], symbol=d["symbol"],
             mint=mint, pair_address=d["pair_address"])
    for k, v in d.items():
        if v is not None:
            setattr(p, k, v)
    if p.symbol == "?":
        p.symbol = (p.name or "?").split()[0]

    # OHLCV -> RSI + swings
    pool = gecko.pool_for_token(mint, p.chain)
    if pool:
        p.gecko_pool = pool
        c15 = gecko.ohlcv(pool, "minute", 15, 120, chain=p.chain)
        if c15:
            p.rsi_15m = rsi([c[4] for c in c15])
            p.swing_low, p.swing_high = find_swing(c15, 24)
        ch = gecko.ohlcv(pool, "hour", 1, 160, chain=p.chain)
        if ch:
            cl = [c[4] for c in ch]
            p.rsi_1h = rsi(cl)
            p.rsi_4h = rsi(cl[::4] if len(cl) >= 8 else cl)
        bits = []
        for tf, val in (("15m", p.rsi_15m), ("1h", p.rsi_1h), ("4h", p.rsi_4h)):
            if val is not None:
                bits.append(f"{tf} {val}")
        p.rsi_note = " · ".join(bits)

    # wallets
    if config.HELIUS_API_KEY:
        conc = helius.holder_concentration(mint)
        p.top_holder_pct = conc["top_holder_pct"]
        p.top10_pct = conc["top10_pct"]
        sw = smart_wallets or []
        sm = helius.smart_money_holding(mint, sw)
        p.smart_holders = sm["count"]
        p.smart_names = sm["wallets"]
        from .engine import attach_wallet_detail
        attach_wallet_detail(p, sm.get("addresses"), sm.get("wallets"))
        # accumulation récente (méthode MikeMike)
        acc = discover_wallets.accumulating_now(mint, sw) if sw else {"count": 0}
        p.smart_accumulating = acc.get("count", 0)
        p.wallets_available = True

    p.phase = detect_phase(p)
    p.intel = build_intel(p)
    score_pair(p)
    p.updated_at = time.time()

    flow = {"available": False, "reason": "désactivé"}
    if with_flow:
        try:
            holder_flow.snapshot(mint, p.price_usd)   # photo à chaque consultation
            flow = holder_flow.compute(mint, p.price_usd)
        except Exception as e:
            flow = {"available": False, "reason": str(e)}

    return p, flow
