"""Resout les meilleurs traders FOMO en wallets et les ajoute aux adresses suivies."""
import sys, time, requests
try: sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception: pass
import config
from mmscanner import fomoscan, followed as F

H = {"Accept": "application/json", "Authorization": "Bearer " + config.FOMOSCAN_API_KEY}
have = {a for a, _ in F.load_followed()}
rows, seen_handles = [], set()

for win in ["7d", "24h", "30d"]:
    r = requests.get("https://api.fomoscan.sh/v2/leaderboard/traders",
                     params={"window": win}, headers=H, timeout=25)
    es = [e for e in r.json().get("entries", []) if e.get("handle")]
    todo = [e for e in es if e["handle"] not in seen_handles]
    print(f"--- {win} : {len(todo)} nouveaux handles a resoudre ---")
    added = 0
    for e in todo:
        seen_handles.add(e["handle"])
        res = fomoscan.resolve_handle(e["handle"])
        if res.get("ok") and res["solana"] not in have:
            pnl = (e.get("pnl") or 0) / 1000
            rows.append((res["solana"], f"{e['handle']} (top{e['rank']} {win} {pnl:+.0f}k)"))
            have.add(res["solana"]); added += 1
        time.sleep(1.05)          # ~57 req/min, sous la limite de 60
    print(f"    +{added} wallets")
    F.append_followed(rows); rows = []      # on sauvegarde au fur et a mesure

print(f"\nTOTAL SUIVI : {len(F.load_followed())} wallets")
