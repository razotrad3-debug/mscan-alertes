"""MSCAN — interface. Radar (liste) · Recherche (checker + whale flow) · Wallets.
Design : noir dominant, hairlines, or sobre. Market cap en direct."""
import threading
import time
from flask import Flask, render_template_string, jsonify, request
from markupsafe import Markup

import config
from mmscanner import engine, telegram
from mmscanner import model as _model

STATE = {"pairs": [], "updated": 0, "scanning": False, "mode": "live", "error": "",
         "progress": {"pct": 0, "phase": "", "detail": ""},
         "followed": {"wallets": [], "coins": [], "available": True, "empty": True},
         "followed_at": 0, "stale": False,
         "holdings": None}          # rempli au scan ; sinon relu sur disque
_LOCK = threading.Lock()


# ── formatage ──────────────────────────────────────────────────────
def _fmt(v):
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return str(v)
    s = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{s}${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{s}${v/1_000:.0f}K"
    return f"{s}${v:.0f}"


def _flowfmt(v):
    if v is None:
        return "—"
    return ("+" if v >= 0 else "−") + _fmt(abs(v)).lstrip("-")


def _ago(ts):
    """Horodatage -> '2h', '35min', '3j'."""
    if not ts:
        return "—"
    d = max(0, time.time() - float(ts))
    if d < 3600:
        return f"{int(d//60)}min"
    if d < 86400:
        return f"{d/3600:.1f}h".replace(".0h", "h")
    return f"{int(d//86400)}j"


def _rsicolor(v):
    """Dégradé continu : blanc à 50 → rouge vers 80 → vert vers 20."""
    if v is None:
        return "#4a4a52"
    if v >= 50:
        t = min((v - 50) / 30.0, 1.0)
        g = int(255 - 168 * t)
        return f"rgb(255,{g},{max(g-10,60)})"
    t = min((50 - v) / 30.0, 1.0)
    r = int(255 - 180 * t)
    return f"rgb({r},{int(255-30*t)},{int(255-120*t)})"


def _flowpct(b, sl):
    tot = (b or 0) + (sl or 0)
    return (b / tot * 100) if tot else None


def _pctcolor(v):
    """>55% achats = vert, <45% = rouge, sinon neutre."""
    if v is None:
        return "#3d3d44"
    if v >= 55:
        return "#4ade80"
    if v <= 45:
        return "#ff7a7a"
    return "#f3f4f7"


GRADE_COLOR = {"A+": "var(--gold-2)", "A": "var(--gold)",
               "A-": "color-mix(in srgb, var(--gold) 72%, #4a4a52)",
               "B+": "#9a9aa2", "B": "#8a8a92", "B-": "#7a7a82",
               "C+": "#6b6b73", "C": "#5f5f66", "C-": "#57575e", "D": "#4a4a52"}
PHASE_COLOR = {"Running": "#4ade80", "Early": "#7cc4ff", "Retest": "#e8c86a",
               "Compressing": "#b9a7ff", "Exhausted": "#ff7a7a", "Watch": "#7a7a82"}

_S = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
      'stroke-linecap="round" stroke-linejoin="round">{}</svg>')
ICONS = {
    "intel":  _S.format('<path d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .9 1.7h5.4c.1-.7.4-1.3.9-1.7A6 6 0 0 0 12 3z"/><path d="M10 19h4"/><path d="M10.5 21.5h3"/>'),
    "wallet": _S.format('<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a2 2 0 0 1 2 2v1"/><rect x="3" y="7.5" width="18" height="12" rx="2"/><circle cx="16.5" cy="13.5" r="1.2"/>'),
    "open":   _S.format('<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>'),
    "chart":  _S.format('<path d="M7 4v16"/><rect x="4.8" y="8" width="4.4" height="8" rx=".8"/><path d="M17 5v15"/><rect x="14.8" y="9.5" width="4.4" height="6.5" rx=".8"/>'),
    "trend":  '<img class="dexlogo" alt="DexScreener" src="data:image/png;base64,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">',
    "copy":   _S.format('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/>'),
    "search": _S.format('<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5 16.7 16.7"/>'),
    "done":   _S.format('<path d="M20 6 9 17l-5-5"/>'),
    "chev":   _S.format('<path d="m6 9 6 6 6-6"/>'),
    # marques de chaines (formes simples, lisibles en 14px)
    "c_solana": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 7.5 8 5h11l-3 2.5H5z"/><path d="M5 13.2 8 10.7h11l-3 2.5H5z"/><path d="M5 19 8 16.5h11L16 19H5z"/></svg>',
    "c_ethereum": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2 6 12l6 3.5L18 12 12 2z" opacity=".75"/><path d="m6 13.6 6 8.4 6-8.4-6 3.5-6-3.5z"/></svg>',
    "c_base": '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="9"/></svg>',
    "c_robinhood": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 20V7c3.5-2.2 8.5-2.2 12 0v13l-3-2-3 2-3-2-3 2z"/></svg>',
}


def create_app():
    app = Flask(__name__)
    j = app.jinja_env
    j.filters["fmt"] = _fmt
    j.filters["flowfmt"] = _flowfmt
    j.filters["ago"] = _ago
    j.globals.update(
        gradecolor=lambda g: GRADE_COLOR.get(g, "#7a7a82"),
        phasecolor=lambda p: PHASE_COLOR.get(p, "#7a7a82"),
        flowcolor=lambda v: "#4ade80" if (v or 0) >= 0 else "#ff7a7a",
        rsicolor=_rsicolor,
        flowpct=_flowpct,
        dexlink=_model.dex_link,
        gmgnlink=_model.gmgn_link,
        pctcolor=_pctcolor,
        icon=lambda n: Markup(ICONS.get(n, "")),
        money=_fmt,
    )

    @app.route("/")
    def index():
        with _LOCK:
            pairs, meta = STATE["pairs"], dict(STATE)

        # UNE seule liste, triee par grade (A+ en tete) puis par score.
        # Le filtrage se fait cote client via les onglets : instantane.
        ranked = sorted(pairs, key=lambda p: (config.grade_rank(p.grade), p.score,
                                              p.smart_holders or 0), reverse=True)
        seen = {p.mint for p in pairs}

        # tout coin qu'une adresse suivie vient d'acheter et qui n'est pas dans
        # l'univers scanne : on le remonte quand meme — c'est le signal le plus
        # frais qu'on ait. Les convergences (plusieurs wallets) passent devant.
        try:
            from mmscanner.followed import split_group
        except Exception:
            split_group = lambda l: ("Suivi", l)
        extra = []
        for c in (meta.get("followed") or {}).get("coins", []):
            if c["mint"] in seen:
                continue
            # provenance par GROUPE (Dabal, Grand, ...) — jamais les pseudos
            g = {}
            for lab in c.get("by", []):
                grp = split_group(lab)[0] or "Suivi"
                g[grp] = g.get(grp, 0) + 1
            c = dict(c)
            c["groups"] = ", ".join(f"{k} ×{v}" if v > 1 else k
                                    for k, v in sorted(g.items(), key=lambda kv: -kv[1]))
            extra.append(c)
        extra.sort(key=lambda c: (len(c["by"]), c.get("ts", 0)), reverse=True)

        chains = {"all": len(ranked) + len(extra)}
        for cid in config.CHAINS:
            chains[cid] = sum(1 for p in ranked if (p.chain or "solana") == cid)

        counts = {
            "tous": len(ranked) + len(extra),
            "conv": sum(1 for p in ranked if (p.smart_holders or 0) >= 2) + len(extra),
            "top": sum(1 for p in ranked if p.grade in ("A+", "A", "A-")),
            "wallet": sum(1 for p in ranked if (p.smart_holders or 0) >= 1),
            "running": sum(1 for p in ranked if p.phase == "Running"),
            "early": sum(1 for p in ranked if p.phase == "Early"),
            "retest": sum(1 for p in ranked if p.phase == "Retest"),
            "compress": sum(1 for p in ranked if p.phase == "Compressing"),
        }
        # coins sous veille d'expansion : reperes avant d'etre notes
        try:
            from mmscanner import expansion
            connus = {p.mint for p in ranked} | {c.get("mint") for c in extra}
            veille = [dict(e, mint=m) for m, e in expansion._lire().items()
                      if e.get("mc") and m not in connus]
            veille.sort(key=lambda e: (bool(e.get("impulsion_at")),
                                       e.get("mc") or 0), reverse=True)
            veille = veille[:40]
        except Exception:
            veille = []
        counts["veille"] = len(veille)

        return render_template_string(PAGE_RADAR, pairs=ranked, extra=extra,
                                      veille=veille, counts=counts,
                                      chains=chains, chainmeta=config.CHAIN_META,
                                      meta=meta, active="radar",
                                      prog=meta.get("progress", {}),
                                      helius=bool(config.HELIUS_API_KEY))

    @app.route("/coin")
    def coin():
        mint = (request.args.get("mint") or "").strip()
        with _LOCK:
            demo, meta = STATE["mode"] == "demo", dict(STATE)
        if not mint:
            return render_template_string(PAGE_SEARCH, active="search", meta=meta,
                                          helius=bool(config.HELIUS_API_KEY))
        if mint.upper() == "DEMO" or demo:
            from mmscanner.demo import demo_check
            p, flow = demo_check()
        else:
            from mmscanner import checker
            p, flow = checker.check(mint, load_smart_wallets())
        err = None if p else (flow or {}).get("error", "Token introuvable")
        return render_template_string(PAGE_COIN, p=p, flow=flow, err=err, mint=mint,
                                      meta=meta, active="search",
                                      helius=bool(config.HELIUS_API_KEY))

    @app.route("/wallets")
    def wallets():
        with _LOCK:
            demo, meta = STATE["mode"] == "demo", dict(STATE)
        if demo:
            from mmscanner.demo import demo_wallets
            rows = demo_wallets()
        else:
            from mmscanner import wallet_store
            rows = wallet_store.ranked(min_coins=2)
            # provenance : on affiche le groupe, jamais le pseudo
            try:
                from mmscanner.followed import tracked_registry
                reg = tracked_registry()
            except Exception:
                reg = {}
            for r in rows:
                meta_w = reg.get(r["address"]) or {}
                r["group"] = meta_w.get("group") or "on-chain"
                # les libelles auto-generes ("early_x2 (AXE+2132%...)") sont du bruit :
                # on n'affiche un nom que s'il vient d'une liste tenue a la main
                lab = meta_w.get("label") or ""
                r["label"] = lab if (meta_w.get("origin") == "suivi" and lab
                                     and not lab.startswith("early_x")) else ""
        for r in rows:
            r.setdefault("group", "on-chain")
            r.setdefault("grade", "B")
            r.setdefault("recent", (r.get("coins") or [])[:3])
        return render_template_string(PAGE_WALLETS, wallets=rows, meta=meta,
                                      active="wallets", subtab="detectes", helius=bool(config.HELIUS_API_KEY))

    @app.route("/alertes", methods=["GET", "POST"])
    def alertes():
        from mmscanner import telegram_alerts as tg
        with _LOCK:
            meta = dict(STATE)
        msg = err = ""

        if request.method == "POST":
            token = (request.form.get("token") or "").strip()
            chat = (request.form.get("chat_id") or "").strip()
            # le champ affiche un token masque : s'il n'a pas ete retouche, on garde l'ancien
            if token.startswith("•"):
                token = ""
            action = request.form.get("action") or "save"

            if token or chat:
                tg.save_config(token or None, chat or None)
            # pas de chat_id fourni : on le lit dans les messages recus par le bot
            if tg._token() and not tg._chat():
                trouve = tg.resolve_chat_id()
                if trouve:
                    tg.save_config(None, trouve)
                    msg = f"Conversation détectée ({trouve})."
                else:
                    err = ("Aucune conversation trouvée — envoie d'abord "
                           "/start à ton bot sur Telegram, puis réessaie.")

            if action == "test" and not err:
                if tg.test():
                    msg = "Message envoyé — regarde Telegram."
                else:
                    err = err or "Envoi refusé par Telegram (token invalide ?)."
            elif not msg and not err:
                msg = "Enregistré." if tg.enabled() else "Token enregistré, conversation manquante."

        etat = tg.status()
        tok = tg._token()
        masque = ("•" * 8 + tok[-6:]) if tok else ""
        return render_template_string(PAGE_ALERTES, tg=etat, token_masque=masque,
                                      msg=msg, err=err, meta=meta, active="wallets",
                                      helius=bool(config.HELIUS_API_KEY))

    @app.route("/holdings")
    def holdings_page():
        """Ce que les wallets suivis detiennent — une seule liste, filtrable."""
        from mmscanner import holdings as hmod
        with _LOCK:
            meta = dict(STATE)
        data = meta.get("holdings") or hmod.load()

        # note du radar, quand le coin y est passe : elle situe le setup
        notes = {p.mint: p.grade for p in (meta.get("pairs") or [])}
        # achete dans les 48 h : le coin est chaud, pas seulement detenu
        try:
            from mmscanner.followed import recent_mints
            recents = set(recent_mints(48))
        except Exception:
            recents = set()

        tous = list(data.get("coins") or []) + list(data.get("solo") or [])
        coins = []
        for c in tous:
            c = dict(c)
            g = {}
            for grp in (c.get("by") or []):
                grp = grp or "Suivi"
                g[grp] = g.get(grp, 0) + 1
            # ordre canonique : la provenance se lit toujours au meme endroit
            # ordre canonique, mais pas de couleur : la provenance se lit
            # dans le meme gris que le reste de la ligne
            c["groupes"] = [{"nom": k, "n": v}
                            for k, v in sorted(g.items(),
                                               key=lambda kv: config.rang_groupe(kv[0]))]
            c["fomo"] = any(x["nom"] != "on-chain" for x in c["groupes"])
            c["grade"] = notes.get(c.get("mint"))
            c["neuf"] = c.get("mint") in recents
            coins.append(c)

        tri = request.args.get("tri", "convergence")
        if tri == "solo":
            coins = [c for c in coins if c.get("holders", 0) <= 1]
            coins.sort(key=lambda c: c.get("value_usd", 0), reverse=True)
        elif tri == "fomo":
            coins = [c for c in coins if c.get("fomo")]
            coins.sort(key=lambda c: (c.get("holders", 0), c.get("value_usd", 0)),
                       reverse=True)
        elif tri == "mc":
            coins.sort(key=lambda c: c.get("mc") or 0, reverse=True)
        elif tri == "dollars":
            coins.sort(key=lambda c: c.get("value_usd", 0), reverse=True)
        elif tri == "conviction":
            coins.sort(key=lambda c: (bool(c.get("dip")), c.get("holders", 0),
                                      c.get("value_usd", 0)), reverse=True)
        else:
            coins.sort(key=lambda c: (c.get("holders", 0), c.get("value_usd", 0)),
                       reverse=True)

        return render_template_string(PAGE_HOLDINGS, coins=coins, meta=meta,
                                      tri=tri, updated=data.get("at"),
                                      nwallets=data.get("wallets", 0),
                                      active="holdings",
                                      helius=bool(config.HELIUS_API_KEY))

    @app.route("/positions")
    def positions_redirect():
        """Positions a fusionne avec Holdings : meme question, meme reponse."""
        from flask import redirect
        return redirect("/holdings")

    @app.route("/adresses", methods=["GET", "POST"])
    def adresses():
        from mmscanner import followed as fmod
        with _LOCK:
            demo, meta = STATE["mode"] == "demo", dict(STATE)

        saved = resolved = None
        if request.method == "POST":
            if request.form.get("handles"):
                from mmscanner import fomoscan
                names = [h.strip() for h in
                         request.form["handles"].replace(",", chr(10)).splitlines() if h.strip()]
                # fiches publiques fomoscan.sh : gratuites, contrairement a l'API
                from mmscanner import fomoscan_web
                grp = (request.form.get("groupe") or "").strip()[:24]
                res, rows = [], []
                for h in names[:40]:
                    w = fomoscan_web.lookup(h)
                    lab = f"[{grp}] {h}" if grp else f"[Suivi] {h}"
                    if w.get("solana"):
                        rows.append((w["solana"], lab))
                    if w.get("ethereum"):
                        rows.append((w["ethereum"], lab))
                    res.append({"handle": h, "ok": w["ok"],
                                "solana": w.get("solana"), "ethereum": w.get("ethereum"),
                                "reason": w.get("reason", "")})
                resolved = {"res": res, "added": fmod.append_followed(rows),
                            "haskey": True}
            elif request.form.get("action") == "restore":
                saved = fmod.restore_followed()
            else:
                saved = fmod.save_followed(request.form.get("addresses", ""))

        raw = fmod.raw_followed()
        if demo:
            from mmscanner.demo import demo_followed
            data = demo_followed()
        else:
            # resultat calcule en tache de fond : affichage instantane
            data = meta.get("followed") or {"wallets": [], "coins": [],
                                            "available": True, "empty": not fmod.load_followed()}

        top_clans = []
        try:
            from mmscanner import fomoscan
            if fomoscan.has_key():
                top_clans = fomoscan.clans("24h")[:10]
        except Exception as e:
            print(f"[clans] {e}")

        multi = [c for c in data.get("coins", []) if len(c.get("by", [])) > 1]
        try:
            from mmscanner import clans as clans_mod
            clanstate = clans_mod.summary()
        except Exception:
            clanstate = None

        return render_template_string(PAGE_FOLLOW, data=data, multi=multi, meta=meta,
                                      clanstate=clanstate,
                                      raw=raw, saved=saved, resolved=resolved,
                                      clans=top_clans, active="follow", subtab="adresses",
                                      helius=bool(config.HELIUS_API_KEY))

    @app.route("/flow")
    def flow_page():
        with _LOCK:
            pairs, demo, meta = list(STATE["pairs"]), STATE["mode"] == "demo", dict(STATE)
        rows = []
        if demo:
            from mmscanner.demo import demo_pairs, demo_flow
            import random
            random.seed(7)
            for p in demo_pairs():
                f = demo_flow()
                mult = random.uniform(-0.7, 1.6)
                f = {**f, "tiers": {k: {**v, "24h": v["24h"] * mult, "7d": v["7d"] * mult}
                                    for k, v in f["tiers"].items()},
                     "totals": {k: v * mult for k, v in f["totals"].items()}}
                f["strong"] = f["tiers"]["Whale"]["24h"] + f["tiers"]["Shark"]["24h"]
                rows.append({"p": p, "f": f})
        else:
            from mmscanner import holder_flow
            for p in pairs:
                f = holder_flow.compute(p.mint, p.price_usd)
                cov = f.get("covered", {})
                if not f.get("available") or not (cov.get("24h") or cov.get("recent")):
                    continue
                # si 24h pas encore couvert, on classe sur le flux depuis
                # la derniere photo : l'onglet est utile des le 2e scan
                win = "24h" if cov.get("24h") else "recent"
                f["win"] = win
                f["strong"] = f["tiers"]["Whale"][win] + f["tiers"]["Shark"][win]
                rows.append({"p": p, "f": f})
        rows.sort(key=lambda r: r["f"]["strong"], reverse=True)
        return render_template_string(PAGE_FLOW, rows=rows, pairs=pairs, meta=meta,
                                      active="wallets", helius=bool(config.HELIUS_API_KEY))

    @app.route("/api/pairs")
    def api():
        with _LOCK:
            return jsonify({"updated": STATE["updated"], "mode": STATE["mode"],
                            "scanning": STATE["scanning"],
                            "progress": STATE.get("progress", {}),
                            "pairs": [p.to_dict() for p in STATE["pairs"]]})

    return app


def scan_loop(demo: bool = False):
    if demo:
        from mmscanner.demo import demo_pairs
        with _LOCK:
            STATE.update(pairs=demo_pairs(), updated=time.time(), mode="demo")
        return
    # le Radar ne doit jamais etre vide : on repart du dernier scan connu
    try:
        from mmscanner import cache
        old, saved_at = cache.load()
        if old:
            with _LOCK:
                STATE.update(pairs=old, updated=saved_at, mode="live", stale=True)
            age = int((time.time() - saved_at) / 60)
            print(f"[cache] {len(old)} paires rechargees (il y a {age} min)")
    except Exception as e:
        print(f"[cache] {e}")

    while True:
        with _LOCK:
            STATE["scanning"] = True
            STATE["progress"] = {"pct": 0, "phase": "Démarrage", "detail": ""}

        def _prog(pct, phase, detail=""):
            with _LOCK:
                STATE["progress"] = {"pct": pct, "phase": phase, "detail": detail}

        def _publier(partiel):
            # resultats visibles au fur et a mesure, sans attendre la fin
            with _LOCK:
                STATE["pairs"] = partiel
                STATE["updated"] = time.time()
                STATE["stale"] = False

        try:
            pairs = engine.scan(load_smart_wallets(), progress=_prog, on_scored=_publier)
            try:
                from mmscanner import cache
                cache.save(pairs)
            except Exception as e:
                print(f"[cache] {e}")
            with _LOCK:
                STATE.update(pairs=pairs, updated=time.time(), scanning=False,
                             mode="live", error="", stale=False)
                STATE["progress"] = {"pct": 100, "phase": "Terminé", "detail": f"{len(pairs)} paires"}
            # rosters de clans en attente : on retente des que le quota revient
            try:
                from mmscanner import clans as clans_mod
                clans_mod.resolve_pending(budget=40)
            except Exception as e:
                print(f"[clans] {e}")

            # les A+ sont deja partis un par un pendant la notation (engine.scan) ;
            # ce passage rattrape ce qui aurait echoue a ce moment-la.
            try:
                from mmscanner import telegram_alerts
                telegram_alerts.notify_new(pairs)
            except Exception as e:
                print(f"[telegram] {e}")

            # photos de soldes -> alimente le Whale Flow (méthode sun-flow)
            try:
                from mmscanner import holder_flow
                n = sum(1 for p in pairs[:config.SMARTMONEY_TOP_N]
                        if holder_flow.snapshot(p.mint, p.price_usd))
                if n:
                    print(f"[flow] {n} photos de soldes prises")
            except Exception as e:
                print(f"[flow] {e}")

            # achats recents des adresses suivies -> alimente la convergence
            try:
                from mmscanner import followed as fmod
                if fmod.load_followed():
                    fdata = fmod.scan()
                    fmod.save_buys(fdata)      # le moteur les reprend au scan suivant
                    with _LOCK:
                        STATE["followed"] = fdata
                        STATE["followed_at"] = time.time()
                    nb = sum(1 for c in fdata.get("coins", []) if len(c.get("by", [])) > 1)
                    print(f"[convergence] {len(fdata.get('coins', []))} coins suivis, {nb} en convergence")
            except Exception as e:
                print(f"[convergence] {e}")

            # ce que les adresses suivies DETIENNENT (et non ce qu'elles
            # viennent d'acheter) : coins etablis, lus par vagues, cache 3 h
            try:
                from mmscanner import holdings as hmod

                def _garder(res):
                    with _LOCK:
                        STATE["holdings"] = res

                hmod.lancer_en_fond(sur_fin=_garder)
            except Exception as e:
                print(f"[holdings] {e}")

            # veille d'expansion : on arme les nouvelles paires et on suit
            # leur etat pour l'afficher. L'envoi reste au cloud.
            try:
                from mmscanner import expansion
                expansion.armer_naissances()
                expansion.poll(envoyer=False)
            except Exception as e:
                print(f"[expansion] {e}")

            # les smart wallets se mettent a jour tout seuls sur les coins
            # qui viennent de percer (toutes les DISCOVER_INTERVAL_H heures)
            try:
                from mmscanner import discover_wallets
                discover_wallets.run_if_due()
            except Exception as e:
                print(f"[wallets] {e}")
            # (l'ancien chemin telegram.push_alerts a ete retire : il doublonnait
            #  avec telegram_alerts, avec un autre seuil et un autre format, d'ou
            #  des alertes qui ne correspondaient pas a ce qu'affichait le Radar)
        except Exception as e:
            with _LOCK:
                STATE.update(scanning=False, error=str(e))
            print(f"[scan] erreur: {e}")
        time.sleep(config.SCAN_INTERVAL_SEC)


def load_smart_wallets():
    """Wallets surveilles = detectes automatiquement + TES adresses suivies."""
    try:
        from mmscanner import followed
        return followed.tracked_lines()
    except Exception:
        try:
            with open(config.SMART_WALLETS_FILE, "r", encoding="utf-8") as f:
                return [ln for ln in (l.strip() for l in f) if ln and not ln.startswith("#")]
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════
STYLE = r"""
<style>
:root{
 --bg:#000;--surface:#08090b;--surface-2:#0b0c0e;--inset:#050506;
 --hair:rgba(255,255,255,.065);--hair-2:rgba(255,255,255,.038);
 --fg:#fff;--fg-2:#9a9aa2;--fg-3:#5c5c64;--fg-4:#3d3d44;
 --gold:#d4af37;--gold-2:#e8c86a;--gold-dim:rgba(212,175,55,.14);
 --up:#4ade80;--down:#ff7a7a;
 --r:3px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
body{background:var(--bg);color:var(--fg);
 font-family:"Inter","Segoe UI",system-ui,-apple-system,sans-serif;
 font-size:13px;line-height:1.5;min-height:100vh;letter-spacing:.005em}
a{color:inherit;text-decoration:none}
button{font-family:inherit}
::selection{background:rgba(212,175,55,.22)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:#17181b;border-radius:0}
::-webkit-scrollbar-thumb:hover{background:#212227}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.mono{font-family:"SF Mono",ui-monospace,Consolas,monospace}

/* ── chrome ───────────────────────────────── */
.top{position:sticky;top:0;z-index:20;background:rgba(0,0,0,.86);
 backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--hair)}
.brandrow{max-width:1080px;margin:0 auto;padding:0 28px;height:56px;display:flex;align-items:center;gap:14px}
.mark{width:26px;height:26px;display:block;object-fit:contain}
.wordmark{font-size:14px;font-weight:600;letter-spacing:.32em;color:var(--fg)}
.status{margin-left:auto;display:flex;align-items:center;gap:14px;
 font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fg-3)}
.status .live{color:var(--fg-2)}
.status .live i{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--up);
 margin-right:7px;vertical-align:middle;box-shadow:0 0 6px var(--up)}
.status .demo{color:var(--gold);border:1px solid var(--gold-dim);padding:3px 8px}
/* groupees au centre : la grille de 4 colonnes fixes laissait un vide a
   droite des que le nombre d'onglets changeait, et tirait tout vers la gauche */
.nav{max-width:1080px;margin:0 auto;padding:0 28px;display:flex;justify-content:center}
.nav a{min-width:148px;height:40px;display:flex;align-items:center;justify-content:center;
 font-size:10px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;color:var(--fg-3);
 border-bottom:1px solid transparent;transition:color .16s,border-color .16s}
.nav a:hover{color:var(--fg-2)}
.nav a.on{color:var(--gold-2);border-bottom-color:var(--gold)}

.page{max-width:1080px;margin:0 auto;padding:30px 28px 70px}
.eyebrow{font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--fg-3);font-weight:600}
.sechead{display:flex;align-items:baseline;gap:14px;padding-bottom:12px;margin-bottom:2px;border-bottom:1px solid var(--hair)}
.sechead h1{font-size:15px;font-weight:600;letter-spacing:.02em}
.sechead .sub{font-size:11px;color:var(--fg-3);margin-left:auto}
.sechead.conv{border-bottom-color:rgba(212,175,55,.45)}
.sechead.conv h1{color:var(--gold-2)}
.notice{border:1px solid var(--gold-dim);background:rgba(212,175,55,.045);color:var(--gold-2);
 padding:12px 16px;margin-bottom:24px;font-size:11.5px}
.notice code{color:var(--fg-2);font-size:11px}

/* ── rows ─────────────────────────────────── */
.rows{border:1px solid var(--hair);border-radius:var(--r);overflow:hidden;background:var(--surface)}
.item{border-bottom:1px solid var(--hair-2)}
.item:last-child{border-bottom:none}
.item.open{background:var(--surface-2)}
.r{display:grid;grid-template-columns:38px minmax(0,1fr) 96px 104px auto;
 align-items:center;gap:16px;padding:15px 20px;transition:background .14s}
.item:hover .r{background:rgba(255,255,255,.017)}
.gr{font-size:13px;font-weight:600;letter-spacing:.02em;color:var(--gc);
 border-left:2px solid var(--gc);padding-left:9px;line-height:1.1}
.id .n{font-size:13.5px;font-weight:500;letter-spacing:.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.id .s{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-4);margin-top:3px}
.val{text-align:right}
.val .m{font-size:14px;font-weight:600;letter-spacing:-.01em;transition:color .35s}
.val .c{font-size:10.5px;margin-top:3px;font-weight:500}
.up{color:var(--up)}.down{color:var(--down)}
.ph{font-size:9px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--pc);
 display:flex;align-items:center;gap:7px;justify-content:flex-end}
.ph i{width:4px;height:4px;border-radius:50%;background:var(--pc);box-shadow:0 0 6px var(--pc);flex:0 0 auto}
.acts{display:flex;gap:1px}
.ic{width:30px;height:30px;display:flex;align-items:center;justify-content:center;
 color:var(--fg-4);background:none;border:none;cursor:pointer;transition:color .14s}
.ic:hover{color:var(--gold-2)}
.ic.on{color:var(--gold)}
.ic.ok{color:var(--up)}
.ic svg{width:15px;height:15px}
.ic .dexlogo{width:15px;height:15px;object-fit:contain;opacity:.62;transition:opacity .14s}
.ic:hover .dexlogo{opacity:1}

/* ── panneaux ─────────────────────────────── */
.panel{border-top:1px solid var(--hair-2);padding:20px;background:var(--inset)}
.panel[hidden]{display:none}
.cols{display:grid;grid-template-columns:1.35fr 1fr;gap:28px}
@media(max-width:760px){.cols{grid-template-columns:1fr;gap:22px}}
.lab{font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:var(--fg-3);
 font-weight:600;display:block;margin-bottom:10px}
.entry{font-size:12.5px;color:var(--fg-2);line-height:1.65}
.tg{display:flex;gap:0;margin-top:16px;border:1px solid var(--hair);border-radius:var(--r)}
.tg div{flex:1;padding:9px 12px;border-right:1px solid var(--hair)}
.tg div:last-child{border-right:none}
.tg .k{font-size:8.5px;letter-spacing:.18em;color:var(--fg-4);text-transform:uppercase}
.tg .v{font-size:12.5px;font-weight:600;color:var(--up);margin-top:4px}
.cutline{margin-top:14px;padding-top:12px;border-top:1px solid var(--hair-2);
 font-size:11.5px;color:var(--fg-2);line-height:1.6}
.cutline b{color:var(--down);font-weight:600}
.rsirow{display:flex;border:1px solid var(--hair);border-radius:var(--r)}
.rsirow div{flex:1;padding:11px 8px;text-align:center;border-right:1px solid var(--hair)}
.rsirow div:last-child{border-right:none}
.rsirow .k{font-size:8.5px;letter-spacing:.18em;color:var(--fg-4);text-transform:uppercase}
.rsirow .v{font-size:18px;font-weight:600;margin-top:4px;letter-spacing:-.02em}
.rsirow .bs{font-size:9px;color:var(--fg-4);margin-top:3px;letter-spacing:.04em}
.stats{margin-top:18px;display:grid;grid-template-columns:1fr 1fr;gap:0 22px}
.st{display:flex;justify-content:space-between;align-items:baseline;
 padding:7px 0;border-bottom:1px solid var(--hair-2);font-size:11.5px}
.st .k{color:var(--fg-3);font-size:10px;letter-spacing:.08em;text-transform:uppercase}
.st .v{font-weight:600;color:var(--fg)}
.miss{margin-top:16px;display:flex;flex-wrap:wrap;gap:7px;align-items:center}
.miss .k{font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--fg-4);font-weight:600}
.miss span.t{font-size:10px;color:var(--fg-2);border:1px solid var(--hair);padding:3px 9px;border-radius:var(--r)}
.score{font-size:11px;color:var(--fg-3);letter-spacing:.06em}
.score b{color:var(--gold-2);font-weight:600}

/* wallets in panel */
.wl{border:1px solid var(--hair);border-radius:var(--r)}
.wl .w{padding:13px 15px;border-bottom:1px solid var(--hair-2)}
.wl .w:last-child{border-bottom:none}
.wtop{display:flex;align-items:baseline;gap:10px}
.wtop .nm{font-size:12.5px;font-weight:600;color:var(--gold-2)}
.wtop .ad{font-size:10px;color:var(--fg-4)}
.wtop .cnt{margin-left:auto;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-3)}
.why{margin-top:9px;display:flex;flex-wrap:wrap;gap:6px}
.why .c{font-size:10.5px;color:var(--fg-2);border:1px solid var(--hair);padding:3px 9px;border-radius:var(--r);white-space:nowrap}
.why .c b{color:var(--up);font-weight:600}
.why .c i{color:var(--fg-4);font-style:normal;font-size:9.5px}
.emptyline{color:var(--fg-3);font-size:11.5px;padding:14px 15px}

/* ── recherche ────────────────────────────── */
.hero{max-width:600px;margin:12vh auto 0;text-align:center}
.hero .eyebrow{margin-bottom:14px}
.hero h2{font-size:26px;font-weight:300;letter-spacing:-.01em;margin-bottom:10px}
.hero p{color:var(--fg-3);font-size:12.5px;margin-bottom:30px;line-height:1.7}
.sbox{display:flex;border:1px solid var(--hair);border-radius:var(--r);background:var(--surface);transition:border-color .2s}
.sbox:focus-within{border-color:rgba(212,175,55,.5)}
.sbox input{flex:1;background:none;border:none;color:var(--fg);padding:15px 18px;font-size:13px;outline:none;
 font-family:"SF Mono",ui-monospace,Consolas,monospace}
.sbox input::placeholder{color:var(--fg-4);font-family:"Inter","Segoe UI",sans-serif;letter-spacing:.02em}
.sbox button{background:none;border:none;border-left:1px solid var(--hair);color:var(--gold);
 padding:0 22px;cursor:pointer;display:flex;align-items:center;gap:9px;
 font-size:10px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;transition:background .16s}
.sbox button:hover{background:rgba(212,175,55,.07)}
.sbox button svg{width:14px;height:14px}

/* ── analyse ──────────────────────────────── */
.card{border:1px solid var(--hair);border-radius:var(--r);background:var(--surface)}
.chead{display:flex;align-items:flex-start;gap:18px;padding:22px 24px;border-bottom:1px solid var(--hair-2)}
.chead .g{font-size:20px;font-weight:600;color:var(--gc);border-left:2px solid var(--gc);padding-left:12px;line-height:1}
.chead .t .n{font-size:17px;font-weight:500}
.chead .t .s{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fg-4);margin-top:4px}
.chead .r{margin-left:auto;text-align:right}
.chead .r .m{font-size:22px;font-weight:600;letter-spacing:-.02em}
.chead .r .x{font-size:10.5px;margin-top:5px;display:flex;gap:10px;justify-content:flex-end;align-items:center}
.cbody{padding:22px 24px}
.cbody .lab{margin-top:0}
.sect{margin-top:26px}
.addr{display:flex;align-items:center;gap:12px;margin-top:26px;padding:12px 14px;border:1px solid var(--hair);border-radius:var(--r)}
.addr .a{flex:1;font-size:10.5px;color:var(--fg-3);word-break:break-all}
details.wcard{overflow:hidden}
details.wcard>summary{list-style:none;cursor:pointer}
details.wcard>summary::-webkit-details-marker{display:none}
.wgrade{display:flex;align-items:center;justify-content:center;height:30px;border-radius:8px;
 font-size:12px;font-weight:700;letter-spacing:.02em;border:1px solid}
.wgrade.gAp{color:#ffd76a;border-color:#ffd76a55;background:#ffd76a12}
.wgrade.gA{color:#e8c86a;border-color:#e8c86a44;background:#e8c86a0e}
.wgrade.gAm{color:#c9b25f;border-color:#c9b25f3a;background:#c9b25f0c}
.wgrade.gBp{color:#8fb3d9;border-color:#8fb3d93a;background:#8fb3d90c}
.wgrade.gB{color:#7f8794;border-color:#7f87943a;background:#7f87940c}
.wgrade.gC{color:#6b6b73;border-color:#6b6b733a;background:transparent}
.wid{min-width:0}
.wid .nm{display:flex;align-items:center;gap:8px}
.wsrc{font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-4);
 border:1px solid var(--hair);border-radius:4px;padding:2px 6px}
.wlast{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end}
.wlast .tk{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;color:var(--fg-3);
 border:1px solid var(--hair);border-radius:6px;padding:3px 8px}
.wlast .tk b{font-weight:600;color:var(--fg-4);font-variant-numeric:tabular-nums}
.wchev{display:flex;color:var(--fg-4);transition:transform .18s}
details[open]>summary .wchev{transform:rotate(180deg)}
.wchev svg{width:16px;height:16px}
.coin .cmc{color:var(--fg-3);font-size:11.5px}
.plan{}
.pact{font-size:14px;font-weight:600;color:var(--gold-2);margin-bottom:12px;
 letter-spacing:.01em}
.pgrid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:12px}
.pgrid>div{display:flex;flex-direction:column;gap:4px;border:1px solid var(--hair);
 border-radius:var(--r);padding:9px 11px;background:var(--bg)}
.pgrid .k{font-size:8.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fg-4)}
.pgrid .v{font-size:13px;font-weight:600;color:var(--fg)}
.pgrid .v.cut{color:var(--down)}
.pwhy{font-size:12px;line-height:1.6;color:var(--fg-2)}
.pdetail{margin-top:12px}
.pdetail>summary{cursor:pointer;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--fg-4);list-style:none}
.pdetail>summary::-webkit-details-marker{display:none}
.pdetail>summary:hover{color:var(--gold)}
.pdetail .entry{margin-top:10px}
.addbox .ft button.ghost{background:transparent;border:1px solid var(--hair);
 color:var(--fg-3)}
.addbox .ft button.ghost:hover{border-color:var(--gold);color:var(--gold)}
.live.stale i{background:#e0a83a}
.live.stale b{color:#e0a83a;font-weight:600}
.s1.wsrc2{color:var(--gold);border-color:var(--gold);background:var(--gold-dim)}
.tgin{width:100%;background:var(--bg);border:1px solid var(--hair);border-radius:var(--r);
 color:var(--fg);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
 padding:11px 13px;margin-bottom:9px;outline:none}
.tgin:focus{border-color:var(--gold)}
.addbox .ft .ok{font-size:11px;color:var(--up)}
.addbox .ft .ko{font-size:11px;color:var(--down)}
.explain{border:1px solid var(--hair);border-left:2px solid var(--gold);border-radius:var(--r);
 background:var(--surface);padding:12px 16px;margin-bottom:18px;font-size:12px;
 line-height:1.65;color:var(--fg-3)}
.explain b{color:var(--fg-2);font-weight:600}
.chains{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.chains .ch{background:var(--surface);border:1px solid var(--hair);color:var(--fg-3);
 padding:5px 10px;border-radius:var(--r);cursor:pointer;font-size:9px;font-weight:600;
 letter-spacing:.07em;text-transform:uppercase;transition:.15s;font-family:inherit;
 display:inline-flex;align-items:center;gap:6px}
.chains .ch:hover{color:var(--fg);border-color:var(--cc)}
.chains .ch i{font-style:normal;font-size:8.5px;color:var(--fg-4);font-variant-numeric:tabular-nums}
.chains .ch.on{background:color-mix(in srgb,var(--cc) 14%,transparent);
 border-color:var(--cc);color:var(--cc)}
.chains .ch.on i{color:var(--cc)}
.chains .clogo{display:flex;color:var(--cc)}
.chains .clogo svg{width:12px;height:12px}
.tag{font-size:7.5px;letter-spacing:.05em;border:1px solid;border-radius:var(--r);
 padding:0 4px;vertical-align:2px;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
.chips .chip{background:var(--surface);border:1px solid var(--hair);color:var(--fg-3);
 padding:7px 13px;border-radius:var(--r);cursor:pointer;font-size:10px;font-weight:600;
 letter-spacing:.13em;text-transform:uppercase;transition:.15s;font-family:inherit;
 display:inline-flex;align-items:center;gap:7px}
.chips .chip:hover{color:var(--fg);border-color:var(--fg-4)}
.chips .chip i{font-style:normal;font-size:9.5px;color:var(--fg-4);font-variant-numeric:tabular-nums}
.chips .chip.on{background:rgba(212,175,55,.12);border-color:var(--gold);color:var(--gold-2)}
.chips .chip.on i{color:var(--gold)}
.chips .chip.gold{border-color:rgba(212,175,55,.3);color:var(--gold-2)}
.chips .chip.gold.on{background:rgba(212,175,55,.16)}
.nores{text-align:center;padding:44px;color:var(--fg-3);font-size:12.5px;
 border:1px solid var(--hair);border-radius:var(--r);background:var(--surface)}
.gauge{border:1px solid var(--hair);border-radius:var(--r);background:var(--surface);
 padding:13px 16px;margin-bottom:20px}
.gauge[hidden]{display:none}
.gl{display:flex;align-items:baseline;gap:10px;margin-bottom:9px}
.gl .gp{font-size:11px;font-weight:600;color:var(--gold-2);letter-spacing:.04em}
.gl .gd{font-size:10.5px;color:var(--fg-3)}
.gl .gn{margin-left:auto;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--fg-2)}
.gbar{height:3px;background:rgba(255,255,255,.07);border-radius:2px;overflow:hidden}
.gfill{height:100%;background:linear-gradient(90deg,var(--gold),var(--gold-2));
 border-radius:2px;transition:width .5s ease}
.subtabs{display:flex;gap:2px;margin-bottom:20px;border-bottom:1px solid var(--hair)}
.subtabs a{padding:9px 18px;font-size:10px;font-weight:600;letter-spacing:.16em;
 text-transform:uppercase;color:var(--fg-3);border-bottom:1px solid transparent;margin-bottom:-1px}
.subtabs a:hover{color:var(--fg-2)}
.subtabs a.on{color:var(--gold-2);border-bottom-color:var(--gold)}
.src{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.src .s1{font-size:9.5px;padding:2px 8px;border-radius:2px;font-weight:600;letter-spacing:.04em}
.src .kol{background:rgba(212,175,55,.13);color:var(--gold-2);border:1px solid rgba(212,175,55,.3)}
.src .oc{background:rgba(255,255,255,.05);color:var(--fg-3);border:1px solid var(--hair)}
.back{display:inline-flex;align-items:center;gap:8px;font-size:10px;letter-spacing:.18em;
 text-transform:uppercase;color:var(--fg-3);margin-bottom:20px}
.back:hover{color:var(--gold-2)}

/* ── whale flow ───────────────────────────── */
.flow{margin-top:20px;border:1px solid var(--hair);border-radius:var(--r);background:var(--surface)}
.flow .h{padding:18px 22px;border-bottom:1px solid var(--hair-2)}
.flow .h .t{font-size:13px;font-weight:600;letter-spacing:.02em}
.flow .h .s{font-size:10.5px;color:var(--fg-3);margin-top:4px}
.flow .h .sig{margin-top:10px;font-size:11.5px;color:var(--gold-2);border-left:2px solid var(--gold);padding-left:10px}
.tot .na{font-size:9px;color:var(--fg-4);margin-top:3px}
.tot{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid var(--hair-2)}
.tot div{padding:16px 22px;border-right:1px solid var(--hair-2)}
.tot div:last-child{border-right:none}
.tot .k{font-size:8.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--fg-4)}
.tot .v{font-size:20px;font-weight:600;margin-top:5px;letter-spacing:-.02em}
table{width:100%;border-collapse:collapse}
th{text-align:right;font-size:8.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--fg-4);
 font-weight:600;padding:12px 22px;border-bottom:1px solid var(--hair-2)}
th:first-child,td:first-child{text-align:left}
td{padding:13px 22px;border-bottom:1px solid var(--hair-2);text-align:right;font-size:12.5px;font-weight:500}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:rgba(255,255,255,.015)}
td.tier{font-weight:600;color:var(--fg)}
td.tier span{color:var(--fg-4);font-size:10px;margin-left:8px;letter-spacing:.1em}

/* ── page wallets ─────────────────────────── */
.wcard{border:1px solid var(--hair);border-radius:var(--r);background:var(--surface);margin-bottom:10px}
.wh{display:grid;grid-template-columns:46px minmax(150px,1fr) 62px 92px 78px minmax(0,1.1fr) 18px;
 gap:18px;align-items:center;padding:16px 22px;border-bottom:1px solid var(--hair-2)}
details.wcard:not([open])>summary.wh{border-bottom:none}
details.wcard>summary.wh:hover{background:var(--gold-dim)}
.wh .nm{font-size:13.5px;font-weight:600;color:var(--gold-2)}
.wh .ad{font-size:10px;color:var(--fg-4);margin-top:4px}
.wh .met{text-align:right}
.wh .met .k{font-size:8.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fg-4)}
.wh .met .v{font-size:15px;font-weight:600;margin-top:3px}
.wb{padding:16px 22px}
.wb .lab{margin-bottom:12px}
.coin{display:grid;grid-template-columns:minmax(0,1fr) 84px 84px 92px 108px;gap:14px;align-items:center;
 padding:9px 0;border-bottom:1px solid var(--hair-2);font-size:12px}
.coin:last-child{border-bottom:none}
.coin .cn{font-weight:500}
.coin .cn small{color:var(--fg-4);margin-left:8px;font-size:10px;letter-spacing:.08em}
.coin .p{text-align:right;color:var(--up);font-weight:600}
.coin .rk{text-align:right;color:var(--fg-3);font-size:10.5px;letter-spacing:.06em}
.addbox{border:1px solid var(--hair);border-radius:var(--r);background:var(--surface);margin-bottom:26px}
.addbox .hd{padding:15px 20px;border-bottom:1px solid var(--hair-2);display:flex;align-items:baseline;gap:12px}
.addbox .hd .t{font-size:12.5px;font-weight:600}
.addbox .hd .h{font-size:10.5px;color:var(--fg-3);margin-left:auto}
.addbox .bd{padding:16px 20px}
.addbox textarea{width:100%;min-height:130px;resize:vertical;background:var(--inset);
 border:1px solid var(--hair);border-radius:var(--r);color:var(--fg);padding:13px 15px;
 font-family:"SF Mono",ui-monospace,Consolas,monospace;font-size:12px;line-height:1.7;outline:none;
 transition:border-color .18s}
.addbox textarea:focus{border-color:rgba(212,175,55,.5)}
.addbox textarea::placeholder{color:var(--fg-4)}
.addbox .ft{display:flex;align-items:center;gap:14px;margin-top:13px}
.addbox button{background:none;border:1px solid var(--gold);color:var(--gold);padding:9px 22px;
 border-radius:var(--r);cursor:pointer;font-size:10px;font-weight:600;letter-spacing:.18em;
 text-transform:uppercase;transition:background .16s}
.addbox button:hover{background:rgba(212,175,55,.1)}
.addbox .hint{font-size:10.5px;color:var(--fg-4)}
.addbox .grp{background:var(--inset);border:1px solid var(--hair);border-radius:var(--r);
 color:var(--fg);padding:9px 12px;font-size:11.5px;outline:none;width:190px;font-family:inherit}
.addbox .grp:focus{border-color:rgba(212,175,55,.5)}
.addbox .grp::placeholder{color:var(--fg-4)}
.addbox .ok{font-size:11px;color:var(--up)}
.resolved{margin-top:13px;border-top:1px solid var(--hair-2);padding-top:11px}
.rr{display:flex;align-items:center;gap:12px;padding:5px 0;font-size:11.5px}
.rr .h2{color:var(--gold-2);font-weight:600;min-width:150px}
.rr .ok2{color:var(--fg-2);font-size:10.5px;word-break:break-all}
.rr .ko{color:var(--down);font-size:11px}
table.clans{width:100%;border-collapse:collapse;font-size:12px}
table.clans th{text-align:right;font-size:8.5px;letter-spacing:.16em;text-transform:uppercase;
 color:var(--fg-4);font-weight:600;padding:7px 10px;border-bottom:1px solid var(--hair-2)}
table.clans th:nth-child(-n+2),table.clans td:nth-child(-n+2){text-align:left}
table.clans td{padding:8px 10px;border-bottom:1px solid var(--hair-2);text-align:right;font-variant-numeric:tabular-nums}
table.clans tr:last-child td{border-bottom:none}
table.clans .rk{color:var(--fg-4);width:28px}
table.clans .cn{font-weight:600;color:var(--gold-2)}
.empty{text-align:center;padding:70px 24px;color:var(--fg-3);font-size:12.5px;line-height:2}
.empty code{color:var(--gold-2);font-size:11.5px}
.empty .big{font-size:15px;color:var(--fg-2);display:block;margin-bottom:10px;font-weight:500}

/* ── page adresses ────────────────────────── */
.conv{border:1px solid rgba(212,175,55,.3);border-radius:var(--r);background:linear-gradient(180deg,rgba(212,175,55,.05),transparent);margin-bottom:30px}
.conv .ch{padding:15px 22px;border-bottom:1px solid var(--hair-2);display:flex;align-items:baseline;gap:12px}
.conv .ch .t{font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--gold-2)}
.conv .ch .s{font-size:10.5px;color:var(--fg-3)}
.crow{display:grid;grid-template-columns:minmax(0,1fr) 90px 74px minmax(0,1.1fr) 56px auto;
 gap:14px;align-items:center;padding:13px 22px;border-bottom:1px solid var(--hair-2)}
.crow:last-child{border-bottom:none}
.crow:hover{background:rgba(255,255,255,.017)}
.crow .cn{min-width:0}
.crow .cn .a{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crow .cn .b{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-4);margin-top:3px}
.crow .mcv{text-align:right;font-size:13px;font-weight:600}
.crow .cg{text-align:right;font-size:11.5px;font-weight:600}
.by{display:flex;flex-wrap:wrap;gap:5px}
.by span{font-size:10px;color:var(--gold-2);border:1px solid var(--gold-dim);padding:2px 8px;border-radius:var(--r);white-space:nowrap}
.by.multi span{background:rgba(212,175,55,.1)}
.ago{font-size:10px;color:var(--fg-4);text-align:right;letter-spacing:.04em}
.wsec{border:1px solid var(--hair);border-radius:var(--r);background:var(--surface);margin-bottom:10px}
.wsec .wh2{display:flex;align-items:center;gap:12px;padding:14px 22px;border-bottom:1px solid var(--hair-2)}
.wsec .wh2 .nm{font-size:13px;font-weight:600;color:var(--gold-2)}
.wsec .wh2 .ad{font-size:10px;color:var(--fg-4)}
.wsec .wh2 .cnt{margin-left:auto;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-3)}
.wsec .crow{grid-template-columns:minmax(0,1fr) 90px 74px 56px auto}
</style>
"""

MACROS = r"""
{% macro subtabs(cur) %}
<div class="subtabs">
  <a href="/wallets" class="{{ 'on' if cur=='detectes' }}">Classement</a>
  <a href="/flow" class="{{ 'on' if cur=='flow' }}">Flux</a>
  <a href="/adresses" class="{{ 'on' if cur=='adresses' }}">Mes adresses</a>
  <a href="/alertes" class="{{ 'on' if cur=='alertes' }}">Alertes</a>
</div>
{% endmacro %}

{% macro rsi3(p) %}
<div class="rsirow">
  {% for tf,v in [('15m',p.rsi_15m),('1h',p.rsi_1h),('4h',p.rsi_4h)] %}
  <div><div class="k">{{ tf }}</div><div class="v num" style="color:{{ rsicolor(v) }}">{{ v if v is not none else '—' }}</div></div>
  {% endfor %}
</div>
{% endmacro %}

{% macro flux(p) %}
<div class="rsirow">
  {% for tf,b,sl in [('5m',p.buys_m5,p.sells_m5),('1h',p.buys_h1,p.sells_h1),('6h',p.buys_h6,p.sells_h6),('24h',p.buys_h24,p.sells_h24)] %}
  {% set v = flowpct(b,sl) %}
  <div><div class="k">{{ tf }}</div>
    <div class="v num" style="color:{{ pctcolor(v) }};font-size:15px">{{ ('%.0f'|format(v)) ~ '%' if v is not none else '—' }}</div>
    <div class="bs num">{{ b }}/{{ sl }}</div></div>
  {% endfor %}
</div>
{% endmacro %}

{% macro targets(p) %}
<div class="tg">
  {% for k,v in [('T1',p.intel.t1),('T2',p.intel.t2),('T3',p.intel.t3)] %}
  <div><div class="k">{{ k }}</div><div class="v num">{{ v|fmt }}</div></div>
  {% endfor %}
</div>
{% endmacro %}

{% macro wallets_block(p) %}
{% if p.smart_detail %}
<div class="wl">
  {% for w in p.smart_detail %}
  <div class="w">
    <div class="wtop"><span class="nm">{{ w.label or w.short }}</span>
      <span class="ad mono">{{ w.short }}</span>
      <span class="cnt">{{ w.count }} pump{{ 's' if w.count > 1 }}</span></div>
    {% if w.coins %}
    <div class="why">{% for c in w.coins %}
      <span class="c">{{ c.symbol or c.name }} <b>+{{ c.pump_pct }}%</b> <i>{{ ('#' ~ c.entry_rank) if c.entry_rank else 'pos.' }}</i></span>
    {% endfor %}</div>
    {% else %}<div class="why"><span class="c" style="color:var(--fg-4)">historique non encore constitué</span></div>{% endif %}
  </div>
  {% endfor %}
</div>
{% elif p.wallets_available %}
<div class="wl"><div class="emptyline">Aucun wallet suivi n'est positionné sur ce coin.</div></div>
{% else %}
<div class="wl"><div class="emptyline">Couche wallet inactive — ajoute une clé Helius.</div></div>
{% endif %}
{% endmacro %}

{% macro row(p) %}
<div class="item" data-mint="{{ p.mint }}"
     data-grade="{{ p.grade }}" data-phase="{{ p.phase }}" data-chain="{{ p.chain }}"
     data-wallets="{{ p.smart_holders or 0 }}"
     style="--gc:{{ gradecolor(p.grade) }};--pc:{{ phasecolor(p.phase) }}">
  <div class="r">
    <div class="gr">{{ p.grade }}</div>
    <div class="id"><div class="n">{{ p.name }}</div><div class="s">{{ p.symbol }} · {{ p.score }}/{{ p.max_score }}</div></div>
    <div class="val"><div class="m num js-mc">{{ p.market_cap|fmt }}</div>
      <div class="c num js-chg {{ 'up' if p.chg_h1>=0 else 'down' }}">{{ '%+.1f'|format(p.chg_h1) }}%</div></div>
    <div class="ph js-phase"><i></i>{{ p.phase }}</div>
    <div class="acts">
      <button class="ic" title="Intel" onclick="tog(this,'intel')">{{ icon('intel') }}</button>
      <button class="ic" title="Smart wallets" onclick="tog(this,'wal')">{{ icon('wallet') }}</button>
      <a class="ic" title="Analyse" href="/coin?mint={{ p.mint }}">{{ icon('open') }}</a>
      <a class="ic" title="GMGN" href="{{ p.gmgn_url }}" target="_blank">{{ icon('chart') }}</a>
      <a class="ic" title="DexScreener" href="{{ p.dex_url }}" target="_blank">{{ icon('trend') }}</a>
      <button class="ic" title="Copier le contract" onclick="cp(this,'{{ p.mint }}')">{{ icon('copy') }}</button>
    </div>
  </div>
  {% if p.sources %}
  <div class="src" style="padding:0 20px 12px">
    {% if p.from_wallet %}<span class="s1 wsrc2">entrée wallet suivi</span>{% endif %}
    {% for s in p.sources %}
    <span class="s1 {{ 'kol' if s.kind=='suivi' else 'oc' }}">{{ s.name }}</span>
    {% endfor %}
  </div>
  {% endif %}
  <div class="panel intel" hidden>
    <div class="cols">
      <div>
        <div class="plan">
          <div class="pact">{{ p.intel.action }}</div>
          <div class="pgrid">
            <div><span class="k">Entry</span><span class="v num">{{ p.intel.zone }}</span></div>
            <div><span class="k">Cut</span><span class="v num cut">{{ p.intel.cut_mc }}</span></div>
            <div><span class="k">T1</span><span class="v num">{{ p.intel.t1|fmt }}</span></div>
            <div><span class="k">T2</span><span class="v num">{{ p.intel.t2|fmt }}</span></div>
            <div><span class="k">T3</span><span class="v num">{{ p.intel.t3|fmt }}</span></div>
          </div>
          <div class="pwhy">{{ p.intel.pourquoi }}</div>
          <details class="pdetail"><summary>Le détail</summary>
            <div class="entry">{{ p.intel.entry }}</div>
            <div class="cutline"><b>Cut</b> — {{ p.intel.cut }}</div>
          </details>
        </div>
      </div>
      <div>
        <span class="lab">RSI</span>
        {{ rsi3(p) }}
        <span class="lab" style="margin-top:16px">Flux · % d'achats</span>
        {{ flux(p) }}
        <div class="stats">
          <div class="st"><span class="k">Vol 24h</span><span class="v num">{{ p.vol_h24|fmt }}</span></div>
          <div class="st"><span class="k">Vol 1h</span><span class="v num">{{ p.vol_h1|fmt }}</span></div>
          <div class="st"><span class="k">Liquidité</span><span class="v num">{{ p.liquidity_usd|fmt }}</span></div>
          <div class="st"><span class="k">Âge</span><span class="v num">{{ '%.1f'|format(p.age_hours) }}h</span></div>
          <div class="st"><span class="k">24h</span><span class="v num {{ 'up' if p.chg_h24>=0 else 'down' }}">{{ '%+.0f'|format(p.chg_h24) }}%</span></div>
          <div class="st"><span class="k">Holders</span><span class="v num">{{ p.holders if p.holders else '—' }}</span></div>
        </div>
        {% set fails = p.criteria|rejectattr('ok')|list %}
        <div class="miss">
          <span class="score"><b>{{ p.score }}/{{ p.max_score }}</b></span>
          {% if fails %}<span class="k">manque</span>{% for c in fails %}<span class="t" title="{{ c.detail }}">{{ c.name }}</span>{% endfor %}{% endif %}
        </div>
      </div>
    </div>
  </div>
  <div class="panel wal" hidden>
    <span class="lab">Smart wallets positionnés</span>
    {{ wallets_block(p) }}
  </div>
</div>
{% endmacro %}

{% macro flowtable(flow) %}
{% if flow and flow.available %}
<div class="flow">
  <div class="h"><div class="t">Whale Flow</div>
    <div class="s">Net USD flow — différence entre photos de soldes on-chain ·
      {{ flow.holders }} holders{% if flow.holders_delta %} ({{ '%+d'|format(flow.holders_delta) }} depuis la dernière photo){% endif %} ·
      {{ flow.snapshots }} photos</div>
    {% if flow.signal %}<div class="sig">{{ flow.signal }}</div>{% endif %}
  </div>
  <div class="tot">{% for w in ['24h','7d','30d'] %}
    <div><div class="k">Net {{ w }}</div>
      {% if flow.covered.get(w) %}
        <div class="v num" style="color:{{ flowcolor(flow.totals[w]) }}">{{ flow.totals[w]|flowfmt }}</div>
      {% else %}<div class="v num" style="color:var(--fg-4)">—</div>
        <div class="na">pas encore d'historique</div>{% endif %}
    </div>
  {% endfor %}</div>
  <table><thead><tr><th>Catégorie</th><th>Wallets</th><th>Net 24h</th><th>Net 7j</th><th>Net 30j</th></tr></thead><tbody>
  {% for t in ['Whale','Shark','Dolphin','Fish'] %}{% set d = flow.tiers[t] %}
    <tr><td class="tier">{{ t }}<span>{{ flow.labels[t] }}</span></td>
      <td class="num">{{ d.count }}</td>
      {% for w in ['24h','7d','30d'] %}
      <td class="num" style="color:{{ flowcolor(d[w]) if flow.covered.get(w) else 'var(--fg-4)' }}">{{ d[w]|flowfmt if flow.covered.get(w) else '—' }}</td>
      {% endfor %}</tr>
  {% endfor %}</tbody></table>
</div>
{% elif flow %}
<div class="flow"><div class="h"><div class="t">Whale Flow</div>
  <div class="s">{{ flow.reason or 'données insuffisantes' }}</div></div></div>
{% endif %}
{% endmacro %}
"""

SCRIPT = r"""
<script>
function tog(b,k){var it=b.closest('.item'),t=it.querySelector('.panel.'+k);
 it.querySelectorAll('.panel').forEach(function(p){if(p!==t)p.hidden=true});
 t.hidden=!t.hidden;it.classList.toggle('open',!t.hidden);
 it.querySelectorAll('.ic').forEach(function(x){x.classList.remove('on')});
 if(!t.hidden)b.classList.add('on');}
function cp(b,m){navigator.clipboard.writeText(m).then(function(){
 var h=b.innerHTML;b.classList.add('ok');b.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
 setTimeout(function(){b.classList.remove('ok');b.innerHTML=h},1300)});}
function fm(v){var s=v<0?'-':'';v=Math.abs(v);
 if(v>=1e6)return s+'$'+(v/1e6).toFixed(2)+'M';
 if(v>=1e3)return s+'$'+(v/1e3).toFixed(0)+'K';return s+'$'+v.toFixed(0);}
function majLabel(ts){if(!ts)return '—';var d=Math.max(0,Date.now()/1000-ts);
 if(d<60)return 'màj il y a '+Math.round(d)+'s';
 if(d<3600)return 'màj il y a '+Math.round(d/60)+'min';
 return 'màj il y a '+(d/3600).toFixed(1)+'h';}
async function tick(){try{
 var d=await(await fetch('/api/pairs')).json(),seen={},rows=document.querySelectorAll('#rows .item[data-grade]:not([data-grade="—"])');
 var lm=document.getElementById('lastmaj'); if(lm)lm.textContent=majLabel(d.updated);
 var np=document.getElementById('npairs'); if(np)np.textContent=d.pairs.length;
 d.pairs.forEach(function(p){seen[p.mint]=1;
  var it=document.querySelector('.item[data-mint="'+p.mint+'"]');if(!it)return;
  var m=it.querySelector('.js-mc');if(m&&m.textContent!==fm(p.market_cap)){m.textContent=fm(p.market_cap);
   m.style.color='var(--gold-2)';setTimeout(function(){m.style.color=''},600);}
  var c=it.querySelector('.js-chg');if(c){c.textContent=(p.chg_h1>=0?'+':'')+p.chg_h1.toFixed(1)+'%';
   c.className='c num js-chg '+(p.chg_h1>=0?'up':'down');}
  var f=it.querySelector('.js-phase');if(f)f.innerHTML='<i></i>'+p.phase;});
 if(rows.length!==d.pairs.length){location.reload();return;}
 for(var i=0;i<rows.length;i++)if(!seen[rows[i].getAttribute('data-mint')]){location.reload();return;}
}catch(e){}}
var CHAIN_COLORS={all:['#d4af37','#e8c86a'],solana:['#9945FF','#B980FF'],
 robinhood:['#CCFF00','#E2FF66'],ethereum:['#627EEA','#8DA2F0'],base:['#4FA9FF','#8FCEFF']};
function paint(c){var v=CHAIN_COLORS[c]||CHAIN_COLORS.all,r=document.documentElement.style;
 r.setProperty('--gold',v[0]); r.setProperty('--gold-2',v[1]);
 r.setProperty('--gold-dim','color-mix(in srgb,'+v[0]+' 14%,transparent)');
 // les contours des cartes prennent la teinte de la chaine
 r.setProperty('--hair','color-mix(in srgb,'+v[0]+' 26%,transparent)');
 r.setProperty('--hair-2','color-mix(in srgb,'+v[0]+' 13%,transparent)');}
var curChain='all';
function applyChain(c){curChain=c; paint(c);
 var cur=document.querySelector('.chips .chip.on');
 applyFilter(cur?cur.getAttribute('data-f'):'tous');}
document.addEventListener('click',function(e){
 var b=e.target.closest('.chains .ch'); if(!b)return;
 document.querySelectorAll('.chains .ch').forEach(function(x){x.classList.remove('on');});
 b.classList.add('on'); var c=b.getAttribute('data-c');
 try{sessionStorage.setItem('mscan_chain',c);}catch(_){}
 applyChain(c);});
function applyFilter(f){
 var rows=document.querySelectorAll('#rows .item'),shown=0;
 rows.forEach(function(it){
  var g=it.getAttribute('data-grade'),ph=it.getAttribute('data-phase'),
      ch=it.getAttribute('data-chain')||'solana',
      w=parseInt(it.getAttribute('data-wallets')||'0',10),ok=true;
  if(curChain!=='all'&&ch!==curChain){it.hidden=true;return;}
  var vl=it.getAttribute('data-veille')==='1';
  if(f!=='veille'&&vl){it.hidden=true;return;}   // la veille a son propre onglet
  if(f==='veille')    ok = vl;
  else if(f==='conv') ok = w>=2;
  else if(f==='top')  ok = (g==='A+'||g==='A'||g==='A-');
  else if(f==='wallet')ok = w>=1;
  else if(f==='running')  ok = ph==='Running';
  else if(f==='early')    ok = ph==='Early';
  else if(f==='retest')   ok = ph==='Retest';
  else if(f==='compress') ok = ph==='Compressing';
  it.hidden=!ok; if(ok)shown++;});
 var n=document.getElementById('nores'); if(n)n.hidden=shown>0;
 var r=document.getElementById('rows'); if(r)r.hidden=shown===0;}
document.addEventListener('click',function(e){
 var b=e.target.closest('.chips .chip'); if(!b)return;
 document.querySelectorAll('.chips .chip').forEach(function(x){x.classList.remove('on');});
 b.classList.add('on');
 try{sessionStorage.setItem('mscan_filter',b.getAttribute('data-f'));}catch(_){}
 applyFilter(b.getAttribute('data-f'));});
// on retrouve le filtre choisi apres un rafraichissement automatique
(function(){try{var c=sessionStorage.getItem('mscan_chain');
 if(c&&c!=='all'){var cb=document.querySelector('.chains .ch[data-c="'+c+'"]');
  if(cb){document.querySelectorAll('.chains .ch').forEach(function(x){x.classList.remove('on');});
   cb.classList.add('on');curChain=c;paint(c);}}}catch(_){}})();
(function(){try{var f=sessionStorage.getItem('mscan_filter');
 if(f&&f!=='tous'){var b=document.querySelector('.chips .chip[data-f="'+f+'"]');
  if(b){document.querySelectorAll('.chips .chip').forEach(function(x){x.classList.remove('on');});
   b.classList.add('on');applyFilter(f);}}}catch(_){}})();
async function gauge(){try{
 var d=await(await fetch('/api/pairs')).json(),g=document.getElementById('gauge');
 if(!g)return 15000;
 var p=d.progress||{};
 if(d.scanning){g.hidden=false;
  document.getElementById('gfill').style.width=(p.pct||0)+'%';
  document.getElementById('gpct').textContent=(p.pct||0)+'%';
  document.getElementById('gphase').textContent=p.phase||'Scan en cours';
  document.getElementById('gdetail').textContent=p.detail||'';
  return 2000;}                       // pendant le scan : rafraichi vite
 g.hidden=true;return 15000;}catch(e){return 15000;}}
(function loop(){gauge().then(function(ms){setTimeout(loop,ms||15000);});})();
if(document.querySelector('.rows'))setInterval(tick,15000);
</script>
"""

CHROME = r"""
<div class="top">
  <div class="brandrow">
    <a href="/" style="display:flex;align-items:center;gap:12px"><img class="mark" alt="" src="data:image/png;base64,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"><span class="wordmark">MSCAN</span></a>
    <div class="status">
      {% if meta is defined and meta.mode=='demo' %}<span class="demo">Démo</span>{% endif %}
      {% if pairs is defined %}<span class="live {{ 'stale' if meta.stale }}"><i></i><span id="npairs">{{ pairs|length }}</span> paires · <span id="lastmaj">—</span>{% if meta.stale %} <b>scan précédent</b>{% endif %}</span>{% endif %}
    </div>
  </div>
  <nav class="nav">
    <a href="/" class="{{ 'on' if active=='radar' }}">Radar</a>
    <a href="/holdings" class="{{ 'on' if active=='holdings' }}">Holdings</a>
    <a href="/wallets" class="{{ 'on' if active in ('wallets','follow','flow') }}">Wallets</a>
    <a href="/coin" class="{{ 'on' if active=='search' }}">Recherche</a>
  </nav>
</div>
"""

_FAVICON = '<link rel="icon" type="image/png" href="data:image/png;base64,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">'

_H = ("<!doctype html><html lang=fr><head><meta charset=utf-8>"
      "<meta name=viewport content='width=device-width,initial-scale=1'>"
      + _FAVICON
      + "<script>(function(){var C={solana:['#9945FF','#B980FF'],"
        "robinhood:['#CCFF00','#E2FF66'],ethereum:['#627EEA','#8DA2F0'],"
        "base:['#4FA9FF','#8FCEFF']};try{var c=sessionStorage.getItem('mscan_chain');"
        "var v=C[c];if(v){var r=document.documentElement.style;"
        "r.setProperty('--gold',v[0]);r.setProperty('--gold-2',v[1]);"
        "r.setProperty('--gold-dim','color-mix(in srgb,'+v[0]+' 14%,transparent)');"
        "r.setProperty('--hair','color-mix(in srgb,'+v[0]+' 26%,transparent)');"
        "r.setProperty('--hair-2','color-mix(in srgb,'+v[0]+' 13%,transparent)');}}"
        "catch(e){}})();</script>")

PAGE_RADAR = (_H + "<title>MSCAN · Radar</title>" + STYLE + "</head><body>"
              + MACROS + CHROME + r"""
<div class="page">
  {% if not helius and meta.mode!='demo' %}<div class="notice">Couche wallet inactive — ajoute <code>HELIUS_API_KEY</code> dans <code>.env</code> pour les smart wallets et le whale flow.</div>{% endif %}
  <div class="gauge" id="gauge" {% if not meta.scanning %}hidden{% endif %}>
    <div class="gl"><span class="gp" id="gphase">{{ prog.phase or 'Scan en cours' }}</span>
      <span class="gd" id="gdetail">{{ prog.detail or '' }}</span>
      <span class="gn" id="gpct">{{ prog.pct or 0 }}%</span></div>
    <div class="gbar"><div class="gfill" id="gfill" style="width:{{ prog.pct or 0 }}%"></div></div>
  </div>

  <div class="chains">
    <button class="ch on" data-c="all" style="--cc:var(--gold)">Toutes <i>{{ chains.all }}</i></button>
    {% for cid, m in chainmeta.items() %}
    <button class="ch" data-c="{{ cid }}" style="--cc:{{ m.color }}">
      <span class="clogo">{{ icon('c_' ~ cid) }}</span>{{ m.label }} <i>{{ chains[cid] }}</i></button>
    {% endfor %}
  </div>

  <div class="chips">
    <button class="chip on" data-f="tous">Tous <i>{{ counts.tous }}</i></button>
    <button class="chip gold" data-f="conv">Convergence <i>{{ counts.conv }}</i></button>
    <button class="chip gold" data-f="veille">Early <i>{{ counts.veille }}</i></button>
    <button class="chip gold" data-f="top">A+ / A / A- <i>{{ counts.top }}</i></button>
    <button class="chip" data-f="wallet">Smart wallet <i>{{ counts.wallet }}</i></button>
    <button class="chip" data-f="running">Running <i>{{ counts.running }}</i></button>
    <button class="chip" data-f="early">Jeune <i>{{ counts.early }}</i></button>
    <button class="chip" data-f="retest">Retest <i>{{ counts.retest }}</i></button>
    <button class="chip" data-f="compress">Compressing <i>{{ counts.compress }}</i></button>
  </div>

  {% if pairs or extra or veille %}
  <div class="rows" id="rows">
    {% for p in pairs %}{{ row(p) }}{% endfor %}
    {% for c in extra %}
    <div class="item" data-mint="{{ c.mint }}" data-grade="—" data-phase="—"
         data-chain="solana" data-wallets="{{ c.by|length }}">
      <div class="r" style="grid-template-columns:38px minmax(0,1fr) 96px auto">
        <div class="gr" style="--gc:var(--gold);color:var(--gold)">{{ c.by|length }}×</div>
        <div class="id"><div class="n">{{ c.name }}</div>
          <div class="s">{{ c.symbol }} · hors radar · {{ c.groups }}</div></div>
        <div class="val"><div class="m num">{{ c.mc|fmt }}</div>
          <div class="c num {{ 'up' if c.chg_h1>=0 else 'down' }}">{{ '%+.1f'|format(c.chg_h1) }}%</div></div>
        <div class="acts">
          <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
          <a class="ic" title="GMGN" href="{{ gmgnlink(c.chain, c.mint) }}" target="_blank">{{ icon('chart') }}</a>
          <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
          <button class="ic" title="Copier" onclick="cp(this,'{{ c.mint }}')">{{ icon('copy') }}</button>
        </div>
      </div>
    </div>
    {% endfor %}
    {% for c in veille %}
    <div class="item" data-mint="{{ c.mint }}" data-grade="—" data-phase="—"
         data-chain="{{ c.chain or 'solana' }}" data-wallets="0" data-veille="1">
      <div class="r" style="grid-template-columns:38px minmax(0,1fr) 96px auto">
        <div class="gr" style="--gc:#7cc4ff;color:#7cc4ff;font-size:9px;letter-spacing:.1em">
          {{ 'REPLI' if c.pret else ('IMPULS' if c.impulsion_at else 'VEILLE') }}</div>
        <div class="id"><div class="n">{{ c.symbol or '?' }}</div>
          <div class="s">{{ 'sous surveillance' if not c.impulsion_at else 'impulsion repérée' }}
            · {{ c.source or 'veille' }}{% if c.liq %} · liq {{ c.liq|fmt }}{% endif %}</div></div>
        <div class="val"><div class="m num">{{ c.mc|fmt }}</div>
          <div class="c num {{ 'up' if (c.chg_h1 or 0) >= 0 else 'down' }}">{{ '%+.1f'|format(c.chg_h1 or 0) }}%</div></div>
        <div class="acts">
          <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
          <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
  <div class="nores" id="nores" hidden>Aucun coin dans cette catégorie pour l'instant.</div>
  {% else %}<div class="empty"><span class="big">Premier scan en cours</span>
    Les paires apparaissent dès qu'elles sont notées — la barre ci-dessus suit l'avancement.<br>
    Le scanner continue de tourner en arrière-plan.</div>{% endif %}
</div>""" + SCRIPT + "</body></html>")

PAGE_SEARCH = (_H + "<title>MSCAN · Recherche</title>" + STYLE + "</head><body>"
               + CHROME + r"""
<div class="page"><div class="hero">
  <div class="eyebrow">Analyse à la demande</div>
  <h2>Analyser un coin</h2>
  <p>Colle un contract address Solana — grade, phase, intel, RSI,<br>smart wallets positionnés et whale flow.</p>
  <form class="sbox" action="/coin" method="get">
    <input name="mint" placeholder="Contract address (mint)…" spellcheck="false" autocomplete="off" autofocus>
    <button>""" + ICONS["search"] + r""" Analyser</button>
  </form>
</div></div></body></html>""")

PAGE_WALLETS = (_H + "<title>MSCAN · Wallets</title>" + STYLE + "</head><body>"
                + MACROS + CHROME + r"""
<div class="page">
  {{ subtabs('detectes') }}
  <div class="sechead"><h1>Smart wallets</h1>
    <span class="sub">classés par pertinence · récurrence sur les pumps, précocité d'entrée, activité récente</span></div>
  {% if wallets %}
    {% for w in wallets %}
    <details class="wcard">
      <summary class="wh">
        <span class="wgrade g{{ w.grade|replace('+','p')|replace('-','m') }}">{{ w.grade }}</span>
        <div class="wid">
          <div class="nm">{{ w.label or w.short }}{% if w.group %}<span class="wsrc">{{ w.group }}</span>{% endif %}</div>
          <div class="ad mono">{{ w.address }}</div>
        </div>
        <div class="met"><div class="k">Pumps</div><div class="v num">{{ w.count }}</div></div>
        <div class="met"><div class="k">Pump moyen</div><div class="v num up">+{{ w.avg_pump }}%</div></div>
        <div class="met"><div class="k">Entrée</div><div class="v" style="font-size:11px">{{ ('early #' ~ w.avg_rank) if w.avg_rank else 'position' }}</div></div>
        <div class="wlast">
          {% for c in w.recent %}<span class="tk">{{ c.symbol or c.name }}<b>{{ money(c.mc) }}</b></span>{% endfor %}
        </div>
        <span class="wchev">{{ icon('chev') }}</span>
      </summary>
      <div class="wb">
        <span class="lab">Présent sur ces coins qui ont pumpé · {{ w.count }} au total</span>
        {% for c in w.coins %}
        <div class="coin">
          <div class="cn">{{ c.symbol or c.name }}{% if c.name and c.symbol %}<small>{{ c.name }}</small>{% endif %}</div>
          <div class="cmc num">{{ money(c.mc) }}</div>
          <div class="p num">+{{ c.pump_pct }}%</div>
          <div class="rk num">{{ ('entré #' ~ c.entry_rank) if c.entry_rank else 'gros porteur' }}</div>
          <div class="acts" style="justify-content:flex-end">
            {% if c.mint %}
            <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
            <a class="ic" title="GMGN" href="{{ gmgnlink(c.chain, c.mint) }}" target="_blank">{{ icon('chart') }}</a>
            <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
            {% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
    </details>
    {% endfor %}
  {% else %}
    <div class="empty"><span class="big">Aucun wallet détecté pour l'instant</span>
      La découverte automatique lit les coins qui viennent de percer,<br>
      relève leurs premiers acheteurs et retient ceux qui reviennent sur plusieurs pumps.<br><br>
      <code>python -m mmscanner.discover_wallets</code></div>
  {% endif %}
</div></body></html>""")


PAGE_HOLDINGS = (_H + "<title>MSCAN · Holdings</title>" + STYLE + "</head><body>"
                 + MACROS + CHROME + r"""
<div class="page">
  <div class="sechead"><h1>Ce qu'ils tiennent</h1>
    <span class="sub">{{ coins|length }} coins · {{ nwallets }} portefeuilles lus{% if updated %} · relevé {{ updated|ago }}{% endif %}</span></div>

  <div class="chips">
    <a class="chip {{ 'on' if tri=='convergence' }}" href="/holdings">Convergence</a>
    <a class="chip {{ 'on' if tri=='dollars' }}" href="/holdings?tri=dollars">Dollars détenus</a>
    <a class="chip {{ 'on' if tri=='mc' }}" href="/holdings?tri=mc">Market cap</a>
    <a class="chip {{ 'on' if tri=='conviction' }}" href="/holdings?tri=conviction">Conviction</a>
    <a class="chip {{ 'on' if tri=='solo' }}" href="/holdings?tri=solo">Tenu par 1</a>
    <a class="chip gold {{ 'on' if tri=='fomo' }}" href="/holdings?tri=fomo">FOMO</a>
  </div>

  {% if coins %}
  <div class="rows">
    {% for c in coins %}
    <div class="item" data-mint="{{ c.mint }}">
      <div class="r" style="grid-template-columns:52px minmax(0,1fr) 120px 108px auto">
        <div class="gr" style="--gc:var(--gold);color:{{ 'var(--gold)' if c.holders > 1 else 'var(--fg-3)' }}">{{ c.holders }}</div>
        <div class="id">
          <div class="n">{{ c.symbol }}{% if c.grade %} <span class="tag" style="color:{{ gradecolor(c.grade) }};border-color:{{ gradecolor(c.grade) }}44">{{ c.grade }}</span>{% endif %}{% if c.dip %} <span class="tag" style="color:#7cc4ff;border-color:rgba(124,196,255,.4)">CONVICTION</span>{% endif %}{% if c.neuf %} <span class="tag neuf" data-neuf="{{ c.mint }}" title="Cliquer pour marquer comme vu" style="color:#4ade80;border-color:rgba(74,222,128,.4);cursor:pointer">NOUVEAU</span>{% endif %}</div>
          <div class="s">{{ c.name }} ·
            {% for g in c.groupes %}{{ g.nom }}{% if g.n > 1 %} ×{{ g.n }}{% endif %}{% if not loop.last %} · {% endif %}{% endfor %}
          </div>
        </div>
        <div class="val">
          <div class="m num">{{ c.mc|fmt }}</div>
          <div class="c num {{ 'up' if (c.chg_h24 or 0) >= 0 else 'down' }}">{{ '%+.1f'|format(c.chg_h24 or 0) }}%</div>
        </div>
        <div class="val">
          <div class="m num" style="font-size:12px;color:var(--gold-2)">{{ c.value_usd|fmt }}</div>
          <div class="c" style="font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-4)">détenu</div>
        </div>
        <div class="acts">
          <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
          <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty"><span class="big">Portefeuilles en cours de lecture</span>
    Les avoirs sont relus par vagues et gardés 3 h en cache.<br>
    Reviens dans quelques minutes — ou ajoute des adresses dans <b>Mes adresses</b>.</div>
  {% endif %}

  <div class="explain" style="margin-top:22px;margin-bottom:0">Ce que les adresses suivies <b>portent en ce moment</b>, Solana et EVM confondues.
    Le chiffre de gauche est le nombre de wallets sur le coin ; une position compte à partir de $200, en dessous c'est de la poussière.
    <b>Conviction</b> marque un recul de 10 % ou plus sur 24 h alors qu'ils tiennent toujours — tu entres sous eux.
    <b>Nouveau</b> signale un achat des 48 dernières heures. La note (A+, B…) est celle du radar quand le coin y est passé.
    Écartés d'office : majors et actions, capitalisation au-dessus d'un milliard, liquidité sous $25K, volume fabriqué, et tout token
    dont l'émetteur garde le pouvoir de geler ou d'imprimer.</div>
</div>
<script>
// L'etiquette NOUVEAU disparait des qu'on a regarde le coin — au clic sur
// l'etiquette, ou en ouvrant la ligne. Garde en memoire du navigateur, donc
// elle ne revient pas au rafraichissement.
(function(){
 var vus={};
 try{vus=JSON.parse(localStorage.getItem('mscan_vus')||'{}');}catch(_){}
 function nettoyer(){
  document.querySelectorAll('.tag.neuf').forEach(function(b){
   if(vus[b.getAttribute('data-neuf')])b.remove();});}
 nettoyer();
 document.addEventListener('click',function(e){
  var b=e.target.closest('.tag.neuf'), ligne=e.target.closest('.item[data-mint]');
  var mint=b?b.getAttribute('data-neuf'):(ligne?ligne.getAttribute('data-mint'):null);
  if(!mint)return;
  vus[mint]=1;
  try{localStorage.setItem('mscan_vus',JSON.stringify(vus));}catch(_){}
  nettoyer();});
})();
</script>
</body></html>""")


PAGE_ALERTES = (_H + "<title>MSCAN · Alertes</title>" + STYLE + "</head><body>"
                + MACROS + CHROME + r"""
<div class="page">
  {{ subtabs('alertes') }}
  <div class="sechead"><h1>Alertes Telegram</h1>
    <span class="sub">chaque setup A+ poussé sur ton téléphone dès sa détection</span></div>

  {% if tg.ok %}
  <div class="explain" style="border-left-color:var(--up)">
    <b>Actif.</b> Les A+ partent vers la conversation <code>{{ tg.chat }}</code> dès
    qu'ils sont notés, sans attendre la fin du scan. Un même coin n'est pas réalerté
    avant {{ tg.cooldown_h }} h, sauf s'il change de grade.<br>
    Depuis Telegram : <code>/top</code> <code>/aplus</code> <code>/solana</code>
    <code>/wallets</code> <code>/coin &lt;adresse&gt;</code> <code>/etat</code>.
  </div>
  {% else %}
  <div class="explain">
    <b>Trois étapes.</b><br>
    1. Sur Telegram, écris à <b>@BotFather</b> → <code>/newbot</code>, choisis un nom.
       Il te donne un token du type <code>8123456789:AAH...</code><br>
    2. <b>Envoie <code>/start</code> à ton nouveau bot</b> — sans ça il n'a pas le droit de t'écrire.<br>
    3. Colle le token ci-dessous. Le numéro de conversation est trouvé tout seul.
  </div>
  {% endif %}

  <form class="addbox" method="post" action="/alertes">
    <div class="hd"><span class="t">Configuration</span>
      <span class="h">token BotFather · le chat est détecté automatiquement</span></div>
    <div class="bd">
      <input class="tgin" type="text" name="token" spellcheck="false" autocomplete="off"
             placeholder="8123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" value="{{ token_masque }}">
      <input class="tgin" type="text" name="chat_id" spellcheck="false" autocomplete="off"
             placeholder="numéro de conversation (laisse vide : détection auto)" value="{{ tg.chat }}">
      <div class="ft">
        <button type="submit" name="action" value="save">Enregistrer</button>
        <button type="submit" name="action" value="test">Envoyer un test</button>
        {% if msg %}<span class="ok">{{ msg }}</span>{% endif %}
        {% if err %}<span class="ko">{{ err }}</span>{% endif %}
      </div>
    </div>
  </form>

  <div class="explain" style="margin-top:18px">
    <b>Même ordinateur éteint ?</b> Il faut que le scan tourne ailleurs.
    L'option <b>gratuite</b> est déjà prête : <code>.github/workflows/alertes.yml</code>
    fait scanner GitHub toutes les 30&nbsp;min et pousse les A+ ici même. Il te faut un dépôt
    GitHub et quatre secrets à y coller — la marche à suivre est dans
    <code>DEPLOY-BOT.md</code>, à côté de l'application.<br>
    Pour un scan continu toutes les 10&nbsp;min et un bot qui répond aux commandes,
    il faut un hébergement payé (Railway ~5&nbsp;$/mois, ou un VPS via le <code>Dockerfile</code>).
  </div>
</div></body></html>""")

PAGE_COIN = (_H + "<title>MSCAN · Analyse</title>" + STYLE + "</head><body>"
             + MACROS + CHROME + r"""
<div class="page">
  <a class="back" href="/">← Radar</a>
  {% if err %}
    <div class="notice">{{ err }}<br><span class="mono" style="color:var(--fg-4);font-size:10.5px">{{ mint }}</span></div>
  {% else %}
  <div class="card" style="--gc:{{ gradecolor(p.grade) }};--pc:{{ phasecolor(p.phase) }}">
    <div class="chead">
      <div class="g">{{ p.grade }}</div>
      <div class="t"><div class="n">{{ p.name }}</div><div class="s">{{ p.symbol }}/SOL · {{ p.score }}/{{ p.max_score }}</div></div>
      <div class="r"><div class="m num">{{ p.market_cap|fmt }}</div>
        <div class="x"><span class="num {{ 'up' if p.chg_h1>=0 else 'down' }}">{{ '%+.1f'|format(p.chg_h1) }}% 1H</span>
          <span class="ph"><i></i>{{ p.phase }}</span></div></div>
    </div>
    <div class="cbody">
      <div class="cols">
        <div>
          <div class="plan">
          <div class="pact">{{ p.intel.action }}</div>
          <div class="pgrid">
            <div><span class="k">Entry</span><span class="v num">{{ p.intel.zone }}</span></div>
            <div><span class="k">Cut</span><span class="v num cut">{{ p.intel.cut_mc }}</span></div>
            <div><span class="k">T1</span><span class="v num">{{ p.intel.t1|fmt }}</span></div>
            <div><span class="k">T2</span><span class="v num">{{ p.intel.t2|fmt }}</span></div>
            <div><span class="k">T3</span><span class="v num">{{ p.intel.t3|fmt }}</span></div>
          </div>
          <div class="pwhy">{{ p.intel.pourquoi }}</div>
          <details class="pdetail"><summary>Le détail</summary>
            <div class="entry">{{ p.intel.entry }}</div>
            <div class="cutline"><b>Cut</b> — {{ p.intel.cut }}</div>
          </details>
        </div>
        </div>
        <div>
          <span class="lab">RSI</span>
          {{ rsi3(p) }}
          <span class="lab" style="margin-top:16px">Flux · % d'achats</span>
          {{ flux(p) }}
          <div class="stats">
            <div class="st"><span class="k">Vol 24h</span><span class="v num">{{ p.vol_h24|fmt }}</span></div>
            <div class="st"><span class="k">Vol 1h</span><span class="v num">{{ p.vol_h1|fmt }}</span></div>
            <div class="st"><span class="k">Liquidité</span><span class="v num">{{ p.liquidity_usd|fmt }}</span></div>
            <div class="st"><span class="k">Âge</span><span class="v num">{{ '%.1f'|format(p.age_hours) }}h</span></div>
            <div class="st"><span class="k">24h</span><span class="v num {{ 'up' if p.chg_h24>=0 else 'down' }}">{{ '%+.0f'|format(p.chg_h24) }}%</span></div>
            <div class="st"><span class="k">Holders</span><span class="v num">{{ p.holders if p.holders else '—' }}</span></div>
          </div>
        </div>
      </div>
      <div class="sect">
        <span class="lab">Smart wallets positionnés{% if p.smart_accumulating %} · {{ p.smart_accumulating }} en accumulation (&lt; 6h){% endif %}</span>
        {{ wallets_block(p) }}
      </div>
      <div class="addr">
        <span class="a mono">{{ p.mint }}</span>
        <button class="ic" title="Copier" onclick="cp(this,'{{ p.mint }}')">""" + ICONS["copy"] + r"""</button>
        <a class="ic" title="GMGN" href="{{ p.gmgn_url }}" target="_blank">""" + ICONS["chart"] + r"""</a>
        <a class="ic" title="DexScreener" href="{{ p.dex_url }}" target="_blank">""" + ICONS["trend"] + r"""</a>
      </div>
    </div>
  </div>
  {{ flowtable(flow) }}
  {% endif %}
</div>""" + SCRIPT + "</body></html>")


PAGE_FOLLOW = (_H + "<title>MSCAN · Adresses</title>" + STYLE + "</head><body>"
               + MACROS + CHROME + r"""
<div class="page">
  {{ subtabs('adresses') }}
  {% if clanstate and clanstate.total %}
  <div class="explain">
    <b>Clans FOMO</b> — {{ clanstate.clans }} rosters, {{ clanstate.total }} membres ·
    {{ clanstate.resolus }} wallets trouvés · {{ clanstate.restants }} en attente
    {%- if clanstate.sans_wallet %} · {{ clanstate.sans_wallet }} sans wallet vérifié{% endif %}.
    {% if clanstate.restants %}<br>La résolution repart toute seule dès que la clé FomoScan a du crédit
    (2 500 CU par membre). Sinon, remplis <code>clans/A_REMPLIR.txt</code> depuis l'app FOMO
    puis lance <code>python add_clan.py --remplir</code>.{% endif %}
  </div>
  {% endif %}
  <form class="addbox" method="post" action="/adresses">
    <div class="hd"><span class="t">Mes adresses</span>
      <span class="h">une adresse par ligne · label optionnel après un espace</span></div>
    <div class="bd">
      <textarea name="addresses" spellcheck="false" placeholder="9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM   Cupsey&#10;7phbaH6UeyFJmjdPoyiaQAHX3B1gRtnCVZL7HZNtbonk   Euris">{{ raw }}</textarea>
      <div class="ft"><button type="submit" name="action" value="save">Enregistrer</button>
        <button type="submit" name="action" value="restore" class="ghost"
                title="Remet la liste telle qu'elle etait avant la derniere sauvegarde">Annuler la derniere modif</button>
        {% if saved is not none %}<span class="ok">{{ saved }} adresse{{ 's' if saved > 1 }} enregistrée{{ 's' if saved > 1 }} — recharge pour voir leurs achats</span>
        {% else %}<span class="hint">Leurs achats des 72 dernières heures s'affichent ci-dessous. &nbsp;·&nbsp; Où trouver un wallet : FOMO → profil du trader → adresse, ou <b>fomoscan.sh</b>.</span>{% endif %}</div>
    </div>
  </form>

  <form class="addbox" method="post" action="/adresses">
    <div class="hd"><span class="t">Ajouter par handle FOMO</span>
      <span class="h">@pseudo — un par ligne · résolu en wallet vérifié</span></div>
    <div class="bd">
      <textarea name="handles" spellcheck="false" style="min-height:78px"
        placeholder="@MINHxDYNASTY&#10;@autre_trader&#10;@encore_un"></textarea>
      <div class="ft">
      <input class="grp" name="groupe" placeholder="Groupe (ex : Dabal, Grand)" maxlength="24">
      <button type="submit">Résoudre &amp; ajouter</button>
      {% if resolved %}<span class="ok">{{ resolved.added }} adresse(s) ajoutée(s)</span>
      {% else %}<span class="hint">Les @handles sont résolus via les fiches publiques de fomoscan.sh — gratuit, Solana et Ethereum.</span>{% endif %}
      </div>
      {% if resolved %}<div class="resolved">
        {% for r in resolved.res %}
        <div class="rr"><span class="h2">@{{ r.handle }}</span>
          {% if r.ok %}<span class="mono ok2">{{ r.solana or '—' }}</span>
            {% if r.ethereum %}<span class="mono" style="color:var(--fg-4);font-size:10px">{{ r.ethereum }}</span>{% endif %}
          {% else %}<span class="ko">{{ r.reason }}</span>{% endif %}</div>
        {% endfor %}
      </div>{% endif %}
    </div>
  </form>

  {% if clans %}
  <div class="addbox">
    <div class="hd"><span class="t">Clans FOMO — top 24h</span>
      <span class="h">FomoScan ne publie pas la liste des membres · ouvre le clan dans FOMO pour les voir</span></div>
    <div class="bd"><table class="clans">
      <thead><tr><th>#</th><th>Clan</th><th>Membres</th><th>PnL 24h</th></tr></thead><tbody>
      {% for c in clans %}
      <tr><td class="rk">{{ c.rank }}</td>
        <td class="cn">{{ c.handle or c.label }}</td>
        <td class="num">{{ c.memberCount or '—' }}</td>
        <td class="num" style="color:{{ flowcolor(c.pnl) }}">{{ c.pnl|flowfmt }}</td></tr>
      {% endfor %}
      </tbody></table></div>
  </div>
  {% endif %}

  {% if not data.available %}
    <div class="notice">Lecture on-chain indisponible — {{ data.reason or 'clé Helius requise' }}.</div>
  {% elif data.empty %}
    <div class="empty"><span class="big">Aucune adresse suivie</span>Colle tes adresses dans l'encadré ci-dessus, puis Enregistrer.<br>L'onglet affichera les coins où chacune vient d'entrer, et fera remonter<br>ceux sur lesquels <b>plusieurs</b> d'entre elles arrivent.</div>
  {% else %}
    {% if multi %}
    <div class="conv">
      <div class="ch"><span class="t">Convergence</span>
        <span class="s">plusieurs adresses suivies sur le même coin — le signal le plus fort</span></div>
      {% for c in multi %}
      <div class="crow">
        <div class="cn"><div class="a">{{ c.symbol }}</div><div class="b">{{ c.name }}</div></div>
        <div class="mcv num">{{ c.mc|fmt }}</div>
        <div class="cg num {{ 'up' if c.chg_h1>=0 else 'down' }}">{{ '%+.1f'|format(c.chg_h1) }}%</div>
        <div class="by multi">{% for b in c.by %}<span>{{ b }}</span>{% endfor %}</div>
        <div class="ago">{{ c.ts|ago }}</div>
        <div class="acts">
          <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
          <a class="ic" title="GMGN" href="{{ gmgnlink(c.chain, c.mint) }}" target="_blank">{{ icon('chart') }}</a>
          <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
          <button class="ic" title="Copier" onclick="cp(this,'{{ c.mint }}')">{{ icon('copy') }}</button>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}

    <div class="sechead"><h1>Achats récents par adresse</h1><span class="sub">72 dernières heures</span></div>
    {% for w in data.wallets %}
    <div class="wsec">
      <div class="wh2"><span class="nm">{{ w.label or w.short }}</span>
        <span class="ad mono">{{ w.address }}</span>
        <span class="cnt">{{ w.buys|length }} coin{{ 's' if w.buys|length > 1 }}</span></div>
      {% if w.buys %}
        {% for c in w.buys %}
        <div class="crow">
          <div class="cn"><div class="a">{{ c.symbol }}</div><div class="b">{{ c.name }}</div></div>
          <div class="mcv num">{{ c.mc|fmt }}</div>
          <div class="cg num {{ 'up' if c.chg_h1>=0 else 'down' }}">{{ '%+.1f'|format(c.chg_h1) }}%</div>
          <div class="ago">{{ c.ts|ago }}</div>
          <div class="acts">
            <a class="ic" title="Analyse" href="/coin?mint={{ c.mint }}">{{ icon('open') }}</a>
            <a class="ic" title="GMGN" href="{{ gmgnlink(c.chain, c.mint) }}" target="_blank">{{ icon('chart') }}</a>
            <a class="ic" title="DexScreener" href="{{ dexlink(c.chain, c.pair or c.mint) }}" target="_blank">{{ icon('trend') }}</a>
            <button class="ic" title="Copier" onclick="cp(this,'{{ c.mint }}')">{{ icon('copy') }}</button>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <div class="emptyline">Aucun achat sur la période.</div>
      {% endif %}
    </div>
    {% endfor %}
  {% endif %}
</div>""" + SCRIPT + "</body></html>")


PAGE_FLOW = (_H + "<title>MSCAN · Flux</title>" + STYLE + """
<style>
.fl{display:grid;grid-template-columns:minmax(0,1fr) 110px 110px 96px 88px;gap:16px;align-items:center;
 padding:14px 20px;border-bottom:1px solid var(--hair-2)}
.fl:last-child{border-bottom:none}
.fl:hover{background:rgba(255,255,255,.017)}
.fl .who .n{font-size:13.5px;font-weight:500}
.fl .who .s{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--fg-4);margin-top:3px}
.fl .c{text-align:right;font-variant-numeric:tabular-nums}
.fl .c .k{font-size:8.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fg-4);display:none}
.fl .c .v{font-size:13.5px;font-weight:600}
.flh{display:grid;grid-template-columns:minmax(0,1fr) 110px 110px 96px 88px;gap:16px;
 padding:11px 20px;border-bottom:1px solid var(--hair)}
.flh div{font-size:8.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--fg-4);font-weight:600;text-align:right}
.flh div:first-child{text-align:left}
.sigline{font-size:10.5px;color:var(--fg-3);margin-top:4px}
</style></head><body>""" + MACROS + CHROME + r'''
<div class="page">
  {{ subtabs('flow') }}
  <div class="sechead"><h1>Flux des gros porteurs</h1><span class="sub">variation des soldes entre deux scans — qui accumule, qui distribue</span></div>
  <div class="explain">On photographie le solde des plus gros détenteurs à chaque scan, puis on compare
    les photos. Un wallet qui passe de 0,4&nbsp;% à 0,9&nbsp;% du supply <b>accumule</b> ; s'il descend, il <b>distribue</b>.
    C'est le positionnement en cours, pas ce qu'il détient depuis six mois.</div>
  {% if rows %}
  <div class="rows">
    <div class="flh"><div>Coin</div><div>Whale+Shark</div><div>Net total</div><div>Holders</div><div>Market cap</div></div>
    {% for r in rows %}
    <div class="fl">
      <div class="who">
        <div class="n"><a href="/coin?mint={{ r.p.mint }}">{{ r.p.name }}</a></div>
        <div class="s">{{ r.p.symbol }} · {{ r.f.snapshots }} photos · {{ 'net 24h' if r.f.win=='24h' else 'depuis derniere photo' }}</div>
        {% if r.f.signal %}<div class="sigline">{{ r.f.signal }}</div>{% endif %}
      </div>
      <div class="c"><div class="v" style="color:{{ flowcolor(r.f.strong) }}">{{ r.f.strong|flowfmt }}</div></div>
      <div class="c"><div class="v" style="color:{{ flowcolor(r.f.totals[r.f.win]) }}">{{ r.f.totals[r.f.win]|flowfmt }}</div></div>
      <div class="c"><div class="v">{{ r.f.holders }}{% if r.f.holders_delta %} <span style="color:{{ flowcolor(r.f.holders_delta) }};font-size:10.5px">{{ '%+d'|format(r.f.holders_delta) }}</span>{% endif %}</div></div>
      <div class="c"><div class="v">{{ r.p.market_cap|fmt }}</div>
        <div class="acts" style="justify-content:flex-end;margin-top:4px">
          <a class="ic" title="GMGN" href="{{ r.p.gmgn_url }}" target="_blank">{{ icon('chart') }}</a>
          <a class="ic" title="DexScreener" href="{{ r.p.dex_url }}" target="_blank">{{ icon('trend') }}</a>
        </div></div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty"><span class="big">Historique de flux en constitution</span>
    Le flux se calcule en comparant deux photos de soldes on-chain.<br>
    Une photo est prise à chaque scan — le classement apparaît après ~24h de fonctionnement.<br><br>
    {% if not helius %}<span style="color:var(--gold-2)">Clé Helius manquante — ajoute-la dans .env.</span>{% endif %}</div>
  {% endif %}
</div></body></html>''')
