"""
Resolution handle -> wallet par les pages publiques de fomoscan.sh.

L'API facture 2 500 CU par handle et le plan gratuit n'en donne aucun. Mais
fomoscan.sh publie une fiche par trader, rendue cote serveur, qui contient
les wallets verifies Solana et Ethereum. C'est la meme donnee, en acces libre.

On y va doucement (une requete a la fois, avec une pause) : c'est un site
public qu'on consulte, pas une API a marteler.
"""
import re
import time
from typing import Dict, List, Optional

import requests

BASE = "https://fomoscan.sh"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html"}

SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

DELAY = 1.2          # pause entre deux fiches
_cache: Dict[str, dict] = {}


def _plausible_sol(a: str) -> bool:
    """Ecarte les faux positifs base58 (hashes de build Next.js, etc.)."""
    if len(a) < 32 or len(a) > 44:
        return False
    # une adresse Solana melange majuscules, minuscules et chiffres
    return (any(c.isupper() for c in a) and any(c.islower() for c in a)
            and any(c.isdigit() for c in a))


def lookup(handle: str, tries: int = 2) -> dict:
    """
    {handle, solana, ethereum, ok, reason}
    `ok` est vrai des qu'au moins un wallet a ete trouve.
    """
    h = (handle or "").strip().lstrip("@")
    if not h:
        return {"handle": handle, "ok": False, "reason": "handle vide"}
    if h.lower() in _cache:
        return _cache[h.lower()]

    out = {"handle": h, "solana": None, "ethereum": None,
           "ok": False, "reason": ""}
    for attempt in range(tries):
        try:
            r = requests.get(f"{BASE}/{h}", headers=HEADERS, timeout=25)
            if r.status_code == 404:
                out["reason"] = "inconnu de fomoscan"
                break
            r.raise_for_status()
            html = r.text
            sol = [a for a in dict.fromkeys(SOL_RE.findall(html)) if _plausible_sol(a)]
            evm = list(dict.fromkeys(EVM_RE.findall(html)))
            out["solana"] = sol[0] if sol else None
            out["ethereum"] = evm[0] if evm else None
            out["ok"] = bool(out["solana"] or out["ethereum"])
            if not out["ok"]:
                out["reason"] = "fiche sans wallet verifie"
            break
        except Exception as e:
            out["reason"] = str(e)[:80]
            if attempt < tries - 1:
                time.sleep(2.0 * (attempt + 1))

    _cache[h.lower()] = out
    return out


def lookup_many(handles: List[str], log=None) -> List[dict]:
    """Resout une liste de handles, en marquant une pause entre chaque fiche."""
    out = []
    for i, h in enumerate(handles):
        res = lookup(h)
        out.append(res)
        if log:
            w = res.get("solana") or res.get("ethereum") or "-"
            log(f"  [{i+1}/{len(handles)}] @{res['handle']:24} "
                f"{w if res['ok'] else res.get('reason', '')}")
        time.sleep(DELAY)
    return out
