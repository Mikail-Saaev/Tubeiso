"""Generation du modele solide 3D et exports CAO.

Le tube est construit par balayage d'une couronne (diametre exterieur moins
epaisseur de paroi) le long de la fibre neutre exacte : des droites et des
arcs de cercle, pas une polyligne approchee. Le solide obtenu est donc
geometriquement exact, ce qui compte pour de la sous-traitance.

Formats produits :

  STEP AP214  format d'echange universel. C'est ce qu'attend un sous-traitant,
              et ce que lisent les logiciels de cintrage qui re-extraient les
              donnees LRA depuis un solide.
  STL         maillage, pour visualisation ou impression 3D. Pas un format de
              fabrication.
  BREP        format natif OpenCascade, utile pour du debug.

Le fichier STEP contient deux entites nommees : le solide du tube et sa fibre
neutre sous forme de wire. La seconde permet au sous-traitant de retrouver
directement les points de cintrage sans avoir a les extraire du solide.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import bsa, geometry
from .geometry import Centerline
from .model import Tooling, TubeProgram

MIN_EDGE = 1e-6


class SolidError(RuntimeError):
    """Le balayage a echoue : geometrie degeneree ou rayon trop grand."""


def _cq():
    try:
        import cadquery as cq
    except ImportError as exc:                                  # pragma: no cover
        raise SolidError(
            f"noyau CAO indisponible : {type(exc).__name__}: {exc}"
        ) from exc
    return cq


def _vec(cq, p) -> "object":
    return cq.Vector(float(p[0]), float(p[1]), float(p[2]))


def centerline_wire(cl: Centerline):
    """Assemble la fibre neutre en un wire CAO exact."""
    cq = _cq()
    edges = []
    for prim in cl.primitives:
        if prim.length < MIN_EDGE:
            continue
        if prim.kind == "line":
            edges.append(cq.Edge.makeLine(_vec(cq, prim.start), _vec(cq, prim.end)))
        else:
            edges.append(cq.Edge.makeThreePointArc(
                _vec(cq, prim.start), _vec(cq, prim.mid), _vec(cq, prim.end)))
    if not edges:
        raise SolidError("fibre neutre vide")
    return cq.Wire.assembleEdges(edges)


def build_solid(tube: TubeProgram, cl: Centerline, wall: float | None = None):
    """Balaie une couronne le long de la fibre neutre. Retourne un cq.Solid."""
    cq = _cq()
    outer_r = float(tube.diameter) / 2.0
    if outer_r <= 0:
        raise SolidError(f"diametre invalide : {tube.diameter}")

    path = centerline_wire(cl)
    origin = _vec(cq, cl.points[0])
    normal = _vec(cq, cl.start_tangent())

    outer = cq.Wire.makeCircle(outer_r, origin, normal)
    inners = []
    if wall and 0 < wall < outer_r:
        inners.append(cq.Wire.makeCircle(outer_r - wall, origin, normal))

    try:
        solid = cq.Solid.sweep(outer, inners, path, makeSolid=True, isFrenet=True)
    except Exception as exc:                                    # pragma: no cover
        raise SolidError(
            f"balayage impossible sur {tube.ref} : {exc}. Cause frequente : un "
            "segment droit plus court que le rayon de cintrage, ou deux coudes "
            "consecutifs sans droite entre eux."
        ) from exc
    return solid


def export(tube: TubeProgram, cl: Centerline, out_dir: str | Path,
           tooling: Tooling | None = None, formats=("step",),
           with_centerline: bool = True) -> list[Path]:
    """Ecrit les fichiers CAO. Retourne la liste des chemins produits."""
    cq = _cq()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    wall = tooling.wall if tooling else None
    solid = build_solid(tube, cl, wall)
    written: list[Path] = []

    for fmt in formats:
        fmt = fmt.lower()
        target = out / f"{tube.ref}.{'stp' if fmt == 'step' else fmt}"
        if fmt == "step" and with_centerline:
            asm = cq.Assembly(name=f"TUBE_{tube.ref}")
            asm.add(cq.Workplane(obj=solid), name=f"tube_{tube.ref}")
            asm.add(cq.Workplane(obj=centerline_wire(cl)),
                    name=f"fibre_neutre_{tube.ref}")
            asm.save(str(target), exportType="STEP")
        else:
            cq.exporters.export(cq.Workplane(obj=solid), str(target),
                                exportType=fmt.upper())
        written.append(target)
    return written


def report(tube: TubeProgram, cl: Centerline, tooling: Tooling | None = None) -> dict:
    """Metriques du solide, pour controle avant expedition au sous-traitant."""
    solid = build_solid(tube, cl, tooling.wall if tooling else None)
    bb = solid.BoundingBox()
    d = int(tube.diameter)
    return {
        "repere": tube.ref,
        "diametre": tube.diameter,
        "paroi": tooling.wall if tooling else None,
        "rayon_cintrage": bsa.RM.get(d),
        "coudes": tube.n_bends,
        "developpe_mm": round(cl.developed, 2),
        "volume_mm3": round(solid.Volume(), 1),
        "encombrement_mm": [round(bb.xlen, 1), round(bb.ylen, 1), round(bb.zlen, 1)],
        "etanche": solid.isValid(),
    }


def preview_png(tube: TubeProgram, cl: Centerline, path: str | Path,
                tooling: Tooling | None = None, width: int = 900) -> Path:
    """Rendu PNG rapide du solide, pour verification visuelle."""
    cq = _cq()
    solid = build_solid(tube, cl, tooling.wall if tooling else None)
    p = Path(path)
    svg = p.with_suffix(".svg")
    cq.exporters.export(
        cq.Workplane(obj=solid), str(svg), exportType="SVG",
        opt={"width": width, "height": int(width * 0.7), "marginLeft": 20,
             "marginTop": 20, "projectionDir": (0.6, 0.6, 0.5),
             "showAxes": False, "strokeWidth": 0.5},
    )
    return svg
