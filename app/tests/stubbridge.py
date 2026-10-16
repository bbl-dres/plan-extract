"""The stub bridge of conftest as a module file: what a worker process of the tests loads (FPX_APP_BRIDGE, see
app/worker.py). Its extraction writes conftest's RESULT when the job folder holds a file "ok", ends the process when
it holds "die" (a crashed worker), sleeps a minute when it holds "hang" (a stuck worker), waits while it holds "hold", and fails as the stub does otherwise; a file "session.pkl" is the saved session."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402

_b = conftest.make_bridge()
for _name in dir(_b):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_b, _name)


def hold(job_dir):
    """A file "hold" in the job folder keeps the work in the process until it is removed (at most 20 s)."""
    t0 = time.time()
    while (Path(job_dir) / "hold").exists() and time.time() - t0 < 20:
        time.sleep(0.05)


def extract(job_dir, session, confirm, log, progress, analysis=None):
    if (Path(job_dir) / "die").exists():
        os._exit(3)
    if (Path(job_dir) / "hang").exists():
        time.sleep(60)
    hold(job_dir)
    _b.EXTRACT_OK = (Path(job_dir) / "ok").exists()
    return _b.extract(job_dir, session, confirm, log, progress, analysis)


def prefetch_text(job_dir, session, analysis, confirm, keys, log):
    hold(job_dir)
    return _b.prefetch_text(job_dir, session, analysis, confirm, keys, log)


def load_session(job_dir):
    """A file "session.pkl" in the job folder stands for the saved session (the server's stub writes it in the test)."""
    return {"packages": [], "from_disk": True} if (Path(job_dir) / "session.pkl").exists() else None


def save_session(job_dir, session):
    (Path(job_dir) / "session.pkl").write_text("stub")
    return True
