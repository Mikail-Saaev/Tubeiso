"""Configuration et lecture des sources.

tooling.json est le seul endroit ou vivent les donnees que le programme
machine ne contient pas : rayon de fibre neutre, epaisseur, matiere,
contraintes machine. C'est le fichier a remplir quand tu auras la fiche
outillage.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bsa
from .model import Tooling

DEFAULT_CONFIG = {
    "convention": "feed_only",
    "handedness": 1,
    "length_tolerance": 0.5,
    "tooling": {
        "L54": {"diameter": 4.0, "clr": None, "wall": None, "material": None,
                "min_straight": None, "max_angle": 180.0, "elongation": 0.0},
        "L56": {"diameter": 6.0, "clr": None, "wall": None, "material": None,
                "min_straight": None, "max_angle": 180.0, "elongation": 0.0},
        "L58": {"diameter": 8.0, "clr": None, "wall": None, "material": None,
                "min_straight": None, "max_angle": 180.0, "elongation": 0.0},
    },
    "code_mat_to_tooling": {
        "293-421-006": "L56",
        "293-421-008": "L58",
        "416-421-004": "L54",
    },
}


class Config:
    def __init__(self, data: dict):
        self.data = data
        fields = {"diameter", "clr", "wall", "material", "elongation",
                  "min_straight", "max_angle"}
        self.tooling: dict[str, Tooling] = {
            name: Tooling(name=name, **{k: v for k, v in spec.items() if k in fields})
            for name, spec in data["tooling"].items()
        }

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            return cls(json.loads(json.dumps(DEFAULT_CONFIG)))
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def write_template(cls, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                     encoding="utf-8")
        return p

    @property
    def convention(self) -> str:
        return self.data.get("convention", "feed_only")

    @property
    def handedness(self) -> int:
        return int(self.data.get("handedness", 1))

    @property
    def tolerance(self) -> float:
        return float(self.data.get("length_tolerance", 0.5))

    def for_program(self, tooling_name: str, diameter: float | None,
                    code_mat: str | None = None) -> Tooling:
        """Resout l'outillage : nom du sous-programme, sinon CODE_MAT, sinon Ø."""
        if tooling_name in self.tooling:
            return self.tooling[tooling_name]
        mapped = self.data.get("code_mat_to_tooling", {}).get(code_mat or "")
        if mapped in self.tooling:
            return self.tooling[mapped]
        if diameter is not None:
            for t in self.tooling.values():
                if abs(t.diameter - diameter) < 1e-6:
                    return t
            d = int(diameter)
            if d in bsa.RM:                 # repli sur les tables BSA officielles
                return Tooling(
                    name=tooling_name or f"Ø{d}", diameter=float(d),
                    clr=bsa.bend_radius(d),
                    elongation=bsa.ELONGATION_PCT.get(d, 0.0),
                    min_straight=bsa.MIN_STRAIGHT.get(d),
                )
        return Tooling(name=tooling_name or "?", diameter=diameter or 0.0)


# --------------------------------------------------------------------------- LFT

def read_lft(path: str | Path, program_column: str = "PROGCRIPPA") -> list[dict]:
    """Lit un fichier LFT et retourne une ligne par piece.

    Le gabarit LFT est une liste de fils detournee pour des tubes : la plupart
    des colonnes sont vides. On ne garde que celles qui portent une information.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = [str(h) if h is not None else "" for h in next(rows)]
    idx = {h: i for i, h in enumerate(header)}
    if program_column not in idx:
        raise KeyError(f"colonne {program_column} absente. Presentes : {header[:12]}...")

    out = []
    for r in rows:
        if r[idx[program_column]] is None:
            continue
        out.append({
            "ref": str(r[idx.get("REP", 0)] or ""),
            "code_mat": r[idx["CODE_MAT"]] if "CODE_MAT" in idx else None,
            "length": r[idx["LONGUEUR"]] if "LONGUEUR" in idx else None,
            "liste": r[idx["LISTE"]] if "LISTE" in idx else None,
            "program_name": r[idx["PROGRAMME"]] if "PROGRAMME" in idx else None,
            "recut": (r[idx["RECOUPE_1"]] if "RECOUPE_1" in idx else None)
                     or (r[idx["RECOUPE_2"]] if "RECOUPE_2" in idx else None),
            "end_1": r[idx["EMBOUT_1"]] if "EMBOUT_1" in idx else None,
            "end_2": r[idx["EMBOUT_2"]] if "EMBOUT_2" in idx else None,
            "iso": r[idx[program_column]],
        })
    wb.close()
    return out
