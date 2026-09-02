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
}


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

        cle = _cle(infos["chain"], pair, pts)
        vieille = anciennes.get(cle) or {}
        gardees[cle] = {
            "chain": infos["chain"], "pair": pair, "mint": infos["mint"],
            "symbol": infos["symbol"], "outil": outil,
            "t1": pts[0][0], "v1": pts[0][1],
            "t2": pts[1][0] if len(pts) == 2 else None,
            "v2": pts[1][1] if len(pts) == 2 else None,
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


# ── geometrie ──────────────────────────────────────────────────────
def niveau(l: dict, t: float = None) -> Optional[float]:
    """Market cap ou passe la ligne a l'instant t. C'est tout le calcul."""
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
    """Un market cap se lit $160K ; un cours de memecoin a besoin de decimales."""
    from mmscanner import telegram_alerts as tg
    if unite != "prix":
        return tg._usd(v)
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.8f}".rstrip("0")


def _message(l: dict, x: dict, val: float, n: float, ecart: float) -> str:
    from mmscanner import telegram_alerts as tg
    from mmscanner.model import dex_link, gmgn_link

    chain = (x.get("chain") or l.get("chain") or "solana").lower()
    label = config.CHAIN_META.get(chain, {}).get("label", chain.title())
    pastille = tg.PASTILLE.get(chain, "⚪")
    titre = tg._esc(x.get("symbol") or l.get("symbol") or "?")
    unite = l.get("unite") or "mc"
    quoi = "Market Cap" if unite != "prix" else "Cours"
    role = "support" if ecart >= 0 else "resistance"
    cote = "au-dessus" if ecart >= 0 else "sous"
    pente = ""
    if l.get("t2") and l.get("t2") != l.get("t1"):
        par_h = (niveau(l, time.time() + 3600) or n) - n
        mot = "montante" if par_h > 0 else ("descendante" if par_h < 0 else "plate")
        pente = f", {mot}"
    trace = time.strftime("%d/%m a %H:%M", time.localtime(l.get("cree") or 0))

    corps = [
        f"{pastille} *{titre}*",
        f"{label} · Touche de ta ligne",
        "",
        f"- {quoi} : `{_val(val, unite)}`",
        f"- Ta ligne : `{_val(n, unite)}`  (`{ecart*100:+.1f}%` {cote})",
        f"- 5 min : `{x.get('chg_m5', 0):+.0f}%`  ·  1h : `{x.get('chg_h1', 0):+.0f}%`",
        "",
        f"- Ligne tracee le {trace} — {role}{pente}",
        "",
        "Le prix revient sur la ligne que tu as tracee.",
        "C'est ta zone, pas celle du scanner.",
    ]
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
        envoyer = tg.enabled()

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
        if val <= 0 or not n or n <= 0:
            continue
        ecart = val / n - 1.0
        if abs(ecart) > HORS_JEU:
            continue

        # rearmement : la ligne doit s'etre eloignee pour pouvoir resonner
        if abs(ecart) >= APPROCHE:
            if not l.get("arme"):
                l["arme"] = True
                change = True
            continue
        if abs(ecart) > TOLERANCE or not l.get("arme"):
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
        if envoyer and tg.send(_message(l, x, val, n, ecart)):
            envoyees += 1

    if change:
        _ecrire(d)
    return envoyees


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
