"""
MSCAN — Le journal des alertes : ce qu'on voyait, et ce qui s'est passe.

L'analyse des pepites a bute sur un mur : huit gagnants. Assez pour dire ce
qui NE marche pas — le retracement ne trie rien, la concentration non plus —
pas assez pour dire ce qui marche.

Le probleme n'etait pas la methode, c'etait la matiere. On analysait des
photos de detenteurs qui n'avaient jamais ete prises pour ca, alors que le
radar calcule a chaque scan trente mesures bien plus fines : note, phase,
RSI, ratios de volume, equilibre achats/ventes, age, smart wallets. Rien de
tout cela n'etait conserve avec ce que le coin est devenu.

Ce module le conserve. A chaque alerte envoyee, il fige ce que le radar
voyait a cet instant. Puis il revient mesurer : ou en est le market cap une
heure apres, six heures, un jour, trois jours, et le plus haut atteint.

Dans quelques semaines, la meme analyse — groupe temoin et test de bruit —
aura de quoi repondre.
"""
import json
import os
import time
from typing import Dict, List, Optional

import config

FICHIER = config.path("journal_alertes.json")
PARTAGE = "journal_alertes.enc"
MAX = 3000                  # au-dela, on oublie les plus anciennes
JALONS = (1, 6, 24, 72)     # heures ou l'on releve le market cap
SUIVI_MAX_H = 168.0         # on cesse de suivre au bout d'une semaine


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
            json.dump(d, f, separators=(",", ":"))
        os.replace(tmp, FICHIER)
    except Exception:
        pass


def _photo(p) -> dict:
    """Tout ce que le radar sait d'une paire, au moment ou elle part."""
    def v(nom, defaut=None):
        x = getattr(p, nom, defaut)
        return x if x is not None else defaut

    return {
        "symbol": v("symbol", "?"), "chain": v("chain", "solana"),
        "pair": v("pair_address", ""),
        "grade": v("grade", ""), "score": v("score", 0),
        "phase": v("phase", ""),
        "mc": v("market_cap", 0.0), "liq": v("liquidity_usd", 0.0),
        "vol_h24": v("vol_h24", 0.0), "vol_h6": v("vol_h6", 0.0),
        "vol_h1": v("vol_h1", 0.0), "vol_m5": v("vol_m5", 0.0),
        "chg_m5": v("chg_m5", 0.0), "chg_h1": v("chg_h1", 0.0),
        "chg_h6": v("chg_h6", 0.0), "chg_h24": v("chg_h24", 0.0),
        "achats_h1": v("buys_h1", 0), "ventes_h1": v("sells_h1", 0),
        "achats_h24": v("buys_h24", 0), "ventes_h24": v("sells_h24", 0),
        "age_h": v("age_hours", 0.0),
        "holders": v("holders"), "top_holder_pct": v("top_holder_pct"),
        "top10_pct": v("top10_pct"),
        "smart_holders": v("smart_holders", 0),
        "smart_accumulating": v("smart_accumulating", 0),
        "rsi_15m": v("rsi_15m"), "rsi_1h": v("rsi_1h"), "rsi_4h": v("rsi_4h"),
    }


def noter(p, source: str = "scan", log=print) -> bool:
    """
    Fige ce que le radar voyait au moment de l'alerte.

    `p` est une paire notee (source "scan") ou un dictionnaire deja pret
    (source "pullback"), auquel cas on prend ce qu'il contient.
    """
    mint = getattr(p, "mint", None) or (p.get("mint") if isinstance(p, dict) else None)
    if not mint:
        return False
    d = _lire()
    if mint in d:
        return False              # deja note : on ne rejoue pas une alerte
    photo = _photo(p) if not isinstance(p, dict) else {
        k: p.get(k) for k in ("symbol", "chain", "pair", "grade", "score",
                              "phase", "mc", "liq", "chg_m5", "chg_h1")}
    photo.update(at=time.time(), source=source,
                 jour=time.strftime("%Y-%m-%d"))
    d[mint] = {"depart": photo, "suite": {}}
    if len(d) > MAX:
        for k in sorted(d, key=lambda k: d[k]["depart"].get("at", 0))[:len(d) - MAX]:
            d.pop(k, None)
    _ecrire(d)
    log(f"[journal] {photo.get('symbol')} note ({source}, {photo.get('grade') or '-'})")
    return True


def suivre(log=print, budget: int = 120) -> int:
    """
    Revient mesurer ce que les coins notes sont devenus.

    On ne redemande le prix que pour ceux dont un jalon est atteint : une
    alerte d'il y a deux heures n'a rien de neuf a dire avant la sixieme.
    """
    from mmscanner import holdings

    d = _lire()
    if not d:
        return 0
    maintenant = time.time()
    a_voir = []
    for mint, e in d.items():
        depart = e.get("depart") or {}
        at = depart.get("at") or 0
        ecoule = (maintenant - at) / 3600
        if ecoule > SUIVI_MAX_H:
            continue
        suite = e.get("suite") or {}
        # un jalon franchi et pas encore releve ?
        du = [str(j) for j in JALONS if ecoule >= j and str(j) not in suite]
        if du or "max" not in suite or ecoule < 1:
            a_voir.append(mint)
    if not a_voir:
        return 0
    a_voir = a_voir[:budget]

    infos = holdings._metriques(a_voir, frais=True)
    n = 0
    for mint in a_voir:
        x = infos.get(mint)
        if not x:
            continue
        mc = x.get("mc") or 0
        if mc <= 0:
            continue
        e = d[mint]
        depart = e.get("depart") or {}
        suite = e.setdefault("suite", {})
        ecoule = (maintenant - (depart.get("at") or 0)) / 3600
        mc0 = depart.get("mc") or 0
        for j in JALONS:
            if ecoule >= j and str(j) not in suite:
                suite[str(j)] = mc
        haut = suite.get("max") or mc0
        if mc > haut:
            suite["max"] = mc
        suite.setdefault("max", max(mc, mc0))
        suite["dernier"] = mc
        suite["vu"] = maintenant
        n += 1
    _ecrire(d)
    _publier(log)
    return n


def _publier(log=print) -> bool:
    """
    Pousse le journal chiffre sur le depot.

    Le cloud garde son etat dans un cache GitHub qui peut disparaitre a
    tout moment. Un journal qu'on perd ne sert a rien : celui-la survit.
    """
    try:
        from mmscanner import partage
        return partage.publier(PARTAGE, _lire(), log=log)
    except Exception:
        return False


def charger_partage() -> dict:
    """Le journal tel que le cloud l'a publie — pour l'application."""
    try:
        from mmscanner import partage
        return partage.lire(PARTAGE, defaut={}) or {}
    except Exception:
        return {}


def resume() -> dict:
    """Un etat des lieux lisible : combien de notes, combien exploitables."""
    d = _lire() or charger_partage()
    total = len(d)
    murs = 0
    gagnants = 0
    for e in d.values():
        depart, suite = e.get("depart") or {}, e.get("suite") or {}
        mc0 = depart.get("mc") or 0
        if not mc0 or "24" not in suite:
            continue
        murs += 1
        if (suite.get("max") or 0) / mc0 >= 3:
            gagnants += 1
    return {"notes": total, "mesurables": murs, "gagnants": gagnants}
