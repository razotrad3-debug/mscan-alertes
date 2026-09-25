"""Alertes RSI sur un coin precis, envoyees dans le canal Telegram.

Reprend le jeu d'alertes TradingView pose a la main sur MEMECOINUSDT : un
RSI(14) par unite de temps, des seuils a 50, 65 et 70, et une consigne de vente
qui grandit avec l'unite de temps — 10-15 % en 15 minutes, 95 % en journalier.

TradingView ne peut pas prevenir MSCAN : ses webhooks appellent une URL
publique, et l'application tourne en local. On refait donc le calcul ici, sur
les memes bougies que le reste du radar, et on envoie le message nous-memes.

Le RSI n'est pas recalcule a l'identique de TradingView au centieme pres — les
bougies viennent de GeckoTerminal et non de Binance — mais les croisements
tombent au meme endroit, et c'est ce qui declenche l'action.
"""
import json
import os
import threading
import time
from typing import Dict, List, Optional

import config
from .indicators import rsi

FICHIER = config.path("rsi_watch.json")
_VERROU = threading.RLock()

# Les coins surveilles. Un couple (mint, nom affiche) par ligne.
SURVEILLES: List[tuple] = [
    ("Bb4jR951QtVjeFAYFLBYXDSMKjbTDroCLPbFLdd7pump", "MEMECOIN"),
    ("9sfCHMLWSVy6MD6zr7pR1gD3qbT7C6K1E3dMf2ZBpump", "LAYOOO"),
    ("HcfnJxLov6tY8i1dq9uYRRZKCvxADPpovcfkyXzdpump", "CHONKETHA"),
]

# La table des alertes, lue sur les alertes TradingView existantes.
#   unite -> {seuil: consigne}
# Une consigne vide signifie « simple croisement, pas d'ordre de vente ».
CONSIGNES: Dict[str, Dict[float, str]] = {
    "15m": {65: "Sell 10-15 %", 70: "Sell 25 %"},
    "1h":  {50: "", 65: "Sell 25-33 %", 70: "Sell 50 %"},
    "2h":  {65: "", 70: ""},
    "4h":  {50: "", 65: "Sell 50 % puis 75 %"},
    "8h":  {50: "", 65: "", 70: ""},
    "12h": {65: "Sell 50 % puis 75 %"},
    "1D":  {65: "Sell 85 %", 70: "Sell 95 %"},
    "2D":  {70: ""},
}

# Les seuils franchis VERS LE BAS. Le RSI 30 marque la survente : il ne se
# franchit pas en montant, il se franchit en descendant. Le meme jeu d'unites
# de temps que les seuils hauts, et aucune consigne — a 30 on regarde pour
# entrer, pas pour vendre.
SEUILS_BAS: Dict[float, str] = {30: ""}
CONSIGNES_BAS: Dict[str, Dict[float, str]] = {u: dict(SEUILS_BAS)
                                              for u in CONSIGNES}

# Comment obtenir chaque unite de temps a partir de trois appels seulement.
#   unite -> (serie, pas)   ; `pas` echantillonne une bougie sur N
SERIES = {
    "15m": ("m15", 1), "1h": ("m15", 4), "2h": ("m15", 8),
    "4h": ("h4", 1), "8h": ("h4", 2), "12h": ("h4", 3),
    "1D": ("d1", 1), "2D": ("d1", 2),
}

PERIODE = 14
RELANCE_MIN = 12.0          # on ne realerte pas le meme seuil avant ce delai

# Cadence de la surveillance. Elle tournait dans la boucle de scan : un scan
# dure cinq a six minutes et part toutes les dix, et le RSI etait calcule apres
# la veille et les trendlines — une alerte pouvait donc attendre un quart
# d'heure. Elle a maintenant son propre fil, qui ne fait que ca.
PAS_S = 60.0

# Chaque serie n'a pas besoin d'etre relue au meme rythme. Une bougie de 4 h
# bouge lentement : la relire chaque minute gaspille le quota GeckoTerminal
# dont le scan a besoin. On garde donc chaque serie le temps qu'elle reste
# valable, et seules les bougies de 15 minutes sont vraiment rafraichies.
FRAICHEUR = {"m15": 55.0, "h4": 290.0, "d1": 900.0}
_SERIES: Dict[str, Dict[str, dict]] = {}


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


def _bougies(pool: str, chain: str) -> Dict[str, List[float]]:
    """Les trois series de base, d'ou toutes les unites sont derivees.

    Chaque serie est gardee le temps qu'elle reste valable (voir FRAICHEUR) :
    sans ca, surveiller trois coins chaque minute couterait neuf appels par
    minute a GeckoTerminal, et le scan n'aurait plus son quota.
    """
    from . import sources_gecko as gecko
    maintenant = time.time()
    cache = _SERIES.setdefault(pool, {})
    out = {}
    for cle, (tf, agg, lim) in {"m15": ("minute", 15, 400),
                                "h4": ("hour", 4, 300),
                                "d1": ("day", 1, 200)}.items():
        garde = cache.get(cle)
        if garde and maintenant - garde["at"] < FRAICHEUR[cle]:
            out[cle] = garde["closes"]
            continue
        c = gecko.ohlcv(pool, tf, agg, lim, chain=chain)
        closes = [float(x[4]) for x in (c or []) if x and x[4]]
        # un appel qui echoue ne doit pas effacer ce qu'on avait
        if not closes and garde:
            out[cle] = garde["closes"]
            continue
        cache[cle] = {"at": maintenant, "closes": closes}
        out[cle] = closes
    return out


def _rsi_par_unite(series: Dict[str, List[float]]) -> Dict[str, tuple]:
    """RSI actuel et precedent, pour chaque unite de temps.

    Le precedent sert a reconnaitre un CROISEMENT plutot qu'un simple
    depassement : sans lui on realerterait a chaque tour tant que le RSI
    reste au-dessus du seuil.
    """
    out = {}
    for unite, (cle, pas) in SERIES.items():
        closes = series.get(cle) or []
        if pas > 1:
            closes = closes[::-1][::pas][::-1]      # une bougie sur `pas`
        if len(closes) < PERIODE + 2:
            continue
        maintenant = rsi(closes, PERIODE)
        avant = rsi(closes[:-1], PERIODE)
        if maintenant is None or avant is None:
            continue
        out[unite] = (avant, maintenant)
    return out


def _message(nom: str, mint: str, unite: str, seuil: float,
             consigne: str, avant: float, apres: float) -> str:
    """Le coin, l'unite, le seuil franchi — et la consigne s'il y en a une.

    Deux lignes quand le franchissement ne demande rien, trois quand un ordre
    de vente y est attache. Le lien DexScreener n'est plus ecrit : l'alerte
    sert a regarder le graphique, pas a le remplacer.
    """
    # 🟡 CHONKETHA
    # RSI 30 en 1H (BUY Zone)     — survente
    # RSI 70 en 1H (Sell Zone)    — surachat, a partir de 60
    zone = " (BUY Zone)" if seuil <= 30 else " (Sell Zone)" if seuil >= 60 else ""
    texte = f"🟡 {nom}\nRSI {seuil:.0f} en {unite.upper()}{zone}"
    return f"{texte}\n{consigne}" if consigne else texte


def verifier(log=print, envoyer: bool = None) -> int:
    """Calcule les RSI et envoie ce qui vient de franchir un seuil."""
    from . import sources_gecko as gecko
    from . import telegram_alerts as tg

    if envoyer is None:
        # un seul emetteur, le bot cloud : l'alerte part PC eteint, et jamais
        # en double quand il est allume (voir telegram_alerts.alerts_enabled)
        envoyer = tg.alerts_enabled()
    etat = _lire()
    maintenant = time.time()
    envoyes = 0

    from . import silence
    for mint, nom in SURVEILLES:
        if silence.muet(mint):
            continue          # suspendu depuis l'application
        pool = gecko.pool_for_token(mint, "solana")
        if not pool:
            continue
        rsis = _rsi_par_unite(_bougies(pool, "solana"))
        if not rsis:
            continue
        # ("haut", table) : le seuil se franchit en montant ; ("bas", table) :
        # en descendant. Le sens doit etre explicite, sinon un RSI 30 alerterait
        # au moment ou il REMONTE au-dessus de 30 — l'inverse de ce qu'on veut.
        for sens, table in (("haut", CONSIGNES), ("bas", CONSIGNES_BAS)):
            for unite, seuils in table.items():
                paire = rsis.get(unite)
                if not paire:
                    continue
                avant, apres = paire
                for seuil, consigne in sorted(seuils.items()):
                    if sens == "haut":
                        franchi = avant < seuil <= apres
                    else:
                        franchi = avant > seuil >= apres
                    if not franchi:
                        continue
                    # le sens fait partie de la cle : un seuil haut et un seuil
                    # bas de meme valeur garderaient sinon le meme delai
                    cle = (f"{mint}:{unite}:{seuil:g}" if sens == "haut"
                           else f"{mint}:{unite}:{seuil:g}:bas")
                    if maintenant - float(etat.get(cle) or 0) < RELANCE_MIN * 3600:
                        continue
                    etat[cle] = maintenant
                    texte = _message(nom, mint, unite, seuil, consigne,
                                     avant, apres)
                    if envoyer and tg.send(texte, chat=tg.chat_lignes()):
                        envoyes += 1
                        log(f"[rsi] {nom} {unite} RSI {seuil:g} {sens} — "
                            f"{consigne or 'croisement'}")
    if envoyes:
        _ecrire(etat)
    return envoyes


_FIL = None


def boucle(log=print) -> None:
    """Surveille sans s'arreter, a sa propre cadence."""
    while True:
        try:
            verifier(log=log)
        except Exception as e:
            log(f"[rsi] {e}")
        time.sleep(PAS_S)


def demarrer(log=print):
    """Lance la surveillance dans son fil, une seule fois."""
    global _FIL
    if _FIL is not None and _FIL.is_alive():
        return _FIL
    from . import telegram_alerts as tg
    if not tg.alerts_enabled():
        log("[rsi] surveillance assuree par le bot cloud")
        return None
    _FIL = threading.Thread(target=boucle, kwargs={"log": log}, daemon=True,
                            name="rsi-watch")
    _FIL.start()
    log(f"[rsi] surveillance lancee — {len(SURVEILLES)} coin(s), "
        f"un tour toutes les {PAS_S:.0f} s")
    return _FIL


def etat_actuel() -> Dict[str, Dict[str, float]]:
    """Le RSI du moment, par coin et par unite — pour verifier sans attendre."""
    from . import sources_gecko as gecko
    out = {}
    for mint, nom in SURVEILLES:
        pool = gecko.pool_for_token(mint, "solana")
        if not pool:
            out[nom] = {}
            continue
        out[nom] = {u: round(v[1], 1)
                    for u, v in _rsi_par_unite(_bougies(pool, "solana")).items()}
    return out
