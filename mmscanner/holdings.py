"""
MSCAN — Holdings : ce que les wallets suivis DETIENNENT en ce moment.

Difference avec Positions : Positions liste les ACHATS des 72 dernieres heures,
donc surtout des lancements. Ici on lit le portefeuille tel qu'il est
aujourd'hui — ce sont des coins gardes, souvent deja etablis, avec une
capitalisation et une reconnaissance. Le setup n'est pas le meme : on ne
cherche pas l'entree la plus tot, on cherche un repli sur un coin que le smart
money n'a pas lache.

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
MAX_REFRESH = 30       # portefeuilles relus par passage sur le RPC public
MAX_REFRESH_RAPIDE = 99  # quand Helius repond, plus besoin d'etaler
TTL_SPAM_H = 24.0      # un aimant a spam le reste : inutile de le relire souvent
MIN_HOLDERS = 2        # un coin tenu par une seule adresse n'est pas un signal
MIN_POS_USD = 200.0    # en dessous : poussiere / airdrop, on ne compte pas
MIN_MC = 150_000.0     # coins etablis : en dessous, c'est du lancement
MAX_COINS = 60
DIP_PCT = -10.0        # repli sur 24 h a partir duquel on signale le setup
MAX_PRESELECT = 600    # mints envoyes a DexScreener par scan (30 par appel)
# Au-dela, ce n'est plus un portefeuille de trader mais un aimant a spam
# (un wallet vu ici portait 10 910 jetons) : ses "avoirs" ne disent rien et
# ses airdrops se retrouveraient en tete du classement par convergence.
MAX_MINTS_WALLET = 600
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


def rpc(method: str, params: list, essais: int = 4):
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
        time.sleep(1.0 * (essai + 1) + random.random() * 0.5)
    return None


def _comptes(addr: str) -> Optional[Dict[str, float]]:
    """
    {mint: quantite} pour une adresse Solana, soldes nuls exclus.

    Helius d'abord (rapide), RPC public en repli. None = les deux ont echoue :
    on garde alors la valeur precedente du cache plutot que de l'effacer.
    """
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
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
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
        t0 = time.time()
        lus = spam = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            for a, mints in zip(perimes, ex.map(_comptes, perimes)):
                if mints is None:
                    # echec : on retente plus tard, et de plus en plus tard,
                    # sinon une adresse morte occupe un creneau a chaque tour
                    e = cache.get(a) or {"mints": {}}
                    e["ko"] = int(e.get("ko", 0)) + 1
                    recul = min(TTL_H * 3600, 300 * 2 ** min(e["ko"] - 1, 5))
                    e["at"] = time.time() - (TTL_H * 3600 - recul)
                    cache[a] = e
                    continue
                lus += 1
                if "__spam__" in mints or len(mints) > MAX_MINTS_WALLET:
                    # aimant a spam : on retient le verdict, pas les jetons
                    n = int(mints.get("__spam__") or len(mints))
                    cache[a] = {"at": time.time(), "spam": True,
                                "n": n, "mints": {}}
                    spam += 1
                else:
                    cache[a] = {"at": time.time(), "n": len(mints),
                                "mints": mints}
        _ecrire_cache(cache)
        log(f"[holdings] {lus}/{len(perimes)} portefeuilles relus"
            + (f", {spam} ecartes (spam)" if spam else "")
            + f" ({time.time() - t0:.0f}s)")

    # une adresse jamais lue est absente, pas "sans jetons" : la nuance
    # compte pour le comptage smart-money du scanner
    return {a: cache[a].get("mints") or {} for a in adresses
            if a in cache and not cache[a].get("spam") and cache[a].get("n")}


# ── prix / metriques : un appel DexScreener pour 30 mints ───────────
def _dex_lot(mints: List[str]) -> dict:
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
        if m in meilleur and liq <= meilleur[m]["liquidity_usd"]:
            continue
        cree = p.get("pairCreatedAt")
        meilleur[m] = {
            "chain": p.get("chainId") or "solana",
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
    return meilleur


def _metriques(mints: List[str]) -> dict:
    lots = [mints[i:i + 30] for i in range(0, len(mints), 30)]
    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for d in ex.map(_dex_lot, lots):
            out.update(d)
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
    """Coins detenus par au moins MIN_HOLDERS adresses suivies."""
    from mmscanner import followed as fmod
    from mmscanner import safety
    from mmscanner.engine import is_crypto_native

    t0 = time.time()
    registre = fmod.tracked_registry()
    # TES adresses d'abord : le budget de lecture par passage est limite, et
    # ce sont les wallets FOMO/clans qui t'interessent, pas les wallets
    # trouves tout seuls par recurrence.
    adresses = sorted((a for a in registre if not a.startswith("0x")),
                      key=lambda a: registre[a].get("origin") != "suivi")
    if not adresses:
        return {"coins": [], "wallets": 0, "at": time.time(), "empty": True}

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
    log(f"[holdings] {lus}/{len(adresses)} portefeuilles connus, "
        f"{len(par_mint)} mints, {len(retenus)} tenus par {MIN_HOLDERS}+")
    if not retenus:
        return {"coins": [], "wallets": lus, "total": len(adresses),
                "at": time.time(), "empty": False}

    infos = _metriques(retenus)

    coins = []
    for m in retenus:
        d = infos.get(m)
        if not d or not d["price_usd"]:
            continue
        if not is_crypto_native(d["symbol"], d["name"], m):
            continue
        if d["mc"] < MIN_MC:
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
        if len(porteurs) < MIN_HOLDERS:
            continue
        porteurs.sort(key=lambda p: p["usd"], reverse=True)

        c = dict(d)
        c.update(mint=m, holders=len(porteurs), value_usd=total,
                 top_usd=porteurs[0]["usd"],
                 by=[p["group"] for p in porteurs],
                 dip=(d["chg_h24"] <= DIP_PCT))
        coins.append(c)

    coins.sort(key=lambda c: (c["holders"], c["value_usd"]), reverse=True)
    coins = coins[:MAX_COINS]
    res = {"coins": coins, "wallets": lus, "total": len(adresses),
           "at": time.time(), "empty": False}
    log(f"[holdings] {len(coins)} coins detenus retenus "
        f"({time.time() - t0:.0f}s)")
    save(res)
    return res


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
            return json.load(f) or {}
    except Exception:
        return {"coins": [], "wallets": 0, "at": 0, "empty": True}
