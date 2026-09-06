"""
MSCAN — Pepites : la categorie qui apprend de ses propres resultats.

Une categorie predictive construite sur huit gagnants serait un tirage au
sort deguise. Celle-ci est donc batie autrement : elle n'affirme rien
qu'elle ne puisse justifier, et elle dit toujours sur combien de gagnants
elle repose.

Trois etats :

  PROVISOIRE   le modele vient des mesures du 06/09 (15 gagnants sur 106
               coins photographies). Faible, et annonce comme tel.
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


# ── modele provisoire ──────────────────────────────────────────────
# Tire des mesures du 06/09 sur 106 coins photographies. Ce sont les seules
# tendances qui allaient dans le bon sens ; aucune n'etait solide, et le
# libelle de la categorie le rappelle.
PROVISOIRE = {
    "_source": "mesure du 06/09 sur 106 coins, 15 gagnants — tendances faibles",
    "_gagnants": 15,
    "criteres": [
        {"mesure": "mc", "sens": "entre", "bas": 700_000, "haut": 1_500_000,
         "poids": 2, "court": "tranche 700K-1,5M",
         "pourquoi": "26 % de reussite dans cette tranche contre 14 % ailleurs"},
        {"mesure": "mc", "sens": "au-dessus", "seuil": 100_000,
         "poids": 2, "court": "au-dessus de 100K",
         "pourquoi": "aucun gagnant sur 10 coins vus sous 100 K$"},
        {"mesure": "top10_pct", "sens": "en-dessous", "seuil": 0.20,
         "poids": 1, "court": "top10 disperse",
         "pourquoi": "les gagnants sont moins concentres au sommet"},
    ],
}
# Ce qui a ete mesure mais qu'on ne peut PAS appliquer : les gagnants mettent
# 11 h a poser leur premier sommet contre 3 h aux perdants (AUC 0,745, la
# mesure la plus solide du 06/09). Elle porte sur les 48 premieres heures de
# la pool et n'a rien a voir avec l'age du coin — la confondre avec age_hours
# donnerait des points a n'importe quel jeton de plusieurs jours. Elle
# reviendra quand le journal permettra de la calculer sur du direct.


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
    if isinstance(p, dict):
        return p.get(mesure)
    equiv = {"mc": "market_cap", "liq": "liquidity_usd", "age_h": "age_hours"}
    return getattr(p, equiv.get(mesure, mesure), None)


def noter(p) -> Tuple[int, int, List[str]]:
    """
    Points obtenus / points possibles / ce qui a compte.

    Une mesure absente ne compte ni pour ni contre : on ne devine pas.
    """
    m = modele()
    obtenus = possibles = 0
    retenus = []
    for c in m.get("criteres", []):
        v = _valeur(p, c["mesure"])
        if v is None:
            continue
        poids = c.get("poids", 1)
        possibles += poids
        ok = False
        if c["sens"] == "entre":
            ok = c["bas"] <= v <= c["haut"]
        elif c["sens"] == "au-dessus":
            ok = v >= c["seuil"]
        elif c["sens"] == "en-dessous":
            ok = v <= c["seuil"]
        if ok:
            obtenus += poids
            retenus.append(c.get("court") or c.get("pourquoi") or c["mesure"])
    return obtenus, possibles, retenus


def est_pepite(p, part: float = 0.75) -> bool:
    """Retient les coins qui cochent au moins trois quarts du modele."""
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

    etat = {"gagnants": len(G), "perdants": len(P), "quand": time.time()}
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
