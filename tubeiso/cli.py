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

from . import bsa, calibrate, conventions, geometry, lft, render, solid, validate
from .config import Config, code_mat_diameter
from .parsers import crippa
from .validate import ERROR


def _load(path: str, column: str = "PROGCRIPPA"):
    """Retourne (record LFT, programme brut) pour chaque tube, tous lots confondus."""
    book = lft.read(path)
    out = []
    for lot in book.lots:
        for rec in lot.tubes:
            raw = crippa.parse(rec.iso, ref=rec.rep)
            raw.name = raw.name or rec.rep or rec.program_number
            length = rec.number("LONGUEUR")
            if raw.declared_length is None and length:
                raw.declared_length = float(length)
            if raw.diameter is None:
                d = code_mat_diameter(rec.get("CODE_MAT"))
                if d:
                    raw.diameter = float(d)
            out.append((rec, raw))
    return book, out


def _make(cfg, rec, raw):
    """Construit la piece et ses controles, tube droit compris."""
    conv = conventions.get(cfg.convention)
    diameter = raw.diameter or code_mat_diameter(rec.get("CODE_MAT"))
    tooling = cfg.for_program(raw.tooling, diameter, rec.get("CODE_MAT"))
    recut = rec.recut or float(raw.recut or 0.0)
    length = rec.number("LONGUEUR")
    if rec.straight or not rec.iso.strip():
        tube = conv.build_straight(rec.rep, length or 0.0,
                                   diameter or tooling.diameter, tooling)
    else:
        tube = conv.build(raw, tooling, recut=recut, angle_mode=cfg.angle_mode)
    tube.ref = rec.rep or rec.program_number
    tube.list_number = rec.list_number
    tube.program_number = rec.program_number
    tube.warnings.extend(rec.warnings)
    issues = validate.check(tube, tooling, recut=recut,
                            length_tol=cfg.tolerance, lft_length=length)
    return tube, tooling, recut, issues


def cmd_init(args) -> int:
    p = Config.write_template(args.output)
    print(f"Modele ecrit : {p}")
    print("Renseigne 'clr' pour chaque outillage, puis relance calibrate ou plan.")
    return 0


def cmd_inspect(args) -> int:
    cfg = Config.load(args.config)
    book, data = _load(args.source, args.column)
    print(f"Fichier  : {book.path}")
    print(f"Feuilles : {', '.join(book.sheets_read)}")
    print(f"Colonnes : {len(book.columns)}")
    print(f"Lots     : {len(book.lots)}   Pieces : {len(data)}" + chr(10))

    counts = {validate.INFO: 0, validate.WARN: 0, validate.ERROR: 0}
    pairs = {id(rec): raw for rec, raw in data}
    for lot in book.lots:
        print(f"LOT {lot.label}   -   {len(lot.tubes)} piece(s)")
        print(f"  {'REP':>6} {'PROGRAMME':>18} {'OUT':>5} {'D':>4} {'R6':>7} "
              f"{'C':>3} {'LIG':>4}  ETAT     DETAIL")
        print("  " + "-" * 92)
        for rec in lot.tubes:
            raw = pairs[id(rec)]
            tube, tooling, recut, issues = _make(cfg, rec, raw)
            level = validate.worst(issues)
            counts[level] += 1
            note = "; ".join(i.message for i in issues if i.level != validate.INFO) or "conforme"
            print(f"  {tube.ref:>6} {rec.program_number:>18} {tooling.name:>5} "
                  f"{tube.diameter or 0:>4g} {tube.declared_length or 0:>7g} "
                  f"{tube.n_bends:>3} {len(rec.rows):>4}  {level:7s}  {note[:52]}")
        print()
    for w in book.warnings:
        print(f"! {w}")
    print(f"{counts[validate.INFO]} conforme(s), {counts[validate.WARN]} alerte(s), "
          f"{counts[validate.ERROR]} erreur(s).")
    return 0


def cmd_calibrate(args) -> int:
    cfg = Config.load(args.config)
    _book, data = _load(args.source, args.column)
    raws = [raw for _, raw in data]
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
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    _book, data = _load(args.source, args.column)

    made, skipped = 0, 0
    for rec, raw in data:
        tube, tooling, recut, issues = _make(cfg, rec, raw)

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
                                length_tol=cfg.tolerance,
                                lft_length=rec.number("LONGUEUR"))
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
    out = Path(args.output)
    formats = [f for f in ("step", "stl", "brep") if getattr(args, f)] or ["step"]
    _book, data = _load(args.source, args.column)

    made, skipped = 0, 0
    for rec, raw in data:
        tube, tooling, recut, issues = _make(cfg, rec, raw)

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
