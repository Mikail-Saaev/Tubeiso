"""Mise en plan isometrique.

Sortie SVG en millimetres papier (format A4 paysage), directement imprimable,
et export DXF pour reprise en CAO.

L'orientation de la piece n'est pas donnee par le programme machine : on
cherche donc l'azimut qui minimise les recouvrements a l'ecran. Le jour ou tu
disposeras du repere d'assemblage, il suffira de forcer `azimuth`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy as np

from .geometry import Centerline
from .model import Tooling, TubeProgram
from .validate import ERROR, WARN, Issue

COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))

PAGE_W, PAGE_H = 297.0, 210.0
FRAME = (10.0, 10.0, 287.0, 200.0)
DRAW = (14.0, 14.0, 186.0, 148.0)
TABLE = (14.0, 154.0, 120.0, 196.0)
BLOCK = (190.0, 148.0, 287.0, 196.0)


def project(pts: np.ndarray, azimuth: float = 0.0) -> np.ndarray:
    """Projection isometrique classique a 30 degres, apres rotation d'azimut."""
    a = math.radians(azimuth)
    c, s = math.cos(a), math.sin(a)
    x = pts[:, 0] * c - pts[:, 1] * s
    y = pts[:, 0] * s + pts[:, 1] * c
    z = pts[:, 2]
    return np.column_stack([(x - y) * COS30, -((x + y) * SIN30 - z)])


def best_azimuth(cl: Centerline, diameter: float, step: float = 10.0) -> float:
    """Azimut qui separe le mieux les portions eloignees en 3D."""
    p = cl.points
    if len(p) < 4:
        return 0.0
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
    azimuth: float

    def to_paper(self, pts: np.ndarray) -> np.ndarray:
        return project(pts, self.azimuth) * self.scale + self.offset


def layout(cl: Centerline, diameter: float, azimuth: float | None = None) -> Layout:
    az = best_azimuth(cl, diameter) if azimuth is None else azimuth
    q = project(cl.points, az)
    lo, hi = q.min(axis=0), q.max(axis=0)
    span = np.maximum(hi - lo, 1e-6)
    x0, y0, x1, y1 = DRAW
    avail = np.array([x1 - x0 - 24.0, y1 - y0 - 20.0])
    scale = float(min(avail / span))
    centre = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    offset = centre - (lo + hi) / 2 * scale
    return Layout(scale, offset, az)


# --------------------------------------------------------------------------- SVG

def _t(x: float, y: float, s: str, size: float = 2.5, anchor: str = "start",
       weight: str = "normal", fill: str = "#111") -> str:
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" '
            f'font-family="Helvetica,Arial,sans-serif" font-weight="{weight}" '
            f'text-anchor="{anchor}">{escape(s)}</text>')


def _line(x1, y1, x2, y2, w=0.25, colour="#111", dash="") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{colour}" stroke-width="{w}"{d}/>')


def _rect(x0, y0, x1, y1, w=0.35) -> str:
    return (f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" '
            f'height="{y1 - y0:.2f}" fill="none" stroke="#111" stroke-width="{w}"/>')


def to_svg(tube: TubeProgram, cl: Centerline, tooling: Tooling,
           issues: list[Issue] | None = None, azimuth: float | None = None) -> str:
    issues = issues or []
    lay = layout(cl, tube.diameter or 6.0, azimuth)
    pts = lay.to_paper(cl.points)
    verts = lay.to_paper(cl.vertices)
    tang = lay.to_paper(cl.tangent_points) if len(cl.tangent_points) else np.empty((0, 2))

    body = [f'<rect width="{PAGE_W}" height="{PAGE_H}" fill="#fff"/>', _rect(*FRAME, w=0.5)]

    # --- le tube : trace epais clair + fibre neutre fine
    path = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
    wall = max(0.8, (tube.diameter or 6.0) * lay.scale)
    body.append(f'<path d="{path}" fill="none" stroke="#cfd8dc" stroke-width="{wall:.2f}" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')
    body.append(f'<path d="{path}" fill="none" stroke="#111" stroke-width="0.45" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')

    # --- extremites
    for i, (x, y) in enumerate(pts[[0, -1]]):
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.1" fill="#fff" '
                    f'stroke="#111" stroke-width="0.35"/>')
        body.append(_t(x + 2.2, y - 1.6, f"E{i + 1}", 2.4, weight="bold"))

    # --- points de tangence
    for x, y in tang:
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.5" fill="#111"/>')

    # --- cotation des droites : texte au milieu, decale perpendiculairement
    seg_ends = [(verts[i], verts[i + 1]) for i in range(len(verts) - 1)]
    for i, (a, b) in enumerate(seg_ends):
        if i >= len(tube.straights):
            break
        mid = (a + b) / 2
        v = b - a
        n = np.array([-v[1], v[0]])
        norm = np.linalg.norm(n)
        if norm < 1e-6:
            continue
        n = n / norm * 4.0
        pos = mid + n
        body.append(_line(*mid, *pos, w=0.15, colour="#888", dash="1 1"))
        body.append(_t(pos[0], pos[1] - 0.8, f"{tube.straights[i]:.1f}", 2.4,
                       anchor="middle", weight="bold"))

    # --- annotation des coudes
    for i, bend in enumerate(tube.bends):
        if i + 1 >= len(verts):
            break
        x, y = verts[i + 1]
        label = f"{bend.angle:g}°"
        if bend.springback:
            label += f" (R15 {bend.r15:g})"
        if bend.rotation:
            label += f"  R{bend.rotation:g}°"
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.4" fill="none" '
                    f'stroke="#c62828" stroke-width="0.3"/>')
        body.append(_t(x + 2.6, y + 3.4, label, 2.4, fill="#c62828", weight="bold"))

    # --- reperes d'axes isometriques
    ox, oy = DRAW[0] + 8, DRAW[3] - 6
    for lbl, (dx, dy) in (("X", (COS30, -SIN30)), ("Y", (-COS30, -SIN30)), ("Z", (0, -1))):
        body.append(_line(ox, oy, ox + dx * 7, oy + dy * 7, w=0.2, colour="#666"))
        body.append(_t(ox + dx * 9, oy + dy * 9 + 0.8, lbl, 2.2, anchor="middle", fill="#666"))

    body.append(_table(tube))
    body.append(_titleblock(tube, cl, tooling, lay, issues))

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_W}mm" '
            f'height="{PAGE_H}mm" viewBox="0 0 {PAGE_W} {PAGE_H}">'
            + "".join(body) + "</svg>")


def _table(tube: TubeProgram) -> str:
    x0, y0, x1, y1 = TABLE
    out = [_rect(x0, y0, x1, y1), _t(x0 + 2, y0 + 5, "Donnees de cintrage (LRA)", 3.0,
                                     weight="bold")]
    cols = [x0 + 2, x0 + 16, x0 + 40, x0 + 62, x0 + 84, x0 + 104]
    heads = ["Coude", "Longueur L", "Rotation R", "Angle reel", "R15 prog.",
             "Rayon"]
    yh = y0 + 11
    for cx, h in zip(cols, heads):
        out.append(_t(cx, yh, h, 2.4, weight="bold"))
    out.append(_line(x0, yh + 1.6, x1, yh + 1.6, w=0.25))
    y = yh + 6
    for i, bend in enumerate(tube.bends):
        L = tube.straights[i] if i < len(tube.straights) else float("nan")
        vals = [f"{i + 1}", f"{L:.1f}", f"{bend.rotation:g}°", f"{bend.angle:g}°",
                f"{bend.r15:g}°" if bend.r15 is not None else "-",
                f"{bend.clr:.1f}" if bend.clr else "?"]
        for cx, v in zip(cols, vals):
            out.append(_t(cx, y, v, 2.4))
        y += 5
        if y > y1 - 8:
            break
    if tube.straights:
        out.append(_line(x0, y - 3.4, x1, y - 3.4, w=0.15, colour="#888"))
        out.append(_t(cols[0], y + 1, "Sortie", 2.4, weight="bold"))
        out.append(_t(cols[1], y + 1, f"{tube.straights[-1]:.1f}", 2.4))
    return "".join(out)


def _titleblock(tube: TubeProgram, cl: Centerline, tooling: Tooling,
                lay: Layout, issues: list[Issue]) -> str:
    x0, y0, x1, y1 = BLOCK
    lvl = ERROR if any(i.level == ERROR for i in issues) else (
        WARN if any(i.level == WARN for i in issues) else "conforme")
    colour = {"erreur": "#c62828", "alerte": "#ef6c00"}.get(lvl, "#2e7d32")
    delta = ""
    if tube.declared_length:
        delta = f"{cl.developed - tube.declared_length:+.1f}"

    rows = [
        ("Repere", tube.ref),
        ("Diametre", f"Ø{tube.diameter:g}" + (f" x {tooling.wall:g}" if tooling.wall else "")),
        ("Matiere", tooling.material or "-"),
        ("Outillage", f"{tooling.name}  R={tooling.clr:g}" if tooling.clr else tooling.name),
        ("Developpe", f"{cl.developed:.1f} mm"),
        ("Declare (R6)", f"{tube.declared_length:.0f} mm  ({delta})"
         if tube.declared_length else "-"),
        ("Echelle", f"1:{1 / lay.scale:.1f}" if lay.scale < 1 else f"{lay.scale:.1f}:1"),
        ("Coudes", str(tube.n_bends)),
        ("Programme", tube.program_number or "-"),
        ("Liste / lot", tube.list_number or "-"),
    ]
    out = [_rect(x0, y0, x1, y1), _t(x0 + 2, y0 + 5, "Plan isometrique de tube cintre",
                                     3.0, weight="bold")]
    y = y0 + 11
    for k, v in rows:
        out.append(_t(x0 + 2, y, k, 2.3, fill="#555"))
        out.append(_t(x1 - 2, y, v, 2.3, anchor="end", weight="bold"))
        y += 3.9
    out.append(_line(x0, y - 2.6, x1, y - 2.6, w=0.2))
    out.append(_t(x0 + 2, y + 1.6, "Statut", 2.3, fill="#555"))
    out.append(_t(x1 - 2, y + 1.6, lvl.upper(), 2.6, anchor="end", weight="bold",
                  fill=colour))
    return "".join(out)


# --------------------------------------------------------------------------- DXF

def to_dxf(tube: TubeProgram, cl: Centerline, path: str) -> None:
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for name, colour in (("FIBRE_NEUTRE", 5), ("SOMMETS", 1), ("TANGENCE", 3)):
        if name not in doc.layers:
            doc.layers.add(name, color=colour)

    msp.add_polyline3d([tuple(p) for p in cl.points], dxfattribs={"layer": "FIBRE_NEUTRE"})
    msp.add_polyline3d([tuple(p) for p in cl.vertices],
                       dxfattribs={"layer": "SOMMETS", "linetype": "DASHED"})
    for p in cl.tangent_points:
        msp.add_circle(tuple(p), radius=max(0.5, tube.diameter / 6),
                       dxfattribs={"layer": "TANGENCE"})
    doc.saveas(path)
