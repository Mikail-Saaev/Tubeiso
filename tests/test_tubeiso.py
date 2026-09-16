"""Tests d'invariants. L'essentiel : un aller-retour LRA -> 3D -> developpe."""
import json
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


# ---------------------------------------------------------------- v6 : matieres

def test_matiere_rigide_vs_souple():
    """La distinction rigide / souple vient du scan des codes matiere BSA."""
    from tubeiso import materials
    assert materials.kind_of("293-421-008") == materials.RIGIDE
    assert materials.kind_of("769-421-035") == materials.SOUPLE
    # meme prefixe 750, deux familles : le deuxieme triplet tranche
    assert materials.lookup("750 423 012").family == "Pneumatique"
    assert materials.lookup("750 421 012").family == "Forflex"
    assert materials.diameter("293-421-008") == 8
    assert materials.diameter("769-421-035") is None, "un flexible n'a pas de Ø cintrable"
    assert materials.lookup("293-421-008").wall == 1.0
    # famille deduite pour un code absent du catalogue
    inconnu = materials.lookup("778-421-099")
    assert inconnu is not None and inconnu.kind == materials.SOUPLE
    # Ø22 : rigide, mais BSA ne le cintre plus
    assert materials.lookup("293-421-022").kind == materials.RIGIDE
    assert not materials.lookup("293-421-022").bendable


def test_perimetre_progcrippa():
    """Seuls les tubes rigides porteurs d'une PROGCRIPPA sont traites."""
    from tubeiso import lft, scope
    from tubeiso.parsers import crippa

    class Rec:
        def __init__(self, code, iso="", droit=False, main=False):
            self.cells = {"CODE_MAT": code}
            self.iso = iso
            self.straight = droit
            self.handmade = main

        def get(self, col, default=None):
            return self.cells.get(col, default)

    v = scope.evaluate(Rec("769-421-035", PROG))
    assert not v.ok and v.reason == scope.MATIERE_SOUPLE

    v = scope.evaluate(Rec("293-421-006", ""))
    assert not v.ok and v.reason == scope.SANS_PROGRAMME

    v = scope.evaluate(Rec("293-421-006", "", droit=True))
    assert not v.ok and v.reason == scope.TUBE_DROIT

    v = scope.evaluate(Rec("293-421-006", "", main=True))
    assert not v.ok and v.reason == scope.FAIT_MAIN

    raw = crippa.parse(PROG)
    v = scope.evaluate(Rec("293-421-006", PROG), raw)
    assert v.ok and v.diameter == 6 and v.kind == "rigide"

    tronque = PROG.replace("M30", "")
    v = scope.evaluate(Rec("293-421-006", tronque), crippa.parse(tronque))
    assert not v.ok and v.reason == scope.PROGRAMME_TRONQUE


# --------------------------------------------------- v6 : correctifs de lecture

def test_cycle_de_chargement_L4():
    """[DOC 3.1] L4 charge les tubes longs : il porte R6 et R12 comme L1."""
    from tubeiso.parsers import crippa
    prog = PROG.replace("L1 R1=155", "L4 R1=155")
    raw = crippa.parse(prog)
    assert raw.loading == "L4"
    assert raw.init["R12"] == 39.5, "R12 doit etre lu sur un cycle L4"
    assert raw.declared_length == 263.0
    assert crippa.parse(PROG).loading == "L1"


def test_faux_pli_a_zero_degre_fusionne():
    """[DOC 5.3] Un R15=0 ne plie rien : les deux segments n'en font qu'un."""
    from tubeiso import bsa, conventions
    from tubeiso.model import Tooling
    from tubeiso.parsers import crippa

    # Repere 161 du lot Masterflex, tel quel : le dernier bloc est un L3 a
    # R15=0, et le DS du commentaire vaut le segment droit RECOLLE.
    prog = """%MPF 161
(Masterflex HD, Ø10, cassette, L=1444, ds=1306)
L510
L1 R1=155 R4=20 R6=1444 R7=2 R12=30.5 R31=100 R33=279.5 M70
N1 L2 R15=92
G91 G0 Y-35 G90 C0
G91 G0 Y-4 B90
N2 L2 R15=90
G91 G0 Y-35 G90 C0
G91 G0 Y-921
N3 L3 R15=0 R41=23 R43=300
M30
"""
    raw = crippa.parse(prog)
    tooling = Tooling(name="Ø10", diameter=10, clr=23, wall=1.0, elongation=5.0)
    tube = conventions.get("bsa").build(raw, tooling)
    assert tube.n_bends == 2, "le pli a 0° ne doit pas compter comme un coude"
    assert len(tube.straights) == 3
    assert tube.false_bends, "la fusion doit etre tracee"
    # le dernier segment recolle retombe sur le DS du commentaire, a l'arrondi
    assert abs(tube.straights[-1] - 1306) < 1.0, tube.straights


def test_r7_deja_present_ne_declenche_pas_d_alerte():
    """[XLSM Feuil2!B15] Le R7=0 vit dans un bloc N, pas dans la ligne L1."""
    from tubeiso import conventions, validate
    from tubeiso.model import Tooling
    from tubeiso.parsers import crippa

    raw = crippa.parse(PROG)            # le 412 porte R7=0 dans le bloc N1
    tooling = Tooling(name="Ø6", diameter=6, clr=11, wall=1.0, elongation=5.0,
                      min_straight=11)
    tube = conventions.get("bsa").build(raw, tooling)
    assert tube.r7_released
    codes = [i.code for i in validate.check(tube, tooling)]
    assert "r7_manquant" not in codes


def test_angles_au_demi_degre():
    """Le programmeur ecrit au demi-degre : 92.5 en Ø8 doit rester lisible."""
    from tubeiso import bsa
    assert bsa.real_angle(92.5, 8)[0] == 90.5
    assert bsa.real_angle(46, 4)[0] == 45.0, "l'exemple meme de [DOC 5.4]"
    assert bsa.real_angle(92, 12)[0] == 90.0
    assert bsa.real_angle(25.5, 6)[0] == 25.0
    # l'aller-retour reste exact sur tous les angles entiers
    for d in (4, 6, 8, 10, 12, 15, 16, 18):
        for theta in range(5, 186):
            r15 = bsa.programmed_angle(theta, d)
            assert bsa.real_angle(r15, d)[0] == theta, (d, theta, r15)


def test_tables_du_classeur_pour_le_diametre_16():
    """[XLSM Feuil2!B47] Ø16 : droite mini 36 mm, et non 30 comme le Ø15."""
    from tubeiso import bsa
    assert bsa.MIN_STRAIGHT[16] == 36
    assert bsa.R15_FOR_90[16] == 92.5, "sans quoi le Ø16 sort sans compensation"
    assert bsa.real_angle(92.5, 16)[0] == 90.0


# ------------------------------------------------------------ v6 : rattachement

def test_rattachement_groupe_machine():
    from tubeiso import registry
    e = registry.parse_filename("BCH_PLATINE_82_0889_0877-0000-CL")
    assert e.groupe == "BSH", "le prefixe de fichier BCH designe le groupe BSH"
    assert e.machine == "PLATINE_82_0889"
    assert registry.list_number_from("BCH_PLATINE_82_0889_0877-0000-CL") == "0877-0000-CL"
    assert registry.safe_name("A/B:C*?") == "A_B_C"
    assert registry.parse_filename("FLX_MASTERFLEX_HD_EXPERFLEX_0227_0198-0002-JV").groupe == "FLX"


# ------------------------------------------------------------------ v6 : le plan

def test_plan_pdf_autoportant():
    """Le PDF doit exister, faire deux pages, et porter les donnees cles."""
    import tempfile

    from tubeiso import conventions, geometry, render, validate
    from tubeiso.model import Tooling
    from tubeiso.parsers import crippa

    raw = crippa.parse(PROG)
    tooling = Tooling(name="Ø6", diameter=6, clr=11, wall=1.0, elongation=5.0,
                      material="Tube Ermeto zingué 6/4", min_straight=11)
    tube = conventions.get("bsa").build(raw, tooling)
    cl = geometry.build(tube)
    issues = validate.check(tube, tooling, cl)
    data = render.PlanData(tube=tube, centerline=cl, tooling=tooling, issues=issues,
                           groupe="FLX", machine="ESSAI", lft="ESSAI_0000-0000-XX")

    svg = render.to_svg(data)
    assert svg.startswith("<svg") and "PLAN DE FABRICATION" in svg
    assert "TABLE LRA" not in svg, "la page 1 ne porte pas la table LRA"

    with tempfile.TemporaryDirectory() as tmp:
        out = render.to_pdf(data, Path(tmp) / "412.pdf")
        blob = out.read_bytes()
        assert blob.startswith(b"%PDF"), "en-tete PDF absent"
        assert blob.count(b"/Type /Page") >= 2 or blob.count(b"/Type/Page") >= 2, \
            "le plan doit faire deux pages"
        assert len(blob) > 3000

        cahier = render.booklet([data], Path(tmp) / "cahier.pdf", "ESSAI",
                                [{"repere": "9", "motif": "matière_souple",
                                  "detail": "Uniflex 44/35"}])
        assert cahier.read_bytes().startswith(b"%PDF")


def test_segments_du_plan_suivent_le_tube():
    """Les cotes du plan doivent porter sur les vrais segments droits."""
    from tubeiso import geometry, render
    from tubeiso.model import Bend, TubeProgram

    tube = TubeProgram(ref="T", diameter=8.0, straights=[50.0, 60.0, 70.0],
                       bends=[Bend(angle=90, rotation=0, clr=14),
                              Bend(angle=45, rotation=90, clr=14)])
    cl = geometry.build(tube)
    spans = render.straight_spans(cl, tube.n_bends)
    assert len(spans) == len(tube.straights)
    for (a, b), attendu in zip(spans, tube.straights):
        longueur = float(((b - a) ** 2).sum() ** 0.5)
        assert abs(longueur - attendu) < 1e-6, (longueur, attendu)


# ------------------------------------------------------------- v6 : campagne

def test_campagne_range_et_indexe():
    """Une campagne produit l'arborescence, l'index et le rapport."""
    import tempfile

    from openpyxl import Workbook, load_workbook

    from tubeiso import batch

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sources"
        src.mkdir()
        wb = Workbook()
        ws = wb.active
        ws.append(["REP", "CODE_MAT", "LONGUEUR", "LISTE", "PROGRAMME",
                   "PROGCRIPPA", "DROIT"])
        ws.append(["412", "293-421-006", 263, "0792-0002-JV", "792_JV-412",
                   PROG, "Faux"])
        ws.append(["900", "769-421-035", 5000, "0792-0002-JV", "", "", "Vrai"])
        ws.append(["901", "293-421-006", 300, "0792-0002-JV", "", "", "Vrai"])
        wb.save(src / "BCH_ESSAI_0001_0792-0002-JV.xlsx")

        out = Path(tmp) / "biblio"
        campagne = batch.run([src], out, batch.Options(models=False))
        s = campagne.summary()
        assert s["pieces"] == 3 and s["traitees"] == 1 and s["exclues"] == 2
        assert s["plans"] == 1

        lot = out / "BSH" / "ESSAI_0001" / "BCH_ESSAI_0001_0792-0002-JV"
        assert (lot / "plans" / "412.pdf").exists()
        assert (lot / "donnees" / "412.json").exists()
        assert (lot / "BCH_ESSAI_0001_0792-0002-JV_cahier.pdf").exists()
        assert not (lot / "plans" / "900.pdf").exists(), \
            "un tuyau souple ne doit produire aucun plan"

        donnees = json.loads((lot / "donnees" / "412.json").read_text("utf-8"))
        assert donnees["matiere"]["nature"] == "rigide"
        assert donnees["cintrage"]["lra"][0]["r15_programme"] == 46
        assert len(donnees["geometrie"]["sommets_xyz"]) == tube_sommets(donnees)

        index = load_workbook(out / "INDEX.xlsx")
        assert index.sheetnames == ["Tubes", "LFT", "Campagne"]
        assert index["Tubes"].max_row == 4          # en-tete + 3 pieces
        motifs = {index["Tubes"].cell(row=r, column=9).value
                  for r in range(2, 5)}
        assert "matière_souple" in motifs and "tube_droit_sans_programme" in motifs

        # reprise : la meme campagne relancee ne refait rien
        again = batch.run([src], out, batch.Options(models=False))
        assert again.summary()["fichiers_sautes"] == 1


def tube_sommets(donnees: dict) -> int:
    return len(donnees["cintrage"]["lra"]) + 2


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


