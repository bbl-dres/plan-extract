"""Small helpers shared by server.py, bridge.py and the tests: standard library only, no pipeline.

    AppError                an error to show as it is (400), with an optional code the page translates
    dump(obj, path)         JSON to a file, atomically (a crash never leaves half a file)
    read_json(path, d)      JSON from a file, or the default when it is missing or broken
    jsonable(v)             json.dumps' fallback for numpy values and paths
    drawing_key(p, d)       '<package>/<drawing>', the key of a drawing everywhere in the app
    iter_drawings(a)        (key, package, drawing) for every drawing of an analysis
"""
import json
import os
from pathlib import Path


class AppError(RuntimeError):
    """An error the user can act on (a missing file, an unsupported upload, a busy sheet): shown as it is. `code`
    names it for the page, which shows its own translation (i18n.json: error.<code>)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def jsonable(v):
    import numpy as np
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def dump(obj, path, indent=1):
    """Write JSON next to its target and swap it in: readers see the old file or the new one, never half of one."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False, default=jsonable), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def drawing_key(pkg_id, drawing_id):
    return f"{pkg_id}/{drawing_id}"


def iter_drawings(analysis):
    for pkg in (analysis or {}).get("packages", []):
        for d in pkg.get("drawings", []):
            yield drawing_key(pkg["id"], d["id"]), pkg, d
