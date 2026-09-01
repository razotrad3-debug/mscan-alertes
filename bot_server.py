"""
MSCAN — runner sans interface, pour un serveur allume en permanence.

Fait tourner le meme moteur que l'application de bureau, mais sans fenetre :
il scanne en boucle et pousse les setups A+ sur Telegram. C'est ce fichier
qu'on deploie (Railway / Fly.io / VPS) pour recevoir les alertes PC eteint.

    python bot_server.py                  # boucle sans fin
    python bot_server.py --test           # verifie la config Telegram et sort
    python bot_server.py --once           # un seul scan puis sort
    python bot_server.py --minutes 340    # boucle puis s'arrete proprement
"""
import os
import sys
import threading
import time
import traceback

sys.stdout.reconfigure(line_buffering=True)

# c'est ce processus qui envoie les alertes (voir telegram_alerts.alerts_enabled)
os.environ.setdefault("MSCAN_HEADLESS", "1")

import config
from mmscanner import engine
from mmscanner import telegram_alerts as tg
from mmscanner import telegram_bot
from mmscanner import insider_watch

# dernier scan garde en memoire : c'est ce que les commandes Telegram lisent,
# pour repondre instantanement sans relancer quoi que ce soit.
STATE = {"pairs": [], "last_scan": 0.0, "scan_now": threading.Event()}


def load_smart_wallets():
    """Wallets suivis : smart_wallets.txt + adresses suivies (memes fichiers que l'app)."""
    try:
        from mmscanner.followed import tracked_lines
        return tracked_lines()
    except Exception:
        try:
            with open(config.path("smart_wallets.txt"), "r", encoding="utf-8") as f:
                return [l.strip() for l in f if l.strip() and not l.startswith("#")]
        except Exception:
            return []


def one_scan() -> int:
    wallets = load_smart_wallets()
    print(f"[scan] demarrage · {len(wallets)} wallets suivis")
    t0 = time.time()
    pairs = engine.scan(wallets)
    aplus = [p for p in pairs if p.grade == "A+"]
    print(f"[scan] {len(pairs)} paires en {time.time()-t0:.0f}s · {len(aplus)} A+")

    STATE["pairs"] = pairs
    STATE["last_scan"] = time.time()

    sent = tg.notify_new(pairs)

    # photos de soldes : garde le Whale Flow alimente meme sans l'interface
    try:
        from mmscanner import holder_flow
        for p in pairs[: config.SMARTMONEY_TOP_N]:
            holder_flow.snapshot(p.mint, p.price_usd)
    except Exception as e:
        print(f"[flow] {e}")

    # avoirs des adresses suivies : c'est ce cache que le scanner relit pour
    # savoir qui detient quoi. Sans ce passage, le critere smart-money
    # tomberait a zero dans le cloud et les notes s'ecrouleraient.
    try:
        from mmscanner import holdings
        holdings.scan()
    except Exception as e:
        print(f"[holdings] {e}")

    # rosters de clans en attente de resolution
    try:
        from mmscanner import clans
        clans.resolve_pending(budget=40)
    except Exception as e:
        print(f"[clans] {e}")

    # decouverte periodique de nouveaux smart wallets
    try:
        from mmscanner import discover_wallets
        discover_wallets.run_if_due()
    except Exception as e:
        print(f"[discover] {e}")

    return sent


def main():
    args = set(sys.argv[1:])

    if not tg.enabled():
        print("!! TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents — aucune alerte ne partira.")
        if "--test" in args:
            return 1
    if "--test" in args:
        ok = tg.test()
        print("Telegram :", "OK — regarde ta conversation" if ok else "ECHEC")
        return 0 if ok else 1

    interval = int(os.getenv("SCAN_INTERVAL_SEC") or config.SCAN_INTERVAL_SEC)

    # duree de vie maximale : sur GitHub Actions un job est tue a la limite,
    # et l'etape de sauvegarde de l'etat ne tourne alors pas. On s'arrete donc
    # nous-memes, un peu avant, pour rendre la main proprement.
    limite = 0.0
    if "--minutes" in sys.argv:
        try:
            limite = time.time() + float(sys.argv[sys.argv.index("--minutes") + 1]) * 60
        except (IndexError, ValueError):
            limite = 0.0
    # on dit tout haut ce qu'on a retrouve : sans ca, un cache perdu passe
    # inapercu et le bot re-alerte tout comme s'il decouvrait les coins.
    try:
        deja = tg._load()
        coins = {k: v for k, v in deja.items() if not k.startswith("_")}
        print(f"[memoire] {len(coins)} coin(s) deja alerte(s) en memoire "
              f"(fenetre {tg.ALERT_COOLDOWN_H} h)")
        if not coins:
            print("[memoire] ATTENTION : memoire vide, tout sera re-alerte une fois")
    except Exception as e:
        print(f"[memoire] {e}")

    print(f"MSCAN bot · scan toutes les {interval//60} min · "
          f"alertes {'/'.join(tg.ALERT_GRADES)} vers Telegram")
    tg.send("🟢 *MSCAN bot* demarre.\n"
            "Envoie `/top` a tout moment pour voir les coins du dernier scan — "
            "aucune attente, la reponse est immediate.\n`/help` pour la liste.")

    # ── veille rapide sur les insiders ──────────────────────────────
    # Le scan complet passe par les classements volume : il voit un coin une
    # fois qu'il a bouge. Cette veille regarde les wallets suivis toutes les
    # 60 s et signale l'entree elle-meme, donc avant le classement.
    def _veille():
        try:
            insider_watch.poll(amorcage=True)   # 1er tour : on note sans alerter
        except Exception as e:
            print(f"[insider] amorcage : {e}")
        while True:
            debut = time.time()
            try:
                insider_watch.poll()
            except Exception as e:
                print(f"[insider] {e}")
            # cadence de 60 s quoi qu'il arrive, meme si le tour a ete long
            time.sleep(max(5, 60 - (time.time() - debut)))

    if "--once" not in args:
        threading.Thread(target=_veille, daemon=True, name="insider").start()

    # thread de consultation : repond aux commandes pendant que le scan tourne
    def _listen():
        while True:
            try:
                telegram_bot.poll_once(
                    pairs_getter=lambda: STATE["pairs"],
                    rescan=lambda: STATE["scan_now"].set(),
                    last_scan_getter=lambda: STATE["last_scan"],
                )
            except Exception as e:
                print(f"[bot] {e}")
                time.sleep(5)

    if "--once" not in args:
        threading.Thread(target=_listen, daemon=True, name="telegram").start()

    while True:
        try:
            one_scan()
        except Exception:
            traceback.print_exc()
            try:
                tg.send("⚠️ MSCAN : erreur pendant le scan, nouvelle tentative au prochain cycle.")
            except Exception:
                pass
        if "--once" in args:
            return 0
        if limite and time.time() + interval > limite:
            print("[fin] limite de duree atteinte — arret propre")
            tg.send("⏸ MSCAN : fenetre de scan terminee, "
                    "la suivante demarre dans quelques minutes.")
            return 0

        # on dort jusqu'au prochain cycle, sauf si /scan reclame plus tot
        STATE["scan_now"].wait(timeout=interval)
        STATE["scan_now"].clear()


if __name__ == "__main__":
    sys.exit(main() or 0)
