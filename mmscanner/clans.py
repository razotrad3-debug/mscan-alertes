"""
Rosters de clans FOMO -> wallets suivis.

L'API FomoScan n'expose PAS la composition des clans (son OpenAPI ne declare
qu'un classement, pas de route "membres"). On travaille donc a partir des
rosters fournis a la main, deposes dans `clans/<Nom du clan>.txt`, un @handle
par ligne.

Pour la correspondance handle -> wallet, on passe par les fiches publiques de
fomoscan.sh : meme donnee que la route API /v2/user/handle, mais gratuite,
alors que l'API facture 2 500 CU par handle et que le plan gratuit n'en
accorde aucun. L'API ne sert plus que de secours si le site ne repond pas.

Ce module garde la trace de ce qui reste a faire et reprend tout seul aux
scans suivants, jusqu'a ce que tout soit resolu.
"""
import glob
import json
import os
import time
from typing import Dict, List

import config
from . import fomoscan, fomoscan_web, followed

CLANS_DIR = config.path("clans")
STATE_FILE = config.path("clans_state.json")
RETRY_AFTER_MIN = 45       # apres un 402 on repasse souvent : le quota FomoScan
                           # se recharge au changement de periode mensuelle, et
                           # on veut le rattraper vite, sans marteler l'API.


def _state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"done": {}, "no_wallet": {}, "next_try": 0}


def _save(st: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
    except Exception:
        pass


def rosters() -> Dict[str, List[str]]:
    """{nom du clan: [handles]} — un fichier par clan dans clans/."""
    out = {}
    for path in sorted(glob.glob(os.path.join(CLANS_DIR, "*.txt"))):
        name = os.path.splitext(os.path.basename(path))[0]
        # A_REMPLIR.txt est le formulaire de saisie manuelle, pas un roster ;
        # les fichiers prefixes "_" sont des brouillons.
        if name.startswith(("A_REMPLIR", "_")):
            continue
        handles = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    # "handle   # +$418.5K" -> "handle" (le PnL n'est qu'une note
                    # de priorite, il fixe l'ordre de resolution)
                    line = line.split("#")[0].strip().lstrip("@")
                    if line:
                        handles.append(line)
        except Exception:
            continue
        if handles:
            out[name] = handles
    return out


def pending() -> Dict[str, List[str]]:
    """Handles pas encore resolus (ni ajoutes, ni marques comme sans wallet)."""
    st = _state()
    done, nowal = st.get("done", {}), st.get("no_wallet", {})
    out = {}
    for clan, handles in rosters().items():
        todo = [h for h in handles
                if h.lower() not in done and h.lower() not in nowal]
        if todo:
            out[clan] = todo
    return out


def summary() -> dict:
    st = _state()
    todo = pending()
    return {
        "clans": len(rosters()),
        "total": sum(len(v) for v in rosters().values()),
        "resolus": len(st.get("done", {})),
        "sans_wallet": len(st.get("no_wallet", {})),
        "restants": sum(len(v) for v in todo.values()),
        "prochaine_tentative": st.get("next_try", 0),
    }


def resolve_pending(budget: int = 40, log=print) -> int:
    """
    Resout jusqu'a `budget` handles en attente et les ajoute aux adresses
    suivies. S'arrete net des le premier 402 (quota epuise) et reprogramme.
    Retourne le nombre de wallets ajoutes.
    """
    st = _state()
    if time.time() < st.get("next_try", 0):
        return 0
    todo = pending()
    if not todo or not fomoscan.has_key():
        return 0

    added, used = 0, 0
    for clan, handles in todo.items():
        rows = []
        for h in handles:
            if used >= budget:
                break
            used += 1
            # 1) fiche publique fomoscan.sh — gratuite
            w = fomoscan_web.lookup(h)
            wallet = w.get("solana") or w.get("ethereum")
            if wallet:
                rows.append((wallet, f"[{clan}] {h}"))
                st.setdefault("done", {})[h.lower()] = wallet
                continue
            if w.get("reason") == "inconnu de fomoscan":
                st.setdefault("no_wallet", {})[h.lower()] = w["reason"]
                continue

            # 2) secours : l'API (payante), si elle a du credit
            r = fomoscan.resolve_handle(h)
            if r.get("wallet"):
                rows.append((r["wallet"], f"[{clan}] {r.get('handle', h)}"))
                st.setdefault("done", {})[h.lower()] = r["wallet"]
            elif "402" in str(r.get("reason", "")):
                # quota epuise : on sauvegarde ce qu'on a et on repasse plus tard
                if rows:
                    added += followed.append_followed(rows)
                st["next_try"] = time.time() + RETRY_AFTER_MIN * 60
                _save(st)
                log(f"[clans] quota epuise — {added} ajoutes, reprise dans {RETRY_AFTER_MIN} min")
                return added
            else:
                st.setdefault("no_wallet", {})[h.lower()] = r.get("reason", "sans wallet")
        if rows:
            n = followed.append_followed(rows)
            added += n
            log(f"[clans] {clan} : {len(rows)} wallets resolus, {n} ajoutes")
        _save(st)
        if used >= budget:
            break
    return added
