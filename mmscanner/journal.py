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
JALONS = (1, 6, 24, 72, 168, 336, 720)   # heures ou l'on releve le market cap
SUIVI_MAX_H = 720.0         # on suit un mois, pas une semaine


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


def _hors_pump(mint):
    from mmscanner.model import hors_pump
    return hors_pump(mint)


def _attente(ecoule_h: float) -> float:
    """
    Delai avant de rejeter un oeil, croissant avec l'age.

    Un coin de deux heures bouge vite et merite d'etre revu souvent ; un coin
    de trois semaines, non. Sans cette progression, suivre un mois d'alertes
    couterait un appel par coin et par passage.
    """
    if ecoule_h < 24:
        return 1800.0            # une demi-heure
    if ecoule_h < 168:
        return 3 * 3600.0        # trois heures
    return 12 * 3600.0


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
        "holders": v("holders"), "foule": v("foule"),
        "cohorte": v("cohorte"),
        "hors_pump": _hors_pump(getattr(p, "mint", "")),
        "top_holder_pct": v("top_holder_pct"),
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
        elif maintenant - (suite.get("vu") or 0) >= _attente(ecoule):
            # et meme sans jalon en attente, on repasse pour le maximum. Sans
            # cela le suivi s'arretait au dernier jalon : un coin qui fait x5
            # le cinquieme jour n'etait jamais compte comme gagnant, alors
            # que c'est exactement ce qu'on cherche a apprendre.
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


# ── les coups gagnants ─────────────────────────────────────────────
SEUIL_WIN = 5.0
MC_BAS = 3_000_000.0        # au-dela, "repere bas" ne veut plus rien dire


def _mc_photos(snaps):
    """(mc au depart, plus haut vu, dernier connu) d'apres les photos."""
    mcs = [(s.get("price") or 0) * (s.get("supply") or 0) for s in snaps]
    mcs = [m for m in mcs if m > 0]
    if not mcs:
        return 0.0, 0.0, 0.0
    return mcs[0], max(mcs), mcs[-1]


def _symboles() -> dict:
    """
    {mint: symbole}, glane dans les fichiers de travail.

    Les photos de soldes ne portaient pas le symbole avant aujourd'hui ; les
    anciennes n'ont donc qu'une adresse. Plutot que de les reecrire pendant
    que l'application tourne, on retrouve les noms ailleurs. Les nouvelles
    photos, elles, se suffisent a elles-memes.
    """
    import config as _cfg

    noms = {}

    def _avaler(obj):
        if isinstance(obj, dict):
            m = obj.get("mint")
            sym = obj.get("symbol") or obj.get("sym")
            if m and sym and m not in noms:
                noms[m] = sym
            for k, v in obj.items():
                if isinstance(v, dict):
                    sym = v.get("symbol") or v.get("sym")
                    if sym and len(k) > 30 and k not in noms:
                        noms[k] = sym
                _avaler(v)
        elif isinstance(obj, list):
            for v in obj[:4000]:
                _avaler(v)

    for nom in ("last_scan.json", "parcours.json", "holdings.json",
                "expansion_watch.json", "followed_buys.json"):
        try:
            with open(_cfg.path(nom), "r", encoding="utf-8") as fh:
                _avaler(json.load(fh))
        except Exception:
            continue
    return noms


_WIN = {"at": 0.0, "liste": [], "cle": None}
TTL_WIN_S = 600.0


def _tous_reperes() -> list:
    """
    Tous les coins qu'on a vus, avec leur point de depart le plus ancien.

    Deux sources : le journal sait a quel market cap l'alerte est partie mais
    ne contient que les coins alertes ; les photos de soldes couvrent tout ce
    qui passe au radar, market cap deduit du prix et du supply.
    """
    import os as _os

    import config as _cfg

    trouves = {}

    for mint, e in (_lire() or charger_partage()).items():
        depart, suite = e.get("depart") or {}, e.get("suite") or {}
        mc0 = depart.get("mc") or 0
        if not mc0:
            continue
        trouves[mint] = {
            "mint": mint, "symbol": depart.get("symbol") or mint[:6],
            "chain": depart.get("chain") or "solana",
            "pair": depart.get("pair") or "",
            "mc0": mc0, "haut": max(suite.get("max") or 0, mc0),
            "dernier": suite.get("dernier") or 0,
            "at": depart.get("at") or 0,
            "grade": depart.get("grade") or "", "source": "alerte",
        }

    d = getattr(_cfg, "SNAPSHOT_DIR", None)
    if d and _os.path.isdir(d):
        for f in _os.listdir(d):
            if not f.endswith(".json"):
                continue
            mint = f[:-5]
            try:
                with open(_os.path.join(d, f), "r", encoding="utf-8") as fh:
                    snaps = json.load(fh)
            except Exception:
                continue
            if not isinstance(snaps, list) or not snaps:
                continue
            mc0, haut, dernier = _mc_photos(snaps)
            if mc0 <= 0:
                continue
            sym = ""
            for s in snaps:
                if s.get("sym"):
                    sym = s["sym"]
                    break
            e = trouves.get(mint)
            if e:
                # Le point de depart est le PLUS ANCIEN des deux, pas celui du
                # journal. OTC a ete alerte a 5,4 M$ alors que le radar l'avait
                # photographie a 753 K$ trois jours plus tot : garder la valeur
                # de l'alerte faisait passer un x8 pour un x1,2 et le sortait
                # de la liste.
                t_photo = snaps[0].get("ts") or 0
                if t_photo and (not e["at"] or t_photo < e["at"]):
                    e["at"] = t_photo
                    e["mc0"] = mc0
                    e["source"] = "radar"
                e["haut"] = max(e["haut"], haut)
                e["dernier"] = e["dernier"] or dernier
                if sym and e["symbol"] == mint[:6]:
                    e["symbol"] = sym
            else:
                trouves[mint] = {
                    "mint": mint, "symbol": sym or mint[:6], "chain": "solana",
                    "pair": "", "mc0": mc0, "haut": haut, "dernier": dernier,
                    "at": snaps[0].get("ts") or 0, "grade": "",
                    "source": "radar",
                }

    noms = _symboles()
    for e in trouves.values():
        if len(e["symbol"]) <= 6 and e["mint"].startswith(e["symbol"]):
            e["symbol"] = noms.get(e["mint"], e["symbol"])   # nom retrouve

    # ceux qui n'ont de nom nulle part : on demande a DexScreener. Ce sont des
    # coins photographies avant qu'on pense a garder le symbole ; une adresse
    # brute dans la liste des coups gagnants ne dit rien a personne.
    orphelins = [e["mint"] for e in trouves.values()
                 if len(e["symbol"]) <= 8 and e["mint"].startswith(e["symbol"])]
    if orphelins:
        try:
            from mmscanner import holdings as _h
            infos = _h._metriques(orphelins[:40])
            for e in trouves.values():
                x = infos.get(e["mint"]) or {}
                if x.get("symbol") and e["mint"].startswith(e["symbol"]):
                    e["symbol"] = x["symbol"]
                    if x.get("pair") and not e["pair"]:
                        e["pair"] = x["pair"]
        except Exception:
            pass

    # Le plus haut connu vient des photos et du journal. Un coin qu'on a
    # cesse de photographier a donc un maximum fige : il ne pourrait plus
    # jamais entrer ici, meme en montant. On va chercher son cours du jour,
    # pour les seuls candidats qui pourraient basculer.
    faibles = [e["mint"] for e in trouves.values()
               if 0 < e["mc0"] <= MC_BAS
               and e["haut"] / e["mc0"] < SEUIL_WIN]
    if faibles:
        try:
            from mmscanner import holdings as _h
            frais = _h._metriques(faibles[:80])
            for e in trouves.values():
                x = frais.get(e["mint"]) or {}
                if (x.get("mc") or 0) > e["haut"]:
                    e["haut"] = x["mc"]
                if x.get("pair") and not e["pair"]:
                    e["pair"] = x["pair"]
        except Exception:
            pass
    return list(trouves.values())


def gagnants(seuil: float = SEUIL_WIN, mc_max: float = MC_BAS) -> list:
    """
    Les coins reperes bas qui ont fait au moins `seuil` fois leur mise.

    Deux multiples, et le second est celui qui compte : depuis la premiere
    vue, et depuis le creux qui a suivi. On repere un coin, il retrace, et
    c'est de ce creux que part l'expansion — c'est la le trade. Compter
    depuis la premiere vue supposerait qu'on achete a l'instant du reperage.
    """
    cle = (seuil, mc_max)
    if (_WIN["cle"] == cle and _WIN["liste"]
            and time.time() - _WIN["at"] < TTL_WIN_S):
        return _WIN["liste"]

    tous = _tous_reperes()
    bougies = _lire_bougies()
    out = []
    for e in tous:
        if e["mc0"] <= 0 or e["mc0"] > mc_max:
            continue                     # pas "repere bas"
        b = bougies.get(e["mint"]) or {}
        e["creux"] = b.get("creux_mc")
        e["mult_creux"] = b.get("mult")
        depuis_vue = e["haut"] / e["mc0"] if e["mc0"] else 0
        e["mult_vue"] = depuis_vue
        e["mult"] = max(depuis_vue, e["mult_creux"] or 0)
        if e["mult"] < seuil:
            continue
        out.append(e)
    out.sort(key=lambda e: -e["mult"])
    _WIN.update(at=time.time(), liste=out, cle=cle)
    return out
# ── le parcours reel, celui qu'on aurait pu prendre ────────────────
# Compter depuis la premiere vue suppose qu'on achete a l'instant du
# reperage. Ce n'est pas la methode : on repere, le coin retrace, et c'est de
# ce creux que part l'expansion. MINI l'a montre — vu a 1,31 M$, retrace a
# 495 K$ trois heures plus tard, puis sommet a 3,62 M$. Depuis la premiere
# vue cela fait x2,8 et le coin sortait de la liste ; depuis le creux, x7,3.
#
# Les photos de soldes ne peuvent pas repondre : elles sont espacees d'au
# moins trente minutes et s'arretent des que le coin quitte le classement.
# Les bougies, elles, couvrent tout. On les relit en fond, pas a l'affichage.
FICHIER_BOUGIES = "win_bougies.json"
# Un parcours deja lu ne bouge pas vite : le creux est passe, le sommet ne
# se deplace qu'a la hausse. Le relire toutes les demi-heures consommait tout
# le budget sur les memes soixante-cinq coins pendant que deux cent trente
# attendaient leur tour. Les coins jamais lus passent maintenant devant.
TTL_BOUGIES_S = 3 * 3600.0      # deja lu, pas encore gagnant
TTL_GAGNANT_S = 12 * 3600.0     # deja gagnant : plus rien a decouvrir de vital
_BOUGIES = None


def _lire_bougies() -> dict:
    global _BOUGIES
    if _BOUGIES is not None:
        return _BOUGIES
    import config as _cfg
    try:
        with open(_cfg.path(FICHIER_BOUGIES), "r", encoding="utf-8") as f:
            _BOUGIES = json.load(f) or {}
    except Exception:
        _BOUGIES = {}
    return _BOUGIES


def _ecrire_bougies() -> None:
    import config as _cfg
    try:
        chemin = _cfg.path(FICHIER_BOUGIES)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_BOUGIES or {}, f, separators=(",", ":"))
        os.replace(tmp, chemin)
    except Exception:
        pass


REPLI_MINI = 0.20    # sous ce reste, ce n'est plus un repli mais un effondrement


def _parcours_bougies(pool: str, chain: str, depuis: float,
                      px_depart: float = 0.0) -> Optional[dict]:
    """
    Creux puis sommet, apres l'instant `depuis`.

    Le sommet est cherche APRES le creux, dans cet ordre : ce qui nous
    interesse est la remontee qui suit le repli.

    Deux garde-fous, sans lesquels ce chiffre serait de la reconstruction
    d'apres coup. On prend le plus bas des CLOTURES et non des meches :
    personne n'achete une impression a 2 643 $ vue une seconde. Et le creux
    doit rester au-dessus du cinquieme du cours de depart — en dessous, le
    coin s'est effondre, il n'a pas retrace, et la remontee mesuree depuis ce
    fond n'a rien d'un trade qu'on aurait pu prendre.
    """
    from mmscanner import sources_gecko as gecko

    lst = gecko.ohlcv(pool, timeframe="hour", aggregate=1, limit=300, chain=chain)
    lst = sorted([x for x in (lst or []) if x and x[0] >= depuis],
                 key=lambda x: x[0])
    if len(lst) < 3:
        return None
    plancher = px_depart * REPLI_MINI if px_depart else 0.0
    haut = max(lst, key=lambda x: x[2])
    avant = [x for x in lst if x[0] <= haut[0] and x[4] > plancher]
    if not avant:
        return None
    creux = min(avant, key=lambda x: x[4])      # clotures, pas meches
    if creux[4] <= 0:
        return None
    return {"creux_px": creux[4], "sommet_px": haut[2],
            "t_creux": creux[0], "t_sommet": haut[0],
            "mult": haut[2] / creux[4], "at": time.time()}


TTL_ECHEC_S = 6 * 3600.0     # une lecture impossible ne se retente pas sans cesse


def rafraichir_parcours(log=print, budget: int = 25) -> int:
    """
    Relit les bougies des candidats, par vagues, en tache de fond.

    Trois economies, apprises a la premiere execution ou vingt-cinq appels
    n'ont rien donne :
      - les chaines que GeckoTerminal ne couvre pas (Robinhood) sont ecartees
        d'emblee, sinon elles consomment tout le budget en echecs ;
      - on commence par les coins les plus proches du seuil, ceux dont la
        reponse changera quelque chose ;
      - un echec est memorise six heures, une reussite trente minutes.
    """
    from mmscanner import holdings as _h
    from mmscanner import sources_gecko as gecko

    cache = _lire_bougies()
    maintenant = time.time()
    attente = []
    for e in _candidats():
        if not gecko.net_for(e.get("chain") or "solana"):
            continue                       # reseau non couvert, inutile d'essayer
        v = cache.get(e["mint"]) or {}
        if not v:
            attente.append((0, e))         # jamais lu : priorite absolue
            continue
        age = maintenant - (v.get("at") or 0)
        mult = v.get("mult") or 0
        if not mult:
            seuil = TTL_ECHEC_S
        elif mult >= SEUIL_WIN:
            seuil = TTL_GAGNANT_S
        else:
            seuil = TTL_BOUGIES_S
        if age < seuil:
            continue
        attente.append((1, e))
    if not attente:
        return 0
    # jamais lus d'abord, puis les plus proches du seuil
    attente.sort(key=lambda t: (t[0], -(t[1]["haut"] / t[1]["mc0"] if t[1]["mc0"] else 0)))
    attente = [e for _, e in attente[:budget]]

    manque = [e["mint"] for e in attente if not e.get("pair")]
    pools = _h._metriques(manque) if manque else {}
    n = 0
    for e in attente:
        pool = e.get("pair") or (pools.get(e["mint"]) or {}).get("pair") or ""
        if not pool:
            cache[e["mint"]] = {"at": maintenant}
            continue
        try:
            sup0 = _supply(e["mint"])
            px0 = (e["mc0"] / sup0) if sup0 else 0.0
            r = _parcours_bougies(pool, e.get("chain") or "solana", e["at"], px0)
        except Exception:
            r = None
        if r:
            # les bougies donnent des prix ; on les rend lisibles en market
            # cap avec le supply de la derniere photo du coin
            sup = _supply(e["mint"])
            if sup:
                r["creux_mc"] = r["creux_px"] * sup
                r["sommet_mc"] = r["sommet_px"] * sup
            n += 1
        cache[e["mint"]] = r or {"at": maintenant}
    _ecrire_bougies()
    _WIN["at"] = 0.0                      # la liste doit se refaire
    if n:
        log(f"[win] parcours relu pour {n} coin(s) sur {len(attente)}")
    return n


def _supply(mint: str) -> float:
    """Le supply vu a la derniere photo, pour traduire un prix en market cap."""
    import config as _cfg
    d = getattr(_cfg, "SNAPSHOT_DIR", None)
    if not d:
        return 0.0
    try:
        with open(os.path.join(d, mint + ".json"), "r", encoding="utf-8") as f:
            snaps = json.load(f)
        return float((snaps[-1] or {}).get("supply") or 0)
    except Exception:
        return 0.0


def _candidats() -> list:
    """Les coins reperes bas — ceux dont le parcours vaut la peine d'etre lu."""
    out = []
    for e in _tous_reperes():
        if 0 < e["mc0"] <= MC_BAS and e.get("at"):
            out.append(e)
    return out
