"""
Mémoire des smart wallets : qui ils sont, et SUR QUOI ils ont été early.

Chaque wallet retenu garde la trace des coins pumpés sur lesquels il est entré
tôt — c'est la justification (le "pourquoi c'est un smart wallet") exigée par la
méthode MikeMike : on ne suit pas un wallet parce qu'il est riche, mais parce
qu'il est REPETITIVEMENT early avant le move.

Fichier : smart_wallets_data.json
{
  "<adresse>": {
     "label": "...",
     "coins": [ {mint,name,symbol,pump_pct,mc,entry_rank,seen} , ... ],
     "first_seen": ts, "last_seen": ts
  }
}
"""
import json
import os
import time
from typing import Dict, List

import config

STORE = config.WALLET_STORE_FILE


def load() -> Dict:
    if os.path.exists(STORE):
        try:
            with open(STORE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save(data: Dict) -> None:
    try:
        with open(STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except Exception:
        pass


def record(data: Dict, wallet: str, coin: dict, entry_rank: int) -> None:
    """Enregistre qu'un wallet était early (rang `entry_rank`) sur un coin pumpé."""
    w = data.setdefault(wallet, {"label": "", "coins": [],
                                 "first_seen": time.time(), "last_seen": time.time()})
    w["last_seen"] = time.time()
    if any(c.get("mint") == coin.get("mint") for c in w["coins"]):
        return
    w["coins"].append({
        "mint": coin.get("mint", ""),
        "name": coin.get("name", "?"),
        "symbol": coin.get("symbol", ""),
        "pump_pct": round(float(coin.get("pump_pct") or 0)),
        "mc": float(coin.get("mc") or 0),
        "entry_rank": entry_rank,
        "seen": time.time(),
    })


def grade_wallet(count: int, avg_pump: float, avg_rank: float, last_seen: float) -> tuple:
    """
    Note un wallet /10 selon la logique MikeMike : ce qui compte n'est pas
    la taille du portefeuille, c'est la REPETITION et la PRECOCITE des entrees.

      · recurrence  — sur combien de pumps il etait deja dedans
      · amplitude   — de quelle taille etaient ces pumps
      · precocite   — a quel rang d'acheteur il est entre (plus bas = plus tot)
      · fraicheur   — est-il encore actif aujourd'hui

    Retourne (score, grade).
    """
    s = 0
    # recurrence : le critere le plus lourd, un coup de chance ne se repete pas
    if count >= 8:   s += 4
    elif count >= 5: s += 3
    elif count >= 3: s += 2
    elif count >= 2: s += 1
    # amplitude moyenne des pumps ou il etait present
    if avg_pump >= 300:   s += 3
    elif avg_pump >= 150: s += 2
    elif avg_pump >= 60:  s += 1
    # precocite d'entree
    if avg_rank and avg_rank <= 10:   s += 2
    elif avg_rank and avg_rank <= 30: s += 1
    # actif dans les 7 derniers jours
    if last_seen and (time.time() - last_seen) < 7 * 86400:
        s += 1

    if s >= 9:   g = "A+"
    elif s >= 7: g = "A"
    elif s >= 6: g = "A-"
    elif s >= 5: g = "B+"
    elif s >= 3: g = "B"
    else:        g = "C"
    return s, g


def ranked(data: Dict = None, min_coins: int = 2) -> List[dict]:
    """Wallets triés par nb de pumps où ils étaient early (les plus récurrents devant)."""
    data = data if data is not None else load()
    out = []
    for addr, w in data.items():
        coins = w.get("coins", [])
        if len(coins) < min_coins:
            continue
        coins = sorted(coins, key=lambda c: c.get("pump_pct", 0), reverse=True)
        avg_pump = round(sum(c.get("pump_pct", 0) for c in coins) / len(coins)) if coins else 0
        avg_rank = round(sum(c.get("entry_rank", 0) for c in coins) / len(coins)) if coins else 0
        last_seen = w.get("last_seen", 0)
        score, grade = grade_wallet(len(coins), avg_pump, avg_rank, last_seen)
        out.append({
            "score": score,
            "grade": grade,
            "recent": sorted(coins, key=lambda c: c.get("seen", 0), reverse=True)[:3],
            "address": addr,
            "label": w.get("label") or "",
            "short": addr[:4] + "…" + addr[-4:],
            "coins": coins,
            "count": len(coins),
            "best": coins[0] if coins else None,
            "avg_pump": round(sum(c.get("pump_pct", 0) for c in coins) / len(coins)) if coins else 0,
            "avg_rank": round(sum(c.get("entry_rank", 0) for c in coins) / len(coins)) if coins else 0,
            "last_seen": w.get("last_seen", 0),
        })
    out.sort(key=lambda w: (w["score"], w["count"], w["avg_pump"]), reverse=True)
    return out


def coins_for(address: str, data: Dict = None) -> List[dict]:
    data = data if data is not None else load()
    w = data.get(address) or {}
    return sorted(w.get("coins", []), key=lambda c: c.get("pump_pct", 0), reverse=True)


def export_watchlist(min_coins: int = 2, top_n: int = None) -> int:
    """
    Ecrit les meilleurs wallets dans smart_wallets.txt (consomme par le scanner).

    On plafonne volontairement : chaque wallet exporte coute une requete Helius
    PAR coin analyse. Une liste de 289 wallets, c'etait 7 000 requetes par scan
    pour un signal qui vient de la vingtaine de tetes de liste. La methode
    MikeMike suit les wallets recurrents, pas tous les wallets.
    """
    if top_n is None:
        top_n = getattr(config, "WATCHLIST_MAX", 60)
    rows = ranked(min_coins=min_coins)[:top_n]
    lines = ["# Généré par `python -m mmscanner.discover_wallets`",
             "# adresse  label — early sur N pumps\n"]
    for w in rows:
        coins = ", ".join(f"{c['symbol'] or c['name']}+{c['pump_pct']}%" for c in w["coins"][:3])
        lines.append(f"{w['address']}  early_x{w['count']} ({coins})")
    try:
        with open(config.SMART_WALLETS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        return 0
    return len(rows)
