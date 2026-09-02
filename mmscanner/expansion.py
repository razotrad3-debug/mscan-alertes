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

import requests

import config

_SESSION = requests.Session()

WATCH_FILE = config.path("expansion_watch.json")
NEES_FILE = config.path("paires_nees.json")     # jetons deja vus naitre

# Robinhood n'a ni classement par volume ni API de tendance : le seul moyen
# d'y voir un coin AVANT qu'il bouge est de regarder naitre sa pool. Toutes
# passent par la factory Uniswap v3 de la chaine, contrat verifie, dont les
# transactions sont decodees par Blockscout.
FACTORIES = {
    "robinhood": {
        "explorateur": "https://robinhoodchain.blockscout.com",
        "factory": "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA",
        "quote": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",   # WETH
    },
}
FENETRE_NEE_H = 36.0     # un coin tout neuf merite plus de temps qu'un autre

# Solana, Ethereum et Base sont indexes par GeckoTerminal, qui publie ses
# pools les plus recentes : pas besoin d'aller lire les factories a la main.
# En revanche le debit y est sans commune mesure avec Robinhood — des
# centaines de lancements par heure sur Solana — donc on n'arme que les pools
# qui ont deja de quoi s'echanger. Une pool a zero ne donne rien a acheter.
CHAINES_GECKO = ("solana", "ethereum", "base")
LIQ_NAISSANCE = 8_000.0
# Budgets separes : sans ca une rafale de cinquante clones sur Robinhood
# mangeait tout le quota et les trois autres chaines ne passaient jamais.
MAX_NEES_FACTORY = 12
MAX_NEES_GECKO = 25
# Sur Solana la quasi-totalite des lancements naissent sous le seuil et
# tombent de la liste des nouveautes en quelques minutes. On les met de cote
# et on regarde s'ils se remplissent : sans ca, la chaine principale n'etait
# couverte qu'a la marge (un jeton arme sur vingt).
ATTENTE_H = 6.0
MAX_ATTENTE = 300

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
MAX_SURVEILLES = 300
# Un spammeur peut deployer cinquante jetons clones d'un coup : les coins
# venus des wallets suivis et du radar passent devant, les naissances
# ensuite. Une naissance qui n'a toujours aucune paire indexee au bout de
# trois heures est abandonnee.
PRIORITE = {"insider": 0, "radar": 1, "naissance": 2}
DELAI_NEE_MORTE_H = 3.0


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
          groupes: List[str] = None, source: str = "",
          fenetre_h: float = None) -> bool:
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
    if fenetre_h:
        d[mint]["fenetre"] = fenetre_h
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


# ── naissances : voir un coin avant qu'il bouge ─────────────────────
def _lire_nees() -> dict:
    try:
        with open(NEES_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def armer_naissances(log=print) -> int:
    """
    Arme les jetons dont la pool vient d'etre creee.

    Un coin nait toujours avec une pool vide : on ne juge donc rien ici, on se
    contente d'ouvrir l'oeil. Le controle de liquidite se fait au moment de
    l'alerte, quand il y a quelque chose a acheter.

    Beaucoup de creations sont du spam — un meme jeton recree avec plusieurs
    paliers de frais. On dedoublonne par jeton.
    """
    from mmscanner import sources_evm as evm

    brut = _lire_nees()
    maintenant = time.time()
    attente = brut.pop("_attente", {}) or {}
    # on oublie ce qui est vu depuis plus de trois jours, le fichier reste petit
    vus = {k: v for k, v in brut.items()
           if isinstance(v, (int, float)) and maintenant - v < 3 * 86400}
    attente = {m: e for m, e in attente.items()
               if maintenant - (e.get("at") or 0) < ATTENTE_H * 3600
               and m.lower() not in vus}

    total = 0
    pris_factory = 0
    for chaine, cfg in FACTORIES.items():
        try:
            r = _SESSION.get(
                f"{cfg['explorateur']}/api/v2/addresses/{cfg['factory']}/transactions",
                headers=evm.ENTETES, params={"filter": "to"}, timeout=30)
            if r.status_code != 200:
                continue
            items = (r.json() or {}).get("items") or []
        except Exception as e:
            log(f"[naissance] {chaine} : {e}")
            continue

        quote = (cfg.get("quote") or "").lower()
        for t in items:
            if pris_factory >= MAX_NEES_FACTORY:
                break
            di = t.get("decoded_input") or {}
            ps = {p.get("name"): p.get("value") for p in (di.get("parameters") or [])}
            a, b = ps.get("tokenA"), ps.get("tokenB")
            if not a or not b:
                continue
            jeton = b if a.lower() == quote else a
            cle = jeton.lower()
            if cle in vus:
                continue
            vus[cle] = maintenant
            if armer(jeton, chaine, "", None, "naissance", FENETRE_NEE_H):
                total += 1
                pris_factory += 1

    # ── chaines indexees par GeckoTerminal
    from mmscanner import sources_gecko as gecko
    pris_gecko = 0
    for chaine in CHAINES_GECKO:
        if pris_gecko >= MAX_NEES_GECKO:
            break
        net = gecko.net_for(chaine)
        if not net:
            continue
        try:
            data = gecko._get(f"{gecko.BASE}/networks/{net}/new_pools", {"page": 1})
        except Exception as e:
            log(f"[naissance] {chaine} : {e}")
            continue
        for item in ((data or {}).get("data") or []):
            pool = gecko._parse_pool(item)
            if not pool:
                continue
            jeton = pool.get("mint")
            if not jeton or jeton.lower() in vus:
                continue
            if (pool.get("liquidity_usd") or 0) < LIQ_NAISSANCE:
                # trop vide pour l'instant : on la garde a l'oeil
                attente.setdefault(jeton, {"chain": chaine, "at": maintenant})
                continue
            vus[jeton.lower()] = maintenant
            if armer(jeton, chaine, pool.get("symbol") or "", None,
                     "naissance", FENETRE_NEE_H):
                total += 1
                pris_gecko += 1
            if pris_gecko >= MAX_NEES_GECKO:
                break

    # ── les mises de cote qui se sont remplies depuis
    if attente:
        from mmscanner import holdings
        infos = holdings._metriques(list(attente)[:MAX_ATTENTE])
        for jeton, e in list(attente.items()):
            d = infos.get(jeton)
            if not d:
                continue
            if (d.get("liquidity_usd") or 0) < LIQ_NAISSANCE:
                continue
            vus[jeton.lower()] = maintenant
            attente.pop(jeton, None)
            if armer(jeton, e.get("chain") or d.get("chain") or "",
                     d.get("symbol") or "", None, "naissance", FENETRE_NEE_H):
                total += 1

    try:
        tmp = NEES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({**vus, "_attente": attente}, f)
        os.replace(tmp, NEES_FILE)
    except Exception:
        pass

    if total:
        log(f"[naissance] {total} nouvelle(s) paire(s) sous veille")
    return total


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
def poll(log=print, envoyer: bool = None) -> int:
    """
    Un tour de veille. Retourne le nombre d'alertes envoyees.

    envoyer=False : on suit l'etat des coins sans rien envoyer. C'est ce que
    fait l'application de bureau — elle tient la liste a jour pour l'afficher,
    pendant que le cloud garde le monopole des alertes.
    """
    from mmscanner import holdings, safety, telegram_alerts as tg
    from mmscanner.engine import is_crypto_native

    if envoyer is None:
        envoyer = tg.alerts_enabled()

    d = _lire()
    if not d:
        return 0

    maintenant = time.time()
    vivants = {m: e for m, e in d.items()
               if (e.get("at") or 0) >= maintenant - (e.get("fenetre") or FENETRE_H) * 3600}
    if len(vivants) != len(d):
        d = vivants
    if not d:
        _ecrire(d)
        return 0

    mints = sorted(d, key=lambda m: (PRIORITE.get(d[m].get("source"), 3),
                                     -(d[m].get("at") or 0)))[:MAX_SURVEILLES]
    infos = holdings._metriques(mints)

    envoyees = 0
    for m in mints:
        e, x = d[m], infos.get(m)
        if not x:
            # nee sans marche et toujours rien : elle ne donnera rien
            if (e.get("source") == "naissance" and not e.get("impulsion_at")
                    and maintenant - (e.get("at") or 0) > DELAI_NEE_MORTE_H * 3600):
                d.pop(m, None)
            continue
        e["mint"] = m
        mc = x.get("mc") or 0.0
        if mc <= 0:
            continue
        # on retient les derniers chiffres : l'interface les affiche sans
        # avoir a redemander quoi que ce soit au reseau
        e.update(symbol=x.get("symbol") or e.get("symbol"),
                 chain=x.get("chain") or e.get("chain"),
                 pair=x.get("pair") or e.get("pair"),
                 mc=mc, liq=x.get("liquidity_usd"),
                 chg_h1=x.get("chg_h1"), chg_m5=x.get("chg_m5"), vu=maintenant)

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

        if not envoyer:
            e["pret"] = maintenant      # zone d'entree atteinte, sans envoi
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
