"""
Helius Enhanced Transactions — récupération et parsing des swaps on-chain.
Sert à deux choses :
  · découverte des early buyers (wallet hunting auto, Module 07)
  · calcul des net USD flows par wallet (whale flow, style sun-flow)

Nécessite HELIUS_API_KEY. Sans clé -> listes vides (dégradation propre).
"""
import time
import requests
from typing import List, Optional, Tuple

import config

BASE = "https://api.helius.xyz/v0"


QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _enhanced(address: str, tx_type: str = None, limit: int = 100,
              before: Optional[str] = None, tries: int = 3) -> List[dict]:
    if not config.HELIUS_API_KEYS:
        return []
    url = f"{BASE}/addresses/{address}/transactions"
    params = {"limit": limit}
    if tx_type:
        params["type"] = tx_type
    if before:
        params["before"] = before
    # Toujours la cle COURANTE, jamais la premiere de la liste. Cette fonction
    # prenait config.HELIUS_API_KEY : une fois la cle 1 a sec ("max usage
    # reached"), chaque lecture rendait une liste vide, sans erreur — et la
    # surveillance des traders est restee aveugle tout un apres-midi alors que
    # deux autres cles repondaient. Un 429 "max usage" fait passer a la
    # suivante sans attendre ; un 429 ordinaire demande seulement de ralentir.
    essais = max(tries, len(config.HELIUS_API_KEYS) + 1)
    for attempt in range(essais):
        cle = config.helius_key()
        try:
            r = requests.get(url, params=dict(params, **{"api-key": cle}),
                             timeout=25)
            if r.status_code == 429:
                if "max usage" in (r.text or "").lower():
                    config.helius_a_sec(cle)
                    continue
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json() or []
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    print(f"[helius] lecture impossible pour {address[:8]} "
          f"({len(config.HELIUS_API_KEYS)} cle(s) essayee(s))")
    return []


# ── Relire un wallet seulement quand il a bouge ─────────────────────────
#
# L'historique « enhanced » est l'appel le plus cher de Helius (environ cent
# credits). On le faisait pour 90 adresses suivies a CHAQUE scan, soit ~9 000
# appels par jour : une cle gratuite y passait en une journee. Or la plupart de
# ces wallets n'ont rien fait depuis le tour precedent.
#
# On demande donc d'abord la derniere signature du wallet (getSignaturesForAddress,
# un credit). Si elle n'a pas change, l'historique non plus : on rend celui
# qu'on a deja. On ne paie le gros appel que pour les wallets qui ont bouge.
_SWAPS: dict = {}            # adresse -> {"sig", "txs", "at"}
_SWAPS_VERROU = __import__("threading").Lock()
SWAPS_SANS_SIG_S = 1800      # sans reponse RPC, le cache vaut encore 30 min


def derniere_signature(address: str) -> Optional[str]:
    """Signature la plus recente du wallet, ou None si Helius ne repond pas."""
    if not config.HELIUS_API_KEYS:
        return None
    corps = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
             "params": [address, {"limit": 1}]}
    for _ in range(len(config.HELIUS_API_KEYS) + 1):
        cle = config.helius_key()
        try:
            r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={cle}",
                              json=corps, timeout=15)
            if r.status_code == 429:
                if "max usage" in (r.text or "").lower():
                    config.helius_a_sec(cle)
                    continue
                time.sleep(1.0)
                continue
            r.raise_for_status()
            res = (r.json() or {}).get("result")
            if isinstance(res, list):
                return (res[0] or {}).get("signature") if res else ""
            return None
        except Exception:
            time.sleep(0.5)
    return None


def swaps(address: str, limit: int = 100) -> List[dict]:
    """Les swaps recents d'un wallet, relus seulement s'il a bouge."""
    sig = derniere_signature(address)
    with _SWAPS_VERROU:
        c = _SWAPS.get(address)
    if c is not None:
        if sig is not None and sig == c["sig"]:
            return c["txs"]
        if sig is None and time.time() - c["at"] < SWAPS_SANS_SIG_S:
            return c["txs"]
    txs = _enhanced(address, "SWAP", limit=limit)
    # une lecture vide sur un wallet qui a des signatures est suspecte (cle a
    # sec, coupure) : on ne la retient pas, pour reessayer au tour suivant
    if txs or sig == "":
        with _SWAPS_VERROU:
            _SWAPS[address] = {"sig": sig, "txs": txs, "at": time.time()}
    return txs


def fetch_swaps(mint: str, max_tx: int = 800, max_age_days: int = 30) -> List[dict]:
    """
    Remonte jusqu'à `max_tx` transactions récentes impliquant `mint`.

    NB : on NE filtre PAS sur type=SWAP — Helius classe la majorité des trades
    AMM (pump.fun, Raydium…) en "UNKNOWN". Le tri buy/sell se fait dans
    parse_swap, qui exige un mouvement de quote (SOL/USDC) pour écarter les
    simples transferts.
    """
    out: List[dict] = []
    before = None
    cutoff = time.time() - max_age_days * 86400
    while len(out) < max_tx:
        batch = _enhanced(mint, None, 100, before)
        if not batch:
            break
        out.extend(batch)
        before = batch[-1].get("signature")
        oldest = batch[-1].get("timestamp", 0) or 0
        if oldest and oldest < cutoff:
            break
        if len(batch) < 100:
            break
        time.sleep(0.35)
    return out


def parse_swap(tx: dict, mint: str) -> Tuple[Optional[str], float, int]:
    """
    Retourne (wallet, token_delta, timestamp) pour un TRADE sur `mint`.
      token_delta > 0  -> le wallet REÇOIT le token  = BUY
      token_delta < 0  -> le wallet ENVOIE le token   = SELL
      0                -> pas un trade (simple transfert) -> ignoré

    Un vrai trade implique un mouvement de quote (WSOL/USDC/USDT) ou de SOL natif :
    c'est ce qui distingue un achat d'un simple envoi de tokens.
    """
    ts = tx.get("timestamp", 0) or 0
    wallet = tx.get("feePayer")
    if not wallet:
        return None, 0.0, ts

    delta = 0.0
    quote_moved = False
    for tt in tx.get("tokenTransfers", []) or []:
        m = tt.get("mint")
        amt = float(tt.get("tokenAmount") or 0)
        if m == mint:
            if tt.get("toUserAccount") == wallet:
                delta += amt
            elif tt.get("fromUserAccount") == wallet:
                delta -= amt
        elif m in QUOTE_MINTS and amt > 0:
            quote_moved = True

    if not quote_moved:
        # SOL natif échangé par le wallet (hors frais) -> c'est un trade
        for nt in tx.get("nativeTransfers", []) or []:
            amount = float(nt.get("amount") or 0) / 1e9
            if amount < 0.001:
                continue
            if wallet in (nt.get("fromUserAccount"), nt.get("toUserAccount")):
                quote_moved = True
                break

    if not quote_moved:
        return wallet, 0.0, ts
    return wallet, delta, ts
