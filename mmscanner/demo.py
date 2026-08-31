"""Jeu de données démo (issu d'exemples réels prntwrx) pour voir le dashboard
sans clé ni réseau :  python run.py --demo"""
import time
from .model import Pair
from .phases import detect_phase, build_intel
from .scoring import score_pair

_SAMPLE = [
    # name, symbol, mint, mc, vol24, vol1h, vol5m, m5, h1, h6, h24, buys, sells, age, rsi15, rsi1h, top1, top10, smart, names
    ("Mario64", "MARIO64", "CX2v7JSHJQDcNooubzzvZG8TPaDwbaPgfzcXRSWJpump", 623_000, 1_460_000, 210_000, 9_000, -8.8, 16.2, 37.8, -14.3, 457, 444, 52.8, 62, 58, 0.06, 0.28, 3, ["Insider #1", "KOL-nonpub", "VM-hunter"]),
    ("Baby Shib", "BABYSHIB", "5nZMRLSFnA3oWXXswKyyaW5or2FFy34tkTUhtkWPpump", 528_000, 1_420_000, 180_000, 6_000, 3.6, 6.8, -2.8, 175.0, 340, 361, 26.4, 55, 61, 0.09, 0.34, 2, ["Insider #1", "Early-ape"]),
    ("The Toad Pepe", "TOAD", "A13oRB9FFaiUjfi6LdCg6p9ka1u8SfGkUFs4SKvPpump", 9_310_000, 3_720_000, 240_000, 5_000, -0.6, 7.5, 7.7, 2.3, 623, 603, 120.0, 48, 66, 0.11, 0.38, 1, ["VM-hunter"]),
    ("Chibi Neko", "Chibi", "5BQpi43RtPxsw7jw3dpeE7duQAXcNYhw9MD6KUxGpump", 274_000, 535_000, 80_000, 12_000, -7.3, 16.4, -16.1, 810.0, 177, 162, 18.7, 71, 59, 0.13, 0.41, 0, []),
    ("Dumb Phone Gang", "DPG", "LFEJTxJ9yi6ojGDFpjbGfABLbH55Fc3oEK8syJJpump", 994_000, 1_020_000, 130_000, 8_000, -0.3, 21.1, 18.2, 75.1, 99, 92, 24.0, 64, 63, 0.10, 0.33, 2, ["Insider #1", "Early-ape"]),
    ("The Red Plunger", "PLANSEM", "j8RdRQ8tQRbx62cr46e5LM8ekRDZr1opqmbgsobpump", 550_000, 8_340_000, 300_000, 14_000, 13.7, 10.6, -32.1, 1681.0, 2362, 1905, 8.9, 66, 60, 0.08, 0.29, 4, ["Insider #1", "VM-hunter", "KOL-nonpub", "Early-ape"]),
    ("Qenis", "Qenis", "EkcTa8n14fXcHdfvZqCg72cTCutJnnKb19vcHwKTpump", 180_000, 1_080_000, 95_000, 7_000, -6.4, 0.6, -16.6, 495.0, 451, 348, 13.4, 45, 52, 0.14, 0.39, 1, ["Early-ape"]),
    ("Plumber", "Plumber", "GCa9TZMK9Q3VUSkhZgX76YAQBjqQd1dPxkBnZojFpump", 630_000, 3_860_000, 120_000, 4_000, 8.5, 1.8, -40.1, -77.3, 673, 543, 57.6, 38, 44, 0.22, 0.55, 0, []),
]


_WALLET_HISTORY = {
    "Insider #1": ("7Ln4Qk2mVxRtPqB9sWdE6hYuJ3aZcXvNbMkGfTrHsPq1", [
        ("CATANA", "CATANA", 4180, 12_400_000, 3),
        ("Mario64", "MARIO64", 1240, 3_740_000, 7),
        ("PLANSEM", "The Red Plunger", 1681, 2_480_000, 11),
        ("CLAWD", "CLAWD", 860, 7_100_000, 5),
    ]),
    "VM-hunter": ("9pQr3TzWmKdX7vBnHs2LcYaEjU5oFgRtNiPbMwQx4Zke", [
        ("TROLL", "TROLL", 2390, 21_000_000, 2),
        ("Mario64", "MARIO64", 1240, 3_740_000, 14),
        ("NEET", "NEET", 640, 4_900_000, 9),
    ]),
    "KOL-nonpub": ("4Hs8VbNmQrT2yWxZcE5kLpJ9aUdFgRt3oBvMnKiPq7Xw", [
        ("PENGUIN", "PENGUIN", 5600, 31_000_000, 1),
        ("ZAZU", "ZAZU", 980, 2_200_000, 4),
        ("PLANSEM", "The Red Plunger", 1681, 2_480_000, 6),
    ]),
    "Early-ape": ("2Wc9WdKpLmN4rXvBqZt7yHsEjA3oUgFiRb5nMkTxQ8Pe", [
        ("BWOATS", "BWOATS", 720, 1_100_000, 8),
        ("ELON", "ELON", 1150, 5_600_000, 12),
    ]),
}


def _wallet_detail(label):
    addr, rows = _WALLET_HISTORY.get(label, ("", []))
    return {
        "address": addr,
        "short": (addr[:4] + "…" + addr[-4:]) if addr else "?",
        "label": label,
        "count": len(rows),
        "coins": [{"symbol": s, "name": n, "pump_pct": p, "mc": m,
                   "entry_rank": r, "mint": ""} for (s, n, p, m, r) in rows],
    }


def demo_wallets():
    """Pour l'onglet WALLETS : classement des smart wallets + leur historique."""
    out = []
    for label, (addr, rows) in _WALLET_HISTORY.items():
        coins = [{"symbol": s, "name": n, "pump_pct": p, "mc": m,
                  "entry_rank": r, "mint": ""} for (s, n, p, m, r) in rows]
        coins.sort(key=lambda c: c["pump_pct"], reverse=True)
        out.append({
            "address": addr, "short": addr[:4] + "…" + addr[-4:], "label": label,
            "coins": coins, "count": len(coins), "best": coins[0],
            "avg_pump": round(sum(c["pump_pct"] for c in coins) / len(coins)),
            "avg_rank": round(sum(c["entry_rank"] for c in coins) / len(coins)),
            "last_seen": time.time() - 3600,
        })
    out.sort(key=lambda w: (w["count"], w["avg_pump"]), reverse=True)
    return out


def demo_pairs():
    pairs = []
    for (name, sym, mint, mc, v24, v1, v5, m5, h1, h6, h24, b, s, age, r15, r1, t1p, t10p, smart, names) in _SAMPLE:
        p = Pair(chain="solana", name=name, symbol=sym, mint=mint, pair_address=mint,
                 price_usd=mc / 1_000_000_000, market_cap=mc, fdv=mc, liquidity_usd=max(60_000, v24 * 0.05),
                 vol_h24=v24, vol_h6=v24 * 0.4, vol_h1=v1, vol_m5=v5,
                 chg_m5=m5, chg_h1=h1, chg_h6=h6, chg_h24=h24, buys_h1=b, sells_h1=s, age_hours=age)
        # swing simulé pour les fibs (jambe récente ~ +move 1h)
        p.swing_high = p.price_usd
        p.swing_low = p.price_usd / (1 + max(h1, 5) / 100.0 + 0.25)
        p.rsi_15m, p.rsi_1h = r15, r1
        p.rsi_4h = round((r15 + r1) / 2, 1)
        p.rsi_note = f"15m {r15} · 1h {r1} · 4h {p.rsi_4h}"
        p.buys_m5, p.sells_m5 = int(b*0.08), int(s*0.09)
        p.buys_h6, p.sells_h6 = int(b*4.2), int(s*3.6)
        p.buys_h24, p.sells_h24 = int(b*12), int(s*12.5)
        p.holders = 400 + int(mc/2500)
        p.top_holder_pct, p.top10_pct = t1p, t10p
        p.smart_holders, p.smart_names = smart, names
        p.smart_detail = [_wallet_detail(n) for n in names]
        # origine de chaque detenteur : 2 premiers = tes KOL suivis, le reste on-chain
        for k, d in enumerate(p.smart_detail):
            d["origin"] = "suivi" if k < 2 else "onchain"
        kols = [d["label"] for d in p.smart_detail if d["origin"] == "suivi"]
        oc = sum(1 for d in p.smart_detail if d["origin"] == "onchain")
        p.sources = [{"kind": "suivi", "name": n} for n in kols]
        if oc:
            p.sources.append({"kind": "onchain",
                              "name": f"{oc} wallet{'s' if oc > 1 else ''} on-chain"})
        p.smart_accumulating = max(0, smart - 1)
        p.wallets_available = True
        p.phase = detect_phase(p)
        p.intel = build_intel(p)
        score_pair(p)
        p.updated_at = time.time()
        pairs.append(p)
    import config
    pairs.sort(key=lambda x: (config.grade_rank(x.grade), x.score, x.vol_h24), reverse=True)
    return pairs


def demo_flow():
    """Whale flow d'exemple — structure identique à holder_flow.compute."""
    return {
        "available": True, "snapshots": 14, "price": 0.00062,
        "holders": 3892, "holders_delta": 137, "since": 9 * 86400, "supply": 1e9,
        "labels": {"Whale": "≥ 1% supply", "Shark": "≥ 0.25%",
                   "Dolphin": "≥ 0.05%", "Fish": "< 0.05%"},
        "covered": {"24h": True, "7d": True, "30d": False},
        "tiers": {
            "Whale":   {"24h":  62_000, "7d":  180_000, "30d": 0.0, "count": 6},
            "Shark":   {"24h":  18_500, "7d":   41_000, "30d": 0.0, "count": 23},
            "Dolphin": {"24h":  -4_200, "7d":   12_000, "30d": 0.0, "count": 71},
            "Fish":    {"24h":   1_900, "7d":   -6_500, "30d": 0.0, "count": 3792},
        },
        "totals": {"24h": 78_200, "7d": 226_500, "30d": 0.0},
        "signal": "Accumulation whales — l'argent fort entre.",
    }


def demo_check():
    """Coin d'exemple pour le checker (barre de recherche)."""
    p = demo_pairs()[0]  # Mario64 A+
    return p, demo_flow()


_FOLLOWED = [
    ("Cupsey",      "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", [
        ("Mario64", "MARIO64", "CX2v7JSHJQDcNooubzzvZG8TPaDwbaPgfzcXRSWJpump", 623_000, 16.2, 1240, 1.46e6, 0.4),
        ("The Red Plunger", "PLANSEM", "j8RdRQ8tQRbx62cr46e5LM8ekRDZr1opqmbgsobpump", 550_000, 10.6, 1681, 8.34e6, 2.1),
        ("Qenis", "QENIS", "EkcTa8n14fXcHdfvZqCg72cTCutJnnKb19vcHwKTpump", 180_000, 0.6, 495, 1.08e6, 5.5),
    ]),
    ("Euris",       "7phbaH6UeyFJmjdPoyiaQAHX3B1gRtnCVZL7HZNtbonk", [
        ("Mario64", "MARIO64", "CX2v7JSHJQDcNooubzzvZG8TPaDwbaPgfzcXRSWJpump", 623_000, 16.2, 1240, 1.46e6, 1.2),
        ("Dumb Phone Gang", "DPG", "LFEJTxJ9yi6ojGDFpjbGfABLbH55Fc3oEK8syJJpump", 994_000, 21.1, 75, 1.02e6, 3.8),
    ]),
    ("KOL non-public", "4Hs8VbNmQrT2yWxZcE5kLpJ9aUdFgRt3oBvMnKiPq7Xw", [
        ("The Red Plunger", "PLANSEM", "j8RdRQ8tQRbx62cr46e5LM8ekRDZr1opqmbgsobpump", 550_000, 10.6, 1681, 8.34e6, 0.8),
        ("Chibi Neko", "CHIBI", "5BQpi43RtPxsw7jw3dpeE7duQAXcNYhw9MD6KUxGpump", 274_000, 16.4, 810, 535_000, 6.9),
    ]),
    ("Trench sniper", "2Wc9WdKpLmN4rXvBqZt7yHsEjA3oUgFiRb5nMkTxQ8Pe", [
        ("Baby Shib", "BABYSHIB", "5nZMRLSFnA3oWXXswKyyaW5or2FFy34tkTUhtkWPpump", 528_000, 6.8, 175, 1.42e6, 11.4),
    ]),
]


def demo_followed():
    """Onglet ADRESSES : achats récents des adresses suivies + agrégat par coin."""
    now = time.time()
    wallets, agg = [], {}
    for label, addr, rows in _FOLLOWED:
        buys = []
        for (name, sym, mint, mc, h1, h24, vol, hours_ago) in rows:
            row = {"mint": mint, "ts": now - hours_ago * 3600, "amount": 0,
                   "name": name, "symbol": sym, "mc": mc, "chg_h1": h1,
                   "chg_h24": h24, "vol_h24": vol, "pair": mint}
            buys.append(row)
            a = agg.setdefault(mint, {**row, "by": [], "ts": row["ts"]})
            a["by"].append(label)
            a["ts"] = max(a["ts"], row["ts"])
        wallets.append({"address": addr, "label": label,
                        "short": addr[:4] + "…" + addr[-4:], "buys": buys})
    coins = sorted(agg.values(), key=lambda c: (len(c["by"]), c["ts"]), reverse=True)
    return {"wallets": wallets, "coins": coins, "available": True, "empty": False}
