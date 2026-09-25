"""Interrupteurs d'alertes, pilotes depuis l'application.

Trois choses peuvent se taire, independamment :

    · les alertes RSI d'UN coin precis (celles du canal Telegram) ;
    · toutes les alertes de trendlines ;
    · toutes les alertes de coins (grades, retours au plancher, devs).

Rien n'est supprime : on pose un drapeau, et on peut le retirer. Les calculs
continuent de tourner et restent visibles dans l'application — seul l'envoi
Telegram s'arrete. C'est voulu : couper le bruit ne doit pas rendre aveugle.

C'est le bot cloud qui envoie, PC eteint ou non. Les interrupteurs, eux, se
manipulent dans l'application : chaque bascule y est donc publiee (chiffree,
voir partage), et le bot relit ce fichier. Sans ce pont, couper « Coins »
dans l'application ne coupait rien sur Telegram.
"""
import json
import os
import threading
import time
from typing import Dict

import config

FICHIER = config.path("silence.json")
PARTAGE = "silence.enc"
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


def _etat_effectif() -> dict:
    """Cote cloud : l'etat publie par l'application. Ailleurs : le fichier local."""
    if os.getenv("MSCAN_HEADLESS"):
        try:
            from mmscanner import partage
            d = partage.lire(PARTAGE, defaut=None)   # relu au plus toutes les 110 s
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return _lire() or {}


def muet(quoi: str) -> bool:
    """Cet envoi est-il suspendu ? `quoi` vaut 'lignes', 'coins', ou un mint."""
    return bool(_etat_effectif().get(quoi))


def publier_en_fond() -> None:
    """Pousse l'etat vers le bot. Deux essais : partage refuse de publier
    deux fois en moins de 45 s, et une seconde bascule rapide serait perdue."""
    def _go():
        from mmscanner import partage
        for attente in (0, 50):
            time.sleep(attente)
            try:
                partage.publier(PARTAGE, _lire() or {}, log=print)
            except Exception as e:
                print(f"[silence] publication : {e}")
    threading.Thread(target=_go, daemon=True, name="silence-pub").start()


def basculer(quoi: str) -> bool:
    """Inverse l'interrupteur et renvoie le nouvel etat (True = suspendu)."""
    d = _lire()
    nouveau = not d.get(quoi)
    if nouveau:
        d[quoi] = True
    else:
        d.pop(quoi, None)
    _ecrire(d)
    publier_en_fond()
    return nouveau


def etat() -> Dict[str, bool]:
    """Tout ce qui est suspendu, pour l'affichage."""
    return dict(_lire() or {})
