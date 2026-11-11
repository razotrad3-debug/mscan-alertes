"""
Profil d'un coin au moment de l'alerte : est-il fabrique ? ses holders sont-ils solides ?

D'OU VIENNENT CES REGLES
    Etude du 11 novembre 2026 sur les 1 044 coins vus par le scanner entre le
    25 septembre et le 11 novembre (324 notifies, 720 seulement photographies),
    parcours reconstruit sur bougies, sommet compte seulement s'il est atteint
    avec du vrai volume.

    1. UN TIERS DES COINS ETAIENT FABRIQUES (353 sur 1 044). Des bots
       s'echangent le coin entre eux : volume identique a 3 % pres chaque
       heure (6 M$/h sur 400 K$ de liquidite), prix qui monte en ligne droite,
       puis la liquidite est retiree. Le meme nom revient sous d'autres
       adresses (ALLOX 18 fois, RSGP 12 fois). Trois jours apres, 72 % ont
       perdu plus de la moitie. Deux marques, mesurables au moment de l'alerte :
         - trade moyen >= 250 $ ET volume d'une heure >= 3 fois la liquidite
           (91 fabriques sur 101 captes, 2 vrais coins bloques, aucun gagnant) ;
         - un meme wallet-coffre detient >= 50 % de l'offre (34 sur 34, aucun
           vrai coin).

    2. LA SEULE MESURE QUI SEPARE LES FUTURS x5 : LA QUALITE DES HOLDERS.
       Sur 456 vrais coins photographies, verifie a l'identique sur deux
       periodes et dans les deux groupes (notifies / seulement vus) :
         - holders SOLIDES (detention mediane >= 20 $, ou >= 20 % des wallets
           entre 100 et 1 000 $) : 9 a 21 % de x5 ;
         - holders FAIBLES (mediane < 8 $ ET < 13 % de wallets entre 100 et
           1 000 $) : 2 % de x5, aucun x10 sur 94 coins.
       Ni les wallets (leur passe ne predit pas leur avenir), ni la forme du
       graphique, ni les volumes, ni la note du scanner ne font mieux que le
       hasard sur cet echantillon.

On affiche le profil, on ne decide pas a la place de l'utilisateur — sauf pour
les coins fabriques, qui ne sont pas des setups mais des pieges.
"""
import time
from typing import Optional

# wallet-coffre commun a tous les coins de l'usine (autorite d'une courbe de
# lancement) : il porte 80 % de l'offre tant que la courbe n'est pas videe
COFFRE = "FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM"
COFFRE_MINI = 0.50

TRADE_MOYEN_MINI = 250.0      # $ par trade sur une heure
ROTATION_MINI = 3.0           # volume d'une heure / liquidite

SOLIDE_MEDIANE = 20.0         # $ detenus par le holder median
SOLIDE_PART = 0.20            # part des wallets entre 100 et 1 000 $
FAIBLE_MEDIANE = 8.0
FAIBLE_PART = 0.13

PHOTO_FRAICHE_S = 1800

# Pools, routeurs, plateformes : presents chez plus d'un coin sur cinq, ce ne
# sont pas des holders.
INFRA = {
    "27HFmP7ccLadGswvQfvea4o3juLw75cPF4V6jWpHM3MX",
    "2QfBNK2WDwSLoUQRb1zAnp3KM12N9hQ8q6ApwUMnWW2T",
    "2rbMgYvzAb3xDk6vXrzKkY3VwsmyDZsJTkvB3JJYsRzA",
    "33zdNtc9NcA2GZtuRTvGSc8XxaABedRDrny31fGkDpWF",
    "34FKjAdVcTax2DHqV2XnbXa9J3zmyKcFuFKWbcmgxjgm",
    "3CgvbiM3op4vjrrjH2zcrQUwsqh5veNVRjFCB9N6sRoD",
    "3KcPSJ8ouE7H1feoWHmhDwXhLnXhpxP6R6713uytFmBM",
    "3LoAYHuSd7Gh8d7RTFnhvYtiTiefdZ5ByamU42vkzd76",
    "4XBqViD1XYF1qHrErrsXBzDrCapvP9fEFX4LPjXZi9YU",
    "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
    "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD",
    "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV",
    "6Gfahmxqn1VfJE1rZ3JsDZdztUKMc4F8bjR9pfu1XhWZ",
    "6ooVBXhnqAXaF91cu49YmWhoFuE6WLdZWTwNYvTuhyBd",
    "7NNzakXLFv2VfhUuoZ9i4Uy4f63BDJcR96tMCucXa6KL",
    "7QoTjc15WNqZnRqBnR6ie81XSbfUDHz4bRbcqKxuuAGu",
    "7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ",
    "7hTckgnGnLQR6sdH7YkqFTAA7VwTfYFaZ6EhEsU3saCX",
    "8pY1AukbuPgUE3EetyLa59rFLMimJGT94ZzbMEZcQF4w",
    "8psNvWTrdNTiVRNzAgsou9kETXNJm2SXZyaKuJraVRtf",
    "9rPYyANsfQZw3DnDmKE3YCQF5E8oD89UXoHn9JFEhJUz",
    "AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY",
    "BEochrdGhm5pcKVF73DURYrGo1DLzxq6bBcUBphQR64d",
    "BM9CcyErJcu2mjrFvUsRRrD3snGeHDDVirJLvL6EjvMN",
    "CzYQ2kFnBxsNEt9Zy34vQ3n5fSDhvA4o4XaTnq1rLvyr",
    "DSN3j1ykL3obAVNv7ZX49VsFCPe4LqzxHnmtLiPwY6xg",
    "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL",
    "FMVHP1xw6ZHB9zLGyrJeeAvEzCBgouH8bPzdkYqq2C5W",
    "FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz",
    "Fn9n3ftUzjhcmoLwXbTqJdATzamCp3xPEu4LmLSS8ELr",
    "Fs9RN3wAsuJKPbTmtX5eek1bhW5krNH8RkQxkFAtgNfR",
    "G5UZAVbAf46s7cKWoyKu8kYTip9DGTpbLZ2qa9Aq69dP",
    "GP8StUXNYSZjPikyRsvkTbvRV1GBxMErb59cpeCJnDf1",
    "HFqp6ErWHY6Uzhj8rFyjYuDya2mXUpYEk8VW75K9PSiY",
    "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC",
    "HU23r7UoZbqTUuh3vA7emAGztFtqwTeVips789vqxxBw",
    "HWGoJ1HaMFUiDgfD7FVxMaYJAE2A5JjF7si6CgWSvRFQ",
    "kiwiC4pg5mC4N5AhpXc4Av3V6oV7Sn2p3CqB7NeHbJJ",
    "mP4tnNkwAtRLpSZG5CqcH3CVPJHgVw7XH3j6YRyayQP",
    "sobLmyY3kjTYqk2r7Ui18PC6YzHUBDaD2tk6PPNAv4o",
}


def fabrique(p) -> Optional[str]:
    """La raison pour laquelle ce coin est fabrique, ou None."""
    tx = (getattr(p, "buys_h1", 0) or 0) + (getattr(p, "sells_h1", 0) or 0)
    vol = float(getattr(p, "vol_h1", 0) or 0)
    liq = float(getattr(p, "liquidity_usd", 0) or 0)
    if tx > 0 and liq > 0 and vol / tx >= TRADE_MOYEN_MINI and vol >= ROTATION_MINI * liq:
        return (f"volume de bots ({vol / liq:.0f} fois la liquidite en une heure, "
                f"{vol / tx:.0f} $ par trade)")
    return None


def mesurer(photo: dict, prix: float = 0.0) -> Optional[dict]:
    """Les mesures de holders d'une photo de soldes (voir holder_flow)."""
    h = photo.get("holders") or {}
    if len(h) < 50:
        return None
    offre = float(photo.get("supply") or 0) or sum(h.values()) or 1.0
    px = float(photo.get("price") or 0) or float(prix or 0)
    tri = sorted(h.items(), key=lambda kv: -kv[1])
    pool = {w for w, b in tri[:3] if w in INFRA or b / offre > 0.12}
    usd = [b * px for w, b in tri if w not in pool and w not in INFRA]
    o = {"n": len(h), "coffre": h.get(COFFRE, 0.0) / offre}
    if px > 0 and usd:
        usd.sort()
        n = len(usd)
        o["mediane"] = usd[n // 2] if n % 2 else (usd[n // 2 - 1] + usd[n // 2]) / 2
        o["part_100_1k"] = sum(1 for u in usd if 100 <= u < 1000) / n
    return o


def verdict(m: Optional[dict]) -> str:
    """'solides', 'faibles', 'moyens' ou '' si on ne sait pas."""
    if not m or "mediane" not in m:
        return ""
    if m["mediane"] >= SOLIDE_MEDIANE or m["part_100_1k"] >= SOLIDE_PART:
        return "solides"
    if m["mediane"] < FAIBLE_MEDIANE and m["part_100_1k"] < FAIBLE_PART:
        return "faibles"
    return "moyens"


def holders(mint: str, prix: float, symbole: str = "") -> Optional[dict]:
    """Mesures sur la photo la plus recente ; en prend une si elle a vieilli."""
    if not mint or mint.startswith("0x"):
        return None                       # les photos n'existent que sur Solana
    from . import holder_flow
    photos = holder_flow._load(mint)
    if not photos or time.time() - float(photos[-1].get("ts") or 0) > PHOTO_FRAICHE_S:
        try:
            if holder_flow.snapshot(mint, prix, force=True, symbol=symbole):
                photos = holder_flow._load(mint)
        except Exception:
            pass
    if not photos:
        return None
    return mesurer(photos[-1], prix)


def juger(p) -> dict:
    """
    Tout ce qu'il faut pour decider d'une alerte :
      fabrique : raison (str) ou None
      holders  : 'solides' / 'faibles' / 'moyens' / ''
      mediane, part_100_1k : les deux mesures, si connues
    """
    out = {"fabrique": fabrique(p), "holders": "", "mediane": None, "part_100_1k": None}
    try:
        m = holders(getattr(p, "mint", ""), float(getattr(p, "price_usd", 0) or 0),
                    getattr(p, "symbol", "") or "")
    except Exception:
        m = None
    if m:
        if not out["fabrique"] and m.get("coffre", 0) >= COFFRE_MINI:
            out["fabrique"] = f"wallet-coffre de l'usine ({m['coffre'] * 100:.0f} % de l'offre)"
        out["holders"] = verdict(m)
        out["mediane"] = m.get("mediane")
        out["part_100_1k"] = m.get("part_100_1k")
    return out


def ligne(j: dict) -> str:
    """La ligne ajoutee a l'alerte Telegram, ou '' s'il n'y a rien a dire."""
    if j.get("holders") == "solides":
        return "💎 Holders solides"
    if j.get("holders") == "faibles":
        return "⚠️ Holders faibles"
    return ""
