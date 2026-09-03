"""Genere la reference de simulation a partir de la geometrie Python.

    python tests/simulation_ref.py

Ecrit tests/simulation_ref.json, consomme par test_simulation.mjs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tubeiso import conventions, geometry                       # noqa: E402
from tubeiso.config import Config, read_lft                     # noqa: E402
from tubeiso.parsers import crippa                              # noqa: E402


def main(source: str) -> int:
    cfg = Config.load(None)
    conv = conventions.get(cfg.convention)
    cases = []
    for row in read_lft(source):
        raw = crippa.parse(str(row["iso"]), ref=row["ref"])
        raw.name = raw.name or row["ref"]
        if raw.declared_length is None and row["length"]:
            raw.declared_length = float(row["length"])
        if not raw.complete:
            continue
        tooling = cfg.for_program(raw.tooling, raw.diameter, row["code_mat"])
        tube = conv.build(raw, tooling,
                          recut=float(row.get("recut") or raw.recut or 0.0))
        cl = geometry.build(tube, handedness=cfg.handedness)
        cases.append({
            "ref": tube.ref,
            "developed": round(cl.developed, 6),
            "end_point": [round(float(v), 6) for v in cl.points[-1]],
            "simulation": {
                "straights": [round(v, 6) for v in tube.straights],
                "bends": [{"angle": b.angle, "rotation": b.rotation,
                           "clr": b.clr} for b in tube.bends],
                "handedness": cfg.handedness,
            },
        })
    out = Path(__file__).with_name("simulation_ref.json")
    out.write_text(json.dumps({"cases": cases}, indent=1), encoding="utf-8")
    print(f"{len(cases)} cas ecrits dans {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "LFT.xlsx"))
