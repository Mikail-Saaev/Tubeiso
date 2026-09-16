"""Reconstruction de la fibre neutre 3D a partir des donnees LRA.

Convention adoptee :
  - le tube part de l'origine et progresse selon +X
  - `u` est le vecteur de reference qui pointe vers le centre de courbure
  - a chaque coude, on tourne d'abord `u` autour de `t` (rotation B),
    puis on courbe dans le plan (t, u)
  - une rotation B positive est trigonometrique vue depuis l'aval

Le signe de B et le sens de reference dependent de la machine. Si les pieces
sortent en miroir, inverser `handedness` (voir build()).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .model import TubeProgram


def rodrigues(v: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rotation de v autour de axis (unitaire) d'un angle en radians."""
    c, s = math.cos(angle), math.sin(angle)
    return v * c + np.cross(axis, v) * s + axis * np.dot(axis, v) * (1.0 - c)


@dataclass
class Primitive:
    """Element exact de la fibre neutre, pour la CAO.

    kind == "line" : start -> end
    kind == "arc"  : arc de cercle passant par start, mid, end
    """

    kind: str
    start: np.ndarray
    end: np.ndarray
    mid: np.ndarray | None = None
    centre: np.ndarray | None = None
    radius: float = 0.0
    angle: float = 0.0          # degres
    axis: np.ndarray | None = None

    @property
    def length(self) -> float:
        if self.kind == "line":
            return float(np.linalg.norm(self.end - self.start))
        return self.radius * math.radians(self.angle)


@dataclass
class Centerline:
    points: np.ndarray          # polyligne echantillonnee (N x 3)
    vertices: np.ndarray        # points d'intersection theoriques (M x 3)
    tangent_points: np.ndarray  # entrees/sorties de coude (2n x 3)
    developed: float            # longueur developpee recalculee
    ends: np.ndarray            # les 2 extremites (2 x 3)
    primitives: list = None     # droites et arcs exacts, pour l'export CAO

    def __post_init__(self) -> None:
        if self.primitives is None:
            self.primitives = []

    def start_tangent(self) -> np.ndarray:
        v = self.points[1] - self.points[0]
        return v / np.linalg.norm(v)

    @property
    def bbox(self) -> tuple[np.ndarray, np.ndarray]:
        return self.points.min(axis=0), self.points.max(axis=0)

    @property
    def envelope(self) -> np.ndarray:
        lo, hi = self.bbox
        return hi - lo


class MissingRadius(ValueError):
    """Leve quand le rayon de cintrage n'est pas renseigne."""


def build(tube: TubeProgram, samples: int = 24, handedness: int = 1) -> Centerline:
    """Construit la fibre neutre. handedness = -1 inverse le sens des rotations."""
    if not tube.bends:
        # Tube droit. La longueur est la SOMME des droites : un tube sans coude
        # peut en porter plusieurs (premier segment + dernier deduit de R6).
        L = float(sum(tube.straights)) if tube.straights else 0.0
        if L <= 1e-9:
            raise ValueError(
                f"piece {tube.ref} : tube droit de longueur nulle. Renseigne "
                "LONGUEUR dans la LFT ou R6 dans le programme.")
        n = max(2, samples)
        pts = np.linspace([0.0, 0.0, 0.0], [L, 0.0, 0.0], n)
        return Centerline(pts, pts[[0, -1]], np.empty((0, 3)), L, pts[[0, -1]],
                          [Primitive("line", pts[0].copy(), pts[-1].copy())])

    missing = [i for i, b in enumerate(tube.bends) if b.clr is None]
    if missing:
        raise MissingRadius(
            f"piece {tube.ref} : rayon de cintrage absent pour le(s) coude(s) "
            f"{[i + 1 for i in missing]}. Renseigne l'outillage dans tooling.json."
        )

    p = np.zeros(3)
    t = np.array([1.0, 0.0, 0.0])
    u = np.array([0.0, 0.0, 1.0])

    pts: list[np.ndarray] = [p.copy()]
    vertices: list[np.ndarray] = [p.copy()]
    tangents: list[np.ndarray] = []
    prims: list[Primitive] = []
    developed = 0.0

    for i, bend in enumerate(tube.bends):
        straight = tube.straights[i]
        if straight > 1e-9:
            prims.append(Primitive("line", p.copy(), p + straight * t))
        p = p + straight * t
        developed += straight
        pts.append(p.copy())
        tangents.append(p.copy())          # point de tangence d'entree

        # rotation du plan de cintrage autour de l'axe du tube
        if bend.rotation:
            u = rodrigues(u, t, handedness * math.radians(bend.rotation))
            u -= np.dot(u, t) * t          # reorthogonalisation anti-derive
            u /= np.linalg.norm(u)

        axis = np.cross(t, u)
        axis /= np.linalg.norm(axis)

        clr = float(bend.clr)
        theta = math.radians(bend.angle)
        centre = p + clr * u

        # sommet theorique : intersection des deux droites adjacentes
        vertices.append(p + clr * math.tan(theta / 2.0) * t)

        radial = p - centre
        arc_start = p.copy()
        for k in range(1, samples + 1):
            a = theta * k / samples
            pts.append(centre + rodrigues(radial, axis, a))
        arc_mid = centre + rodrigues(radial, axis, theta / 2.0)

        p = pts[-1].copy()
        prims.append(Primitive("arc", arc_start, p.copy(), arc_mid,
                               centre.copy(), clr, bend.angle, axis.copy()))
        t = rodrigues(t, axis, theta)
        t /= np.linalg.norm(t)
        u = rodrigues(u, axis, theta)
        u -= np.dot(u, t) * t
        u /= np.linalg.norm(u)

        developed += clr * theta
        tangents.append(p.copy())          # point de tangence de sortie

    tail = tube.straights[-1]
    if tail > 1e-9:
        prims.append(Primitive("line", p.copy(), p + tail * t))
    p = p + tail * t
    developed += tail
    pts.append(p.copy())
    vertices.append(p.copy())

    arr = np.asarray(pts)
    return Centerline(
        points=arr,
        vertices=np.asarray(vertices),
        tangent_points=np.asarray(tangents),
        developed=developed,
        ends=arr[[0, -1]],
        primitives=prims,
    )


def developed_length(tube: TubeProgram) -> float:
    """Longueur developpee sans construire la geometrie complete."""
    total = sum(tube.straights)
    for b in tube.bends:
        if b.clr is None:
            raise MissingRadius(f"piece {tube.ref} : rayon absent")
        total += b.clr * math.radians(b.angle)
    return total


COLLISION_SAMPLE = 260


def min_segment_distance(cl: Centerline, diameter: float = 0.0,
                         clr: float = 0.0) -> float:
    """Plus petite distance entre deux portions ELOIGNEES le long du tube.

    On compare le long de l'abscisse curviligne, pas des indices : deux points
    voisins dans un meme coude sont proches par construction et ne constituent
    pas une collision. Le seuil de separation vaut plusieurs diametres ou un
    demi-tour de matrice, selon le plus grand.
    """
    p = cl.points
    if len(p) < 4:
        return float("inf")
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    # Matrice N x N : sur une piece a trente coudes elle pese un demi-million
    # de distances. On echantillonne — un rapprochement se voit aussi bien sur
    # un point sur trois, et le seuil d'alerte est en millimetres, pas en
    # micrometres.
    if len(p) > COLLISION_SAMPLE:
        idx = np.unique(np.linspace(0, len(p) - 1, COLLISION_SAMPLE).astype(int))
        p, s = p[idx], s[idx]
    gap = max(5.0 * diameter, 2.0 * clr, 15.0)
    d3 = np.linalg.norm(p[:, None, :] - p[None, :, :], axis=-1)
    far = np.abs(s[:, None] - s[None, :]) > gap
    return float(d3[far].min()) if far.any() else float("inf")
