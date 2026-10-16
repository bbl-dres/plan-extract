"""A worker process of the app's heavy lane (server.py, FPX_APP_WORKERS): one extraction or read-ahead at a time, asked
for on a pipe by its thread in the server, with its own segmenter and OCR engine. Started by server.Remote with
multiprocessing's spawn; never run by hand.

Messages from the server: ("extract" | "prefetch", args) with job_dir, file, confirm, analysis (and keys), threads;
("stop", {}). Back: ("ready", seconds) once after the warm-up, or ("fatal", message); per job ("log", line),
("progress", key, stage) as they happen, then ("done", result) or ("error", exception type, message, traceback).

The analysed session (the packages with their rasters) is read from the job folder (bridge.load_session, written by
the server after the analysis) and kept for the next job when it is the same sheet; a missing one is analysed again. The
segmenter's threads follow what the server sends: all cores while this is the only busy worker, a share otherwise.
FPX_APP_BRIDGE names a bridge module file to load instead of app/bridge.py (the tests' stub)."""
import ctypes
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path

SESSIONS_KEPT = 1                                          # a session holds the sheet's rasters: one per process


def load_bridge():
    path = os.environ.get("FPX_APP_BRIDGE")
    if path:
        spec = importlib.util.spec_from_file_location("bridge", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bridge"] = mod
        spec.loader.exec_module(mod)
        return mod
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bridge
    return bridge


def set_threads(n):
    """The segmenter's threads for the next job (torch; absent in the tests)."""
    if not n:
        return
    try:
        import torch
        torch.set_num_threads(int(n))
    except Exception:                                      # noqa: BLE001 - no torch, or a backend that cannot change it
        pass


def trim_memory():
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def main(conn, settings):
    try:
        bridge = load_bridge()
        bridge.JOB_CACHE = bool(settings.get("public"))
        t0 = time.time()
        for fn in (getattr(bridge, "engine", None), getattr(bridge, "model", None)):
            if fn:
                fn()
        conn.send(("ready", round(time.time() - t0, 1)))
    except Exception as ex:                                # noqa: BLE001 - reported once; the server fails the jobs it would get
        conn.send(("fatal", f"{type(ex).__name__}: {ex}"))
        return
    sessions = {}                                          # job id -> session, the last SESSIONS_KEPT

    def session_of(d, file, log):
        session = sessions.get(d.name)
        if session is None:
            session = bridge.load_session(d)
            if session is not None:
                log("analysed session loaded from disk")
        if session is None:
            log("the analysed session is not on disk: the layout runs again first")
            _, session = bridge.analyse(d, d / file, log)
            bridge.save_session(d, session)
        session["upload"] = d / file
        sessions.pop(d.name, None)
        sessions[d.name] = session
        while len(sessions) > SESSIONS_KEPT:
            sessions.pop(next(iter(sessions)))
        return session

    while True:
        try:
            op, args = conn.recv()
        except (EOFError, OSError):
            return
        if op == "stop":
            return
        log = lambda line: conn.send(("log", line))        # noqa: E731
        progress = lambda key, stage: conn.send(("progress", key, stage))   # noqa: E731
        try:
            set_threads(args.get("threads"))
            d = Path(args["job_dir"])
            log(f"worker {settings.get('name', '')}: {args.get('threads')} threads")
            session = session_of(d, args["file"], log)
            if op == "extract":
                result = bridge.extract(d, session, args["confirm"], log, progress, analysis=args.get("analysis"))
            elif op == "prefetch":
                result = bridge.prefetch_text(d, session, args["analysis"], args["confirm"], args["keys"], log)
            else:
                raise ValueError(f"unknown work: {op}")
            conn.send(("done", result))
        except Exception as ex:                            # noqa: BLE001 - the server reports it on the job
            conn.send(("error", type(ex).__name__, str(ex), traceback.format_exc()))
        finally:
            if settings.get("public"):                     # the rasters do not stay in an idle process; the pickle is on disk
                sessions.clear()
            trim_memory()
