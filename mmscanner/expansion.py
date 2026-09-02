"""
MSCAN — Veille d'expansion : alerter au DEPART du mouvement.

Le scan complet decouvre un coin parce qu'il est deja sorti dans les
classements par volume. Quand l'alerte part, la premiere expansion est faite :
MISSION a ete signale a +1588 % sur l'heure, soit une heure trop tard pour
qui trade la seconde jambe.

Cette veille prend le probleme a l'envers. On arme d'abord les coins de
qualite — ceux ou une adresse suivie vient d'entrer, ceux que le radar a
notes — puis on suit leur prix toutes les 60 s, en deux temps :

  1. l'IMPULSION : la premiere expansion, qu'on note sans rien envoyer, en
     retenant le plus haut atteint ;
  2. le REPLI : quand le prix redescend de 18 a 50 % sous ce haut et que la
     baisse se calme, l'alerte part. C'est la zone d'entree de la seconde
     expansion, celle qui se trade.

On ne signale donc jamais une bougie en train de monter : le message arrive
quand il y a quelque chose a faire.
"""
import json
import os
import time
from typing import Dict, List, Optional

import config

WATCH_FILE = config.path("expansion_watch.json")

FENETRE_H = 10.0         # duree pendant laquelle un coin arme reste surveille
SEUIL_M5 = 20.0          # % sur 5 min qui signale l'impulsion
IMPULSION_X = 1.45       # ou : le MC a pris 45 % sur sa base depuis l'armement
RETRACE_MIN = 0.18       # repli minimum sous le haut pour parler d'entree
RETRACE_MAX = 0.50       # au-dela, ce n'est plus un repli mais un abandon
STAB_M5 = -6.0           # la chute doit se calmer : pas de couteau qui tombe
MARGE_BASE = 1.12        # le repli doit rester au-dessus de la base de depart
MIN_LIQ = 12_000.0
MIN_VOL_M5 = 1_500.0     # dollars echanges sur la bougie du repli
MIN_ACHATS_M5 = 5        # des acheteurs reviennent deja
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
    haut = e.get("haut_mc") or mc
    base = e.get("base_mc") or 0.0
    repli = (1 - mc / haut) * 100 if haut else 0.0
    depuis = max(0, (time.time() - (e.get("impulsion_at") or 0)) / 60.0)

    # stop sous la base de l'impulsion : si le prix y revient, la jambe a
    # echoue. Jamais plus loin que -30 % pour que le risque reste tenable.
    sl = max(base * 0.95, mc * 0.70)
    t1p, t2p, t3p = _target_mults(mc)
    t1 = max(haut, mc * (1 + t1p))          # premier objectif : le haut repris
    t2, t3 = mc * (1 + t2p), mc * (1 + t3p)

    def _gain(v):
        return f"`{tg._usd(v)}` (+{(v / mc - 1) * 100:.0f}%)" if mc else f"`{tg._usd(v)}`"

    lignes = [
        f"{pastille} *{titre}* — REPLI APRES 1re EXPANSION",
        f"{label} · impulsion il y a {depuis:.0f} min",
        "",
        f"- Market Cap : `{tg._usd(mc)}`  (haut : `{tg._usd(haut)}`)",
        f"- Repli : `-{repli:.0f}%` sous le haut",
        f"- 5 min : `{d.get('chg_m5', 0):+.0f}%`  ·  1h : `{d.get('chg_h1', 0):+.0f}%`",
        "",
        "- Entry : `ici, sur le repli`",
        f"- SL : `{tg._usd(sl)}`",
        "",
        f"- TP1 : {_gain(t1)}",
        f"  TP2 : {_gain(t2)}",
        f"  TP3 : {_gain(t3)}",
        "",
        "La premiere expansion est passee, le repli se calme.",
        "C'est la seconde expansion qui se joue ici.",
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
    vivants = {m: e for m, e in d.items() if (e.get("at") or 0) >= limite}
    if len(vivants) != len(d):
        d = vivants
    if not d:
        _ecrire(d)
        return 0

    mints = list(d)[:MAX_SURVEILLES]
    infos = holdings._metriques(mints)

    envoyees = 0
    for m in mints:
        e, x = d[m], infos.get(m)
        if not x:
            continue
        e["mint"] = m
        mc = x.get("mc") or 0.0
        if mc <= 0:
            continue

        # ── temps 1 : on cherche l'impulsion, sans rien envoyer
        if not e.get("impulsion_at"):
            base = e.get("base_mc") or mc
            e["base_mc"] = min(base, mc)          # la base, c'est le plus bas vu
            impulsion = (x.get("chg_m5", 0) >= SEUIL_M5
                         or mc >= e["base_mc"] * IMPULSION_X)
            if impulsion:
                e["impulsion_at"] = maintenant
                e["haut_mc"] = mc
                log(f"[expansion] {x.get('symbol')} : impulsion reperee "
                    f"({tg._usd(e['base_mc'])} -> {tg._usd(mc)})")
            continue

        # ── temps 2 : l'impulsion est passee, on attend le repli
        e["haut_mc"] = max(e.get("haut_mc") or mc, mc)
        haut, base = e["haut_mc"], e.get("base_mc") or 0.0

        if base and mc <= base * MARGE_BASE:
            # tout est rendu : la jambe a echoue, on desarme
            log(f"[expansion] {x.get('symbol')} : repli complet, abandonne")
            d.pop(m, None)
            continue

        if maintenant - (e.get("alerte_at") or 0) < COOLDOWN_H * 3600:
            continue

        repli = 1 - mc / haut if haut else 0.0
        if not (RETRACE_MIN <= repli <= RETRACE_MAX):
            continue
        # un couteau qui tombe n'est pas une entree : la baisse doit se calmer
        if x.get("chg_m5", 0) < STAB_M5:
            continue
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
            log(f"[expansion] {x.get('symbol')} — repli de {repli*100:.0f}% "
                f"sous {tg._usd(haut)}, 5 min a {x.get('chg_m5', 0):+.0f}%")

    _ecrire(d)
    return envoyees


def etat() -> dict:
    """Ce qui est sous surveillance — affiche dans l'interface."""
    d = _lire()
    return {"surveilles": len(d),
            "impulsions": sum(1 for e in d.values() if e.get("impulsion_at")),
            "alertes": sum(1 for e in d.values() if e.get("alerte_at"))}
