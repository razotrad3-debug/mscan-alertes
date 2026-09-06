"""
Recherche approfondie : quelle mesure separe reellement les pepites ?

Premiere passe : six mesures, aucune ne separait. On elargit — une
vingtaine de mesures, et surtout un classement OBJECTIF de leur pouvoir de
separation plutot qu'une comparaison de medianes.

L'indicateur retenu est l'AUC : la probabilite qu'un gagnant tire au hasard
ait une valeur plus haute qu'un perdant tire au hasard. 0,50 = la mesure ne
sait rien. 0,70 = elle sait quelque chose. 0,80 = elle sait beaucoup.

On y ajoute un test de solidite que la premiere passe n'avait pas : les
memes calculs sur des groupes tires au hasard. Si une mesure "separe" aussi
bien du bruit, elle ne separe rien.
"""
import json
import math
import os
import random
import statistics

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "dist", "flow_snapshots")
PART_POOL = 0.30


def _parts(s):
    """Parts du supply detenues, pool exclue, de la plus grosse a la plus petite."""
    sold, sup = s.get("holders") or {}, s.get("supply") or 0
    if not sold or sup <= 0:
        return None
    p = sorted((v / sup for v in sold.values()), reverse=True)
    return [x for x in p if x < PART_POOL] or None


def _gini(parts):
    """0 = tout le monde pareil, 1 = un seul detient tout."""
    n = len(parts)
    if n < 2:
        return None
    tri = sorted(parts)
    cum = sum((i + 1) * v for i, v in enumerate(tri))
    tot = sum(tri)
    return (2 * cum) / (n * tot) - (n + 1) / n if tot else None


def mesures(snaps):
    """Tout ce qu'on peut mesurer a la premiere photo, plus l'issue."""
    if len(snaps) < 3:
        return None
    s0, s1 = snaps[0], snaps[1]
    p0 = s0.get("price") or 0
    sup = s0.get("supply") or 0
    b0 = s0.get("holders") or {}
    b1 = s1.get("holders") or {}
    parts = _parts(s0)
    if not parts or p0 <= 0 or sup <= 0 or len(parts) < 20:
        return None
    apres = [s.get("price") or 0 for s in snaps[1:] if (s.get("price") or 0) > 0]
    if not apres:
        return None

    mc = sup * p0
    # valeur en dollars de chaque position
    dollars = sorted((v * p0 for v in b0.values() if v * p0 > 0), reverse=True)
    dollars = [d for d in dollars if d < mc * PART_POOL]
    if len(dollars) < 20:
        return None

    nouveaux = set(b1) - set(b0)
    partis = set(b0) - set(b1)
    communs = set(b0) & set(b1)
    hausse = sum(1 for a in communs if b1[a] > b0[a] * 1.02)
    baisse = sum(1 for a in communs if b1[a] < b0[a] * 0.98)
    dt = max(0.25, (s1.get("ts", 0) - s0.get("ts", 0)) / 3600)

    # ce que les dix premiers font entre les deux photos
    top10 = [a for a, _ in sorted(b0.items(), key=lambda kv: -kv[1])
             if b0[a] / sup < PART_POOL][:10]
    var_top10 = sum(b1.get(a, 0) - b0[a] for a in top10)

    return {
        "_issue": max(apres) / p0,
        "market cap": mc,
        "nb de detenteurs": len(b0),
        "part du 1er": parts[0] * 100,
        "part des 5 premiers": sum(parts[:5]) * 100,
        "part des 10 premiers": sum(parts[:10]) * 100,
        "part des 50 premiers": sum(parts[:50]) * 100,
        "part des 100 premiers": sum(parts[:100]) * 100,
        "part hors 10 premiers": (sum(parts) - sum(parts[:10])) * 100,
        "inegalite (gini)": (_gini(parts) or 0) * 100,
        "position mediane ($)": statistics.median(dollars),
        "position moyenne ($)": statistics.mean(dollars),
        "positions > 1000 $": sum(1 for d in dollars if d > 1000),
        "positions > 10k $": sum(1 for d in dollars if d > 10_000),
        "part des positions > 1000 $": sum(1 for d in dollars if d > 1000) / len(dollars) * 100,
        "supply detenue (%)": sum(parts) * 100,
        "nouveaux par heure": len(nouveaux) / dt,
        "partants par heure": len(partis) / dt,
        "renfort / allegement": hausse / max(1, baisse),
        "part qui renforcent (%)": hausse / max(1, hausse + baisse) * 100,
        "flux des 10 premiers (%)": var_top10 / sup * 100,
        "nb de photos": len(snaps),
    }


def auc(gagnants, perdants):
    """Probabilite qu'un gagnant depasse un perdant. 0,5 = ne sait rien."""
    if not gagnants or not perdants:
        return None
    n = 0
    for g in gagnants:
        for p in perdants:
            n += 1 if g > p else (0.5 if g == p else 0)
    return n / (len(gagnants) * len(perdants))


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    lignes = []
    for f in os.listdir(D):
        try:
            s = json.load(open(os.path.join(D, f), encoding="utf-8"))
        except Exception:
            continue
        m = mesures(s) if isinstance(s, list) else None
        if m:
            lignes.append((f[:-5], m))

    G = [m for _, m in lignes if m["_issue"] >= 3.0]
    P = [m for _, m in lignes if m["_issue"] < 1.3]
    print(f"{len(lignes)} coins exploitables — {len(G)} gagnants (x3+), {len(P)} perdants (<+30%)\n")

    champs = [c for c in lignes[0][1] if not c.startswith("_")]
    res = []
    for c in champs:
        a = auc([m[c] for m in G], [m[c] for m in P])
        if a is None:
            continue
        # bruit : meme calcul sur des groupes au hasard, 200 fois
        tout = [m[c] for m in G + P]
        alea = []
        for _ in range(200):
            random.shuffle(tout)
            alea.append(auc(tout[:len(G)], tout[len(G):]))
        ecart = statistics.pstdev(alea) or 1e-9
        z = (a - 0.5) / ecart
        res.append((abs(a - 0.5), a, z, c))

    res.sort(reverse=True)
    print(f"{'mesure a la 1re photo':32} {'AUC':>6} {'sens':>10} {'z':>7}  verdict")
    print("-" * 78)
    for _, a, z, c in res:
        sens = "gagnants +" if a > 0.5 else "gagnants -"
        verdict = ("solide" if abs(z) >= 2.5 else
                   "faible" if abs(z) >= 1.8 else "rien")
        print(f"{c:32} {a:6.3f} {sens:>10} {z:>7.1f}  {verdict}")
    print("\nz = de combien la mesure depasse ce que donnerait le hasard.")
    print("Sous 1,8, c'est indistinguable du bruit.")


if __name__ == "__main__":
    main()
