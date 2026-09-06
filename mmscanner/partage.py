"""
MSCAN — Partage chiffre entre le cloud et l'application.

Les trendlines vont de l'application vers le cloud. Il fallait l'inverse :
le cloud envoie les alertes, l'application n'a aucun moyen de savoir ce
qu'il a envoye — son registre vit dans le cache GitHub, invisible d'ici.

Ce module ouvre le chemin retour. Meme depot, meme cle, meme principe :
on n'y pose jamais rien en clair, parce que le depot est public.
"""
import json
import os
import subprocess
import time
from typing import Optional

import requests

_SESSION = requests.Session()
DEPOT = "razotrad3-debug/mscan-alertes"
API = f"https://api.github.com/repos/{DEPOT}/contents/"
BRUT = f"https://raw.githubusercontent.com/{DEPOT}/main/"
DELAI_PUBLI_S = 45.0
DELAI_LECTURE_S = 110.0

_PUBLI = {}          # fichier -> {at, empreinte}
_LECTURE = {}        # fichier -> {at, valeur}


def _boite():
    from mmscanner import trendlines
    return trendlines._boite()


def _racine():
    from mmscanner import trendlines
    return trendlines._racine_depot()


def publier(fichier: str, donnees, log=print) -> bool:
    """
    Chiffre `donnees` et pousse le fichier sur le depot.

    Rien ne part sans cle : mieux vaut pas de partage qu'un contenu lisible
    par n'importe qui.
    """
    boite = _boite()
    racine = _racine()
    if boite is None or not racine:
        return False

    brut = json.dumps(donnees, sort_keys=True, default=str).encode()
    import hashlib
    empreinte = hashlib.sha1(brut).hexdigest()
    etat = _PUBLI.setdefault(fichier, {"at": 0.0, "empreinte": ""})
    if empreinte == etat["empreinte"]:
        return False
    if time.time() - etat["at"] < DELAI_PUBLI_S:
        return False

    chemin = os.path.join(racine, fichier)
    try:
        with open(chemin, "wb") as f:
            f.write(boite.encrypt(brut))
    except Exception as e:
        log(f"[partage] ecriture {fichier} : {e}")
        return False

    sans_fenetre = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def git(*args):
        # L'identite est passee a chaque appel : le runner GitHub n'en a
        # aucune de configuree, et un commit sans auteur echoue avec
        # "Author identity unknown". C'est ce qui empechait le cloud de
        # publier quoi que ce soit, en silence.
        return subprocess.run(("git", "-c", "user.name=MSCAN",
                               "-c", "user.email=mscan@localhost") + args,
                              cwd=racine, capture_output=True,
                              text=True, timeout=90, creationflags=sans_fenetre)
    try:
        git("add", fichier)
        r = git("commit", fichier, "-m", fichier.split(".")[0])
        if r.returncode != 0 and "nothing to commit" not in (r.stdout or ""):
            log(f"[partage] ECHEC commit {fichier} : "
                f"{((r.stderr or '') + (r.stdout or ''))[:200]}")
            return False
        r = git("push", "origin", "HEAD")
        if r.returncode != 0:
            log(f"[partage] push : {(r.stderr or '')[:140]}")
            return False
    except Exception as e:
        log(f"[partage] git : {e}")
        return False

    etat.update(at=time.time(), empreinte=empreinte)
    log(f"[partage] {fichier} publie")
    return True


def lire(fichier: str, log=print, defaut=None):
    """
    Recupere et dechiffre un fichier publie. Mis en cache 110 s.

    On passe par l'API contents et non par raw.githubusercontent : le CDN de
    ce dernier sert une copie perimee pendant plusieurs minutes, et ignore
    les parametres anti-cache.
    """
    etat = _LECTURE.setdefault(fichier, {"at": 0.0, "valeur": defaut})
    if time.time() - etat["at"] < DELAI_LECTURE_S:
        return etat["valeur"]
    etat["at"] = time.time()

    boite = _boite()
    if boite is None:
        return etat["valeur"]

    entetes = {"Accept": "application/vnd.github.raw"}
    jeton = (os.getenv("GITHUB_TOKEN") or "").strip()
    if jeton:
        entetes["Authorization"] = "Bearer " + jeton
    brut = None
    try:
        r = _SESSION.get(API + fichier, headers=entetes, timeout=25)
        if r.status_code == 404:
            return etat["valeur"]
        r.raise_for_status()
        brut = r.content
    except Exception:
        try:
            r = _SESSION.get(BRUT + fichier, timeout=25)
            if r.status_code == 404:
                return etat["valeur"]
            r.raise_for_status()
            brut = r.content
        except Exception as e:
            log(f"[partage] lecture {fichier} : {e}")
            return etat["valeur"]
    try:
        etat["valeur"] = json.loads(boite.decrypt(brut).decode())
    except Exception as e:
        log(f"[partage] dechiffrement {fichier} : {e}")
    return etat["valeur"]
