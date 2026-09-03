"""Conversion programme ISO -> geometrie, convention BSA confirmee.

La question ouverte des versions precedentes est resolue. Sources : la
documentation de formation Crippa et les formules de `archivage_crippa.xlsm`.

Trois regles, toutes verifiees numeriquement :

1. Un segment droit vaut la SOMME SIGNEE des deplacements Y de son bloc.
   Le programmeur ecrit `Y-35` puis `Y-(L-35)` des que L > 35, exactement la
   formule `=IF(Y>35, Y-35, "")` de la feuille Excel. Verifie sur 412
   (35+107=142), 409 (35+148=183), 407 (35+47=82) et 410 (35+28.5=63.5).
   La somme est signee pour absorber l'astuce anti-collision du chapitre 5.8,
   ou le programme avance de 30, tourne de 180 puis revient de 30.

2. Le premier segment est R12. Le dernier n'est PAS ecrit : il se deduit de
   R6 via la formule d'allongement. DS n'est qu'un commentaire d'aide, et
   l'expert BSA a explicitement dit de ne pas s'en servir comme reference.

3. Les Y sont des longueurs tangente-a-tangente, arrondies a 0.5 mm.
   Confirme par la mesure Catia du tube 410 : 33.861 -> 34, 63.591 -> 63.5.
"""
from __future__ import annotations

from . import bsa
from .model import Bend, Tooling, TubeProgram
from .parsers.crippa import RawProgram


class BSAConvention:
    name = "bsa"
    description = "somme signee des Y, dernier segment deduit de R6 [DOC+XLSM]"

    def build(self, raw: RawProgram, tooling: Tooling,
              recut: float = 0.0, use_true_angles: bool = False) -> TubeProgram:
        warnings = list(raw.warnings)
        diameter = raw.diameter or tooling.diameter
        r15 = list(raw.angles)
        angles = ([bsa.true_angle(a, diameter) for a in r15]
                  if use_true_angles else list(r15))

        clr = tooling.clr
        if clr is None and int(diameter) in bsa.RM:
            clr = bsa.bend_radius(diameter)

        head = raw.init.get("R12")
        if head is None:
            warnings.append("R12 absent : premier segment inconnu")
            head = 0.0
        mid = [b.segment() for b in raw.blocks[:-1]] if raw.blocks else []
        straights = [head, *mid]

        last = None
        if raw.declared_length is not None and clr is not None and raw.complete:
            try:
                last = bsa.last_straight(raw.declared_length, straights,
                                         r15, diameter, recut)
            except KeyError as exc:
                warnings.append(str(exc))
        if last is None:
            last = raw.ds if raw.ds is not None else 0.0
            if raw.complete:
                warnings.append("dernier segment repris de DS, valeur non fiable")
        elif last < 0:
            warnings.append(
                f"dernier segment negatif ({last:.1f} mm) : programme incomplet "
                "ou R6 faux")
        elif raw.ds is not None and abs(last - raw.ds) > 1.0:
            warnings.append(
                f"dernier segment calcule {last:.1f} mm contre DS={raw.ds:g} "
                f"(ecart {last - raw.ds:+.1f} mm)")
        straights.append(last)

        sign = bsa.rotation_sign(raw.head) if raw.head else 1
        bends = []
        for i, a in enumerate(angles):
            rot = raw.blocks[i - 1].rotation() if i > 0 else 0.0
            bends.append(Bend(angle=a, rotation=sign * rot, clr=clr))

        return TubeProgram(
            ref=raw.name, program=raw.name, diameter=diameter,
            tooling=raw.tooling or tooling.name,
            declared_length=raw.declared_length, comment=raw.comment,
            straights=straights, bends=bends, params=dict(raw.init),
            source=raw.source, complete=raw.complete, warnings=warnings,
        )


REGISTRY = {BSAConvention.name: BSAConvention()}
DEFAULT = BSAConvention.name


def get(name: str = DEFAULT):
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"convention '{name}' inconnue. Disponibles : "
                       f"{sorted(REGISTRY)}") from None
