"""Verifie que le noyau CAO se charge et fonctionne.

Ce controle vit dans un fichier Python plutot que dans un bloc bash du
workflow : les blocs multilignes se comportent differemment selon la
plateforme, alors qu'un fichier .py se comporte partout pareil.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import cadquery as cq
        from OCP.STEPControl import STEPControl_Writer  # noqa: F401
    except Exception as exc:                             # noqa: BLE001
        print(f"ECHEC : le noyau CAO ne se charge pas — "
              f"{type(exc).__name__}: {exc}")
        return 1

    print(f"cadquery {cq.__version__} — OCP charge")

    # Au-dela de l'import, on verifie qu'une operation reelle aboutit.
    try:
        box = cq.Workplane("XY").box(10, 10, 10).val()
        volume = box.Volume()
    except Exception as exc:                             # noqa: BLE001
        print(f"ECHEC : le noyau se charge mais ne calcule pas — "
              f"{type(exc).__name__}: {exc}")
        return 1

    if abs(volume - 1000.0) > 1e-6:
        print(f"ECHEC : volume attendu 1000, obtenu {volume}")
        return 1

    print("Operation geometrique verifiee (cube de 1000 mm3).")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
