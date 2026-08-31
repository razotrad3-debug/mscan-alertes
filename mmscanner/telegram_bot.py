"""
Interface de consultation Telegram.

Le serveur scanne en boucle de son cote ; ce module se contente de REPONDRE
aux commandes envoyees depuis le telephone, en lisant le dernier scan garde en
memoire. Aucune commande ne declenche de scan (sauf /scan, explicitement) :
la reponse est donc instantanee, meme quand l'ordinateur est eteint.

Commandes :
  /top       les 10 meilleurs coins du dernier scan
  /aplus     uniquement les A+
  /solana /base /eth /robinhood   filtre par chaine
  /wallets   les smart wallets les mieux notes
  /coin <adresse>   analyse d'un token precis
  /etat      date du dernier scan, nb de paires
  /scan      force un nouveau scan (2 a 5 min)
"""
import time
from typing import Callable, Dict, List

import requests

import config
from . import telegram_alerts as tg

API = "https://api.telegram.org/bot{token}/{method}"
_offset = [0]

HELP = (
    "*MSCAN*\n"
    "`/top` les 10 meilleurs coins\n"
    "`/aplus` uniquement les A+\n"
    "`/solana` `/base` `/eth` `/robinhood` par chaine\n"
    "`/wallets` les smart wallets les mieux notes\n"
    "`/coin <adresse>` analyse d'un token\n"
    "`/etat` date du dernier scan\n"
    "`/scan` force un scan (2-5 min)"
)


def _line(p) -> str:
    chain = (getattr(p, "chain", "") or "solana").lower()
    lab = config.CHAIN_META.get(chain, {}).get("label", chain[:3].title())
    w = f" · 👛{p.smart_holders}" if getattr(p, "smart_holders", 0) else ""
    return (f"*{tg._esc(p.symbol)}* `{p.grade}` {p.score}/{p.max_score} · {lab} · "
            f"{tg._esc(p.phase)}\n  {tg._usd(p.market_cap)} · 24h {p.chg_h24:+.0f}%{w}\n"
            f"  `{p.mint}`")


def _list(pairs: List, title: str, limit: int = 10) -> str:
    if not pairs:
        return f"*{title}*\nRien pour l'instant — le prochain scan remplira la liste."
    body = "\n\n".join(_line(p) for p in pairs[:limit])
    return f"*{title}* · {len(pairs)} resultat(s)\n\n{body}"


def handle(text: str, pairs: List, rescan: Callable = None, last_scan: float = 0) -> str:
    """Traduit une commande en reponse. `pairs` = dernier scan garde en memoire."""
    text = (text or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().lstrip("/").split("@")[0]
    arg = arg.strip()

    if cmd in ("start", "help", "aide"):
        return HELP

    if cmd == "etat":
        if not last_scan:
            return "Aucun scan termine pour l'instant."
        mins = int((time.time() - last_scan) / 60)
        grades: Dict[str, int] = {}
        for p in pairs:
            grades[p.grade] = grades.get(p.grade, 0) + 1
        rep = " · ".join(f"{g} {n}" for g, n in sorted(grades.items())[:6])
        return (f"Dernier scan il y a *{mins} min* — {len(pairs)} paires\n{rep}\n\n"
                f"Le serveur rescanne tout seul toutes les "
                f"{config.SCAN_INTERVAL_SEC // 60} min.")

    if cmd == "top":
        return _list(pairs, "Top du dernier scan")

    if cmd in ("aplus", "a+"):
        return _list([p for p in pairs if p.grade == "A+"], "Setups A+")

    chains = {"solana": "solana", "sol": "solana", "base": "base",
              "eth": "ethereum", "ethereum": "ethereum", "robinhood": "robinhood",
              "hood": "robinhood"}
    if cmd in chains:
        c = chains[cmd]
        lab = config.CHAIN_META.get(c, {}).get("label", c.title())
        return _list([p for p in pairs if (p.chain or "solana") == c], lab)

    if cmd == "wallets":
        from . import wallet_store
        rows = wallet_store.ranked(min_coins=2)[:10]
        if not rows:
            return "Aucun smart wallet retenu pour l'instant."
        out = []
        for w in rows:
            recent = ", ".join(tg._esc(c.get("symbol") or c.get("name"))
                               for c in w.get("recent", [])[:3])
            out.append(f"`{w['grade']}` {w['short']} · {w['count']} pumps · "
                       f"+{w['avg_pump']}% moyen\n  {recent}")
        return "*Smart wallets*\n\n" + "\n\n".join(out)

    if cmd == "coin":
        if not arg:
            return "Donne une adresse : `/coin <adresse du token>`"
        from . import checker
        p, _f = checker.check(arg, with_flow=False)
        if not p:
            return "Token introuvable."
        return tg.format_alert(p)

    if cmd == "scan":
        if not rescan:
            return "Le scan a la demande n'est pas disponible ici."
        return "Scan lance — je t'envoie les A+ des qu'il est fini (2 a 5 min)."

    return HELP


def poll_once(pairs_getter: Callable, rescan: Callable = None,
              last_scan_getter: Callable = None, timeout: int = 25) -> int:
    """
    Un tour de long-polling. Retourne le nombre de messages traites.
    A appeler en boucle depuis un thread dedie.
    """
    token = tg._token()
    if not token:
        return 0
    try:
        r = requests.get(API.format(token=token, method="getUpdates"),
                         params={"offset": _offset[0], "timeout": timeout},
                         timeout=timeout + 10)
        updates = r.json().get("result", [])
    except Exception:
        return 0

    n = 0
    for u in updates:
        _offset[0] = max(_offset[0], u.get("update_id", 0) + 1)
        msg = u.get("message") or u.get("channel_post") or {}
        text = msg.get("text") or ""
        if not text.startswith("/"):
            continue
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if chat_id and chat_id != tg._chat():
            continue                      # on ne repond qu'a son proprietaire
        try:
            reply = handle(text, pairs_getter() or [], rescan,
                           last_scan_getter() if last_scan_getter else 0)
            tg.send(reply)
            n += 1
            # /scan est lance apres avoir accuse reception
            if text.lower().lstrip("/").startswith("scan") and rescan:
                rescan()
        except Exception as e:
            print(f"[bot] {e}")
    return n
