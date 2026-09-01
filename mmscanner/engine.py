"""
Orchestration : découverte -> filtres -> enrichissement (metrics/RSI/wallets)
-> phase -> intel -> score -> classement. Gère le rate-limit en n'enrichissant
lourdement que les meilleurs candidats.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

import config
from .model import Pair
from .indicators import rsi, find_swing
from . import sources_gecko as gecko
from . import sources_dex as dex
from . import sources_helius as helius
from . import wallet_store
from .phases import detect_phase, build_intel
from . import safety
from .scoring import score_pair


def _liquidity_ok(liq: float, vol24: float) -> bool:
    """Liquidité suffisante — ou turnover assez fort pour compenser."""
    if liq >= config.MIN_LIQUIDITY_USD:
        return True
    return (liq >= config.MIN_LIQ_EARLY) and (vol24 >= config.MIN_VOL_EARLY)


def is_crypto_native(symbol: str, name: str, mint: str = "", mc: float = None) -> bool:
    """
    Vrai si le token est du crypto-natif jouable.

    Écarte : stables, majors, et tout ce qui trace/imite une action, un indice,
    une matière première ou une entreprise (même lancé sur pumpswap).
    """
    if mint and mint in config.EXCLUDE_MINTS:
        return False
    sym = (symbol or "").strip().upper()
    if sym in config.EXCLUDE_SYMBOLS or sym in config.EXCLUDE_STOCKS:
        return False
    low = (name or "").lower()
    # ticker entre parenthèses : "NVIDIA (NVDA)"
    for t in config.EXCLUDE_STOCKS:
        if f"({t.lower()})" in low:
            return False
    # nom d'entreprise dans le libellé
    for w in config.EXCLUDE_NAME_WORDS:
        if w in low:
            return False
    if mc is not None:
        if not mc or mc <= 0:
            return False
        if not (config.MIN_MCAP <= mc <= config.MAX_MCAP):
            return False
    return True


def _is_memecoin(p) -> bool:
    return is_crypto_native(p.symbol, p.name, p.mint, p.market_cap)


def attach_wallet_detail(p, addresses, names, store=None, registry=None):
    """
    Construit p.smart_detail (POURQUOI ce wallet est smart) et p.sources
    (D'OU vient ce coin : quel KOL suivi, ou detection on-chain).
    """
    store = store if store is not None else wallet_store.load()
    if registry is None:
        try:
            from .followed import tracked_registry
            registry = tracked_registry()
        except Exception:
            registry = {}

    detail, groups = [], {}
    for i, addr in enumerate(addresses or []):
        coins = wallet_store.coins_for(addr, store)
        meta = registry.get(addr, {})
        origin = meta.get("origin", "onchain")
        group = meta.get("group") or ("Suivi" if origin == "suivi" else "on-chain")
        label = meta.get("label") or (names[i] if i < len(names or []) else "")
        detail.append({
            "address": addr,
            "short": addr[:4] + "…" + addr[-4:],
            "label": label,
            "origin": origin,
            "group": group,
            "coins": coins[:5],
            "count": len(coins),
        })
        groups[group] = groups.get(group, 0) + 1

    p.smart_detail = detail
    # on affiche le GROUPE (Dabal, Grand, Top FOMO, on-chain...) et son compte,
    # pas les pseudos : c'est la provenance qui compte sur le radar.
    p.sources = [{"kind": "onchain" if g == "on-chain" else "suivi",
                  "name": f"{g} ×{n}" if n > 1 else g}
                 for g, n in sorted(groups.items(), key=lambda kv: -kv[1])]


def _resample_4h(hourly_closes: List[float]) -> List[float]:
    """Approxime des closes 4h à partir des closes 1h (une bougie sur 4)."""
    if not hourly_closes:
        return []
    return hourly_closes[::4] if len(hourly_closes) >= 8 else hourly_closes


def scan(smart_wallets: List[str], log=print, progress=None, on_scored=None) -> List[Pair]:
    """
    `progress(pct, phase, detail)` est appelee au fil du scan (jauge UI).

    `on_scored(pairs)` est appelee des qu'une paire vient d'etre notee, avec
    le classement partiel. Sans ca, une alerte Telegram pouvait partir deux a
    cinq minutes avant que le coin n'apparaisse dans le Radar : le scan ne
    publiait ses resultats qu'une fois termine.
    """
    def _p(pct, phase, detail=""):
        if progress:
            try:
                progress(pct, phase, detail)
            except Exception:
                pass

    t0 = time.time()
    _p(2, "Découverte des paires")
    raw = gecko.discover(
        pages=config.DISCOVERY_PAGES,
        include_new=config.INCLUDE_NEW_POOLS,
        include_trending=config.INCLUDE_TRENDING,
        dexes=getattr(config, "DISCOVERY_DEXES", None),
        dex_pages=getattr(config, "DEX_PAGES", 2),
    )
    if not raw:
        # quota GeckoTerminal momentanement sature : on souffle et on retente
        log("[discover] vide — pause de 20 s puis nouvelle tentative")
        time.sleep(20)
        raw = gecko.discover(pages=1, include_new=True, include_trending=True,
                             dexes=getattr(config, "DISCOVERY_DEXES", None), dex_pages=1)
    log(f"[discover] {len(raw)} pools bruts  ({time.time()-t0:.0f}s)")
    _p(22, "Filtrage", f"{len(raw)} pools")

    # ── préfiltre (les filtres MikeMike) ─────────────────
    cands = []
    for r in raw:
        if not _liquidity_ok(r["liquidity_usd"], r["vol_h24"]):
            continue
        if r["vol_h24"] < config.MIN_VOL_H24:
            continue
        age = r["age_hours"]
        if age is not None and (age > config.MAX_AGE_HOURS or age < config.MIN_AGE_HOURS):
            continue
        cands.append(r)
    # source complémentaire : endpoints de découverte DexScreener
    if getattr(config, "USE_DEXSCREENER_DISCOVERY", False):
        known = {r["mint"] for r in cands}
        extra = [m for m in dex.discover_mints() if m not in known]
        for m in extra:
            cands.append({"mint": m, "gecko_pool": None, "name": "?", "price_usd": 0.0,
                          "liquidity_usd": 0.0, "market_cap": 0.0, "fdv": 0.0,
                          "vol_h24": 0.0, "chg_h24_g": 0.0, "age_hours": None,
                          "_unverified": True})
        log(f"[dexscreener] +{len(extra)} mints via endpoints de découverte")

    # ── coins ou une adresse suivie vient d'entrer ───────
    # Priorite absolue : c'est le signal le plus direct de la methode. Ils
    # entrent dans le scan meme s'ils ne ressortent pas de la decouverte
    # generique, et ils sont exemptes des seuils de taille — un wallet suivi
    # qui achete a 40k, c'est justement ce qu'on veut voir tot.
    wallet_mints = set()
    try:
        from .followed import recent_mints
        connus = {r["mint"] for r in cands}
        wallet_mints = {m for m in recent_mints(hours=48) if m not in connus}
        for m in wallet_mints:
            cands.append({"mint": m, "gecko_pool": None, "name": "?", "price_usd": 0.0,
                          "liquidity_usd": 0.0, "market_cap": 0.0, "fdv": 0.0,
                          "vol_h24": 0.0, "chg_h24_g": 0.0, "age_hours": None,
                          "_unverified": True, "_from_wallet": True})
        if wallet_mints:
            log(f"[wallets] +{len(wallet_mints)} coins ou une adresse suivie vient d'entrer")
    except Exception as e:
        log(f"[wallets] {e}")

    # les coins venus des wallets passent devant, le reste par volume
    cands.sort(key=lambda x: (bool(x.get("_from_wallet")), x["vol_h24"]), reverse=True)
    working = cands[: max(config.ENRICH_TOP_N, 60) + len(wallet_mints)]
    log(f"[filter] {len(cands)} candidats -> {len(working)} en working set")
    _p(28, "Métriques de marché", f"{len(working)} candidats")

    # ── construction + enrichissement DexScreener ────────
    t_dex = time.time()
    pairs: List[Pair] = []
    ecartes = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        enriched = list(ex.map(lambda r: dex.enrich(r["mint"]), working))
    for r, d in zip(working, enriched):
        p = Pair(
            chain="solana", name=r["name"] or "?", symbol="?", mint=r["mint"],
            pair_address="", gecko_pool=r["gecko_pool"],
            price_usd=r["price_usd"], market_cap=r["market_cap"], fdv=r["fdv"],
            liquidity_usd=r["liquidity_usd"], vol_h24=r["vol_h24"],
            age_hours=r["age_hours"] or 0.0,
        )
        if d:
            for k, v in d.items():
                if v is not None:
                    setattr(p, k, v)
        if p.symbol == "?":
            p.symbol = (p.name or "?").split()[0]

        depuis_wallet = bool(r.get("_from_wallet"))
        p.from_wallet = depuis_wallet

        # volume fabrique : des bots s'echangent le jeton pour entrer dans les
        # classements. On peut vendre, mais le prix tombe des que ca s'arrete.
        motif = safety.raison_exclusion(p)
        if motif:
            ecartes.append((p.symbol, motif))
            continue

        # on ecarte toujours les actions tokenisees et les stables, meme venu
        # d'un wallet suivi : ce n'est pas du crypto-natif jouable.
        if not is_crypto_native(p.symbol, p.name, p.mint, None):
            continue
        if depuis_wallet:
            # seuls garde-fous : le coin doit exister et etre echangeable
            if p.market_cap and p.market_cap > config.MAX_MCAP:
                continue
            if not p.liquidity_usd:
                continue
        else:
            if not _is_memecoin(p):
                continue
            # les mints venus de DexScreener n'ont pas encore été filtrés
            if not _liquidity_ok(p.liquidity_usd, p.vol_h24) or p.vol_h24 < config.MIN_VOL_H24:
                continue
            if p.age_hours and not (config.MIN_AGE_HOURS <= p.age_hours <= config.MAX_AGE_HOURS):
                continue
        pairs.append(p)

    log(f"[dexscreener] {len(pairs)} paires enrichies  ({time.time()-t_dex:.0f}s)")
    if ecartes:
        detail = ", ".join(f"{s} ({m})" for s, m in ecartes[:5])
        log(f"[securite] {len(ecartes)} ecartee(s) pour volume fabrique : {detail}")
    _p(36, "RSI & structure", f"{len(pairs)} paires")
    pairs.sort(key=lambda x: x.vol_h24, reverse=True)
    seen_sym, dedup = set(), []
    for p in pairs:
        key = (p.symbol or "").upper()
        if key and key in seen_sym:
            continue
        seen_sym.add(key)
        dedup.append(p)
    if len(dedup) != len(pairs):
        log(f"[dedup] {len(pairs)} -> {len(dedup)} (doublons de symbole)")
    pairs = dedup

    # ── OHLCV / RSI / swing (top N) ──────────────────────
    t_ohlcv = time.time()
    targets_ohlcv = pairs[: config.ENRICH_TOP_N]

    def _ohlcv_one(p):
        if not gecko.net_for(p.chain):
            return                      # chaine sans OHLCV (ex: robinhood)
        if not p.gecko_pool:
            p.gecko_pool = gecko.pool_for_token(p.mint, p.chain)
        if not p.gecko_pool:
            return
        # UN seul appel : 15m sur ~100h, dont on derive 1h (::4) et 4h (::16)
        c15 = gecko.ohlcv(p.gecko_pool, "minute", 15, 400, chain=p.chain)
        if not c15:
            return
        closes = [c[4] for c in c15]
        p.rsi_15m = rsi(closes)
        p.rsi_1h = rsi(closes[::4])
        p.rsi_4h = rsi(closes[::16])
        p.swing_low, p.swing_high = find_swing(c15, 24)
        p.rsi_note = " · ".join(f"{tf} {v}" for tf, v in
                                (("15m", p.rsi_15m), ("1h", p.rsi_1h), ("4h", p.rsi_4h))
                                if v is not None)

    done_o = [0]
    total_o = max(1, len(targets_ohlcv))

    def _ohlcv_tracked(p):
        _ohlcv_one(p)
        done_o[0] += 1
        _p(36 + int(34 * done_o[0] / total_o), "RSI & structure",
           f"{done_o[0]}/{total_o} paires")

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_ohlcv_tracked, targets_ohlcv))

    log(f"[ohlcv/rsi] terminé  ({time.time()-t_ohlcv:.0f}s)")

    # ── couche wallet (Helius, top N) ────────────────────
    t_w = time.time()
    if config.HELIUS_API_KEY:
        store = wallet_store.load()
        try:
            from .followed import tracked_registry
            reg = tracked_registry()
        except Exception:
            reg = {}
        # Helius ne couvre que Solana : la couche wallet s'y limite
        targets = [p for p in pairs if p.chain == "solana"][: config.SMARTMONEY_TOP_N]

        # On lit les avoirs de chaque wallet suivi UNE fois, puis on repond a
        # toutes les questions en memoire. Avant : une requete par couple
        # (wallet, coin), soit ~1 300 appels par scan. Maintenant : un par wallet.
        t_idx = time.time()
        index = helius.build_holdings_index(smart_wallets, log=log)
        log(f"[index] construit en {time.time()-t_idx:.0f}s")

        def _wallet_layer(p):
            conc = helius.holder_concentration(p.mint, max_pages=5)
            p.top_holder_pct = conc["top_holder_pct"]
            p.top10_pct = conc["top10_pct"]
            p.holders = conc.get("holders")
            sm = helius.smart_money_from_index(p.mint, index)
            p.smart_holders = sm["count"]
            p.smart_names = sm["wallets"]
            attach_wallet_detail(p, sm.get("addresses"), sm.get("wallets"), store, reg)
            p.wallets_available = True

        done_w = [0]
        total_w = max(1, len(targets))

        def _wallet_tracked(p):
            _wallet_layer(p)
            done_w[0] += 1
            _p(70 + int(22 * done_w[0] / total_w), "Holders & smart money",
               f"{done_w[0]}/{total_w} coins")

        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(_wallet_tracked, targets))
        log(f"[wallets] {len(targets)} coins  ({time.time()-t_w:.0f}s)")

    # ── securite : autorites du token ────────────────────
    # freeze authority active = l'emetteur peut geler tes jetons, tu achetes
    # et tu ne peux plus vendre. C'est le seul vrai piege, et il se verifie.
    try:
        n_danger = safety.controler_autorites(pairs[: config.ENRICH_TOP_N], log=log)
        if n_danger:
            pairs = [p for p in pairs if not getattr(p, "danger", "")]
    except Exception as e:
        log(f"[securite] {e}")

    # ── phase + intel + score ────────────────────────────
    _p(94, "Notation", f"{len(pairs)} paires")
    try:
        from . import telegram_alerts
        alerte = telegram_alerts.enabled()
    except Exception:
        telegram_alerts, alerte = None, False

    notees = []
    for p in pairs:
        p.phase = detect_phase(p)
        p.intel = build_intel(p)
        score_pair(p)
        p.updated_at = time.time()
        notees.append(p)

        # le Radar recoit la paire AVANT que l'alerte ne parte : ce qui arrive
        # sur le telephone est deja visible dans l'application.
        if on_scored:
            try:
                on_scored(sorted(notees,
                                 key=lambda x: (config.grade_rank(x.grade), x.score,
                                                x.smart_holders or 0),
                                 reverse=True))
            except Exception as e:
                log(f"[publish] {e}")

        if alerte and p.grade in telegram_alerts.ALERT_GRADES:
            try:
                telegram_alerts.notify_new([p])
            except Exception as e:
                log(f"[telegram] {e}")

    pairs.sort(
        key=lambda x: (config.grade_rank(x.grade), x.score, x.vol_h24),
        reverse=True,
    )
    log(f"[done] {len(pairs)} paires scorées en {time.time()-t0:.0f}s"
        f"  (debit {gecko.rate_state()['budget']}/min, {gecko.rate_state()['rate_limits']} rate-limits)")
    _p(100, "Terminé", f"{len(pairs)} paires")
    return pairs
