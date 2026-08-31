"""
Persistance du dernier scan.

Sans ca, fermer l'application jette tout : au redemarrage le Radar reste vide
pendant les cinq minutes du scan, alors qu'on avait des donnees exploitables
il y a deux minutes. On les ecrit sur disque a chaque fin de scan et on les
recharge au demarrage, en affichant leur age.
"""
import json
import os
import time
from typing import List

import config
from .model import Pair

CACHE_FILE = config.path("last_scan.json")
MAX_AGE_H = 6          # au-dela, les donnees ne valent plus rien sur du memecoin


def save(pairs: List[Pair]) -> bool:
    try:
        data = {"saved_at": time.time(),
                "pairs": [p.to_dict() for p in pairs]}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_FILE)     # ecriture atomique : jamais de fichier a moitie ecrit
        return True
    except Exception as e:
        print(f"[cache] ecriture impossible : {e}")
        return False


def load():
    """Retourne (pairs, saved_at). Liste vide si absent, illisible ou perime."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], 0.0

    saved_at = float(data.get("saved_at") or 0)
    if not saved_at or (time.time() - saved_at) > MAX_AGE_H * 3600:
        return [], 0.0

    champs = set(Pair.__dataclass_fields__)
    pairs = []
    for d in data.get("pairs", []):
        try:
            # to_dict() ajoute des proprietes calculees : on ne garde que les champs
            pairs.append(Pair(**{k: v for k, v in d.items() if k in champs}))
        except Exception:
            continue
    return pairs, saved_at
