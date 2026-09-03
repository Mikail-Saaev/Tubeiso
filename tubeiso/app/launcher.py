"""Point d'entree de l'application.

Demarre le serveur local puis ouvre l'interface. Trois modes, dans l'ordre de
preference :

  1. fenetre native (pywebview) si la bibliotheque est presente ;
  2. navigateur par defaut de la machine ;
  3. rien, et on affiche l'adresse a ouvrir a la main.

Le mode 2 est le repli qui marche partout, y compris sur un poste verrouille.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

BANNER = r"""
   _         _        _
  | |_ _   _| |__   ___(_)___  ___
  | __| | | | '_ \ / _ \ / __|/ _ \    Tubes cintres Crippa
  | |_| |_| | |_) |  __/ \__ \ (_) |   programme ISO -> modele 3D
   \__|\__,_|_.__/ \___|_|___/\___/
"""


def free_port(preferred: int = 8731) -> int:
    for port in (preferred, 0):
        with contextlib.closing(socket.socket()) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("aucun port disponible sur 127.0.0.1")


def wait_ready(url: str, timeout: float = 25.0) -> bool:
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


def open_window(url: str, mode: str) -> bool:
    """Retourne True si une fenetre native a ete ouverte (bloquante)."""
    if mode in ("auto", "window"):
        try:
            import webview
            webview.create_window("tubeiso — Tubes cintres Crippa", url,
                                  width=1500, height=940, min_size=(1100, 700))
            webview.start()
            return True
        except Exception:
            if mode == "window":
                print("Fenetre native indisponible, bascule sur le navigateur.")
    if mode != "none":
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tubeiso-app", description="Interface graphique tubeiso")
    p.add_argument("source", nargs="?", help="fichier LFT .xlsx a ouvrir au demarrage")
    p.add_argument("-c", "--config", help="tooling.json")
    p.add_argument("--port", type=int, default=8731)
    p.add_argument("--mode", choices=("auto", "window", "browser", "none"),
                   default="auto")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)

    from .server import Session, create_app

    session = Session()
    if args.config:
        from ..config import Config
        try:
            session.config = Config.load(args.config)
        except Exception as exc:
            print(f"Configuration ignoree ({args.config}) : {exc}\n"
                  "  Les valeurs BSA par defaut sont utilisees.", file=sys.stderr)
    if args.source:
        try:
            session.open_lft(args.source)
        except Exception as exc:
            print(f"Fichier non charge : {exc}", file=sys.stderr)

    app = create_app(session)
    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    if not args.debug:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

    def serve():
        app.run(host="127.0.0.1", port=port, debug=False,
                use_reloader=False, threaded=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    print(BANNER)
    if not wait_ready(url + "api/status"):
        print("Le serveur n'a pas demarre.", file=sys.stderr)
        return 1
    print(f"  Interface : {url}")
    print("  Fermez cette fenetre pour quitter.\n")

    if open_window(url, args.mode):
        return 0
    try:
        while thread.is_alive():
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nArret.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
