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


def wait_ready(url: str, timeout: float = 90.0) -> bool:
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


def _pick(argv: list[str]) -> int:
    """Ouvre le selecteur de fichiers du systeme et ecrit le chemin choisi.

    Le navigateur ne donne jamais le chemin reel d'un fichier ni d'un dossier :
    c'est une protection de confidentialite incontournable. On passe donc par
    une fenetre native, lancee dans un processus separe — Tk exige le thread
    principal, ce que le serveur Flask n'a pas a lui offrir.

    L'executable empaquete se relance lui-meme avec ce drapeau ; depuis les
    sources, c'est `python -m tubeiso.app --pick-folder`.
    """
    mode = argv[0]
    title = "Choisir un dossier" if mode == "--pick-folder" else "Choisir un fichier"
    initial = ""
    for i, a in enumerate(argv[1:], start=1):
        if a == "--title" and i + 1 < len(argv):
            title = argv[i + 1]
        elif a == "--initial" and i + 1 < len(argv):
            initial = argv[i + 1]
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:                                     # pragma: no cover
        print(f"selecteur indisponible : {exc}", file=sys.stderr)
        return 2
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if mode == "--pick-folder":
            path = filedialog.askdirectory(title=title, initialdir=initial or None,
                                           mustexist=True)
        else:
            path = filedialog.askopenfilename(
                title=title, initialdir=initial or None,
                filetypes=[("Fichiers LFT et modeles", "*.xlsx *.xlsm *.stp *.step"),
                           ("Tous les fichiers", "*.*")])
    finally:
        with contextlib.suppress(Exception):
            root.destroy()
    if not path:
        return 1
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    # L'executable empaquete est le seul point d'entree distribue : il doit
    # donc aussi donner acces a la ligne de commande, sinon le traitement par
    # lot resterait reserve aux postes ou Python est installe.
    #     tubeiso.exe --cli batch D:\LFT -o D:\bibliotheque
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] in ("--cli", "cli"):
        from ..cli import main as cli_main
        return cli_main(argv_list[1:])
    if argv_list and argv_list[0] in ("--pick-folder", "--pick-file"):
        return _pick(argv_list)

    p = argparse.ArgumentParser(
        prog="tubeiso-app", description="Interface graphique tubeiso")
    p.add_argument("source", nargs="?", help="fichier LFT .xlsx a ouvrir au demarrage")
    p.add_argument("-c", "--config", help="tooling.json")
    p.add_argument("--port", type=int, default=8731)
    p.add_argument("--mode", choices=("auto", "window", "browser", "none"),
                   default="auto")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv_list)

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
    ready = wait_ready(url + "api/status")
    print(f"  Interface : {url}")
    if not ready:
        # Machine lente, antivirus qui inspecte le binaire, premier
        # chargement du noyau CAO : le demarrage peut depasser la minute.
        # On previent sans tuer le serveur, qui finit generalement par
        # repondre. Quitter ici priverait l'utilisateur de l'application
        # pour une simple lenteur.
        if not thread.is_alive():
            print("  Le serveur s'est arrete au demarrage.", file=sys.stderr)
            return 1
        print("  Demarrage plus long que prevu — laissez la page se charger,")
        print("  ou ouvrez l'adresse ci-dessus manuellement.")
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
