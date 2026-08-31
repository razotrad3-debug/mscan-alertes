"""
GeckoTerminal — découverte des pools SOL (classés par volume, nouveaux, trending)
et OHLCV pour calculer le RSI et les swings. API gratuite, rate-limit ~30 req/min.
"""
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import requests
from typing import List, Dict, Optional

import config

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}
NET = "solana"
# correspondance chaine DexScreener -> identifiant reseau GeckoTerminal
GECKO_NET = {"solana": "solana", "ethereum": "eth", "base": "base"}


def net_for(chain: str):
    """Reseau GeckoTerminal pour une chaine, ou None si non couvert (ex: robinhood)."""
    return GECKO_NET.get((chain or "solana").lower())


# GeckoTerminal free tier : la limite reelle n'est pas documentee precisement
# et varie. On utilise donc un SEAU A JETONS ADAPTATIF :
#   · fenetre glissante de 60 s, budget _budget appels
#   · chaque 429 reduit le budget (on tapait trop fort)
#   · une serie de succes le fait remonter doucement
# Resultat : on trouve tout seul le debit maximal soutenable, sans se faire
# blacklister, et on parallelise sans risque.
RATE_MIN, RATE_MAX = 10, 26
RATE_WINDOW = 60.0
_budget = [12]           # on demarre BAS et on monte : partir a 22 declenchait
                         # une salve de 429 des la decouverte, et le budget
                         # retombait au plancher pour tout le reste du scan.
_ok_streak = [0]
_429 = [0]
_calls = deque()
_rate_lock = threading.Lock()


_last_call = [0.0]


def _throttle():
    """
    Debit LISSE, pas en rafale.

    L'ancienne version laissait partir les 22 appels du budget d'un coup en
    debut de fenetre : GeckoTerminal repondait 429, le budget s'effondrait au
    plancher et le scan doublait de duree. On impose maintenant un intervalle
    minimal de 60/budget secondes entre deux appels, en plus du plafond par
    fenetre. Meme debit theorique, mais etale — donc accepte.
    """
    while True:
        with _rate_lock:
            now = time.time()
            while _calls and now - _calls[0] >= RATE_WINDOW:
                _calls.popleft()

            espacement = RATE_WINDOW / max(1, _budget[0])
            depuis = now - _last_call[0]
            if len(_calls) < _budget[0] and depuis >= espacement:
                _calls.append(now)
                _last_call[0] = now
                return

            attente = espacement - depuis
            if len(_calls) >= _budget[0]:
                attente = max(attente, RATE_WINDOW - (now - _calls[0]) + 0.05)
        time.sleep(max(0.02, min(attente, 5.0)))


def _on_429():
    """On tape trop fort : on reduit le budget d'un cran."""
    with _rate_lock:
        _429[0] += 1
        _ok_streak[0] = 0
        _budget[0] = max(RATE_MIN, _budget[0] - 1)   # recul d'un cran, pas d'effondrement


def _on_ok():
    """Serie de succes : on re-augmente prudemment."""
    with _rate_lock:
        _ok_streak[0] += 1
        if _ok_streak[0] >= 5 and _budget[0] < RATE_MAX:
            _budget[0] += 1
            _ok_streak[0] = 0


def rate_state():
    return {"budget": _budget[0], "rate_limits": _429[0]}


def _get(url: str, params: dict = None, tries: int = 3) -> Optional[dict]:
    for attempt in range(tries):
        try:
            _throttle()
            r = requests.get(url, params=params, headers=HEADERS, timeout=25)
            if r.status_code == 429:
                _on_429()
                # le serveur dit combien de temps attendre : on l'ecoute
                ra = r.headers.get("Retry-After")
                try:
                    pause = min(float(ra), 30.0) if ra else 2.0 * (attempt + 1)
                except Exception:
                    pause = 2.0 * (attempt + 1)
                time.sleep(pause)
                continue
            r.raise_for_status()
            _on_ok()
            return r.json()
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return None


def _parse_pool(item: dict) -> Optional[Dict]:
    try:
        a = item.get("attributes", {})
        rel = item.get("relationships", {})
        base_id = rel.get("base_token", {}).get("data", {}).get("id", "")
        mint = base_id.split("_", 1)[1] if "_" in base_id else base_id
        if not mint:
            return None
        vol = a.get("volume_usd", {}) or {}
        pc = a.get("price_change_percentage", {}) or {}
        created = a.get("pool_created_at")
        age_h = None
        if created:
            try:
                t = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
                age_h = max(0.0, (time.time() - t) / 3600.0)
            except Exception:
                age_h = None
        return {
            "gecko_pool": a.get("address"),
            "name": a.get("name", ""),
            "mint": mint,
            "price_usd": float(a.get("base_token_price_usd") or 0) or 0.0,
            "liquidity_usd": float(a.get("reserve_in_usd") or 0) or 0.0,
            "market_cap": float(a.get("market_cap_usd") or 0) or 0.0,
            "fdv": float(a.get("fdv_usd") or 0) or 0.0,
            "vol_h24": float(vol.get("h24") or 0) or 0.0,
            "chg_h24_g": float(pc.get("h24") or 0) or 0.0,
            "age_hours": age_h,
        }
    except Exception:
        return None


def _list(endpoint: str, params: dict, pages: int = 1) -> List[Dict]:
    out = []
    for p in range(1, pages + 1):
        q = dict(params or {})
        q["page"] = p
        data = _get(f"{BASE}/networks/{NET}/{endpoint}", q)
        if not data:
            break
        for item in data.get("data", []):
            parsed = _parse_pool(item)
            if parsed:
                out.append(parsed)
    return out


def discover(pages: int = 2, include_new: bool = True, include_trending: bool = True,
             dexes: List[str] = None, dex_pages: int = 2) -> List[Dict]:
    """Univers de candidats : DEX memecoin + top volume + trending + nouveaux."""
    # toutes les listes sont tirees en parallele : le seau a jetons empeche
    # de depasser le quota, donc autant remplir la fenetre au maximum.
    jobs = [(f"dexes/{d}/pools", {"sort": "h24_volume_usd_desc"}, dex_pages)
            for d in (dexes or [])]
    jobs.append(("pools", {"sort": "h24_volume_usd_desc"}, pages))
    if include_trending:
        jobs.append(("trending_pools", {}, 1))
    if include_new:
        jobs.append(("new_pools", {}, 2))

    pools: List[Dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda j: _list(j[0], j[1], j[2]), jobs):
            pools += res
    dedup: Dict[str, Dict] = {}
    for pool in pools:
        m = pool["mint"]
        # garde la version la plus liquide si doublon
        if m not in dedup or pool["liquidity_usd"] > dedup[m]["liquidity_usd"]:
            dedup[m] = pool
    return list(dedup.values())


_POOL_CACHE_FILE = config.POOL_CACHE_FILE
_pool_cache = None


def _load_pool_cache() -> dict:
    global _pool_cache
    if _pool_cache is None:
        try:
            import json
            with open(_POOL_CACHE_FILE, "r", encoding="utf-8") as f:
                _pool_cache = json.load(f)
        except Exception:
            _pool_cache = {}
    return _pool_cache


def _save_pool_cache() -> None:
    try:
        import json
        with open(_POOL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_pool_cache or {}, f)
    except Exception:
        pass


def pool_for_token(mint: str, chain: str = "solana") -> Optional[str]:
    """Adresse du pool le plus liquide d'un mint (mise en cache : un pool ne change pas)."""
    net = net_for(chain)
    if not net:
        return None
    cache = _load_pool_cache()
    key = f"{net}:{mint}"
    if key in cache:
        return cache[key]
    data = _get(f"{BASE}/networks/{net}/tokens/{mint}/pools", {"page": 1})
    if not data:
        return None
    best, best_liq = None, -1.0
    for item in data.get("data", []):
        a = item.get("attributes", {})
        liq = float(a.get("reserve_in_usd") or 0)
        if liq > best_liq:
            best_liq, best = liq, a.get("address")
    if best:
        cache[key] = best
        _save_pool_cache()
    return best


_OHLCV_TTL = 240.0          # 4 min : plus court qu'une bougie 15m, donc sans perte
_ohlcv_cache: Dict[str, tuple] = {}


def ohlcv(pool: str, timeframe: str = "minute", aggregate: int = 15, limit: int = 120,
          chain: str = "solana") -> List[list]:
    """
    Récupère l'OHLCV d'un pool. timeframe ∈ {minute, hour, day}.
    Retourne une liste [ts, o, h, l, c, v] triée par temps croissant.
    """
    net = net_for(chain)
    if not net:
        return []
    # deux scans rapproches sur la meme bougie 15m : inutile de redemander
    ck = f"{net}:{pool}:{timeframe}:{aggregate}:{limit}"
    hit = _ohlcv_cache.get(ck)
    if hit and (time.time() - hit[0]) < _OHLCV_TTL:
        return hit[1]
    url = f"{BASE}/networks/{net}/pools/{pool}/ohlcv/{timeframe}"
    data = _get(url, {"aggregate": aggregate, "limit": limit})
    if not data:
        return []
    rows = data.get("data", {}).get("attributes", {}).get("ohlcv_list", []) or []
    rows = sorted(rows, key=lambda r: r[0])  # ancien -> récent
    if rows:
        _ohlcv_cache[ck] = (time.time(), rows)
    return rows
