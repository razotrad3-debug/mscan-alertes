"""
Resolution de handles FOMO -> wallets Solana verifies (via FomoScan).

Pourquoi ce module :
  FOMO ne publie pas les wallets. Son API interne (prod-api.fomo.family/user/<handle>)
  repond 401 sans token de session. FomoScan maintient un index public de
  correspondances handle <-> wallet verifiees et l'expose proprement :

      GET /v2/user/handle/{handle}  ->  { handle, name, solanaAddress, evmAddress, ... }
      GET /v2/leaderboard/clans     ->  les clans FOMO classes
      GET /search?q=...             ->  recherche de profils (PUBLIC, sans cle)

Une cle API gratuite s'obtient sur https://partner.fomoscan.sh
et se colle dans .env :   FOMOSCAN_API_KEY=fsk_live_...
"""
import os
import time
from typing import Dict, List, Optional

import requests

import config

BASE = "https://api.fomoscan.sh"

_CACHE = {}          # cle -> (instant, valeur)
_CACHE_TTL = 6 * 3600
_TIMEOUT = 20


def _keys():
    """
    Toutes les cles disponibles, dans l'ordre d'essai.

    FOMOSCAN_API_KEYS accepte plusieurs cles separees par des virgules. Chaque
    compte recoit son propre quota mensuel, et une resolution de handle coute
    2 500 CU : cumuler les cles est le seul moyen d'aller au-dela d'un quota
    sans payer. Quand une cle rend 402, on passe automatiquement a la suivante.
    """
    raw = (os.getenv("FOMOSCAN_API_KEYS") or "").strip()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    single = (os.getenv("FOMOSCAN_API_KEY") or "").strip()
    if single and single not in keys:
        keys.append(single)
    return [k for k in keys if k not in _dead_keys]


_dead_keys = set()          # cles a court de credits sur cette periode


def has_key() -> bool:
    """
    Y a-t-il au moins une cle utilisable ?

    On interroge _keys() plutot que la variable au singulier : le depot et le
    workflow ne renseignent que FOMOSCAN_API_KEYS, au pluriel. La resolution
    de pseudo se refusait donc elle-meme avant d'essayer, alors que la cle
    etait bien la.
    """
    return bool(_keys())


def _get(path: str, params: dict = None, tries: int = 3) -> Optional[dict]:
    """
    Appel GET avec rotation de cles : un 402 (quota epuise) marque la cle comme
    vide pour la session et relance immediatement avec la suivante.
    """
    keys = _keys()
    if not keys:
        return None
    for key in keys:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        for attempt in range(tries):
            try:
                r = requests.get(BASE + path, params=params, headers=headers, timeout=25)
                if r.status_code == 402:
                    _dead_keys.add(key)      # cle a sec : on passe a la suivante
                    break
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception:
                if attempt == tries - 1:
                    break
                time.sleep(1.0 * (attempt + 1))
    return None



def search_public(query: str) -> List[Dict]:
    """Recherche de profils — endpoint PUBLIC, ne donne pas les wallets."""
    d = _get("/search", {"q": query.lstrip("@")})
    if not d or d.get("_error"):
        return []
    return d.get("results", []) or []


def resolve_handle(handle: str) -> Dict:
    """
    Handle FOMO -> wallet Solana verifie.

    Retour : {ok, handle, name, solana, evm}  ou  {ok: False, reason}
    """
    h = (handle or "").strip().lstrip("@")
    if not h:
        return {"ok": False, "handle": handle, "reason": "handle vide"}
    if not has_key():
        return {"ok": False, "handle": h, "reason": "cle FomoScan manquante"}

    d = _get(f"/v2/user/handle/{h}")
    if not d:
        return {"ok": False, "handle": h, "reason": "pas de reponse"}
    if d.get("_error") == "auth":
        return {"ok": False, "handle": h, "reason": "cle refusee (401/403)"}
    if d.get("_error") == "introuvable":
        return {"ok": False, "handle": h, "reason": "handle introuvable"}
    if d.get("_error"):
        return {"ok": False, "handle": h, "reason": d["_error"]}

    sol = d.get("solanaAddress")
    if not sol:
        return {"ok": False, "handle": h, "reason": "aucun wallet Solana verifie"}
    return {"ok": True, "handle": d.get("handle") or h, "name": d.get("name") or "",
            "solana": sol, "evm": d.get("evmAddress") or "", "id": d.get("id") or ""}


def resolve_many(handles: List[str], log=None) -> List[Dict]:
    """Resout une liste de handles, en douceur pour l'API."""
    out = []
    for h in handles:
        r = resolve_handle(h)
        out.append(r)
        if log:
            log(f"  {r['handle']:22} -> " + (r["solana"] if r["ok"] else "ECHEC : " + r["reason"]))
        time.sleep(0.25)
    return out


def clans(window: str = "24h") -> List[Dict]:
    """
    Classement des clans FOMO.

    Mis en cache 6 h : cet appel consomme du quota, et il etait declenche a
    chaque affichage de la page "Mes adresses" — de quoi vider la reserve
    mensuelle sans rien apporter, le classement ne bougeant pas si vite.
    """
    key = f"clans:{window}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    d = _get("/v2/leaderboard/clans", {"window": window}) or {}
    rows = d.get("data") or d.get("clans") or (d if isinstance(d, list) else [])
    rows = rows if isinstance(rows, list) else []
    if rows:                    # on ne met pas un echec en cache
        _CACHE[key] = (time.time(), rows)
    return rows


def find_clan(name: str, window: str = "24h") -> Optional[Dict]:
    """Retrouve un clan par son nom/handle (insensible a la casse)."""
    n = (name or "").strip().lstrip("@").lower()
    for c in clans(window):
        if n in ((c.get("handle") or "").lower(), (c.get("label") or "").lower()):
            return c
    # correspondance partielle
    for c in clans(window):
        blob = f"{c.get('handle') or ''} {c.get('label') or ''}".lower()
        if n and n in blob:
            return c
    return None


def quota() -> dict:
    """
    Etat de la cle : /v2/me dit combien d'unites il reste et sur quelle periode.
    Un 402 sur les autres routes veut dire "quota epuise", pas "route absente".
    """
    d = _get("/v2/me") or {}
    u = d.get("usage") or {}
    e = d.get("entitlement") or {}
    return {
        "plan": d.get("plan", "?"),
        "periode": u.get("period", "?"),
        "utilise": u.get("unitsUsed", 0),
        "restant": u.get("unitsRemaining", 0),
        "par_minute": e.get("ratePerMinute", 0),
        "ok": (u.get("unitsRemaining") or 0) > 0,
    }


def clan_members(name: str, window: str = "24h") -> List[Dict]:
    """
    Membres d'un clan, SI l'API les expose.

    L'OpenAPI de FomoScan (1.1.0) ne declare qu'une seule route clan,
    /v2/leaderboard/clans : il n'existe pas d'endpoint "membres". Cette
    fonction lit donc l'objet clan et en extrait toute liste de membres
    presente, sous les differents noms possibles. Elle renvoie [] tant que
    l'API ne fournit rien — sans jamais inventer d'adresse.
    """
    clan = find_clan(name, window)
    if not clan:
        return []
    for key in ("members", "traders", "users", "topTraders", "roster", "participants"):
        val = clan.get(key)
        if isinstance(val, list) and val:
            out = []
            for m in val:
                if not isinstance(m, dict):
                    continue
                out.append({
                    "handle": m.get("handle") or m.get("username") or "",
                    "id": m.get("id") or m.get("userId") or "",
                    "wallet": (m.get("wallet") or m.get("address")
                               or (m.get("wallets") or [{}])[0].get("address", "")
                               if isinstance(m.get("wallets"), list) else m.get("wallet", "")),
                })
            return [m for m in out if m["handle"] or m["wallet"]]
    return []
