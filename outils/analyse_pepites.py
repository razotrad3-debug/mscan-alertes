"""
Qu'est-ce qui distinguait les pepites, AVANT qu'elles montent ?

On dispose de 219 coins photographies entre le 20/08 et le 02/09 : pour
chacun, une suite de releves {instant, prix, supply, solde de chaque
detenteur}. Certains ont fait x10, d'autres ont perdu 80 %.

La question n'est pas "qu'ont en commun les gagnants" — cinq gagnants ont
toujours quelque chose en commun, et les perdants l'ont souvent aussi. La
question est : quelle mesure, prise a la PREMIERE photo, separe les deux ?

On ne regarde donc que ce qui etait connaissable au moment ou le radar a vu
le coin, et on compare la suite.
"""
import json
import os
import statistics
import sys

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "dist", "flow_snapshots")

GAGNANT = 3.0        # a fait au moins x3 apres la premiere photo
PERDANT = 1.3        # n'a jamais depasse +30 %
PART_POOL = 0.30     # au-dela, ce n'est pas un detenteur mais la pool


def profil(snaps):
    """Ce qu'on pouvait mesurer a la premiere photo, et ce qui a suivi."""
    if len(snaps) < 3:
        return None
    s0 = snaps[0]
    sold = s0.get("holders") or {}
    supply = s0.get("supply") or 0
    p0 = s0.get("price") or 0
    if not sold or supply <= 0 or p0 <= 0:
        return None

    # la pool n'est pas un detenteur : on l'ecarte comme partout ailleurs
    parts = sorted(((v / supply) for v in sold.values()), reverse=True)
    parts = [p for p in parts if p < PART_POOL]
    if len(parts) < 10:
        return None

    prix = [s.get("price") or 0 for s in snaps]
    apres = [p for p in prix[1:] if p > 0]
    if not apres:
        return None

    # combien de detenteurs ont augmente entre la 1re et la 2e photo
    s1 = snaps[1]
    b1 = s1.get("holders") or {}
    communs = set(sold) & set(b1)
    hausse = sum(1 for a in communs if b1[a] > sold[a] * 1.02)
    baisse = sum(1 for a in communs if b1[a] < sold[a] * 0.98)

    return {
        "mc0": supply * p0,
        "n_holders": len(sold),
        "top1": parts[0] * 100,
        "top5": sum(parts[:5]) * 100,
        "top10": sum(parts[:10]) * 100,
        "top50": sum(parts[:50]) * 100,
        "part_mediane": statistics.median(parts) * 100,
        "croissance_holders": (len(b1) / len(sold) - 1) * 100,
        "ratio_acheteurs": (hausse / max(1, hausse + baisse)) * 100,
        "n_photos": len(snaps),
        "multiple_max": max(apres) / p0,
        "multiple_fin": apres[-1] / p0,
    }


def main():
    profils = {}
    for f in os.listdir(D):
        if not f.endswith(".json"):
            continue
        try:
            snaps = json.load(open(os.path.join(D, f), encoding="utf-8"))
        except Exception:
            continue
        p = profil(snaps) if isinstance(snaps, list) else None
        if p:
            profils[f[:-5]] = p

    gagnants = {k: v for k, v in profils.items() if v["multiple_max"] >= GAGNANT}
    perdants = {k: v for k, v in profils.items() if v["multiple_max"] < PERDANT}

    print(f"{len(profils)} coins exploitables (>= 3 photos)")
    print(f"   {len(gagnants):3} ont fait au moins x{GAGNANT:.0f}")
    print(f"   {len(perdants):3} n'ont jamais depasse +{(PERDANT-1)*100:.0f} %")
    print(f"   {len(profils)-len(gagnants)-len(perdants):3} entre les deux, ecartes de la comparaison\n")

    if not gagnants or not perdants:
        print("pas assez de contraste pour comparer")
        return

    champs = [("mc0", "market cap a la 1re photo", "$"),
              ("n_holders", "nombre de detenteurs", ""),
              ("top1", "part du 1er detenteur", "%"),
              ("top5", "part des 5 premiers", "%"),
              ("top10", "part des 10 premiers", "%"),
              ("top50", "part des 50 premiers", "%"),
              ("part_mediane", "part du detenteur median", "%"),
              ("croissance_holders", "croissance des detenteurs", "%"),
              ("ratio_acheteurs", "detenteurs qui renforcent", "%")]

    print(f"{'mesure a la 1re photo':30} {'gagnants':>14} {'perdants':>14}   ecart")
    print("-" * 78)
    for cle, nom, unite in champs:
        g = statistics.median(v[cle] for v in gagnants.values())
        p = statistics.median(v[cle] for v in perdants.values())
        if unite == "$":
            fg, fp = f"${g:,.0f}", f"${p:,.0f}"
        else:
            fg, fp = f"{g:,.1f}{unite}", f"{p:,.1f}{unite}"
        rapport = (g / p) if p else float("inf")
        marque = "  <<<" if (rapport > 1.5 or rapport < 0.67) else ""
        print(f"{nom:30} {fg:>14} {fp:>14}   x{rapport:5.2f}{marque}")

    print(f"\n(mediane de chaque groupe ; << signale un ecart d'au moins 50 %)")

    print(f"\n{'les gagnants, un par un':30} {'x max':>8} {'MC depart':>12} "
          f"{'holders':>8} {'top10':>7}")
    print("-" * 70)
    for k, v in sorted(gagnants.items(), key=lambda kv: -kv[1]["multiple_max"])[:15]:
        print(f"{k[:28]:30} x{v['multiple_max']:6.1f} {v['mc0']:>12,.0f} "
              f"{v['n_holders']:>8} {v['top10']:>6.1f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
