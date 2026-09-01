"""
Ecarter les coins piegés ou au volume fabriqué.

Deux dangers distincts, souvent confondus sous le mot "honeypot" :

1. Le vrai piege : la freeze authority du token n'est pas revoquee. L'emetteur
   peut geler tes jetons, tu achetes et tu ne peux plus vendre. Verifiable de
   maniere certaine on-chain, donc exclusion ferme.

2. Le volume fabrique : des bots s'echangent le jeton en boucle pour gonfler
   le volume et entrer dans les classements. On peut vendre, mais le prix
   s'effondre des que le lavage s'arrete. Se reconnait a un rapport
   volume/liquidite aberrant.

Mesure sur un scan reel : mediane a 6x, les coins bien notes entre 6 et 20x,
et trois coins entre 100x et 1257x — tous du lavage. Le seuil est pose a 50x.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

import requests

import config

RATIO_MAX = 50.0        # volume 24h / liquidite au-dela duquel on ecarte
AGE_MAX_H = 72.0        # le lavage se voit surtout sur les coins tres jeunes
_cache: Dict[str, dict] = {}
_TTL = 3600.0


def volume_suspect(liquidity_usd: float, vol_h24: float,
                   age_hours: float = None) -> Tuple[bool, str]:
    """
    Rapport volume/liquidite aberrant = volume fabrique.

    Un coin qui echange 180 fois sa liquidite en 24 h ne trouve pas 180 fois
    preneur : ce sont les memes jetons qui tournent en boucle.
    """
    liq = liquidity_usd or 0
    vol = vol_h24 or 0
    if liq < 1000 or vol <= 0:
        return False, ""
    ratio = vol / liq
    if ratio <= RATIO_MAX:
        return False, ""
    # sur un coin etabli, un fort turnover peut etre legitime ; sur un coin
    # de quelques heures, c'est la signature du lavage.
    if age_hours is not None and age_hours > AGE_MAX_H and ratio < RATIO_MAX * 3:
        return False, ""
    return True, f"volume {ratio:.0f}x la liquidite"


def authorities(mint: str) -> dict:
    """
    Etat des autorites du token : {mint_authority, freeze_authority, ok}.

    `ok` est faux des qu'une autorite subsiste — l'emetteur garde alors le
    pouvoir de geler les jetons ou d'en imprimer.
    """
    hit = _cache.get(mint)
    if hit and (time.time() - hit["at"]) < _TTL:
        return hit["val"]
    if not config.HELIUS_API_KEY:
        return {"ok": True, "inconnu": True}

    val = {"ok": True, "inconnu": True}
    try:
        r = requests.post(
            f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}",
            json={"jsonrpc": "2.0", "id": "mm", "method": "getAsset",
                  "params": {"id": mint}}, timeout=15)
        ti = ((r.json() or {}).get("result") or {}).get("token_info") or {}
        gel = ti.get("freeze_authority")
        frappe = ti.get("mint_authority")
        val = {
            "freeze_authority": gel,
            "mint_authority": frappe,
            "ok": not gel and not frappe,
            "inconnu": False,
        }
    except Exception:
        pass
    _cache[mint] = {"at": time.time(), "val": val}
    return val


def raison_exclusion(p) -> Optional[str]:
    """
    Retourne la raison d'ecarter cette paire, ou None si elle est jouable.
    Ne fait AUCUN appel reseau : n'utilise que ce qu'on a deja.
    """
    suspect, motif = volume_suspect(p.liquidity_usd, p.vol_h24, p.age_hours)
    if suspect:
        return motif
    return None


def controler_autorites(pairs, log=print) -> int:
    """
    Verifie les autorites des paires retenues et marque celles qui sont
    piegees. Un appel par coin, en parallele. Retourne le nombre d'ecartes.
    """
    if not config.HELIUS_API_KEY:
        return 0
    cibles = [p for p in pairs if (p.chain or "solana") == "solana"]
    if not cibles:
        return 0

    with ThreadPoolExecutor(max_workers=10) as ex:
        etats = list(ex.map(lambda p: authorities(p.mint), cibles))

    n = 0
    for p, e in zip(cibles, etats):
        if e.get("inconnu") or e.get("ok"):
            continue
        motifs = []
        if e.get("freeze_authority"):
            motifs.append("freeze authority active")
        if e.get("mint_authority"):
            motifs.append("mint authority active")
        p.danger = ", ".join(motifs)
        n += 1
        log(f"[securite] {p.symbol} ecarte : {p.danger}")
    return n
