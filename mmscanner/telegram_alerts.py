"""
Alertes Telegram pour les setups A+.

Deux usages :
  · depuis l'app de bureau (scan_loop) — alertes quand MSCAN tourne ;
  · depuis `bot_server.py` sur un serveur — alertes 24/7, PC eteint.

Anti-doublon : un coin deja alerte n'est pas renvoye avant ALERT_COOLDOWN_H,
sauf s'il change de grade (A -> A+ = nouvelle information).
"""
import json
import os
import time
from typing import List

import requests

import config

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram ne colore pas le texte : la pastille porte la couleur de la chaine.
PASTILLE = {
    "solana":    "🟣",   # violet
    "robinhood": "🟢",   # vert fluo
    "ethereum":  "🔵",   # bleu
    "base":      "🟦",   # bleu clair (carre, pour le distinguer d'ETH)
}
STATE_FILE = config.path("telegram_sent.json")
ALERT_COOLDOWN_H = 12          # un meme coin n'est pas realerte avant ce delai
def _grades_alerte():
    """
    Grades qui declenchent une alerte, derives de config.ALERT_MIN_GRADE.
    Un seul reglage pour toute l'application : ce qui est alerte est
    exactement ce que le filtre du Radar montre.
    """
    plancher = config.grade_rank(getattr(config, "ALERT_MIN_GRADE", "A+"))
    return tuple(g for g in ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D")
                 if config.grade_rank(g) >= plancher)


ALERT_GRADES = _grades_alerte()


def _token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def _chat() -> str:
    return (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def enabled() -> bool:
    return bool(_token() and _chat())


def alerts_enabled() -> bool:
    """
    Est-ce a CE processus d'envoyer les alertes ?

    Le scan tourne a deux endroits : l'application de bureau et le serveur
    cloud. Si les deux alertent, chacun avec son propre fichier de memoire,
    le meme coin part deux fois. Le cloud tourne en continu, c'est donc lui
    l'emetteur ; l'application de bureau se tait, sauf si on lui demande
    explicitement le contraire (MSCAN_SEND_ALERTS=1).
    """
    v = (os.getenv("MSCAN_SEND_ALERTS") or "").strip().lower()
    if v in ("1", "true", "oui", "yes"):
        return True
    if v in ("0", "false", "non", "no"):
        return False
    # par defaut : seul le runner sans interface alerte
    return bool(os.getenv("MSCAN_HEADLESS"))


def _load() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


SENT_IDS_FILE = config.path("telegram_msgids.json")
MAX_IDS = 400          # au-dela on oublie les plus anciens : ils sont de toute
                       # facon trop vieux pour etre supprimables (limite 48 h)


def _ids() -> list:
    try:
        with open(SENT_IDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _garder_id(mid: int) -> None:
    """Retient l'identifiant et son jour, pour pouvoir effacer plus tard."""
    ids = _ids()
    ids.append({"id": mid, "jour": time.strftime("%Y-%m-%d")})
    try:
        with open(SENT_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(ids[-MAX_IDS:], f)
    except Exception:
        pass


def send(text: str, preview: bool = False) -> bool:
    """Envoie un message Markdown. Retourne True si Telegram a accepte."""
    if not enabled():
        return False
    try:
        r = requests.post(
            API.format(token=_token(), method="sendMessage"),
            json={"chat_id": _chat(), "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": not preview},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[telegram] {r.status_code} {r.text[:160]}")
            return False
        try:
            _garder_id(r.json()["result"]["message_id"])
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[telegram] {e}")
        return False


def clear_chat() -> dict:
    """
    Efface les messages envoyes par le bot.

    Telegram ne laisse un bot supprimer que SES propres messages, et
    seulement ceux de moins de 48 h : tes commandes et les vieilles alertes
    restent. On rapporte donc precisement ce qui a pu partir.
    """
    if not enabled():
        return {"efface": 0, "trop_vieux": 0, "restants": 0}
    ids = _ids()
    efface = vieux = 0
    restants = []
    for e in ids:
        mid = e["id"] if isinstance(e, dict) else e     # ancien format : entier nu
        try:
            r = requests.post(API.format(token=_token(), method="deleteMessage"),
                              json={"chat_id": _chat(), "message_id": mid}, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                efface += 1
            else:
                vieux += 1        # trop ancien, ou deja supprime a la main
        except Exception:
            restants.append(e)
        time.sleep(0.05)
    try:
        with open(SENT_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(restants, f)
    except Exception:
        pass
    return {"efface": efface, "trop_vieux": vieux, "restants": len(restants)}



def _usd(v) -> str:
    """$1.24M / $88K / $420 — meme lecture que dans l'app."""
    try:
        v = float(v or 0)
    except Exception:
        return str(v)
    sign, v = ("-" if v < 0 else ""), abs(v)
    if v >= 1_000_000:
        return f"{sign}${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v/1_000:.0f}K"
    return f"{sign}${v:.0f}"


def _esc(s) -> str:
    """Neutralise les caracteres Markdown dans les noms de tokens."""
    return str(s or "").replace("*", "").replace("_", "").replace("`", "").replace("[", "(").replace("]", ")")


def format_alert(p) -> str:
    chain = (getattr(p, "chain", "") or "solana").lower()
    label = config.CHAIN_META.get(chain, {}).get("label", chain.title())
    pastille = PASTILLE.get(chain, "⚪")
    intel = getattr(p, "intel", {}) or {}

    # un symbole vide donnerait "**", ce qui casse le Markdown de Telegram
    titre = _esc(p.symbol) or _esc(p.name) or "?"

    lines = [
        f"{pastille} *{titre}* — {_esc(p.grade)}  ({p.score}/{p.max_score})",
        f"{label} · Phase {_esc(p.phase)}",
        "",
        f"– Market Cap : `{_usd(p.market_cap)}`",
    ]
    if getattr(p, "smart_holders", 0):
        srcs = ", ".join(_esc(s.get("name")) for s in (getattr(p, "sources", []) or [])[:3])
        lines.append(f"👛 {p.smart_holders} insiders{(' — ' + srcs) if srcs else ''}")

    zone, cut_mc = intel.get("zone"), intel.get("cut_mc")
    bloc = []
    if zone and zone != "—":
        bloc.append(f"– Entry `{_esc(zone)}`  ·  Cut `{_esc(cut_mc)}`")
    elif cut_mc:
        bloc.append(f"– Cut `{_esc(cut_mc)}`")
    if intel.get("t1"):
        mc = p.market_cap or 0

        def _tp(v):
            """Objectif en market cap, avec le gain que ca represente depuis ici."""
            if not v:
                return "—"
            if mc > 0:
                return f"`{_usd(v)}` (+{(v / mc - 1) * 100:.0f}%)"
            return f"`{_usd(v)}`"

        bloc.append(f"– TP1 : {_tp(intel['t1'])}  ·  TP2 {_tp(intel.get('t2'))}"
                    f"  ·  TP3 {_tp(intel.get('t3'))}")
    if bloc:
        lines += [""] + bloc

    # le raisonnement en dernier : on lit les chiffres d'abord, l'explication ensuite
    fin_txt = [t for t in (intel.get("action"), intel.get("pourquoi")) if t]
    if fin_txt:
        lines += [""] + [_esc(t) for t in fin_txt]

    # pas d'adresse de contrat en clair : les deux liens y menent deja
    lines += ["", f"[DexScreener]({p.dex_url}) · [GMGN]({p.gmgn_url})"]
    return "\n".join(lines)



def notify_new(pairs: List, grades=None) -> int:
    """
    Alerte les paires du grade voulu, au plus une fois toutes les 12 h.

    Un coin deja signale dans la fenetre ne repasse que s'il monte en A+ :
    c'est une information neuve, tout le reste serait du bruit.

    L'emetteur est unique (voir `alerts_enabled`) : deux scanners qui
    alerteraient chacun avec sa propre memoire enverraient tout en double.
    """
    if not enabled() or not alerts_enabled():
        return 0
    grades = grades or ALERT_GRADES
    sent = _load()
    now = time.time()
    aujourdhui = time.strftime("%Y-%m-%d", time.localtime(now))
    fenetre = now - ALERT_COOLDOWN_H * 3600
    n = supprimees = 0

    # premier passage de la journee : on efface les alertes de la veille pour
    # que la conversation ne contienne que ce qui est encore d'actualite.
    if sent.get("_jour") != aujourdhui:
        try:
            purge_veille()
        except Exception as e:
            print(f"[telegram] purge : {e}")
        sent["_jour"] = aujourdhui

    for p in pairs:
        if p.grade not in grades:
            continue
        prev = sent.get(p.mint)
        if not isinstance(prev, dict):
            prev = {}

        recent = prev.get("at", 0) > fenetre
        if recent:
            monte_en_ap = p.grade == "A+" and prev.get("grade") != "A+"
            if not monte_en_ap:
                supprimees += 1
                continue          # deja vu dans les 12 h, et rien de neuf

        if send(format_alert(p)):
            sent[p.mint] = {"at": now, "jour": aujourdhui,
                            "grade": p.grade, "symbol": p.symbol}
            n += 1
            time.sleep(0.4)       # limite Telegram : ~30 msg/s, on reste large

    # purge des entrees de plus de 7 jours
    vieux = now - 7 * 86400
    for k in [k for k, v in sent.items()
              if isinstance(v, dict) and v.get("at", 0) < vieux]:
        sent.pop(k, None)
    _save(sent)
    if n or supprimees:
        print(f"[telegram] {n} envoyee(s), {supprimees} deja vue(s) dans "
              f"les {ALERT_COOLDOWN_H} h")
    return n



def test() -> bool:
    """Message de verification de la configuration."""
    return send("✅ *MSCAN* connecte.\nTu recevras ici les setups *A+* des leur detection.")


def resolve_chat_id() -> str:
    """
    Retrouve le chat_id via getUpdates : l'utilisateur envoie /start au bot,
    on lit la mise a jour. Evite d'avoir a le chercher a la main.
    """
    if not _token():
        return ""
    try:
        r = requests.get(API.format(token=_token(), method="getUpdates"), timeout=15)
        for u in reversed(r.json().get("result", [])):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat") or {}
            if chat.get("id"):
                return str(chat["id"])
    except Exception as e:
        print(f"[telegram] {e}")
    return ""


def save_config(token: str = None, chat_id: str = None) -> dict:
    """
    Ecrit le token et le chat_id dans .env et les active immediatement.
    Retourne l'etat apres ecriture.
    """
    env = config.path(".env")
    try:
        lignes = open(env, "r", encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        lignes = []

    def _set(cle, val):
        nonlocal lignes
        if val is None:
            return
        lignes = [l for l in lignes if not l.startswith(cle + "=")]
        if val:
            lignes.append(f"{cle}={val}")
        os.environ[cle] = val or ""

    _set("TELEGRAM_BOT_TOKEN", (token or "").strip() or None)
    _set("TELEGRAM_CHAT_ID", (chat_id or "").strip() or None)
    try:
        open(env, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
    except Exception as e:
        return {"ok": False, "erreur": str(e)[:120]}
    return status()


def status() -> dict:
    """Etat lisible de la configuration Telegram, pour l'interface."""
    return {
        "ok": enabled(),
        "token": bool(_token()),
        "chat": _chat(),
        "grades": list(ALERT_GRADES),
        "cooldown_h": ALERT_COOLDOWN_H,
    }


def purge_veille() -> int:
    """
    Efface les messages des jours precedents, en gardant ceux d'aujourd'hui.

    Appelee au premier scan de chaque journee : la conversation ne contient
    donc jamais que les alertes du jour, sans rien avoir a faire a la main.
    Ce qui depasse 48 h ne peut plus etre supprime — Telegram l'interdit aux
    bots — donc on l'oublie simplement.
    """
    if not enabled():
        return 0
    aujourdhui = time.strftime("%Y-%m-%d")
    ids = _ids()
    garde, efface = [], 0
    for e in ids:
        if not isinstance(e, dict):
            continue                       # ancien format sans date : on abandonne
        if e.get("jour") == aujourdhui:
            garde.append(e)
            continue
        try:
            r = requests.post(API.format(token=_token(), method="deleteMessage"),
                              json={"chat_id": _chat(), "message_id": e["id"]}, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                efface += 1
        except Exception:
            pass
        time.sleep(0.05)
    try:
        with open(SENT_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(garde, f)
    except Exception:
        pass
    if efface:
        print(f"[telegram] {efface} alerte(s) de la veille effacee(s)")
    return efface
