"""
MSCAN — Defense : les trois drapeaux rouges du cours qui manquaient.

Le module safety verifie deja le piege pur (autorites de mint et de gel) et le
volume fabrique. Restaient trois signaux que le cours cite explicitement :

    LP non lock / burn          l'emetteur peut retirer la liquidite
    dev wallet > 5-10 %         une seule main peut tout distribuer
    bundle / snipe au launch    la supply a ete pre-repartie au bloc zero

Ce ne sont pas des interpretations de chart : ce sont des faits on-chain. On
les lit, on ne les devine pas. Quand une donnee n'est pas accessible on le dit
— "inconnu" et non "sain".
"""
import time
from typing import Dict, Optional

import requests

import config

_SESSION = requests.Session()
_CACHE: Dict[str, dict] = {}
_TTL = 6 * 3600.0        # ces caracteristiques ne bougent pas d'une heure a l'autre

# Seuils tires du cours
DEV_ALERTE = 5.0         # au-dessus : a surveiller
DEV_DANGER = 10.0        # au-dessus : le cours dit danger
BUNDLE_DESTS = 6         # destinataires distincts des le premier bloc

BRULE = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
}
EXPLORATEURS = {
    "robinhood": "https://robinhoodchain.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
}


def _get(url: str, params: dict = None, timeout: int = 25):
    from mmscanner import sources_evm as evm
    try:
        r = _SESSION.get(url, headers=evm.ENTETES, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ── EVM ────────────────────────────────────────────────────────────
def _evm(mint: str, chain: str, pair: str = "") -> dict:
    base = EXPLORATEURS.get(chain)
    out = {"dev_pct": None, "lp": "inconnu", "bundle": None}
    if not base:
        return out

    info = _get(f"{base}/api/v2/tokens/{mint}")
    if not info:
        return out
    try:
        dec = int(info.get("decimals") or 18)
        supply = float(info.get("total_supply") or 0) / (10 ** dec)
    except Exception:
        return out
    if supply <= 0:
        return out

    # ── part du plus gros porteur, pool et adresses de burn exclues
    h = _get(f"{base}/api/v2/tokens/{mint}/holders")
    lignes = (h or {}).get("items") or []
    exclus = {(pair or "").lower()} | BRULE
    brule = 0.0
    for x in lignes:
        a = ((x.get("address") or {}).get("hash") or "").lower()
        try:
            q = float(x.get("value") or 0) / (10 ** dec)
        except Exception:
            continue
        if a in BRULE:
            brule += q
        elif a not in exclus and out["dev_pct"] is None:
            out["dev_pct"] = q / supply * 100.0

    # ── liquidite : sur une paire v2 le jeton LP existe et peut etre brule ;
    # sur une v3 il n'y a pas de jeton LP du tout, la position est un NFT
    if pair:
        lp = _get(f"{base}/api/v2/tokens/{pair}")
        if lp is None:
            out["lp"] = "v3"          # pas de jeton LP : notion sans objet
        else:
            try:
                ldec = int(lp.get("decimals") or 18)
                lsup = float(lp.get("total_supply") or 0) / (10 ** ldec)
            except Exception:
                lsup = 0.0
            if lsup > 0:
                hl = _get(f"{base}/api/v2/tokens/{pair}/holders")
                mort = 0.0
                for x in ((hl or {}).get("items") or []):
                    a = ((x.get("address") or {}).get("hash") or "").lower()
                    if a in BRULE:
                        try:
                            mort += float(x.get("value") or 0) / (10 ** ldec)
                        except Exception:
                            pass
                out["lp"] = "brule" if mort / lsup >= 0.9 else "libre"

    # ── bundle : la supply a-t-elle ete distribuee des le premier bloc ?
    d = _get(f"{base}/api", {"module": "account", "action": "tokentx",
                             "contractaddress": mint, "page": 1,
                             "offset": 60, "sort": "asc"})
    res = (d or {}).get("result")
    if isinstance(res, list) and res:
        premier = res[0].get("blockNumber")
        dests = {x.get("to") for x in res if x.get("blockNumber") == premier}
        out["bundle"] = len(dests) >= BUNDLE_DESTS
        out["bundle_dests"] = len(dests)
    return out


# ── Solana ─────────────────────────────────────────────────────────
def _solana(mint: str) -> dict:
    """
    Sur Solana on dispose de la repartition, pas de l'historique : la lecture
    des premieres transactions passe par l'API payante d'Helius. Le bundle
    reste donc inconnu plutot que declare sain.
    """
    from mmscanner import sources_helius as helius

    out = {"dev_pct": None, "lp": "inconnu", "bundle": None}
    conc = helius.holder_concentration(mint)
    if conc.get("top_holder_pct") is not None:
        # holder_concentration ecarte deja le pool (part > 30 % du supply)
        out["dev_pct"] = conc["top_holder_pct"] * 100.0
    # un jeton issu de pump.fun a sa liquidite brulee par construction
    if mint.endswith("pump"):
        out["lp"] = "brule"
    return out


# ── entree publique ────────────────────────────────────────────────
def analyser(mint: str, chain: str = "solana", pair: str = "") -> dict:
    """
    {dev_pct, lp, bundle, alertes} — jamais d'exception, jamais d'invention.

    `alertes` liste en clair ce qui cloche, vide si rien.
    """
    cle = f"{chain}:{mint}"
    hit = _CACHE.get(cle)
    if hit and time.time() - hit["at"] < _TTL:
        return hit["val"]

    try:
        d = _evm(mint, chain, pair) if mint.startswith("0x") else _solana(mint)
    except Exception:
        d = {"dev_pct": None, "lp": "inconnu", "bundle": None}

    alertes = []
    p = d.get("dev_pct")
    if p is not None and p >= DEV_DANGER:
        alertes.append(f"Le plus gros holder a {p:.0f}% de la supply")
    elif p is not None and p >= DEV_ALERTE:
        alertes.append(f"Le plus gros holder a {p:.0f}%")
    if d.get("lp") == "libre":
        alertes.append("liquidite ni bloquee ni brulee")
    if d.get("bundle"):
        alertes.append(f"launch groupe : {d.get('bundle_dests')} portefeuilles "
                       f"servis au premier bloc")
    d["alertes"] = alertes

    _CACHE[cle] = {"at": time.time(), "val": d}
    return d
