"""
Veille rapide sur les adresses suivies.

Le scan complet passe par les classements volume/trending : un coin n'y
apparait qu'une fois qu'il a deja bouge. Cette veille-ci ne regarde que les
wallets suivis, toutes les 60 s, et signale l'entree elle-meme — donc avant
que le coin ne monte dans les classements.

Elle ne remplace pas la notation : elle dit "quelqu'un vient d'entrer", le
score arrive au scan suivant.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import config
from . import sources_dex, telegram_alerts as tg
from .engine import is_crypto_native
from .followed import load_followed, recent_buys, split_group

VU_FILE = config.path("insider_seen.json")

FENETRE_MIN = 20        # on ignore un achat plus vieux que ca : trop tard pour agir
COOLDOWN_H = 12         # un meme coin ne repasse pas avant ce delai
MAX_PAR_TOUR = 6

# Avec 145 adresses suivies, une entree isolee ne veut rien dire : elles
# touchent des dizaines de coins par heure. Les regles ci-dessous ne gardent
# que ce qui ressemble a un setup jouable — le reste part quand meme sous
# veille d'expansion, donc rien n'est perdu : l'alerte arrive plus tard, au
# repli, quand il y a quelque chose a faire.
MIN_ACHETEURS = 2       # deux adresses au moins, pas une
MAX_CHG_H1 = 60.0       # au-dela l'entree est passee : on ne chase pas
MAX_CHG_H24 = 300.0
MIN_CHG_H24 = -25.0     # deja en train de couler : on laisse tomber
MIN_MC = 50_000         # sous ca il n'y a rien a trader
MIN_LIQ = 15_000
MAX_PAR_HEURE = 4       # plafond global, quoi qu'il arrive
EVM_TOUS_LES_S = 300.0     # cadence des adresses EVM (3 requetes chacune)
_EVM_PROCHAIN = 0.0        # garde-fou : jamais plus de 6 alertes d'un coup


def _charger() -> dict:
    try:
        with open(VU_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ecrire(d: dict) -> None:
    try:
        with open(VU_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _format(coin: dict) -> str:
    chain = (coin.get("chain") or "solana").lower()
    label = config.CHAIN_META.get(chain, {}).get("label", chain.title())
    pastille = tg.PASTILLE.get(chain, "\u26aa")
    titre = tg._esc(coin.get("symbol")) or tg._esc(coin.get("name")) or "?"
    age = max(0, int((time.time() - coin["ts"]) / 60))

    lines = [
        f"\u26a1 *{titre}* \u2014 INSIDER",
        f"{label} \u00b7 entr\u00e9e il y a {age} min",
        "",
        f"- Market Cap : `{tg._usd(coin.get('mc'))}`",
    ]
    if coin.get("chg_h1") is not None:
        lines.append(f"- 1h : `{coin['chg_h1']:+.0f}%`  \u00b7  24h : `{coin.get('chg_h24', 0):+.0f}%`")

    qui = ", ".join(coin["par"][:4])
    lines += ["", f"\U0001f45b {len(coin['par'])} insider(s) : {qui}",
              "_Score complet au prochain scan._", ""]

    # liens construits sur la chaine du coin : GMGN etait cable sur
    # /sol/, ce qui envoyait vers une page vide pour une paire
    # Ethereum ou Base
    from .model import dex_link, gmgn_link
    mint = coin["mint"]
    lines.append(f"[DexScreener]({dex_link(chain, mint)})"
                 f" · [GMGN]({gmgn_link(chain, mint)})")
    return "\n".join(lines)


def poll(log=print, amorcage: bool = False) -> int:
    """
    Un tour de veille. Retourne le nombre d'alertes envoyees.

    `amorcage=True` enregistre ce qui existe deja sans rien envoyer : sans ca,
    le premier tour signalerait d'un coup tous les achats des derniers jours.
    """
    suivis = load_followed()
    if not suivis:
        return 0

    vu = _charger()
    maintenant = time.time()
    limite = maintenant - FENETRE_MIN * 60

    # Solana a chaque tour ; les adresses EVM tous les EVM_TOUS_LES_S, car
    # elles coutent trois requetes chacune (Ethereum, Base, Robinhood). Elles
    # etaient purement et simplement exclues : c'est ce qui faisait arriver
    # les paires Robinhood et Ethereum bien apres le setup.
    global _EVM_PROCHAIN
    cibles = [(a, l) for a, l in suivis
              if not a.startswith("0x") and config.HELIUS_API_KEY]
    if maintenant >= _EVM_PROCHAIN:
        _EVM_PROCHAIN = maintenant + EVM_TOUS_LES_S
        cibles += [(a, l) for a, l in suivis if a.startswith("0x")]

    def _lire(item):
        addr, label = item
        try:
            return addr, label, recent_buys(addr, hours=2)
        except Exception:
            return addr, label, []

    with ThreadPoolExecutor(max_workers=15) as ex:
        brut = list(ex.map(_lire, cibles))

    # regroupement par coin : qui vient d'entrer, et quand
    frais: Dict[str, dict] = {}
    for addr, label, achats in brut:
        for b in achats:
            mint, ts = b.get("mint"), b.get("ts", 0)
            if not mint or ts < limite:
                continue
            groupe = split_group(label)[0] if label else "Suivi"
            e = frais.setdefault(mint, {"mint": mint, "ts": ts, "par": [],
                                        "acheteurs": set()})
            if groupe not in e["par"]:
                e["par"].append(groupe)
            e["acheteurs"].add(addr)
            e["ts"] = max(e["ts"], ts)

    # tout ce sur quoi une adresse suivie vient d'entrer passe sous veille
    # d'expansion : c'est ce vivier, deja filtre par la qualite des wallets,
    # qui permet d'alerter au depart du mouvement et non une heure apres.
    try:
        from . import expansion
        n = expansion.armer_lot(
            [{"mint": c["mint"], "groupes": c["par"]} for c in frais.values()],
            source="insider")
        if n:
            log(f"[expansion] {n} coin(s) mis sous veille")
    except Exception as e:
        log(f"[expansion] {e}")

    # on ecarte ce qui a deja ete signale dans la fenetre
    cooldown = maintenant - COOLDOWN_H * 3600
    nouveaux = [c for m, c in frais.items()
                if (vu.get(m, {}).get("at", 0) if isinstance(vu.get(m), dict) else 0) < cooldown]
    # le plus de monde dessus d'abord
    nouveaux.sort(key=lambda c: (len(c.get("acheteurs") or ()), len(c["par"]),
                                 c["ts"]), reverse=True)

    if amorcage:
        for c in frais.values():
            vu[c["mint"]] = {"at": maintenant, "amorcage": True}
        _ecrire(vu)
        log(f"[insider] amorcage : {len(frais)} coin(s) enregistre(s) sans alerte")
        return 0

    envoyes = 0
    # ce qui est deja parti dans l'heure compte dans le plafond
    deja = sum(1 for v in vu.values()
               if isinstance(v, dict) and not v.get("amorcage")
               and (v.get("at") or 0) > maintenant - 3600)

    for c in nouveaux[:MAX_PAR_TOUR]:
        if deja + envoyes >= MAX_PAR_HEURE:
            break
        # une seule adresse dessus : ca n'a pas valeur de signal
        if len(c.get("acheteurs") or ()) < MIN_ACHETEURS:
            continue
        info = sources_dex.enrich(c["mint"]) or {}
        if not info:
            continue
        c.update({"symbol": info.get("symbol"), "name": info.get("name"),
                  "mc": info.get("market_cap"), "chain": info.get("chain"),
                  "chg_h1": info.get("chg_h1"), "chg_h24": info.get("chg_h24")})
        # meme garde-fous que le scan : pas d'action tokenisee, pas de poussiere
        if not is_crypto_native(c.get("symbol"), c.get("name"), c["mint"], None):
            continue
        if (info.get("liquidity_usd") or 0) < MIN_LIQ:
            continue
        if (info.get("market_cap") or 0) < MIN_MC:
            continue
        # deja parti, ou deja en train de couler : dans les deux cas il n'y a
        # plus d'entree a prendre
        h1, h24 = info.get("chg_h1") or 0, info.get("chg_h24") or 0
        if h1 > MAX_CHG_H1 or h24 > MAX_CHG_H24:
            log(f"[insider] {info.get('symbol')} ignore : deja +{max(h1, h24):.0f}%")
            continue
        if h24 < MIN_CHG_H24:
            continue
        if tg.send(_format(c)):
            vu[c["mint"]] = {"at": maintenant, "par": c["par"]}
            envoyes += 1
            time.sleep(0.4)

    # purge : au-dela de 3 jours, l'information n'a plus de valeur
    for k in [k for k, v in vu.items()
              if isinstance(v, dict) and v.get("at", 0) < maintenant - 3 * 86400]:
        vu.pop(k, None)
    _ecrire(vu)

    if envoyes:
        log(f"[insider] {envoyes} entree(s) signalee(s)")
    return envoyes
