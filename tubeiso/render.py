"""Mise en plan du tube : une feuille autoportante.

Objectif, et c'est un objectif dur : **un fabricant qui ne dispose que de ce
plan doit pouvoir produire le tube.** Rien ne doit rester implicite — ni la
matiere, ni le rayon de matrice, ni le sens des rotations, ni la convention de
mesure des longueurs, ni ce que contient exactement la colonne R15.

Le plan tient en deux pages A4 paysage :

  page 1  la piece      vue isometrique cotee, trois vues orthogonales,
                        bloc matiere / debit / cintrage / extremites, cartouche
  page 2  les donnees   table LRA complete, coordonnees XYZ des points
                        d'intersection et de tangence, raccords, tolerances,
                        notes de fabrication, controles, programme d'origine

Le dessin est ecrit une seule fois contre `sheet.Sheet`, puis rendu en SVG
pour l'apercu de l'application et en PDF pour la sous-traitance. Les deux
sorties sont donc identiques au trait pres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from . import bsa, materials, scope, sheet as sh
from .geometry import Centerline
from .model import Tooling, TubeProgram
from .validate import ERROR, WARN, Issue, unsafe

COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))

PAGE_W, PAGE_H = sh.A4_LANDSCAPE
FRAME = (8.0, 8.0, 289.0, 202.0)

# Page 1
ISO_BOX = (10.0, 12.0, 192.0, 144.0)
ORTHO_BOX = (10.0, 147.0, 192.0, 200.0)
DATA_BOX = (196.0, 12.0, 287.0, 133.0)
TITLE_BOX = (196.0, 136.0, 287.0, 200.0)

# Page 2
P2_HEAD = (10.0, 12.0, 287.0, 23.0)
P2_LEFT = (10.0, 26.0, 160.0, 200.0)
P2_RIGHT = (164.0, 26.0, 287.0, 200.0)

REVISION = "A"


# --------------------------------------------------------------- donnees du plan

@dataclass
class PlanData:
    """Tout ce qui figure sur la feuille, rassemble en un seul objet.

    Ce qui n'est pas ici ne peut pas apparaitre sur le plan : c'est voulu, la
    liste sert de contrat entre le traitement par lot et la mise en plan.
    """

    tube: TubeProgram
    centerline: Centerline
    tooling: Tooling
    issues: list[Issue] = field(default_factory=list)
    material: materials.Material | None = None

    # rattachement industriel
    groupe: str = ""
    machine: str = ""
    designation: str = ""          # description de la machine ou du circuit
    lft: str = ""                  # code LFT complet, = nom du fichier source
    source_file: str = ""

    # donnees de la ligne LFT
    embout_1: str = ""
    embout_2: str = ""
    recoupe_1: float = 0.0
    recoupe_2: float = 0.0
    recoupe_programme: float = 0.0   # RECOUPE= lue dans le commentaire du programme
    lft_length: float | None = None
    quantite: float | None = None
    vitesse: str = ""
    gabarit: str = ""
    dessin: str = ""
    remarque: str = ""

    handedness: int = 1
    angle_mode: str = bsa.DEFAULT_ANGLE_MODE
    drawn_on: str = ""
    revision: str = REVISION

    def __post_init__(self) -> None:
        if not self.drawn_on:
            self.drawn_on = date.today().strftime("%d.%m.%Y")

    # -- raccourcis
    @property
    def diameter(self) -> float:
        return float(self.tube.diameter or 0.0)

    @property
    def wall(self) -> float | None:
        if self.material and self.material.wall:
            return self.material.wall
        return self.tooling.wall

    @property
    def recut(self) -> float:
        """Recoupe totale. La LFT fait foi ; a defaut, celle du commentaire."""
        lft_side = float(self.recoupe_1 or 0.0) + float(self.recoupe_2 or 0.0)
        return lft_side or float(self.recoupe_programme or 0.0)

    @property
    def recut_label(self) -> str:
        if self.recoupe_1 or self.recoupe_2:
            return f"{self.recoupe_1:g} / {self.recoupe_2:g} mm (LFT)"
        if self.recoupe_programme:
            return f"{self.recoupe_programme:g} mm (commentaire programme)"
        return "aucune"

    @property
    def status(self) -> str:
        if any(i.level == ERROR for i in self.issues):
            return "ERREUR"
        if any(i.level == WARN for i in self.issues):
            return "ALERTE"
        return "CONFORME"

    @property
    def status_colour(self) -> str:
        return {"ERREUR": sh.RED, "ALERTE": sh.ORANGE}.get(self.status, sh.GREEN)

    @property
    def cut_length(self) -> float:
        """Longueur de debit : developpe declare + recoupes. C'est ce qu'on scie."""
        base = self.tube.declared_length or self.centerline.developed
        return float(base) + self.recut

    @property
    def name(self) -> str:
        return self.tube.ref or self.tube.program_number or "sans repère"


# --------------------------------------------------------------- projections

def project(pts: np.ndarray, azimuth: float = 0.0) -> np.ndarray:
    """Projection isometrique a 30 degres, apres rotation d'azimut autour de Z."""
    a = math.radians(azimuth)
    c, s = math.cos(a), math.sin(a)
    x = pts[:, 0] * c - pts[:, 1] * s
    y = pts[:, 0] * s + pts[:, 1] * c
    z = pts[:, 2]
    return np.column_stack([(x - y) * COS30, -((x + y) * SIN30 - z)])


def project_ortho(pts: np.ndarray, plane: str) -> np.ndarray:
    """Vues orthogonales normalisees. XY = dessus, XZ = face, YZ = gauche."""
    if plane == "XY":
        return np.column_stack([pts[:, 0], -pts[:, 1]])
    if plane == "XZ":
        return np.column_stack([pts[:, 0], -pts[:, 2]])
    return np.column_stack([pts[:, 1], -pts[:, 2]])


# La recherche d'azimut compare toutes les paires de points, pour chacun des
# 36 azimuts essayes. Sur un tube a 30 coudes, cela faisait 750 points, donc
# 20 millions de distances par plan — presque une seconde, et ce cout explose
# sur une campagne de plusieurs milliers de pieces. Un echantillon suffit
# largement : on cherche une ORIENTATION, pas une cote.
AZIMUTH_SAMPLE = 140


def best_azimuth(cl: Centerline, diameter: float, step: float = 10.0) -> float:
    """Azimut qui separe le mieux a l'ecran les portions eloignees en 3D."""
    p = cl.points
    if len(p) < 4:
        return 0.0
    if len(p) > AZIMUTH_SAMPLE:
        # On garde les extremites et les sommets en echantillonnant
        # regulierement : la silhouette est conservee.
        idx = np.unique(np.linspace(0, len(p) - 1, AZIMUTH_SAMPLE).astype(int))
        p = p[idx]
    d3 = np.linalg.norm(p[:, None, :] - p[None, :, :], axis=-1)
    far = d3 > max(4.0 * diameter, 12.0)
    if not far.any():
        return 0.0
    best, best_score = 0.0, -1.0
    for az in np.arange(0.0, 360.0, step):
        q = project(p, float(az))
        d2 = np.linalg.norm(q[:, None, :] - q[None, :, :], axis=-1)
        clearance = d2[far].min()
        span = q.max(axis=0) - q.min(axis=0)
        score = clearance + 0.02 * float(span[0] * span[1]) ** 0.5
        if score > best_score:
            best, best_score = float(az), score
    return best


@dataclass
class Layout:
    scale: float
    offset: np.ndarray
    azimuth: float = 0.0
    plane: str = "ISO"

    def to_paper(self, pts: np.ndarray) -> np.ndarray:
        q = (project(pts, self.azimuth) if self.plane == "ISO"
             else project_ortho(pts, self.plane))
        return q * self.scale + self.offset

    @property
    def label(self) -> str:
        return f"1:{1 / self.scale:.1f}" if self.scale < 1 else f"{self.scale:.1f}:1"


def layout(cl: Centerline, box, diameter: float, azimuth: float | None = None,
           plane: str = "ISO", margin: float = 14.0) -> Layout:
    az = (best_azimuth(cl, diameter) if azimuth is None else azimuth) if plane == "ISO" else 0.0
    q = project(cl.points, az) if plane == "ISO" else project_ortho(cl.points, plane)
    lo, hi = q.min(axis=0), q.max(axis=0)
    span = np.maximum(hi - lo, 1e-6)
    x0, y0, x1, y1 = box
    avail = np.array([max(x1 - x0 - 2 * margin, 5.0), max(y1 - y0 - 2 * margin, 5.0)])
    # Un tube droit a une etendue nulle dans deux directions : sans plafond,
    # l'echelle partirait a l'infini et la vue afficherait un pate.
    scale = min(float(min(avail / span)), 10.0)
    centre = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    return Layout(scale, centre - (lo + hi) / 2 * scale, az, plane)


# ------------------------------------------------------------------ utilitaires

def straight_spans(cl: Centerline, n_bends: int):
    """Extremites papier de chaque segment droit, dans l'ordre du tube.

    On les reconstruit a partir des points de tangence plutot que des
    primitives : un segment de longueur nulle ne produit pas de primitive, et
    l'indexation deraperait silencieusement.
    """
    t = cl.tangent_points
    spans = []
    if n_bends == 0 or len(t) < 2:
        return [(cl.points[0], cl.points[-1])]
    spans.append((cl.points[0], t[0]))
    for i in range(1, n_bends):
        spans.append((t[2 * i - 1], t[2 * i]))
    spans.append((t[2 * n_bends - 1], cl.points[-1]))
    return spans


def fitting_label(code: str, diameter: float) -> tuple[str, str]:
    """(libelle, profondeur d'emmanchement) pour un code d'embout de la LFT."""
    code = (code or "").strip().upper()
    if not code:
        return "—", ""
    label = bsa.END_FITTINGS.get(code, code)
    depth = ""
    if code.startswith("V"):
        try:
            d = float(code[1:])
        except ValueError:
            d = diameter
        row = bsa.VOGEL_DRILL.get(int(d)) or bsa.VOGEL_DRILL.get(d)
        if row:
            depth = f"forage {row[0]}, T1 {row[1]:g} mm, {row[3]}"
    return label, depth


def _ermeto_depth(diameter: float) -> str:
    row = bsa.ERMETO_INSERT.get(int(diameter))
    if not row:
        return ""
    light, heavy = row
    return f"série L {light:g} mm" + (f" · série S {heavy:g} mm" if heavy else "")


def _block(s: sh.Sheet, box, title: str) -> float:
    """Encadre un bloc titre. Retourne l'ordonnee de la premiere ligne utile."""
    x0, y0, x1, y1 = box
    s.rect(x0, y0, x1, y1, w=sh.W_THIN, colour=sh.LIGHT)
    s.rect(x0, y0, x1, y0 + 6.0, w=sh.W_THIN, colour=sh.LIGHT, fill="#F2F5F7")
    s.text(x0 + 2.0, y0 + 4.2, title.upper(), 2.5, bold=True, colour=sh.BLUE)
    return y0 + 10.5


# ------------------------------------------------------------------- page 1

def _draw_curve(s: sh.Sheet, lay: Layout, cl: Centerline, diameter: float,
                thin: bool = False) -> np.ndarray:
    pts = lay.to_paper(cl.points)
    body_w = min(max(0.7, diameter * lay.scale), 6.0)
    s.polyline(pts, w=body_w, colour=sh.BODY)
    s.polyline(pts, w=sh.W_HAIR if thin else 0.4, colour=sh.INK)
    return pts


def _draw_iso(s: sh.Sheet, d: PlanData, azimuth: float | None,
              shift: float = 0.0) -> Layout:
    cl, tube = d.centerline, d.tube
    box = ISO_BOX if shift <= 0 else (
        ISO_BOX[0], ISO_BOX[1] + shift, ISO_BOX[2], ISO_BOX[3])
    lay = layout(cl, box, d.diameter or 6.0, azimuth)
    x0, y0, x1, y1 = box
    s.rect(x0, y0, x1, y1, w=sh.W_THIN, colour=sh.LIGHT)

    pts = _draw_curve(s, lay, cl, d.diameter)
    tang = lay.to_paper(cl.tangent_points) if len(cl.tangent_points) else np.empty((0, 2))

    # --- extremites, nommees A (depart programme) et B (cote pince)
    for name, p in (("A", pts[0]), ("B", pts[-1])):
        s.circle(p[0], p[1], 1.5, w=0.35, colour=sh.INK, fill="#FFFFFF")
        s.text(p[0], p[1] + 0.9, name, 2.4, anchor="middle", bold=True)

    # --- points de tangence
    for p in tang:
        s.circle(p[0], p[1], 0.55, w=0.2, colour=sh.INK, fill=sh.INK)

    # --- numerotation des coudes, posee en premier : les cotes de segment
    # s'ecarteront ensuite pour ne pas la recouvrir.
    verts = lay.to_paper(cl.vertices)
    placed: list[np.ndarray] = []
    for i, bend in enumerate(tube.bends):
        if i + 1 >= len(verts):
            break
        x, y = verts[i + 1]
        s.circle(x, y, 2.0, w=0.3, colour=sh.RED, fill="#FFFFFF")
        s.text(x, y + 0.85, str(i + 1), 2.4, anchor="middle", bold=True, colour=sh.RED)
        s.text(x + 3.0, y + 3.6, f"{bend.angle:g}°", 2.3, colour=sh.RED, bold=True)
        placed.append(np.array([x + 6.0, y + 3.6]))
        if bend.rotation:
            s.text(x + 3.0, y + 6.4, f"B{bend.rotation:+g}°", 2.1, colour=sh.GREY)
            placed.append(np.array([x + 6.5, y + 6.4]))

    # --- cotation des segments droits
    # Les etiquettes se bousculent des que deux segments courts se suivent :
    # on eloigne progressivement celles qui empietent sur une voisine.
    spans = straight_spans(cl, tube.n_bends)
    for i, (a3, b3) in enumerate(spans):
        if i >= len(tube.straights):
            break
        a = lay.to_paper(np.asarray([a3]))[0]
        b = lay.to_paper(np.asarray([b3]))[0]
        v = b - a
        n = np.array([-v[1], v[0]])
        norm = float(np.linalg.norm(n))
        if norm < 1e-6:
            continue
        n = n / norm
        mid = (a + b) / 2
        pos = mid + n * 4.2
        for attempt in range(8):
            if all(abs(pos[0] - q[0]) > 15.0 or abs(pos[1] - q[1]) > 3.6
                   for q in placed):
                break
            sign = 1 if attempt % 2 == 0 else -1
            pos = mid + n * (4.2 + 3.4 * (attempt // 2 + 1)) * sign
        placed.append(pos)
        s.line(mid[0], mid[1], pos[0], pos[1], w=sh.W_HAIR, colour=sh.GREY,
               dash="1 1")
        s.text(pos[0], pos[1] - 0.9, f"L{i + 1}={tube.straights[i]:.1f}", 2.4,
               anchor="middle", bold=True)

    # --- triedre isometrique
    ox, oy = x0 + 9, y1 - 7
    for lbl, (dx, dy) in (("X", (COS30, -SIN30)), ("Y", (-COS30, -SIN30)), ("Z", (0, -1))):
        s.line(ox, oy, ox + dx * 7, oy + dy * 7, w=0.2, colour=sh.GREY)
        s.text(ox + dx * 9.4, oy + dy * 9.4 + 0.8, lbl, 2.2, anchor="middle",
               colour=sh.GREY)

    # --- encombrement et echelle
    env = cl.envelope
    titre = ("TUBE DROIT — AUCUN CINTRAGE" if not tube.bends
             else "VUE ISOMÉTRIQUE")
    s.text(x1 - 2, y0 + 4.5, f"{titre}  ·  échelle {lay.label}", 2.5,
           anchor="end", bold=True, colour=sh.BLUE)
    s.text(x1 - 2, y1 - 2.5,
           f"encombrement {env[0]:.0f} × {env[1]:.0f} × {env[2]:.0f} mm", 2.3,
           anchor="end", colour=sh.GREY)
    s.text(x0 + 2, y1 - 2.5,
           "A = départ programme (1er segment R12)   ·   B = extrémité côté pince",
           2.3, colour=sh.GREY)
    return lay


def _draw_ortho(s: sh.Sheet, d: PlanData) -> None:
    x0, y0, x1, y1 = ORTHO_BOX
    width = (x1 - x0) / 3.0
    titles = (("XZ", "VUE DE FACE  (XZ)"), ("XY", "VUE DE DESSUS  (XY)"),
              ("YZ", "VUE DE GAUCHE  (YZ)"))
    for i, (plane, title) in enumerate(titles):
        bx0 = x0 + i * width
        box = (bx0, y0, bx0 + width - 2.0, y1)
        s.rect(*box, w=sh.W_THIN, colour=sh.LIGHT)
        s.text(bx0 + 2, y0 + 4.5, title, 2.3, bold=True, colour=sh.BLUE)
        lay = layout(d.centerline, (box[0], box[1] + 5, box[2], box[3]),
                     d.diameter or 6.0, plane=plane, margin=7.0)
        _draw_curve(s, lay, d.centerline, d.diameter, thin=True)
        s.text(box[2] - 2, y1 - 2.2, lay.label, 2.2, anchor="end", colour=sh.GREY)


def _draw_data_column(s: sh.Sheet, d: PlanData) -> None:
    x0, y0, x1, y1 = DATA_BOX
    inner = x1 - 2.0
    label_w = (x1 - x0) - 4.0
    mat = d.material
    tube, tl = d.tube, d.tooling

    def rows_block(top: float, title: str, rows: list[tuple[str, str]],
                   pitch: float = 3.9) -> float:
        height = 10.5 + pitch * len(rows) - 1.0
        y = _block(s, (x0, top, x1, top + height), title)
        for k, v in rows:
            s.label_value(x0 + 2, inner, y, k,
                          sh.ellipsis(s, v, label_w - 26, 2.3, True), 2.3)
            y += pitch
        return top + height + 3.0

    nature = "RIGIDE — cintrable Crippa"
    if mat and mat.kind != materials.RIGIDE:
        nature = "SOUPLE"
    top = rows_block(y0, "Matière", [
        ("Désignation", materials.describe(mat.code) if mat else (tl.material or "—")),
        ("Code BSA", mat.code if mat else "—"),
        ("Nature", nature),
        ("Ø extérieur", f"{d.diameter:g} mm"),
        ("Ø intérieur", f"{mat.bore:g} mm" if mat and mat.bore else "—"),
        ("Épaisseur paroi", f"{d.wall:g} mm" if d.wall else "—"),
    ])

    dev = tube.declared_length or d.centerline.developed
    top = rows_block(top, "Débit", [
        ("Longueur développée R6", f"{dev:.0f} mm"),
        ("Recoupe départ / arrivée", d.recut_label),
        ("Longueur à débiter", f"{d.cut_length:.0f} mm"),
        ("Allongement appliqué", f"{tl.elongation:g} % de la longueur d'arc"),
        ("Développé recalculé", f"{d.centerline.developed:.1f} mm"),
    ])

    total = sum(b.angle for b in tube.bends)
    if tube.bends:
        cintrage = [
            ("Rayon de fibre neutre Rm", f"{tl.clr:g} mm" if tl.clr else "—"),
            ("Nombre de coudes", str(tube.n_bends)),
            ("Somme des angles", f"{total:g}°"),
            ("Rotation B positive", "horaire, vue de B vers A"
             if d.handedness >= 0 else "antihoraire, vue de B vers A"),
            ("Faux pli à 0°", f"{len(tube.false_bends)} fusionné(s)"
             if tube.false_bends else "aucun"),
        ]
        verrous = sum(1 for b in tube.bends if b.locked)
        if verrous:
            # Un R15 de 90 a 94 decrit une equerre : le dire sur le plan evite
            # qu'un sous-traitant croie a un 89 ou un 91 mesure au rapporteur.
            cintrage.append(("Équerres à 90°", f"{verrous} coude(s) verrouillé(s)"))
    else:
        # Aucun coude : afficher un rayon de matrice induirait en erreur.
        cintrage = [
            ("Nombre de coudes", "aucun"),
            ("Opération", "débit droit, sans cintrage"),
            ("Rayon de cintrage", "sans objet"),
            ("Rotation B", "sans objet"),
            ("Origine des cotes", "colonne LONGUEUR de la LFT"),
        ]
    top = rows_block(top, "Cintrage", cintrage)

    l1, _ = fitting_label(d.embout_1, d.diameter)
    l2, _ = fitting_label(d.embout_2, d.diameter)
    rows_block(top, "Extrémités", [
        ("A — départ", l1), ("B — arrivée", l2),
    ])


def _draw_title_block(s: sh.Sheet, d: PlanData, lay: Layout, page: int,
                      pages: int) -> None:
    x0, y0, x1, y1 = TITLE_BOX
    s.rect(x0, y0, x1, y1, w=sh.W_OUTLINE, colour=sh.INK)
    s.rect(x0, y0, x1, y0 + 10.0, w=sh.W_OUTLINE, colour=sh.INK, fill="#EEF3F6")
    s.text(x0 + 2.5, y0 + 4.6, "PLAN DE FABRICATION", 2.9, bold=True)
    nature = "Tube droit — débit seul" if not d.tube.bends else \
        "Tube cintré — Crippa Mastercut"
    s.text(x0 + 2.5, y0 + 8.4, f"{nature} · {d.drawn_on}", 2.1, colour=sh.GREY)

    # --- repere, en gros
    s.text(x0 + 2.5, y0 + 14.4, "REPÈRE", 2.2, colour=sh.GREY)
    s.text(x1 - 2.5, y0 + 16.4, sh.ellipsis(s, d.name, 46, 5.6, True), 5.6,
           anchor="end", bold=True)
    s.hline(x0, x1, y0 + 19.0, w=sh.W_THIN, colour=sh.LIGHT)

    rows = [
        ("Programme", d.tube.program_number or d.tube.program or "—"),
        ("Liste / lot", d.tube.list_number or "—"),
        ("LFT", d.lft or "—"),
        ("Groupe", d.groupe or "—"),
        ("Machine", d.machine or "—"),
        ("Désignation", d.designation or "—"),
    ]
    y = y0 + 23.4
    for k, v in rows:
        s.label_value(x0 + 2.5, x1 - 2.5, y, k,
                      sh.ellipsis(s, v, (x1 - x0) - 28, 2.3, True), 2.3)
        y += 3.9
    s.hline(x0, x1, y - 1.4, w=sh.W_THIN, colour=sh.LIGHT)

    quad = [("Échelle", lay.label), ("Outillage", d.tube.tooling or "—"),
            ("Indice", d.revision), ("Format", f"A4 · page {page}/{pages}")]
    half = (x1 - x0) / 2
    y += 3.4
    for i, (k, v) in enumerate(quad):
        cx = x0 + 2.5 + (i % 2) * half
        cy = y + (i // 2) * 4.0
        s.text(cx, cy, k, 2.1, colour=sh.GREY)
        s.text(cx + half - 5.0, cy, sh.ellipsis(s, v, half - 24, 2.2, True), 2.2,
               anchor="end", bold=True)

    # --- pastille de conformite, calee en bas du cartouche
    s.hline(x0, x1, y1 - 8.5, w=sh.W_THIN, colour=sh.LIGHT)
    s.rect(x0, y1 - 8.5, x1, y1, w=0.0, colour=sh.LIGHT,
           fill={"ERREUR": "#FAE9E7", "ALERTE": "#FBF2E0"}.get(d.status, "#E9F3ED"))
    s.text(x0 + 2.5, y1 - 3.1, "CONTRÔLE AUTOMATIQUE", 2.2, colour=sh.GREY)
    s.text(x1 - 2.5, y1 - 2.8, d.status, 3.4, anchor="end", bold=True,
           colour=d.status_colour)


def _frame(s: sh.Sheet, d: PlanData, page: int, pages: int) -> None:
    s.rect(*FRAME, w=sh.W_FRAME, colour=sh.INK)
    s.text(FRAME[0], FRAME[1] - 2.2,
           f"{d.groupe or '—'} · {d.machine or '—'} · LFT {d.lft or '—'}"
           f" · repère {d.name}", 2.2, colour=sh.GREY)
    s.text(FRAME[2], FRAME[1] - 2.2, f"page {page}/{pages}", 2.2, anchor="end",
           colour=sh.GREY)


# ------------------------------------------------------------------- page 2

def _lra_rows(d: PlanData) -> list[list[str]]:
    tube, cl = d.tube, d.centerline
    rows = [["N°", "Segment L (mm)", "Rotation B (°)", "Angle réel (°)",
             "R15 programmé", "Rayon Rm", "Cumul développé"]]
    if not tube.bends:
        L = sum(tube.straights)
        rows.append(["—", f"{L:.1f}", "—", "—", "—", "—", f"{L:.1f}"])
        return rows
    cumul = 0.0
    for i, b in enumerate(tube.bends):
        L = tube.straights[i] if i < len(tube.straights) else float("nan")
        cumul += L
        arc = (b.clr or 0.0) * math.radians(b.angle)
        cumul += arc
        rows.append([
            str(i + 1), f"{L:.1f}", f"{b.rotation:+g}" if b.rotation else "0",
            f"{b.angle:g}", f"{b.r15:g}" if b.r15 is not None else "—",
            f"{b.clr:g}" if b.clr else "—", f"{cumul:.1f}",
        ])
    if tube.straights:
        cumul += tube.straights[-1]
        rows.append(["fin", f"{tube.straights[-1]:.1f}", "—", "—", "—", "—",
                     f"{cumul:.1f}"])
    return rows


def _xyz_rows(d: PlanData) -> list[list[str]]:
    cl = d.centerline
    rows = [["Point", "X (mm)", "Y (mm)", "Z (mm)"]]
    v = cl.vertices
    for i, p in enumerate(v):
        if i == 0:
            name = "A — départ"
        elif i == len(v) - 1:
            name = "B — arrivée"
        else:
            name = f"P{i} — sommet {i}"
        rows.append([name, f"{p[0]:.2f}", f"{p[1]:.2f}", f"{p[2]:.2f}"])
    return rows


NOTES = [
    "Les longueurs L sont des cotes TANGENTE À TANGENTE, mesurées entre les points "
    "de tangence des coudes adjacents, et non entre les sommets théoriques.",
    "La colonne « Angle réel » donne l'angle du tube fini, retour élastique relâché. "
    "C'est cet angle qui doit être obtenu, quel que soit le moyen de cintrage.",
    "La colonne « R15 programmé » est la consigne d'origine de la Crippa : elle "
    "contient déjà la surcompensation d'élasticité propre à cette machine. Un autre "
    "moyen de production doit appliquer sa PROPRE compensation à partir de l'angle réel.",
    "La rotation B est appliquée AVANT le cintrage du coude concerné, autour de l'axe "
    "du tube. Elle est positive dans le sens HORAIRE pour un observateur placé à "
    "l'extrémité B et regardant vers A, c'est-à-dire en regardant le tube revenir "
    "vers la machine. B et B±360 sont équivalents.",
    "Le rayon Rm est le rayon de la FIBRE NEUTRE. Toute autre valeur change la longueur "
    "développée et rend le débit faux.",
    "Cotes relevées au demi-millimètre et angles au degré, conformément à la méthode de "
    "mesure BSA sur maquette CATIA.",
    "Le repère du dessin est celui du tube : origine au point A, premier segment suivant "
    "+X, première rotation mesurée depuis le plan XZ.",
]

NOTE_VERROU_90 = (
    "Les coudes marqués « 90° » sont des ÉQUERRES. La Crippa les programme "
    "entre R15=90 et R15=94 selon le diamètre et la série ; appliquer le "
    "coefficient d'élasticité à ces valeurs donnerait 89, 90,5 ou 91°, ce qui "
    "n'a jamais été la cote demandée. L'angle à obtenir est 90,0°."
)


def _draw_ends_block(s: sh.Sheet, d: PlanData, box) -> None:
    """Embouts, profondeurs d'emmanchement et tolerances.

    Ces cotes conditionnent la longueur utile du premier et du dernier segment
    quand les extremites sont serties : sans elles, un sous-traitant coupe au
    nu du tube et le tube monte trop court. [DOC 8.1.2]
    """
    x0, y0, x1, y1 = box
    if y1 - y0 < 18:
        return
    y = _block(s, box, "Extrémités et raccordement")
    width = x1 - x0 - 6

    for side, code in (("A — départ", d.embout_1), ("B — arrivée", d.embout_2)):
        label, depth = fitting_label(code, d.diameter)
        s.text(x0 + 2.5, y, side, 2.2, colour=sh.GREY)
        s.text(x0 + 30, y, sh.ellipsis(s, label, width - 32, 2.2, True), 2.2,
               bold=True)
        y += 3.4
        if depth:
            s.text(x0 + 30, y, depth, 2.05, colour=sh.GREY)
            y += 3.2
        y += 0.6

    ermeto = _ermeto_depth(d.diameter)
    if ermeto and y < y1 - 8:
        s.text(x0 + 2.5, y, "Raccord Ermeto EO 24°", 2.2, colour=sh.GREY)
        s.text(x0 + 30, y, f"profondeur d'emmanchement {ermeto}", 2.05)
        y += 3.8
    if y < y1 - 7:
        n = len(d.tube.straights)
        ou = "la longueur L1" if n <= 1 else f"les longueurs L1 et L{n}"
        for line in sh.wrap(
                s, f"Vérifier la profondeur d'emmanchement avant de couper : elle "
                   f"est comprise dans {ou} du tableau LRA. Tolérances de "
                   f"fabrication à convenir avec le donneur d'ordre : la "
                   f"documentation BSA n'en fixe pas.",
                width, 2.0, limit=3):
            if y > y1 - 2.5:
                break
            s.text(x0 + 2.5, y, line, 2.0, colour=sh.GREY)
            y += 2.9
    if d.remarque and y < y1 - 3:
        s.text(x0 + 2.5, y, sh.ellipsis(s, f"Remarque LFT : {d.remarque}", width,
                                        2.05), 2.05, colour=sh.BLUE)


# Un tube droit n'a ni angle, ni rotation, ni rayon : lui servir les notes du
# cintrage ferait douter de tout le reste de la feuille.
NOTES_DROIT = [
    "Pièce débitée droite, sans cintrage. La cote unique est la longueur totale "
    "du tube fini, extrémités comprises.",
    "Cote relevée au demi-millimètre, conformément à la méthode de mesure BSA.",
    "La longueur provient de la colonne LONGUEUR de la LFT : elle fait foi, il "
    "n'y a pas de programme machine pour la recouper.",
    "Le repère du dessin est celui du tube : origine au point A, axe suivant +X.",
    "Tolérances de fabrication à convenir avec le donneur d'ordre : la "
    "documentation BSA n'en fixe pas.",
]


def _draw_page2(s: sh.Sheet, d: PlanData, pages: int) -> None:
    _frame(s, d, 2, pages)

    # --- bandeau d'identification
    x0, y0, x1, y1 = P2_HEAD
    s.rect(x0, y0, x1, y1, w=sh.W_OUTLINE, colour=sh.INK, fill="#EEF3F6")
    s.text(x0 + 2.5, y0 + 5.0, "DONNÉES DE FABRICATION", 3.0, bold=True)
    s.text(x0 + 2.5, y0 + 9.4,
           f"repère {d.name} · programme {d.tube.program_number or d.tube.program or '—'}"
           f" · LFT {d.lft or '—'} · {d.groupe or '—'} / {d.machine or '—'}",
           2.3, colour=sh.GREY)
    s.text(x1 - 2.5, y0 + 5.0, d.status, 3.2, anchor="end", bold=True,
           colour=d.status_colour)
    s.text(x1 - 2.5, y0 + 9.4, f"Ø{d.diameter:g} × {d.wall:g} mm"
           if d.wall else f"Ø{d.diameter:g}", 2.3, anchor="end", colour=sh.GREY)

    # --- colonne gauche : LRA puis XYZ
    lx0, ly0, lx1, ly1 = P2_LEFT
    y = _block(s, (lx0, ly0, lx1, ly0 + 12 + 4.6 * (d.tube.n_bends + 2)),
               "Table LRA — longueur · rotation · angle")
    widths = [9, 24, 23, 23, 24, 17, 25]
    aligns = ["start", "end", "end", "end", "end", "end", "end"]
    y = s.table(lx0 + 2.5, y, widths, _lra_rows(d), size=2.3, row_h=4.6,
                aligns=aligns)

    top = ly0 + 12 + 4.6 * (d.tube.n_bends + 2) + 4
    n_pts = len(d.centerline.vertices)
    h_xyz = 12 + 4.4 * (n_pts + 1) + 5.5
    y = _block(s, (lx0, top, lx1, min(top + h_xyz, ly1)),
               "Points d'intersection — coordonnées XYZ")
    y = s.table(lx0 + 2.5, y, [44, 34, 34, 34], _xyz_rows(d), size=2.3, row_h=4.4,
                aligns=["start", "end", "end", "end"])
    s.text(lx0 + 2.5, y + 1.2,
           "Sommets théoriques de la fibre neutre : intersection des droites "
           "adjacentes, coudes non déduits.", 2.0, colour=sh.GREY)

    # --- extremites et raccordement
    top = min(top + h_xyz, ly1) + 4
    _draw_ends_block(s, d, (lx0, top, lx1, min(top + 46.0, ly1)))

    # --- colonne droite : notes, controles, programme
    rx0, ry0, rx1, ry1 = P2_RIGHT
    notes = list(NOTES if d.tube.bends else NOTES_DROIT)
    if any(b.locked for b in d.tube.bends):
        # Note placee en tete : c'est la seule qui change une cote lue sur le
        # tableau LRA, donc celle qu'il ne faut pas manquer.
        notes.insert(0, NOTE_VERROU_90)
    note_h = 12 + 3.4 * sum(len(sh.wrap(s, n, rx1 - rx0 - 8, 2.15)) + 0.4
                            for n in notes)
    note_h = min(note_h, 92.0)
    y = _block(s, (rx0, ry0, rx1, ry0 + note_h), "Notes de fabrication")
    for i, note in enumerate(notes, start=1):
        lines = sh.wrap(s, note, rx1 - rx0 - 9, 2.15)
        s.text(rx0 + 2.5, y, f"{i}.", 2.15, bold=True, colour=sh.BLUE)
        for line in lines:
            s.text(rx0 + 6.5, y, line, 2.15)
            y += 3.0
        y += 0.9
        if y > ry0 + note_h - 3:
            break

    # --- controles
    top = ry0 + note_h + 3
    ctrl_h = min(12 + 3.6 * max(len(d.issues), 1), 46.0)
    y = _block(s, (rx0, top, rx1, top + ctrl_h), "Contrôles automatiques")
    if not d.issues:
        s.text(rx0 + 2.5, y, "Aucune anomalie détectée.", 2.2, colour=sh.GREEN)
    for issue in d.issues:
        colour = {"erreur": sh.RED, "alerte": sh.ORANGE}.get(issue.level, sh.GREY)
        s.text(rx0 + 2.5, y, issue.level.upper(), 2.0, bold=True, colour=colour)
        s.text(rx0 + 14, y,
               sh.ellipsis(s, f"{issue.code} — {issue.message}", rx1 - rx0 - 18, 2.1),
               2.1)
        y += 3.6
        if y > top + ctrl_h - 3:
            break

    # --- programme d'origine
    top2 = top + ctrl_h + 3
    lines = [ln.strip() for ln in (d.tube.source or "").splitlines() if ln.strip()]
    if not lines:
        # Pas de programme : un cadre vide laisserait croire a une donnee perdue.
        y = _block(s, (rx0, top2, rx1, min(top2 + 22.0, ry1)),
                   "Programme ISO d'origine")
        for ln in sh.wrap(s, "Aucun programme Crippa pour cette pièce : elle est "
                             "débitée droite. Sa cote vient de la colonne LONGUEUR "
                             "de la LFT.", rx1 - rx0 - 6, 2.15, limit=3):
            s.text(rx0 + 2.5, y, ln, 2.15, colour=sh.GREY)
            y += 3.2
    elif top2 < ry1 - 14:
        y = _block(s, (rx0, top2, rx1, ry1), "Programme ISO d'origine")
        avail = int((ry1 - y - 2) / 2.9)
        for ln in lines[:avail]:
            s.text(rx0 + 2.5, y, sh.ellipsis(s, ln, rx1 - rx0 - 6, 2.0, mono=True),
                   2.0, mono=True, colour=sh.GREY)
            y += 2.9
        if len(lines) > avail:
            s.text(rx0 + 2.5, y, f"… {len(lines) - avail} ligne(s) non affichée(s)",
                   2.0, mono=True, colour=sh.GREY)


# --------------------------------------------------------------------- assemblage

def _banner_lines(s: sh.Sheet, d: PlanData) -> list[str]:
    """Texte du bandeau d'erreur, deja decoupe en lignes. Vide s'il n'y en a pas.

    Le message entier doit tenir : le tronquer laissait le lecteur devant
    « 108 mm de matière manquante (156.2 … », c'est-a-dire devant rien.
    """
    if d.status != "ERREUR":
        return []
    x0, x1 = ISO_BOX[0], ISO_BOX[2]
    lignes = sh.wrap(s, "CONTRÔLE EN ERREUR — VÉRIFIER AVANT FABRICATION : "
                        + _first_error(d), (x1 - x0) - 6, 2.5, bold=True,
                     limit=2)
    if unsafe(d.issues):
        # Le plan part seul chez le sous-traitant : il doit dire lui-meme
        # qu'aucun solide ne l'accompagne, et pourquoi.
        lignes.append("AUCUN MODÈLE 3D N'A ÉTÉ PRODUIT : la forme ci-dessous "
                      "est reconstruite à partir d'un programme incomplet.")
    return lignes


def _warn_banner(s: sh.Sheet, d: PlanData) -> float:
    """Bandeau d'avertissement sur une piece dont un controle est en erreur.

    Retourne la hauteur dont la vue doit descendre pour ne pas le recouvrir.

    Le cartouche porte deja la mention, mais un cartouche se lit apres coup. Un
    plan faux qui part en fabrication coute une serie entiere : il faut que le
    doute saute aux yeux avant meme d'avoir lu la piece.
    """
    lignes = _banner_lines(s, d)
    if not lignes:
        return 0.0
    x0, x1 = ISO_BOX[0], ISO_BOX[2]
    y = ISO_BOX[1] + 4.2
    haut = y - 5.4
    bas = y + 2.4 + 3.6 * (len(lignes) - 1)
    s.rect(x0, haut, x1, bas, w=sh.W_THIN, colour=sh.RED, fill="#FAE9E7")
    for i, line in enumerate(lignes):
        s.text((x0 + x1) / 2, y + 3.6 * i,
               sh.ellipsis(s, line, (x1 - x0) - 6, 2.5, True),
               2.5, anchor="middle", bold=True, colour=sh.RED)
    return bas - ISO_BOX[1] + 2.0


def _first_error(d: PlanData) -> str:
    for i in d.issues:
        if i.level == ERROR:
            return f"{i.code} — {i.message}"
    return "voir la page 2"


def draw(s: sh.Sheet, d: PlanData, azimuth: float | None = None,
         pages: int = 2) -> None:
    """Dessine la feuille complete sur la surface fournie."""
    _frame(s, d, 1, pages)
    shift = _warn_banner(s, d)
    lay = _draw_iso(s, d, azimuth, shift)
    _draw_ortho(s, d)
    _draw_data_column(s, d)
    _draw_title_block(s, d, lay, 1, pages)
    if pages >= 2:
        s.page_break()
        _draw_page2(s, d, pages)



# ---------------------------------------------------------------- fiche de debit

# Ce qu'il reste a faire, une fois la matiere commandee. La phrase depend du
# motif : dire « façonner selon le gabarit » d'un tuyau souple qu'on coupe et
# qu'on monte tel quel enverrait un sous-traitant chercher un gabarit inexistant.
CONSIGNE_DEBIT = {
    scope.MATIERE_SOUPLE:
        "Tuyau souple : il se coupe à la longueur indiquée et se monte sans "
        "mise en forme. Les embouts sont sertis selon la nomenclature de la "
        "machine.",
    scope.FAIT_MAIN:
        "Pièce façonnée à la main : la forme se relève sur le gabarit ou sur "
        "la pièce d'origine. Cette fiche ne remplace ni l'un ni l'autre, elle "
        "ne sert qu'à commander la matière.",
    scope.HORS_OUTILLAGE:
        "Ce diamètre n'a pas de matrice de cintrage chez BSA : la forme n'a "
        "pas pu être calculée. Se reporter au programme machine ou au plan "
        "d'origine avant toute mise en forme.",
    scope.DIAMETRE_INCONNU:
        "Le diamètre n'a pu être lu ni dans le programme, ni dans le code "
        "matière : la forme n'a pas pu être calculée. Vérifier CODE_MAT dans "
        "la LFT avant de commander.",
    "": "La forme de cette pièce n'a pas pu être établie à partir de la LFT. "
        "Se reporter au gabarit, au schéma ou au programme d'origine.",
}


def _debit_page(s: sh.Sheet, d: PlanData, motif: str, detail: str) -> None:
    """Une page, sans geometrie, pour une piece dont la forme n'est pas definie.

    Un tuyau souple ou un tube façonne a la main n'a pas de forme calculable.
    Sa matiere, sa longueur et sa quantite restent utiles a l'approvisionnement
    — mais le document doit dire, sans ambiguite possible, que ce n'est pas un
    plan de fabrication. D'ou le bandeau, et l'absence de toute vue.

    Le reste de la page ne reste pas vide pour autant : tout ce que la LFT sait
    de la piece y figure. C'est precisement ce qui permet a quelqu'un de
    retrouver la forme ailleurs — gabarit, dessin, remarque d'atelier.
    """
    x0, y0, x1, y1 = FRAME
    s.rect(*FRAME, w=sh.W_FRAME, colour=sh.INK)
    s.text(x0, y0 - 2.2, f"{d.groupe or '—'} · {d.machine or '—'} · LFT "
           f"{d.lft or '—'} · repère {d.name}", 2.2, colour=sh.GREY)
    s.text(x1, y0 - 2.2, "page 1/1", 2.2, anchor="end", colour=sh.GREY)

    # --- bandeau : il occupe toute la largeur, on ne peut pas le manquer
    by = y0 + 16.0
    s.rect(x0 + 2, y0 + 2, x1 - 2, by, w=sh.W_THIN, colour=sh.ORANGE,
           fill="#FBF2E0")
    s.text((x0 + x1) / 2, y0 + 8.4, "FICHE DE DÉBIT — CE N'EST PAS UN PLAN DE "
           "FABRICATION", 4.4, anchor="middle", bold=True, colour=sh.ORANGE)
    s.text((x0 + x1) / 2, y0 + 13.4,
           "La forme de cette pièce n'est pas définie : aucune cote, aucun "
           "angle, aucun modèle 3D.", 2.5, anchor="middle", colour=sh.GREY)

    lx0, lx1 = x0 + 4, 148.0            # colonne gauche
    rx0, rx1 = 152.0, x1 - 4            # colonne droite
    top1 = by + 5.0
    h1 = 56.0

    # --- identite, a gauche
    y = _block(s, (lx0, top1, lx1, top1 + h1), "Identification")
    for k, v in (("Repère", d.name),
                 ("Programme", d.tube.program_number or "—"),
                 ("Liste / lot", d.tube.list_number or "—"),
                 ("LFT", d.lft or "—"),
                 ("Groupe", d.groupe or "—"),
                 ("Machine", d.machine or "—"),
                 ("Désignation", d.designation or "—")):
        s.label_value(lx0 + 2.5, lx1 - 2.5, y, k,
                      sh.ellipsis(s, v, lx1 - lx0 - 30, 2.4, True), 2.4)
        y += 6.9

    # --- matiere et debit, a droite
    y = _block(s, (rx0, top1, rx1, top1 + h1), "Matière et débit")
    mat = d.material
    longueur = d.lft_length or d.tube.declared_length
    total = (longueur or 0) * (d.quantite or 1)
    for k, v in (("Désignation", mat.designation if mat else "—"),
                 ("Code BSA", mat.code if mat else "—"),
                 ("Nature", (mat.kind if mat else "inconnue").upper()),
                 ("Ø extérieur", f"{d.diameter:g} mm" if d.diameter else "—"),
                 ("Ø intérieur", f"{mat.bore:g} mm" if mat and mat.bore else "—"),
                 ("Longueur à débiter",
                  f"{longueur:.0f} mm" if longueur else "—"),
                 ("Quantité · métrage total",
                  f"{d.quantite:g} × {longueur:.0f} = {total:.0f} mm"
                  if d.quantite and longueur else
                  (f"{d.quantite:g}" if d.quantite else "—"))):
        s.label_value(rx0 + 2.5, rx1 - 2.5, y, k,
                      sh.ellipsis(s, v, rx1 - rx0 - 34, 2.4, True), 2.4)
        y += 6.9

    # --- extremites : sur un tuyau serti, elles conditionnent la coupe
    top2 = top1 + h1 + 4.0
    h2 = 44.0
    y = _block(s, (lx0, top2, lx1, top2 + h2), "Extrémités")
    lab1, dep1 = fitting_label(d.embout_1, d.diameter)
    lab2, dep2 = fitting_label(d.embout_2, d.diameter)
    for k, v, extra in (("Départ", f"{d.embout_1} · {lab1}" if d.embout_1 else "—", dep1),
                        ("Arrivée", f"{d.embout_2} · {lab2}" if d.embout_2 else "—", dep2)):
        s.label_value(lx0 + 2.5, lx1 - 2.5, y, k,
                      sh.ellipsis(s, v, lx1 - lx0 - 24, 2.4, True), 2.4)
        y += 5.2
        if extra:
            s.text(lx0 + 2.5, y, sh.ellipsis(s, extra, lx1 - lx0 - 8, 2.2),
                   2.2, colour=sh.GREY)
            y += 4.6
        else:
            y += 1.4
    if d.recut:
        s.label_value(lx0 + 2.5, lx1 - 2.5, y, "Recoupe", d.recut_label, 2.4)
        y += 6.0
    for line in sh.wrap(s, "La longueur ci-dessus est celle du tuyau nu. Les "
                           "embouts sont montés selon la nomenclature de la "
                           "machine, qui fait foi.", lx1 - lx0 - 8, 2.2, limit=3):
        s.text(lx0 + 2.5, y, line, 2.2, colour=sh.GREY)
        y += 3.3

    # --- ce que la LFT sait encore : c'est par la qu'on retrouve la forme
    y = _block(s, (rx0, top2, rx1, top2 + h2), "Autres données de la LFT")
    for k, v in (("Gabarit", d.gabarit or "—"),
                 ("Dessin", d.dessin or "—"),
                 ("Vitesse", d.vitesse or "—"),
                 ("Fichier source", Path(d.source_file).name if d.source_file else "—")):
        s.label_value(rx0 + 2.5, rx1 - 2.5, y, k,
                      sh.ellipsis(s, v, rx1 - rx0 - 26, 2.4, True), 2.4)
        y += 6.4
    if d.remarque:
        s.text(rx0 + 2.5, y, "REMARQUE", 2.2, bold=True, colour=sh.BLUE)
        y += 4.0
        for line in sh.wrap(s, d.remarque, rx1 - rx0 - 8, 2.3, limit=3):
            s.text(rx0 + 2.5, y, line, 2.3)
            y += 3.4

    # --- motif, sur toute la largeur
    top3 = top2 + h2 + 4.0
    h3 = y1 - 8.0 - top3
    y = _block(s, (lx0, top3, rx1, top3 + h3),
               "Pourquoi cette pièce n'a pas de plan")
    s.text(lx0 + 2.5, y, motif, 2.8, bold=True, colour=sh.ORANGE)
    y += 5.2
    for line in sh.wrap(s, detail, rx1 - lx0 - 10, 2.5, limit=2):
        s.text(lx0 + 2.5, y, line, 2.5)
        y += 3.8
    y += 2.0
    for line in sh.wrap(s, CONSIGNE_DEBIT.get(motif, CONSIGNE_DEBIT[""]),
                        rx1 - lx0 - 10, 2.4, limit=3):
        s.text(lx0 + 2.5, y, line, 2.4, colour=sh.GREY)
        y += 3.6
    y += 2.0
    for line in sh.wrap(
            s, "Cette fiche ne sert qu'à commander la matière et à préparer le "
               "débit. Elle ne décrit ni la forme, ni les cotes, ni le montage : "
               "aucun contrôle de conformité ne peut s'appuyer dessus.",
            rx1 - lx0 - 10, 2.3, limit=3):
        s.text(lx0 + 2.5, y, line, 2.3, colour=sh.GREY)
        y += 3.4

    s.text(x0 + 4, y1 - 4,
           f"Établi le {d.drawn_on} par tubeiso — d'après {Path(d.source_file).name}"
           if d.source_file else f"Établi le {d.drawn_on} par tubeiso",
           2.2, colour=sh.GREY)


def debit_sheet(data: PlanData, path, motif: str = "", detail: str = ""):
    """Ecrit la fiche de debit d'une piece sans forme definie."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = sh.PdfSheet(p, PAGE_W, PAGE_H,
                    title=f"Débit {data.name} — {data.lft or ''}".strip(),
                    subject="Fiche de débit — forme non définie")
    _debit_page(s, data, motif or "forme non définie", detail or "")
    s.page_break()
    s.save()
    return p


def debit_svg(data: PlanData, motif: str = "", detail: str = "") -> str:
    s = sh.SvgSheet(PAGE_W, PAGE_H)
    _debit_page(s, data, motif or "forme non définie", detail or "")
    return s.to_svg(0)


def to_svg(data: PlanData, azimuth: float | None = None) -> str:
    """Page 1 en SVG, pour l'apercu de l'application."""
    s = sh.SvgSheet(PAGE_W, PAGE_H)
    draw(s, data, azimuth, pages=2)
    return s.to_svg(0)


def to_svg_pages(data: PlanData, azimuth: float | None = None) -> list[str]:
    s = sh.SvgSheet(PAGE_W, PAGE_H)
    draw(s, data, azimuth, pages=2)
    return [s.to_svg(i) for i in range(len(s.pages))]


def to_pdf(data: PlanData, path, azimuth: float | None = None):
    """Ecrit le plan complet en PDF. Retourne le chemin."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = sh.PdfSheet(p, PAGE_W, PAGE_H,
                    title=f"Tube {data.name} — {data.lft or ''}".strip(),
                    subject=f"Plan de fabrication — {data.designation or 'tube cintré'}")
    draw(s, data, azimuth, pages=2)
    s.page_break()
    s.save()
    return p


def booklet(items: list[PlanData], path, lot_label: str = "",
            excluded: list[dict] | None = None):
    """Cahier d'atelier : une couverture puis deux pages par tube."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = sh.PdfSheet(p, PAGE_W, PAGE_H, title=f"Cahier LFT {lot_label}",
                    subject="Cahier de fabrication — tubes cintrés")
    _cover(s, items, lot_label, excluded or [])
    for d in items:
        s.page_break()
        draw(s, d, None, pages=2)
    s.page_break()
    s.save()
    return p


COVER_COLS = [24, 34, 46, 12, 24, 16, 18, 24]
COVER_ALIGNS = ["start", "start", "start", "end", "end", "end", "end", "start"]
COVER_HEAD = ["Repère", "Programme", "Matière", "Ø", "Développé", "Coudes",
              "Recoupe", "Contrôle"]
COVER_ROW_H = 4.6
COVER_BOTTOM = 194.0          # au-dela, on deborde du cadre


def _cover(s: sh.Sheet, items: list[PlanData], lot_label: str,
           excluded: list[dict]) -> None:
    """Couverture du cahier : le sommaire du lot, puis ce qui n'a pas de plan.

    Le sommaire est PAGINE. Un lot de soixante-dix pieces tenait autrefois sur
    une seule page : les dernieres lignes sortaient du cadre, et le bloc des
    pieces sans plan n'etait jamais imprime.
    """
    # Une piece qui a une fiche de debit n'est pas une exclusion : le cahier
    # doit dire ou elle est passee, sinon on la cherche dans plans/.
    fiches = [e for e in excluded if e.get("fiche")]
    manquantes = [e for e in excluded if not e.get("fiche")]

    def page_footer() -> None:
        s.text(FRAME[0] + 4, FRAME[3] - 4,
               f"{len(items)} plan(s) · {len(fiches)} fiche(s) de débit · "
               f"{len(manquantes)} sans livrable · "
               f"établi le {date.today().strftime('%d.%m.%Y')} par tubeiso",
               2.2, colour=sh.GREY)

    def new_page(suite: bool) -> float:
        """Ouvre une page de couverture. Retourne l'ordonnee de depart."""
        s.rect(*FRAME, w=sh.W_FRAME, colour=sh.INK)
        if suite:
            s.text(FRAME[0] + 4, 18, f"CAHIER DE FABRICATION — LFT {lot_label}"
                   " (suite)", 3.4, bold=True)
            s.hline(FRAME[0] + 4, FRAME[2] - 4, 22, w=sh.W_THIN, colour=sh.LIGHT)
            return 28.0
        first = items[0] if items else None
        s.text(FRAME[0] + 4, 26, "CAHIER DE FABRICATION", 7.0, bold=True)
        s.text(FRAME[0] + 4, 34, f"LFT {lot_label}", 4.0, colour=sh.BLUE)
        if first:
            s.text(FRAME[0] + 4, 41,
                   f"{first.groupe or '—'} · {first.machine or '—'}"
                   + (f" · {first.designation}" if first.designation else ""),
                   2.8, colour=sh.GREY)
        s.hline(FRAME[0] + 4, FRAME[2] - 4, 46, w=sh.W_THIN, colour=sh.LIGHT)
        return 54.0

    y = new_page(False)
    lignes = [COVER_HEAD]
    for d in items:
        lignes.append([
            d.name, d.tube.program_number or "—",
            sh.ellipsis(s, d.material.designation if d.material else "—", 42, 2.3),
            f"{d.diameter:g}",
            f"{d.tube.declared_length or d.centerline.developed:.0f}",
            str(d.tube.n_bends), f"{d.recut:g}" if d.recut else "—", d.status,
        ])

    corps = lignes[1:]
    while corps:
        tiennent = max(1, int((COVER_BOTTOM - y - 6) / COVER_ROW_H))
        y = s.table(FRAME[0] + 4, y, COVER_COLS, [COVER_HEAD] + corps[:tiennent],
                    size=2.4, row_h=COVER_ROW_H, aligns=COVER_ALIGNS)
        corps = corps[tiennent:]
        if corps:
            page_footer()
            s.page_break()
            y = new_page(True)

    if excluded:
        besoin = 18 + COVER_ROW_H * (min(len(excluded), 16) + 1)
        if y + besoin > COVER_BOTTOM:
            page_footer()
            s.page_break()
            y = new_page(True)
        else:
            y = max(y + 6, y)
        s.text(FRAME[0] + 4, y,
               "PIÈCES SANS PLAN DE CINTRAGE" if fiches else "PIÈCES SANS LIVRABLE",
               3.0, bold=True, colour=sh.ORANGE)
        y += 5.5
        legende = []
        if fiches:
            legende.append(f"{len(fiches)} pièce(s) sortent en fiche de débit "
                           "(dossier debits/) : leur forme n'est pas définie, "
                           "seuls la matière et le débit sont fournis.")
        if manquantes:
            legende.append(f"{len(manquantes)} ligne(s) ne produisent rien.")
        for line in sh.wrap(s, " ".join(legende), FRAME[2] - FRAME[0] - 10,
                            2.2, limit=2):
            s.text(FRAME[0] + 4, y, line, 2.2, colour=sh.GREY)
            y += 3.3
        y += 2.0
        ex = [["Repère", "Sortie", "Motif", "Détail"]]
        for e in excluded[:16]:
            ex.append([str(e.get("repere", "")),
                       "fiche de débit" if e.get("fiche") else "rien",
                       str(e.get("motif", "")),
                       sh.ellipsis(s, str(e.get("detail", "")), 120, 2.2)])
        y = s.table(FRAME[0] + 4, y, [24, 34, 48, 130], ex, size=2.2, row_h=4.0)
        if len(excluded) > 16:
            s.text(FRAME[0] + 4, y + 1, f"… et {len(excluded) - 16} autre(s) — "
                   "la liste complète est dans INDEX.xlsx.", 2.2, colour=sh.GREY)

    page_footer()


# --------------------------------------------------------------------------- DXF

def to_dxf(tube: TubeProgram, cl: Centerline, path: str) -> None:
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for name, colour in (("FIBRE_NEUTRE", 5), ("SOMMETS", 1), ("TANGENCE", 3)):
        if name not in doc.layers:
            doc.layers.add(name, color=colour)

    msp.add_polyline3d([tuple(p) for p in cl.points],
                       dxfattribs={"layer": "FIBRE_NEUTRE"})
    msp.add_polyline3d([tuple(p) for p in cl.vertices],
                       dxfattribs={"layer": "SOMMETS", "linetype": "DASHED"})
    for p in cl.tangent_points:
        msp.add_circle(tuple(p), radius=max(0.5, (tube.diameter or 6) / 6),
                       dxfattribs={"layer": "TANGENCE"})
    doc.saveas(path)


__all__ = ["PlanData", "Layout", "layout", "project", "project_ortho",
           "best_azimuth", "draw", "to_svg", "to_svg_pages", "to_pdf",
           "booklet", "debit_sheet", "debit_svg", "to_dxf", "fitting_label",
           "straight_spans", "NOTES"]
