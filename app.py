"""
MSCAN — application de bureau.

Lance le serveur en tache de fond et affiche l'interface dans une VRAIE fenetre
native (WebView2), sans barre d'adresse ni console. L'app a sa propre entree
dans la barre des taches et peut y etre epinglee.

Repli automatique si WebView2 est indisponible : fenetre Chrome en mode --app,
puis navigateur par defaut.
"""
import os
import socket
import sys
import threading
import time

# En mode fenetre (console=False), sys.stdout peut etre None : tout print()
# leverait une exception. On redirige donc vers un fichier de log.
def _setup_output():
    import config
    log_path = os.path.join(config.APP_DIR, "mscan.log")
    try:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        f = open(os.devnull, "w")
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = f
        sys.stderr = f
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
            sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass
    return f


def _port_libre(prefere: int) -> int:
    """Retourne le port prefere s'il est libre, sinon un autre."""
    for port in [prefere] + list(range(prefere + 1, prefere + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return prefere


def _attendre_serveur(url: str, timeout: float = 30) -> bool:
    import urllib.request
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _icone() -> str:
    """Chemin de l'icone, qu'on soit en source ou empaquete."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "mscan.ico")
    return p if os.path.exists(p) else ""


def _fenetre_chrome(url: str) -> bool:
    """Repli : fenetre Chrome sans barre d'adresse (ressemble a une app)."""
    import subprocess
    for exe in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]:
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1280,880"])
                return True
            except Exception:
                continue
    return False


def main():
    log = _setup_output()
    import config
    from web.server import create_app, scan_loop, load_smart_wallets

    demo = "--demo" in sys.argv
    port = _port_libre(config.WEB_PORT)
    config.WEB_PORT = port
    url = f"http://{config.WEB_HOST}:{port}"

    print(f"\n=== MSCAN === {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {url}  ({'DEMO' if demo else 'LIVE'})  Helius: {'oui' if config.HELIUS_API_KEY else 'non'}")
    print(f"  Dossier : {config.APP_DIR}")

    # scan en tache de fond
    threading.Thread(target=scan_loop, kwargs={"demo": demo}, daemon=True).start()

    # serveur web en tache de fond
    flask_app = create_app()

    def _serve():
        try:
            flask_app.run(host=config.WEB_HOST, port=port,
                          debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            print(f"[serveur] {e}")

    threading.Thread(target=_serve, daemon=True).start()

    if not _attendre_serveur(url):
        print("[erreur] le serveur n'a pas demarre")
        return

    # 1) fenetre native (WebView2)
    try:
        import webview
        win = webview.create_window(
            "MSCAN", url,
            width=1280, height=880, min_size=(900, 600),
            background_color="#000000",
        )
        try:
            webview.start(icon=_icone() or None)
        except TypeError:      # anciennes versions : pas de parametre icon
            webview.start()
        return
    except Exception as e:
        print(f"[fenetre native indisponible] {e}")

    # 2) repli : fenetre Chrome/Edge en mode app
    if _fenetre_chrome(url):
        print("[repli] fenetre Chrome --app")
    else:
        import webbrowser
        webbrowser.open(url)
        print("[repli] navigateur par defaut")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
