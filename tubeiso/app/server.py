"""Serveur applicatif.

L'interface est une page web servie en local. Ce choix n'est pas un pis-aller :
il garantit que le rendu 3D fonctionne sur n'importe quelle machine (le moteur
WebGL du navigateur est deja installe partout), evite tout probleme de pilote
OpenGL ou de contexte graphique, et rend l'application identique sous Windows,
macOS et Linux.

Le serveur n'ecoute que sur 127.0.0.1 : rien n'est expose sur le reseau.

Deux notions distinctes, et jamais melangees dans l'API :

    lot / LISTE   le numero de LFT           0792-0002-JV
    programme     le numero de programme     792_JV-412

Une piece est identifiee par un `uid` stable, et non par son repere : le meme
repere peut exister dans deux lots differents du meme fichier.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .. import (bsa, conventions, geometry, lft, materials, registry, render,
                scope, solid, stepreader, validate)
from ..config import Config, code_mat_diameter
from ..parsers import crippa

STATIC = Path(__file__).with_name("static")
MESH_CACHE_MAX = 24          # pieces gardees en memoire, maillage compris


class Piece:
    """Une piece prete a servir : donnees LFT, programme, geometrie, controles."""

    def __init__(self, uid: str, record: lft.TubeRecord, lot: str):
        self.uid = uid
        self.record = record
        self.lot = lot
        self.raw: crippa.RawProgram | None = None
        self.tube = None
        self.tooling = None
        self.recut = 0.0
        self.issues: list = []
        self.verdict: scope.Verdict | None = None

    @property
    def in_scope(self) -> bool:
        return bool(self.verdict and self.verdict.ok)

    @property
    def ref(self) -> str:
        return self.record.rep or self.record.program_number or self.uid


class Session:
    """Etat courant : le fichier ouvert et les pieces qu'il contient."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config = Config.load(None)
        self.source: Path | None = None
        self.book: lft.Workbook | None = None
        self.pieces: dict[str, Piece] = {}
        self.workdir = tempfile.mkdtemp(prefix="tubeiso-")
        self._cache: dict[tuple, dict] = {}

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    # ------------------------------------------------------------- invalidation
    @property
    def config_key(self) -> str:
        blob = json.dumps(self.config.data, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------ chargement
    def open_lft(self, path: str, sheet: str | None = None) -> dict:
        book = lft.read(path, sheet)
        pieces: dict[str, Piece] = {}
        for i, lot in enumerate(book.lots):
            for j, rec in enumerate(lot.tubes):
                uid = f"{i}-{j}"
                pieces[uid] = Piece(uid, rec, lot.number)

        with self.lock:
            self.source = Path(path)
            self.book = book
            self.pieces = pieces
            self.invalidate()
            for p in pieces.values():
                self._build(p)
        return self.summary()

    def _build(self, p: Piece) -> None:
        """Perimetre -> programme -> geometrie -> controles, pour une piece."""
        rec = p.record
        conv = conventions.get(self.config.convention)
        lft_length = rec.number("LONGUEUR")

        raw = crippa.parse(rec.iso, ref=p.ref) if scope.has_program(rec.iso) else None
        p.verdict = scope.evaluate(rec, raw)
        diameter = p.verdict.diameter or code_mat_diameter(rec.get("CODE_MAT"))

        if not p.verdict.ok:
            # Hors perimetre : on ne fabrique AUCUNE geometrie. Un modele
            # invente pour un tuyau souple serait pris pour argent comptant.
            tooling = self.config.for_program("", diameter, rec.get("CODE_MAT"))
            p.raw = raw
            p.tooling = tooling
            p.recut = rec.recut
            p.tube = conv.build_straight(p.ref, lft_length or 0.0,
                                         diameter or 0.0, tooling)
            p.tube.list_number = rec.list_number
            p.tube.program_number = rec.program_number
            p.issues = [validate.Issue(
                validate.WARN, "hors_perimetre",
                f"{p.verdict.reason} — {p.verdict.detail}", "perimetre")]
            return

        raw.name = raw.name or p.ref
        if raw.declared_length is None and lft_length:
            raw.declared_length = float(lft_length)
        if raw.diameter is None and diameter:
            raw.diameter = float(diameter)
        tooling = self.config.for_program(raw.tooling, raw.diameter,
                                          rec.get("CODE_MAT"))
        p.recut = rec.recut or float(raw.recut or 0.0)
        p.raw = raw
        p.tube = conv.build(raw, tooling, recut=p.recut,
                            angle_mode=self.config.angle_mode)

        p.tube.ref = p.ref
        p.tube.list_number = rec.list_number
        p.tube.program_number = rec.program_number
        p.tooling = tooling
        p.tube.warnings.extend(rec.warnings)
        p.issues = validate.check(p.tube, tooling, recut=p.recut,
                                  length_tol=self.config.tolerance,
                                  lft_length=lft_length)

    def rebuild(self) -> dict:
        """Rejoue tout le fichier avec la configuration courante."""
        with self.lock:
            self.invalidate()
            for p in self.pieces.values():
                self._build(p)
        return self.summary()

    # --------------------------------------------------------------- resume
    def summary(self) -> dict:
        lots = []
        for lot in (self.book.lots if self.book else []):
            items = []
            for p in self.pieces.values():
                if p.lot != lot.number:
                    continue
                t, raw = p.tube, p.raw
                mat = p.verdict.material if p.verdict else None
                items.append({
                    "uid": p.uid,
                    "ref": p.ref,
                    "lot": p.lot,
                    "programme": p.record.program_number,
                    "liste": p.record.list_number,
                    "scope": p.verdict.status if p.verdict else "",
                    "reason": p.verdict.reason if p.verdict else "",
                    "reason_label": p.verdict.detail if p.verdict else "",
                    "nature": p.verdict.kind if p.verdict else "inconnue",
                    "matiere": mat.designation if mat else None,
                    "code_mat": mat.code if mat else None,
                    "diameter": t.diameter,
                    "tooling": raw.tooling if raw else "",
                    "head": bsa.HEAD_NAMES.get(raw.head if raw else None, "—"),
                    "declared": t.declared_length,
                    "bends": t.n_bends,
                    "recut": p.recut,
                    "complete": t.complete,
                    "straight": t.straight,
                    "rows": len(p.record.rows),
                    "status": "exclue" if not p.in_scope else validate.worst(p.issues),
                })
            lots.append({"number": lot.number, "label": lot.label,
                         "count": len(items), "tubes": items})
        return {
            "source": str(self.source) if self.source else None,
            "sheets": self.book.sheets_read if self.book else [],
            "columns": self.book.columns if self.book else [],
            "warnings": self.book.warnings if self.book else [],
            "lots": lots,
            "count": sum(l["count"] for l in lots),
        }

    # -------------------------------------------------------------- geometrie
    def detail(self, uid: str, deflection: float = 0.04) -> dict:
        p = self.pieces.get(uid)
        if p is None:
            raise KeyError(f"piece {uid} inconnue")
        key = (uid, round(deflection, 4), self.config_key)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        data = self._detail(p, deflection)
        if len(self._cache) >= MESH_CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = data
        return data

    def _detail(self, p: Piece, deflection: float) -> dict:
        tube, tooling = p.tube, p.tooling
        cl = geometry.build(tube, handedness=self.config.handedness)

        mesh = mesh_error = None
        try:
            shp = solid.build_solid(tube, cl, tooling.wall)
            mesh = stepreader.tessellate(shp, deflection)
        except Exception as exc:
            mesh_error = str(exc)

        prims = [{
            "kind": pr.kind,
            "start": [round(float(v), 4) for v in pr.start],
            "end": [round(float(v), 4) for v in pr.end],
            "mid": None if pr.mid is None else [round(float(v), 4) for v in pr.mid],
            "centre": None if pr.centre is None else [round(float(v), 4) for v in pr.centre],
            "radius": round(pr.radius, 4),
            "angle": round(pr.angle, 4),
            "length": round(pr.length, 4),
        } for pr in cl.primitives]

        bends = [{"angle": round(b.angle, 4), "r15": b.r15,
                  "springback": round(b.springback, 4),
                  "rotation": b.rotation, "clr": b.clr} for b in tube.bends]

        return {
            "uid": p.uid,
            "ref": p.ref,
            "lot": p.lot,
            "programme": p.record.program_number,
            "liste": p.record.list_number,
            "diameter": tube.diameter,
            "wall": (p.verdict.material.wall if p.verdict and p.verdict.material
                     else tooling.wall),
            "material": tooling.material,
            "matiere": p.verdict.as_dict() if p.verdict else None,
            "bend_radius": tooling.clr,
            "tooling": p.raw.tooling if p.raw else "",
            "head": bsa.HEAD_NAMES.get(p.raw.head if p.raw else None, "—"),
            "comment": tube.comment,
            "source": tube.source,
            "declared": tube.declared_length,
            "ds": tube.ds,
            "recut": p.recut,
            "straight": tube.straight,
            "angle_mode": tube.angle_mode,
            "developed": round(cl.developed, 4),
            "straights": [round(s, 4) for s in tube.straights],
            "bends": bends,
            "primitives": prims,
            "polyline": [[round(float(v), 4) for v in q] for q in cl.points],
            "vertices": [[round(float(v), 4) for v in q] for q in cl.vertices],
            "tangents": [[round(float(v), 4) for v in q] for q in cl.tangent_points],
            "bbox": {"size": [round(float(v), 3) for v in cl.envelope],
                     "min": [round(float(v), 3) for v in cl.bbox[0]],
                     "max": [round(float(v), 3) for v in cl.bbox[1]]},
            "mesh": mesh,
            "mesh_error": mesh_error,
            "issues": [{"level": i.level, "code": i.code, "message": i.message,
                        "source": i.source} for i in p.issues],
            "status": validate.worst(p.issues),
            "fields": self.fields(p),
            "simulation": {
                "straights": [round(v, 4) for v in tube.straights],
                "bends": bends,
                "handedness": self.config.handedness,
                "blank_length": round(cl.developed, 3),
                "cut_length": tube.declared_length,
            },
        }

    def fields(self, p: Piece) -> list[dict]:
        """Toutes les colonnes de la LFT pour cette piece, sans exception.

        C'est la garantie qu'aucune information de l'Excel n'est perdue :
        l'interface les affiche telles quelles, y compris les colonnes que
        l'application n'exploite pas.
        """
        out = []
        for col, f in p.record.fields.items():
            out.append({
                "column": col,
                "values": [str(v) for v in f.values],
                "rows": f.rows,
                "conflict": f.conflicting,
            })
        out.sort(key=lambda x: x["column"])
        return out

    # ------------------------------------------------------------------ plan
    def plan_data(self, p: Piece, cl) -> render.PlanData:
        """Rassemble ce qui figure sur le plan, y compris le rattachement."""
        entry = registry.parse_filename(Path(self.source).stem) if self.source \
            else registry.Entry("", "", "", "nom de fichier")
        rec = p.record
        return render.PlanData(
            tube=p.tube, centerline=cl, tooling=p.tooling, issues=p.issues,
            material=p.verdict.material if p.verdict else None,
            groupe=entry.groupe, machine=entry.machine,
            designation=entry.description,
            lft=Path(self.source).stem if self.source else "",
            source_file=str(self.source or ""),
            embout_1=str(rec.get("EMBOUT_1") or ""),
            embout_2=str(rec.get("EMBOUT_2") or ""),
            recoupe_1=float(rec.number("RECOUPE_1") or 0.0),
            recoupe_2=float(rec.number("RECOUPE_2") or 0.0),
            recoupe_programme=float((p.raw.recut if p.raw else 0) or 0.0),
            lft_length=rec.number("LONGUEUR"),
            quantite=rec.number("QTE_DEB"),
            remarque=str(rec.get("REMARQUE") or ""),
            handedness=self.config.handedness,
            angle_mode=self.config.angle_mode,
        )

    # ---------------------------------------------------------------- exports
    def export(self, uids: list[str], out_dir: str, formats: list[str]) -> dict:
        out = Path(out_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        done, failed = [], []
        for uid in uids:
            p = self.pieces.get(uid)
            if p is None:
                failed.append({"ref": uid, "error": "piece inconnue"})
                continue
            # deux lots peuvent porter le meme repere : le nom de fichier doit
            # rester unique, donc on prefixe par le lot quand il y en a plusieurs
            if not p.in_scope:
                failed.append({"ref": p.ref,
                               "error": f"hors périmètre : {p.verdict.detail}"})
                continue
            name = p.ref
            if self.book and len(self.book.lots) > 1 and p.lot:
                name = f"{p.lot}_{p.ref}"
            try:
                cl = geometry.build(p.tube, handedness=self.config.handedness)
                cad = [f for f in formats if f in ("step", "stl", "brep")]
                if cad:
                    for f in solid.export(p.tube, cl, out, p.tooling, cad,
                                          basename=name):
                        done.append(str(f))
                if "pdf" in formats or "svg" in formats:
                    data = self.plan_data(p, cl)
                    if "pdf" in formats:
                        done.append(str(render.to_pdf(data, out / f"{name}.pdf")))
                    if "svg" in formats:
                        q = out / f"{name}.svg"
                        q.write_text(render.to_svg(data), encoding="utf-8")
                        done.append(str(q))
                if "dxf" in formats:
                    render.to_dxf(p.tube, cl, str(out / f"{name}.dxf"))
                    done.append(str(out / f"{name}.dxf"))
            except Exception as exc:
                failed.append({"ref": p.ref, "error": str(exc)})
        return {"written": done, "failed": failed, "dir": str(out)}


def create_app(session: Session | None = None, token: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    # Flask trie les cles JSON par defaut, ce qui renverrait les outillages
    # dans l'ordre alphabetique : Ø10, Ø12, Ø15... avant Ø4. On conserve
    # l'ordre d'insertion, qui est l'ordre croissant des diametres.
    app.json.sort_keys = False
    app.config["SESSION"] = session or Session()
    app.config["TOKEN"] = token or secrets.token_urlsafe(16)

    def api_error(exc: Exception, code: int = 400):
        app.logger.debug(traceback.format_exc())
        return jsonify({"error": str(exc), "type": type(exc).__name__}), code

    # ------------------------------------------------------------- statiques
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/<path:filename>")
    def asset(filename: str):
        return send_from_directory(STATIC, filename)

    # -------------------------------------------------------------------- api
    @app.get("/api/diag")
    def diag():
        """Diagnostic d'environnement, utile sur un poste ou rien ne marche."""
        import platform
        mods = {}
        for name in ("cadquery", "OCP", "OCP.STEPControl", "OCP.BRepOffsetAPI",
                     "numpy", "openpyxl", "ezdxf", "flask"):
            try:
                __import__(name)
                mods[name] = "ok"
            except Exception as exc:
                mods[name] = f"{type(exc).__name__}: {exc}"
        return jsonify({
            "python": sys.version,
            "platform": platform.platform(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "modules": mods,
        })

    @app.get("/api/status")
    def status():
        s: Session = app.config["SESSION"]
        return jsonify({
            "ready": True,
            "cad": _cad_available(),
            "cad_error": _cad_error(),
            "source": str(s.source) if s.source else None,
            "count": len(s.pieces),
            "diameters": sorted(bsa.RM),
        })

    @app.post("/api/upload")
    def upload():
        """Recoit un fichier choisi dans l'explorateur du systeme.

        Le navigateur ne communique jamais le chemin reel d'un fichier, pour
        des raisons de confidentialite. On recupere donc son contenu, on
        l'ecrit dans un dossier temporaire, et on traite ce fichier-la.
        """
        s: Session = app.config["SESSION"]
        f = request.files.get("file")
        if f is None or not f.filename:
            return api_error(ValueError("aucun fichier recu"))
        target = Path(s.workdir) / Path(f.filename).name
        f.save(target)
        try:
            if target.suffix.lower() in (".stp", ".step"):
                return jsonify({"kind": "step",
                                "data": stepreader.analyse(str(target))})
            return jsonify({"kind": "lft", "data": s.open_lft(str(target)),
                            "path": str(target)})
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/settings")
    def get_settings():
        s: Session = app.config["SESSION"]
        return jsonify({
            "config": s.config.data,
            "conventions": sorted(conventions.REGISTRY),
            "angle_modes": list(bsa.ANGLE_MODES),
            "reference": {
                "rm": bsa.RM,
                "wall": bsa.WALL,
                "elongation": bsa.ELONGATION_PCT,
                "elasticity": bsa.ELASTICITY_PCT,
                "r15_for_90": bsa.R15_FOR_90,
                "min_straight": bsa.MIN_STRAIGHT,
                "min_last": bsa.MIN_LAST,
                "min_last_two": bsa.MIN_LAST_TWO,
                "max_last": bsa.MAX_LAST,
                "max_angle": bsa.MAX_BEND_ANGLE,
                "developed": [bsa.MIN_DEVELOPED, bsa.RECOMMENDED_DEVELOPED,
                              bsa.MAX_DEVELOPED],
            },
        })

    @app.post("/api/settings")
    def post_settings():
        """Applique une configuration et rejoue le fichier courant, pour que
        l'effet de chaque reglage soit immediatement visible."""
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        previous = s.config
        try:
            s.config = Config(data)
            conventions.get(s.config.convention)      # valide le nom
            return jsonify({"ok": True, "reloaded": s.rebuild()})
        except Exception as exc:
            s.config = previous
            s.invalidate()
            return api_error(exc)

    @app.post("/api/settings/reset")
    def reset_settings():
        s: Session = app.config["SESSION"]
        s.config = Config.load(None)
        return jsonify({"ok": True, "config": s.config.data,
                        "reloaded": s.rebuild()})

    @app.post("/api/open")
    def open_file():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(s.open_lft(data.get("path", ""), data.get("sheet")))
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/tubes")
    def tubes():
        return jsonify(app.config["SESSION"].summary())

    @app.get("/api/tube/<uid>")
    def tube_detail(uid: str):
        s: Session = app.config["SESSION"]
        try:
            defl = float(request.args.get("deflection", 0.04))
            return jsonify(s.detail(uid, defl))
        except KeyError as exc:
            return api_error(exc, 404)
        except Exception as exc:
            return api_error(exc)

    @app.post("/api/step")
    def read_step():
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(stepreader.analyse(data.get("path", ""),
                                              float(data.get("deflection", 0.04))))
        except Exception as exc:
            return api_error(exc)

    @app.post("/api/export")
    def export():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(s.export(data.get("uids") or list(s.pieces),
                                    data.get("dir") or ".",
                                    data.get("formats") or ["step"]))
        except Exception as exc:
            return api_error(exc)

    @app.post("/api/shutdown")
    def shutdown():
        func = request.environ.get("werkzeug.server.shutdown")
        if func:
            func()
        return jsonify({"ok": True})

    return app


def _cad_available() -> bool:
    return not _cad_error()


def _cad_error() -> str:
    """Retourne le message d'erreur d'import du noyau CAO, ou une chaine vide."""
    try:
        import cadquery  # noqa: F401
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
