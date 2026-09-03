"""Verification du rayon Rm sur un corpus de programmes.

Le rayon n'est plus une inconnue : la documentation BSA le donne (11 mm pour
Ø4 et Ø6, 14 pour Ø8, etc.). Ce module sert desormais a VERIFIER que la table
s'applique bien a l'ensemble du parc, ce qui compte quand on traite 10 000
programmes dont certains sont anciens.

Pour chaque diametre, on ajuste le Rm qui fait boucler R6 au mieux, et on le
compare a la valeur officielle. Un ecart signale un outillage different, un
programme errone, ou une famille de tubes a traiter a part.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import bsa
from .parsers.crippa import RawProgram


@dataclass
class Fit:
    diameter: int
    rm_officiel: float
    rm_ajuste: float
    rms: float
    worst: float
    n: int

    @property
    def coherent(self) -> bool:
        return abs(self.rm_ajuste - self.rm_officiel) < 0.3 and self.rms < 1.0

    def __str__(self) -> str:
        flag = "ok" if self.coherent else "ECART"
        return (f"Ø{self.diameter:<3d} n={self.n:<4d} officiel={self.rm_officiel:5.1f} "
                f"ajuste={self.rm_ajuste:6.2f}  rms={self.rms:5.2f}  "
                f"max={self.worst:5.2f}  {flag}")


def _residuals(group, rm: float) -> list[float]:
    out = []
    for straights, angles, dia, r6, recut in group:
        arcs = bsa.K_DEG * sum(angles) * rm
        theo = arcs + sum(straights) + recut
        out.append(theo - arcs * bsa.ELONGATION_PCT[dia] / 100.0 - r6)
    return out


def fit(samples, lo: float = 1.0, hi: float = 120.0, steps: int = 11900) -> list[Fit]:
    """samples : (straights_sans_dernier, angles, diametre, R6, recoupe, DS)."""
    groups: dict[int, list] = {}
    for straights, angles, dia, r6, recut, ds in samples:
        if ds is None or r6 is None or not angles:
            continue
        groups.setdefault(int(dia), []).append(
            (list(straights) + [ds], list(angles), int(dia), float(r6), float(recut)))

    fits = []
    for dia, group in sorted(groups.items()):
        best = None
        for k in range(steps + 1):
            rm = lo + (hi - lo) * k / steps
            res = _residuals(group, rm)
            rms = (sum(r * r for r in res) / len(res)) ** 0.5
            if best is None or rms < best[1]:
                best = (rm, rms, max(abs(r) for r in res))
        fits.append(Fit(dia, float(bsa.RM.get(dia, 0)), best[0], best[1],
                        best[2], len(group)))
    return fits


def samples_from(raws: list[RawProgram], recuts: dict[str, float] | None = None):
    recuts = recuts or {}
    out = []
    for raw in raws:
        if not raw.complete or raw.declared_length is None or raw.diameter is None:
            continue
        straights = [raw.init.get("R12", 0.0)] + [b.segment() for b in raw.blocks[:-1]]
        out.append((straights, raw.angles, raw.diameter, raw.declared_length,
                    recuts.get(raw.name, raw.recut or 0.0), raw.ds))
    return out
