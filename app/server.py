"""Server of the floor plan workflow app: the page, the JSON API (openapi.json, shown at /api/docs) and the workers
that run the pipeline.

    python app/server.py [--port 8765] [--warm] [--public] [--workers N]

Standard library only: http.server with a thread per request, uploads as the raw request body. It binds to 127.0.0.1;
app/deploy publishes it behind nginx with HTTPS. POST and DELETE sent by another site's page are refused. Pipeline
work runs in two lanes, each a queue with what a user waits for first: the light lane (the analysis of an upload, a
scale step: seconds) on a thread of this process, the heavy lane (an extraction: tens of seconds; the read-ahead of
the text inside the drawings while the user confirms, behind everything else) on FPX_APP_WORKERS worker processes
(app/worker.py, each with its own segmenter and OCR engine), or on a thread of this process with 0. A newcomer's sheet
is analysed while another's extraction runs; the page polls its job and is told how many jobs are ahead of it and
when a worker is expected to take it.

--public is the public instance (app/deploy): no sheet list, every sheet job deleted FPX_APP_KEEP_MINUTES (60) after
its last request (also across restarts), the OCR cache and the analysed session (worker processes read it) inside
the job folder, no file names in the console, a reduced pipeline check, and limits on what is queued
(FPX_APP_MAX_BUSY). Its jobs live in app/data/public/, apart from a local run's app/data/sheets/.

A sheet job (app/data/<sheets|public>/<id>/): the upload, <package>_preview.jpg, analysis.json, job.json (status,
confirmations, results), log.txt, review.json, session.pkl (local runs, and the public server while worker processes need it) and out/ with the pipeline's outputs.
"""
import argparse
import collections
import copy
import csv
import ctypes
import functools
import io
import itertools
import json
import math
import multiprocessing
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import bridge
from records import AppError, dump, iter_drawings, jsonable, read_json

APP = Path(__file__).resolve().parent
DATA = APP / "data"
SHEETS = DATA / "sheets"                                   # app/data/public with --public
DEMO = APP / "demo"                                        # the demo sheet: demo.json (file, title, source, licence) and the file
ASSETS = ("js", "css", "media")                            # app/js, app/css and app/media are served as /js/..., /css/..., /media/...
STATIC = {"/i18n.json": "i18n.json",                       # the interface texts in DE, FR, IT, EN
          "/api/openapi.json": "openapi.json",             # the API description (OpenAPI 3.1)
          "/api/docs": "api-docs.html"}                    # the API documentation page (Swagger UI)
BUSY = ("analysing", "scaling", "queued", "running")       # also in js/state.js (a test keeps the two equal)

PUBLIC = False                                             # --public (see the module docstring)
KEEP_MINUTES = int(os.environ.get("FPX_APP_KEEP_MINUTES", "60"))   # public: a sheet job lives this long after its last request
MAX_BUSY = int(os.environ.get("FPX_APP_MAX_BUSY", "10"))  # public: new work is refused while this many jobs wait or run
MIN_FREE = 2e9                                             # public: new sheets are refused below this much free disk (bytes)
ABANDON_SECONDS = 180                                      # public: queued work nobody has asked about for this long is not run
TOUCH_SECONDS = 60                                         # public: the job folder's time records the last request, at most once a minute
SESSION_KEEP = int(os.environ.get("FPX_APP_SESSIONS", "3"))   # analysed sessions kept in memory (each holds the sheet rasters)
SESSION_BYTES = int(os.environ.get("FPX_APP_SESSION_MB", "1500")) * 1024 * 1024   # ... and at most this many bytes of rasters
PAYLOAD_KEEP = 8                                           # results payloads kept in memory, keyed by file
MAX_UPLOAD = 300 * 1024 * 1024                             # also nginx's client_max_body_size
MAX_JSON = 1024 * 1024                                     # a JSON request body
CHUNK = 1024 * 1024                                        # uploads are written in pieces of this size
LOG_KEEP, LOG_TAIL = 400, 40                               # log lines kept in memory, and sent with a polled job
SWEEP_SECONDS = 60
NAME_MAX = 60                                              # characters of the upload's name kept in its file name
MAX_EXTENT_M, MAX_AREA_M2 = 400, 30000                     # a confirmed scale must keep a drawing within these (see check_scale)
MAX_WORK_MPX = 40                                          # ... and its working image (50 px/m) within this many megapixels (fpx config work_max_mpx)
MAX_POLYGON = 500                                          # corners of a confirmed building area
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".dxf", ".dwg"}
DOWNLOADS = (".json", ".dxf", ".xlsx", ".ifc", ".csv", ".png")   # sent as attachments with ?download
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".dxf": "application/dxf", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".ifc": "application/x-step",
        ".svg": "image/svg+xml", ".webm": "video/webm"}


class NotFound(Exception):
    """A missing sheet, route or result: answered with 404 (a KeyError in the code stays a 500, as it should)."""


JOBS = {}                 # id -> job record (also job.json on disk); keys starting with _ stay in memory
SESSIONS = {}             # id -> the analysed packages and layouts (bounded: SESSION_KEEP, SESSION_BYTES)
PAYLOADS = {}             # (id, key) -> (stamp, payload) of results_payload
LOCK = threading.RLock()
# Work runs in two lanes, each a priority queue of (priority, sequence, (kind, job id, payload)): "light" for the
# analysis of an upload and a scale step, "heavy" for an extraction and a read-ahead. put_work() fills them,
# stop_workers() ends them; QUEUED mirrors what waits in each lane, in order, for the position a job is told.
LANES = {"light": queue.PriorityQueue(), "heavy": queue.PriorityQueue()}
LANE_OF = {"analyse": "light", "scale": "light", "extract": "heavy", "prefetch": "heavy"}
QUEUED = {"light": [], "heavy": []}                        # [(priority, sequence, kind, job id)], under LOCK
WORKERS = int(os.environ.get("FPX_APP_WORKERS", "0"))      # worker processes of the heavy lane; 0: a thread of this process
JOB_TIMEOUT = int(os.environ.get("FPX_APP_JOB_TIMEOUT", "1200"))   # seconds a worker process may spend on one piece of work
WARM_TIMEOUT = 600                                         # seconds a worker process has for its warm-up (the models)
FATAL_RETRY = 60                                           # seconds before a worker process that could not start is tried again
POLL_SECONDS = 5                                           # how often a waiting server thread looks at its process
REMOTES = {}              # worker name -> Remote (GET /api/status: which are unavailable)
JOB_LOCKS = {}            # job id -> Lock: one pipeline run at a time on a sheet's session (the local executor shares it)
WORKER_THREADS = []                                        # the light worker and the heavy workers (threads; each heavy one drives a process)
SEQ = itertools.count()
HEALTH = {}
CURRENT = {}              # worker name -> {kind, jid, since}: what each worker does right now (GET /api/status, the queue positions)
TYPICAL = {"analyse": 10.0, "scale": 6.0, "extract": 30.0, "prefetch": 15.0}   # seconds a kind of work takes (the demo sheet, 8 cores)
RECENT = {k: collections.deque(maxlen=5) for k in TYPICAL}   # the last few durations per kind: the estimate follows the machine
EXEC = threading.local()  # per worker thread: the executor of the heavy lane (Local, or the Remote of its worker process)


# ---------------------------------------------------------------------------------------------------------------------
# Jobs

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def job_dir(jid):
    return SHEETS / jid


def stored(job):
    """The record as job.json and the page see it: without the in-memory keys (_...)."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def commit(job, **fields):
    """Change a job record: the fields, its revision and job.json together, under the lock. A poll with the old
    revision gets the new record; one with the new revision cannot see a half-made change."""
    with LOCK:
        job.update(fields)
        job["rev"] = job.get("rev", 0) + 1
        dump(stored(job), job_dir(job["id"]) / "job.json")


def log(job, line):
    text = f"{time.strftime('%H:%M:%S')} {line}"
    with LOCK:
        job["log"].append(text)
        del job["log"][:-LOG_KEEP]
        job["rev"] = job.get("rev", 0) + 1
    try:
        with open(job_dir(job["id"]) / "log.txt", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    if not PUBLIC:                                         # the public console carries no file names or drawing titles
        print(f"[{job['id']}] {text}", flush=True)


def load_jobs():
    """The jobs on disk at start-up. A job removed while busy is deleted; one that was running is reported as stopped
    (a scale step that was running leaves the sheet ready). On the public server, jobs whose last request is more than
    KEEP_MINUTES old are deleted (the folder's time records it) and the rest stay."""
    SHEETS.mkdir(parents=True, exist_ok=True)
    for d in sorted(p for p in SHEETS.iterdir() if p.is_dir()):
        job = read_json(d / "job.json")
        if not isinstance(job, dict) or "id" not in job:
            if PUBLIC:
                remove_tree(d)
            continue
        age = time.time() - d.stat().st_mtime
        if job.get("cancel") or (PUBLIC and age > KEEP_MINUTES * 60):
            remove_tree(d)
            continue
        if job.get("status") == "scaling" and job.get("analysis"):
            job.update(status="ready", progress=None, error="The server restarted while the scale cues were read: read them again.")
        elif job.get("status") in BUSY:
            job.update(status="error", progress=None,
                       error="The server was stopped while this job ran. Upload the sheet again or run the extraction again.")
        job["seen"] = time.time() - age if PUBLIC else job.get("seen", time.time())
        job.setdefault("log", [])
        JOBS[job["id"]] = job
    if JOBS:
        print(f"{len(JOBS)} sheet jobs found in {SHEETS}")


def safe_name(name):
    """The upload's name as an ASCII file name (accents dropped, the rest replaced): ids, paths, URLs and headers stay
    ASCII whatever the name."""
    ascii_name = unicodedata.normalize("NFKD", Path(name).name).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w.\-]+", "_", ascii_name, flags=re.ASCII).strip("._") or "sheet"


def check_capacity():
    """The public server refuses new work while MAX_BUSY jobs wait or run, or below MIN_FREE of free disk."""
    if not PUBLIC:
        return
    with LOCK:
        waiting = sum(j["status"] in BUSY for j in JOBS.values())
    if waiting >= MAX_BUSY:
        raise AppError("The server is busy with other sheets. Try again in a few minutes.", "server_busy")
    if shutil.disk_usage(SHEETS).free < MIN_FREE:
        raise AppError("The server is short of disk space. Try again later.", "disk_full")


def new_job(name, size, write):
    """A sheet job from an upload of `size` bytes, stored by `write(path)` once the checks passed; queues its analysis."""
    safe = safe_name(name)
    ext = Path(safe).suffix.lower()
    if ext not in EXTENSIONS:
        raise AppError(f"Unsupported file type {ext or '(none)'}: upload PDF, JPG, PNG, TIFF, DXF or DWG.", "unsupported_type")
    check_capacity()
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex   # the id is the only key to a sheet: unguessable
    d = job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    upload = d / f"{Path(safe).stem[:NAME_MAX]}{ext}"
    try:
        write(upload)
    except BaseException:
        remove_tree(d)
        raise
    job = {"id": jid, "name": name, "file": upload.name, "size": size, "created": now(), "status": "analysing",
           "progress": {"key": None, "stage": "waiting for the worker"}, "error": None, "log": [], "analysis": None,
           "confirm": {}, "results": None, "times": {}, "seen": time.time()}
    with LOCK:
        JOBS[jid] = job
        try:
            commit(job)
        except Exception as ex:                            # noqa: BLE001 - the record could not be written: no job
            JOBS.pop(jid, None)
            remove_tree(d)
            raise AppError("The server could not write the job record (disk full?). Try again later.", "disk_full") from ex
    log(job, f"uploaded {name} ({size / 1e6:.1f} MB)")
    put_work("analyse", jid)
    return job


def demo_info():
    """The bundled demo sheet: app/demo/demo.json with file, title, source and licence, plus the file's size; None when
    there is none."""
    info = read_json(DEMO / "demo.json")
    if not isinstance(info, dict):
        return None
    f = DEMO / str(info.get("file") or "")
    if not f.is_file() or f.parent.resolve() != DEMO.resolve():
        return None
    return {**info, "size": f.stat().st_size}


def remove_tree(path):
    """Delete a job folder. Retried a few times: on Windows a file the worker still holds open keeps it. A failure is
    printed (the public server promises to keep nothing)."""
    for _ in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not Path(path).exists():
            return True
        time.sleep(0.1)
    print(f"could not delete {Path(path).name}", flush=True)
    return False


def drop(jid):
    """Drop a job from memory, under the lock; its folder is deleted after the lock is released (remove_tree)."""
    with LOCK:
        JOBS.pop(jid, None)
        SESSIONS.pop(jid, None)
        JOB_LOCKS.pop(jid, None)
        drop_payloads(jid)


def drop_payloads(jid):
    with LOCK:
        for k in [k for k in PAYLOADS if k[0] == jid]:
            PAYLOADS.pop(k, None)


def forget(jid):
    drop(jid)
    remove_tree(job_dir(jid))


def discard(job):
    """Forget a job removed while the worker was busy with it."""
    forget(job["id"])
    print(f"[{job['id']}] discarded (removed while busy)", flush=True)


def sweep(now=None):
    """Public server: delete the sheet jobs nobody has asked for in KEEP_MINUTES, with the upload, previews, outputs and
    OCR cache in their folders. A job the worker is busy with waits for the next round. Returns the deleted ids."""
    now = time.time() if now is None else now
    with LOCK:                                             # decided and dropped under the lock: no request can queue it meanwhile
        gone = [jid for jid, j in JOBS.items() if not in_flight(jid) and now - j.get("seen", 0) > KEEP_MINUTES * 60]
        for jid in gone:
            drop(jid)
        known = set(JOBS)
    for jid in gone:                                       # the folders are deleted without the lock: requests go on
        remove_tree(job_dir(jid))
        print(f"[{jid}] deleted after {KEEP_MINUTES} min without a request", flush=True)
    for d in list(SHEETS.iterdir()) if SHEETS.is_dir() else []:   # a folder without a job: left by a read-ahead that outlived a delete
        if d.is_dir() and d.name not in known and not (d / "job.json").exists() and now - d.stat().st_mtime > 600:
            remove_tree(d)
            print(f"[{d.name}] orphan folder deleted", flush=True)
    return gone


def sweeper():
    while True:
        time.sleep(SWEEP_SECONDS)
        try:
            sweep()
        except Exception:                                  # noqa: BLE001 - the sweeper must survive every round
            traceback.print_exc()


def session_bytes(session):
    return sum(getattr(getattr(P.get("pkg"), "img", None), "nbytes", 0) for P in session.get("packages", []))


def remember_session(jid, session):
    """Keep the analysed session in memory: the most recent SESSION_KEEP of them, and no more than SESSION_BYTES of
    rasters (a page can hold 480 MB). Older ones come back from their pickle, or are analysed again (_session)."""
    with LOCK:
        SESSIONS.pop(jid, None)
        SESSIONS[jid] = session
        while len(SESSIONS) > 1 and (len(SESSIONS) > SESSION_KEEP or sum(map(session_bytes, SESSIONS.values())) > SESSION_BYTES):
            SESSIONS.pop(next(iter(SESSIONS)))


def enqueue(job, kind, status, progress, payload=None, mark=None):
    """Queue work on a job. Refused while the job is busy, and on the public server while MAX_BUSY jobs are; the check,
    `mark(job)` and the new status happen under one lock, so two requests cannot both queue it."""
    with LOCK:
        if job["status"] in BUSY:
            raise AppError(f"The sheet is busy ({job['status']}).", "sheet_busy")
        check_capacity()
        before = copy.deepcopy({k: job.get(k) for k in ("status", "error", "progress", "confirm")})
        if mark:
            mark(job)
        try:
            commit(job, status=status, error=None, progress=progress)
        except Exception as ex:                            # noqa: BLE001 - the record could not be written (disk full): nothing is queued
            job.update(before)
            raise AppError("The server could not write the job record (disk full?). Try again later.", "disk_full") from ex
    put_work(kind, job["id"], payload)


def put_work(kind, jid, payload=None, low=False):
    """Queue work for its lane: what a user waits for first, work done ahead of time (low) after all of it."""
    lane, prio, seq = LANE_OF[kind], 1 if low else 0, next(SEQ)
    with LOCK:
        QUEUED[lane].append((prio, seq, kind, jid))
        QUEUED[lane].sort(key=lambda e: e[:2])
    LANES[lane].put((prio, seq, (kind, jid, payload)))


def lane_size(lane):
    return max(1, WORKERS) if lane == "heavy" else 1


def stop_workers():
    """End every worker once its queue is done (the tests, the shutdown)."""
    for lane, q in LANES.items():
        for _ in range(lane_size(lane)):
            q.put((2, next(SEQ), None))


def wait_idle():
    """Block until every queued piece of work is done (the tests)."""
    for q in LANES.values():
        q.join()


def typical(kind):
    """Seconds a kind of work takes here: the last few runs averaged, else the figure of the demo sheet."""
    with LOCK:                                             # a worker appends while a poll reads
        recent = list(RECENT[kind])
    return sum(recent) / len(recent) if recent else TYPICAL[kind]


def took(kind, seconds):
    with LOCK:
        RECENT[kind].append(seconds)


def job_lock(jid):
    with LOCK:
        return JOB_LOCKS.setdefault(jid, threading.Lock())


def in_flight(jid):
    """Whether work of the job waits in a queue or runs on a worker (under LOCK)."""
    return any(e[3] == jid for lane in QUEUED.values() for e in lane) or any(c["jid"] == jid for c in CURRENT.values())


def queue_eta(lane, jid, kind):
    """Where a piece of work stands in its lane: (jobs ahead of it, seconds until a worker is expected to take it),
    or None when it is not queued. The workers busy now finish first (what is left of a typical run), then the queue
    in its order, each piece taking a typical run on the worker free soonest."""
    now_ = time.time()
    with LOCK:
        slots = [max(0.0, typical(c["kind"]) - (now_ - c["since"])) for n, c in CURRENT.items() if n.startswith(lane)]
        waiting = list(QUEUED[lane])
    slots += [0.0] * max(0, lane_size(lane) - len(slots))
    slots.sort()
    for i, (_, _, k, j) in enumerate(waiting):
        start = slots[0]
        if j == jid and k == kind:
            return i, int(round(start))
        slots[0] = start + typical(k)
        slots.sort()
    return None


def queue_info(job):
    """The position and wait of a job whose work waits in a queue, for the page; None otherwise."""
    kind = {"analysing": "analyse", "scaling": "scale", "queued": "extract"}.get(job["status"])
    if not kind:
        return None
    eta = queue_eta(LANE_OF[kind], job["id"], kind)
    return {"position": eta[0], "wait": eta[1]} if eta else None


def wait_for_read_ahead(jid, limit=None):
    """An extraction waits while another worker still reads this sheet's text ahead: the cache it writes is what the
    extraction will use."""
    t0, limit = time.time(), JOB_TIMEOUT if limit is None else limit
    while time.time() - t0 < limit:
        with LOCK:
            reading = any(c["kind"] == "prefetch" and c["jid"] == jid for c in CURRENT.values())
        if not reading:
            return
        time.sleep(0.2)


# ---------------------------------------------------------------------------------------------------------------------
# The worker and its runners

def trim_memory():
    """Give the memory a finished job freed back to the system (glibc keeps it otherwise); Linux only."""
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def worker(lane, name, executor=None):
    """One worker of a lane: takes its queue in order, runs each piece with its runner (the heavy lane's through
    `executor`: Local, or the Remote of a worker process) and survives every job."""
    EXEC.bridge = executor or LOCAL
    q = LANES[lane]
    while True:
        _, seq, item = q.get()
        if item is None:
            q.task_done()
            return
        kind, jid, payload = item
        with LOCK:                                         # from the queue to the worker in one step: a delete meanwhile sees it busy
            QUEUED[lane] = [e for e in QUEUED[lane] if e[1] != seq]
            CURRENT[name] = {"kind": kind, "jid": jid, "since": time.time()}
            job = JOBS.get(jid)
        try:
            if job is None:
                continue
            with LOCK:
                cancelled, busy = job.get("cancel"), job["status"] in BUSY
            if cancelled:
                continue                                   # discarded in finally
            abandoned = PUBLIC and time.time() - job.get("seen", 0) > ABANDON_SECONDS
            if kind == "prefetch":                         # work ahead of time: only for a sheet at rest that someone looks at
                if busy or abandoned:
                    continue
            elif abandoned:
                commit(job, status="ready" if job.get("analysis") else "error", progress=None,
                       error="Not run: nobody was waiting for it any more.")
                continue
            if kind == "extract":
                wait_for_read_ahead(jid)
            with LOCK:
                CURRENT[name]["since"] = time.time()
            RUNNERS[kind](job, payload or {})
        except Exception as ex:                            # noqa: BLE001 - the worker must survive every job
            try:
                tb = traceback.format_exc()
                log(job, f"internal error: {type(ex).__name__}: {ex}" if PUBLIC else f"internal error\n{tb}")
                if PUBLIC:
                    print(f"[{job['id']}] internal error\n{tb}", flush=True)
                commit(job, status="error", progress=None, error=f"{type(ex).__name__}: {ex}")
            except Exception:                              # noqa: BLE001 - e.g. the job folder is gone: the loop must go on
                print(f"worker: {type(ex).__name__}", flush=True) if PUBLIC else traceback.print_exc()
        finally:
            with LOCK:
                CURRENT.pop(name, None)
            if job is not None:
                with LOCK:
                    cancelled = job.get("cancel")
                if cancelled:
                    discard(job)
            q.task_done()
            trim_memory()


class Local:
    """The heavy lane's executor when it runs in this process: the bridge with the session from memory or disk, one
    run at a time per sheet (a scale step on the light lane shares the session's packages)."""

    def extract(self, d, job, confirm, analysis, log, progress):
        with job_lock(job["id"]):
            return bridge.extract(d, _session(job, d), confirm, log, progress, analysis=analysis)

    def prefetch(self, d, job, analysis, confirm, keys, log):
        with job_lock(job["id"]):
            return bridge.prefetch_text(d, _session(job, d), analysis, confirm, keys, log)


LOCAL = Local()


class Remote:
    """A worker process (app/worker.py) and its pipe: the heavy lane's executor for one worker thread. extract() and
    prefetch() run in the process, which reads the analysed session from the job folder; its log lines and progress
    come back on the pipe as they happen. A process that dies is replaced for the next job; the job it held fails
    with that reason. One that cannot start, or does not finish its warm-up in WARM_TIMEOUT, fails its jobs with the
    message and is tried again after FATAL_RETRY seconds (the status lists it as unavailable meanwhile). The threads
    of the segmenter follow the number of busy workers: all cores for one, a share for several."""

    def __init__(self, name):
        self.name, self.proc, self.conn, self.fatal, self.fatal_at = name, None, None, None, 0.0
        REMOTES[name] = self
        self.start()

    def start(self):
        """Start the process and wait for its warm-up; whatever goes wrong is kept as `fatal` (the jobs fail with it)."""
        far = None
        try:
            import worker as child
            ctx = multiprocessing.get_context("spawn")
            self.conn, far = ctx.Pipe()
            proc = ctx.Process(target=child.main, args=(far, {"public": PUBLIC, "threads": getattr(bridge, "THREADS", 4), "name": self.name}),
                               daemon=True, name=self.name)
            proc.start()
            self.proc = proc                               # only a started process is kept: close() joins it
            try:
                if self.conn.poll(WARM_TIMEOUT):
                    msg = self.conn.recv()                 # ("ready", seconds) after the warm-up, or ("fatal", message)
                else:
                    msg = ("fatal", f"the worker process did not finish its warm-up within {WARM_TIMEOUT} s")
                    self.proc.terminate()
            except (EOFError, OSError):
                msg = ("fatal", f"the worker process ended at start (exit code {self.proc.exitcode})")
        except Exception as ex:                            # noqa: BLE001 - e.g. no spawn: reported on every job it would get
            msg = ("fatal", f"{type(ex).__name__}: {ex}")
            self.close()
        finally:
            if far is not None:
                far.close()
        self.fatal = None if msg[0] == "ready" else msg[1]
        self.fatal_at = 0.0 if self.fatal is None else time.time()
        print(f"worker {self.name}: " + (f"ready in {msg[1]} s" if msg[0] == "ready" else f"not available: {self.fatal}"), flush=True)

    def close(self, kill=False):
        """End the process: asked to stop (and given a moment), or killed (`kill`: one that hangs); the pipe is closed."""
        proc, conn = self.proc, self.conn
        self.proc = self.conn = None
        if proc is not None:
            try:
                if kill:
                    proc.terminate()
                else:
                    if conn is not None:
                        conn.send(("stop", {}))
                    proc.join(3)
                    if proc.is_alive():
                        proc.terminate()
            except (OSError, ValueError, AssertionError):  # a dead pipe, or a process that never started
                pass
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def threads(self):
        with LOCK:
            busy = sum(1 for n in CURRENT if n.startswith("heavy"))
        return bridge.THREADS if busy <= 1 else max(2, bridge.THREADS // max(1, WORKERS))

    def call(self, op, args, log, progress):
        if self.fatal and time.time() - self.fatal_at < FATAL_RETRY:   # not tried again at once: the failure costs seconds each time
            raise AppError(f"The worker process is not available: {self.fatal}")
        if self.proc is None or not self.proc.is_alive():
            self.close()
            self.start()
        if self.fatal:
            raise AppError(f"The worker process is not available: {self.fatal}")
        t0, failure = time.time(), None
        try:
            self.conn.send((op, {**args, "threads": self.threads()}))
            while True:
                if not self.conn.poll(POLL_SECONDS):       # a process that hangs (or dies without a word) does not hold the lane
                    if time.time() - t0 > JOB_TIMEOUT:
                        raise TimeoutError
                    if not self.proc.is_alive():
                        raise EOFError
                    continue
                msg = self.conn.recv()
                if msg[0] == "log":
                    log(msg[1])
                elif msg[0] == "progress":
                    progress(msg[1], msg[2])
                elif msg[0] == "done":
                    return msg[1]
                elif msg[0] == "error":                    # the process answered and waits for the next job: it goes on
                    kind, message, tb = msg[1:]
                    if kind == "AppError":
                        failure = AppError(message)
                    else:
                        if PUBLIC:
                            print(f"worker {self.name}: {kind}: {message}\n{tb}", flush=True)
                        else:
                            log(f"worker {self.name}:\n{tb}")
                        failure = RuntimeError(message)
                    break
        except TimeoutError:
            self.close(kill=True)
            limit = f"{JOB_TIMEOUT // 60} minutes" if JOB_TIMEOUT >= 60 else f"{JOB_TIMEOUT} seconds"
            raise AppError(f"The extraction took longer than {limit} and was stopped. Try a smaller area or a lower resolution.") from None
        except (EOFError, OSError, BrokenPipeError) as ex:
            code = self.proc.exitcode if self.proc else None
            self.close(kill=True)
            raise AppError(f"The worker process stopped while this sheet was processed (exit code {code}); try again.") from ex
        except Exception:
            self.close(kill=True)                          # a message the server could not handle, or a callback that failed: the
            raise                                          # process is replaced, so its remaining messages never reach the next job
        raise failure

    def extract(self, d, job, confirm, analysis, log, progress):
        return self.call("extract", {"job_dir": str(d), "file": job["file"], "confirm": confirm, "analysis": analysis}, log, progress)

    def prefetch(self, d, job, analysis, confirm, keys, log):
        return self.call("prefetch", {"job_dir": str(d), "file": job["file"], "confirm": confirm, "analysis": analysis, "keys": keys}, log, lambda k, st: None)


def pump(name):
    """A heavy worker thread that owns a worker process."""
    remote = Remote(name)
    try:
        worker("heavy", name, remote)
    finally:
        remote.close()


def run_job(job, busy, fail, progress, work, finish, kind=None):
    """The scaffold of the runners: mark the job busy with its first progress, run `work()` and hand its result to
    `finish(result)`, which changes the record under the lock together with the final commit. An AppError becomes the
    job's error as it is; any other exception also leaves its traceback in the job log. The duration of a `kind` of
    work feeds the wait estimates."""
    commit(job, status=busy, error=None, progress=progress)
    t0 = time.time()
    try:
        result = work()
        if kind:
            took(kind, time.time() - t0)
    except Exception as ex:                                # noqa: BLE001 - reported on the job, the worker goes on
        known = isinstance(ex, AppError)
        message = str(ex) if known else f"{type(ex).__name__}: {ex}"
        log(job, f"error: {message}" + ("" if known or PUBLIC else f"\n{traceback.format_exc()}"))
        commit(job, status=fail, error=message, progress=None)
        if PUBLIC and not known:                           # the job log goes to the visitor: the console keeps the traceback
            print(f"[{job['id']}] {busy} failed\n{traceback.format_exc()}", flush=True)
        return False
    with LOCK:
        finish(result)
        commit(job, progress=None)
    return True


def run_analysis(job, payload):
    """The analysis of an upload: the layout from the ink (bridge.analyse), then the scale cues of the floor plans inside
    their detected area (bridge.scale_step), so the scale step only shows them; it reads them again where the user
    changes an area. The session is kept for the later steps."""
    d = job_dir(job["id"])
    upload = d / job["file"]
    say = functools.partial(log, job)
    t0 = time.time()

    def work():
        analysis, session = bridge.analyse(d, upload, say)
        session["upload"] = upload
        remember_session(job["id"], session)
        if not PUBLIC or WORKERS:                          # a worker process reads it from the job folder
            saved = bridge.save_session(d, session)
            if saved is not True:
                say(f"note: the analysed session could not be saved ({saved}); after a server restart the layout runs again")
        confirm = default_confirm(analysis)
        keys = [k for k, c in confirm.items() if c["extract"]]
        done = []
        with LOCK:
            go_on = keys and not job.get("cancel")
            if go_on:
                job["progress"] = {"key": keys[0], "stage": "1c scale cues"}
        if go_on:
            try:
                done = bridge.scale_step(d, session, analysis, confirm, keys, say)
            except Exception as ex:                        # noqa: BLE001 - the layout stands; the scale step asks again
                say(f"scale cues not read with the analysis ({type(ex).__name__}: {ex}); the scale step reads them")
        return analysis, confirm, done

    def finish(result):
        analysis, confirm, done = result
        job.update(analysis=analysis, status="ready")
        job["times"]["analysis"] = round(time.time() - t0, 1)
        for k, c in confirm.items():                       # anything confirmed meanwhile stays on top of the defaults
            job["confirm"][k] = {**c, **job["confirm"].get(k, {})}
        apply_scale(job, analysis, done, {k: None for k in done})   # read inside the detected areas

    if run_job(job, "analysing", "error", {"key": None, "stage": "0a normalisation, 1b layout"}, work, finish, "analyse"):
        prefetch(job)


def apply_scale(job, analysis, done, areas):
    """The scale records of the drawings in `done` into the confirmations: the proposal, no longer pending, and the area
    the cues were read in (`areas`: key -> the confirmed polygon at that time, None for the detected one)."""
    for key, _, dr in iter_drawings(analysis):
        if key in done:
            c = job["confirm"].setdefault(key, {})
            c.update(proposal_px_per_m=(dr.get("scale") or {}).get("px_per_m"), scale_pending=False, scale_area=areas.get(key))
            if dr.get("storey") and not c.get("storey"):
                c["storey"] = dr["storey"]


def default_confirm(analysis):
    """What the pipeline proposes, as the starting point of the confirmations."""
    return {key: {"extract": dr["kind"] == bridge.FLOOR_PLAN, "polygon_px": None, "px_per_m": None, "scale_source": None,
                  "measured": None, "storey": dr.get("storey"), "proposal_px_per_m": (dr.get("scale") or {}).get("px_per_m"),
                  "area_confirmed": False, "scale_confirmed": False, "scale_pending": True}
            for key, _, dr in iter_drawings(analysis)}


def _session(job, d):
    """The analysed session of the job: in memory, from its pickle (local runs), or analysed again. The job's analysis
    record is kept as it is in that last case: it carries the scale records of the later steps, which a fresh layout
    pass does not."""
    session = SESSIONS.get(job["id"])
    if session is None:
        session = bridge.load_session(d)
        if session is not None:
            log(job, "analysed session loaded from disk")
    if session is None:
        log(job, "the analysed session is not in memory: the layout runs again first")
        _, session = bridge.analyse(d, d / job["file"], functools.partial(log, job))
        if not PUBLIC or WORKERS:
            bridge.save_session(d, session)
    session["upload"] = d / job["file"]
    with LOCK:
        if job["id"] in JOBS:                              # a sheet deleted while its read-ahead ran is not kept in memory
            remember_session(job["id"], session)
    return session


def run_scale(job, payload):
    """The scale cues of the drawings in payload["keys"] read again (their area changed, or they were not selected at
    the analysis), or the door-width search when payload["doors"]."""
    d = job_dir(job["id"])
    keys, doors = list(payload.get("keys") or []), bool(payload.get("doors"))
    t0 = time.time()

    def work():
        with job_lock(job["id"]):                          # not while a read-ahead runs on the same session
            session = _session(job, d)
            with LOCK:                                     # the worker edits copies; the live record is swapped at the end
                analysis, confirm = copy.deepcopy(job["analysis"]), copy.deepcopy(job["confirm"])
            done = bridge.scale_step(d, session, analysis, confirm, keys, functools.partial(log, job), doors=doors)
        return analysis, done, {k: (confirm.get(k) or {}).get("polygon_px") for k in done}

    def finish(result):
        analysis, done, areas = result
        job.update(analysis=analysis, status="ready")
        apply_scale(job, analysis, done, areas)
        job["times"]["scale"] = round(job["times"].get("scale", 0) + time.time() - t0, 1)

    stage = "1c door search (segmenter)" if doors else "1c scale cues"
    if run_job(job, "scaling", "ready", {"key": keys[0] if keys else None, "stage": stage, "doors": doors}, work, finish, "scale"):
        prefetch(job)


def run_extraction(job, payload):
    """Stages 2-10 for the confirmed drawings (bridge.extract), the confirmations as they are at this moment."""
    d = job_dir(job["id"])
    t0 = time.time()
    with LOCK:
        job["results"] = None
        drop_payloads(job["id"])

    ahead = bool((job.get("progress") or {}).get("ahead"))   # set when the run was queued (post_run)

    def progress(key, stage):
        with LOCK:
            job["progress"] = {"key": key, "stage": stage, "ahead": ahead, "since": time.time()}
        log(job, f"  [{key}] {stage}")

    def work():
        with LOCK:                                         # a confirm posted meanwhile must not reach into the run
            confirm, analysis = copy.deepcopy(job["confirm"]), copy.deepcopy(job["analysis"])
        return EXEC.bridge.extract(d, job, confirm, analysis, functools.partial(log, job), progress)

    def finish(results):
        failed = [k for k, r in results.items() if r.get("error")]
        done = [k for k, r in results.items() if r.get("outputs")]
        job.update(results=results, status="done" if done or not failed else "error",
                   error="; ".join(f"{k}: {results[k]['error']}" for k in failed) or None)
        job["times"]["extraction"] = round(time.time() - t0, 1)
        log(job, f"extraction finished in {job['times']['extraction']} s")
        if PUBLIC:                                         # the pickle of the rasters is not kept once the sheet is extracted
            (d / "session.pkl").unlink(missing_ok=True)

    run_job(job, "running", "error", {"key": None, "stage": "starting", "ahead": ahead, "since": time.time()}, work, finish, "extract")


def prefetch(job):
    """Queue the text inside the selected drawings to be read ahead of the extraction, behind any work a user waits
    for: the worker is idle while the user confirms the area and the scale, and that OCR is the longest stage of the
    extraction (half of it). Drawings with a scale proposal whose cues were read in the current area."""
    with LOCK:
        keys = [k for k, c in job["confirm"].items()
                if c.get("extract") and c.get("proposal_px_per_m") and c.get("scale_pending") is False]
    if keys:
        put_work("prefetch", job["id"], {"keys": keys}, low=True)


def run_prefetch(job, payload):
    """bridge.prefetch_text for the drawings in payload["keys"]: with the proposed scale and the confirmed (else the
    detected) area, into the OCR cache, where the extraction finds it when the user confirms the proposal (or a
    scale within a few per cent of it) and keeps the area. Invisible to the job's status; a failure only means the
    extraction reads the text itself."""
    keys = list(payload.get("keys") or [])
    d = job_dir(job["id"])
    t0 = time.time()
    try:
        with LOCK:
            analysis, confirm = copy.deepcopy(job["analysis"]), copy.deepcopy(job["confirm"])
        done = EXEC.bridge.prefetch(d, job, analysis, confirm, keys, functools.partial(log, job))
        took("prefetch", time.time() - t0)
        with LOCK:                                         # what was read ahead, for text_ahead() when the run is queued
            job["_ahead"] = {k: {"polygon_px": confirm[k].get("polygon_px"), "px_per_m": confirm[k].get("proposal_px_per_m")} for k in done}
        log(job, f"text inside {', '.join(done)} read ahead in {time.time() - t0:.1f} s" if done else "nothing to read ahead")
    except Exception as ex:                                # noqa: BLE001 - the extraction reads the text itself
        log(job, f"reading the text ahead failed ({type(ex).__name__}: {ex}); the extraction reads it")


def text_ahead(job):
    """Whether the extraction will find the text of every selected drawing in the cache: read ahead in the same
    area, at a scale within the cache's resolution step (a quarter octave, bridge/pipeline OCR_RES_STEPS) of the
    proposal. The page shows a shorter wait then."""
    ahead = job.get("_ahead") or {}
    for key, c in job["confirm"].items():
        if not c.get("extract"):
            continue
        a = ahead.get(key)
        p = c.get("px_per_m") or c.get("proposal_px_per_m")
        if not a or not a.get("px_per_m") or not p or (c.get("polygon_px") or None) != (a.get("polygon_px") or None):
            return False
        if abs(math.log2(p / a["px_per_m"])) > 0.125:
            return False
    return True


RUNNERS = {"analyse": run_analysis, "scale": run_scale, "extract": run_extraction, "prefetch": run_prefetch}


# ---------------------------------------------------------------------------------------------------------------------
# Results for the viewer

def results_payload(job, key):
    """One drawing's results for the viewer: the output JSON reduced to what the screen draws. Cached per file time,
    since the JSON of a large sheet runs to megabytes and the screen asks for it on every render."""
    r = (job.get("results") or {}).get(key)
    if not r:
        raise NotFound(f"no results for {key}")
    if not r.get("outputs"):
        return {"key": key, "error": r.get("error"), "skipped": r.get("skipped"), "record": r}
    out = job_dir(job["id"]) / "out"
    src, rp = out / r["outputs"]["json"], job_dir(job["id"]) / "review.json"
    stamp = (src.stat().st_mtime_ns, rp.stat().st_mtime_ns if rp.exists() else 0)
    with LOCK:
        hit = PAYLOADS.get((job["id"], key))
    if hit and hit[0] == stamp:
        return hit[1]
    payload = _results_payload(key, r, read_json(src), (read_json(rp, {}) or {}).get(key, {}))
    with LOCK:
        PAYLOADS[(job["id"], key)] = (stamp, payload)
        while len(PAYLOADS) > PAYLOAD_KEEP:
            PAYLOADS.pop(next(iter(PAYLOADS)), None)
    return payload


def _results_payload(key, r, data, review):
    pick = lambda d, keys: {k: d.get(k) for k in keys}      # noqa: E731
    rooms = [pick(room, ("id", "name", "usage", "number", "aoid", "area", "area_net", "area_net_simple", "area_gross", "gross_basis",
                         "area_stamp", "area_deviation_pct", "area_basis", "confidence", "reasons", "face_flags", "small_region",
                         "review", "neighbours", "geometry", "geometry_simple", "geometry_gross", "name_raw")) for room in data["rooms"]]
    return {"key": key, "sheet_id": r["sheet_id"], "title": data["sheet"]["title"], "storey": r.get("storey"),
            "px_per_m": r["px_per_m"], "work_px": r["work_px"], "extent": data["sheet"]["extent"],
            "files": r["outputs"], "scale": data["sheet"]["scale"], "triage": data["sheet"].get("triage"),
            "times": r["times"], "summary": r.get("summary"), "rooms": rooms,
            "voids": [pick(v, ("id", "kind", "label", "area", "gf_deducted", "geometry")) for v in data["voids"]],
            "stairs": data["stairs"], "walls": data["wall_polygons"], "walls_raw": data.get("wall_polygons_raw", []),
            "wall_segments": [pick(w, ("id", "centre_line", "thickness", "thickness_measured", "length", "kind", "rooms", "construction", "notes", "openings")) for w in data.get("walls", [])],
            "columns": [c["geometry"] for c in data.get("structure", [])],
            "openings": [pick(o, ("id", "kind", "width", "exterior", "host", "connects", "flag", "geometry")) for o in data["openings"]],
            "floor": pick(data["floor"], ("gf", "gf_area", "agf_area", "sum_net", "sum_net_simple", "sum_gross", "facade_half_wall")),
            "connectivity": data["connectivity"], "qa": data["qa"],
            "stamps": [pick(s, ("name", "area", "number", "box")) for s in data.get("stamps", [])],
            "drawing": (data.get("drawings") or [None])[0], "review": review,
            "exports": data.get("exports") or {}}             # Excel and IFC only when the pipeline could write them


def _cell(v):
    """A CSV cell that a spreadsheet will not run as a formula (room names come from OCR and from other people's plans).
    Numbers stay numbers: -2.4 is a deviation, not a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def rooms_csv(job, key):
    p = results_payload(job, key)
    if "rooms" not in p:
        raise NotFound(f"no rooms for {key}: the drawing failed or was skipped")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "name", "usage", "number", "area_net_m2", "area_gross_m2", "area_stamp_m2", "deviation_pct",
                "confidence", "checked", "reasons"])
    for r in p["rooms"]:
        rv = (p["review"].get("rooms") or {}).get(r["id"], {})
        w.writerow([_cell(v) for v in (r["id"], r["name"], r["usage"], r["number"], r["area_net"], r["area_gross"],
                                       r["area_stamp"], r["area_deviation_pct"], r["confidence"],
                                       "yes" if rv.get("checked") else "", "; ".join(r.get("reasons") or []))])
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------------------------
# Checks of what the page sends

CONFIRM_FIELDS = {"extract": "bool", "area_confirmed": "bool", "scale_confirmed": "bool", "polygon_px": "polygon",
                  "px_per_m": "positive", "dpi": "positive", "scale_source": "text", "storey": "text", "measured": "measured"}


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) < 1e7


def _point(v):
    return isinstance(v, list) and len(v) == 2 and all(map(_number, v))


def _valid(kind, v):
    if v is None:
        return True
    return {"bool": lambda: isinstance(v, bool),
            "positive": lambda: _number(v) and v > 0,
            "text": lambda: isinstance(v, str) and len(v) <= 200,
            "polygon": lambda: isinstance(v, list) and 3 <= len(v) <= MAX_POLYGON and all(map(_point, v)),
            "measured": lambda: isinstance(v, dict) and set(v) <= {"px", "distance", "unit", "p1", "p2"}
            and all(_number(v[k]) for k in ("px", "distance") if k in v) and all(_point(v[k]) for k in ("p1", "p2") if k in v)
            and (not isinstance(v.get("unit"), str) or len(v["unit"]) <= 10)}[kind]()


def check_scale(drawing, c):
    """A confirmed scale that makes the drawing implausibly large is refused: the pipeline scales the drawing's area to
    its working (50 px/m) and OCR (150 px/m) resolutions, which for a distance typed ten times too long asks for tens
    of GB and would stop the server for everyone."""
    p, poly = c.get("px_per_m"), c.get("polygon_px") or drawing.get("polygon_px")
    if not p or not poly:
        return
    xs, ys = [pt[0] for pt in poly], [pt[1] for pt in poly]
    w, h = (max(xs) - min(xs)) / p, (max(ys) - min(ys)) / p
    if max(w, h) > MAX_EXTENT_M or w * h > MAX_AREA_M2:
        raise AppError(f"At this scale the drawing would be {w:.0f} x {h:.0f} m, not plausible for a floor plan. Check the "
                       f"measured distance and its unit.", "implausible_scale")
    if w * h * 2500 > MAX_WORK_MPX * 1e6:                  # the pipeline works at 50 px/m: 2500 px per m²
        raise AppError(f"At this scale the drawing ({w:.0f} x {h:.0f} m) would need a working image of {w * h * 2500 / 1e6:.0f} megapixels, "
                       f"more than this server processes ({MAX_WORK_MPX}). Confirm a smaller area or check the scale.", "implausible_scale")


def check_confirm(job, confirm):
    """The confirmations of a POST /confirm, checked: only drawings of this sheet, only the fields the page may set,
    each of its type, and a plausible scale (check_scale)."""
    if not isinstance(confirm, dict) or not confirm:
        raise AppError("confirm must map '<package>/<drawing>' keys to objects.", "bad_request")
    drawings = {k: dr for k, _, dr in iter_drawings(job["analysis"])}
    for key, val in confirm.items():
        if key not in drawings or not isinstance(val, dict):
            raise AppError(f"No drawing {key!r} on this sheet, or its value is not an object.", "bad_request")
        for field, v in val.items():
            if field not in CONFIRM_FIELDS:
                raise AppError(f"{field!r} cannot be confirmed (fields: {', '.join(CONFIRM_FIELDS)}).", "bad_request")
            if not _valid(CONFIRM_FIELDS[field], v):
                raise AppError(f"Invalid value for {field!r} of {key}.", "bad_request")
        check_scale(drawings[key], {**job["confirm"].get(key, {}), **val})
    return confirm


def check_review(job, data):
    key, rooms = data.get("key"), data.get("rooms")
    keys = {k for k, _, _ in iter_drawings(job.get("analysis"))}
    if not isinstance(key, str) or key not in keys:
        raise AppError("review needs the key of a drawing of this sheet.", "bad_request")
    if not isinstance(rooms, dict) or len(rooms) > 10000 or not all(
            isinstance(r, str) and len(r) <= 40 and isinstance(v, dict) and set(v) <= {"checked"}
            and isinstance(v.get("checked", False), bool) for r, v in rooms.items()):
        raise AppError("review needs {key, rooms: {<room id>: {checked: bool}}}.", "bad_request")
    return key, rooms


def health(refresh=False, full=True):
    """The pipeline check. The public server tells its visitors only what is there, not where (paths) or which
    version; the full check goes to requests on the server itself (setup.sh)."""
    if not HEALTH or refresh:                              # the check imports the libraries once
        HEALTH.clear()
        HEALTH.update(bridge.check())
    if not full:
        return {"ok": HEALTH["ok"], "public": True, "keep_minutes": KEEP_MINUTES,
                "items": {k: {"ok": v["ok"]} for k, v in HEALTH["items"].items()}}
    return {**HEALTH, "public": PUBLIC, "keep_minutes": KEEP_MINUTES if PUBLIC else None}


def rss_mb():
    try:
        with open("/proc/self/statm") as f:                # Linux
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6)
    except (OSError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------------------------------------------------
# Routes: ROUTES holds (method, pattern, handler, takes a sheet); a sheet route's handler gets the job, 404 if unknown

ROUTES = []
SHEET = r"/api/sheets/(?P<jid>[\w\-]+)"


def route(method, pattern, sheet=False):
    def register(fn):
        ROUTES.append((method, re.compile(pattern), fn, sheet))
        return fn
    return register


def arg(q, name, default=""):
    return (q.get(name) or [default])[0]


def created(job):
    return {"id": job["id"], "name": job["name"], "status": job["status"]}


@route("GET", r"/api/health")
def get_health(h, q):
    full = not PUBLIC or h.local()
    h.send_json(health(refresh="refresh" in q and full, full=full))


@route("GET", r"/api/status")
def get_status(h, q):
    with LOCK:
        busy, jobs = sum(j["status"] in BUSY for j in JOBS.values()), len(JOBS)
        working = [{"worker": n, "kind": c["kind"], "seconds": round(time.time() - c["since"]), "stuck": time.time() - c["since"] > JOB_TIMEOUT} for n, c in CURRENT.items()]
        unavailable = {n: r.fatal for n, r in REMOTES.items() if r.fatal}
        queued = {lane: len(v) for lane, v in QUEUED.items()}
    h.send_json({"queue": sum(queued.values()), "queued": queued, "busy": busy, "jobs": jobs,
                 "worker_alive": bool(WORKER_THREADS) and all(t.is_alive() for t in WORKER_THREADS),
                 "workers": {"light": 1, "heavy": lane_size("heavy"), "processes": bool(WORKERS), "unavailable": unavailable}, "working": working or None,
                 "free_disk_gb": round(shutil.disk_usage(SHEETS).free / 1e9, 1), "rss_mb": rss_mb()})


@route("GET", r"/api/demo")
def get_demo(h, q):
    h.send_json({"demo": demo_info()})


@route("POST", r"/api/demo")
def post_demo(h, q):
    info = demo_info()
    if info is None:
        raise AppError("No demo sheet is bundled with this server (app/demo/).", "no_demo")
    src = DEMO / info["file"]
    h.send_json(created(new_job(info["file"], src.stat().st_size, lambda p: shutil.copyfile(src, p))))


@route("POST", r"/api/upload")
def post_upload(h, q):
    name = arg(q, "name", h.headers.get("X-File-Name") or "sheet")
    size = h.content_length()
    if not size:
        raise AppError("The upload is empty.", "empty")
    if size > MAX_UPLOAD:
        raise AppError(f"The upload is larger than {MAX_UPLOAD // 1024 // 1024} MB.", "too_large")
    h.send_json(created(new_job(name, size, lambda p: h.stream_body(p, size))))


@route("GET", r"/api/sheets")
def get_sheets(h, q):
    if PUBLIC:                                             # every visitor knows only the ids of their own sheets
        raise NotFound("The sheet list is not available on the public server.")
    with LOCK:
        rows = [{"id": j["id"], "name": j["name"], "status": j["status"], "created": j["created"],
                 "drawings": sum(1 for _ in iter_drawings(j.get("analysis")))}
                for j in sorted(JOBS.values(), key=lambda j: j["id"], reverse=True) if not j.get("cancel")]
    h.send_json({"sheets": rows})


@route("GET", SHEET, sheet=True)
def get_sheet(h, q, job):
    rev = arg(q, "rev", None)
    with LOCK:                                             # serialised under the lock: the worker changes the record meanwhile
        q_info = queue_info(job)                           # where a waiting job stands: with every poll, the wait shrinks
        if rev and rev.isascii() and rev.isdigit() and len(rev) < 12 and int(rev) == job.get("rev", 0):
            body = {"id": job["id"], "rev": job["rev"], "unchanged": True, **({"queue": q_info} if q_info else {})}
        else:
            body = {**stored(job), "log": job["log"][-LOG_TAIL:]}
            if q_info and body.get("progress"):
                body["progress"] = {**body["progress"], **q_info}
        text = json.dumps(body, ensure_ascii=False, default=jsonable)
    h.send_raw_json(text)


@route("DELETE", SHEET, sheet=True)
def delete_sheet(h, q, job):
    with LOCK:
        busy = in_flight(job["id"])                        # also a read-ahead, which the status does not show; not a job stuck busy
        if busy:                                           # the worker cannot be interrupted: it discards the job when done
            commit(job, cancel=True)
        else:
            drop(job["id"])
    if busy:
        log(job, "removed while busy: discarded as soon as the worker is done with it")
        return h.send_json({"deleted": job["id"], "pending": True})
    remove_tree(job_dir(job["id"]))
    h.send_json({"deleted": job["id"]})


@route("POST", SHEET + r"/confirm", sheet=True)
def post_confirm(h, q, job):
    data = h.read_json()
    with LOCK:
        if not job.get("analysis"):
            raise AppError("The sheet is not analysed yet.", "not_analysed")
        for key, val in check_confirm(job, data.get("confirm")).items():
            job["confirm"].setdefault(key, {}).update(val)
        commit(job)
        text = json.dumps({"confirm": job["confirm"]}, ensure_ascii=False, default=jsonable)
    h.send_raw_json(text)


@route("POST", SHEET + r"/scale", sheet=True)
def post_scale(h, q, job):
    data = h.read_json()
    if not job.get("analysis"):
        raise AppError("The sheet is not analysed yet.", "not_analysed")
    keys, doors = data.get("keys"), bool(data.get("doors"))
    known = {k for k, _, _ in iter_drawings(job["analysis"])}
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k in known for k in keys):
        raise AppError("scale needs the keys of drawings of this sheet.", "bad_request")
    keys = list(dict.fromkeys(keys))

    def mark(j):                                           # the scale step waits for these instead of showing the old record
        for k in keys:
            j["confirm"].setdefault(k, {})["scale_pending"] = True

    enqueue(job, "scale", "scaling", {"key": keys[0], "stage": "waiting for the worker", "doors": doors},
            {"keys": keys, "doors": doors}, mark)
    h.send_json({"status": "scaling", "keys": keys})


@route("POST", SHEET + r"/run", sheet=True)
def post_run(h, q, job):
    if not job.get("analysis"):
        raise AppError("The sheet is not analysed yet.", "not_analysed")
    if not any(c.get("extract") for c in job["confirm"].values()):
        raise AppError("No drawing is selected for extraction.", "nothing_selected")
    with LOCK:                                             # a worker may be reading this sheet's text ahead right now
        reading = next((c for c in CURRENT.values() if c["kind"] == "prefetch" and c["jid"] == job["id"]), None)
        ahead = bool(reading) or text_ahead(job)
    enqueue(job, "extract", "queued", {"key": None, "stage": "reading the text ahead" if reading else "waiting for the worker",
                                       "ahead": ahead, "since": reading["since"] if reading else time.time()})
    for stale in (job_dir(job["id"]) / "out").glob("*_live.json"):   # the page would draw the previous run while this one waits
        stale.unlink(missing_ok=True)
    h.send_json({"status": "queued", "text_ahead": ahead})


@route("GET", SHEET + r"/results", sheet=True)
def get_results(h, q, job):
    h.send_json(results_payload(job, arg(q, "key")))


@route("GET", SHEET + r"/rooms\.csv", sheet=True)
def get_rooms_csv(h, q, job):
    key = arg(q, "key")
    h.send_text(rooms_csv(job, key), MIME[".csv"], f"{key.replace('/', '-')}_rooms.csv")


@route("GET", SHEET + r"/file/(?P<name>[^/]+)", sheet=True)
def get_file(h, q, job, name):
    """The outputs (out/) and the sheet previews; the job's own files (the upload, job.json, the log, the pickle)
    are not served."""
    name = Path(unquote(name)).name
    d = job_dir(job["id"])
    path = d / "out" / name
    if not path.is_file():
        path = d / name
        if not (name.endswith("_preview.jpg") and path.is_file()):
            raise NotFound(f"{name} not found")
    h.send_file(path, download=name if name.endswith(DOWNLOADS) and "download" in q else None)


@route("GET", SHEET + r"/live", sheet=True)
def get_live(h, q, job):
    key = arg(q, "key")
    if not re.fullmatch(r"[\w\-][\w\-.]*/[\w\-]+", key):
        raise AppError("live needs key=<package>/<drawing>.", "bad_request")
    p = job_dir(job["id"]) / "out" / bridge.live_name(key)
    if not p.is_file():
        return h.send_json({"stage": None})
    h.send_file(p)                                         # with its ETag: an unchanged record costs a 304


@route("GET", SHEET + r"/log", sheet=True)
def get_log(h, q, job):
    p = job_dir(job["id"]) / "log.txt"
    h.send_text(p.read_text(encoding="utf-8") if p.exists() else "\n".join(job["log"]), MIME[".txt"])


@route("POST", SHEET + r"/review", sheet=True)
def post_review(h, q, job):
    key, rooms = check_review(job, h.read_json())
    p = job_dir(job["id"]) / "review.json"
    with LOCK:                                             # two ticks in a row must not lose each other
        review = read_json(p, {})
        review = review if isinstance(review, dict) else {}
        entry = review.setdefault(key, {"rooms": {}})
        for rid, val in rooms.items():
            entry["rooms"].setdefault(rid, {}).update(val)
        entry["updated"] = now()
        dump(review, p)
        text = json.dumps({"review": entry}, ensure_ascii=False)
    h.send_raw_json(text)


# ---------------------------------------------------------------------------------------------------------------------
# HTTP

def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON allows")


class Handler(BaseHTTPRequestHandler):
    server_version = "fpx-workflow/0.1"
    timeout = 60                                           # a client that stops sending is dropped

    def log_message(self, fmt, *args):                     # quiet: the job log is what matters
        line = str(args[0]) if args else ""                # log_error passes the status code first, as an int
        status = str(args[1]) if len(args) > 1 else ""
        if "/api/" not in line or status in ("200", "204", "304"):
            return
        if PUBLIC:                                         # only the route: paths and queries carry upload names
            line = re.sub(r"\?\S*", "", line)
            line = re.sub(r"(/api/sheets/)[\w\-]+(/file/)\S+", r"\1<id>\2<name>", line)
            args = (re.sub(r"(/api/sheets/)[\w\-]+", r"\1<id>", line),) + args[1:]
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    # --- answers
    def send_response(self, code, message=None):
        self._sent = True
        super().send_response(code, message)

    def _send(self, status, body, ctype, headers=None, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_raw_json(self, text, status=200, headers=None):
        self._send(status, text.encode("utf-8"), MIME[".json"], headers)

    def send_json(self, obj, status=200, headers=None):
        self.send_raw_json(json.dumps(obj, ensure_ascii=False, default=jsonable), status, headers)

    def send_text(self, text, ctype, download=None):
        self._send(200, text.encode("utf-8"), ctype, attachment(download))

    def fail(self, status, message, code=None):
        if getattr(self, "_sent", False):                  # the answer had begun: a second one would corrupt the stream
            self.close_connection = True
            return
        self.send_json({"error": message, "code": code}, status)

    def send_unauthorized(self):
        """The batch API (/api/v1, a preview): every request needs an API token. Tokens come with the login, which is
        not there yet, so every request is refused, with a token or without (RFC 6750)."""
        given = (self.headers.get("Authorization") or "").startswith("Bearer ")
        challenge = 'Bearer realm="plan-extract"' + (', error="invalid_token"' if given else "")
        message = ("This token is not valid: API tokens are not issued yet." if given else
                   "The batch API needs an API token (Authorization: Bearer <token>). Tokens come with the login, "
                   "which is not available yet.")
        self.send_json({"error": "invalid_token" if given else "token_required", "message": message, "docs": "/api/docs"},
                       401, {"WWW-Authenticate": challenge})

    def send_file(self, path, download=None):
        """A file with an ETag from its size and time: the browser revalidates and gets a 304 while it is unchanged.
        The file is opened first and measured through the open handle, so a file replaced meanwhile (the live record)
        is sent whole and consistent. A Range request gets the slice as 206 (a video seeks only on a server that
        answers them)."""
        try:
            f = open(path, "rb")
        except OSError:
            raise NotFound(f"{Path(path).name} not found") from None
        with f:
            st = os.fstat(f.fileno())
            etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
            asked = [t.strip().removeprefix("W/") for t in (self.headers.get("If-None-Match") or "").split(",")]
            if etag in asked or "*" in asked:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            start, end = 0, st.st_size - 1                 # a byte range (a video seeks with them): 206 and the slice
            m = re.fullmatch(r"bytes=([0-9]{0,15})-([0-9]{0,15})", (self.headers.get("Range") or "").strip())
            partial = bool(m and (m.group(1) or m.group(2)) and st.st_size)
            if partial:
                if m.group(1):
                    start, end = int(m.group(1)), min(int(m.group(2)), end) if m.group(2) else end
                else:
                    start = max(0, st.st_size - int(m.group(2)))
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{st.st_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", MIME.get(Path(path).suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{st.st_size}")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            for k, v in attachment(download).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    piece = f.read(min(CHUNK, left))
                    if not piece:
                        break
                    self.wfile.write(piece)
                    left -= len(piece)

    def send_asset(self, top, path):
        folder = (APP / top).resolve()
        target = (folder / path[len(top) + 2:]).resolve()  # js/..., css/...; nothing outside those folders
        if not target.is_relative_to(folder) or not target.is_file():
            raise NotFound("not found")
        self.send_file(target)

    # --- what the client sends
    def content_length(self):
        n = self.headers.get("Content-Length") or "0"
        if not (n.isascii() and n.isdigit() and len(n) <= 15):
            raise AppError("Malformed Content-Length.", "bad_request")
        return int(n)

    def read_body(self, limit):
        n = self.content_length()
        if n > limit:
            raise AppError(f"The request is larger than {limit // 1024} kB.", "too_large")   # drained in do_GET
        self._body_read = True
        return self.rfile.read(n) if n else b""

    def stream_body(self, path, size):
        """The upload into its file, in pieces: a 300 MB upload never sits in memory."""
        self._body_read = True
        with open(path, "wb") as f:
            left = size
            while left:
                chunk = self.rfile.read(min(CHUNK, left))
                if not chunk:
                    raise AppError("The upload was cut off.", "bad_request")
                f.write(chunk)
                left -= len(chunk)

    def read_json(self):
        body = self.read_body(MAX_JSON)
        try:
            data = json.loads(body.decode("utf-8"), parse_constant=_no_constant) if body else {}
        except (ValueError, UnicodeDecodeError) as ex:
            raise AppError(f"Malformed JSON body: {ex}", "bad_request") from ex
        if not isinstance(data, dict):
            raise AppError("The JSON body must be an object.", "bad_request")
        return data

    def drain(self):
        """Read what the client sent and no handler read (a refused request): closing a socket with unread data resets
        the connection on some systems, and the client then loses the answer."""
        if getattr(self, "_body_read", True):
            return
        self._body_read = True
        try:
            left = min(self.content_length(), MAX_UPLOAD)
        except AppError:
            return
        while left > 0:
            chunk = self.rfile.read(min(CHUNK, left))
            if not chunk:
                return
            left -= len(chunk)

    def cross_site(self):
        """A state-changing request sent by another site's page (CSRF): the browser marks it with Sec-Fetch-Site, or
        with an Origin whose scheme, host or port is not this server's (behind nginx: X-Forwarded-Proto, the default
        port of the scheme). Requests with neither header (scripts, curl, the tests) pass."""
        site = self.headers.get("Sec-Fetch-Site")
        if site:
            return site not in ("same-origin", "none")
        origin = self.headers.get("Origin")
        if not origin:
            return False
        try:
            o, here = urlparse(origin), urlparse("//" + (self.headers.get("Host") or ""))
            scheme = self.headers.get("X-Forwarded-Proto") or "http"
            port = lambda u, s: u.port or (443 if s == "https" else 80)   # noqa: E731
            return not o.hostname or (o.scheme, o.hostname, port(o, o.scheme)) != (scheme, here.hostname, port(here, scheme))
        except ValueError:                                 # a malformed Origin or Host
            return True

    def local(self):
        """A request made on this machine, not through nginx (which always adds X-Forwarded-For)."""
        return not self.headers.get("X-Forwarded-For")

    def job(self, jid):
        """The job of a sheet route; its last request is now (the public server deletes it KEEP_MINUTES later and
        records the time on the folder, at most once a minute, so it survives a restart)."""
        job = JOBS.get(jid)
        if job is None or job.get("cancel"):               # removed, or removed while busy and not yet discarded
            raise NotFound(f"sheet {jid} not found")
        t = time.time()
        job["seen"] = t
        if PUBLIC and t - job.get("_touched", 0) > TOUCH_SECONDS:
            job["_touched"] = t
            try:
                os.utime(job_dir(jid))
            except OSError:
                pass
        return job

    # --- routing
    def do_GET(self):
        self._sent, self._body_read = False, False
        try:
            self.dispatch()
        except NotFound as ex:
            self.fail(404, str(ex), "not_found")
        except AppError as ex:
            self.fail(400, str(ex), ex.code)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception as ex:                            # noqa: BLE001
            if PUBLIC:
                print(f"500 on {self.command}: {type(ex).__name__}", flush=True)
            else:
                traceback.print_exc()
            try:
                self.fail(500, "Internal error." if PUBLIC else f"{type(ex).__name__}: {ex}", "internal")
            except (ConnectionError, TimeoutError):
                self.close_connection = True
        finally:
            try:
                self.drain()
            except (ConnectionError, TimeoutError, OSError):
                self.close_connection = True

    do_HEAD = do_POST = do_DELETE = do_GET

    def dispatch(self):
        if not self.path.startswith("/"):
            raise AppError("Malformed request target.", "bad_request")
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query, keep_blank_values=True)   # ?download and ?download=1 alike
        method = "GET" if self.command == "HEAD" else self.command
        if method != "GET" and self.cross_site():
            return self.fail(403, "Cross-site request refused.", "cross_site")
        if path.startswith("/api/v1/"):                    # the batch API: needs a token, and none are issued yet
            return self.send_unauthorized()
        if method == "GET":
            if path in ("/", "/index.html"):
                return self.send_file(APP / "index.html")
            if path in STATIC:
                return self.send_file(APP / STATIC[path])
            top = path.split("/")[1]
            if top in ASSETS:
                return self.send_asset(top, path)
        for m, pattern, handler, sheet in ROUTES:
            hit = pattern.fullmatch(path) if m == method else None
            if hit:
                args = hit.groupdict()
                if sheet:
                    return handler(self, q, self.job(args.pop("jid")), **args)
                return handler(self, q, **args)
        raise NotFound(f"no route for {self.command} {path}")


def attachment(name):
    """Content-Disposition of a download: an ASCII name for old clients and the real one (RFC 6266)."""
    if not name:
        return {}
    plain = safe_name(name)
    return {"Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"}


def start_workers():
    """The light lane on a thread of this process; the heavy lane on WORKERS threads that each drive a worker
    process, or on one thread of this process with 0. Returns the threads."""
    WORKER_THREADS[:] = [threading.Thread(target=worker, args=("light", "light"), daemon=True, name="light-worker")]
    if WORKERS:
        WORKER_THREADS.extend(threading.Thread(target=pump, args=(f"heavy-{i}",), daemon=True, name=f"heavy-{i}") for i in range(1, WORKERS + 1))
    else:
        WORKER_THREADS.append(threading.Thread(target=worker, args=("heavy", "heavy-1"), daemon=True, name="heavy-worker"))
    for t in WORKER_THREADS:
        t.start()
    return WORKER_THREADS


class Server(ThreadingHTTPServer):
    """A thread per request; a listen backlog for the burst of a page load by several browsers at once (the default
    of 5 refused connections when two visitors arrived together)."""
    daemon_threads = True
    request_queue_size = 128


def main():
    global PUBLIC, SHEETS, WORKERS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--warm", action="store_true", help="load the OCR engine and the segmenter at start-up, in the background, so the first sheet does not wait for them")
    ap.add_argument("--workers", type=int, default=WORKERS, help="worker processes for the extractions (FPX_APP_WORKERS; 0: a thread of this process)")
    ap.add_argument("--public", action="store_true", help="the public instance: no sheet list, sheets deleted FPX_APP_KEEP_MINUTES (60) after their last request, no pickles, OCR cache per sheet, no file names in the console, queue limits")
    a = ap.parse_args()
    PUBLIC, WORKERS = a.public, max(0, a.workers)
    bridge.JOB_CACHE = a.public
    if PUBLIC:                                             # a folder of its own: a local run's sheets are never touched
        SHEETS = DATA / "public"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    DATA.mkdir(parents=True, exist_ok=True)
    load_jobs()
    start_workers()
    if WORKERS:
        print(f"Heavy lane: {WORKERS} worker processes (app/worker.py); the analysis runs here.", flush=True)
    if PUBLIC:
        threading.Thread(target=sweeper, daemon=True, name="sweeper").start()
        print(f"Public server: sheets are deleted {KEEP_MINUTES} min after their last request.", flush=True)
    if a.warm:
        def warm():
            for name, fn in (("OCR engine", bridge.engine),) + (() if WORKERS else (("segmenter", bridge.model),)):   # the processes warm their own
                try:
                    t0 = time.time()
                    fn()
                    print(f"warm-up: {name} ready in {time.time() - t0:.1f} s", flush=True)
                except Exception as ex:                    # noqa: BLE001 - the health check will say the same
                    print(f"warm-up: {name} not available: {ex}", flush=True)
        threading.Thread(target=warm, daemon=True, name="warm-up").start()
    srv = Server(("127.0.0.1", a.port), Handler)
    print(f"Floor plan workflow app: http://127.0.0.1:{a.port}/   (pipeline: {bridge.PIPELINE_DIR}; data: {DATA})", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
