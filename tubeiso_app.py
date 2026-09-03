"""Point d'entree de l'executable.

PyInstaller demarre ce fichier. Il ne fait qu'appeler le lanceur, mais il
existe pour donner un point d'entree stable au binaire, independant de la
structure interne du paquet.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()      # requis sous Windows
    from tubeiso.app.launcher import main
    sys.exit(main())
