"""
Découverte AUTOMATIQUE de smart wallets — la méthode MikeMike (cours, Module 07) :

  1) Prendre les coins qui ont RÉCEMMENT PERCÉ (gros move, jeunes, liquides).
  2) Pour chacun, lire les EARLY BUYERS on-chain (les N premiers acheteurs).
  3) Garder ceux qui reviennent sur PLUSIEURS pumps différents (récurrence).
  4) MÉMORISER pourquoi : sur quels coins, à quel rang d'entrée, pour quel pump.

Lancement :  python -m mmscanner.discover_wallets
Sorties   :  smart_wallets_data.json (détail)  +  smart_wallets.txt (watchlist)
"""
import json
import os
import time
from typing import List

import config
from . import sources_gecko as gecko
from . import sources_dex as dex
from . import helius_tx, wallet_store, holder_flow


def _is_meme(symbol: str, name: str, mc: float) -> bool:
    from .engine import is_crypto_native
    return is_crypto_native(symbol, name, "", mc)


def recently_pumped(min_mult: float = 3.0, max_age_h: float = 72,
                    min_liq: float = 30_000, limit: int = 25, log=print) -> List[dict]:
    """Coins jeunes ayant fait >= min_mult x sur 24h et encore liquides."""
    pools = gecko.discover(pages=3, include_new=True, include_trending=True)
    picks = []
    for p in pools:
        age = p.get("age_hours")
        chg = p.get("chg_h24_g", 0) or 0
        if p["liquidity_usd"] < min_liq:
            continue
        if age is not None and age > max_age_h:
            continue
        if chg >= (min_mult - 1) * 100:
            picks.append(p)
    picks.sort(key=lambda x: x.get("chg_h24_g", 0), reverse=True)
    log(f"[pumped] {len(picks)} coins qui ont percé")
    return picks[:limit]


def early_buyers(mint: str, first_n: int = 60) -> List[str]:
    """Les `first_n` premiers acheteurs uniques (ordre chronologique)."""
    swaps = helius_tx.fetch_swaps(mint, max_tx=1000, max_age_days=90)
    buys = []
    for tx in swaps:
        w, delta, ts = helius_tx.parse_swap(tx, mint)
        if w and delta > 0:
            buys.append((ts, w))
    buys.sort(key=lambda x: x[0])
    seen, early = set(), []
    for ts, w in buys:
        if w in seen:
            continue
        seen.add(w)
        early.append(w)
        if len(early) >= first_n:
            break
    return early


def big_holders(mint: str, top_n: int = 40, min_share: float = 0.002) -> List[str]:
    """
    Gros porteurs ACTUELS d'un coin (>= min_share du supply), hors pool de liquidité.

    Source fiable (API DAS) — complète early_buyers, dont les données de
    transactions sont partielles sur certains AMM. Un wallet qui détient une
    position significative sur PLUSIEURS coins qui viennent de pumper est,
    par définition, de la smart money.
    """
    bal = holder_flow.balances(mint)
    if not bal:
        return []
    supply = bal.pop("__supply__", 0.0) or sum(bal.values())
    if supply <= 0:
        return []
    ranked = sorted(bal.items(), key=lambda kv: -kv[1])
    # le plus gros compte est presque toujours le pool -> on l'écarte
    if ranked and (ranked[0][1] / supply) > 0.30:
        ranked = ranked[1:]
    return [owner for owner, amt in ranked[:top_n] if (amt / supply) >= min_share]


def discover(min_recurrence: int = 2, log=print):
    """Scanne les pumps récents, mémorise les early buyers, retourne le classement."""
    data = wallet_store.load()
    pumped = recently_pumped(log=log)
    for i, p in enumerate(pumped, 1):
        log(f"[{i}/{len(pumped)}] early buyers · {p['name'][:24]}")
        info = dex.enrich(p["mint"]) or {}
        if not _is_meme(info.get("symbol"), info.get("name") or p.get("name"),
                        info.get("market_cap", 0)):
            log("     -> ignoré (stock/major hors univers memecoin)")
            continue
        coin = {
            "mint": p["mint"],
            "name": info.get("name") or p.get("name") or "?",
            "symbol": info.get("symbol") or "",
            "pump_pct": info.get("chg_h24", p.get("chg_h24_g", 0)),
            "mc": info.get("market_cap", p.get("market_cap", 0)),
        }
        eb = early_buyers(p["mint"])
        for rank, w in enumerate(eb, 1):
            wallet_store.record(data, w, coin, rank)
        # second signal (fiable) : les gros porteurs actuels
        bh = big_holders(p["mint"])
        for w in bh:
            wallet_store.record(data, w, coin, 0)   # rang 0 = détecté par position
        log(f"     {len(eb)} early buyers · {len(bh)} gros porteurs")
        time.sleep(0.3)

    wallet_store.save(data)
    ranked = wallet_store.ranked(data, min_coins=min_recurrence)
    log(f"[discover] {len(ranked)} wallets récurrents (>= {min_recurrence} pumps)")
    return ranked


def accumulating_now(mint: str, wallets: List[str], lookback_h: float = 6) -> dict:
    """Wallets suivis ayant ACHETÉ ce coin dans les dernières `lookback_h` heures."""
    if not config.HELIUS_API_KEY or not wallets:
        return {"count": 0, "wallets": []}
    watch = set(w.strip().split()[0] for w in wallets if w.strip())
    cutoff = time.time() - lookback_h * 3600
    swaps = helius_tx.fetch_swaps(mint, max_tx=400, max_age_days=2)
    buyers = {}
    for tx in swaps:
        w, delta, ts = helius_tx.parse_swap(tx, mint)
        if w in watch and delta > 0 and ts >= cutoff:
            buyers[w] = buyers.get(w, 0) + delta
    return {"count": len(buyers), "wallets": list(buyers.keys())}


def _last_run() -> float:
    try:
        with open(config.DISCOVER_STATE_FILE, "r", encoding="utf-8") as f:
            return float(json.load(f).get("last_run", 0))
    except Exception:
        return 0.0


def _mark_run() -> None:
    try:
        with open(config.DISCOVER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_run": time.time()}, f)
    except Exception:
        pass


def due(interval_h: float = None) -> bool:
    """Vrai s'il est temps de relancer la decouverte."""
    interval_h = interval_h if interval_h is not None else config.DISCOVER_INTERVAL_H
    return (time.time() - _last_run()) >= interval_h * 3600


def run_if_due(log=print) -> int:
    """
    Relance la decouverte si l'intervalle est ecoule, puis reexporte la watchlist.

    Appelee par la boucle de scan : les smart wallets se mettent donc a jour
    tout seuls au fil des nouveaux coins qui percent. Ne leve jamais : une
    erreur de decouverte ne doit pas casser le scan.
    """
    if not getattr(config, "AUTO_DISCOVER_WALLETS", False):
        return 0
    if not config.HELIUS_API_KEY or not due():
        return 0
    try:
        log("[wallets] decouverte automatique en cours...")
        ranked = discover(log=log)
        n = wallet_store.export_watchlist()
        _mark_run()
        log(f"[wallets] watchlist mise a jour : {n} wallets ({len(ranked)} recurrents)")
        return n
    except Exception as e:
        log(f"[wallets] decouverte echouee : {e}")
        _mark_run()      # on n'insiste pas avant le prochain intervalle
        return 0


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not config.HELIUS_API_KEY:
        print("HELIUS_API_KEY manquante — impossible de lire les wallets on-chain.")
        return
    ranked = discover()
    n = wallet_store.export_watchlist()
    print(f"\n{len(ranked)} wallets mémorisés · {n} exportés vers {config.SMART_WALLETS_FILE}\n")
    for w in ranked[:15]:
        coins = ", ".join(f"{c['symbol'] or c['name']} +{c['pump_pct']}%" for c in w["coins"][:3])
        print(f"  x{w['count']}  {w['short']}  →  {coins}")


if __name__ == "__main__":
    main()
