"""Configuration et resolution de l'outillage.

tooling.json est le seul endroit ou vivent les donnees que le programme
machine ne contient pas : rayon de fibre neutre, epaisseur, matiere,
contraintes machine. Toutes les valeurs par defaut viennent des tables de
`bsa.py`, elles-memes sourcees dans la documentation de formation.

La lecture du fichier LFT a demenage dans `lft.py`, qui ne fait plus aucune
hypothese sur la mise en page du classeur.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bsa, lft
from .model import Tooling

# [DOC p.4] Code matiere BSA -> diametre exterieur. La LFT ecrit des tirets,
# la documentation des espaces : on normalise a la lecture.
CODE_MAT_DIAMETER = {
    "416421004": 4, "293421006": 6, "293421008": 8, "293421010": 10,
    "293422012": 12, "293421015": 15, "293421016": 16, "293421018": 18,
    "293421022": 22, "2934212022": 22,
}


def code_mat_diameter(code: object) -> int | None:
    """'293-421-006' ou '293 421 006' -> 6."""
    digits = "".join(c for c in str(code or "") if c.isdigit())
    return CODE_MAT_DIAMETER.get(digits)


def _default_tooling() -> dict:
    """Table outillage par defaut, integralement issue des tables BSA."""
    return {
        f"Ø{d}": {
            "diameter": float(d),
            "clr": float(bsa.RM[d]),
            "wall": bsa.WALL.get(d),
            "material": (f"Tube Ermeto zingue {d}/{bsa.BORE[d]:g}"
                         if d in bsa.BORE else None),
            "elongation": float(bsa.ELONGATION_PCT.get(d, 0.0)),
            "min_straight": bsa.MIN_STRAIGHT.get(d),
            "max_angle": bsa.MAX_BEND_ANGLE,
        }
        for d in sorted(bsa.RM)
    }


DEFAULT_CONFIG = {
    "convention": "bsa",
    "angle_mode": bsa.DEFAULT_ANGLE_MODE,
    "handedness": 1,
    "length_tolerance": 1.0,
    "tooling": _default_tooling(),
    "code_mat_to_tooling": {
        code: f"Ø{d}" for code, d in (
            ("416-421-004", 4), ("293-421-006", 6), ("293-421-008", 8),
            ("293-421-010", 10), ("293-422-012", 12), ("293-421-015", 15),
            ("293-421-016", 16), ("293-421-018", 18),
        )
    },
}


class Config:
    def __init__(self, data: dict):
        self.data = data
        fields = {"diameter", "clr", "wall", "material", "elongation",
                  "min_straight", "max_angle"}
        self.tooling: dict[str, Tooling] = {
            name: Tooling(name=name, **{k: v for k, v in spec.items() if k in fields})
            for name, spec in data.get("tooling", {}).items()
        }

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            return cls(json.loads(json.dumps(DEFAULT_CONFIG)))
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # une configuration ecrite pour une version anterieure reste valable :
        # on complete ce qui manque au lieu de refuser le fichier
        for key, value in DEFAULT_CONFIG.items():
            data.setdefault(key, json.loads(json.dumps(value)))
        return cls(data)

    @classmethod
    def write_template(cls, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                     encoding="utf-8")
        return p

    @property
    def convention(self) -> str:
        return self.data.get("convention") or "bsa"

    @property
    def angle_mode(self) -> str:
        mode = self.data.get("angle_mode") or bsa.DEFAULT_ANGLE_MODE
        return mode if mode in bsa.ANGLE_MODES else bsa.DEFAULT_ANGLE_MODE

    @property
    def handedness(self) -> int:
        return 1 if int(self.data.get("handedness", 1)) >= 0 else -1

    @property
    def tolerance(self) -> float:
        return float(self.data.get("length_tolerance", 1.0))

    def for_program(self, tooling_name: str, diameter: float | None,
                    code_mat: str | None = None) -> Tooling:
        """Resout l'outillage : nom du sous-programme, sinon Ø, sinon CODE_MAT."""
        if tooling_name in self.tooling:
            return self.tooling[tooling_name]
        if diameter is None:
            diameter = code_mat_diameter(code_mat)
        mapped = self.data.get("code_mat_to_tooling", {}).get(
            str(code_mat or "").strip())
        if diameter is None and mapped in self.tooling:
            return self.tooling[mapped]
        if diameter is not None:
            for t in self.tooling.values():
                if abs(t.diameter - diameter) < 1e-6:
                    return t
            d = int(diameter)
            if d in bsa.RM:                 # repli sur les tables BSA officielles
                return Tooling(
                    name=tooling_name or f"Ø{d}", diameter=float(d),
                    clr=bsa.bend_radius(d), wall=bsa.WALL.get(d),
                    elongation=bsa.ELONGATION_PCT.get(d, 0.0),
                    min_straight=bsa.MIN_STRAIGHT.get(d),
                    max_angle=bsa.MAX_BEND_ANGLE,
                )
        if mapped in self.tooling:
            return self.tooling[mapped]
        return Tooling(name=tooling_name or "?", diameter=diameter or 0.0)


# --------------------------------------------------------------------------- LFT

def read_lft(path: str | Path, program_column: str = "PROGCRIPPA") -> list[dict]:
    """Compatibilite : une ligne plate par tube, comme dans les versions <= v4.

    Le code nouveau doit utiliser `lft.read()`, qui conserve toutes les
    colonnes, regroupe les lignes d'un meme tube et rend les lots.
    """
    book = lft.read(path)
    out = []
    for lot in book.lots:
        for t in lot.tubes:
            out.append({
                "ref": t.rep or t.program_number or t.key,
                "lot": lot.number,
                "code_mat": t.get("CODE_MAT"),
                "length": t.number("LONGUEUR"),
                "liste": t.list_number,
                "program_name": t.program_number,
                "recut": t.recut,
                "end_1": t.get("EMBOUT_1"),
                "end_2": t.get("EMBOUT_2"),
                "straight": t.straight,
                "iso": t.iso,
                "record": t,
            })
    return out
