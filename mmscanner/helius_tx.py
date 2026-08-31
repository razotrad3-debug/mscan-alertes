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
    if not config.HELIUS_API_KEY:
        return []
    url = f"{BASE}/addresses/{address}/transactions"
    params = {"api-key": config.HELIUS_API_KEY, "limit": limit}
    if tx_type:
        params["type"] = tx_type
    if before:
        params["before"] = before
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=25)
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json() or []
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return []


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
