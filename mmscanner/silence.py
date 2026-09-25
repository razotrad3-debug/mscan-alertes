"""Interrupteurs d'alertes, pilotes depuis l'application.

Trois choses peuvent se taire, independamment :

    · les alertes RSI d'UN coin precis (celles du canal Telegram) ;
    · toutes les alertes de trendlines ;
    · toutes les alertes de coins (grades, retours au plancher, devs).

Rien n'est supprime : on pose un drapeau, et on peut le retirer. Les calculs
continuent de tourner et restent visibles dans l'application — seul l'envoi
Telegram s'arrete. C'est voulu : couper le bruit ne doit pas rendre aveugle.
"""
import json
import os
import threading
from typing import Dict

import config

FICHIER = config.path("silence.json")
_VERROU = threading.RLock()

# les interrupteurs globaux reconnus
GLOBAUX = ("lignes", "coins")


def _lire() -> dict:
    with _VERROU:
        try:
            with open(FICHIER, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}


def _ecrire(d: dict) -> None:
    with _VERROU:
        tmp = FICHIER + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, separators=(",", ":"))
            os.replace(tmp, FICHIER)
        except OSError:
            pass


def muet(quoi: str) -> bool:
    """Cet envoi est-il suspendu ? `quoi` vaut 'lignes', 'coins', ou un mint."""
    return bool((_lire() or {}).get(quoi))


def basculer(quoi: str) -> bool:
    """Inverse l'interrupteur et renvoie le nouvel etat (True = suspendu)."""
    d = _lire()
    nouveau = not d.get(quoi)
    if nouveau:
        d[quoi] = True
    else:
        d.pop(quoi, None)
    _ecrire(d)
    return nouveau


def etat() -> Dict[str, bool]:
    """Tout ce qui est suspendu, pour l'affichage."""
    return dict(_lire() or {})
