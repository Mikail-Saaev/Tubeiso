"""Lecture de fichiers STEP et extraction analytique exacte.

Deux niveaux de lecture :

1. **Tesselation** — un maillage triangulaire pour l'affichage 3D. C'est une
   approximation, et elle ne sert QU'A afficher.

2. **Extraction analytique** — on lit les surfaces exactes du modele B-Rep.
   Un tube cintre balaye est fait de faces cylindriques (les parties droites)
   et toriques (les coudes). Chaque cylindre porte son axe et son rayon
   exacts, chaque tore porte son centre, son rayon de cintrage et son angle
   balaye exacts. On reconstruit la fibre neutre a partir de ces valeurs.

C'est le point qui rend les cotations fiables : elles ne sont jamais mesurees
sur le maillage, elles sont lues dans la definition exacte de la surface. Un
segment de 63.591 mm est rendu comme 63.591 mm, pas comme 63.59 ± la fleche
de tesselation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import geometry

TOL = 1e-6
JOIN_TOL = 1e-4          # tolerance de raccordement entre deux faces voisines


class StepError(RuntimeError):
    pass


def _occ():
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRepTools import BRepTools
        from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS
    except ImportError as exc:                                  # pragma: no cover
        raise StepError(
            f"noyau CAO indisponible : {type(exc).__name__}: {exc}"
        ) from exc
    return dict(
        BRep_Tool=BRep_Tool, BRepAdaptor_Surface=BRepAdaptor_Surface,
        BRepMesh_IncrementalMesh=BRepMesh_IncrementalMesh, BRepTools=BRepTools,
        GeomAbs_Cylinder=GeomAbs_Cylinder, GeomAbs_Torus=GeomAbs_Torus,
        TopAbs_FACE=TopAbs_FACE, TopExp_Explorer=TopExp_Explorer,
        TopLoc_Location=TopLoc_Location, TopoDS=TopoDS,
    )


def _v(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


# --------------------------------------------------------------------- lecture

def load(path: str | Path):
    """Ouvre un STEP et retourne la forme OCC."""
    try:
        import cadquery as cq
    except ImportError as exc:                                  # pragma: no cover
        raise StepError(f"noyau CAO indisponible : "
                        f"{type(exc).__name__}: {exc}") from exc
    p = Path(path)
    if not p.exists():
        raise StepError(f"fichier introuvable : {p}")
    try:
        wp = cq.importers.importStep(str(p))
    except Exception as exc:
        raise StepError(f"STEP illisible : {exc}") from exc
    shapes = wp.vals()
    if not shapes:
        raise StepError("aucune forme dans le fichier")
    return shapes[0] if len(shapes) == 1 else shapes[0].fuse(*shapes[1:])


def tessellate(shape, deflection: float = 0.05, angular: float = 0.2) -> dict:
    """Maillage pour l'affichage. `deflection` est la fleche maximale en mm."""
    verts, tris = shape.tessellate(deflection, angular)
    positions = np.array([[v.x, v.y, v.z] for v in verts], dtype=np.float32)
    indices = np.array(tris, dtype=np.uint32).reshape(-1)
    normals = _vertex_normals(positions, indices)
    return {
        "positions": positions.ravel().tolist(),
        "normals": normals.ravel().tolist(),
        "indices": indices.tolist(),
        "deflection": deflection,
    }


def _vertex_normals(pos: np.ndarray, idx: np.ndarray) -> np.ndarray:
    n = np.zeros_like(pos)
    tri = idx.reshape(-1, 3)
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(b - a, c - a)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    norm[norm < TOL] = 1.0
    return (n / norm).astype(np.float32)


# ------------------------------------------------------ extraction analytique

@dataclass
class Feature:
    """Une portion de fibre neutre lue dans une surface exacte."""

    kind: str                  # "line" ou "arc"
    start: np.ndarray
    end: np.ndarray
    tube_radius: float
    bend_radius: float = 0.0
    angle: float = 0.0         # degres balayes
    centre: np.ndarray | None = None
    axis: np.ndarray | None = None
    mid: np.ndarray | None = None
    radii: set = field(default_factory=set)

    def __post_init__(self) -> None:
        self.radii.add(round(self.tube_radius, 5))

    @property
    def length(self) -> float:
        if self.kind == "line":
            return float(np.linalg.norm(self.end - self.start))
        return self.bend_radius * math.radians(self.angle)

    def reversed_(self) -> "Feature":
        return Feature(self.kind, self.end.copy(), self.start.copy(),
                       self.tube_radius, self.bend_radius, self.angle,
                       None if self.centre is None else self.centre.copy(),
                       None if self.axis is None else -self.axis,
                       None if self.mid is None else self.mid.copy(),
                       set(self.radii))


def features(shape) -> list[Feature]:
    """Lit les faces cylindriques et toriques, exactement."""
    o = _occ()
    out: list[Feature] = []
    exp = o["TopExp_Explorer"](shape.wrapped, o["TopAbs_FACE"])
    seen: list[Feature] = []
    while exp.More():
        face = o["TopoDS"].Face_s(exp.Current())
        exp.Next()
        surf = o["BRepAdaptor_Surface"](face, True)
        umin, umax, vmin, vmax = o["BRepTools"].UVBounds_s(face)
        t = surf.GetType()

        if t == o["GeomAbs_Cylinder"]:
            cyl = surf.Cylinder()
            ax = cyl.Axis()
            origin, direction = _v(ax.Location()), _v(ax.Direction())
            direction /= np.linalg.norm(direction)
            # v est l'abscisse le long de l'axe pour une surface cylindrique
            a, b = origin + vmin * direction, origin + vmax * direction
            f = Feature("line", a, b, float(cyl.Radius()))
            if f.length > TOL:
                out.append(f)

        elif t == o["GeomAbs_Torus"]:
            tor = surf.Torus()
            pos = tor.Position()
            centre = _v(pos.Location())
            zdir = _v(pos.Direction());  zdir /= np.linalg.norm(zdir)
            xdir = _v(pos.XDirection()); xdir /= np.linalg.norm(xdir)
            ydir = np.cross(zdir, xdir)
            R, r = float(tor.MajorRadius()), float(tor.MinorRadius())
            # u est l'angle autour de l'axe du tore : c'est l'angle de cintrage
            def on_axis(u):
                return centre + R * (math.cos(u) * xdir + math.sin(u) * ydir)
            sweep = math.degrees(umax - umin)
            f = Feature("arc", on_axis(umin), on_axis(umax), r, R, sweep,
                        centre, zdir, on_axis((umin + umax) / 2))
            if f.length > TOL:
                out.append(f)

    return _dedupe(out)


def _dedupe(items: list[Feature]) -> list[Feature]:
    """Une meme portion de tube peut donner plusieurs faces (couture, paroi
    interieure et exterieure). On ne garde qu'une entree par portion d'axe."""
    kept: list[Feature] = []
    for f in items:
        dup = None
        for k in kept:
            if k.kind != f.kind:
                continue
            same = (np.allclose(k.start, f.start, atol=JOIN_TOL)
                    and np.allclose(k.end, f.end, atol=JOIN_TOL))
            flipped = (np.allclose(k.start, f.end, atol=JOIN_TOL)
                       and np.allclose(k.end, f.start, atol=JOIN_TOL))
            if same or flipped:
                dup = k
                break
        if dup is None:
            kept.append(f)
        else:            # meme portion : on cumule les rayons vus (ext. et int.)
            dup.radii |= f.radii
            dup.tube_radius = max(dup.radii)
    return kept


def chain(items: list[Feature]) -> list[Feature]:
    """Ordonne les portions bout a bout pour former une fibre neutre continue."""
    if not items:
        return []
    pool = list(items)
    ordered = [pool.pop(0)]

    def attach(front: bool) -> bool:
        anchor = ordered[0].start if front else ordered[-1].end
        for i, f in enumerate(pool):
            if np.allclose(f.end, anchor, atol=JOIN_TOL):
                g = f if front else f.reversed_()
                (ordered.insert(0, g) if front else ordered.append(g))
                pool.pop(i)
                return True
            if np.allclose(f.start, anchor, atol=JOIN_TOL):
                g = f.reversed_() if front else f
                (ordered.insert(0, g) if front else ordered.append(g))
                pool.pop(i)
                return True
        return False

    while attach(False) or attach(True):
        pass
    return ordered


def to_lra(ordered: list[Feature]) -> dict:
    """Table LRA exacte : longueurs, rotations, angles, rayons."""
    straights = [f.length for f in ordered if f.kind == "line"]
    bends = [f for f in ordered if f.kind == "arc"]

    rotations = [0.0]
    for prev, cur in zip(bends, bends[1:]):
        n1, n2 = prev.axis, cur.axis
        # direction du tube a la sortie du coude precedent
        t = cur.start - prev.end
        nt = np.linalg.norm(t)
        t = t / nt if nt > TOL else np.cross(n1, [1.0, 0.0, 0.0])
        a = n1 - np.dot(n1, t) * t
        b = n2 - np.dot(n2, t) * t
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < TOL or nb < TOL:
            rotations.append(0.0)
            continue
        a, b = a / na, b / nb
        ang = math.degrees(math.atan2(float(np.dot(np.cross(a, b), t)),
                                      float(np.clip(np.dot(a, b), -1, 1))))
        # `ang` est l'angle direct autour de la tangente. L'axe B de la machine
        # compte dans l'autre sens (voir geometry.B_SIGN) : sans ce facteur,
        # relire un STEP produit par l'application rendait des rotations de
        # signe oppose a celles du programme d'origine.
        rotations.append(geometry.B_SIGN * ang)

    radii = sorted({round(r, 4) for f in ordered for r in f.radii})
    return {
        "segments": [round(v, 4) for v in straights],
        "rotations": [round(v, 4) for v in rotations],
        "angles": [round(f.angle, 4) for f in bends],
        "bend_radii": [round(f.bend_radius, 4) for f in bends],
        "tube_radius": radii[-1] if radii else None,
        "wall": (round(radii[-1] - radii[0], 4)
                 if len(radii) > 1 else None),
        "developed": round(sum(f.length for f in ordered), 4),
        "vertices": [[round(v, 4) for v in f.start] for f in ordered]
                    + ([[round(v, 4) for v in ordered[-1].end]] if ordered else []),
    }


def analyse(path: str | Path, deflection: float = 0.05) -> dict:
    """Lecture complete d'un STEP : maillage, cotations exactes, controles."""
    shape = load(path)
    feats = chain(features(shape))
    lra = to_lra(feats)

    bb = shape.BoundingBox()
    try:
        volume = round(shape.Volume(), 3)
    except Exception:
        volume = None

    warnings = []
    if not feats:
        warnings.append(
            "aucune face cylindrique ou torique : ce STEP n'est probablement "
            "pas un tube balaye, seul le maillage sera exploitable")
    elif len([f for f in feats if f.kind == "line"]) != len(
            [f for f in feats if f.kind == "arc"]) + 1:
        warnings.append(
            "la chaine droite/coude est incomplete : le solide comporte "
            "peut-etre plusieurs corps ou des faces non reconnues")
    rs = {round(f.bend_radius, 3) for f in feats if f.kind == "arc"}
    if len(rs) > 1:
        warnings.append(f"plusieurs rayons de cintrage detectes : {sorted(rs)}")

    return {
        "file": str(Path(path).name),
        "mesh": tessellate(shape, deflection),
        "lra": lra,
        "features": [
            {"kind": f.kind, "length": round(f.length, 4),
             "angle": round(f.angle, 4) if f.kind == "arc" else None,
             "bend_radius": round(f.bend_radius, 4) if f.kind == "arc" else None,
             "start": [round(v, 4) for v in f.start],
             "end": [round(v, 4) for v in f.end]}
            for f in feats
        ],
        "bbox": {"min": [round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)],
                 "max": [round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)],
                 "size": [round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)]},
        "volume": volume,
        "warnings": warnings,
    }
