"""Tests d'invariants. L'essentiel : un aller-retour LRA -> 3D -> developpe."""
import math
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tubeiso import geometry
from tubeiso.model import Bend, TubeProgram
from tubeiso.parsers import crippa

PROG = """%MPF 412
(MASTERCUT 1.7/2.1/1.65, Ø6, SECTEUR, L=263, DS=69)
L56
L1 R1=155 R4=20 R6=263 R7=2 R12=39.5 R31=100 R33=280 M70
N1 L2 R15=46 R7=0
G91 G0 Y-35 G90 C0
G91 G0 Y-107 B24
N2 L3 R15=13 R41=11 R43=300
M30
"""


def test_parser():
    raw = crippa.parse(PROG)
    assert raw.name == "412"
    assert raw.tooling == "L56"
    assert raw.declared_length == 263.0
    assert raw.ds == 69.0
    assert raw.init["R12"] == 39.5
    assert raw.angles == [46.0, 13.0]
    assert raw.blocks[0].segment() == 142.0        # 35 + 107 [DOC 4.2]
    assert raw.head == 5 and raw.diameter == 6.0    # L56 = tete du bas, O6
    assert raw.blocks[0].rotation() == 24.0
    assert raw.complete


def test_truncation_detected():
    raw = crippa.parse("%MPF 1\n" + "x" * (255 - 7))
    assert any("255" in w for w in raw.warnings)
    assert not raw.complete


def test_developed_length_matches_geometry():
    """La polyligne 3D doit reproduire le developpe analytique."""
    tube = TubeProgram(
        ref="T", diameter=6.0, straights=[100.0, 80.0, 60.0],
        bends=[Bend(angle=45, rotation=0, clr=20),
               Bend(angle=90, rotation=90, clr=20)],
    )
    cl = geometry.build(tube, samples=400)
    analytic = geometry.developed_length(tube)
    assert abs(cl.developed - analytic) < 1e-9
    poly = sum(
        float(((cl.points[i + 1] - cl.points[i]) ** 2).sum() ** 0.5)
        for i in range(len(cl.points) - 1)
    )
    assert abs(poly - analytic) < 0.05          # erreur de discretisation


def test_planar_part_stays_planar():
    """Sans rotation, la piece reste dans un plan."""
    tube = TubeProgram(
        ref="T", diameter=6.0, straights=[50.0, 50.0, 50.0],
        bends=[Bend(angle=30, rotation=0, clr=15),
               Bend(angle=30, rotation=0, clr=15)],
    )
    cl = geometry.build(tube)
    assert abs(cl.points[:, 1]).max() < 1e-9


def test_missing_radius_refuses():
    tube = TubeProgram(ref="T", straights=[10.0, 10.0], bends=[Bend(angle=30)])
    try:
        geometry.build(tube)
    except geometry.MissingRadius:
        return
    raise AssertionError("aurait du lever MissingRadius")




def test_bsa_length_model_matches_excel():
    """Reproduit exactement Feuil2 de archivage_crippa.xlsm sur le tube 410."""
    from tubeiso import bsa
    straights = [4.0, 34.0, 63.5, 159.0]
    angles = [94.0, 34.0, 94.0]
    assert abs(bsa.arc_length(angles, 4) - 42.5993) < 1e-3
    r6 = bsa.developed_length(straights, angles, 4)
    assert abs(r6 - 301.82) < 0.01


def test_y_split_rule():
    """Y > 35 se scinde en 35 + reste. [XLSM Feuil1!E20]"""
    from tubeiso import bsa
    assert bsa.split_moves(34.0) == [34.0]
    assert bsa.split_moves(63.5) == [35.0, 28.5]
    assert bsa.split_moves(142.0) == [35.0, 107.0]


def test_tooling_code():
    from tubeiso import bsa
    assert bsa.parse_tooling("L54") == (5, 4)
    assert bsa.parse_tooling("L412") == (4, 12)



def test_primitives_are_exact():
    """Droites et arcs exacts : leur somme doit egaler le developpe."""
    tube = TubeProgram(
        ref="T", diameter=6.0, straights=[50.0, 40.0, 60.0],
        bends=[Bend(angle=45, rotation=0, clr=11),
               Bend(angle=90, rotation=90, clr=11)],
    )
    cl = geometry.build(tube)
    assert [p.kind for p in cl.primitives] == ["line", "arc", "line", "arc", "line"]
    assert abs(sum(p.length for p in cl.primitives) - cl.developed) < 1e-9


def test_solid_is_watertight():
    """Le solide balaye doit etre valide et creux. Ignore si cadquery absent."""
    from tubeiso import solid
    from tubeiso.model import Tooling
    try:
        solid._cq()
    except solid.SolidError:
        print("    (cadquery absent, test ignore)")
        return
    tube = TubeProgram(
        ref="T", diameter=6.0, straights=[50.0, 40.0, 60.0],
        bends=[Bend(angle=45, rotation=0, clr=11),
               Bend(angle=90, rotation=90, clr=11)],
    )
    cl = geometry.build(tube)
    rep = solid.report(tube, cl, Tooling(name="O6", diameter=6.0, clr=11, wall=1.0))
    assert rep["etanche"]
    # volume d'un tube creux : section annulaire fois developpe
    import math
    attendu = math.pi * (3.0 ** 2 - 2.0 ** 2) * cl.developed
    assert abs(rep["volume_mm3"] - attendu) / attendu < 0.01


# ---------------------------------------------------------------- v5 : lecture

def test_lft_multi_lignes_et_lots():
    """Un tube eclate sur plusieurs lignes est reconstitue, les lots separes."""
    from openpyxl import Workbook as XlWb
    from tubeiso import lft
    import tempfile, os

    wb = XlWb()
    ws = wb.active
    ws.append([]); ws.append(["export du jour"]); ws.append([])
    ws.append(["Repère", "CODE_MAT", "Long.", "LISTE", "PROGRAMME", "Prog Crippa",
               "EMBOUT_2", "RECOUPE_2"])
    ws.append([412, "293-421-006", 263, "LOT-A", "A-412", PROG, None, None])
    ws.append([412, None, None, "LOT-A", "A-412", None, "V06", None])   # 2e ligne
    ws.append([None, None, None, None, None, None, None, 7])            # continuation
    ws.append([412, "293-421-006", 263, "LOT-B", "B-412", PROG, "V06", None])
    path = os.path.join(tempfile.mkdtemp(), "t.xlsx")
    wb.save(path)

    book = lft.read(path)
    assert len(book.lots) == 2, "les deux lots doivent etre separes"
    a = book.lots[0].tubes[0]
    assert len(a.rows) == 3, "les 3 lignes du tube doivent etre regroupees"
    assert a.get("EMBOUT_2") == "V06", "aucune donnee ne doit etre perdue"
    assert a.recut == 7, "la recoupe de la ligne de continuation est conservee"
    assert a.list_number == "LOT-A" and a.program_number == "A-412"
    assert book.lots[1].tubes[0].list_number == "LOT-B"
    assert any("412" in w for w in book.warnings), "repere duplique a signaler"


def test_lft_entete_introuvable():
    from openpyxl import Workbook as XlWb
    from tubeiso import lft
    import tempfile, os
    wb = XlWb(); wb.active.append(["a", "b", "c"])
    path = os.path.join(tempfile.mkdtemp(), "x.xlsx")
    wb.save(path)
    try:
        lft.read(path)
    except ValueError:
        return
    raise AssertionError("un classeur sans colonnes LFT doit etre refuse")


# ------------------------------------------------------- v5 : retour elastique

def test_retour_elastique_aller_retour():
    """R15 -> angle reel -> R15 doit boucler, et donner des angles ronds."""
    from tubeiso import bsa
    cas = [(6, 46, 45), (6, 13, 13), (6, 39, 38), (6, 90, 88), (6, 77, 75),
           (4, 94, 91), (4, 34, 33), (4, 54, 52), (4, 26, 25),
           (8, 92, 90), (8, 44, 43), (8, 31, 30), (8, 10, 10)]
    for d, r15, attendu in cas:
        reel, delta = bsa.real_angle(r15, d)
        assert reel == attendu, f"Ø{d} R15={r15} -> {reel}, attendu {attendu}"
        assert bsa.programmed_angle(reel, d) == r15, f"Ø{d} : aller-retour casse"
        assert delta == r15 - attendu


def test_mode_brut_ne_corrige_rien():
    from tubeiso import bsa
    assert bsa.real_angle(94, 4, "brut") == (94.0, 0.0)


def test_geometrie_sur_angle_reel():
    """La 3D doit porter l'angle reel, le modele garde le R15 programme."""
    from tubeiso import bsa, conventions
    from tubeiso.model import Tooling
    raw = crippa.parse(PROG)
    tube = conventions.get("bsa").build(raw, Tooling("L56", 6.0, clr=11.0))
    assert [b.r15 for b in tube.bends] == [46.0, 13.0]
    assert [b.angle for b in tube.bends] == [45.0, 13.0]
    assert tube.bends[0].springback == 1.0


def test_tube_droit_a_une_longueur():
    """Un tube sans programme doit produire un cylindre, pas un point."""
    from tubeiso import conventions
    from tubeiso.model import Tooling
    tube = conventions.get("bsa").build_straight("500", 420.0, 6.0,
                                                 Tooling("Ø6", 6.0, clr=11.0))
    cl = geometry.build(tube)
    assert abs(cl.developed - 420.0) < 1e-9
    assert abs(float(np_norm(cl.points[-1] - cl.points[0])) - 420.0) < 1e-9


def test_controle_DS_detecte_un_faux_rayon():
    """Le controle de longueur doit reagir a un rayon errone. En v4 il valait
    exactement zero quoi qu'il arrive."""
    from tubeiso import conventions, validate
    from tubeiso.model import Tooling
    raw = crippa.parse(PROG)
    bon = conventions.get("bsa").build(raw, Tooling("L56", 6.0, clr=11.0))
    faux = conventions.get("bsa").build(raw, Tooling("L56", 6.0, clr=30.0))
    ecart = lambda t: abs(t.straights[-1] - t.ds)
    assert ecart(faux) > ecart(bon) + 5, "un rayon faux doit se voir"


def np_norm(v):
    return float((v ** 2).sum() ** 0.5)


def _run() -> int:
    echecs = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            echecs += 1
            print(f"ECHEC  {name} : {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"ok  {name}")
    print("\nTous les tests passent." if not echecs
          else f"\n{echecs} test(s) en echec.")
    return 1 if echecs else 0


if __name__ == "__main__":
    code = _run()
    # Sortie immediate, sans passer par la fermeture de l'interpreteur.
    # OpenCascade et VTK liberent leurs ressources natives a ce moment-la et
    # y plantent parfois, ce qui renvoie un code non nul malgre des tests
    # tous verts. Le symptome a ete observe sur les runners GitHub.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


