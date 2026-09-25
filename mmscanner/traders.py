"""Surveillance rapprochee de quelques traders : achat ou vente, notification.

Le module `followed` sait deja lire les achats recents d'un wallet, mais il le
fait par vagues, pour alimenter la Convergence — on apprend qu'un wallet a
achete quelque part dans les 72 dernieres heures. Ici la question est autre :
pour TROIS adresses precises, on veut savoir dans la minute, et les ventes
autant que les achats.

Chaque mouvement n'est annonce qu'une fois : on retient la signature de la
transaction, pas seulement le jeton. Un wallet qui renforce sa position le fait
en plusieurs fois, et chaque renfort est une information.

La cadence a son propre fil, comme la surveillance RSI : elle ne depend pas du
scan, qui dure cinq minutes et ne part que toutes les dix.
"""
import json
import os
import threading
import time
from typing import Dict, List

import config

FICHIER = config.path("traders_vus.json")
_VERROU = threading.RLock()

# Les traders surveilles : (adresse, nom affiche).
# Une adresse base58 se lit chez Helius (Solana), une adresse 0x… chez les
# explorateurs EVM. Un meme trader peut avoir les deux.
SURVEILLES: List[tuple] = [
    ("E9aGsDLRYTfz3JMZYKq4JaGmw8j5Nb55fhfQDHNH7GXA", "XbtPika"),
    ("0x56612ed804fcef43f86baed0c616d3ae9a0d0bf7", "XbtPika"),

    ("2ZXtcsDXkDnWsCgBGd6zbn2TigkDBxT5uLhEGpmz37sc", "Donny"),
    ("0x9a463069f1bdfb513bf04afcf2729c190dc3e16e", "Donny"),

    ("31YTRXzEhdjLhY36BkwxS4JCNjm7tUbwz4BFCJyof7Nt", "KPsConviction"),
    ("0x149cf4fc89cff29be4706e43b94da176b79e7a84", "KPsConviction"),

    ("0xf4a3a96b0f42f9b7d6d2b15186fb8b2c296dea37", "theveeeemam"),

    ("DyXg3Xp6BoMq5K6Nmdb7hEzPMpkRWH2aXHHZhUgN1gd6", "Iruletrenches"),
    ("0xddd462bb053b57d5d73c9615e11a7284cfee9233", "Iruletrenches"),
]

# Traders qu'on veut suivre mais dont l'adresse est encore inconnue.
#
# Le 22/09, la fiche FomoScan de DonnyDicey repond noir sur blanc
# « wallets.solana.address: null, status: inactive » — il n'a jamais fait
# verifier de portefeuille — et KPsConviction renvoie un 404. Aucune recherche
# ne les trouvera tant qu'ils ne se verifient pas.
#
# Plutot que d'attendre qu'on y repense, on redemande periodiquement. Des
# qu'une adresse apparait, elle rejoint SURVEILLES et les notifications
# partent sans que personne n'ait rien a faire.
A_RESOUDRE = []          # toutes les adresses sont connues
RESOUDRE_H = 6.0        # on redemande toutes les six heures


def _resoudre_handles(log=print) -> int:
    """Cherche les adresses encore manquantes et les ajoute si elles sortent."""
    d = _lire()
    dernier = float(d.get("resolu_le") or 0)
    if time.time() - dernier < RESOUDRE_H * 3600:
        return 0
    d["resolu_le"] = time.time()
    trouves = dict(d.get("handles") or {})
    n = 0
    for handle in A_RESOUDRE:
        if handle in trouves:
            continue
        adresses = []
        for module in ("fomoscan_web", "fomoscan"):
            try:
                mod = __import__(f"mmscanner.{module}", fromlist=[module])
                r = (mod.lookup(handle) if module == "fomoscan_web"
                     else mod.resolve_handle(handle))
            except Exception:
                continue
            for cle in ("solana", "ethereum"):
                a = (r or {}).get(cle)
                if a and a not in adresses:
                    adresses.append(a)
            if adresses:
                break
        if adresses:
            trouves[handle] = adresses
            for a in adresses:
                if not any(a == x for x, _ in SURVEILLES):
                    SURVEILLES.append((a, handle))
            n += len(adresses)
            log(f"[traders] {handle} resolu : {', '.join(a[:12] + '…' for a in adresses)}")
    d["handles"] = trouves
    _ecrire(d)
    return n


def ajouter(adresse: str, nom: str, log=print) -> bool:
    """Ajoute une adresse a la main — pour quand on la recupere autrement."""
    adresse = (adresse or "").strip()
    if not adresse or any(adresse == a for a, _ in SURVEILLES):
        return False
    SURVEILLES.append((adresse, nom or adresse[:8]))
    d = _lire()
    h = dict(d.get("handles") or {})
    h.setdefault(nom, [])
    if adresse not in h[nom]:
        h[nom].append(adresse)
    d["handles"] = h
    _ecrire(d)
    log(f"[traders] {nom} ajoute : {adresse[:12]}…")
    return True

# Un tour par minute. Chaque tour relisait l'historique complet de chaque
# adresse Solana (l'appel « enhanced », cent credits) : on avait du ralentir a
# cinq minutes pour ne pas vider les cles. Depuis solana_swaps, un wallet qui
# n'a pas bouge ne coute qu'une signature (gratuite sur le RPC public, un
# credit sinon). On peut donc regarder chaque minute.
PAS_S = 60.0
FENETRE_H = 6.0         # profondeur de LECTURE, pour rattraper une coupure
GARDE = 4000            # signatures retenues, pour ne pas reannoncer

# ON LIT LOIN, MAIS ON N'ANNONCE QUE CE QUI EST FRAIS.
#
# La fenetre de six heures sert a rattraper : si l'application a ete coupee, ou
# si une adresse vient d'etre ajoutee, on veut voir ce qui s'est passe pour ne
# pas le reannoncer plus tard. Mais le PREVENIR n'a aucun sens — un achat
# vieux de deux heures n'est plus une occasion, c'est une nouvelle perimee.
#
# Tout ce qui est plus vieux que ce delai est donc enregistre en silence.
FRAICHEUR_S = 20 * 60

# Un trader achete aussi du SOL ou de l'USDC pour payer : ce n'est pas un
# mouvement de conviction.
QUOTES = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


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


def _mouvements_solana(adresse: str, depuis: float) -> List[Dict]:
    """Achats ET ventes d'un wallet Solana (voir solana_swaps : RPC standard,
    un credit au plus par appel, au lieu des cent de l'API enhanced)."""
    from . import solana_swaps
    return solana_swaps.mouvements(adresse, depuis, limite=40)


def _mouvements_evm(adresse: str, depuis: float) -> List[Dict]:
    """Idem cote EVM. Les explorateurs ne donnent que les achats."""
    from . import sources_evm

    out = []
    for chain in ("ethereum", "base", "robinhood", "bsc"):
        try:
            lignes = sources_evm.recent_buys(adresse, chain,
                                             hours=FENETRE_H) or []
        except Exception:
            continue
        for r in lignes:
            mint = r.get("mint")
            ts = r.get("ts") or 0
            if not mint or ts < depuis:
                continue
            out.append({"cle": f"{chain}:{mint}:{ts}", "mint": mint,
                        "sens": "achat", "montant": float(r.get("amount") or 0),
                        "ts": ts, "chain": chain})
    return out


def _message(nom: str, m: Dict, info: Dict) -> str:
    """Meme mise en forme que les alertes de coins : des liens nommes.

    L'adresse complete collee en clair prenait trois lignes sur le telephone
    et noyait le reste du message. On ecrit donc « DexScreener · GMGN ».
    """
    from .model import dex_link, gmgn_link

    from .telegram_alerts import PASTILLE

    chain = m.get("chain") or "solana"
    sym = (info.get("symbol") or m["mint"][:6])
    # LA PASTILLE DIT LA CHAINE, JAMAIS AUTRE CHOSE.
    #
    # J'avais mis un rond vert pour l'achat et un rouge pour la vente : or le
    # vert est deja la couleur de Robinhood dans tout le reste de
    # l'application. Un achat sur Solana s'affichait donc en vert, comme un
    # coin Robinhood. Le sens du mouvement passe par un triangle, qui n'entre
    # en concurrence avec aucune couleur.
    pastille = PASTILLE.get(chain, "⚪")
    sens = "BUY" if m["sens"] == "achat" else "SELL"
    tete = f"{pastille} {sens} · {nom}"

    # Le montant en dollars, quand on connait le prix. C'est lui qui dit si
    # le mouvement est une conviction ou une pichenette : le meme trader
    # peut poser mille dollars pour voir, ou cinquante mille parce qu'il y
    # croit. Sans prix on n'invente rien — on n'affiche simplement pas.
    px = float(info.get("price_usd") or 0)
    montant = float(m.get("montant") or 0)
    if px > 0 and montant > 0:
        usd = px * montant
        tete += (f" ({usd/1e6:.2f} M$)" if usd >= 1e6
                 else f" ({usd/1e3:.1f} K$)" if usd >= 1000
                 else f" ({usd:,.0f} $)")

    lignes = [tete, f"${sym}"]
    mc = info.get("market_cap") or info.get("mc")
    if mc:
        lignes[-1] += f" — {mc/1e6:.2f} M$" if mc >= 1e6 else f" — {mc/1e3:.0f} K$"
    lignes += ["", f"[DexScreener]({dex_link(chain, m['mint'])})"
                   f" · [GMGN]({gmgn_link(chain, m['mint'])})"]
    return "\n".join(lignes)


def verifier(log=print, envoyer: bool = None) -> int:
    """Lit les mouvements recents et annonce ceux qu'on n'a pas encore vus."""
    from . import telegram_alerts as tg
    from . import silence

    # les handles encore sans adresse : on redemande de temps en temps
    try:
        _resoudre_handles(log=log)
    except Exception as e:
        log(f"[traders] resolution : {e}")
    if not SURVEILLES:
        return 0
    if envoyer is None:
        # un seul emetteur : le bot cloud (voir telegram_alerts.alerts_enabled).
        # Si l'application envoyait aussi, chaque trade partirait deux fois
        # des que le PC est allume.
        envoyer = tg.alerts_enabled()
    vus = _lire()
    connus = set(vus.get("cles") or [])
    depuis = time.time() - FENETRE_H * 3600
    neuf, envoyes = [], 0
    global _PREMIER_TOUR
    bilan = {}

    for adresse, nom in SURVEILLES:
        if silence.muet(adresse) or silence.muet("traders"):
            continue
        try:
            if adresse.startswith("0x"):
                mouv = _mouvements_evm(adresse, depuis)
            else:
                mouv = _mouvements_solana(adresse, depuis)
        except Exception as e:
            log(f"[traders] {nom}: {e}")
            continue
        bilan[nom] = bilan.get(nom, 0) + len(mouv)
        for m in sorted(mouv, key=lambda x: x["ts"]):
            if m["cle"] in connus:
                continue
            neuf.append((nom, m))

    # Au premier tour, on dit ce qu'on a pu lire : c'est la seule facon de
    # voir dans les journaux du cloud que Helius repond (le secret y est
    # masque). Zero partout pendant des heures = cle a sec, pas calme plat.
    if _PREMIER_TOUR:
        _PREMIER_TOUR = False
        log("[traders] premier tour, mouvements lus sur 6 h : "
            + ", ".join(f"{n} {k}" for n, k in sorted(bilan.items())))

    # ce qui est trop vieux : on le retient, on ne le dit pas
    limite = time.time() - FRAICHEUR_S
    vieux = [(n, m) for n, m in neuf if (m.get("ts") or 0) < limite]
    neuf = [(n, m) for n, m in neuf if (m.get("ts") or 0) >= limite]
    if vieux:
        for _, m in vieux:
            connus.add(m["cle"])
        vus["cles"] = list(connus)[-GARDE:]
        _ecrire(vus)
        log(f"[traders] {len(vieux)} mouvement(s) trop anciens, notes sans alerte")

    if not neuf:
        return 0

    # Un trader achete souvent en plusieurs fois, a quelques secondes
    # d'ecart : trois transactions, trois messages identiques. On regroupe
    # par trader, jeton et sens, en additionnant les quantites. Chaque cle
    # reste retenue, pour ne rien reannoncer au tour suivant.
    groupes, cles_groupe = {}, {}
    for nom, m in neuf:
        g = (nom, m["mint"], m["sens"], m.get("chain") or "solana")
        if g not in groupes:
            groupes[g] = (nom, dict(m))
            cles_groupe[g] = []
        else:
            cumul = groupes[g][1]
            cumul["montant"] = float(cumul.get("montant") or 0) + float(m.get("montant") or 0)
            cumul["ts"] = max(cumul.get("ts") or 0, m.get("ts") or 0)
        cles_groupe[g].append(m["cle"])
    for g, cles in cles_groupe.items():
        connus.update(cles)
    neuf = list(groupes.values())

    # une seule requete pour tous les jetons concernes
    from . import holdings
    mints = list({m["mint"] for _, m in neuf})
    try:
        # prix FRAIS : le montant en dollars s'en deduit, et un prix
        # vieux de quarante minutes donnerait un chiffre faux sur un
        # coin qui vient de bouger. Quelques jetons par alerte, le
        # cout est negligeable.
        infos = holdings._metriques(mints, frais=True) or {}
    except Exception:
        infos = {}

    from .engine import is_crypto_native
    for nom, m in neuf:
        connus.add(m["cle"])
        info = infos.get(m["mint"]) or {}
        # LE JETON RECU N'EST PAS TOUJOURS UNE POSITION.
        #
        # Vendre un coin, c'est recevoir de l'USDC — compte comme un "achat"
        # d'USDC si l'on ne regarde que le sens du transfert. La liste QUOTES
        # ne couvrait que Solana ; cote EVM, ou l'USDC a une autre adresse sur
        # chaque chaine, tout passait. On juge donc sur le SYMBOLE, qui est le
        # meme partout, avec le garde-fou deja utilise par le scan : stables,
        # majors et actions tokenisees sont ecartes.
        if not is_crypto_native(info.get("symbol"), info.get("name"),
                                m["mint"], None):
            continue
        texte = _message(nom, m, info)
        if envoyer and tg.send(texte):
            envoyes += 1
            log(f"[traders] {nom} {m['sens']} {m['mint'][:8]}")
            # L'onglet « Aujourd'hui » liste ce qui est parti sur Telegram
            # dans la journee, lu dans ce meme registre. Sans cette ligne, le
            # message arrivait sur le telephone mais le coin restait invisible
            # dans l'application.
            if m["sens"] == "achat":
                try:
                    d_env = tg._load()
                    d_env[m["mint"]] = {
                        "at": time.time(),
                        "jour": time.strftime("%Y-%m-%d"),
                        "symbol": info.get("symbol") or m["mint"][:6],
                        "chain": m.get("chain") or "solana",
                        "grade": "", "par": nom,
                    }
                    tg._save(d_env)
                except Exception as e:
                    log(f"[traders] jour : {e}")

    vus["cles"] = list(connus)[-GARDE:]
    _ecrire(vus)
    return envoyes


_FIL = None
_PREMIER_TOUR = True


def boucle(log=print) -> None:
    while True:
        try:
            verifier(log=log)
        except Exception as e:
            log(f"[traders] {e}")
        time.sleep(PAS_S)


def demarrer(log=print):
    """Lance la surveillance dans son fil, une seule fois."""
    global _FIL
    if _FIL is not None and _FIL.is_alive():
        return _FIL
    from . import telegram_alerts as tg
    if not tg.alerts_enabled():
        # C'est le bot cloud qui surveille et qui envoie, PC allume ou non.
        # Les achats arrivent quand meme dans « Aujourd'hui » : le cloud
        # publie la liste du jour, que l'application relit.
        log("[traders] surveillance assuree par le bot cloud")
        return None
    _FIL = threading.Thread(target=boucle, kwargs={"log": log}, daemon=True,
                            name="traders")
    _FIL.start()
    noms = ", ".join(sorted({n for _, n in SURVEILLES}))
    log(f"[traders] surveillance lancee — {noms} "
        f"({len(SURVEILLES)} adresse(s), un tour toutes les {PAS_S:.0f} s)")
    return _FIL
