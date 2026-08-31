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
STATE_FILE = config.path("telegram_sent.json")
ALERT_COOLDOWN_H = 12
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
        return True
    except Exception as e:
        print(f"[telegram] {e}")
        return False


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
    intel = getattr(p, "intel", {}) or {}

    lines = [
        f"🎯 *{_esc(p.symbol)}* — {_esc(p.grade)}  ({p.score}/{p.max_score})",
        f"_{_esc(p.name)}_ · {label} · phase {_esc(p.phase)}",
        "",
        f"MC `{_usd(p.market_cap)}`  ·  Liq `{_usd(p.liquidity_usd)}`",
        f"Vol 24h `{_usd(p.vol_h24)}`  ·  24h `{p.chg_h24:+.1f}%`",
    ]
    if getattr(p, "rsi_note", ""):
        lines.append(f"RSI {_esc(p.rsi_note)}")
    if getattr(p, "smart_holders", 0):
        srcs = ", ".join(_esc(s.get("name")) for s in (getattr(p, "sources", []) or [])[:3])
        lines.append(f"👛 {p.smart_holders} wallets suivis{(' — ' + srcs) if srcs else ''}")

    entry, t1, cut = intel.get("entry"), intel.get("t1"), intel.get("cut")
    if entry or t1 or cut:
        lines.append("")
        if entry:
            lines.append(f"Entry `{entry}`")
        if t1:
            lines.append(f"T1 `{t1}`" + (f"  ·  T2 `{intel['t2']}`" if intel.get("t2") else ""))
        if cut:
            lines.append(f"Cut `{cut}`")

    lines += ["", f"`{p.mint}`", f"[DexScreener]({p.dex_url}) · [GMGN]({p.gmgn_url})"]
    return "\n".join(lines)


def notify_new(pairs: List, grades=ALERT_GRADES) -> int:
    """
    Alerte les paires du grade voulu pas encore signalees.
    Retourne le nombre de messages envoyes.
    """
    if not enabled():
        return 0
    sent = _load()
    now = time.time()
    cutoff = now - ALERT_COOLDOWN_H * 3600
    n = 0
    for p in pairs:
        if p.grade not in grades:
            continue
        prev = sent.get(p.mint)
        # deja alerte recemment au meme grade (ou mieux) -> on passe
        if prev and prev.get("at", 0) > cutoff and prev.get("grade") == p.grade:
            continue
        if send(format_alert(p)):
            sent[p.mint] = {"at": now, "grade": p.grade, "symbol": p.symbol}
            n += 1
            time.sleep(0.4)          # limite Telegram : ~30 msg/s, on reste large
    # purge des entrees de plus de 7 jours
    old = now - 7 * 86400
    for k in [k for k, v in sent.items() if v.get("at", 0) < old]:
        sent.pop(k, None)
    _save(sent)
    if n:
        print(f"[telegram] {n} alerte(s) A+ envoyee(s)")
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
