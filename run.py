"""
Point d'entrée du scanner MikeMike.

  python run.py --demo     # dashboard avec données d'exemple (sans clé ni réseau)
  python run.py --once     # un seul scan live, affiché dans le terminal
  python run.py            # scan live en boucle + dashboard + alertes Telegram
"""
import argparse
import sys
import threading
import time

try:
    # line_buffering : en .exe la sortie serait sinon bufferisee et l'utilisateur
    # ne verrait pas la progression du scan pendant plusieurs minutes.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import config


def print_table(pairs):
    print("\n" + "=" * 78)
    print(f"{'GRADE':6} {'SCORE':6} {'PHASE':12} {'MC':>10} {'1H':>7}  NAME")
    print("-" * 78)
    for p in pairs[:40]:
        mc = p.market_cap
        mcs = f"${mc/1e6:.2f}M" if mc >= 1e6 else f"${mc/1e3:.0f}K"
        print(f"{p.grade:6} {str(p.score)+'/'+str(p.max_score):6} "
              f"{p.phase:12} {mcs:>10} {p.chg_h1:>6.1f}%  {p.name} ({p.symbol})")
    print("=" * 78 + "\n")


def main():
    ap = argparse.ArgumentParser(description="MikeMike SOL scanner")
    ap.add_argument("--demo", action="store_true", help="données d'exemple, sans réseau")
    ap.add_argument("--once", action="store_true", help="un scan live puis stop")
    ap.add_argument("--no-web", action="store_true", help="pas de dashboard (avec --once ou boucle terminal)")
    ap.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur au démarrage")
    args = ap.parse_args()

    from web.server import create_app, scan_loop, load_smart_wallets

    if args.once:
        from mmscanner import engine
        pairs = engine.scan(load_smart_wallets())
        print_table(pairs)
        return

    demo = args.demo
    t = threading.Thread(target=scan_loop, kwargs={"demo": demo}, daemon=True)
    t.start()

    if args.no_web:
        print("Scan en boucle (Ctrl+C pour arrêter)…")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            return

    app = create_app()
    url = f"http://{config.WEB_HOST}:{config.WEB_PORT}"
    if not args.no_browser:
        def _open():
            time.sleep(1.5)
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()
    print("\n== MikeMike Scanner ==")
    print(f"  Dashboard : {url}")
    print(f"  Mode      : {'DEMO' if demo else 'LIVE'}")
    print(f"  Helius    : {'oui' if config.HELIUS_API_KEY else 'NON (wallets off)'}")
    print(f"  Telegram  : {'oui' if config.TELEGRAM_BOT_TOKEN else 'non'}\n")
    print("  Dossier   : " + config.APP_DIR)
    print("")
    print("  Le premier scan prend 5 a 6 minutes. Laisse cette fenetre ouverte.")
    print("  Pour arreter : Ctrl+C ou ferme la fenetre.")
    print("")
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
