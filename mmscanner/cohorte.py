"""
MSCAN — Presence de la cohorte sur un coin.

Compter, pour un coin donne, combien des 349 adresses de `cohorte_data` le
detiennent. Fait naivement, cela demanderait de paginer les porteurs de
chaque coin — cher, et muet au-dela de quelques milliers d'adresses.

On prend donc le probleme a l'envers : on lit ce que les 349 detiennent, une
fois, et on en deduit la presence sur tous les coins d'un coup. Le cout ne
depend plus du nombre de coins scannes. C'est exactement le mecanisme deja
utilise pour les smart wallets, et il partage le meme cache.

Une reserve de calibrage, a verifier sur du direct : la mesure d'origine
comptait les bots parmi les 4 000 porteurs visibles d'un coin, alors qu'ici
on lit leurs avoirs reels. Un bot hors des 4 000 etait invisible avant et ne
l'est plus. La presence mesuree ici est donc au moins egale a celle qui a
servi a fixer le seuil, jamais inferieure — le signal peut se declencher un
peu plus souvent qu'annonce.
"""
import time
from typing import Dict, List

from mmscanner import cohorte_data

MINI_PORTEURS = 50        # sous ca, la photo ne veut rien dire
FICHIER_PARTS = "cohorte_parts.json"
_PARTS = None             # mint -> part, garde sur disque
_ADR = None


def adresses() -> List[str]:
    """
    Les adresses en vigueur : celles qu'on a apprises si elles existent,
    la liste d'origine sinon.

    Une liste apprise n'est ecrite que si elle a battu le plancher de bruit
    (voir `reconstruire`), donc s'y fier est sans risque.
    """
    apprise = _apprises()
    return apprise or list(cohorte_data.ADRESSES)


def _apprises() -> List[str]:
    import json as _j
    import os as _o
    try:
        chemin = _fichier_appris()
        if not _o.path.exists(chemin):
            return []
        with open(chemin, "r", encoding="utf-8") as f:
            return list((_j.load(f) or {}).get("adresses") or [])
    except Exception:
        return []


def _ensemble():
    global _ADR
    if _ADR is None:
        _ADR = set(adresses())
    return _ADR


def _signature() -> str:
    """Empreinte de la liste d'adresses : si elle change, le cache est caduc."""
    a = adresses()
    return f"{len(a)}:{a[0][:8] if a else ''}:{a[-1][:8] if a else ''}"


def _charger_parts() -> dict:
    global _PARTS
    if _PARTS is not None:
        return _PARTS
    import json as _j
    import config as _cfg
    _PARTS = {}
    try:
        with open(_cfg.path(FICHIER_PARTS), "r", encoding="utf-8") as f:
            d = _j.load(f) or {}
        if d.get("sig") == _signature():
            _PARTS = d.get("parts") or {}
    except Exception:
        pass
    return _PARTS


def _ecrire_parts() -> None:
    import json as _j
    import os as _o
    import config as _cfg
    try:
        chemin = _cfg.path(FICHIER_PARTS)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _j.dump({"sig": _signature(), "parts": _PARTS or {}}, f,
                    separators=(",", ":"))
        _o.replace(tmp, chemin)
    except Exception:
        pass


def _mesurer(mint: str):
    """
    Lit la PREMIERE photo du coin et calcule la part de cohorte presente.

    Pas la derniere : c'est sur la premiere que la regle a ete mesuree. Ces
    bots entrent et ressortent ; lire la photo du jour reviendrait a demander
    "sont-ils encore la maintenant" alors que la question est "etaient-ils la
    quand on a decouvert le coin".
    """
    import json as _j
    import os as _o

    import config as _cfg

    d = getattr(_cfg, "SNAPSHOT_DIR", None)
    if not d:
        return None
    try:
        with open(_o.path.join(d, mint + ".json"), "r", encoding="utf-8") as f:
            snaps = _j.load(f)
    except Exception:
        return None
    if not isinstance(snaps, list) or not snaps:
        return None
    h = snaps[0].get("holders") or {}
    if len(h) < MINI_PORTEURS:
        return None
    adr = _ensemble()
    return len(adr & set(h)) / max(1, len(adr))


def part(mint, log=print):
    """
    Part de la cohorte presente sur ce coin, ou None tant qu'on ne sait pas.

    Le resultat est garde sur disque, definitivement : la premiere photo d'un
    coin ne change plus une fois prise (holder_flow la preserve au rognage).
    Sans ce cache, chaque affichage de page relisait 240 Mo de photos et
    figeait l'application pendant les scans.

    None n'est pas zero : le modele refuse de noter une mesure absente, la ou
    un zero lui ferait affirmer que la cohorte n'est pas la.
    """
    if not mint:
        return None
    cache = _charger_parts()
    if mint in cache:
        v = cache[mint]
        return None if v is None else float(v)
    p = _mesurer(mint)
    cache[mint] = p
    _ecrire_parts()
    return p


def index(log=print) -> Dict[str, float]:
    """{mint: part}, pour tous les coins dont on a une photo exploitable."""
    import os as _o

    import config as _cfg

    d = getattr(_cfg, "SNAPSHOT_DIR", None)
    if not d or not _o.path.isdir(d):
        return {}
    cache = _charger_parts()
    neuf = False
    out = {}
    for f in _o.listdir(d):
        if not f.endswith(".json"):
            continue
        m = f[:-5]
        if m not in cache:
            cache[m] = _mesurer(m)
            neuf = True
        v = cache[m]
        if v is not None:
            out[m] = float(v)
    if neuf:
        _ecrire_parts()
    return out


def retenu(mint: str, log=print) -> bool:
    p = part(mint, log=log)
    return p is not None and p >= cohorte_data.SEUIL


def couverture() -> dict:
    """De quoi dire honnetement si la mesure est exploitable, et sur quoi."""
    import os as _o

    import config as _cfg

    d = getattr(_cfg, "SNAPSHOT_DIR", None)
    n = 0
    if d and _o.path.isdir(d):
        n = sum(1 for f in _o.listdir(d) if f.endswith(".json"))
    return {"adresses": len(adresses()), "coins_photographies": n,
            "seuil": cohorte_data.SEUIL, "quand": cohorte_data.QUAND,
            "apprise": bool(_apprises())}


# ── reconstruction ─────────────────────────────────────────────────
# La liste d'adresses vieillira : ces bots changent de wallets. Ce qui suit
# refait, tout seul, l'analyse qui l'a produite — a partir des seules photos
# de soldes, qui contiennent deja tout : les porteurs, le prix et le supply,
# donc le market cap, donc l'issue.
#
# Le protocole est celui applique a la main le 06/09, y compris ses garde-fous.
# Sans eux la reconstruction trouverait toujours quelque chose : avec assez de
# wallets et peu de gagnants, un cluster apparait par pur hasard.
import json
import os
import random
import statistics

# 25 gagnants, comme pour l'apprentissage du modele. Avec dix, la procedure
# atteint deja 33 % de precision sur des etiquettes melangees : elle a de
# quoi surajuster, et remplacer une liste validee sur une marge pareille
# serait un mauvais echange.
MIN_GAGNANTS = 25
MIN_TEMOINS = 40
MARGE = 1.5             # il faut depasser le plancher de moitie, pas le froler
MULT_GAGNANT = 5.0
MULT_TEMOIN = 1.5
MIN_TIRAGES = 12        # tirages a etiquettes melangees pour le plancher
APPRISE = "cohorte_apprise.json"


def _fichier_appris():
    import config
    return config.path(APPRISE)


def _parcours(log=print):
    """
    {mint: (issue, porteurs de la premiere photo)}.

    L'issue vient des photos elles-memes : le market cap est le prix
    multiplie par le supply, tous deux presents dans chaque photo.
    """
    import config
    d = getattr(config, "SNAPSHOT_DIR", None)
    if not d or not os.path.isdir(d):
        return {}
    out = {}
    for f in os.listdir(d):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                snaps = json.load(fh)
        except Exception:
            continue
        if not isinstance(snaps, list) or not snaps:
            continue
        mcs = [(s.get("price") or 0) * (s.get("supply") or 0) for s in snaps]
        mc0 = mcs[0]
        if mc0 < 20_000:
            continue                     # trop petit : le multiple ne veut rien dire
        porteurs = set((snaps[0].get("holders") or {}).keys())
        if len(porteurs) < 50:
            continue
        out[f[:-5]] = (max(mcs) / mc0, porteurs)
    return out


def _batir(refs, temoins, membres):
    """Wallets presents dans >=3 gagnants et dans au plus 10 % des temoins."""
    if not refs or not temoins:
        return set()
    compte = {}
    for i in refs:
        for w in membres[i]:
            compte[w] = compte.get(w, 0) + 1
    cand = {w for w, n in compte.items() if n >= 3}
    if not cand:
        return set()
    plafond = max(1, int(0.10 * len(temoins)))
    vus = {}
    for i in temoins:
        for w in (membres[i] & cand):
            vus[w] = vus.get(w, 0) + 1
    return {w for w in cand if vus.get(w, 0) <= plafond}


def _seuil(cohorte):
    """La part validee, quelle que soit la taille de la liste."""
    return cohorte_data.SEUIL


def _porte(cohorte, membres, i):
    return len(cohorte & membres[i]) / max(1, len(cohorte)) >= _seuil(cohorte)


def _apparies(G, P, membres):
    """
    Temoins de taille comparable aux gagnants.

    Sans cet appariement la cohorte se contente de distinguer les gros coins
    des petits : un wallet a mecaniquement plus de chances d'apparaitre dans
    un registre de 4 000 porteurs que dans un de 300.
    """
    if not G:
        return list(P)
    seuil = min(len(membres[i]) for i in G) * 0.8
    apparies = [i for i in P if len(membres[i]) >= seuil]
    return apparies if len(apparies) >= MIN_TEMOINS // 2 else list(P)


def _evaluer(G, P, membres):
    """
    Precision hors echantillon.

    Deux precautions, sans lesquelles n'importe quel bruit passe :
      - chaque gagnant est juge par une cohorte batie SANS lui ;
      - les temoins sont coupes en deux, une moitie sert a batir la cohorte,
        l'autre seulement a compter les faux positifs. Sinon les temoins sont
        exclus par construction et il n'y a jamais de faux positif.
    """
    if len(G) < 3 or len(P) < 4:
        return 0.0, 0, 0
    P = list(P)
    moitie = len(P) // 2
    Pbat, Ptest = P[:moitie], P[moitie:]
    pris = 0
    for i in G:
        c = _batir([g for g in G if g != i], Pbat, membres)
        if c and _porte(c, membres, i):
            pris += 1
    c = _batir(G, Pbat, membres)
    if not c:
        return 0.0, 0, 0
    faux = sum(1 for i in Ptest if _porte(c, membres, i))
    # On raisonne en TAUX, puis on applique la prevalence reelle. Compter les
    # faux positifs bruts sur un groupe temoin huit fois plus grand que celui
    # des gagnants les rendrait, selon le sens de la mise a l'echelle, soit
    # ecrasants soit gratuits. Ici la question posee est la bonne : parmi les
    # coins que la cohorte designe, quelle part sont des gagnants ?
    tg = pris / len(G)
    tp = faux / max(1, len(Ptest))
    base = len(G) / max(1, len(G) + len(P))
    denom = tg * base + tp * (1 - base)
    return ((tg * base / denom) if denom else 0.0), pris, faux


def reconstruire(log=print, ecrire: bool = True) -> dict:
    """
    Refait la cohorte sur les photos accumulees, et ne la retient que si
    elle bat un plancher de bruit mesure.

    Le plancher n'est pas une convention : on relance exactement la meme
    recherche, evaluation comprise, sur des etiquettes melangees. Ce qu'elle
    trouve alors est ce que le hasard donne sur cette matiere. Il faut faire
    mieux, sinon on ne remplace rien.
    """
    parc = _parcours(log=log)
    if not parc:
        return {"verdict": "aucune photo de soldes exploitable"}
    mints = sorted(parc)
    membres = [parc[m][1] for m in mints]
    issues = [parc[m][0] for m in mints]
    G = [i for i, x in enumerate(issues) if x >= MULT_GAGNANT]
    P0 = [i for i, x in enumerate(issues) if x < MULT_TEMOIN]
    etat = {"coins": len(mints), "gagnants": len(G), "temoins": len(P0)}
    if len(G) < MIN_GAGNANTS or len(P0) < MIN_TEMOINS:
        etat["verdict"] = (f"pas assez de matiere : {len(G)} gagnants et "
                           f"{len(P0)} temoins, il en faut {MIN_GAGNANTS} "
                           f"et {MIN_TEMOINS}")
        log("[cohorte] " + etat["verdict"])
        return etat

    P = _apparies(G, P0, membres)
    prec, pris, faux = _evaluer(G, P, membres)
    base = len(G) / max(1, len(G) + len(P))

    plancher = []
    pool = G + P
    for _ in range(MIN_TIRAGES):
        melange = pool[:]
        random.shuffle(melange)
        plancher.append(_evaluer(melange[:len(G)], melange[len(G):], membres)[0])
    haut = max(plancher) if plancher else 1.0

    neuve = _batir(G, P, membres)
    etat.update(apparies=len(P), adresses=len(neuve), precision=prec,
                rattrapes=pris, base=base, plancher=haut,
                gain=(prec / base if base else 0))
    if not neuve or prec < haut * MARGE:
        etat["verdict"] = (f"reconstruction refusee : {prec:.0%} de precision "
                           f"hors echantillon, il en faudrait {haut*MARGE:.0%} "
                           f"(le hasard atteint deja {haut:.0%} ici)")
        log("[cohorte] " + etat["verdict"])
        return etat

    etat["verdict"] = (f"{len(neuve)} adresses, {prec:.0%} de precision hors "
                       f"echantillon contre {base:.0%} de base "
                       f"({prec/base:.1f}x), plancher du hasard {haut:.0%} "
                       f"— {pris}/{len(G)} gagnants rattrapes")
    log("[cohorte] " + etat["verdict"])
    if ecrire:
        try:
            tmp = _fichier_appris() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"adresses": sorted(neuve), "quand": time.time(),
                           "precision": prec, "base": base, "plancher": haut,
                           "gagnants": len(G), "temoins": len(P)}, f)
            os.replace(tmp, _fichier_appris())
            # la liste change : tout ce qui a ete mesure avec l'ancienne est
            # caduc. La signature du cache s'en chargera, mais on vide aussi
            # ce qu'on a en memoire.
            global _PARTS, _ADR
            _PARTS = {}
            _ADR = None
            log(f"[cohorte] nouvelle liste ecrite ({len(neuve)} adresses)")
        except Exception as e:
            log(f"[cohorte] ecriture : {e}")
    return etat
