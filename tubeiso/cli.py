"""Ligne de commande.

    tubeiso init                       cree tooling.json a remplir
    tubeiso inspect LFT.xlsx           lit et diagnostique sans rien tracer
    tubeiso calibrate LFT.xlsx         cherche rayon + convention
    tubeiso plan LFT.xlsx -o plans/    genere les plans
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bsa, calibrate, conventions, geometry, render, solid, validate
from .config import Config, read_lft
from .parsers import crippa
from .validate import ERROR


def _load(path: str, column: str):
    rows = read_lft(path, column)
    raws = []
    for row in rows:
        raw = crippa.parse(str(row["iso"]), ref=row["ref"])
        raw.name = raw.name or row["ref"]
        if raw.declared_length is None and row["length"]:
            raw.declared_length = float(row["length"])
        raws.append((row, raw))
    return raws


def cmd_init(args) -> int:
    p = Config.write_template(args.output)
    print(f"Modele ecrit : {p}")
    print("Renseigne 'clr' pour chaque outillage, puis relance calibrate ou plan.")
    return 0


def cmd_inspect(args) -> int:
    cfg = Config.load(args.config)
    data = _load(args.source, args.column)
    print(f"{len(data)} pieces\n")
    print(f"{'REP':>5} {'OUT':>5} {'Ø':>4} {'DECL':>7} {'COUDES':>7} {'M30':>4}  ETAT")
    print("-" * 78)
    bad = 0
    for row, raw in data:
        t = cfg.for_program(raw.tooling, raw.diameter, row["code_mat"])
        flag = "oui" if raw.complete else "NON"
        note = "; ".join(raw.warnings) or "ok"
        if not raw.complete or raw.warnings:
            bad += 1
        print(f"{raw.name:>5} {t.name:>5} {raw.diameter or 0:>4g} "
              f"{raw.declared_length or 0:>7g} {len(raw.angles):>7} {flag:>4}  {note[:38]}")
    print(f"\n{len(data) - bad} piece(s) exploitable(s), {bad} a corriger.")
    return 0


def cmd_calibrate(args) -> int:
    cfg = Config.load(args.config)
    raws = [raw for _, raw in _load(args.source, args.column)]
    usable = [r for r in raws if r.complete and r.declared_length]
    print(f"{len(usable)} programme(s) complet(s) sur {len(raws)} exploitables.\n")
    if not usable:
        print("Aucun programme complet : impossible de calibrer.")
        return 1
    fits = calibrate.fit(calibrate.samples_from(usable))
    print("Rayon Rm ajuste sur le corpus, compare a la table BSA [DOC p.4]\n")
    for f in fits:
        print("  " + str(f))
    print("\nUn ECART signale un outillage different, un R6 errone, ou une")
    print("famille de tubes a traiter a part.")
    return 0


def cmd_plan(args) -> int:
    cfg = Config.load(args.config)
    conv = conventions.get(args.convention or cfg.convention)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    data = _load(args.source, args.column)

    made, skipped = 0, 0
    for row, raw in data:
        tooling = cfg.for_program(raw.tooling, raw.diameter, row["code_mat"])
        # la recoupe vient de la LFT, sinon du commentaire du programme
        recut = float(row.get('recut') or raw.recut or 0.0)
        tube = conv.build(raw, tooling, recut=recut)
        issues = validate.check(tube, tooling, recut=recut,
                                length_tol=cfg.tolerance)

        blocking = [i for i in issues if i.level == ERROR]
        if blocking and not args.force:
            print(f"  {tube.ref:>5}  IGNOREE")
            for i in blocking:
                print(f"           {i}")
            skipped += 1
            continue

        try:
            cl = geometry.build(tube, handedness=cfg.handedness)
        except geometry.MissingRadius as exc:
            print(f"  {tube.ref:>5}  IGNOREE  {exc}")
            skipped += 1
            continue

        issues = validate.check(tube, tooling, cl, recut=recut,
                                length_tol=cfg.tolerance)
        svg = out / f"{tube.ref or raw.name}.svg"
        svg.write_text(render.to_svg(tube, cl, tooling, issues, args.azimuth),
                       encoding="utf-8")
        if args.dxf:
            render.to_dxf(tube, cl, str(out / f"{tube.ref or raw.name}.dxf"))
        print(f"  {tube.ref:>5}  {svg.name}   {validate.worst(issues)}")
        made += 1

    print(f"\n{made} plan(s) generes, {skipped} ignoree(s). Dossier : {out}")
    return 0 if made else 1


def cmd_model(args) -> int:
    """Genere les modeles solides 3D. C'est le livrable pour la sous-traitance."""
    cfg = Config.load(args.config)
    conv = conventions.get(cfg.convention)
    out = Path(args.output)
    formats = [f for f in ("step", "stl", "brep") if getattr(args, f)] or ["step"]
    data = _load(args.source, args.column)

    made, skipped = 0, 0
    for row, raw in data:
        tooling = cfg.for_program(raw.tooling, raw.diameter, row["code_mat"])
        recut = float(row.get("recut") or raw.recut or 0.0)
        tube = conv.build(raw, tooling, recut=recut)
        issues = validate.check(tube, tooling, recut=recut, length_tol=cfg.tolerance)

        blocking = [i for i in issues if i.level == ERROR]
        if blocking and not args.force:
            print(f"  {tube.ref:>5}  IGNOREE")
            for i in blocking:
                print(f"           {i}")
            skipped += 1
            continue
        try:
            cl = geometry.build(tube, handedness=cfg.handedness)
            files = solid.export(tube, cl, out, tooling, formats)
            rep = solid.report(tube, cl, tooling)
        except (geometry.MissingRadius, solid.SolidError) as exc:
            print(f"  {tube.ref:>5}  IGNOREE  {exc}")
            skipped += 1
            continue

        env = " x ".join(f"{v:.0f}" for v in rep["encombrement_mm"])
        seal = "etanche" if rep["etanche"] else "SOLIDE INVALIDE"
        print(f"  {tube.ref:>5}  {', '.join(f.name for f in files):28s} "
              f"Ø{rep['diametre']:g}  {rep['coudes']} coudes  "
              f"dev {rep['developpe_mm']:.1f}  {env} mm  {seal}")
        made += 1

    print(f"\n{made} modele(s) generes, {skipped} ignoree(s). Dossier : {out}")
    return 0 if made else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tubeiso", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("source", help="fichier LFT .xlsx")
        sp.add_argument("-c", "--config", help="tooling.json")
        sp.add_argument("--column", default="PROGCRIPPA")

    s = sub.add_parser("init"); s.add_argument("-o", "--output", default="tooling.json")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("inspect"); common(s); s.set_defaults(func=cmd_inspect)
    s = sub.add_parser("calibrate"); common(s); s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("model", help="solides 3D pour la sous-traitance")
    common(s)
    s.add_argument("-o", "--output", default="modeles_3d")
    s.add_argument("--step", action="store_true", default=True)
    s.add_argument("--stl", action="store_true", help="maillage, pour visualisation")
    s.add_argument("--brep", action="store_true", help="format natif OpenCascade")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_model)

    s = sub.add_parser("plan", help="plans 2D de controle"); common(s)
    s.add_argument("-o", "--output", default="plans")
    s.add_argument("--convention")
    s.add_argument("--azimuth", type=float, default=None)
    s.add_argument("--dxf", action="store_true")
    s.add_argument("--force", action="store_true",
                   help="tracer meme si des erreurs bloquantes sont detectees")
    s.set_defaults(func=cmd_plan)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
