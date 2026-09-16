# -*- mode: python ; coding: utf-8 -*-
"""Specification PyInstaller.

Produit un executable autonome : `tubeiso.exe` sous Windows, `tubeiso` sous
macOS et Linux. Aucune installation de Python n'est requise sur la machine
cible.

Construction :

    pip install -r requirements.txt pyinstaller
    pyinstaller tubeiso.spec --noconfirm

Le binaire pese environ 500 a 700 Mo : le noyau OpenCascade represente a lui
seul l'essentiel de ce volume. C'est le prix d'une CAO exacte sans dependance
externe. Passer `--onefile` reduit le nombre de fichiers mais rallonge le
demarrage de plusieurs secondes a chaque lancement, parce que l'archive est
decompressee dans un dossier temporaire. On garde donc le mode dossier, qui
demarre en une seconde ; il suffit de zipper le dossier `dist/tubeiso` pour le
distribuer.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_dynamic_libs, collect_submodules)

block_cipher = None
ROOT = Path(SPECPATH)

# --- ressources de l'interface (HTML, CSS, JS, three.js embarque)
datas = [
    (str(ROOT / "tubeiso" / "app" / "static"), "tubeiso/app/static"),
    (str(ROOT / "tooling.example.json"), "."),
]

# --- OpenCascade : bibliotheques natives et fichiers de ressources
# Collecte complete du noyau CAO : modules Python, extensions natives et
# fichiers de ressources. `collect_all` evite les imports manques que
# l'analyse statique de PyInstaller ne voit pas (cadquery en fait beaucoup
# de facon dynamique).
binaries = []
extra_hidden = []
for pkg in ("OCP", "cadquery", "cadquery_ocp"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        extra_hidden += h
    except Exception:
        try:
            binaries += collect_dynamic_libs(pkg)
            datas += collect_data_files(pkg)
            extra_hidden += collect_submodules(pkg)
        except Exception:
            pass

hiddenimports = extra_hidden + [
    "OCP", "OCP.STEPControl", "OCP.BRep", "OCP.BRepAdaptor", "OCP.BRepTools",
    "OCP.BRepMesh", "OCP.GeomAbs", "OCP.TopAbs", "OCP.TopExp", "OCP.TopoDS",
    "OCP.TopLoc", "OCP.IFSelect", "OCP.Interface", "OCP.Message",
    "cadquery", "cadquery.occ_impl.shapes", "cadquery.occ_impl.exporters",
    "cadquery.occ_impl.exporters.assembly", "cadquery.occ_impl.assembly",
    "ezdxf", "openpyxl", "numpy", "flask", "jinja2", "werkzeug",
    "reportlab", "reportlab.pdfgen", "reportlab.pdfgen.canvas",
    "reportlab.pdfbase", "reportlab.pdfbase.pdfmetrics", "reportlab.lib.colors",
    "tubeiso", "tubeiso.app", "tubeiso.app.server", "tubeiso.app.launcher",
    "tubeiso.batch", "tubeiso.materials", "tubeiso.registry", "tubeiso.scope",
    "tubeiso.sheet", "tubeiso.parsers", "tubeiso.parsers.crippa",
]

# reportlab embarque ses metriques de polices et ses ressources dans le paquet.
try:
    datas += collect_data_files("reportlab")
except Exception:
    pass

# cadquery tire par defaut un rendu VTK, un compilateur JIT et un solveur
# d'optimisation dont l'application ne se sert jamais : elle n'utilise que le
# noyau geometrique OpenCascade. Les exclure fait passer le paquet de 1,3 Go a
# environ 400 Mo. Verifie : tous les chemins de code (balayage, export STEP /
# STL / BREP, lecture STEP, tesselation, mise en plan) fonctionnent sans eux.
# Liste verifiee en bloquant reellement chaque module (MetaPathFinder.find_spec)
# et en rejouant toute la chaine : balayage, exports STEP/STL/BREP, lecture
# STEP, tesselation, mise en plan.
#
# INDISPENSABLES, ne pas ajouter ici :
#   vtkmodules   l'extension native OCP.so est liee en dur aux libvtk*.so
#   nlopt        importe par le solveur de contraintes de cadquery au chargement
#   casadi       idem
#   multimethod  utilise par la repartition de types de cadquery
excludes = [
    "llvmlite", "numba", "scipy", "matplotlib", "pandas", "PIL",
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "ipykernel", "notebook", "jupyter", "pytest", "sphinx",
    "typish", "sqlalchemy",
]

a = Analysis(
    [str(ROOT / "tubeiso_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# NOTE SUR LA TAILLE
# Le paquet pese environ 700 Mo, dont 310 Mo de VTK. On ne peut PAS le retirer :
# l'extension native OCP.so du noyau OpenCascade est liee en dur aux
# bibliotheques libvtk*.so, qui vivent dans le dossier vtkmodules. Les
# supprimer casse l'import de OCP (verifie : `ldd OCP.so` remonte une trentaine
# de dependances non resolues). En revanche les modules purement Python que
# cadquery tire par defaut — llvmlite, numba, casadi, scipy, nlopt, matplotlib —
# sont exclus plus haut : ils font gagner environ 600 Mo.

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="tubeiso",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX casse certaines DLL OpenCascade
    console=True,                   # la console affiche l'URL et les erreurs
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "tubeiso.ico")
         if (ROOT / "assets" / "tubeiso.ico").exists() else None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="tubeiso",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="tubeiso.app", bundle_identifier="ch.bsa.tubeiso",
        info_plist={"NSHighResolutionCapable": True, "LSBackgroundOnly": False},
    )
