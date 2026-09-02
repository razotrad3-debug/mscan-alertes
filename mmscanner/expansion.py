"""
MSCAN — Veille d'expansion : alerter au DEPART du mouvement.

Le scan complet decouvre un coin parce qu'il est deja sorti dans les
classements par volume. Quand l'alerte part, la premiere expansion est faite :
MISSION a ete signale a +1588 % sur l'heure, soit une heure trop tard pour
qui trade la seconde jambe.

Cette veille prend le probleme a l'envers. On arme d'abord les coins de
qualite — ceux ou une adresse suivie vient d'entrer, ceux que le radar a
notes — puis on surveille leur prix toutes les 60 s. L'alerte part sur la
PREMIERE impulsion : une bougie de 5 minutes qui decolle alors que l'heure
est encore calme. Le coin est deja passe par les memes filtres de qualite,
on ne fait que le prendre plus tot.
"""
import json
import os
import time
from typing import Dict, List, Optional

import config

WATCH_FILE = config.path("expansion_watch.json")

FENETRE_H = 8.0          # duree pendant laquelle un coin arme reste surveille
SEUIL_M5 = 22.0          # % sur 5 min : le depart
PLAFOND_H1 = 260.0       # au-dela, la premiere jambe est deja faite
MIN_LIQ = 12_000.0
MIN_VOL_M5 = 3_000.0     # dollars vraiment echanges sur la bougie
MIN_ACHATS_M5 = 10       # une impulsion, pas un ordre isole
COOLDOWN_H = 12.0        # on ne resignale pas le meme coin avant ca
MAX_SURVEILLES = 220


# ── etat sur disque ────────────────────────────────────────────────
def _lire() -> dict:
    try:
        with open(WATCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _ecrire(d: dict) -> None:
    try:
        tmp = WATCH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, WATCH_FILE)
    except Exception:
        pass


def armer(mint: str, chain: str = "", symbol: str = "",
          groupes: List[str] = None, source: str = "") -> bool:
    """
    Met un coin sous surveillance rapprochee.

    Appele quand une adresse suivie entre dessus, ou quand le radar lui met
    une note. Sans effet si le coin y est deja.
    """
    if not mint:
        return False
    d = _lire()
    if mint in d:
        # on enrichit ce qu'on sait sans repousser l'echeance
        e = d[mint]
        if groupes:
            e["groupes"] = sorted(set((e.get("groupes") or []) + list(groupes)))
        if symbol and not e.get("symbol"):
            e["symbol"] = symbol
        _ecrire(d)
        return False
    d[mint] = {"at": time.time(), "chain": chain, "symbol": symbol,
               "groupes": list(groupes or []), "source": source}
    _ecrire(d)
    return True


def armer_lot(coins: List[dict], source: str = "") -> int:
    """coins : [{mint, chain, symbol, groupes}] — retourne le nombre d'ajouts."""
    n = 0
    for c in coins or []:
        if armer(c.get("mint"), c.get("chain", ""), c.get("symbol", ""),
                 c.get("groupes"), source):
            n += 1
    return n


# ── message ────────────────────────────────────────────────────────
def _message(e: dict, d: dict) -> str:
    from mmscanner import telegram_alerts as tg
    from mmscanner.model import dex_link, gmgn_link
    from mmscanner.phases import _target_mults

    chain = (d.get("chain") or e.get("chain") or "solana").lower()
    label = config.CHAIN_META.get(chain, {}).get("label", chain.title())
    pastille = tg.PASTILLE.get(chain, "⚪")
    titre = tg._esc(d.get("symbol") or e.get("symbol") or "?")
    mc = d.get("mc") or 0.0

    t1p, t2p, t3p = _target_mults(mc)
    t1, t2, t3 = mc * (1 + t1p), mc * (1 + t2p), mc * (1 + t3p)
    age = max(0, (time.time() - (e.get("at") or 0)) / 60.0)

    lignes = [
        f"{pastille} *{titre}* — 1re EXPANSION",
        f"{label} · repéré il y a {age:.0f} min",
        "",
        f"- Market Cap : `{tg._usd(mc)}`",
        f"- 5 min : `{d.get('chg_m5', 0):+.0f}%`  ·  1h : `{d.get('chg_h1', 0):+.0f}%`",
        "",
        "- Entry : `-20 a -30% du MC`",
        f"- SL : `{tg._usd(mc * 0.70)}`",
        "",
        f"- TP1 : `{tg._usd(t1)}` (+{t1p * 100:.0f}%)",
        f"  TP2 : `{tg._usd(t2)}` (+{t2p * 100:.0f}%)",
        f"  TP3 : `{tg._usd(t3)}` (+{t3p * 100:.0f}%)",
        "",
        "Premiere impulsion en cours — n'entre pas dessus.",
        "La seconde expansion se joue apres le repli.",
    ]
    groupes = e.get("groupes") or []
    if groupes:
        lignes.append(f"👛 {len(groupes)} insider(s) : {tg._esc(', '.join(groupes[:4]))}")

    cible = d.get("pair") or e.get("mint")
    lignes += ["", f"[DexScreener]({dex_link(chain, cible)})"
                   f" · [GMGN]({gmgn_link(chain, e.get('mint'))})"]
    return "\n".join(lignes)


# ── tour de veille ─────────────────────────────────────────────────
def poll(log=print) -> int:
    """Un tour. Retourne le nombre d'alertes envoyees."""
    from mmscanner import holdings, safety, telegram_alerts as tg
    from mmscanner.engine import is_crypto_native

    # un seul emetteur : sans cette garde, l'application de bureau doublerait
    # les alertes du cloud, comme c'est deja arrive
    if not tg.alerts_enabled():
        return 0

    d = _lire()
    if not d:
        return 0

    maintenant = time.time()
    limite = maintenant - FENETRE_H * 3600
    # on oublie les coins armes depuis trop longtemps : passe ce delai, ce
    # n'est plus un depart qu'on guette mais un coin qui n'a rien fait
    vivants = {m: e for m, e in d.items() if (e.get("at") or 0) >= limite}
    if len(vivants) != len(d):
        d = vivants
        _ecrire(d)
    if not d:
        return 0

    mints = list(d)[:MAX_SURVEILLES]
    infos = holdings._metriques(mints)

    envoyees = 0
    for m in mints:
        e, x = d[m], infos.get(m)
        if not x:
            continue
        e["mint"] = m
        dernier = e.get("alerte_at") or 0
        if maintenant - dernier < COOLDOWN_H * 3600:
            continue

        # le depart : la bougie de 5 min decolle, l'heure est encore calme
        if x.get("chg_m5", 0) < SEUIL_M5:
            continue
        if x.get("chg_h1", 0) > PLAFOND_H1:
            continue
        # une vraie impulsion, pas un ordre isole sur un pool vide
        if x.get("liquidity_usd", 0) < MIN_LIQ:
            continue
        if x.get("vol_m5", 0) < MIN_VOL_M5:
            continue
        if x.get("achats_m5", 0) < MIN_ACHATS_M5:
            continue
        # memes garde-fous que partout ailleurs
        if not is_crypto_native(x.get("symbol"), x.get("name"), m):
            continue
        louche, motif = safety.volume_suspect(x.get("liquidity_usd"),
                                              x.get("vol_h24"),
                                              x.get("age_hours"))
        if louche:
            log(f"[expansion] {x.get('symbol')} ecarte : {motif}")
            continue
        if (x.get("chain") or "solana") == "solana":
            aut = safety.authorities(m)
            if not aut.get("inconnu") and not aut.get("ok"):
                log(f"[expansion] {x.get('symbol')} ecarte : autorites actives")
                continue

        if tg.send(_message(e, x)):
            envoyees += 1
            e["alerte_at"] = maintenant
            log(f"[expansion] {x.get('symbol')} — {x.get('chg_m5', 0):+.0f}% "
                f"sur 5 min, 1h a {x.get('chg_h1', 0):+.0f}%")

    _ecrire(d)
    return envoyees


def etat() -> dict:
    """Ce qui est sous surveillance — affiche dans l'interface."""
    d = _lire()
    return {"surveilles": len(d),
            "alertes": sum(1 for e in d.values() if e.get("alerte_at"))}
