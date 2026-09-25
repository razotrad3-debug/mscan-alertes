"""
Achats et ventes d'un wallet Solana, lus sans l'API « enhanced » de Helius.

POURQUOI
    L'historique enhanced (/v0/addresses/{a}/transactions) coute 100 credits
    par appel ; une cle gratuite en a un million par mois. Le bot relisait 90
    wallets suivis chaque minute par ce chemin : ~13 millions de credits par
    jour. Les cles mouraient en quelques heures, et avec elles les alertes
    traders et insiders — en silence.

COMMENT
    Deux appels RPC standard suffisent, a un credit chacun chez Helius et
    gratuits sur le RPC public de Solana :
      1. getSignaturesForAddress : les dernieres transactions du wallet ;
      2. getTransaction (jsonParsed), pour les seules signatures jamais vues.
    Un achat, c'est un jeton qui entre pendant que du SOL (ou de l'USDC) sort ;
    une vente, l'inverse. Un jeton recu sans contrepartie (airdrop, transfert)
    n'est pas un trade et n'est pas retenu.

    Le RPC public passe en premier ; Helius ne sert que s'il refuse. Chaque
    transaction lue est gardee en memoire : un wallet qui n'a pas bouge ne
    coute qu'une signature par tour.
"""
import json
import os
import threading
import time
from typing import Dict, List, Optional

import requests

import config

# Deux points d'acces gratuits. Le premier (celui de Solana) repond bien
# depuis un PC mais pas depuis les machines de GitHub : le bot n'y lisait
# aucune transaction. PublicNode prend le relais.
PUBLICS = ("https://api.mainnet-beta.solana.com",
           "https://solana-rpc.publicnode.com")

QUOTES = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
SOL_MINI = 0.002       # en dessous, un mouvement de SOL n'est que des frais

_SESSION = requests.Session()
_SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4,
                                                          pool_maxsize=32))

# Le RPC public accepte un debit regulier mais refuse les rafales : dix
# requetes simultanees passent, les suivantes prennent un 429. Or les lectures
# partent de pools de 12 a 15 fils. Tout passe donc par une file cadencee,
# commune a tous les fils : une requete publique toutes les 0,25 s.
_ECART_PUBLIC_S = 0.25
_ECART_HELIUS_S = 0.15        # l'offre gratuite de Helius plafonne a 10 req/s
_PROCHAIN = {"helius": 0.0}
_RYTHME = threading.Lock()
_EN_PANNE: Dict[str, float] = {}

# compteurs, pour voir dans les journaux qui a servi
STATS = {"public": 0, "helius": 0, "echec": 0, "depuis": time.time()}


def _attendre_tour(voie: str) -> None:
    ecart = _ECART_HELIUS_S if voie == "helius" else _ECART_PUBLIC_S
    with _RYTHME:
        maintenant = time.time()
        t = max(maintenant, _PROCHAIN.get(voie, 0.0))
        _PROCHAIN[voie] = t + ecart
    if t > maintenant:
        time.sleep(t - maintenant)


def _rpc(methode: str, params: list):
    corps = {"jsonrpc": "2.0", "id": 1, "method": methode, "params": params}
    # 1) les RPC publics, gratuits : deux essais chacun avant de payer quoi
    #    que ce soit. Un point d'acces qui echoue est mis de cote une minute.
    for url in PUBLICS:
        if time.time() < _EN_PANNE.get(url, 0.0):
            continue
        for essai in range(2):
            _attendre_tour(url)
            try:
                r = _SESSION.post(url, json=corps, timeout=20)
                if r.status_code == 429:
                    time.sleep(0.5 * (essai + 1))
                    continue
                r.raise_for_status()
                j = r.json()
                if "error" not in j:
                    STATS["public"] += 1
                    return j.get("result")
            except Exception:
                time.sleep(0.3)
        _EN_PANNE[url] = time.time() + 60
    # 2) Helius, un credit, en changeant de cle si l'une est a sec
    for essai in range(len(config.HELIUS_API_KEYS) + 2):
        cle = config.helius_key()
        if not cle:
            break
        _attendre_tour("helius")
        try:
            r = _SESSION.post(f"https://mainnet.helius-rpc.com/?api-key={cle}",
                              json=corps, timeout=20)
            if r.status_code == 429:
                if "max usage" in (r.text or "").lower():
                    config.helius_a_sec(cle)
                else:
                    time.sleep(1.0)
                continue
            r.raise_for_status()
            j = r.json()
            if "error" not in j:
                STATS["helius"] += 1
                return j.get("result")
        except Exception:
            time.sleep(0.3)
    STATS["echec"] += 1
    return None


def _analyser(tx: dict, adresse: str) -> List[Dict]:
    """Les trades de `adresse` dans une transaction jsonParsed."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []

    def soldes(cle):
        out = {}
        for b in meta.get(cle) or []:
            if b.get("owner") != adresse:
                continue
            ui = (b.get("uiTokenAmount") or {})
            v = ui.get("uiAmount")
            if v is None:
                try:
                    v = float(ui.get("amount") or 0) / (10 ** int(ui.get("decimals") or 0))
                except (TypeError, ValueError):
                    v = 0.0
            out[b.get("mint")] = out.get(b.get("mint"), 0.0) + float(v or 0)
        return out

    avant, apres = soldes("preTokenBalances"), soldes("postTokenBalances")
    deltas = {m: apres.get(m, 0.0) - avant.get(m, 0.0) for m in set(avant) | set(apres)}

    # le SOL du wallet lui-meme (hors frais de transaction s'il les paie)
    cles = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    d_sol = 0.0
    for i, k in enumerate(cles):
        pk = k.get("pubkey") if isinstance(k, dict) else k
        if pk == adresse:
            try:
                d_sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
                if i == 0:
                    d_sol += (meta.get("fee") or 0) / 1e9
            except (KeyError, IndexError, TypeError):
                pass
            break
    d_quote = d_sol + sum(v for m, v in deltas.items() if m in QUOTES)

    ts = tx.get("blockTime") or 0
    sig = ((tx.get("transaction") or {}).get("signatures") or [""])[0]
    out = []
    for mint, d in deltas.items():
        if mint in QUOTES or abs(d) <= 0:
            continue
        if d > 0 and d_quote < -SOL_MINI * 0.5:
            sens = "achat"
        elif d < 0 and d_quote > SOL_MINI * 0.5:
            sens = "vente"
        else:
            continue          # transfert, airdrop : pas une position
        out.append({"cle": f"{sig}:{mint}:{sens}", "sig": sig, "mint": mint,
                    "sens": sens, "montant": abs(d), "ts": ts, "chain": "solana"})
    return out


# "adresse:signature" -> trades deja extraits (vide si ce n'etait pas un trade).
# Garde sur disque : sans ca, chaque lancement relisait ~2 700 transactions
# (90 wallets x 30) avant de pouvoir dire quoi que ce soit.
FICHIER = config.path("solana_swaps.json")
_LUES: Dict[str, List[Dict]] = {}
_LUES_VERROU = threading.Lock()
GARDE_LUES = 50_000
ECRIRE_TOUS_LES_S = 300
_ECRIT = {"at": time.time(), "sale": False}


def _charger() -> None:
    try:
        with open(FICHIER, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            _LUES.update(d)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _sauver(force: bool = False) -> None:
    if not _ECRIT["sale"]:
        return
    if not force and time.time() - _ECRIT["at"] < ECRIRE_TOUS_LES_S:
        return
    with _LUES_VERROU:
        instantane = dict(_LUES)
        _ECRIT.update(at=time.time(), sale=False)
    tmp = FICHIER + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(instantane, f, separators=(",", ":"))
        os.replace(tmp, FICHIER)
    except OSError:
        pass


_charger()


def mouvements(adresse: str, depuis: float, limite: int = 25) -> List[Dict]:
    """Achats et ventes de `adresse` posterieurs a `depuis` (horodatage)."""
    sigs = _rpc("getSignaturesForAddress", [adresse, {"limit": limite}])
    if not isinstance(sigs, list):
        return []
    out = []
    for s in sigs:
        sig = s.get("signature")
        bt = s.get("blockTime") or 0
        if not sig or s.get("err") is not None or (bt and bt < depuis):
            continue
        with _LUES_VERROU:
            deja = _LUES.get(adresse + ":" + sig)
        if deja is None:
            tx = _rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                               "maxSupportedTransactionVersion": 0}])
            if tx is None:
                continue          # pas memorise : on reessaiera au tour suivant
            deja = _analyser(tx, adresse)
            with _LUES_VERROU:
                if len(_LUES) > GARDE_LUES:
                    # on garde la moitie la plus recente (ordre d'insertion)
                    for k in list(_LUES)[:GARDE_LUES // 2]:
                        _LUES.pop(k, None)
                _LUES[adresse + ":" + sig] = deja
                _ECRIT["sale"] = True
        out.extend(deja)
    _sauver()
    return out


def bilan() -> str:
    d = max(1.0, time.time() - STATS["depuis"]) / 3600
    return (f"RPC public {STATS['public']}, Helius {STATS['helius']}, "
            f"echecs {STATS['echec']} en {d:.1f} h")
