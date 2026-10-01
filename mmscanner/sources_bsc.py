"""
Achats des traders suivis sur BNB Chain.

Il n'existe pas d'explorateur gratuit pour BNB Chain : BscScan a ferme son
API, Etherscan reserve cette chaine aux offres payantes, et ni Blockscout ni
Routescan ne la couvrent. Les noeuds publics, eux, repondent — mais refusent
les grandes plages de blocs (50 au plus chez 1RPC, 25 chez BlockRazor).

On lit donc la chaine au fil de l'eau, comme un indexeur : a chaque tour, les
blocs apparus depuis le tour precedent (environ 80 par minute), en une seule
requete pour TOUS les traders a la fois — l'evenement Transfer d'ERC-20 dont
le destinataire est l'un d'eux. Un jeton recu est un achat ; un stable ou du
BNB recu est le produit d'une vente, pas une position.

Le curseur (dernier bloc lu) est garde par l'appelant : une session qui
redemarre reprend ou la precedente s'etait arretee, dans la limite d'une
demi-heure de retard.
"""
import time
from typing import Dict, List, Optional

import requests

# (url, plus grande plage acceptee)
NOEUDS = (("https://1rpc.io/bnb", 50),
          ("https://bsc.blockrazor.xyz", 25))

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ce qu'on recoit en vendant : pas une position
QUOTES = {a.lower() for a in (
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",   # WBNB
    "0x55d398326f99059fF775485246999027B3197955",   # USDT
    "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",   # USDC
    "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",   # BUSD
    "0xc5f0f7b66764F6ec8C8Dff7BA683102295E16409",   # FDUSD
    "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",   # ETH (pont)
    "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",   # BTCB
)}

RETARD_MAX_BLOCS = 2400        # ~30 min : au-dela, on repart du present
LECTURE_MAX_BLOCS = 1200       # par tour, pour ne pas bloquer la boucle

_SESSION = requests.Session()
_DECIMALES: Dict[str, int] = {}
_PANNE: Dict[str, float] = {}


def _rpc(url: str, methode: str, params: list):
    try:
        r = _SESSION.post(url, json={"jsonrpc": "2.0", "id": 1,
                                     "method": methode, "params": params},
                          timeout=15)
        j = r.json()
    except Exception:
        return None, "reseau"
    if "error" in j:
        return None, str(j["error"].get("message") or j["error"])[:80]
    return j.get("result"), None


def _appel(methode: str, params: list, plage: int = 0):
    """Premier noeud disponible qui accepte la plage demandee."""
    for url, maxi in NOEUDS:
        if plage and plage > maxi:
            continue
        if time.time() < _PANNE.get(url, 0.0):
            continue
        for essai in range(3):
            res, err = _rpc(url, methode, params)
            if err is None:
                return res
            time.sleep(0.6 * (essai + 1))
        _PANNE[url] = time.time() + 60
    return None


def _decimales(jeton: str) -> int:
    if jeton not in _DECIMALES:
        res = _appel("eth_call", [{"to": jeton, "data": "0x313ce567"}, "latest"])
        try:
            _DECIMALES[jeton] = int(res, 16) if res and res != "0x" else 18
        except (TypeError, ValueError):
            _DECIMALES[jeton] = 18
    return _DECIMALES[jeton]


def _horodatage(bloc: int) -> float:
    res = _appel("eth_getBlockByNumber", [hex(bloc), False])
    try:
        return float(int(res["timestamp"], 16))
    except (TypeError, ValueError, KeyError):
        return time.time()


def achats(adresses: Dict[str, str], etat: dict) -> List[Dict]:
    """
    Les jetons recus par ces adresses depuis le dernier passage.

    `adresses` : {adresse 0x: nom}. `etat` : dict persistant de l'appelant,
    ou l'on garde le curseur ("bloc"). Rend [{nom, cle, mint, sens, montant,
    ts, chain}], au format des autres lectures de traders.
    """
    if not adresses:
        return []
    haut = _appel("eth_blockNumber", [])
    if not haut:
        return []
    haut = int(haut, 16)
    depart = int(etat.get("bloc") or 0) + 1
    if depart <= 1 or haut - depart > RETARD_MAX_BLOCS:
        depart = haut - RETARD_MAX_BLOCS
    fin_tour = min(haut, depart + LECTURE_MAX_BLOCS - 1)

    pads = {"0x" + "0" * 24 + a[2:].lower(): n for a, n in adresses.items()}
    heures: Dict[int, float] = {}
    out = []
    pas = max(m for _, m in NOEUDS)
    debut = depart
    while debut <= fin_tour:
        fin = min(fin_tour, debut + pas - 1)
        logs = _appel("eth_getLogs", [{"fromBlock": hex(debut), "toBlock": hex(fin),
                                       "topics": [TRANSFER, None, list(pads)]}],
                      plage=fin - debut + 1)
        if logs is None:
            # le premier noeud refuse ? on retente par petits morceaux
            pas_petit = min(m for _, m in NOEUDS)
            if pas > pas_petit:
                pas = pas_petit
                continue
            break                      # on reprendra ici au tour suivant
        for l in logs:
            jeton = (l.get("address") or "").lower()
            nom = pads.get((l.get("topics") or [None, None, ""])[2])
            if not jeton or not nom or jeton in QUOTES:
                continue
            try:
                brut = int(l.get("data") or "0x0", 16)
                bloc = int(l["blockNumber"], 16)
            except (TypeError, ValueError, KeyError):
                continue
            out.append({"nom": nom,
                        "cle": f"bsc:{l.get('transactionHash')}:{jeton}",
                        "mint": jeton, "sens": "achat",
                        "montant": brut / (10 ** _decimales(jeton)),
                        "ts": heures.setdefault(bloc, _horodatage(bloc)),
                        "chain": "bsc"})
        etat["bloc"] = fin
        debut = fin + 1
    return out
