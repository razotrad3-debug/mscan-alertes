"""
Compare la FORME des premieres heures : gagnants contre perdants.

Lit le cache de bougies constitue par analyse_chart_early.py et mesure,
dans le vocabulaire de la methode : ampleur de la premiere impulsion, ou
tombe le repli en Fibonacci, tenue du golden pocket, comportement du volume
pendant le repli, nombre de cycles avant le vrai depart.

Puis le meme protocole que pour les detenteurs : AUC, et test de bruit.
Une mesure qui separe aussi bien que le hasard ne separe rien.
"""
import json
import os
import random
import statistics
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(RACINE, "dist", "ohlcv_cache.json")
SNAPS = os.path.join(RACINE, "dist", "flow_snapshots")


def forme(bg, heures=48):
    """bg : [ts, open, high, low, close, volume], du plus ancien au plus recent."""
    if not bg or len(bg) < 12:
        return None
    d = bg[:heures]
    if len(d) < 12:
        return None
    o0 = d[0][1] or d[0][4]
    if not o0:
        return None
    hauts = [b[2] for b in d]
    bas = [b[3] for b in d]
    vols = [b[5] or 0 for b in d]

    i_haut = max(range(len(hauts)), key=lambda i: hauts[i])
    if i_haut == 0:
        return None
    impulsion = hauts[i_haut] / o0

    apres = d[i_haut:]
    if len(apres) < 3:
        return None
    i_bas = min(range(len(apres)), key=lambda i: apres[i][3])
    depart = min(bas[:i_haut + 1])
    ampl = hauts[i_haut] - depart
    if ampl <= 0:
        return None
    retrace = (hauts[i_haut] - apres[i_bas][3]) / ampl

    v_imp = statistics.mean(vols[:i_haut + 1]) or 1e-9
    v_rep = statistics.mean([b[5] or 0 for b in apres[:max(1, i_bas + 1)]])

    # reprise apres le repli : le prix reprend-il le haut dans la fenetre ?
    reste = apres[i_bas:]
    reprise = (max(b[2] for b in reste) / hauts[i_haut]) if reste else 0

    cycles, ref, sens = 0, d[0][4], 0
    for b in d:
        c = b[4] or ref
        if sens >= 0 and c > ref * 1.15:
            cycles += 1; ref = c; sens = 1
        elif sens <= 0 and c < ref * 0.85:
            ref = c; sens = -1
        elif (sens > 0 and c > ref) or (sens < 0 and c < ref):
            ref = c

    return {
        "impulsion (x)": impulsion,
        "heures avant le haut": float(i_haut),
        "retracement (fib)": retrace,
        "dans le golden pocket": 1.0 if 0.55 <= retrace <= 0.72 else 0.0,
        "repli au-dela de 0.786": 1.0 if retrace > 0.786 else 0.0,
        "volume repli / impulsion": v_rep / v_imp,
        "reprise du haut (x)": reprise,
        "cycles de 15%": float(cycles),
        "volume moyen 48h": statistics.mean(vols),
        "heures de bougies": float(len(bg)),
    }


def auc(g, p):
    if not g or not p:
        return None
    n = sum(1 if a > b else (0.5 if a == b else 0) for a in g for b in p)
    return n / (len(g) * len(p))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cache = json.load(open(CACHE, encoding="utf-8"))

    issues = {}
    for f in os.listdir(SNAPS):
        try:
            s = json.load(open(os.path.join(SNAPS, f), encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(s, list) or len(s) < 3:
            continue
        p = [x.get("price") or 0 for x in s]
        if p and p[0] and any(p[1:]):
            issues[f[:-5]] = max(p[1:]) / p[0]

    lignes = []
    for m, bg in cache.items():
        if not bg or m not in issues:
            continue
        fm = forme(bg)
        if fm:
            fm["_issue"] = issues[m]
            lignes.append((m, fm))

    G = [f for _, f in lignes if f["_issue"] >= 3.0]
    P = [f for _, f in lignes if f["_issue"] < 1.3]
    print(f"{len(lignes)} coins avec bougies + issue connue")
    print(f"   {len(G)} gagnants (x3+)   {len(P)} perdants (<+30%)\n")
    if len(G) < 4 or len(P) < 8:
        print("pas encore assez de donnees — la collecte continue")
        return

    champs = [c for c in lignes[0][1] if not c.startswith("_")]
    res = []
    for c in champs:
        a = auc([f[c] for f in G], [f[c] for f in P])
        tout = [f[c] for f in G + P]
        alea = []
        for _ in range(300):
            random.shuffle(tout)
            alea.append(auc(tout[:len(G)], tout[len(G):]))
        ec = statistics.pstdev(alea) or 1e-9
        res.append((abs(a - 0.5), a, (a - 0.5) / ec, c))
    res.sort(reverse=True)

    print(f"{'mesure sur les 48 premieres heures':34} {'AUC':>6} {'z':>7}  verdict")
    print("-" * 72)
    for _, a, z, c in res:
        v = "SOLIDE" if abs(z) >= 2.5 else ("faible" if abs(z) >= 1.8 else "rien")
        sens = "+" if a > 0.5 else "-"
        print(f"{c:34} {a:6.3f} {z:>7.1f}  {v:7} gagnants {sens}")

    print(f"\n{'medianes':34} {'gagnants':>12} {'perdants':>12}")
    print("-" * 62)
    for c in champs:
        g = statistics.median(f[c] for f in G)
        p = statistics.median(f[c] for f in P)
        print(f"{c:34} {g:>12.2f} {p:>12.2f}")


if __name__ == "__main__":
    main()
