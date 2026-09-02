"""
MSCAN — Holdings : ce que les wallets suivis DETIENNENT en ce moment.

Difference avec Positions : Positions liste les ACHATS des 72 dernieres heures,
donc surtout des lancements. Ici on lit le portefeuille tel qu'il est
aujourd'hui — ce sont des coins gardes, souvent deja etablis, avec une
capitalisation et une reconnaissance. Le setup n'est pas le meme : on ne
cherche pas l'entree la plus tot, on cherche un point d'entree sous le leur,
sur un coin que le smart money n'a pas lache.

Cout : on lit les comptes de jetons via getTokenAccountsByOwner, methode RPC
standard, au lieu de l'API DAS getAssetsByOwner qui est facturee bien plus
cher et n'existe que chez Helius. Chaque portefeuille est en plus garde en
cache TTL_H heures : les avoirs bougent lentement, les relire a chaque scan ne
faisait que bruler du quota. Consequence utile : quand Helius est a sec, le
RPC public prend le relais et la lecture continue.
"""
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests

import config

CACHE_FILE = config.path("holdings_cache.json")     # {adresse: {at, mints}}
RESULT_FILE = config.path("holdings.json")          # dernier classement calcule

TTL_H = 3.0            # un portefeuille est relu au plus toutes les 3 h
MAX_REFRESH = 45       # portefeuilles relus par passage (le temps borne aussi)
MAX_REFRESH_RAPIDE = 99  # quand Helius repond, plus besoin d'etaler
TTL_SPAM_H = 24.0      # un aimant a spam le reste : inutile de le relire souvent
MIN_HOLDERS = 2        # un coin tenu par une seule adresse n'est pas un signal
MIN_POS_USD = 200.0    # en dessous : poussiere / airdrop, on ne compte pas
MIN_MC = 150_000.0     # coins etablis : en dessous, c'est du lancement
# Un coin peut afficher une belle capitalisation et n'avoir aucun marche
# derriere : PIPPIN sortait en tete du classement avec 21 wallets dessus,
# 4,31 $ de liquidite et 3,41 $ echanges en 24 h. Sa capitalisation de 1,4 M$
# etait une multiplication sans acheteur en face. Calibre sur le classement
# reel : le coin sain le plus faible avait 39 000 $ de liquidite.
MIN_LIQ = 25_000.0
MIN_VOL_24H = 15_000.0
# Au-dessus, ce n'est plus un memecoin mais un actif cote en bourse ou un
# jeton d'infrastructure : PUMP a 4 Md$, WETH a 4,8 Md$. Ces lignes-la ne
# donnent pas de setup, elles encombrent la liste.
MAX_MC = 1_000_000_000.0
# Quotes ou le prix se fait vraiment. Un pool adosse a un jeton exotique peut
# afficher une grosse liquidite et un prix absurde : PUMP est ressorti a
# 21,26 $ (soit 21 000 Md$ de capitalisation) au lieu de 0,0044 $.
QUOTES_FIABLES = {"SOL", "WSOL", "USDC", "USDT", "WETH", "ETH", "USD1", "PYUSD"}
MAX_COINS = 60
MAX_SOLO = 40
DIP_PCT = -10.0        # badge CONVICTION : ils tiennent malgre cette baisse
# Avec les chaines EVM le nombre de mints tenus par 2+ a explose (les memes
# wallets achetent les memes lancements) : le plafond precedent coupait la
# liste avant les vrais coins. Le cache de prix absorbe le surcout.
MAX_PRESELECT = 1200   # mints tenus par 2+ envoyes a DexScreener
# Coins tenus par une seule adresse suivie : un KOL qui entre seul sur un coin
# hype est un signal, meme sans convergence. Le lot est gros (1 700 mints
# environ) mais le cache de prix rend les passages suivants quasi gratuits.
MAX_PRESELECT_SOLO = 600     # jetons NEUFS interroges par passage
TTL_PRIX_S = 2700.0
# Duree maximale d'un passage. Sur le RPC public un portefeuille recalcitrant
# peut couter une minute a lui seul ; sans plafond, un tour de garde s'etirait
# sur une demi-heure. Ce qui n'a pas ete lu l'est au tour suivant.
BUDGET_S = 240.0
# Au-dela, ce n'est plus un portefeuille de trader mais un aimant a spam
# (un wallet vu ici portait 10 910 jetons) : ses "avoirs" ne disent rien et
# ses airdrops se retrouveraient en tete du classement par convergence.
MAX_MINTS_WALLET = 600
# Un wallet Robinhood actif porte couramment 800 lignes sans etre un aimant a
# spam : le plafond ne vaut que pour Solana, ou 600 jetons signalent autre
# chose qu'un trader.
MAX_MINTS_EVM = 4000
# Sonde prealable : on demande d'abord la LISTE des comptes sans leur contenu
# (dataSlice de longueur nulle). Quelques kilo-octets suffisent alors a voir
# qu'un wallet en porte 22 000 — au lieu de telecharger 40 Mo pour le jeter.
SONDE_MAX = MAX_MINTS_WALLET * 3

TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",     # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",     # Token-2022
)
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"

_SESSION = requests.Session()
_DEADLINE = 0.0            # fin du passage en cours (0 = pas de limite)
_HELIUS_KO_UNTIL = 0.0     # quota epuise : on arrete de le solliciter un moment
_HELIUS_OK_AT = 0.0        # derniere reponse utile d'Helius

# Le RPC public limite par IP, et il compte les requetes SIMULTANEES autant
# que leur nombre : mesure faite, trois appels en parallele font echouer 29
# lectures sur 30, la meme charge en file d'attente n'en fait echouer aucune.
# On serialise donc les appels publics et on impose un intervalle. Helius,
# lui, encaisse le parallelisme : cette file ne le concerne pas.
_PUBLIC_GAP = 0.5
_PUBLIC_TOUR = threading.Lock()      # un seul appel public a la fois
_PUBLIC_NEXT = 0.0


def _post_public(method: str, params: list, timeout: int = 45):
    global _PUBLIC_NEXT
    with _PUBLIC_TOUR:
        d = _PUBLIC_NEXT - time.time()
        if d > 0:
            time.sleep(d)
        try:
            return _rpc(PUBLIC_RPC, method, params, timeout)
        finally:
            _PUBLIC_NEXT = time.time() + _PUBLIC_GAP


def _helius_url() -> Optional[str]:
    k = getattr(config, "HELIUS_API_KEY", "")
    return f"https://mainnet.helius-rpc.com/?api-key={k}" if k else None


def _rpc(url: str, method: str, params: list, timeout: int = 60):
    """(code http, resultat) — jamais d'exception."""
    try:
        r = _SESSION.post(url, json={"jsonrpc": "2.0", "id": 1,
                                     "method": method, "params": params},
                          timeout=timeout)
        if r.status_code != 200:
            return r.status_code, None
        return 200, (r.json() or {}).get("result")
    except Exception:
        return 0, None


def rpc(method: str, params: list, essais: int = 3):
    """
    Appel RPC Solana resilient : Helius d'abord, RPC public en repli.

    Partage par les autres modules pour que plus aucune lecture on-chain ne
    depende d'un quota Helius intact.
    """
    global _HELIUS_KO_UNTIL, _HELIUS_OK_AT
    hu = _helius_url()
    if hu and time.time() > _HELIUS_KO_UNTIL:
        code, res = _rpc(hu, method, params, timeout=25)
        if code == 429:
            _HELIUS_KO_UNTIL = time.time() + 900
        elif res is not None:
            _HELIUS_OK_AT = time.time()
            return res
    for essai in range(essais):
        code, res = _post_public(method, params)
        if res is not None:
            return res
        time.sleep(min(2.0, 0.8 * (essai + 1)) + random.random() * 0.4)
    return None


CHAINES_EVM = ("robinhood", "ethereum", "base")


def _comptes_evm(addr: str):
    """
    Avoirs d'une adresse EVM, chaine par chaine.

    Blockscout rend le solde ET un cours pour chaque jeton : on peut donc
    ecarter la poussiere tout de suite, sans passer par DexScreener. Un wallet
    Robinhood porte couramment 800 lignes dont l'essentiel ne vaut rien.

    Retour : ({mint: quantite}, {mint: chaine}) ou None si tout a echoue.
    """
    from mmscanner import sources_evm as evm

    qtes, chaines, ok = {}, {}, False
    for chaine in CHAINES_EVM:
        hotes = evm.ENDPOINTS.get(chaine) or []
        for h in hotes:
            if h["kind"] != "blockscout" or not evm._alive(h["base"]):
                continue
            try:
                r = _SESSION.get(
                    f"{h['base']}/api/v2/addresses/{addr}/token-balances",
                    headers=evm.ENTETES, timeout=30)
                r.raise_for_status()
                lignes = r.json() or []
            except Exception:
                evm._mark_dead(h["base"])
                continue
            ok = True
            for it in lignes:
                t = it.get("token") or {}
                mint = t.get("address_hash") or t.get("address")
                if not mint:
                    continue
                try:
                    dec = int(t.get("decimals") or 0)
                    q = float(it.get("value") or 0) / (10 ** dec)
                    cours = float(t.get("exchange_rate") or 0)
                except Exception:
                    continue
                if q <= 0:
                    continue
                # sans cours connu on garde : DexScreener tranchera
                if cours and q * cours < MIN_POS_USD:
                    continue
                qtes[mint] = qtes.get(mint, 0.0) + q
                chaines[mint] = chaine
            break
    return (qtes, chaines) if ok else None


def _comptes(addr: str):
    """
    {mint: quantite} pour une adresse Solana, soldes nuls exclus.

    Helius d'abord (rapide), RPC public en repli. None = les deux ont echoue :
    on garde alors la valeur precedente du cache plutot que de l'effacer.
    """
    if _DEADLINE and time.time() > _DEADLINE:
        return "budget"        # pas essaye : ni echec, ni oubli

    if addr.startswith("0x"):
        return _comptes_evm(addr)

    total = 0
    for prog in TOKEN_PROGRAMS:
        res = rpc("getTokenAccountsByOwner",
                  [addr, {"programId": prog},
                   {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
        if res is None:
            return None
        total += len(res.get("value") or [])
    if total > SONDE_MAX:
        return {"__spam__": float(total)}      # verdict sans le telechargement

    out = {}
    for prog in TOKEN_PROGRAMS:
        res = rpc("getTokenAccountsByOwner",
                  [addr, {"programId": prog}, {"encoding": "jsonParsed"}])
        if res is None:
            return None      # reponse incomplete : on garde l'ancienne
        for it in (res.get("value") or []):
            try:
                info = it["account"]["data"]["parsed"]["info"]
                qte = float(info["tokenAmount"]["uiAmountString"])
                if qte > 0:
                    out[info["mint"]] = out.get(info["mint"], 0.0) + qte
            except Exception:
                continue
    return out


# ── cache disque ───────────────────────────────────────────────────
def _lire_cache() -> dict:
    # le rafraichissement tourne dans son fil pendant que le scanner lit :
    # une lecture qui tombe pile sur le remplacement du fichier ne doit pas
    # renvoyer un index vide, sinon le smart-money disparait le temps d'un scan
    for essai in range(3):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except FileNotFoundError:
            return {}
        except Exception:
            time.sleep(0.15)
    return {}


def _ecrire_cache(c: dict) -> None:
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def portefeuilles(adresses: List[str], log=print, force: bool = False,
                  refresh: bool = True) -> dict:
    """
    {adresse: {mint: quantite}} — relit ce qui est perime, garde le reste.

    refresh=False : lecture seule du cache. C'est ce qu'utilise le scanner,
    qui ne doit pas attendre le reseau ; le rafraichissement est fait par le
    passage holdings, apres la notation.
    """
    cache = _lire_cache()
    maintenant = time.time()
    def _perime(a):
        e = cache.get(a) or {}
        ttl = TTL_SPAM_H if e.get("spam") else TTL_H
        return force or (e.get("at", 0) < maintenant - ttl * 3600)
    # tant qu'on depend du RPC public il faut etaler ; des qu'Helius repond,
    # tout le tour de garde passe en un seul passage
    budget = (MAX_REFRESH_RAPIDE if maintenant - _HELIUS_OK_AT < 1800
              else MAX_REFRESH)
    perimes = [a for a in adresses if _perime(a)][:budget] if refresh else []

    if perimes:
        global _DEADLINE
        t0 = time.time()
        _DEADLINE = t0 + BUDGET_S
        lus = spam = reste = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            for a, mints in zip(perimes, ex.map(_comptes, perimes)):
                if mints == "budget":
                    reste += 1
                    continue      # on n'y a pas touche : rien a inscrire
                if mints is None:
                    # echec : on retente plus tard, et de plus en plus tard,
                    # sinon une adresse morte occupe un creneau a chaque tour
                    e = cache.get(a) or {"mints": {}}
                    e["ko"] = int(e.get("ko", 0)) + 1
                    recul = min(TTL_H * 3600, 300 * 2 ** min(e["ko"] - 1, 5))
                    e["at"] = time.time() - (TTL_H * 3600 - recul)
                    cache[a] = e
                    continue
                chaines = {}
                if isinstance(mints, tuple):        # lecture EVM
                    mints, chaines = mints
                lus += 1
                plafond = MAX_MINTS_EVM if a.startswith("0x") else MAX_MINTS_WALLET
                if "__spam__" in mints or len(mints) > plafond:
                    # aimant a spam : on retient le verdict, pas les jetons
                    n = int(mints.get("__spam__") or len(mints))
                    cache[a] = {"at": time.time(), "spam": True,
                                "n": n, "mints": {}}
                    spam += 1
                else:
                    cache[a] = {"at": time.time(), "n": len(mints),
                                "mints": mints}
                    if chaines:
                        cache[a]["chains"] = chaines
        _DEADLINE = 0.0
        _ecrire_cache(cache)
        log(f"[holdings] {lus}/{len(perimes)} portefeuilles relus"
            + (f", {spam} ecartes (spam)" if spam else "")
            + (f", {reste} reportes (temps)" if reste else "")
            + f" ({time.time() - t0:.0f}s)")

    # une adresse jamais lue est absente, pas "sans jetons" : la nuance
    # compte pour le comptage smart-money du scanner
    return {a: cache[a].get("mints") or {} for a in adresses
            if a in cache and not cache[a].get("spam") and cache[a].get("n")}


def chaines_connues(adresses: List[str]) -> dict:
    """{mint: chaine} pour les avoirs EVM deja lus."""
    cache = _lire_cache()
    out = {}
    for a in adresses:
        out.update((cache.get(a) or {}).get("chains") or {})
    return out


# ── prix / metriques : un appel DexScreener pour 30 mints ───────────
# DexScreener ne renvoie pas 30 JETONS par appel mais 30 PAIRES. Un jeton
# ayant plusieurs pools, demander 30 adresses d'un coup en fait revenir cinq
# ou six : les autres disparaissent en silence. Mesure faite sur 7 adresses :
# 30 paires renvoyees, 5 jetons couverts, 2 perdus.
PLAFOND_PAIRES = 30
LOT_INITIAL = 10


def _dex_lot(mints: List[str], profondeur: int = 0) -> dict:
    url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(mints)
    data = None
    for essai in range(3):
        try:
            r = _SESSION.get(url, timeout=25)
            if r.status_code == 429:
                time.sleep(1.5 * (essai + 1))
                continue
            r.raise_for_status()
            data = r.json() or {}
            break
        except Exception:
            time.sleep(1.0 * (essai + 1))
    if data is None:
        return {}

    meilleur = {}
    for p in (data.get("pairs") or []):
        base = p.get("baseToken") or {}
        m = base.get("address")
        if not m:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        quote = ((p.get("quoteToken") or {}).get("symbol") or "").upper()
        rang = (quote in QUOTES_FIABLES, liq)
        if m in meilleur and rang <= meilleur[m]["_rang"]:
            continue
        cree = p.get("pairCreatedAt")
        meilleur[m] = {
            "_rang": rang,
            "chain": p.get("chainId") or "solana",
            # l'adresse de la paire vise directement le bon marche : sur une
            # chaine EVM, l'adresse du jeton seule peut ne rien ouvrir
            "pair": p.get("pairAddress") or "",
            "symbol": base.get("symbol") or "?",
            "name": base.get("name") or base.get("symbol") or "?",
            "price_usd": float(p.get("priceUsd") or 0),
            "mc": float(p.get("marketCap") or p.get("fdv") or 0),
            "liquidity_usd": liq,
            "vol_h24": float((p.get("volume") or {}).get("h24") or 0),
            "chg_h24": float((p.get("priceChange") or {}).get("h24") or 0),
            "chg_h6": float((p.get("priceChange") or {}).get("h6") or 0),
            "age_hours": (max(0.0, (time.time() - cree / 1000.0) / 3600.0)
                          if cree else None),
        }
    # reponse tronquee et jetons manquants : on redecoupe plutot que de les
    # perdre. Deux niveaux suffisent en pratique.
    absents = [m for m in mints if m not in meilleur]
    tronquee = len(data.get("pairs") or []) >= PLAFOND_PAIRES
    if absents and tronquee and len(mints) > 1 and profondeur < 4:
        moitie = max(1, len(absents) // 2)
        for part in (absents[:moitie], absents[moitie:]):
            if part:
                meilleur.update(_dex_lot(part, profondeur + 1))
    return meilleur


_PRIX = {}          # mint -> (instant, metriques) ; evite de tout refetcher


def _metriques(mints: List[str], fetch_max: int = None) -> dict:
    """
    Metriques DexScreener, avec un cache court partage entre passages.

    fetch_max borne le nombre de jetons NEUFS interroges par passage : le
    reste attend le tour suivant. Les jetons deja en cache sont rendus quoi
    qu'il arrive, donc la liste s'etoffe au fil des passages au lieu de
    clignoter.
    """
    maintenant = time.time()
    out, a_lire = {}, []
    for m in mints:
        hit = _PRIX.get(m)
        if hit and maintenant - hit[0] < TTL_PRIX_S:
            if hit[1] is not None:
                out[m] = hit[1]
        else:
            a_lire.append(m)
    if fetch_max is not None:
        a_lire = a_lire[:fetch_max]

    if a_lire:
        lots = [a_lire[i:i + LOT_INITIAL]
                for i in range(0, len(a_lire), LOT_INITIAL)]
        frais = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            for d in ex.map(_dex_lot, lots):
                frais.update(d)
        for m in a_lire:
            # on memorise aussi les absences : un jeton sans paire le reste
            _PRIX[m] = (maintenant, frais.get(m))
        out.update(frais)
    return out


def _airdrop(porteurs) -> bool:
    """
    Signature d'un largage : tout le monde a exactement la meme quantite.

    Deux traders qui achetent le meme coin n'obtiennent jamais le meme nombre
    de jetons a la decimale pres. Ces mints-la sont nombreux et, comme ils
    touchent beaucoup d'adresses d'un coup, ils arrivaient en tete du
    classement par convergence.
    """
    qtes = {q for _, q in porteurs}
    return len(qtes) == 1 and len(porteurs) >= 2


# ── classement ─────────────────────────────────────────────────────
def scan(log=print, force: bool = False) -> dict:
    """
    Deux listes : les coins en convergence (plusieurs adresses suivies
    dessus) et ceux qu'une seule adresse suivie tient.
    """
    from mmscanner import followed as fmod
    from mmscanner import safety
    from mmscanner.engine import is_crypto_native

    t0 = time.time()
    registre = fmod.tracked_registry()
    # TES adresses d'abord : le budget de lecture par passage est limite, et
    # ce sont les wallets FOMO/clans qui t'interessent, pas les wallets
    # trouves tout seuls par recurrence.
    # tes adresses d'abord ; et parmi elles les EVM avant les Solana, car
    # elles se lisent en 2 s contre 8 a 20 s sur le RPC public : a budget de
    # temps egal, on couvre bien plus de portefeuilles.
    adresses = sorted(registre,
                      key=lambda a: (registre[a].get("origin") != "suivi",
                                     not a.startswith("0x")))
    if not adresses:
        return {"coins": [], "solo": [], "wallets": 0, "at": time.time(),
                "empty": True}

    avoirs = portefeuilles(adresses, log=log, force=force)

    # mint -> [(adresse, quantite)]
    par_mint: Dict[str, list] = {}
    for a, mints in avoirs.items():
        for m, q in mints.items():
            if m in fmod.QUOTE_MINTS or m in getattr(config, "EXCLUDE_MINTS", ()):
                continue
            par_mint.setdefault(m, []).append((a, q))

    lus = sum(1 for v in avoirs.values() if v)
    retenus = [m for m, v in par_mint.items()
               if len(v) >= MIN_HOLDERS and not _airdrop(v)]
    retenus.sort(key=lambda m: len(par_mint[m]), reverse=True)
    retenus = retenus[:MAX_PRESELECT]


    # coins tenus par une seule adresse SUIVIE : pas de convergence, mais un
    # KOL qui entre seul sur un coin hype reste un signal. Les wallets
    # trouves automatiquement n'entrent pas dans ce lot, trop de bruit.
    solitaires = [m for m, v in par_mint.items()
                  if len(v) == 1
                  and (registre.get(v[0][0]) or {}).get("origin") == "suivi"]


    log(f"[holdings] {lus}/{len(adresses)} portefeuilles connus, "
        f"{len(par_mint)} mints, {len(retenus)} tenus par {MIN_HOLDERS}+, "
        f"{len(solitaires)} tenus seul")

    # les coins en convergence passent en priorite ; les solitaires sont
    # decouverts par vagues, le cache retenant ce qui a deja ete resolu
    infos = _metriques(retenus)
    infos.update(_metriques(solitaires, fetch_max=MAX_PRESELECT_SOLO))
    coins = _batir(retenus, par_mint, infos, registre, MIN_HOLDERS)
    solos = _batir(solitaires, par_mint, infos, registre, 1)

    coins.sort(key=lambda c: (c["holders"], c["value_usd"]), reverse=True)
    coins = _sans_pieges(coins[:MAX_COINS], log)
    # un seul porteur : c'est la taille de sa position qui classe
    solos.sort(key=lambda c: c["value_usd"], reverse=True)
    solos = _sans_pieges(solos[:MAX_SOLO], log)

    res = {"coins": coins, "solo": solos, "wallets": lus,
           "total": len(adresses), "at": time.time(), "empty": False}
    log(f"[holdings] {len(coins)} coins en convergence, {len(solos)} tenus "
        f"seul ({time.time() - t0:.0f}s)")
    save(res)
    return res


def _batir(mints, par_mint, infos, registre, mini: int) -> list:
    """Applique les memes garde-fous a un lot de mints et rend les coins."""
    from mmscanner import safety
    from mmscanner.engine import is_crypto_native

    out = []
    for m in mints:
        d = infos.get(m)
        if not d or not d["price_usd"]:
            continue
        if not is_crypto_native(d["symbol"], d["name"], m):
            continue
        if not (MIN_MC <= d["mc"] <= MAX_MC):
            continue
        if d["liquidity_usd"] < MIN_LIQ or d["vol_h24"] < MIN_VOL_24H:
            continue
        louche, _ = safety.volume_suspect(d["liquidity_usd"], d["vol_h24"],
                                          d.get("age_hours"))
        if louche:
            continue

        # une position sous MIN_POS_USD ne compte pas : c'est de la poussiere
        porteurs, total = [], 0.0
        for a, q in par_mint[m]:
            val = q * d["price_usd"]
            if val < MIN_POS_USD:
                continue
            porteurs.append({"group": (registre.get(a) or {}).get("group") or "Suivi",
                             "usd": val})
            total += val
        if len(porteurs) < mini:
            continue
        porteurs.sort(key=lambda p: p["usd"], reverse=True)

        c = dict(d)
        c.pop("_rang", None)      # critere de choix du pool, pas une donnee
        c.update(mint=m, holders=len(porteurs), value_usd=total,
                 top_usd=porteurs[0]["usd"],
                 by=[p["group"] for p in porteurs],
                 dip=(d["chg_h24"] <= DIP_PCT))
        out.append(c)
    return out


_EN_COURS = threading.Event()


def lancer_en_fond(log=print, sur_fin=None) -> bool:
    """
    Lance un passage holdings dans son propre fil.

    La lecture des portefeuilles dure quelques minutes sur le RPC public. Elle
    n'a aucune raison de retarder le scan suivant ni les alertes : on la sort
    de la boucle. Un seul passage a la fois.
    """
    if _EN_COURS.is_set():
        return False
    _EN_COURS.set()

    def _tour():
        try:
            res = scan(log=log)
            if sur_fin:
                sur_fin(res)
        except Exception as e:
            log(f"[holdings] {e}")
        finally:
            _EN_COURS.clear()

    threading.Thread(target=_tour, daemon=True, name="holdings").start()
    return True


def _sans_pieges(coins: list, log=print) -> list:
    """
    Ecarte les tokens dont l'emetteur garde la main.

    Une freeze authority active lui permet de geler tes jetons apres l'achat,
    une mint authority d'en imprimer autant qu'il veut. PIPPIN, tenu par 21
    adresses suivies, avait les deux.
    """
    from mmscanner import safety

    sol = [c for c in coins if (c.get("chain") or "solana") == "solana"]
    if not sol:
        return coins
    with ThreadPoolExecutor(max_workers=8) as ex:
        etats = list(ex.map(lambda c: safety.authorities(c["mint"]), sol))

    pieges = set()
    for c, e in zip(sol, etats):
        if e.get("inconnu") or e.get("ok"):
            continue
        motifs = []
        if e.get("freeze_authority"):
            motifs.append("freeze authority active")
        if e.get("mint_authority"):
            motifs.append("mint authority active")
        pieges.add(c["mint"])
        log(f"[holdings] {c['symbol']} ecarte : {', '.join(motifs)}")
    return [c for c in coins if c["mint"] not in pieges]


def save(res: dict) -> None:
    try:
        tmp = RESULT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f)
        os.replace(tmp, RESULT_FILE)
    except Exception:
        pass


def load() -> dict:
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        d.setdefault("solo", [])
        return d
    except Exception:
        return {"coins": [], "solo": [], "wallets": 0, "at": 0, "empty": True}
