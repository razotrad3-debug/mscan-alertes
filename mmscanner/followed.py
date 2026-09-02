"""
Adresses suivies (KOL / influenceurs / wallets repérés sur FOMO & co).

Différence avec discover_wallets :
  · discover_wallets  = découverte AUTOMATIQUE par récurrence sur les pumps
  · followed          = liste CUREE À LA MAIN, que tu choisis de suivre

Pour chaque adresse suivie, on lit ses achats récents on-chain (Helius) et on
affiche les coins où elle vient d'entrer, avec le lien vers le chart / l'analyse.

Signal fort (méthode MikeMike) : quand PLUSIEURS adresses suivies entrent sur
le MÊME coin dans une fenêtre courte -> convergence, à regarder en priorité.

Fichier : followed_wallets.txt   (une adresse par ligne + label optionnel)
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Dict

import config
from . import helius_tx, sources_dex
from . import sources_evm

FOLLOWED_FILE = config.FOLLOWED_FILE

# quote tokens : ce qu'on dépense, pas ce qu'on achète
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def load_followed() -> List[Tuple[str, str]]:
    """Retourne [(adresse, label), ...] depuis followed_wallets.txt."""
    out = []
    try:
        with open(FOLLOWED_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                addr = parts[0]
                label = parts[1].strip() if len(parts) > 1 else ""
                out.append((addr, label))
    except FileNotFoundError:
        pass
    return out


BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def parse_addresses(raw_text: str):
    """
    Extrait les adresses de n'importe quel format collé par l'utilisateur.

    Reconnaît les adresses Solana (base58) et EVM (0x… — Ethereum, Base).

    Accepte : une par ligne, séparées par virgules/points-virgules/espaces,
    avec ou sans label, collées depuis une URL (solscan, gmgn, dexscreener,
    birdeye...), avec des puces ou de la ponctuation autour.
    """
    out, seen = [], set()
    for line in (raw_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        found = sources_evm.EVM_RE_ANY.findall(line) + BASE58_RE.findall(line)
        if not found:
            continue
        # tout ce qui n'est pas une adresse sur la ligne sert de label
        label = line
        for f in found:
            label = label.replace(f, " ")
        # on retire les morceaux d'URL et la ponctuation de séparation
        label = re.sub(r"https?://\S+|[/,;|>\-•]+", " ", label)
        label = re.sub(r"\s+", " ", label)
        # on ne garde que du texte lisible (lettres, chiffres, espaces, . _ #)
        # on garde les crochets : ils portent le groupe, ex "[Dabal] pseudo"
        label = re.sub(r"[^\w .#\[\]À-ſ-]+", " ", label, flags=re.UNICODE)
        label = re.sub(r"\s+", " ", label).strip(" :=-_.")
        for addr in found:
            if addr in seen:
                continue
            seen.add(addr)
            out.append((addr, label[:40]))
    return out


BACKUP_FILE = config.path("followed_wallets.bak")


def save_followed(raw_text: str) -> int:
    """
    Ecrit la liste d'adresses saisie dans l'interface. Retourne le nb d'adresses.

    Le formulaire REMPLACE la liste entiere : une saisie partielle effacerait
    des dizaines d'adresses patiemment collectees. On copie donc l'etat
    precedent dans followed_wallets.bak avant d'ecrire, recuperable par
    restore_followed().
    """
    rows = parse_addresses(raw_text)
    anciennes = load_followed()

    if anciennes:
        try:
            with open(BACKUP_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(f"{a}  {l}".rstrip() for a, l in anciennes) + "\n")
        except Exception:
            pass

    header = ("# Adresses suivies (KOL / influenceurs / wallets reperes)" + "\n"
              + "# Une adresse par ligne, label optionnel apres un espace." + "\n"
              + "# Version precedente conservee dans followed_wallets.bak" + "\n" + "\n")
    body = "\n".join(f"{a}  {l}".rstrip() for a, l in rows)
    with open(FOLLOWED_FILE, "w", encoding="utf-8") as f:
        f.write(header + body + ("\n" if rows else ""))
    return len(rows)


def restore_followed() -> int:
    """Remet la liste telle qu'elle etait avant la derniere sauvegarde."""
    try:
        raw = open(BACKUP_FILE, "r", encoding="utf-8").read()
    except FileNotFoundError:
        return 0
    rows = parse_addresses(raw)
    if not rows:
        return 0
    with open(FOLLOWED_FILE, "w", encoding="utf-8") as f:
        f.write("# Adresses suivies (restaurees depuis la sauvegarde)" + "\n" + "\n"
                + "\n".join(f"{a}  {l}".rstrip() for a, l in rows) + "\n")
    return len(rows)

def append_followed(rows) -> int:
    """Ajoute des (adresse, label) a la liste sans ecraser l'existante."""
    existing = load_followed()
    have = {a for a, _ in existing}
    added = [(a, l) for a, l in rows if a and a not in have]
    if not added:
        return 0
    lines = [f"{a}  {l}".rstrip() for a, l in existing + added]
    save_followed("\n".join(lines))
    return len(added)


def raw_followed() -> str:
    """Contenu éditable (sans les commentaires) pour pré-remplir le formulaire."""
    return "\n".join(f"{a}  {l}".rstrip() for a, l in load_followed())


def recent_buys(address: str, hours: float = 72, max_tx: int = 120) -> List[Dict]:
    """
    Achats récents d'un wallet : [{mint, amount, ts}] du plus récent au plus ancien.

    L'adresse dit d'elle-même sur quelle chaîne aller la lire : une adresse
    base58 part chez Helius (Solana), une adresse 0x… chez les explorateurs
    EVM (Ethereum puis Base).
    """
    if sources_evm.is_evm(address):
        # toutes les chaines, pas seulement la premiere qui repond : un trader
        # actif sur Ethereum l'est souvent aussi sur Robinhood, et s'arreter
        # au premier resultat rendait la seconde invisible.
        rows = []
        for chain in ("ethereum", "base", "robinhood"):
            for r in sources_evm.recent_buys(address, chain, hours=hours):
                rows.append({**r, "chain": chain, "tx": 1})
        return rows
    if not config.HELIUS_API_KEY:
        return []
    cutoff = time.time() - hours * 3600
    txs = helius_tx._enhanced(address, "SWAP", limit=min(max_tx, 100))
    buys: Dict[str, Dict] = {}
    for tx in txs:
        ts = tx.get("timestamp", 0) or 0
        if ts < cutoff:
            continue
        for tt in tx.get("tokenTransfers", []) or []:
            mint = tt.get("mint")
            if not mint or mint in QUOTE_MINTS:
                continue
            if tt.get("toUserAccount") != address:
                continue
            amt = float(tt.get("tokenAmount") or 0)
            if amt <= 0:
                continue
            rec = buys.setdefault(mint, {"mint": mint, "amount": 0.0, "ts": ts, "tx": 0})
            rec["amount"] += amt
            rec["ts"] = max(rec["ts"], ts)
            rec["tx"] += 1
    return sorted(buys.values(), key=lambda b: b["ts"], reverse=True)


# Un wallet suivi achete aussi du WETH, du SOL ou un jeton d'entreprise. Ce
# n'est pas un signal de setup : on ne garde que le crypto-natif jouable, le
# meme perimetre que le radar et l'onglet Detenus.
MAX_MC_JOUABLE = 1_000_000_000
# Meme garde-fou que l'onglet Detenus : un coin sans marche derriere n'est pas
# une position, c'est un piege ou un cadavre.
MIN_LIQ_JOUABLE = 25_000
MIN_VOL_JOUABLE = 15_000


def _jouable(info: dict, mint: str = "") -> bool:
    from .engine import is_crypto_native
    if not is_crypto_native(info.get("symbol"), info.get("name"), mint):
        return False
    mc = info.get("market_cap") or info.get("mc") or 0
    if not (0 < mc <= MAX_MC_JOUABLE):
        return False
    # les releves ecrits avant ce controle n'ont pas ces champs : on ne peut
    # pas les juger la-dessus, le scan suivant s'en chargera
    liq = info.get("liquidity_usd")
    vol = info.get("vol_h24")
    if liq is not None and liq < MIN_LIQ_JOUABLE:
        return False
    if vol is not None and vol < MIN_VOL_JOUABLE:
        return False
    return True


def scan(hours: float = 72, max_coins_per_wallet: int = 8, log=print) -> Dict:
    """
    Retourne :
      wallets : [{address,label,short,buys:[{...coin enrichi...}]}]
      coins   : [{mint,name,symbol,mc,chg_h1,chg_h24,vol_h24,by:[labels],ts}]
    Les coins sur lesquels PLUSIEURS adresses suivies sont entrees remontent en tete.

    Les lectures on-chain et l'enrichissement se font en parallele : en serie,
    145 adresses prenaient plusieurs minutes pendant lesquelles l'application
    restait figee — au point de paraitre en retard sur les alertes.
    """
    followed = load_followed()
    if not followed:
        return {"wallets": [], "coins": [], "available": True, "empty": True}
    if not config.HELIUS_API_KEY:
        return {"wallets": [], "coins": [], "available": False,
                "reason": "cle Helius requise pour lire les achats on-chain"}

    t0 = time.time()

    # 1) achats recents de chaque adresse, en parallele
    def _achats(item):
        addr, label = item
        try:
            return addr, label, recent_buys(addr, hours=hours)[:max_coins_per_wallet]
        except Exception:
            return addr, label, []

    with ThreadPoolExecutor(max_workers=12) as ex:
        brut = list(ex.map(_achats, followed))

    # 2) un seul enrichissement par mint, en parallele lui aussi
    mints = {b["mint"] for _a, _l, buys in brut for b in buys if b.get("mint")}
    cache: Dict[str, Dict] = {}
    if mints:
        liste = list(mints)
        with ThreadPoolExecutor(max_workers=10) as ex:
            for m, info in zip(liste, ex.map(lambda x: sources_dex.enrich(x) or {}, liste)):
                cache[m] = info

    # 3) agregation
    wallets, agg = [], {}
    for addr, label, buys in brut:
        rows = []
        for b in buys:
            info = cache.get(b["mint"]) or {}
            if not info:
                continue
            if not _jouable(info, b["mint"]):
                continue
            row = {
                "mint": b["mint"], "ts": b["ts"], "amount": b.get("amount", 0),
                # sans la chaine, l'interface fabriquait un lien /solana/ pour
                # une adresse Ethereum : DexScreener repondait "introuvable".
                # DexScreener d'abord, pour rester coherent avec la paire qu'on
                # pointe ; sinon la chaine ou l'achat a ete lu.
                "chain": info.get("chain") or b.get("chain") or "",
                "name": info.get("name", "?"), "symbol": info.get("symbol", "?"),
                "mc": info.get("market_cap", 0), "chg_h1": info.get("chg_h1", 0),
                "chg_h24": info.get("chg_h24", 0), "vol_h24": info.get("vol_h24", 0),
                "pair": info.get("pair_address", ""),
            }
            rows.append(row)
            a = agg.setdefault(b["mint"], {**row, "by": [], "ts": b["ts"]})
            who = label or addr[:4] + "…" + addr[-4:]
            # un trader suivi sur Solana ET sur EVM porte le meme libelle :
            # il ne doit compter qu'une fois dans la convergence.
            if who not in a["by"]:
                a["by"].append(who)
            a["ts"] = max(a["ts"], b["ts"])
        wallets.append({"address": addr, "label": label,
                        "short": addr[:4] + "…" + addr[-4:], "buys": rows})

    coins = sorted(agg.values(), key=lambda c: (len(c["by"]), c["ts"]), reverse=True)
    log(f"[followed] {len(followed)} adresses, {len(coins)} coins "
        f"({time.time() - t0:.0f}s)")
    return {"wallets": wallets, "coins": coins, "available": True, "empty": False}



# convention d'etiquette : "[Dabal] pseudo" -> groupe "Dabal"
GROUP_RE = re.compile(r"^\[([^\]]{1,24})\]\s*(.*)$")


def split_group(label: str):
    """
    'label' -> (groupe, reste).

    Convention : "[Dabal] pseudo" -> groupe "Dabal".
    Sans crochets, on deduit un groupe par defaut a partir du contenu.
    """
    label = (label or "").strip()
    m = GROUP_RE.match(label)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    low = label.lower()
    if low.startswith("early_x") or "early_x" in low:
        return "on-chain", label
    if "top" in low and any(w in low for w in ("7d", "24h", "30d")):
        return "Top FOMO", label
    return "Suivi", label


def tracked_registry():
    """
    Tous les wallets suivis, avec leur ORIGINE — pour repondre a
    "ce coin vient d'ou ?" sur le radar.

    Retour : {adresse: {"label": str, "origin": "suivi"|"onchain"}}
      · "suivi"   -> TES adresses (KOL FOMO, clans...) : le label est le pseudo
      · "onchain" -> detecte automatiquement par recurrence sur les pumps
    """
    import config as _c
    reg = {}
    # 1) detectes automatiquement
    try:
        with open(_c.SMART_WALLETS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                lab = (parts[1] if len(parts) > 1 else "")[:60]
                reg[parts[0]] = {"label": lab, "origin": "onchain",
                                 "group": "on-chain"}
    except Exception:
        pass
    # 2) les tiennes ecrasent : une adresse que TU suis est d'abord la tienne
    for addr, label in load_followed():
        grp, rest = split_group(label)
        reg[addr] = {"label": rest or label or "suivi", "origin": "suivi", "group": grp}
    return reg


def tracked_lines():
    """Format attendu par le scanner : 'adresse  label'."""
    return [f"{a}  {v['label']}".rstrip() for a, v in tracked_registry().items()]


BUYS_CACHE = config.path("followed_buys.json")


FENETRE_ACHATS_H = 72.0


def save_buys(data: dict) -> None:
    """
    Ecrit le releve d'achats, en FUSIONNANT avec le precedent.

    Meme principe que pour les avoirs : une chaine muette pendant un scan ne
    doit pas effacer ce qu'elle avait rapporte au scan d'avant. En remplacant
    le fichier, un incident passager sur Robinhood faisait disparaitre tous
    ses coins du radar et de l'onglet Positions jusqu'au scan suivant.

    Les entrees sortent d'elles-memes de la fenetre de 72 h.
    """
    try:
        import json
        anciens = {}
        try:
            with open(BUYS_CACHE, "r", encoding="utf-8") as f:
                for c in (json.load(f) or {}).get("coins", []):
                    if c.get("mint"):
                        anciens[c["mint"]] = c
        except Exception:
            pass

        for c in data.get("coins", []):
            m = c.get("mint")
            if not m:
                continue
            vieux = anciens.get(m)
            # le releve le plus recent gagne, mais on ne perd jamais un coin
            if not vieux or (c.get("ts") or 0) >= (vieux.get("ts") or 0):
                anciens[m] = c

        limite = time.time() - FENETRE_ACHATS_H * 3600
        coins = [c for c in anciens.values() if (c.get("ts") or 0) >= limite]
        with open(BUYS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "coins": coins}, f)
    except Exception:
        pass


_CHAINES_TENTEES = 0.0     # derniere tentative de completion (anti-martelage)


def completer_chaines(coins: list) -> bool:
    """
    Retrouve la chaine et la paire des coins qui ne les ont pas.

    Sans chaine on ne peut pas ouvrir le graphique : le lien tombait sur la
    recherche DexScreener, ou il fallait encore cliquer. Un seul appel couvre
    30 jetons, toutes chaines confondues. Retourne True si quelque chose a
    change, pour que l'appelant reecrive le cache.
    """
    global _CHAINES_TENTEES
    manquants = [c for c in coins if c.get("mint") and not c.get("chain")]
    if not manquants:
        return False
    if time.time() - _CHAINES_TENTEES < 300:      # DexScreener muet : on patiente
        return False
    _CHAINES_TENTEES = time.time()

    from .holdings import _metriques
    infos = _metriques([c["mint"] for c in manquants])
    change = False
    for c in manquants:
        d = infos.get(c["mint"])
        if not d:
            continue
        c["chain"] = d.get("chain") or ""
        if d.get("pair"):
            c["pair"] = d["pair"]
        change = True
    return change


def load_buys() -> dict:
    """
    Dernier releve d'achats ecrit sur disque.

    L'interface s'en sert au demarrage : sans lui, l'onglet Positions restait
    vide jusqu'au premier scan complet, soit une dizaine de minutes.
    """
    try:
        import json
        with open(BUYS_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        # le filtre s'applique aussi a la relecture : un releve ecrit avant
        # sa mise en place ne doit pas reafficher du WETH
        coins = [c for c in (d.get("coins") or []) if _jouable(c, c.get("mint", ""))]
        # un releve ecrit avant qu'on enregistre la chaine se repare ici, une
        # fois, et le fichier est remis a jour : les liens ouvrent alors le
        # graphique directement, sur Ethereum comme sur Solana
        if completer_chaines(coins):
            d["coins"] = coins
            try:
                with open(BUYS_CACHE, "w", encoding="utf-8") as f:
                    json.dump(d, f)
            except Exception:
                pass
        return {"coins": coins, "at": d.get("at") or 0}
    except Exception:
        return {"coins": [], "at": 0}


def recent_mints_dates(hours: float = 48) -> dict:
    """{mint: instant du dernier achat} — sert a savoir si un coin est de
    nouveau frais apres avoir ete marque comme vu."""
    try:
        import json
        with open(BUYS_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    limite = time.time() - hours * 3600
    out = {}
    for c in d.get("coins", []):
        m, ts = c.get("mint"), c.get("ts") or 0
        if m and ts >= limite and _jouable(c, m):
            out[m] = max(out.get(m, 0), ts)
    return out


def recent_mints(hours: float = 48) -> list:
    """
    Mints sur lesquels une adresse suivie est entree recemment.

    Ces coins entrent dans le scan comme candidats a part entiere : c'est le
    signal le plus direct qu'on ait, il ne doit pas dependre du fait que le
    coin soit assez gros pour ressortir de la decouverte generique.
    """
    try:
        import json
        with open(BUYS_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    # Ici on ne filtre que sur la nature du jeton : ces coins partent au scan
    # pour y etre NOTES comme les autres, et c'est la note qui decide. Poser
    # un seuil de liquidite avant l'analyse reviendrait a juger a leur place.
    from .engine import is_crypto_native
    limite = time.time() - hours * 3600
    out = []
    for c in d.get("coins", []):
        m, ts = c.get("mint"), c.get("ts") or 0
        if not m or ts < limite:
            continue
        if not is_crypto_native(c.get("symbol"), c.get("name"), m):
            continue
        if (c.get("mc") or 0) > MAX_MC_JOUABLE:
            continue
        out.append(m)
    return out
