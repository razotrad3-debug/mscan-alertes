"""
MSCAN — Tes trendlines.

Le scanner sait reconnaitre une structure, pas ton oeil. Quand tu traces une
ligne sur DexScreener, tu y mets un jugement que rien ici ne sait reproduire :
quels bas relier, lesquels ignorer, quelle jambe compte. Le probleme n'a
jamais ete de tracer — c'est de surveiller trente lignes a la fois.

Ce module ne trace rien. Il recoit les lignes que TU as tracees, les garde,
et regarde le prix venir dessus.

  1. un script dans ton navigateur lit les ancres de tes traces sur
     DexScreener et les envoie ici (voir outils/dexscreener-mscan.user.js) ;
  2. on convertit ces ancres en market cap, la seule unite dans laquelle tout
     le reste de MSCAN parle ;
  3. a chaque tour on calcule ou passe la ligne MAINTENANT — une trendline
     monte ou descend avec le temps — et on compare au prix.

L'alerte part quand le prix VIENT toucher la ligne, pas quand il traine
dessus : une ligne doit d'abord s'eloigner pour se rearmer.

Unites : peu importe que ton chart soit en prix ou en market cap. On ne le
demande pas au chart — ses rouages internes sont minifies et changent — on le
DEDUIT des valeurs tracees. Un memecoin cote 0,0001 $ pour 150 000 $ de
capitalisation : neuf ordres de grandeur separent les deux, aucune ambiguite.
"""
import hashlib
import json
import math
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

import config

_SESSION = requests.Session()
FICHIER = config.path("trendlines.json")

# Le prix ne vient jamais se poser au centime sur un trait fait a la main :
# il passe dessous, remonte, repasse. On travaille donc dans une bande.
TOLERANCE = 0.015          # +/- 1,5 % autour de la ligne = touche
# Il faut etre VENU de quelque part. Sans cette hysteresis, un prix qui
# flotte autour de la ligne enverrait un message a chaque tour.
APPROCHE = 0.045           # la ligne se rearme quand on s'eloigne de 4,5 %
COOLDOWN_H = 6.0           # deux alertes sur la meme ligne, jamais coup sur coup

# Une trendline n'est pas eternelle : passe un certain point, la pente
# prolongee ne decrit plus rien. On la garde au moins deux jours, au plus
# quinze, et sinon trois fois la portee du trace.
VALIDITE_MIN_H = 48.0
VALIDITE_MAX_J = 14.0
PROLONGE_X = 3.0

MIN_LIQ = 3_000.0          # une touche sur un coin mort n'est pas une entree
HORS_JEU = 0.60            # a plus de 60 % de la ligne, il n'y a rien a suivre
MAX_LIGNES = 300

# Outils TradingView qu'on sait lire. Les trois premiers ont deux ancres,
# les deux derniers une seule (niveau horizontal, pente nulle).
OUTILS = {
    "LineToolTrendLine", "LineToolRay", "LineToolExtended",
    "LineToolHorzLine", "LineToolHorzRay",
    "LineToolFibRetracement",
    # le rectangle : une zone d'interet dessinee a la main (POI, orderblock).
    # Deux coins opposes, donc deux prix : c'est deja une zone, on la prend
    # telle quelle. Sa largeur dans le temps ne compte pas — un POI se lit
    # comme un niveau, il ne s'eteint pas au bord droit du rectangle.
    "LineToolRectangle",
}

# Le golden pocket du cours. Ce ne sont pas deux traits mais UNE zone : a
# 0,618 et 0,65 pres, deux alertes partiraient coup sur coup sur un petit
# mouvement, et sur un gros mouvement un seul niveau median raterait les
# bords. On garde donc les deux bornes et on surveille l'intervalle.
FIB_RATIOS = (0.618, 0.65)


# ── etat sur disque ────────────────────────────────────────────────
def _lire() -> dict:
    try:
        with open(FICHIER, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _ecrire(d: dict) -> None:
    try:
        tmp = FICHIER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, FICHIER)
    except Exception:
        pass


# ── lecture de ce que le navigateur envoie ─────────────────────────
def _epoch(v) -> Optional[float]:
    """Accepte un ISO 8601, un timestamp en secondes ou en millisecondes."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        v = float(v)
        return v / 1000.0 if v > 1e11 else v
    try:
        s = str(v).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            import calendar
            return calendar.timegm(d.timetuple()) + d.microsecond / 1e6
        return d.timestamp()
    except Exception:
        return None


def _cle(chain: str, pair: str, ancres: List[tuple]) -> str:
    """
    Identite d'une ligne : ses ancres brutes, telles que tracees.

    On ne hache pas les valeurs converties — elles dependent du cours au
    moment de la synchro. Les ancres, elles, sont stables : verifie sur un
    rechargement complet de DexScreener, prix identiques a la dixieme
    decimale. Une ligne resynchronisee garde donc son historique, et une
    ligne deplacee devient une nouvelle ligne, ce qui est le comportement
    attendu.
    """
    brut = f"{chain}:{pair}:" + "|".join(f"{t:.0f}/{v:.10g}" for t, v in ancres)
    return hashlib.sha1(brut.encode()).hexdigest()[:12]


def _deduire_unite(valeurs: List[float], prix: float, mc: float) -> Optional[str]:
    """
    Le chart affichait-il un cours ou une capitalisation ?

    On compare la valeur mediane des ancres aux deux candidats. Entre un cours
    de 0,0001 $ et une capitalisation de 150 000 $, il y a un facteur d'un
    milliard : le choix ne se discute pas. On refuse quand meme de trancher si
    les deux candidats sont a moins d'un facteur 10 l'un de l'autre, ou si
    l'ancre est absurdement loin des deux.
    """
    if not valeurs:
        return None
    v = sorted(valeurs)[len(valeurs) // 2]
    if v <= 0:
        return None
    cands = []
    if prix > 0:
        cands.append(("prix", abs(math.log10(v / prix))))
    if mc > 0:
        cands.append(("mc", abs(math.log10(v / mc))))
    if not cands:
        return None
    cands.sort(key=lambda c: c[1])
    if cands[0][1] > 4:                 # plus d'un facteur 10 000 : douteux
        return None
    if len(cands) == 2 and cands[1][1] - cands[0][1] < 1:
        return "prix"                   # trop serre pour trancher : defaut DexScreener
    return cands[0][0]


def _infos_paire(chain: str, pair: str) -> Optional[dict]:
    """Resout une adresse de paire : jeton de base, market cap, cours."""
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"
    d = None
    for essai in range(3):
        try:
            r = _SESSION.get(url, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (essai + 1))
                continue
            r.raise_for_status()
            d = r.json() or {}
            break
        except Exception:
            time.sleep(1.0 * (essai + 1))
    if d is None:
        return None
    p = d.get("pair") or (d.get("pairs") or [None])[0]
    if not p:
        return None
    base = p.get("baseToken") or {}
    mc = float(p.get("marketCap") or p.get("fdv") or 0)
    prix = float(p.get("priceUsd") or 0)
    if not base.get("address") or prix <= 0:
        return None
    return {"mint": base["address"], "symbol": base.get("symbol") or "?",
            "chain": p.get("chainId") or chain, "mc": mc, "prix": prix}


def enregistrer(charge: dict) -> dict:
    """
    Recoit les traces d'UNE paire et remplace ce qu'on avait pour elle.

    Le navigateur fait autorite : une ligne effacee sur DexScreener disparait
    ici, une ligne deplacee remplace l'ancienne. En revanche une ligne
    inchangee garde son cooldown et son armement — sans quoi chaque passage
    sur la page aurait remis le compteur a zero et fait repartir l'alerte.
    """
    chain = (charge.get("chain") or "").lower().strip()
    pair = (charge.get("pair") or "").strip()
    brutes = charge.get("lines") or []
    if not chain or not pair:
        return {"ok": False, "erreur": "paire inconnue"}

    infos = _infos_paire(chain, pair)
    if not infos:
        return {"ok": False, "erreur": "paire introuvable chez DexScreener"}

    # On suit en market cap — c'est ainsi que se lisent toutes les autres
    # alertes. DexScreener ne le publie pas pour tout le monde (supply
    # inconnue) : on retombe alors sur le cours.
    unite = "mc" if infos["mc"] > 0 else "prix"

    d = _lire()
    anciennes = {k: v for k, v in d.items()
                 if v.get("chain") == infos["chain"] and v.get("pair") == pair}
    gardees = {}
    ignorees = 0
    maintenant = time.time()

    for ligne in brutes:
        outil = ligne.get("tool") or ""
        if outil not in OUTILS:
            continue
        pts = []
        for p in (ligne.get("points") or []):
            t, v = _epoch(p.get("time")), p.get("price")
            try:
                v = float(v)
            except Exception:
                v = 0.0
            if t and v > 0:
                pts.append((t, v))
        if not pts:
            continue
        pts = pts[:2]
        if len(pts) == 2 and pts[0][0] > pts[1][0]:
            pts.reverse()

        # L'unite se deduit ligne par ligne, pas une fois pour toutes : le
        # bouton Price/Mcap de DexScreener ne reecrit pas les traces deja
        # posees. Une ligne tracee en capitalisation garde ses valeurs en
        # capitalisation meme si tu repasses en prix — deux lignes d'une
        # meme paire peuvent donc ne pas parler la meme langue.
        affiche = _deduire_unite([v for _, v in pts], infos["prix"], infos["mc"])
        if affiche is None:
            ignorees += 1
            continue
        if unite == "mc":
            facteur = (infos["mc"] / infos["prix"]) if affiche == "prix" else 1.0
        else:
            facteur = 1.0

        # Fibonacci : les deux ancres decrivent le mouvement, pas un trait.
        # On en tire la zone 0,618-0,65, qui ne bouge pas avec le temps.
        zb = zh = None
        genre = ""
        if outil == "LineToolRectangle":
            if len(pts) < 2:
                continue
            zb, zh = sorted((pts[0][1], pts[1][1]))
            genre = "poi"
        elif outil == "LineToolFibRetracement":
            if len(pts) < 2:
                continue
            (_, va), (_, vb) = pts[0], pts[1]
            niv = [vb + r * (va - vb) for r in FIB_RATIOS]
            zb, zh = min(niv), max(niv)
            genre = "fib"

        cle = _cle(infos["chain"], pair, pts)
        vieille = anciennes.get(cle) or {}
        gardees[cle] = {
            "chain": infos["chain"], "pair": pair, "mint": infos["mint"],
            "symbol": infos["symbol"], "outil": outil,
            "t1": pts[0][0], "v1": zb if zb is not None else pts[0][1],
            "t2": None if zb is not None else (pts[1][0] if len(pts) == 2 else None),
            "v2": None if zb is not None else (pts[1][1] if len(pts) == 2 else None),
            "zb": zb, "zh": zh, "zone": genre,
            "facteur": facteur, "unite": unite,
            "cree": vieille.get("cree") or maintenant,
            "vu": maintenant,
            "arme": vieille.get("arme", True),
            "dernier_signal": vieille.get("dernier_signal") or 0,
        }

    # On n'efface l'ancien que si on a de quoi le remplacer, ou si le
    # navigateur dit clairement qu'il ne reste plus rien de trace. Un envoi
    # qui arrive avec des traces mais dont aucune n'a pu etre lue est un
    # accident, pas un effacement : dans ce cas on ne touche a rien.
    if gardees or not brutes:
        for k in anciennes:
            d.pop(k, None)
        d.update(gardees)
    else:
        gardees = anciennes
    if len(d) > MAX_LIGNES:                      # les plus vieilles d'abord
        for k in sorted(d, key=lambda c: d[c].get("vu") or 0)[:len(d) - MAX_LIGNES]:
            d.pop(k, None)
    _ecrire(d)

    return {"ok": True, "symbol": infos["symbol"], "chain": infos["chain"],
            "suivi": unite, "ignorees": ignorees,
            "recues": len(gardees), "total": len(d),
            "niveaux": [round(niveau(x) or 0) for x in gardees.values()]}


def oublier(mint: str) -> int:
    """
    Retire toutes les lignes d'un coin. Retourne combien sont parties.

    Le navigateur reste la source : si les traces existent toujours sur
    DexScreener, elles reviendront au prochain passage sur la paire. C'est
    voulu — on efface une surveillance, pas un dessin.
    """
    if not mint:
        return 0
    d = _lire()
    partantes = [k for k, l in d.items() if l.get("mint") == mint]
    for k in partantes:
        d.pop(k, None)
    if partantes:
        _ecrire(d)
    return len(partantes)


# ── geometrie ──────────────────────────────────────────────────────
def bornes(l: dict) -> Optional[tuple]:
    """Zone Fibonacci convertie, ou None si la ligne n'en est pas une."""
    zb, zh, f = l.get("zb"), l.get("zh"), l.get("facteur") or 0
    if zb is None or zh is None or f <= 0:
        return None
    return zb * f, zh * f


def ecart(l: dict, valeur: float) -> Optional[float]:
    """
    De combien le prix est-il a cote de la ligne ? Zero s'il est dedans.

    Une trendline est un trait : l'ecart se mesure a ce trait. Une zone Fib a
    une epaisseur : tant qu'on est dedans, on est arrive.
    """
    z = bornes(l)
    if z:
        bas, haut = z
        if valeur > haut:
            return valeur / haut - 1.0
        if valeur < bas:
            return valeur / bas - 1.0
        return 0.0
    n = niveau(l)
    return (valeur / n - 1.0) if n and n > 0 else None


def niveau(l: dict, t: float = None) -> Optional[float]:
    """Market cap ou passe la ligne a l'instant t. C'est tout le calcul."""
    z = bornes(l)
    if z:
        return (z[0] + z[1]) / 2.0        # le milieu du golden pocket
    t = t or time.time()
    t1, v1, t2, v2 = l.get("t1"), l.get("v1"), l.get("t2"), l.get("v2")
    if not t1 or not v1:
        return None
    if not t2 or t2 == t1 or not v2:
        v = v1                                    # niveau horizontal
    else:
        v = v1 + (v2 - v1) * (t - t1) / (t2 - t1)
    return v * (l.get("facteur") or 0) if v > 0 else None


def _fin(l: dict) -> float:
    cree = l.get("cree") or 0
    plafond = cree + VALIDITE_MAX_J * 86400
    t1, t2 = l.get("t1"), l.get("t2")
    if not t2 or t2 == t1:
        return plafond                            # un niveau ne perime pas vite
    portee = abs(t2 - t1)
    return min(max(t2 + PROLONGE_X * portee, cree + VALIDITE_MIN_H * 3600), plafond)


def lignes() -> List[dict]:
    """Les lignes vivantes, avec leur niveau du moment."""
    maintenant = time.time()
    out = []
    for cle, l in _lire().items():
        if _fin(l) < maintenant:
            continue
        out.append(dict(l, id=cle, niveau=niveau(l, maintenant), expire=_fin(l)))
    out.sort(key=lambda x: -(x.get("vu") or 0))
    return out


# ── message ────────────────────────────────────────────────────────
def _val(v: float, unite: str) -> str:
    """
    Un market cap se lit $160K ; un cours de memecoin a besoin de decimales.

    Ici les deux chiffres affiches — le prix et la ligne — sont a moins de
    1,5 % l'un de l'autre par construction. Arrondis comme ailleurs dans
    l'app, ils tombaient sur le meme "$2K" et le message n'apprenait plus
    rien. Sous 100 K on garde donc deux decimales.
    """
    from mmscanner import telegram_alerts as tg
    if unite != "prix":
        if 1_000 <= v < 100_000:
            return f"${v/1000:.2f}K"
        return tg._usd(v)
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.10f}".rstrip("0")


def _message(l: dict, x: dict, val: float, n: float, ecart: float) -> str:
    """
    Court, et il dit d'ou vient le prix.

    Le rond est orange quelle que soit la chaine : une alerte de trendline
    vient de TOI, pas du scanner, et doit se reconnaitre d'un coup d'oeil au
    milieu des autres.
    """
    from mmscanner import telegram_alerts as tg
    from mmscanner.model import dex_link, gmgn_link

    chain = (x.get("chain") or l.get("chain") or "solana").lower()
    label = config.CHAIN_META.get(chain, {}).get("label", chain.title())
    titre = tg._esc(x.get("symbol") or l.get("symbol") or "?")
    unite = l.get("unite") or "mc"
    quoi = "Prix" if unite == "prix" else "Market Cap"
    # Au-dessus de la ligne, le prix descend dessus ; en dessous, il remonte.
    # C'est le sens de l'approche, pas celui de la bougie. Dans une zone Fib
    # l'ecart est nul : on tranche alors sur la derniere heure.
    if ecart > 0:
        sens = "Crossing Down"
    elif ecart < 0:
        sens = "Crossing Up"
    else:
        sens = "Crossing Down" if (x.get("chg_h1") or 0) <= 0 else "Crossing Up"

    z = bornes(l)
    corps = [
        f"🟠 *{titre}*",
        f"{label} · " + ("POI touch" if l.get("zone") == "poi"
                         else ("Fib 0.618-0.65 touch" if z else "Trendline touch")),
        "",
        f"- {sens}",
        f"- {quoi} : `{_val(val, unite)}`",
    ]
    if z:
        corps.append(f"- Zone : `{_val(z[0], unite)}` - `{_val(z[1], unite)}`")
    corps += ["", ("Le prix revient dans ta zone POI." if l.get("zone") == "poi"
                   else "Le prix revient dans ta zone Fib.") if z
                  else "Le prix revient sur la ligne que tu as tracee."]
    cible = x.get("pair") or l.get("pair") or l.get("mint")
    corps += ["", f"[DexScreener]({dex_link(chain, cible)})"
                  f" · [GMGN]({gmgn_link(chain, l.get('mint'))})"]
    return "\n".join(corps)


# ── tour de veille ─────────────────────────────────────────────────
def poll(log=print, envoyer: bool = None) -> int:
    """
    Un tour. Retourne le nombre d'alertes envoyees.

    Contrairement au reste de MSCAN, l'envoi n'est pas reserve au cloud : tes
    lignes vivent sur cette machine, c'est donc a elle de les surveiller. Il
    n'y a pas de risque de doublon, le cloud n'en a aucune copie.
    """
    from mmscanner import holdings, telegram_alerts as tg

    if envoyer is None:
        # Une seule des deux machines doit envoyer, sinon chaque touche part
        # en double. Le passage de relais est EXPLICITE : tant que
        # MSCAN_CLOUD_LIGNES n'est pas pose, l'application garde la charge.
        # Une cle presente ne suffit pas — elle ne prouve pas que le secret
        # est en place cote GitHub, et un silence se remarque bien plus tard
        # qu'un doublon.
        cloud = (os.getenv("MSCAN_CLOUD_LIGNES") or "").strip().lower()             in ("1", "true", "oui", "yes")
        envoyer = tg.enabled() and (bool(os.getenv("MSCAN_HEADLESS")) or not cloud)

    d = _lire()
    if not d:
        return 0

    maintenant = time.time()
    vivantes = {k: l for k, l in d.items() if _fin(l) >= maintenant}
    if len(vivantes) != len(d):
        log(f"[trendlines] {len(d) - len(vivantes)} ligne(s) expiree(s)")
        d = vivantes
        _ecrire(d)
    if not d:
        return 0

    mints = sorted({l.get("mint") for l in d.values() if l.get("mint")})
    infos = holdings._metriques(mints, frais=True)

    envoyees, change = 0, False
    for cle, l in d.items():
        x = infos.get(l.get("mint"))
        if not x:
            continue
        val = (x.get("price_usd") if l.get("unite") == "prix" else x.get("mc")) or 0.0
        n = niveau(l, maintenant)
        e = ecart(l, val) if val > 0 else None
        if val <= 0 or not n or n <= 0 or e is None:
            continue
        if abs(e) > HORS_JEU:
            continue

        # rearmement : la ligne doit s'etre eloignee pour pouvoir resonner
        if abs(e) >= APPROCHE:
            if not l.get("arme"):
                l["arme"] = True
                change = True
            continue
        # premier tour du processus : on ne fait qu'observer. Sans ca, une
        # ligne sur laquelle le prix traine deja sonnerait des le demarrage,
        # sans que rien ne soit venu la toucher.
        if not _AMORCE["fait"]:
            if l.get("arme"):
                l["arme"] = False
                change = True
            continue
        if abs(e) > TOLERANCE or not l.get("arme"):
            continue
        if maintenant - (l.get("dernier_signal") or 0) < COOLDOWN_H * 3600:
            continue
        if (x.get("liquidity_usd") or 0) < MIN_LIQ:
            continue

        l["arme"] = False
        l["dernier_signal"] = maintenant
        change = True
        log(f"[trendlines] {x.get('symbol')} touche sa ligne "
            f"({val:.6g} vs {n:.6g})")
        if envoyer and tg.send(_message(l, x, val, n, e)):
            envoyees += 1

    _AMORCE["fait"] = True
    if change:
        _ecrire(d)
    return envoyees


_AMORCE = {"fait": False}
_BOUCLE = {"on": False}


def boucle(log=print, cadence: float = 60.0) -> None:
    """Tour toutes les 60 s. Demarree une seule fois par l'application."""
    if _BOUCLE["on"]:
        return
    _BOUCLE["on"] = True
    while True:
        debut = time.time()
        try:
            poll(log=log)
        except Exception as e:
            log(f"[trendlines] {e}")
        time.sleep(max(5.0, cadence - (time.time() - debut)))


# ── publication vers le cloud ──────────────────────────────────────
# Tes lignes doivent atteindre le bot qui tourne sur GitHub. Le depot est
# PUBLIC — c'est d'ailleurs pour ca que les listes de wallets n'y sont pas —
# donc on n'y pose jamais les lignes en clair : sur quels coins tu es et a
# quels niveaux tu comptes entrer, c'est precisement ce qui ne doit pas se
# lire. Le fichier publie est chiffre ; sans la cle, c'est du bruit.
DEPOT = "razotrad3-debug/mscan-alertes"
FICHIER_ENC = "trendlines.enc"
URL_ENC = f"https://raw.githubusercontent.com/{DEPOT}/main/{FICHIER_ENC}"
DELAI_PUBLI_S = 30.0
_PUBLI = {"at": 0.0, "empreinte": ""}


def cle_secrete() -> Optional[bytes]:
    v = (os.getenv("MSCAN_LIGNES_KEY") or "").strip()
    return v.encode() if v else None


def _boite():
    from cryptography.fernet import Fernet
    cle = cle_secrete()
    return Fernet(cle) if cle else None


def _racine_depot() -> Optional[str]:
    d = os.path.abspath(config.APP_DIR)
    for _ in range(4):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def publier(log=print) -> bool:
    """
    Chiffre les lignes et les pousse sur le depot, pour que le cloud les voie.

    Sans cle, on ne publie rien du tout : mieux vaut pas de cloud que des
    setups lisibles par n'importe qui.
    """
    boite = _boite()
    if boite is None:
        return False
    racine = _racine_depot()
    if not racine:
        return False

    brut = json.dumps(_lire(), sort_keys=True).encode()
    empreinte = hashlib.sha1(brut).hexdigest()
    maintenant = time.time()
    if empreinte == _PUBLI["empreinte"]:
        return False                       # rien de neuf
    if maintenant - _PUBLI["at"] < DELAI_PUBLI_S:
        return False                       # on ne pousse pas a chaque trait

    chemin = os.path.join(racine, FICHIER_ENC)
    try:
        with open(chemin, "wb") as f:
            f.write(boite.encrypt(brut))
    except Exception as e:
        log(f"[trendlines] ecriture chiffree : {e}")
        return False

    import subprocess
    # Sous Windows, une application sans console qui lance git fait clignoter
    # une fenetre noire a chaque appel. CREATE_NO_WINDOW l'empeche.
    sans_fenetre = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def git(*args):
        return subprocess.run(("git",) + args, cwd=racine, capture_output=True,
                              text=True, timeout=90, creationflags=sans_fenetre)
    try:
        # on ne commite QUE ce fichier : le reste du depot ne nous regarde pas
        git("add", FICHIER_ENC)
        r = git("commit", FICHIER_ENC, "-m", "lignes")
        if r.returncode != 0 and "nothing to commit" not in (r.stdout or ""):
            log(f"[trendlines] commit : {(r.stdout or r.stderr)[:120]}")
            return False
        r = git("push", "origin", "HEAD")
        if r.returncode != 0:
            log(f"[trendlines] push : {(r.stderr or '')[:120]}")
            return False
    except Exception as e:
        log(f"[trendlines] git : {e}")
        return False

    _PUBLI.update(at=maintenant, empreinte=empreinte)
    log("[trendlines] lignes publiees (chiffrees)")
    return True


def rapatrier(log=print) -> int:
    """
    Cote cloud : va chercher les lignes publiees et les fusionne.

    L'armement et le cooldown appartiennent a CE processus — ils decrivent ce
    qu'il a deja vu, pas ce que la machine de l'utilisateur a vu. On les garde
    donc, et on ne prend du fichier distant que les lignes elles-memes.
    """
    boite = _boite()
    if boite is None:
        return 0
    try:
        r = _SESSION.get(URL_ENC, timeout=25)
        if r.status_code == 404:
            return 0
        r.raise_for_status()
        distant = json.loads(boite.decrypt(r.content).decode())
    except Exception as e:
        log(f"[trendlines] rapatriement : {e}")
        return 0
    if not isinstance(distant, dict):
        return 0

    local = _lire()
    fusion = {}
    for cle, l in distant.items():
        vieille = local.get(cle) or {}
        l = dict(l)
        l["arme"] = vieille.get("arme", l.get("arme", True))
        l["dernier_signal"] = vieille.get("dernier_signal") or 0
        fusion[cle] = l
    if fusion != local:
        _ecrire(fusion)
        log(f"[trendlines] {len(fusion)} ligne(s) rapatriee(s)")
    return len(fusion)
