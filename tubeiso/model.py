"""Modele de donnees pivot.

Tout le reste de l'application parle ce langage : les parseurs le produisent,
la geometrie et le rendu le consomment. Changer de machine = ecrire un
nouveau parseur, rien d'autre.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tooling:
    """Outillage de cintrage. C'est ici que vit le rayon de fibre neutre."""

    name: str                    # ex. "L56"
    diameter: float              # diametre exterieur du tube, mm
    clr: float | None = None     # rayon de fibre neutre, mm. None = inconnu
    wall: float | None = None    # epaisseur de paroi, mm
    material: str | None = None
    elongation: float = 0.0      # allongement au cintrage, en % du developpe
    min_straight: float | None = None   # droite mini entre 2 coudes (contrainte machine)
    max_angle: float = 180.0

    @property
    def known(self) -> bool:
        return self.clr is not None


@dataclass
class Bend:
    """Un coude.

    `angle`      angle REEL du tube apres retour elastique. C'est lui qui fait
                 la geometrie, donc le modele 3D et le plan.
    `r15`        angle PROGRAMME, tel qu'il est ecrit dans le bloc L2/L3.
    `springback` supplement d'elasticite retire : r15 - angle. [DOC 5.4]
    `rotation`   rotation du plan de cintrage AVANT ce coude (axe B).
    """

    angle: float
    rotation: float = 0.0
    clr: float | None = None     # peut surcharger l'outillage (matrices multi-rayon)
    r15: float | None = None
    springback: float = 0.0

    def __post_init__(self) -> None:
        if self.r15 is None:
            self.r15 = self.angle


@dataclass
class TubeProgram:
    """Une piece. `straights` contient n+1 longueurs pour n coudes.

    ATTENTION : `straights` est en convention MACHINE telle que lue dans le
    programme. La conversion vers des longueurs tangente-a-tangente est faite
    par une LengthConvention (voir conventions.py), parce que cette conversion
    est la principale inconnue du projet.
    """

    ref: str
    program: str = ""              # nom du bloc %MPF
    program_number: str = ""       # colonne PROGRAMME : 792_JV-412
    list_number: str = ""          # colonne LISTE : 0792-0002-JV, le lot
    diameter: float = 0.0
    tooling: str = ""
    declared_length: float | None = None   # R6 : longueur de coupe du brut
    ds: float | None = None                # DS du commentaire : temoin independant
    comment: str = ""
    straights: list[float] = field(default_factory=list)
    bends: list[Bend] = field(default_factory=list)
    params: dict[str, float] = field(default_factory=dict)
    source: str = ""
    complete: bool = True
    straight: bool = False         # tube laisse droit, sans programme
    angle_mode: str = "entier"     # mode de correction d'elasticite applique
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.bends and len(self.straights) != len(self.bends) + 1:
            self.warnings.append(
                f"incoherence : {len(self.bends)} coudes pour "
                f"{len(self.straights)} droites (attendu {len(self.bends) + 1})"
            )

    @property
    def n_bends(self) -> int:
        return len(self.bends)

    @property
    def label(self) -> str:
        """Ce qu'on affiche : le repere, qualifie par son lot s'il en a un."""
        return f"{self.ref} · {self.list_number}" if self.list_number else self.ref

    def lra_rows(self) -> list[tuple[float, float, float]]:
        """Table LRA : une ligne par coude (longueur amont, rotation, angle)."""
        return [
            (self.straights[i], b.rotation, b.angle)
            for i, b in enumerate(self.bends)
            if i < len(self.straights)
        ]
