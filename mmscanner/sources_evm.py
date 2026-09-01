"""
Lecture des achats recents d'un wallet sur les chaines EVM (Ethereum, Base).

Helius ne couvre que Solana. Pour l'EVM on passe par des explorateurs publics
sans cle : plusieurs hotes sont essayes dans l'ordre, on garde le premier qui
repond. Si aucun ne repond pour une chaine, on le dit au lieu de rendre une
liste vide silencieuse.
"""
import re
import time
from typing import Dict, List

import requests

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
EVM_RE_ANY = re.compile(r"0x[a-fA-F0-9]{40}")   # reperage dans un texte colle

# hotes essayes dans l'ordre, par chaine.
#   kind "blockscout" -> /api/v2/addresses/<a>/token-transfers
#   kind "etherscan"  -> ?module=account&action=tokentx
ENDPOINTS: Dict[str, List[dict]] = {
    "ethereum": [
        {"kind": "blockscout", "base": "https://eth.blockscout.com"},
        {"kind": "etherscan",
         "base": "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api"},
    ],
    "base": [
        # Blockscout Base repond 500 par intermittence ; des qu'il revient,
        # la chaine se met a marcher sans rien changer ici.
        {"kind": "blockscout", "base": "https://base.blockscout.com"},
        {"kind": "etherscan",
         "base": "https://api.routescan.io/v2/network/mainnet/evm/8453/etherscan/api"},
    ],
    # Robinhood Chain (id 4663) a bien un Blockscout public. Il etait note
    # "sans indexeur" ici, et c'est ce trou qui faisait arriver les paires
    # Robinhood trop tard : aucune lecture d'achat n'etait possible dessus.
    "robinhood": [
        {"kind": "blockscout", "base": "https://robinhoodchain.blockscout.com"},
    ],
}

# chaines sans indexeur public exploitable a ce jour
UNSUPPORTED = set()

# Sans en-tete de navigateur, l'instance Robinhood repond 403 (protection
# Cloudflare) la ou la meme requete passe avec.
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

_dead: Dict[str, float] = {}          # hote -> instant de reprise
_DEAD_FOR = 600.0                     # on ecarte un hote 10 min apres un echec


def is_evm(address: str) -> bool:
    return bool(EVM_RE.match((address or "").strip()))


def supported(chain: str) -> bool:
    return (chain or "").lower() in ENDPOINTS


def _alive(host: str) -> bool:
    return _dead.get(host, 0) < time.time()


def _mark_dead(host: str) -> None:
    _dead[host] = time.time() + _DEAD_FOR


def _blockscout(base: str, address: str, limit: int) -> List[dict]:
    r = requests.get(f"{base}/api/v2/addresses/{address}/token-transfers",
                     params={"type": "ERC-20"}, headers=ENTETES, timeout=20)
    r.raise_for_status()
    out = []
    for it in (r.json().get("items") or [])[: limit * 3]:
        tok = it.get("token") or {}
        to = ((it.get("to") or {}).get("hash") or "").lower()
        if to != address.lower():          # on ne garde que les entrees (achats)
            continue
        ts = it.get("timestamp") or ""
        try:
            epoch = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            epoch = 0.0
        out.append({"mint": tok.get("address_hash") or tok.get("address") or "",
                    "symbol": tok.get("symbol") or "?",
                    "name": tok.get("name") or "?",
                    "ts": epoch, "amount": 0.0})
    return out


def _etherscan(base: str, address: str, limit: int) -> List[dict]:
    r = requests.get(base, params={"module": "account", "action": "tokentx",
                                   "address": address, "page": 1,
                                   "offset": limit * 3, "sort": "desc"},
                     headers=ENTETES, timeout=20)
    r.raise_for_status()
    res = r.json().get("result")
    if not isinstance(res, list):
        raise RuntimeError(str(res)[:120])
    out = []
    for it in res:
        if (it.get("to") or "").lower() != address.lower():
            continue
        out.append({"mint": it.get("contractAddress") or "",
                    "symbol": it.get("tokenSymbol") or "?",
                    "name": it.get("tokenName") or "?",
                    "ts": float(it.get("timeStamp") or 0), "amount": 0.0})
    return out


def recent_buys(address: str, chain: str = "ethereum",
                hours: float = 72, limit: int = 12) -> List[dict]:
    """
    Tokens ERC-20 recus par ce wallet sur les `hours` dernieres heures.
    Retourne [] si la chaine n'est pas couverte ou si aucun hote ne repond.
    """
    chain = (chain or "ethereum").lower()
    hosts = ENDPOINTS.get(chain) or []
    if not hosts or not is_evm(address):
        return []

    cutoff = time.time() - hours * 3600
    for ep in hosts:
        if not _alive(ep["base"]):
            continue
        try:
            rows = (_blockscout if ep["kind"] == "blockscout" else _etherscan)(
                ep["base"], address, limit)
        except Exception:
            _mark_dead(ep["base"])
            continue
        # dedoublonne par token, garde le plus recent, filtre la fenetre
        best: Dict[str, dict] = {}
        for row in rows:
            if not row["mint"] or row["ts"] < cutoff:
                continue
            cur = best.get(row["mint"])
            if not cur or row["ts"] > cur["ts"]:
                best[row["mint"]] = row
        return sorted(best.values(), key=lambda r: r["ts"], reverse=True)[:limit]
    return []


def status() -> Dict[str, str]:
    """Etat de chaque chaine EVM — affiche dans l'interface."""
    out = {}
    for chain, hosts in ENDPOINTS.items():
        live = [h["base"] for h in hosts if _alive(h["base"])]
        out[chain] = "ok" if live else "indisponible"
    for chain in UNSUPPORTED:
        out[chain] = "pas d'indexeur public"
    return out
