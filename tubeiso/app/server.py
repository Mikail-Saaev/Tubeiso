"""Serveur applicatif.

L'interface est une page web servie en local. Ce choix n'est pas un pis-aller :
il garantit que le rendu 3D fonctionne sur n'importe quelle machine (le moteur
WebGL du navigateur est deja installe partout), evite tout probleme de pilote
OpenGL ou de contexte graphique, et rend l'application identique sous Windows,
macOS et Linux.

Le serveur n'ecoute que sur 127.0.0.1 et tire un jeton aleatoire au demarrage :
rien n'est expose sur le reseau.
"""
from __future__ import annotations

import secrets
import sys
import threading
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .. import bsa, conventions, geometry, render, solid, stepreader, validate
from ..config import Config, read_lft
from ..parsers import crippa

STATIC = Path(__file__).with_name("static")


class Session:
    """Etat courant : le fichier ouvert et les pieces qu'il contient."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.config = Config.load(None)
        self.source: Path | None = None
        self.tubes: dict[str, dict] = {}

    # ------------------------------------------------------------ chargement
    def open_lft(self, path: str, column: str = "PROGCRIPPA") -> dict:
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"fichier introuvable : {p}")
        rows = read_lft(p, column)
        conv = conventions.get(self.config.convention)
        tubes: dict[str, dict] = {}

        for row in rows:
            raw = crippa.parse(str(row["iso"]), ref=row["ref"])
            raw.name = raw.name or row["ref"]
            if raw.declared_length is None and row["length"]:
                raw.declared_length = float(row["length"])
            tooling = self.config.for_program(raw.tooling, raw.diameter,
                                              row["code_mat"])
            recut = float(row.get("recut") or raw.recut or 0.0)
            tube = conv.build(raw, tooling, recut=recut)
            issues = validate.check(tube, tooling, recut=recut,
                                    length_tol=self.config.tolerance)
            tubes[tube.ref] = {
                "raw": raw, "tube": tube, "tooling": tooling,
                "recut": recut, "issues": issues, "row": row,
            }

        with self.lock:
            self.source, self.tubes = p, tubes
        return self.summary()

    def summary(self) -> dict:
        items = []
        for ref, e in self.tubes.items():
            t, raw = e["tube"], e["raw"]
            items.append({
                "ref": ref,
                "diameter": t.diameter,
                "tooling": raw.tooling,
                "head": bsa.HEAD_NAMES.get(raw.head, "-"),
                "machine": raw.machine,
                "declared": t.declared_length,
                "bends": t.n_bends,
                "recut": e["recut"],
                "complete": t.complete,
                "status": validate.worst(e["issues"]),
                "issues": [{"level": i.level, "code": i.code,
                            "message": i.message, "source": i.source}
                           for i in e["issues"]],
            })
        items.sort(key=lambda x: x["ref"])
        return {"source": str(self.source) if self.source else None,
                "count": len(items), "tubes": items}

    # -------------------------------------------------------------- geometrie
    def detail(self, ref: str, deflection: float = 0.04) -> dict:
        e = self.tubes.get(ref)
        if e is None:
            raise KeyError(f"repere {ref} inconnu")
        tube, tooling = e["tube"], e["tooling"]
        cl = geometry.build(tube, handedness=self.config.handedness)

        mesh = None
        mesh_error = None
        try:
            shp = solid.build_solid(tube, cl, tooling.wall)
            mesh = stepreader.tessellate(shp, deflection)
        except Exception as exc:
            mesh_error = str(exc)

        prims = [{
            "kind": p.kind,
            "start": [round(float(v), 4) for v in p.start],
            "end": [round(float(v), 4) for v in p.end],
            "mid": None if p.mid is None else [round(float(v), 4) for v in p.mid],
            "centre": None if p.centre is None else [round(float(v), 4) for v in p.centre],
            "radius": round(p.radius, 4),
            "angle": round(p.angle, 4),
            "length": round(p.length, 4),
        } for p in cl.primitives]

        return {
            "ref": ref,
            "diameter": tube.diameter,
            "wall": tooling.wall,
            "material": tooling.material,
            "bend_radius": tooling.clr,
            "tooling": e["raw"].tooling,
            "head": bsa.HEAD_NAMES.get(e["raw"].head, "-"),
            "comment": tube.comment,
            "source": tube.source,
            "declared": tube.declared_length,
            "recut": e["recut"],
            "developed": round(cl.developed, 4),
            "straights": [round(s, 4) for s in tube.straights],
            "bends": [{"angle": b.angle, "rotation": b.rotation,
                       "clr": b.clr} for b in tube.bends],
            "primitives": prims,
            "polyline": [[round(float(v), 4) for v in p] for p in cl.points],
            "vertices": [[round(float(v), 4) for v in p] for p in cl.vertices],
            "tangents": [[round(float(v), 4) for v in p] for p in cl.tangent_points],
            "bbox": {"size": [round(float(v), 3) for v in cl.envelope]},
            "mesh": mesh,
            "mesh_error": mesh_error,
            "issues": [{"level": i.level, "code": i.code, "message": i.message,
                        "source": i.source} for i in e["issues"]],
            "status": validate.worst(e["issues"]),
        }

    # ---------------------------------------------------------------- exports
    def export(self, refs: list[str], out_dir: str, formats: list[str]) -> dict:
        out = Path(out_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        done, failed = [], []
        for ref in refs:
            e = self.tubes.get(ref)
            if e is None:
                failed.append({"ref": ref, "error": "repere inconnu"})
                continue
            tube, tooling = e["tube"], e["tooling"]
            try:
                cl = geometry.build(tube, handedness=self.config.handedness)
                cad = [f for f in formats if f in ("step", "stl", "brep")]
                if cad:
                    for f in solid.export(tube, cl, out, tooling, cad):
                        done.append(str(f))
                if "svg" in formats:
                    p = out / f"{ref}.svg"
                    p.write_text(render.to_svg(tube, cl, tooling, e["issues"]),
                                 encoding="utf-8")
                    done.append(str(p))
                if "dxf" in formats:
                    render.to_dxf(tube, cl, str(out / f"{ref}.dxf"))
                    done.append(str(out / f"{ref}.dxf"))
            except Exception as exc:
                failed.append({"ref": ref, "error": str(exc)})
        return {"written": done, "failed": failed, "dir": str(out)}


def create_app(session: Session | None = None, token: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
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
            "count": len(s.tubes),
            "diameters": sorted(bsa.RM),
        })

    @app.post("/api/open")
    def open_file():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(s.open_lft(data.get("path", ""),
                                      data.get("column", "PROGCRIPPA")))
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/tubes")
    def tubes():
        return jsonify(app.config["SESSION"].summary())

    @app.get("/api/tube/<ref>")
    def tube_detail(ref: str):
        s: Session = app.config["SESSION"]
        try:
            defl = float(request.args.get("deflection", 0.04))
            return jsonify(s.detail(ref, defl))
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
            return jsonify(s.export(data.get("refs") or list(s.tubes),
                                    data.get("dir") or ".",
                                    data.get("formats") or ["step"]))
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/tooling")
    def get_tooling():
        s: Session = app.config["SESSION"]
        return jsonify(s.config.data)

    @app.post("/api/tooling")
    def set_tooling():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            s.config = Config(data)
            if s.source:
                return jsonify(s.open_lft(str(s.source)))
            return jsonify({"ok": True})
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
