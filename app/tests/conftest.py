"""The server under test without the pipeline: a stub `bridge` module is installed before server.py is imported, so
the routes, the job model and the worker can be exercised in a second with no model, OCR or torch."""
import base64
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


from records import AppError, dump  # noqa: E402 - the app folder is on the path from here on


ANALYSIS = {"packages": [{"id": "p1", "index": 0, "summary": {"input_class": "raster", "dpi": 300},
                          "preview": {"file": "p1_preview.jpg", "scale": 1.0}, "regions": [], "layout": {}, "layout_flags": [],
                          "drawings": [{"id": "d1", "kind": "floor plan", "title": "Plan", "polygon_px": [[0, 0], [10, 0], [10, 10], [0, 10]],
                                        "bbox_px": [0, 0, 10, 10], "scale": None, "storey": None}]}], "seconds": 0.1}


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


# A finished drawing as the pipeline writes it (<drawing>.json), reduced to what the results screen reads: two rooms
# joined by a door in a 10 x 8 m extent, plan metres.
RESULT = {
    "sheet": {"title": "Plan", "extent": [0, 0, 10, 8], "scale": {"consensus": {"px_per_m": 50.0, "confidence": "high"}, "confirmed": {"px_per_m": 50.0}}, "triage": {}},
    "rooms": [
        {"id": "r01", "name": "Office", "number": "1.01", "area": 12.0, "area_net": 12.0, "area_net_simple": 12.1, "area_gross": 13.4, "gross_basis": "wall axes",
         "area_stamp": 12.3, "area_deviation_pct": -2.4, "confidence": "high", "reasons": [], "face_flags": [], "neighbours": ["r02"],
         "geometry": _square(0, 0, 4, 3), "geometry_simple": _square(0.05, 0.05, 3.95, 2.95), "geometry_gross": _square(-0.1, -0.1, 4.1, 3.1)},
        {"id": "r02", "name": None, "small_region": True, "area": 8.0, "area_net": 8.0, "area_gross": 8.4, "area_stamp": None,
         "confidence": "low", "reasons": ["no stamp, tiny"], "neighbours": ["r01"], "geometry": _square(4, 0, 6, 4)},
    ],
    "voids": [], "stairs": [], "structure": [], "stamps": [],
    "wall_polygons": [_square(-0.2, -0.2, 6.2, 0)], "wall_polygons_raw": [_square(-0.21, -0.19, 6.19, 0.01)],
    "walls": [{"id": "w000", "centre_line": {"type": "LineString", "coordinates": [[-0.2, -0.1], [6.2, -0.1]]}, "thickness": 0.2, "length": 6.4,
               "kind": "exterior", "rooms": ["r01", "outside"], "construction": "massive", "notes": ["snapped"], "openings": []}],
    "openings": [{"id": "o1", "kind": "door", "width": 0.9, "exterior": False, "host": "w000", "connects": ["r01", "r02"], "geometry": _square(3.9, 1, 4.1, 1.9)}],
    "floor": {"gf": _square(-0.2, -0.2, 6.2, 4.2), "gf_area": 20.0, "agf_area": 0.0, "sum_net": 20.0, "sum_net_simple": 20.1, "sum_gross": 21.0, "facade_half_wall": 1.5},
    "connectivity": [{"a": "r01", "b": "r02", "opening": "o1"}],
    "qa": [{"check": "room area", "severity": "medium", "element": "r02", "message": "no stamp to check the area against"}],
    "drawings": [{"id": "d1"}],
}
PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")


def wait_until(cond, timeout=10, every=0.02):
    """Poll `cond()` until it holds (True) or `timeout` seconds passed (the last value): the tests wait for the
    workers this way instead of sleeping a fixed time."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(every)
    return bool(cond())


def make_bridge(gate=None):
    """A bridge whose analyse() returns a fixed analysis; with a gate (threading.Event) it waits until set, so a job
    stays 'analysing' as long as a test wants."""
    b = types.ModuleType("bridge")
    b.AppError = AppError
    b.PIPELINE_DIR = APP
    b.FLOOR_PLAN = "floor plan"
    b.THREADS = 1
    b.check = lambda: {"pipeline_dir": str(APP), "python": "x", "threads": 1, "items": {}, "ok": True}

    def analyse(job_dir, upload, log):
        if gate is not None:
            assert gate.wait(10), "a test held the worker and did not let it go"
        log("stub analysis")
        return json.loads(json.dumps(ANALYSIS)), {"packages": []}

    def extract(job_dir, session, confirm, log, progress, analysis=None):
        progress("p1/d1", "stub stage")
        if not b.EXTRACT_OK:
            return {"p1/d1": {"key": "p1/d1", "outputs": None, "error": "stub: no pipeline", "skipped": None, "times": {}}}
        out = Path(job_dir) / "out"
        out.mkdir(exist_ok=True)
        dump(RESULT, out / "p1-d1.json")
        (out / "p1-d1_work.png").write_bytes(PNG_1PX)
        return {"p1/d1": {"key": "p1/d1", "package": "p1", "drawing": "d1", "title": "Plan", "kind": "floor plan", "storey": "EG",
                          "skipped": None, "error": None, "times": {"total": 1.5}, "sheet_id": "p1-d1", "px_per_m": 50,
                          "work_px": [500, 400], "summary": {"rooms": 2, "gf_area": 20.0},
                          "outputs": {"json": "p1-d1.json", "dxf": "p1-d1.dxf", "work": "p1-d1_work.png", "overlay": "p1-d1_overlay.png"}}}

    b.EXTRACT_OK = False                                   # a test sets it for a finished extraction (RESULT)

    def scale_step(job_dir, session, analysis, confirm, keys, log, doors=False):
        if gate is not None:
            assert gate.wait(10), "a test held the worker and did not let it go"
        for p in analysis["packages"]:
            for d in p["drawings"]:
                if f"{p['id']}/{d['id']}" in keys:
                    d["scale"] = {"px_per_m": 50.0 if not doors else 48.0, "confidence": "high", "agreeing": [{"cue": "door_widths" if doors else "dimension_strings"}]}
        log("stub scale cues")
        return list(keys)

    b.scale_step = scale_step
    b.PREFETCHED = []                                      # (job id, keys, polygon of the first key) of every read-ahead
    b.PREFETCH_GATE = threading.Event()                    # cleared by a test: the read-ahead waits until set
    b.PREFETCH_GATE.set()

    def prefetch_text(job_dir, session, analysis, confirm, keys, log):
        assert b.PREFETCH_GATE.wait(10), "a test held the read-ahead and did not let it go"
        b.PREFETCHED.append((Path(job_dir).name, list(keys), (confirm.get(keys[0]) or {}).get("polygon_px")))
        log("stub read-ahead")
        return list(keys)

    b.prefetch_text = prefetch_text
    b.live_name = lambda key: f"{key.replace('/', '-')}_live.json"

    b.analyse = analyse
    b.extract = extract
    b.save_session = lambda job_dir, session: True
    b.load_session = lambda job_dir: None
    return b


@pytest.fixture
def served(tmp_path, monkeypatch):
    """server module with its data folder in tmp_path, a worker thread, and the HTTP server on a free port.
    Yields (base url, server module, gate)."""
    gate = threading.Event()
    gate.set()
    sys.modules["bridge"] = make_bridge(gate)
    sys.modules.pop("server", None)
    import server
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "SHEETS", tmp_path / "sheets")
    server.JOBS.clear()
    server.SESSIONS.clear()
    server.CURRENT.clear()
    for lane in server.QUEUED.values():
        lane.clear()
    monkeypatch.setattr(server, "WORKERS", 0)             # the heavy lane as a thread: the stub bridge lives in this process
    server.load_jobs()
    workers = server.start_workers()
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", server, gate
    finally:
        gate.set()                                         # a test that held the worker lets it finish
        server.bridge.PREFETCH_GATE.set()                  # ... and one that held the read-ahead
        srv.shutdown()
        srv.server_close()
        server.stop_workers()                              # the workers stop after what is queued
        for w in workers:
            w.join(10)
