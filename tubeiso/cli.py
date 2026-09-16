"""Ligne de commande.

    tubeiso init                            cree tooling.json
    tubeiso inspect LFT.xlsx                lit et diagnostique sans rien ecrire
    tubeiso calibrate LFT.xlsx              verifie le rayon Rm sur un corpus
    tubeiso plan  LFT.xlsx -o plans/        plans PDF autoportants
    tubeiso model LFT.xlsx -o modeles_3d/   solides STEP
    tubeiso batch DOSSIER -o bibliotheque/  campagne complete, des milliers de LFT

`batch` est la commande du passage a l'echelle : elle range les plans, les
modeles et les donnees en Groupe / Machine / LFT, et ecrit un INDEX.xlsx.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import (batch, bsa, calibrate, conventions, geometry, lft, materials,
               registry, render, scope, solid, validate)
from .config import Config, code_mat_diameter
from .parsers import crippa
from .validate import ERROR


def _load(path: str, column: str = "PROGCRIPPA"):
    """Retourne (record LFT, programme brut ou None) pour chaque tube."""
    book = lft.read(path)
    out = []
    for lot in book.lots:
        for rec in lot.tubes:
            raw = None
            if scope.has_program(rec.iso):
                raw = crippa.parse(rec.iso, ref=rec.rep)
                raw.name = raw.name or rec.rep or rec.program_number
                length = rec.number("LONGUEUR")
                if raw.declared_length is None and length:
                    raw.declared_length = float(length)
                if raw.diameter is None:
                    d = materials.diameter(rec.get("CODE_MAT")) or \
                        code_mat_diameter(rec.get("CODE_MAT"))
                    if d:
                        raw.diameter = float(d)
            out.append((rec, raw))
    return book, out


def _make(cfg, rec, raw, verdict=None):
    """Construit la piece retenue et ses controles, cintree ou droite."""
    conv = conventions.get(cfg.convention)
    if verdict is not None and verdict.straight:
        tooling = cfg.for_program("", verdict.diameter, rec.get("CODE_MAT"))
        length = float(rec.number("LONGUEUR") or 0.0)
        tube = conv.build_straight(rec.rep or rec.program_number or rec.key,
                                   length, verdict.diameter or tooling.diameter,
                                   tooling)
        tube.list_number = rec.list_number
        tube.program_number = rec.program_number
        tube.warnings.extend(rec.warnings)
        return tube, tooling, 0.0, validate.check(
            tube, tooling, length_tol=cfg.tolerance, lft_length=length)

    diameter = raw.diameter or materials.diameter(rec.get("CODE_MAT"))
    tooling = cfg.for_program(raw.tooling, diameter, rec.get("CODE_MAT"))
    recut = rec.recut or float(raw.recut or 0.0)
    tube = conv.build(raw, tooling, recut=recut, angle_mode=cfg.angle_mode)
    tube.ref = rec.rep or rec.program_number
    tube.list_number = rec.list_number
    tube.program_number = rec.program_number
    tube.warnings.extend(rec.warnings)
    issues = validate.check(tube, tooling, recut=recut,
                            length_tol=cfg.tolerance,
                            lft_length=rec.number("LONGUEUR"))
    return tube, tooling, recut, issues


def _plan_data(cfg, rec, tube, tooling, cl, issues, source, entry=None, raw=None):
    entry = entry or registry.parse_filename(Path(source).stem)
    return render.PlanData(
        tube=tube, centerline=cl, tooling=tooling, issues=issues,
        material=materials.lookup(rec.get("CODE_MAT")),
        groupe=entry.groupe, machine=entry.machine, designation=entry.description,
        lft=Path(source).stem, source_file=str(source),
        embout_1=str(rec.get("EMBOUT_1") or ""),
        embout_2=str(rec.get("EMBOUT_2") or ""),
        recoupe_1=float(rec.number("RECOUPE_1") or 0.0),
        recoupe_2=float(rec.number("RECOUPE_2") or 0.0),
        recoupe_programme=float((raw.recut if raw else 0) or 0.0),
        lft_length=rec.number("LONGUEUR"),
        quantite=rec.number("QTE_DEB"),
        remarque=str(rec.get("REMARQUE") or ""),
        handedness=cfg.handedness, angle_mode=cfg.angle_mode,
    )


# ------------------------------------------------------------------- commandes

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
    print(f"Lots     : {len(book.lots)}   Pieces : {len(data)}\n")

    counts = {validate.INFO: 0, validate.WARN: 0, validate.ERROR: 0}
    motifs: dict[str, int] = {}
    pairs = {id(rec): raw for rec, raw in data}
    for lot in book.lots:
        print(f"LOT {lot.label}   -   {len(lot.tubes)} piece(s)")
        print(f"  {'REP':>7} {'PROGRAMME':>18} {'NATURE':>7} {'D':>4} {'R6':>7} "
              f"{'C':>3}  ETAT     DETAIL")
        print("  " + "-" * 94)
        for rec in lot.tubes:
            raw = pairs.get(id(rec))
            verdict = scope.evaluate(rec, raw)
            ref = rec.rep or rec.program_number or rec.key
            if not verdict.ok:
                motifs[verdict.reason] = motifs.get(verdict.reason, 0) + 1
                print(f"  {ref:>7} {rec.program_number:>18} {verdict.kind:>7} "
                      f"{str(verdict.diameter or '-'):>4} {'-':>7} {'-':>3}  "
                      f"exclue   {verdict.reason} — {verdict.detail[:44]}")
                continue
            tube, tooling, _recut, issues = _make(cfg, rec, raw, verdict)
            worst = validate.worst(issues)
            counts[worst] += 1
            detail = next((i.message for i in issues if i.level == worst), "conforme")
            mark = "droit" if verdict.straight else ""
            print(f"  {ref:>7} {rec.program_number:>18} {verdict.kind:>7} "
                  f"{tube.diameter:>4.0f} {tube.declared_length or 0:>7.0f} "
                  f"{tube.n_bends:>3}  {worst:8s} {mark} {detail[:38]}")
        print()

    total_excl = sum(motifs.values())
    print(f"{counts[validate.INFO]} conforme(s), {counts[validate.WARN]} alerte(s), "
          f"{counts[validate.ERROR]} erreur(s) sur {len(data) - total_excl} piece(s) traitees.")
    if motifs:
        print(f"\n{total_excl} piece(s) hors perimetre :")
        for motif, n in sorted(motifs.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {motif:26s} {scope.LIBELLES.get(motif, '')}")
    if book.warnings:
        print("\nRemarques de lecture :")
        for w in book.warnings[:20]:
            print(f"  - {w}")
    return 0


def cmd_calibrate(args) -> int:
    cfg = Config.load(args.config)
    _book, data = _load(args.source, args.column)
    raws = [raw for _, raw in data if raw is not None]
    usable = [r for r in raws if r.complete and r.declared_length]
    print(f"{len(usable)} programme(s) complet(s) sur {len(raws)} exploitables.\n")
    if not usable:
        print("Aucun programme complet : impossible de calibrer.")
        return 1
    fits = calibrate.fit(calibrate.samples_from(usable))
    print("Rayon Rm ajuste sur le corpus, compare a la table BSA [DOC 1.2]\n")
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
    entry = registry.Registry.load(args.repertoire).resolve(args.source)

    made, skipped = 0, 0
    sheets: list[render.PlanData] = []
    excluded: list[dict] = []
    for rec, raw in data:
        ref = rec.rep or rec.program_number or rec.key
        verdict = scope.evaluate(rec, raw)
        if not verdict.ok:
            excluded.append({"repere": ref, "motif": verdict.reason,
                             "detail": verdict.detail})
            print(f"  {ref:>7}  hors perimetre : {verdict.reason}")
            skipped += 1
            continue

        tube, tooling, recut, issues = _make(cfg, rec, raw, verdict)
        blocking = [i for i in issues if i.level == ERROR]
        if blocking and not args.force:
            print(f"  {ref:>7}  IGNOREE")
            for i in blocking:
                print(f"           {i}")
            excluded.append({"repere": ref, "motif": "controle_bloquant",
                             "detail": blocking[0].message})
            skipped += 1
            continue

        try:
            cl = geometry.build(tube, handedness=cfg.handedness)
        except (geometry.MissingRadius, ValueError) as exc:
            print(f"  {ref:>7}  IGNOREE  {exc}")
            skipped += 1
            continue

        issues = validate.check(tube, tooling, cl, recut=recut,
                                length_tol=cfg.tolerance,
                                lft_length=rec.number("LONGUEUR"))
        pdata = _plan_data(cfg, rec, tube, tooling, cl, issues, args.source, entry, raw)
        base = registry.output_basename(args.source, tube.ref or ref)
        if args.svg:
            (out / f"{base}.svg").write_text(
                render.to_svg(pdata, args.azimuth), encoding="utf-8")
        pdf = render.to_pdf(pdata, out / f"{base}.pdf", args.azimuth)
        if args.dxf:
            render.to_dxf(tube, cl, str(out / f"{base}.dxf"))
        sheets.append(pdata)
        print(f"  {ref:>7}  {pdf.name:28s} {validate.worst(issues)}")
        made += 1

    if sheets and not args.no_booklet:
        label = Path(args.source).stem
        cahier = render.booklet(sheets, out / f"{registry.safe_name(label)}_cahier.pdf",
                                lot_label=label, excluded=excluded)
        print(f"\nCahier du lot : {cahier.name}")

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
        ref = rec.rep or rec.program_number or rec.key
        verdict = scope.evaluate(rec, raw)
        if not verdict.ok:
            print(f"  {ref:>7}  hors perimetre : {verdict.reason}")
            skipped += 1
            continue

        tube, tooling, recut, issues = _make(cfg, rec, raw, verdict)
        blocking = [i for i in issues if i.level == ERROR]
        if blocking and not args.force:
            print(f"  {ref:>7}  IGNOREE")
            for i in blocking:
                print(f"           {i}")
            skipped += 1
            continue
        try:
            cl = geometry.build(tube, handedness=cfg.handedness)
            files = solid.export(tube, cl, out, tooling, formats,
                                 basename=registry.output_basename(
                                     args.source, tube.ref or ref))
            rep = solid.report(tube, cl, tooling)
        except (geometry.MissingRadius, solid.SolidError) as exc:
            print(f"  {ref:>7}  IGNOREE  {exc}")
            skipped += 1
            continue

        env = " x ".join(f"{v:.0f}" for v in rep["encombrement_mm"])
        seal = "etanche" if rep["etanche"] else "SOLIDE INVALIDE"
        print(f"  {ref:>7}  {', '.join(f.name for f in files):28s} "
              f"Ø{rep['diametre']:g}  {rep['coudes']} coudes  "
              f"dev {rep['developpe_mm']:.1f}  {env} mm  {seal}")
        made += 1

    print(f"\n{made} modele(s) generes, {skipped} ignoree(s). Dossier : {out}")
    return 0 if made else 1


def cmd_batch(args) -> int:
    """Campagne : des milliers de LFT vers une bibliotheque rangee."""
    options = batch.Options(
        config=args.config, repertoire=args.repertoire,
        formats=tuple(f for f in ("step", "stl", "brep") if getattr(args, f)) or ("step",),
        plans=not args.no_plans, booklet=not args.no_booklet,
        models=not args.no_3d, dxf=args.dxf, force=args.force,
        azimuth=args.azimuth, limit=args.limit, workers=args.workers,
    )
    files = batch.discover(args.sources)
    if not files:
        print("Aucun fichier .xlsx / .xlsm trouve dans les sources indiquees.")
        return 1
    print(f"{len(files)} fichier(s) LFT a traiter. Sortie : {args.output}\n")

    def progress(i, total, res):
        state = ("saute" if res.skipped else
                 f"ERREUR {res.incident}" if res.incident else
                 f"{res.treated} traitee(s), {res.excluded} exclue(s)")
        print(f"  [{i:>5}/{total}] {Path(res.source).name[:58]:58s} {state}")

    campaign = batch.run(args.sources, args.output, options, on_file=progress)
    s = campaign.summary()
    print("\n" + "=" * 72)
    print(f"  fichiers LFT      : {s['fichiers']}  "
          f"(sautes {s['fichiers_sautes']}, en erreur {s['fichiers_en_erreur']})")
    print(f"  pieces            : {s['pieces']}")
    print(f"  cintrees          : {s['traitees']}")
    print(f"  tubes droits      : {s['tubes_droits']}")
    print(f"  hors perimetre    : {s['exclues']}")
    print(f"  plans PDF         : {s['plans']}")
    print(f"  modeles 3D        : {s['modeles_3d']}")
    if s["motifs"]:
        print("\n  motifs d'exclusion :")
        for motif, n in s["motifs"].items():
            print(f"    {n:6d}  {motif}")
    print(f"\n  index    : {Path(args.output) / 'INDEX.xlsx'}")
    print(f"  rapport  : {Path(args.output) / 'rapport.csv'}")
    print(f"  journal  : {Path(args.output) / 'journal.txt'}")
    return 0 if (s["traitees"] + s["tubes_droits"]) else 1


# ----------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tubeiso", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("source", help="fichier LFT .xlsx")
        sp.add_argument("-c", "--config", help="tooling.json")
        sp.add_argument("--column", default="PROGCRIPPA")

    s = sub.add_parser("init")
    s.add_argument("-o", "--output", default="tooling.json")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("inspect", help="diagnostic, n'ecrit rien")
    common(s)
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("calibrate", help="verifie Rm sur un corpus")
    common(s)
    s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("model", help="solides 3D pour la sous-traitance")
    common(s)
    s.add_argument("-o", "--output", default="modeles_3d")
    s.add_argument("--step", action="store_true", default=True)
    s.add_argument("--stl", action="store_true", help="maillage, pour visualisation")
    s.add_argument("--brep", action="store_true", help="format natif OpenCascade")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_model)

    s = sub.add_parser("plan", help="plans PDF autoportants")
    common(s)
    s.add_argument("-o", "--output", default="plans")
    s.add_argument("--repertoire", help="Repertoire_Machines_Consolide.xlsm")
    s.add_argument("--azimuth", type=float, default=None)
    s.add_argument("--dxf", action="store_true")
    s.add_argument("--svg", action="store_true", help="ecrire aussi un apercu SVG")
    s.add_argument("--no-booklet", action="store_true",
                   help="ne pas assembler le cahier du lot")
    s.add_argument("--force", action="store_true",
                   help="tracer meme si des controles bloquants sont detectes")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("batch", help="campagne sur des milliers de LFT")
    s.add_argument("sources", nargs="+",
                   help="dossiers ou fichiers LFT (.xlsx / .xlsm), parcourus recursivement")
    s.add_argument("-o", "--output", default="bibliotheque_tubes")
    s.add_argument("-c", "--config", help="tooling.json")
    s.add_argument("-r", "--repertoire",
                   help="Repertoire_Machines_Consolide.xlsm, pour le rattachement "
                        "Groupe / Machine")
    s.add_argument("--step", action="store_true", default=True)
    s.add_argument("--stl", action="store_true")
    s.add_argument("--brep", action="store_true")
    s.add_argument("--dxf", action="store_true")
    s.add_argument("--no-plans", action="store_true")
    s.add_argument("--no-3d", action="store_true",
                   help="sauter les modeles STEP (beaucoup plus rapide)")
    s.add_argument("--no-booklet", action="store_true")
    s.add_argument("--azimuth", type=float, default=None)
    s.add_argument("--limit", type=int, default=None,
                   help="s'arreter apres N fichiers, pour un essai")
    s.add_argument("--workers", type=int, default=1,
                   help="processus paralleles ; 4 a 8 sur une machine de bureau")
    s.add_argument("--force", action="store_true",
                   help="retraiter les LFT deja faites")
    s.set_defaults(func=cmd_batch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
