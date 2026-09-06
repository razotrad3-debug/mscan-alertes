"""
MSCAN — Pepites : la categorie qui apprend de ses propres resultats.

Une categorie predictive construite sur huit gagnants serait un tirage au
sort deguise. Celle-ci est donc batie autrement : elle n'affirme rien
qu'elle ne puisse justifier, et elle dit toujours sur combien de gagnants
elle repose.

Trois etats :

  PROVISOIRE   le modele vient des mesures du 06/09 (11 gagnants x5+ sur
               116 coins suivis). Faible, et annonce comme tel.
  APPRIS       le journal a assez de gagnants : les criteres sont recalcules
               sur les 32 mesures que le radar fige a chaque alerte, avec
               groupe temoin et test de bruit. Seuls ceux qui depassent le
               hasard sont retenus.
  MUET         rien ne depasse le hasard. On le dit, et on n'affiche rien.

Le troisieme etat est le plus important. Une categorie honnete doit pouvoir
repondre "je ne sais pas".
"""
import json
import os
import random
import statistics
import time
from typing import Dict, List, Optional, Tuple

import config

MODELE = config.path("pepites_modele.json")
GAGNANTS = config.path("pepites_gagnants.json")
PARTAGE_GAGNANTS = "pepites_gagnants.enc"

MIN_GAGNANTS = 25        # en dessous, on n'apprend rien de fiable
Z_MINI = 1.8             # sous ce seuil, la mesure est indistinguable du bruit
MULTIPLE_GAGNANT = 3.0   # ce qu'on appelle un gagnant, faute de mieux
MULTIPLE_PERDANT = 1.3


# --- modele provisoire ---------------------------------------------
# Refait le 06/09 sur 240 coins suivis, dont 11 ont fait x5 ou mieux depuis
# la premiere fois qu'on les a vus. Base de reference : 4,6 %.
#
# La regle tient en une ligne : la cohorte de bots est-elle passee ?
#
#     cohorte >= 1,1 %   ->  25 coins, 8 gagnants, 32,0 %   (7x la base)
#
# Elle a ete choisie non pas parce qu'elle est la plus precise, mais parce
# qu'elle est la seule qui ait SURVECU. En refaisant toute la recherche en
# retirant chaque gagnant a tour de role, on retombe sur elle onze fois sur
# onze. Les regles plus precises trouvees en chemin (jusqu'a 54 %) ne
# rattrapaient aucun gagnant qu'elles n'avaient pas deja vu : elles
# memorisaient.
#
# Ce qui a ete ecarte en route, et pourquoi :
#   - "supply detenue" : ne mesurait que le plafond de pagination a 4 000.
#   - "nb de photos"   : un gagnant survit plus longtemps, donc il est
#                        photographie plus souvent. Artefact.
#   - "departs/h"      : le meme signal que la cohorte, recompte.
#   - la regle a 5 coins et 0 perdant : ces six coins partagent 60 % de
#                        leurs porteurs entre eux. C'est un seul operateur
#                        vu six fois, pas cinq preuves.
PROVISOIRE = {
    "_source": "mesure du 06/09 sur 240 coins, 11 gagnants x5+ — 32 % contre 4,6 % de base",
    "_gagnants": 11,
    "criteres": [
        {"mesure": "cohorte", "sens": "au-dessus", "seuil": 3.5 / 349,
         "poids": 3, "indispensable": True, "requis": True,
         "court": "cohorte presente",
         "pourquoi": "au moins quatre des 349 adresses de bots deja la. "
                     "25 coins concernes sur 240, 8 gagnants : 32 % contre "
                     "4,6 % de base"},
        {"mesure": "hors_pump", "sens": "vrai", "poids": 1,
         "court": "hors pump.fun",
         "pourquoi": "8 des 11 gagnants venaient d'ailleurs, quand 144 des "
                     "193 temoins etaient des pump.fun (11,0 % contre 1,8 %)"},
        {"mesure": "mc", "sens": "entre", "bas": 700_000, "haut": 1_500_000,
         "poids": 1, "court": "tranche 700K-1,5M",
         "pourquoi": "gagnants vus a 1,10 M$ en median contre 637 K$ pour "
                     "les temoins — sous le seuil de bruit (z +1,4)"},
    ],
}
# Deux reserves a dire franchement :
#   - Onze gagnants sur 17 jours, c'est peu. Toute recherche fine sur cette
#     matiere trouvera des motifs qui n'existent pas ; c'est arrive quatre
#     fois dans la journee. La regle ci-dessus est celle qui a resiste, pas
#     une verite etablie.
#   - La liste d'adresses est datee. Ces bots changeront d'adresses ;
#     apprendre() doit la reconstruire des que le journal aura de quoi.
# Toujours pas applicable : les gagnants mettent 11 h a poser leur premier
# sommet contre 3 h aux perdants. Cela porte sur les 48 premieres heures de
# la pool et non sur l'age du coin.


def _lire(chemin, defaut):
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return defaut


def _ecrire(chemin, d):
    try:
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, separators=(",", ":"))
        os.replace(tmp, chemin)
    except Exception:
        pass


def modele() -> dict:
    """Le modele courant : appris s'il existe, provisoire sinon."""
    m = _lire(MODELE, None)
    return m if m and m.get("criteres") else PROVISOIRE


# ── gagnants marques a la main ─────────────────────────────────────
def gagnants() -> dict:
    """Les coins que l'utilisateur a designes comme pepites."""
    d = _lire(GAGNANTS, {})
    if not d:
        try:
            from mmscanner import partage
            d = partage.lire(PARTAGE_GAGNANTS, defaut={}) or {}
        except Exception:
            d = {}
    return d


def marquer(mint: str, symbol: str = "", chain: str = "",
            note: str = "", log=print) -> bool:
    """Designe un coin comme pepite. C'est le jugement de l'utilisateur."""
    if not mint:
        return False
    d = _lire(GAGNANTS, {})
    if mint in d:
        d.pop(mint)                      # deuxieme clic : on retire
        _ecrire(GAGNANTS, d)
        log(f"[pepites] {symbol or mint[:8]} retire des pepites")
    else:
        d[mint] = {"at": time.time(), "symbol": symbol, "chain": chain,
                   "note": note}
        _ecrire(GAGNANTS, d)
        log(f"[pepites] {symbol or mint[:8]} marque comme pepite")
    try:
        from mmscanner import partage
        partage.publier(PARTAGE_GAGNANTS, d, log=log)
    except Exception:
        pass
    return True


# ── notation d'un coin ─────────────────────────────────────────────
def _valeur(p, mesure):
    if mesure == "hors_pump":
        # se deduit de l'adresse : jamais absente, jamais a rafraichir
        if isinstance(p, dict) and p.get("hors_pump") is not None:
            return p["hors_pump"]
        from mmscanner.model import hors_pump
        mint = (p.get("mint") if isinstance(p, dict)
                else getattr(p, "mint", None))
        return hors_pump(mint)
    if isinstance(p, dict):
        return p.get(mesure)
    equiv = {"mc": "market_cap", "liq": "liquidity_usd", "age_h": "age_hours"}
    return getattr(p, equiv.get(mesure, mesure), None)


def noter(p) -> Tuple[int, int, List[str]]:
    """
    Points obtenus / points possibles / ce qui a compte.

    Une mesure absente ne compte ni pour ni contre : on ne devine pas. Mais
    si c'est la mesure sur laquelle le modele repose qui manque, on ne note
    pas du tout. Sans ce garde-fou, un coin serait declare "potentiel" pour
    avoir coche le seul critere faible encore mesurable, et le score affiche
    dirait "1/1" la ou on ne sait rien.
    """
    m = modele()
    obtenus = possibles = 0
    retenus = []
    bloque = False
    for c in m.get("criteres", []):
        v = _valeur(p, c["mesure"])
        if v is None:
            if c.get("indispensable"):
                return 0, 0, []
            continue
        poids = c.get("poids", 1)
        possibles += poids
        ok = False
        sens = c["sens"]
        if sens == "entre":
            ok = c["bas"] <= v <= c["haut"]
        elif sens == "au-dessus":
            ok = v >= c["seuil"]
        elif sens == "en-dessous":
            ok = v <= c["seuil"]
        elif sens == "vrai":
            ok = bool(v)
        elif sens == "faux":
            ok = not v
        if ok:
            obtenus += poids
            retenus.append(c.get("court") or c.get("pourquoi") or c["mesure"])
        elif c.get("requis"):
            # le critere sur lequel repose la categorie n'est pas rempli :
            # les points des bonus ne doivent pas donner l'illusion contraire
            bloque = True
    if bloque:
        return 0, possibles, []
    return obtenus, possibles, retenus


def est_pepite(p, part: float = 0.6) -> bool:
    """
    Retient les coins qui remplissent le critere requis.

    Le seuil est a 0,6 et non 0,75 parce que le critere requis pese 3
    points sur 5 : le remplir seul suffit a entrer, les deux bonus ne
    servent qu'a classer.
    """
    o, poss, _ = noter(p)
    return poss > 0 and o / poss >= part


# ── apprentissage ──────────────────────────────────────────────────
def _auc(g, p):
    if not g or not p:
        return None
    n = sum(1 if a > b else (0.5 if a == b else 0) for a in g for b in p)
    return n / (len(g) * len(p))


def apprendre(log=print) -> dict:
    """
    Recalcule les criteres sur le journal, avec le meme protocole que
    l'analyse manuelle : groupe temoin, AUC, test de bruit.

    Un gagnant est un coin qui a fait x3, OU que l'utilisateur a marque.
    Son jugement vaut au moins autant que le multiple.
    """
    from mmscanner import journal

    # la cohorte se refait sur les photos de soldes, pas sur le journal :
    # ce sont deux matieres differentes, et elle a ses propres garde-fous.
    # Elle refuse d'elle-meme tant qu'elle n'a pas de quoi faire mieux que
    # la liste en place.
    try:
        from mmscanner import cohorte
        etat_coh = cohorte.reconstruire(log=log)
    except Exception as e:
        log(f"[cohorte] {e}")
        etat_coh = {}

    j = journal._lire() or journal.charger_partage()
    marques = set(gagnants())
    G, P = [], []
    for mint, e in j.items():
        depart, suite = e.get("depart") or {}, e.get("suite") or {}
        mc0 = depart.get("mc") or 0
        if not mc0 or "24" not in suite:
            continue                     # pas encore mesurable
        mult = (suite.get("max") or mc0) / mc0
        if mint in marques or mult >= MULTIPLE_GAGNANT:
            G.append(depart)
        elif mult < MULTIPLE_PERDANT:
            P.append(depart)

    etat = {"gagnants": len(G), "perdants": len(P), "quand": time.time(),
            "cohorte": etat_coh.get("verdict", "")}
    if len(G) < MIN_GAGNANTS or len(P) < MIN_GAGNANTS:
        etat["verdict"] = (f"en apprentissage : {len(G)} gagnants et {len(P)} "
                           f"temoins, il en faut {MIN_GAGNANTS} de chaque")
        log("[pepites] " + etat["verdict"])
        return etat

    champs = [k for k in G[0]
              if isinstance(G[0].get(k), (int, float)) and not k.startswith("_")]
    criteres = []
    for c in champs:
        g = [x[c] for x in G if isinstance(x.get(c), (int, float))]
        p = [x[c] for x in P if isinstance(x.get(c), (int, float))]
        if len(g) < MIN_GAGNANTS // 2 or len(p) < MIN_GAGNANTS // 2:
            continue
        a = _auc(g, p)
        tout = g + p
        alea = []
        for _ in range(300):
            random.shuffle(tout)
            alea.append(_auc(tout[:len(g)], tout[len(g):]))
        ec = statistics.pstdev(alea) or 1e-9
        z = (a - 0.5) / ec
        if abs(z) < Z_MINI:
            continue
        if all(isinstance(x, bool) for x in g + p):
            # une mesure oui/non : un seuil numerique n'aurait aucun sens
            criteres.append({
                "mesure": c, "sens": "vrai" if a > 0.5 else "faux",
                "poids": 2 if abs(z) >= 2.5 else 1,
                "court": c if a > 0.5 else "pas " + c,
                "pourquoi": f"{c} : AUC {a:.2f}, z {z:+.1f} sur {len(g)} gagnants",
            })
            continue
        mediane = statistics.median(p)
        criteres.append({
            "mesure": c,
            "sens": "au-dessus" if a > 0.5 else "en-dessous",
            "seuil": mediane,
            "poids": 2 if abs(z) >= 2.5 else 1,
            "court": f"{c} {'>' if a > 0.5 else '<'} {mediane:,.4g}",
            "pourquoi": f"{c} : AUC {a:.2f}, z {z:+.1f} sur {len(g)} gagnants",
        })

    if not criteres:
        etat["verdict"] = ("aucune mesure ne depasse le hasard sur "
                           f"{len(G)} gagnants — la categorie reste muette")
        log("[pepites] " + etat["verdict"])
        _ecrire(MODELE, {"_source": etat["verdict"], "_gagnants": len(G),
                         "criteres": []})
        return etat

    criteres.sort(key=lambda c: -c["poids"])
    m = {"_source": f"appris le {time.strftime('%d/%m')} sur {len(G)} gagnants "
                    f"et {len(P)} temoins",
         "_gagnants": len(G), "criteres": criteres[:8]}
    _ecrire(MODELE, m)
    etat["verdict"] = f"{len(criteres)} critere(s) retenu(s) sur {len(G)} gagnants"
    log("[pepites] " + etat["verdict"])
    return etat


def etat() -> dict:
    """De quoi afficher honnetement sur quoi la categorie repose."""
    m = modele()
    return {"source": m.get("_source", ""), "gagnants": m.get("_gagnants", 0),
            "criteres": len(m.get("criteres", [])),
            "provisoire": m is PROVISOIRE or not _lire(MODELE, None),
            "marques": len(gagnants())}
