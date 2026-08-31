"""
WHALE FLOW — méthode "sun-flow" : suivre l'argent, pas les bougies.

Principe (et pourquoi c'est fait comme ça) :
  Parser chaque swap est impossible de façon fiable — Helius ne décode pas les
  AMM récents (Meteora DBC, pump AMM) : les tx reviennent en "UNKNOWN" sans
  transferts. En revanche on peut lire, à tout instant, le SOLDE EXACT de tous
  les holders (API DAS). On prend donc des PHOTOS régulières des soldes, et le
  flux = la DIFFÉRENCE entre deux photos.

  Photo(t2) - Photo(t1) = qui a accumulé, qui a distribué, et combien.

C'est plus robuste que le parsing de swaps : ça capture les achats, les ventes,
les transferts et les routages, quel que soit le DEX utilisé.

Sortie : Net USD Flow sur 24h / 7j / 30j, ventilé par catégorie de wallet
(Whale / Shark / Dolphin / Fish), + le nombre de holders et son évolution.
"""
import json
import os
import time
from typing import Dict, List, Optional

import config
from . import sources_helius as helius

SNAP_DIR = config.SNAPSHOT_DIR
MAX_SNAPS = 60          # ~30 j à 2 photos/jour
MIN_GAP_SEC = 1800      # ne pas re-photographier plus d'une fois / 30 min

# Catégories par PART DU SUPPLY (et non en USD fixe) : un "whale" sur un coin à
# 300k n'a pas la même taille que sur un coin à 30M — la part relative, si.
TIERS = [("Whale", 0.01), ("Shark", 0.0025), ("Dolphin", 0.0005), ("Fish", 0.0)]
TIER_NAMES = [t[0] for t in TIERS]
TIER_LABELS = {"Whale": "≥ 1% supply", "Shark": "≥ 0.25%",
               "Dolphin": "≥ 0.05%", "Fish": "< 0.05%"}


def _tier(share: float) -> str:
    for name, floor in TIERS:
        if share >= floor:
            return name
    return "Fish"


def _path(mint: str) -> str:
    os.makedirs(SNAP_DIR, exist_ok=True)
    return os.path.join(SNAP_DIR, f"{mint}.json")


def _load(mint: str) -> List[dict]:
    try:
        with open(_path(mint), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(mint: str, snaps: List[dict]) -> None:
    try:
        with open(_path(mint), "w", encoding="utf-8") as f:
            json.dump(snaps[-MAX_SNAPS:], f)
    except Exception:
        pass


def balances(mint: str, max_pages: int = 4) -> Optional[Dict[str, float]]:
    """Solde de chaque owner (agrégé sur ses token accounts), via l'API DAS."""
    if not config.HELIUS_API_KEY:
        return None
    supply = helius._rpc("getTokenSupply", [mint])
    if not supply:
        return None
    try:
        decimals = int(supply["value"]["decimals"])
    except Exception:
        return None

    try:
        total_supply = float(supply["value"]["uiAmount"] or 0)
    except Exception:
        total_supply = 0.0

    out: Dict[str, float] = {}
    for page in range(1, max_pages + 1):
        res = helius._das("getTokenAccounts", {
            "mint": mint, "limit": 1000, "page": page,
            "options": {"showZeroBalance": False},
        })
        if not res:
            return None if page == 1 else out
        batch = res.get("token_accounts", []) or []
        for a in batch:
            owner = a.get("owner")
            amt = float(a.get("amount") or 0) / (10 ** decimals)
            if owner and amt > 0:
                out[owner] = out.get(owner, 0.0) + amt
        if len(batch) < 1000:
            break
        time.sleep(0.1)
    out["__supply__"] = total_supply
    return out


def snapshot(mint: str, price_usd: float, force: bool = False) -> bool:
    """Prend une photo des soldes si la dernière est assez ancienne."""
    snaps = _load(mint)
    if snaps and not force and (time.time() - snaps[-1].get("ts", 0)) < MIN_GAP_SEC:
        return False
    bal = balances(mint)
    if not bal:
        return False
    supply = bal.pop("__supply__", 0.0)
    snaps.append({"ts": time.time(), "price": price_usd or 0.0,
                  "supply": supply, "holders": bal})
    _save(mint, snaps)
    return True


def _nearest(snaps: List[dict], target_ts: float) -> Optional[dict]:
    """Photo la plus proche d'un instant donné (tolérance : moitié de la fenêtre)."""
    if not snaps:
        return None
    best = min(snaps, key=lambda s: abs(s.get("ts", 0) - target_ts))
    span = max(3600.0, (time.time() - target_ts) * 0.5)
    return best if abs(best.get("ts", 0) - target_ts) <= span else None


def compute(mint: str, price_usd: float = None) -> dict:
    """Net USD flow par tier sur 24h / 7j / 30j, à partir des photos successives."""
    snaps = _load(mint)
    if len(snaps) < 2:
        return {"available": False, "snapshots": len(snaps),
                "reason": ("Historique en constitution — le flux apparaît dès la 2e photo "
                           "(une photo par scan, max une toutes les 30 min).")}

    last = snaps[-1]
    price = price_usd or last.get("price") or 0.0
    now_bal = last["holders"]
    now_ts = last["ts"]
    supply = last.get("supply") or sum(now_bal.values()) or 1.0

    windows = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
    prev_snap = snaps[-2]
    span_recent = max(1.0, now_ts - prev_snap["ts"])
    tiers = {t: {w: 0.0 for w in list(windows) + ["recent"]} | {"count": 0} for t in TIER_NAMES}
    totals = {w: 0.0 for w in list(windows) + ["recent"]}
    covered = {}

    # catégorie = part du supply détenue actuellement
    for owner, amt in now_bal.items():
        tiers[_tier(amt / supply)]["count"] += 1

    for wname, secs in windows.items():
        past = _nearest(snaps[:-1], now_ts - secs)
        if not past:
            covered[wname] = False
            continue
        covered[wname] = True
        past_bal = past["holders"]
        for owner in set(now_bal) | set(past_bal):
            delta = now_bal.get(owner, 0.0) - past_bal.get(owner, 0.0)
            if delta == 0:
                continue
            usd = delta * price
            tiers[_tier(now_bal.get(owner, 0.0) / supply)][wname] += usd
            totals[wname] += usd

    # flux depuis la photo precedente : disponible des qu'on a 2 photos,
    # sans attendre 24 h — c'est ce qui rend l'onglet utile tout de suite.
    prev_bal = prev_snap["holders"]
    for owner in set(now_bal) | set(prev_bal):
        delta = now_bal.get(owner, 0.0) - prev_bal.get(owner, 0.0)
        if delta == 0:
            continue
        usd = delta * price
        tiers[_tier(now_bal.get(owner, 0.0) / supply)]["recent"] += usd
        totals["recent"] += usd
    covered["recent"] = True

    prev = snaps[-2]
    return {
        "available": True,
        "snapshots": len(snaps),
        "price": price,
        "holders": len(now_bal),
        "holders_delta": len(now_bal) - len(prev["holders"]),
        "since": now_ts - snaps[0]["ts"],
        "recent_span": span_recent,
        "supply": supply,
        "labels": TIER_LABELS,
        "tiers": tiers,
        "totals": totals,
        "covered": covered,
        "signal": _signal(tiers, covered),
    }


def _signal(tiers: dict, covered: dict) -> str:
    """Lecture façon MikeMike : qui accumule, qui distribue."""
    win = "24h" if covered.get("24h") else ("recent" if covered.get("recent") else None)
    if not win:
        return ""
    w = tiers["Whale"][win] + tiers["Shark"][win]
    f = tiers["Fish"][win] + tiers["Dolphin"][win]
    if w > 0 and abs(w) > abs(f):
        return "Accumulation whales — l'argent fort entre."
    if w < 0 and f > 0:
        return "Distribution — les whales vendent au retail. Prudence."
    if w < 0:
        return "Sorties whales — l'argent fort se retire."
    return "Flux équilibré — pas de signal net."
