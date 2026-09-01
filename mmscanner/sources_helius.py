"""
Helius (RPC Solana) — la couche wallet que les autres scanners n'ont pas :
  1) concentration des holders (top holder %, top10 %)
  2) smart-money : combien de wallets suivis détiennent actuellement le token

Nécessite HELIUS_API_KEY. Sans clé, tout retourne None/0 (dégradation propre).
"""
import time
from concurrent.futures import ThreadPoolExecutor
import requests
from typing import Optional, Dict, List

import config


def _rpc(method: str, params: list, tries: int = 2) -> Optional[dict]:
    if not config.HELIUS_API_KEY:
        return None
    url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
    payload = {"jsonrpc": "2.0", "id": "mm", "method": method, "params": params}
    for attempt in range(tries):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json().get("result")
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    return None


def _das(method: str, params: dict, tries: int = 2):
    """Appel DAS (Digital Asset Standard) de Helius."""
    if not config.HELIUS_API_KEY:
        return None
    url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
    payload = {"jsonrpc": "2.0", "id": "mm", "method": method, "params": params}
    for attempt in range(tries):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            j = r.json()
            if "error" in j:
                return None
            return j.get("result")
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    return None


def holder_concentration(mint: str, max_pages: int = 6, page_size: int = 1000) -> Dict:
    """
    Concentration des holders : part du 1er porteur et du top 10.

    Chemin normal : getTokenSupply + getTokenLargestAccounts + getMultipleAccounts,
    trois appels RPC standards. L'ancien chemin paginait l'API DAS sur tous les
    comptes du token (jusqu'a 6 pages de 1 000), ce qui coutait cher et rendait
    None des que le token depassait 6 000 comptes — donc precisement sur les
    coins qui marchent. Il ne sert plus que de repli.

    Les montants sont agreges PAR PROPRIETAIRE : un wallet peut detenir
    plusieurs comptes de jetons.
    """
    from mmscanner import holdings as _h

    out = {"top_holder_pct": None, "top10_pct": None, "holders": None}
    supply = _h.rpc("getTokenSupply", [mint])
    if not supply:
        return out
    try:
        decimals = int(supply["value"]["decimals"])
        total = float(supply["value"]["uiAmount"] or 0)
    except Exception:
        return out
    if total <= 0:
        return out

    gros = _h.rpc("getTokenLargestAccounts", [mint], essais=1)
    comptes = (gros or {}).get("value") or []
    if comptes:
        montants = {}
        cles = [c.get("address") for c in comptes if c.get("address")]
        infos = _h.rpc("getMultipleAccounts",
                       [cles, {"encoding": "jsonParsed"}], essais=1) or {}
        proprios = []
        for it in (infos.get("value") or []):
            try:
                proprios.append(it["data"]["parsed"]["info"]["owner"])
            except Exception:
                proprios.append(None)
        for i, c in enumerate(comptes):
            qte = float((c.get("uiAmount") or 0))
            if qte <= 0:
                continue
            cle = (proprios[i] if i < len(proprios) and proprios[i]
                   else c.get("address"))
            montants[cle] = montants.get(cle, 0.0) + qte
        if montants:
            return _parts(sorted(montants.values(), reverse=True), total, out)

    # repli : pagination DAS (couteuse, et muette au-dela de max_pages)
    accounts, complete = [], False
    for page in range(1, max_pages + 1):
        res = _das("getTokenAccounts", {
            "mint": mint, "limit": page_size, "page": page,
            "options": {"showZeroBalance": False},
        })
        if not res:
            return out
        batch = res.get("token_accounts", []) or []
        accounts.extend(batch)
        if len(batch) < page_size:
            complete = True
            break
        time.sleep(0.15)
    if not complete or not accounts:
        return out  # trop de holders pour etre fiable

    by_owner: Dict[str, float] = {}
    for a in accounts:
        owner = a.get("owner")
        amt = float(a.get("amount") or 0) / (10 ** decimals)
        if owner and amt > 0:
            by_owner[owner] = by_owner.get(owner, 0.0) + amt
    if not by_owner:
        return out
    out["holders"] = len(by_owner)
    return _parts(sorted(by_owner.values(), reverse=True), total, out)


def _parts(montants: List[float], total: float, out: Dict) -> Dict:
    """Top 1 et top 10 en part du supply, pool de liquidite mis de cote."""
    if not montants or total <= 0:
        return out
    # heuristique LP : un compte unique > 30% du supply est presque toujours le pool
    filtres = montants[1:] if (montants[0] / total) > 0.30 else montants
    if not filtres:
        filtres = montants
    out["top_holder_pct"] = round(filtres[0] / total, 4)
    out["top10_pct"] = round(sum(filtres[:10]) / total, 4)
    return out


def smart_money_holding(mint: str, wallets: List[str]) -> Dict:
    """
    Compte combien des wallets suivis detiennent actuellement `mint`.
    Retourne aussi les adresses, pour retrouver leur historique (wallet_store).

    Les wallets sont interroges en parallele : la liste suivie grandit (clans,
    decouverte auto), et une boucle sequentielle rendait le scan quadratique —
    35 wallets x 21 coins, c'etait deja plusieurs minutes bloquantes.
    """
    if not config.HELIUS_API_KEY or not wallets:
        return {"count": 0, "wallets": [], "addresses": []}

    cibles = []
    for w in wallets:
        w = w.strip()
        if not w:
            continue
        addr = w.split()[0]
        # Helius ne parle que Solana : une adresse EVM ici serait un appel RPC
        # jete a la poubelle, et on en suit des dizaines depuis les clans.
        if addr.startswith("0x"):
            continue
        label = w[len(addr):].strip(" -	")
        if addr:
            cibles.append((addr, label))

    def _solde(cible):
        addr, label = cible
        res = _rpc("getTokenAccountsByOwner",
                   [addr, {"mint": mint}, {"encoding": "jsonParsed"}])
        try:
            amount = 0.0
            for ac in (res or {}).get("value", []) or []:
                info = ac["account"]["data"]["parsed"]["info"]["tokenAmount"]
                amount += float(info.get("uiAmount") or 0)
            if amount > 0:
                return addr, (label or (addr[:4] + "…" + addr[-4:]))
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        trouves = [r for r in ex.map(_solde, cibles) if r]

    return {"count": len(trouves),
            "wallets": [n for _a, n in trouves],
            "addresses": [a for a, _n in trouves]}


# ── index des avoirs ────────────────────────────────────────────────
# Interroger (wallet, mint) un par un coutait 99 x 13 = 1 287 requetes par
# scan. L'API DAS rend TOUS les jetons d'un wallet en un appel : on construit
# l'index une fois (99 appels), puis chaque question se resout en memoire.

def holdings_of(address: str, limit: int = 1000) -> set:
    """Tous les mints fongibles detenus par ce wallet, en un appel."""
    if address.startswith("0x"):
        return set()
    res = _das("getAssetsByOwner", {
        "ownerAddress": address, "page": 1, "limit": limit,
        "displayOptions": {"showFungible": True},
    })
    out = set()
    for it in ((res or {}).get("items") or []):
        if it.get("interface") in ("FungibleToken", "FungibleAsset"):
            mid = it.get("id")
            if mid:
                out.add(mid)
    return out


def build_holdings_index(wallets, log=None) -> dict:
    """
    {adresse: {"label", "mints"}} pour tous les wallets suivis (Solana).

    Lecture du cache partage avec le module holdings, rempli par vagues apres
    chaque scan via getTokenAccountsByOwner. On ne fait donc aucun appel DAS
    ici : getAssetsByOwner etait appele pour chaque wallet a chaque scan, soit
    l'essentiel du quota Helius pour une information qui bouge lentement.
    """
    from mmscanner import holdings as _h

    cibles = []
    for w in wallets or []:
        w = w.strip()
        if not w:
            continue
        addr = w.split()[0]
        if addr and not addr.startswith("0x"):
            label = w[len(addr):].strip(" -	")
            cibles.append((addr, label))

    avoirs = _h.portefeuilles([a for a, _ in cibles], refresh=False)
    index = {}
    for addr, label in cibles:
        mints = avoirs.get(addr)
        if mints is None:
            continue                      # aimant a spam, ou jamais lu encore
        index[addr] = {"label": label, "mints": set(mints)}
    if log:
        total = sum(len(v["mints"]) for v in index.values())
        log(f"[index] {len(index)}/{len(cibles)} wallets connus, "
            f"{total} positions (cache)")
    return index


def smart_money_from_index(mint: str, index: dict) -> dict:
    """Meme reponse que smart_money_holding, sans une seule requete reseau."""
    noms, adrs = [], []
    for addr, v in (index or {}).items():
        if mint in v["mints"]:
            adrs.append(addr)
            noms.append(v["label"] or (addr[:4] + "…" + addr[-4:]))
    return {"count": len(adrs), "wallets": noms, "addresses": adrs}

