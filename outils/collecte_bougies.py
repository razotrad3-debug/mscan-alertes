"""Collecte ponctuelle de bougies GeckoTerminal pour l'analyse des coins notifies."""
import json, os, sys, time, requests
part, total = int(sys.argv[1]), int(sys.argv[2])
cibles = json.load(open("cibles.json"))
S = requests.Session(); S.headers.update({"Accept": "application/json;version=20230302"})
ecart = [2.2]
def appel(url, p):
    for essai in range(7):
        time.sleep(ecart[0])
        try:
            r = S.get(url, params=p, timeout=30)
        except Exception:
            time.sleep(5); continue
        if r.status_code == 429:
            ecart[0] = min(7.0, ecart[0] + 0.5); time.sleep(15 + 8 * essai); continue
        if r.status_code == 404: return None
        if r.status_code != 200: time.sleep(4); continue
        ecart[0] = max(2.0, ecart[0] - 0.03)
        return r.json()
    return None
def ohlcv(net, pool, tf, agg, limit, before=None, token=None):
    p = {"aggregate": agg, "limit": limit, "currency": "usd"}
    if before: p["before_timestamp"] = int(before)
    if token: p["token"] = token
    d = appel(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pool}/ohlcv/{tf}", p)
    if d is None: return None
    return ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
os.makedirs("bougies", exist_ok=True)
now = time.time(); n = 0
for i, c in enumerate(cibles):
    if i % total != part: continue
    mint, net, t0 = c["mint"], c["net"], c["t0"]
    res = {"mint": mint, "t0": t0}
    pools = list(c.get("pools") or [])
    for tour in range(2):
        for pool in pools:
            h = ohlcv(net, pool, "hour", 1, 1000, token=mint)
            if not h: continue
            if min(x[0] for x in h) > t0 - 96 * 3600 and len(h) >= 990:
                h += ohlcv(net, pool, "hour", 1, 1000, before=min(x[0] for x in h), token=mint) or []
            res["pool"] = pool; res["heure"] = h
            res["m5"] = ohlcv(net, pool, "minute", 5, 1000, before=min(now, t0 + 24 * 3600), token=mint) or []
            res["jour"] = ohlcv(net, pool, "day", 1, 1000, token=mint) or []
            break
        if "pool" in res or tour == 1: break
        # aucune pool connue ne repond : on demande a GeckoTerminal celles du jeton
        d = appel(f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{mint}/pools", {})
        pools = [p["attributes"]["address"] for p in ((d or {}).get("data") or [])[:3]
                 if p["attributes"]["address"] not in pools]
        if not pools: break
    if "pool" not in res: res["erreur"] = "aucune bougie"
    json.dump(res, open(os.path.join("bougies", mint + ".json"), "w")); n += 1
    if n % 10 == 0: print(n, "coins, ecart", round(ecart[0], 1), flush=True)
print("fini", n, flush=True)
