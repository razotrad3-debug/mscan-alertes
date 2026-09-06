"""
La chart des premieres heures : qu'est-ce qui annonce un gros mouvement ?

L'analyse on-chain est fermee (Blockscout de Robinhood limite ou en erreur
sur les cinq routes testees, quota Helius epuise). Reste ce qui est lisible :
les chandelles.

On prend les coins photographies entre le 20/08 et le 02/09 — donc ceux que
le radar a vus — on recupere leurs bougies horaires depuis la naissance de
la pool, et on mesure la FORME des premieres 48 heures. Puis on compare
ceux qui ont fait x3 a ceux qui n'ont jamais depasse +30 %.

Les mesures suivent la methode : impulsion, repli, profondeur du
retracement, tenue du golden pocket, comportement du volume pendant le
repli. Ce sont les questions que se pose le cours, posees a la machine.
"""
import json
import math
import os
import statistics
import sys
import time

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPS = os.path.join(RACINE, "dist", "flow_snapshots")
CACHE = os.path.join(RACINE, "dist", "ohlcv_cache.json")
BASE = "https://api.geckoterminal.com/api/v2"
H = {"Accept": "application/json;version=20230302"}
PAUSE = 2.6           # 30 appels/min autorises : on reste sous la limite


def _get(url, params=None, essais=6):
    for i in range(essais):
        try:
            r = requests.get(url, params=params, headers=H, timeout=30)
        except Exception:
            time.sleep(4 * (i + 1)); continue
        if r.status_code == 429:
            time.sleep(8 * (i + 1)); continue
        if r.status_code != 200:
            return None
        return r.json()
    return None


def bougies(mint, cache):
    """Bougies horaires depuis la naissance de la pool. Mises en cache."""
    if mint in cache:
        return cache[mint]
    d = _get(f"{BASE}/networks/solana/tokens/{mint}/pools")
    time.sleep(PAUSE)
    pools = (d or {}).get("data") or []
    if not pools:
        cache[mint] = None
        return None
    pool = pools[0]["id"].split("_")[-1]
    o = _get(f"{BASE}/networks/solana/pools/{pool}/ohlcv/hour",
             {"aggregate": 1, "limit": 1000})
    time.sleep(PAUSE)
    liste = (((o or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")
    if not liste:
        cache[mint] = None
        return None
    # GeckoTerminal rend du plus recent au plus ancien : on remet dans l'ordre
    liste = sorted(liste, key=lambda b: b[0])
    cache[mint] = liste
    return liste


def forme(bg, heures=48):
    """
    La forme des premieres heures, dans le vocabulaire de la methode.

    bg : [ts, open, high, low, close, volume]
    """
    if not bg or len(bg) < 12:
        return None
    debut = bg[:heures]
    if len(debut) < 12:
        return None
    o0 = debut[0][1] or debut[0][4]
    if not o0:
        return None

    hauts = [b[2] for b in debut]
    bas = [b[3] for b in debut]
    vols = [b[5] or 0 for b in debut]

    # la premiere impulsion : jusqu'ou monte-t-on avant de reculer
    i_haut = max(range(len(hauts)), key=lambda i: hauts[i])
    impulsion = hauts[i_haut] / o0
    if i_haut == 0:
        return None

    # le repli qui suit : le plus bas apres ce haut
    apres = debut[i_haut:]
    if len(apres) < 3:
        return None
    i_bas = min(range(len(apres)), key=lambda i: apres[i][3])
    bas_apres = apres[i_bas][3]
    depart = min(bas[:i_haut + 1])
    amplitude = hauts[i_haut] - depart
    if amplitude <= 0:
        return None
    # ou tombe le repli dans la jambe, en Fibonacci
    retrace = (hauts[i_haut] - bas_apres) / amplitude

    # volume : celui du repli compare a celui de l'impulsion
    v_imp = statistics.mean(vols[:i_haut + 1]) or 1e-9
    v_rep = statistics.mean(v[5] or 0 for v in apres[:max(1, i_bas + 1)])

    # combien de cycles impulsion/repli dans la fenetre
    cycles = 0
    seuil = 0.15
    ref = debut[0][4]
    sens = 0
    for b in debut:
        c = b[4] or ref
        if sens >= 0 and c > ref * (1 + seuil):
            cycles += 1; ref = c; sens = 1
        elif sens <= 0 and c < ref * (1 - seuil):
            ref = c; sens = -1
        elif (sens > 0 and c > ref) or (sens < 0 and c < ref):
            ref = c

    return {
        "impulsion (x)": impulsion,
        "heures avant le haut": float(i_haut),
        "retracement (fib)": retrace,
        "dans le golden pocket": 1.0 if 0.55 <= retrace <= 0.72 else 0.0,
        "repli profond (>0.79)": 1.0 if retrace > 0.786 else 0.0,
        "volume du repli / impulsion": v_rep / v_imp,
        "cycles (15%)": float(cycles),
        "volume moyen 48h ($)": statistics.mean(vols),
        "bougies disponibles": float(len(bg)),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            cache = {}

    # issue mesuree sur les photos : le multiple atteint apres la 1re photo
    issues = {}
    for f in os.listdir(SNAPS):
        try:
            s = json.load(open(os.path.join(SNAPS, f), encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(s, list) or len(s) < 3:
            continue
        p = [x.get("price") or 0 for x in s]
        if not p[0] or not any(p[1:]):
            continue
        issues[f[:-5]] = max(p[1:]) / p[0]

    cibles = sorted(issues, key=lambda m: -issues[m])
    print(f"{len(cibles)} coins a examiner (bougies horaires depuis la naissance)")
    print("GeckoTerminal limite a 30 appels/minute : environ "
          f"{len(cibles) * 2 * PAUSE / 60:.0f} minutes\n")

    lignes = []
    for i, m in enumerate(cibles, 1):
        bg = bougies(m, cache)
        if i % 10 == 0:
            json.dump(cache, open(CACHE, "w"), separators=(",", ":"))
            print(f"   {i}/{len(cibles)} ...")
        fm = forme(bg) if bg else None
        if fm:
            fm["_issue"] = issues[m]
            lignes.append((m, fm))
    json.dump(cache, open(CACHE, "w"), separators=(",", ":"))
    print(f"\n{len(lignes)} coins avec des bougies exploitables")
    json.dump({m: f for m, f in lignes}, open(
        os.path.join(RACINE, "dist", "formes_early.json"), "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
