"""Tests d'invariants. L'essentiel : un aller-retour LRA -> 3D -> developpe."""
import math
import sys
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nTous les tests passent.")
