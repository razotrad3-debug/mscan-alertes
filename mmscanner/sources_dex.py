"""
DexScreener — enrichissement d'un token : MC, liquidité, volumes (24h/6h/1h/5m),
price change (5m/1h/6h/24h), buys/sells, âge du pair. API gratuite, sans clé.
"""
import time
import requests
from typing import Optional, Dict

BASE = "https://api.dexscreener.com/latest/dex"


def _get(url: str, tries: int = 3) -> Optional[dict]:
    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None


CHAINS = ("solana", "robinhood", "base", "ethereum")


def _best_pair(pairs: list, chains=None) -> Optional[dict]:
    """Pair le plus liquide, sur l'une des chaines suivies."""
    allowed = set(chains or CHAINS)
    ok = [p for p in pairs if p.get("chainId") in allowed]
    if not ok:
        return None
    return max(ok, key=lambda p: (p.get("liquidity", {}) or {}).get("usd", 0) or 0)


def enrich(mint: str) -> Optional[Dict]:
    """Retourne un dict de métriques normalisées pour un mint, ou None."""
    data = _get(f"{BASE}/tokens/{mint}")
    if not data:
        return None
    p = _best_pair(data.get("pairs") or [])
    if not p:
        return None
    chain = p.get("chainId") or "solana"
    vol = p.get("volume", {}) or {}
    chg = p.get("priceChange", {}) or {}
    tx = p.get("txns", {}) or {}
    txh1 = tx.get("h1", {}) or {}
    liq = (p.get("liquidity", {}) or {}).get("usd", 0) or 0.0
    created = p.get("pairCreatedAt")
    age_h = None
    if created:
        age_h = max(0.0, (time.time() - created / 1000.0) / 3600.0)
    base = p.get("baseToken", {}) or {}
    return {
        "chain": chain,
        "name": base.get("name") or base.get("symbol") or "?",
        "symbol": base.get("symbol") or "?",
        "pair_address": p.get("pairAddress", ""),
        "price_usd": float(p.get("priceUsd") or 0) or 0.0,
        "market_cap": float(p.get("marketCap") or p.get("fdv") or 0) or 0.0,
        "fdv": float(p.get("fdv") or 0) or 0.0,
        "liquidity_usd": float(liq),
        "vol_h24": float(vol.get("h24") or 0) or 0.0,
        "vol_h6": float(vol.get("h6") or 0) or 0.0,
        "vol_h1": float(vol.get("h1") or 0) or 0.0,
        "vol_m5": float(vol.get("m5") or 0) or 0.0,
        "chg_m5": float(chg.get("m5") or 0) or 0.0,
        "chg_h1": float(chg.get("h1") or 0) or 0.0,
        "chg_h6": float(chg.get("h6") or 0) or 0.0,
        "chg_h24": float(chg.get("h24") or 0) or 0.0,
        "buys_h1": int(txh1.get("buys") or 0),
        "sells_h1": int(txh1.get("sells") or 0),
        "buys_m5": int((tx.get("m5") or {}).get("buys") or 0),
        "sells_m5": int((tx.get("m5") or {}).get("sells") or 0),
        "buys_h6": int((tx.get("h6") or {}).get("buys") or 0),
        "sells_h6": int((tx.get("h6") or {}).get("sells") or 0),
        "buys_h24": int((tx.get("h24") or {}).get("buys") or 0),
        "sells_h24": int((tx.get("h24") or {}).get("sells") or 0),
        "age_hours": age_h,
    }


# ── Découverte : endpoints publics DexScreener (couverture complémentaire) ──
DISCOVERY_ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
    "https://api.dexscreener.com/token-boosts/top/v1",
]


def discover_mints(chains=None) -> list:
    """Mints Solana remontés par les endpoints de découverte DexScreener.

    Complète GeckoTerminal : ces listes contiennent souvent des memecoins
    fraîchement actifs qui ne sont pas encore dans les tops par volume.
    """
    allowed = set(chains or CHAINS)
    seen, out = set(), []
    for url in DISCOVERY_ENDPOINTS:
        data = _get(url)
        if not isinstance(data, list):
            continue
        for item in data:
            if item.get("chainId") not in allowed:
                continue
            mint = item.get("tokenAddress")
            if mint and mint not in seen:
                seen.add(mint)
                out.append(mint)
    return out
