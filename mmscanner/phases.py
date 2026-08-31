"""
Détection de phase + génération de l'intel (entry / POI / T1-T3 / cut),
calquées sur la logique du scanner prntwrx et la méthode MikeMike (retest strategy).
"""
from typing import Dict
from .model import Pair
from .indicators import fib_levels


def _fmt(v: float) -> str:
    if v is None:
        return "?"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"


def _target_mults(mc: float):
    """Paliers T1/T2/T3 (en %) selon le market cap — reproduit l'échelle prntwrx."""
    if mc < 250_000:
        return 0.30, 1.60, 3.50
    if mc < 1_000_000:
        return 0.30, 1.60, 5.00
    if mc < 3_000_000:
        return 0.20, 1.20, 2.50
    if mc < 6_000_000:
        return 0.15, 0.80, 1.80
    return 0.10, 0.50, 1.50


def detect_phase(p: Pair) -> str:
    m5, h1, h6, h24 = p.chg_m5, p.chg_h1, p.chg_h6, p.chg_h24
    rsi = p.rsi_1h
    pulled_back = (
        p.swing_high and p.price_usd and p.price_usd < 0.90 * p.swing_high
    )
    # 1) épuisement : dump à fort volume, ou RSI en top qui retombe
    if (h1 <= -8 and p.vol_m5 and p.vol_m5 > 0.02 * (p.vol_h1 or 1)) or (
        rsi is not None and rsi >= 80 and h1 < 0
    ):
        return "Exhausted"
    # 2) running : bougie chaude en cours
    if h1 >= 8:
        return "Running"
    # 3) early : jeune + gros move 24h
    if (p.age_hours is not None and p.age_hours <= 24) and h24 >= 100:
        return "Early"
    # 4) retest : a pullback depuis le top, structure 6h positive
    if pulled_back and h6 >= 0 and -6 <= h1 <= 8:
        return "Retest"
    # 5) compression : faible volatilité récente
    if abs(h1) < 6 and abs(m5) < 6:
        return "Compressing"
    return "Watch"


def build_intel(p: Pair) -> Dict:
    """Retourne {entry, poi, t1, t2, t3, cut, targets:[..]} en texte + valeurs MC."""
    mc = p.market_cap or 0.0
    t1p, t2p, t3p = _target_mults(mc)
    t1, t2, t3 = mc * (1 + t1p), mc * (1 + t2p), mc * (1 + t3p)
    targets_txt = (
        f"T1: {_fmt(t1)} (+{t1p*100:.0f}%)  ·  "
        f"T2: {_fmt(t2)} (+{t2p*100:.0f}%)  ·  "
        f"T3: {_fmt(t3)} (+{t3p*100:.0f}%)"
    )

    ratio = (mc / p.price_usd) if p.price_usd else None
    poi_lo = poi_hi = None
    if ratio and p.swing_low and p.swing_high and p.swing_high > p.swing_low:
        fl = fib_levels(p.swing_low, p.swing_high)
        poi_hi = ratio * fl["0.618"]   # retrace 0.618 (le plus haut en prix)
        poi_lo = ratio * fl["0.786"]   # retrace 0.786 (le plus bas)

    phase = p.phase
    if phase == "Running":
        if poi_lo and poi_hi:
            entry = (f"Ne chase pas la bougie (+{p.chg_h1:.1f}% 1H). Attends le 1er pullback. "
                     f"POI : {_fmt(poi_lo)}–{_fmt(poi_hi)} (retrace 0.618–0.786). "
                     f"Half size au touch de zone, add sur reclaim 15m.")
            cut_val = poi_lo * 0.94
        else:
            entry = (f"Ne chase pas la bougie (+{p.chg_h1:.1f}% 1H). Attends un pullback "
                     f"de 20–30% vers une zone fib avant d'entrer.")
            cut_val = mc * 0.70
        cut = f"{_fmt(cut_val)} — 15m close en dessous = move épuisé, ne prie pas pour un bounce."

    elif phase == "Retest":
        reclaim = mc * 1.08
        entry = (f"MC actuel {_fmt(mc)} = zone de retest. Attends que le sell volume sèche "
                 f"en 15m, puis une bougie verte qui reclaim {_fmt(reclaim)} = ton trigger. "
                 f"Ne front-run pas, laisse le bounce se prouver.")
        cut_val = (p.swing_low * ratio) if (ratio and p.swing_low) else mc * 0.82
        cut = (f"{_fmt(cut_val)} — retest invalide s'il traverse. Une chance : si ça drop ici "
               f"et que le volume disparaît, ça peut rester le low ; si le volume est lourd, cut direct.")

    elif phase == "Compressing":
        brk = (p.swing_high * ratio) if (ratio and p.swing_high) else mc * 1.12
        entry = (f"Pas d'entrée — attends le breakout au-dessus de {_fmt(brk)} sur un 15m close "
                 f"avec 2x le volume moyen. Rate les premiers 10% pour confirmer. "
                 f"Chaser une compression sans confirmation = fakeout.")
        cut_val = mc * 0.90
        cut = f"Si entrée sur breakout : cut sous {_fmt(cut_val)}. Failed breakout = distribution."

    elif phase == "Early":
        lo, hi = mc * 0.93, mc * 1.06
        entry = (f"Zone d'entrée MAINTENANT : {_fmt(lo)}–{_fmt(hi)}. Cherche des bougies 15m "
                 f"qui se resserrent avec du buy volume régulier. Tu achètes la compression "
                 f"avant l'expansion. Half size, scale up sur confirmation.")
        cut_val = mc * 0.80
        cut = f"{_fmt(cut_val)} — perd 20% du MC avant la 1re expansion = ça fade. Hard cut, no averaging down."

    elif phase == "Exhausted":
        entry = ("Pas d'entrée — momentum épuisé / déclin à fort volume. "
                 "Laisse un reset complet et un nouveau floor se former avant de regarder.")
        cut_val = mc * 0.85
        cut = f"Si en position : cut, ne ride pas à zéro (repump sans volume = top confirmé)."

    else:  # Watch
        entry = ("Sur le radar, pas encore de setup. Trace OB/fibs/trendline et attends "
                 "qu'un retest ou un breakout se présente.")
        cut_val = mc * 0.80
        cut = f"{_fmt(cut_val)} — invalide la thèse."

    # ── version courte : ce qu'on lit en 2 secondes ────────────────
    # `action` = le verbe, `zone` = le prix, `pourquoi` = une phrase.
    # Les textes longs restent disponibles pour la page d'analyse.
    if phase == "Running":
        action = "Attendre le pullback"
        zone = f"{_fmt(poi_lo)}–{_fmt(poi_hi)}" if (poi_lo and poi_hi) else f"-20 a -30% du MC"
        pourquoi = f"Deja +{p.chg_h1:.0f}% en 1H — chaser ici, c'est acheter le haut."
    elif phase == "Retest":
        action = "Attendre le reclaim"
        zone = _fmt(mc * 1.08)
        pourquoi = "Le retest doit se prouver : bougie verte au-dessus, sinon ca casse."
    elif phase == "Compressing":
        action = "Attendre le breakout"
        zone = _fmt((p.swing_high * ratio) if (ratio and p.swing_high) else mc * 1.12)
        pourquoi = "Cloture 15m au-dessus avec 2x le volume, sinon c'est un fakeout."
    elif phase == "Early":
        action = "Entrer maintenant"
        zone = f"{_fmt(mc * 0.93)}–{_fmt(mc * 1.06)}"
        pourquoi = "Compression avant expansion — demi-position, on augmente si ca confirme."
    elif phase == "Exhausted":
        action = "Ne pas toucher"
        zone = "—"
        pourquoi = "Momentum epuise : repump sans volume = top confirme."
    else:
        action = "Surveiller"
        zone = "—"
        pourquoi = "Pas encore de setup — on attend un retest ou un breakout."

    return {
        "entry": entry,
        "targets": targets_txt,
        "t1": t1, "t2": t2, "t3": t3,
        "cut": cut,
        "poi_lo": poi_lo, "poi_hi": poi_hi,
        # version courte
        "action": action,
        "zone": zone,
        "cut_mc": _fmt(cut_val),
        "pourquoi": pourquoi,
    }
