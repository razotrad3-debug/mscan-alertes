"""Alertes Telegram façon prntwrx : n'alerte que sur une grade >= seuil,
et seulement quand une paire APPARAÎT ou est UPGRADÉE (pas de spam)."""
import json
import os
import requests

import config
from .model import Pair
from .phases import _fmt

_STATE = {}


def _load():
    global _STATE
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                _STATE = json.load(f)
        except Exception:
            _STATE = {}


def _save():
    try:
        with open(config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_STATE, f)
    except Exception:
        pass


def _send(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        return True
    except Exception:
        return False


def format_alert(p: Pair, tag: str) -> str:
    arrow = "🟢" if p.chg_h1 >= 0 else "🔴"
    intel = p.intel
    wl = ""
    if p.wallets_available and p.smart_holders:
        wl = f"\n🐋 Smart-money : {p.smart_holders} wallet(s) — {', '.join(p.smart_names[:3])}"
    return (
        f"<b>{tag} — {p.grade}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{arrow} <b>{p.name}</b> [{_fmt(p.market_cap)} / {p.chg_h1:+.1f}%] — {p.symbol}/SOL\n\n"
        f"Chain: SOL · Age: {p.age_hours:.1f}h\n"
        f"Vol 24H: {_fmt(p.vol_h24)} · Vol 1H: {_fmt(p.vol_h1)}\n"
        f"5m {p.chg_m5:+.1f}% · 1H {p.chg_h1:+.1f}% · 6H {p.chg_h6:+.1f}% · 24H {p.chg_h24:+.1f}%\n"
        f"RSI: {p.rsi_note or 'n/a'}\n"
        f"<b>Score: {p.grade} ({p.score}/{p.max_score})</b> · Phase: {p.phase}{wl}\n\n"
        f"⤷ Entry: {intel['entry']}\n"
        f"⤷ {intel['targets']}\n"
        f"✂️ Cut: {intel['cut']}\n\n"
        f"GMGN: {p.gmgn_url}\n"
        f"DEX: {p.dex_url}\n"
        f"<code>{p.mint}</code>"
    )


def push_alerts(pairs, min_grade: str = None):
    """Envoie les alertes pour les nouvelles paires ou les upgrades de grade."""
    _load()
    min_grade = min_grade or config.ALERT_MIN_GRADE
    floor = config.grade_rank(min_grade)
    sent = 0
    for p in pairs:
        if config.grade_rank(p.grade) < floor:
            continue
        prev = _STATE.get(p.mint)
        if prev is None:
            tag = "RADAR"
        elif config.grade_rank(p.grade) > config.grade_rank(prev):
            tag = "⚡ UPGRADED TO LIVE PLAY"
        else:
            continue  # déjà alerté à cette grade ou plus
        if _send(format_alert(p, tag)):
            sent += 1
        _STATE[p.mint] = p.grade
    _save()
    return sent
