"""The two lanes and the worker processes: a newcomer's sheet is analysed while the heavy lane is busy, a waiting job
is told its position and wait, an extraction runs in a worker process and a process that dies is replaced."""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import wait_until
from test_server import call, wait_status

PNG = {"Content-Type": "application/octet-stream"}
HERE = Path(__file__).resolve().parent


def upload(base):
    return call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", PNG)[1]["id"]


def test_the_light_lane_runs_while_the_heavy_lane_is_busy(served):
    base, server, _ = served
    server.bridge.PREFETCH_GATE.clear()                    # the read-ahead of the first sheet holds the heavy worker
    a = upload(base)
    wait_status(base, a, ("ready",))
    assert wait_until(lambda: [c["kind"] for c in server.CURRENT.values()] == ["prefetch"])
    b = upload(base)
    assert wait_status(base, b, ("ready", "error"))["status"] == "ready"   # analysed on the light lane meanwhile
    assert [c["kind"] for c in server.CURRENT.values()] == ["prefetch"]
    server.bridge.PREFETCH_GATE.set()
    server.wait_idle()


def test_a_waiting_job_is_told_its_position_and_wait(served):
    base, server, _ = served
    server.bridge.PREFETCH_GATE.clear()
    a, b = upload(base), upload(base)
    wait_status(base, a, ("ready",))
    wait_status(base, b, ("ready",))
    assert wait_until(lambda: [c["jid"] for c in server.CURRENT.values() if c["kind"] == "prefetch"] == [a] and len(server.QUEUED["heavy"]) == 1)   # a's read-ahead waits at its gate; b's is queued behind it
    assert call(base, f"/api/sheets/{a}/run", "POST", {})[0] == 200
    assert call(base, f"/api/sheets/{b}/run", "POST", {})[0] == 200
    assert wait_until(lambda: len(server.QUEUED["heavy"]) == 3)
    ja, jb = call(base, f"/api/sheets/{a}")[1], call(base, f"/api/sheets/{b}")[1]
    assert ja["status"] == "queued" and ja["progress"]["stage"] == "reading the text ahead"
    assert ja["progress"]["position"] == 0 and ja["progress"]["wait"] >= 0
    assert jb["status"] == "queued" and jb["progress"]["position"] == 1 and jb["progress"]["wait"] >= 1
    unchanged = call(base, f"/api/sheets/{b}?rev={jb['rev']}")[1]   # the wait comes with every poll, even an unchanged record
    assert unchanged["unchanged"] and unchanged["queue"]["position"] == 1 and unchanged["queue"]["wait"] >= 1
    status = call(base, "/api/status")[1]
    assert status["queued"] == {"light": 0, "heavy": 3} and status["workers"] == {"light": 1, "heavy": 1, "processes": False, "unavailable": {}}
    assert [w["kind"] for w in status["working"]] == ["prefetch"]
    server.bridge.PREFETCH_GATE.set()
    wait_status(base, a, ("done", "error"))
    wait_status(base, b, ("done", "error"))
    assert call(base, f"/api/sheets/{b}")[1]["progress"] is None


def test_estimates_follow_the_busy_workers_and_the_queue(served):
    base, server, _ = served
    with server.LOCK:
        server.CURRENT["heavy-1"] = {"kind": "extract", "jid": "x", "since": time.time() - 10}
        server.QUEUED["heavy"] = [(0, 1, "extract", "y"), (0, 2, "extract", "z"), (1, 3, "prefetch", "w")]
    server.RECENT["extract"].extend([20, 20])
    try:
        assert server.queue_eta("heavy", "y", "extract") == (0, 10)      # after the 10 s left of the running job
        assert server.queue_eta("heavy", "z", "extract") == (1, 30)      # and y's 20 s
        assert server.queue_eta("heavy", "w", "prefetch") == (2, 50)
        assert server.queue_eta("heavy", "v", "extract") is None
        server.WORKERS = 2                                               # a second worker is free: y starts at once, z after the running job
        assert server.queue_eta("heavy", "y", "extract") == (0, 0) and server.queue_eta("heavy", "z", "extract") == (1, 10)
    finally:
        server.WORKERS = 0
        with server.LOCK:
            server.CURRENT.clear()
            server.QUEUED["heavy"].clear()
        server.RECENT["extract"].clear()


def serve_processes(tmp_path, monkeypatch, workers, public=False):
    """The server with worker processes for the heavy lane, which load the stub bridge from a file. Yields
    (base url, server module); the caller stops it with the returned closer."""
    import conftest
    monkeypatch.setenv("FPX_APP_BRIDGE", str(HERE / "stubbridge.py"))
    sys.modules["bridge"] = conftest.make_bridge()
    sys.modules["bridge"].THREADS = 8
    sys.modules.pop("server", None)
    import server
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "SHEETS", tmp_path / "sheets")
    monkeypatch.setattr(server, "WORKERS", workers)
    monkeypatch.setattr(server, "PUBLIC", public)
    server.JOBS.clear()
    server.SESSIONS.clear()
    server.CURRENT.clear()
    server.REMOTES.clear()
    for lane in server.QUEUED.values():
        lane.clear()
    server.load_jobs()
    threads = server.start_workers()
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close():
        srv.shutdown()
        srv.server_close()
        server.stop_workers()
        for w in threads:
            w.join(15)
    return f"http://127.0.0.1:{srv.server_address[1]}", server, close


@pytest.fixture
def served_processes(tmp_path, monkeypatch):
    """The server with one worker process for the heavy lane."""
    base, server, close = serve_processes(tmp_path, monkeypatch, 1)
    try:
        yield base, server
    finally:
        close()


@pytest.fixture
def served_two_processes(tmp_path, monkeypatch):
    base, server, close = serve_processes(tmp_path, monkeypatch, 2)
    try:
        yield base, server
    finally:
        close()


@pytest.fixture
def served_public_processes(tmp_path, monkeypatch):
    base, server, close = serve_processes(tmp_path, monkeypatch, 1, public=True)
    try:
        yield base, server
    finally:
        close()


def test_a_worker_process_runs_the_extraction_and_is_replaced_when_it_dies(served_processes):
    base, server = served_processes
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()                                     # the read-ahead, in the process
    d = server.job_dir(jid)
    assert any("stub read-ahead" in line for line in server.JOBS[jid]["log"])
    (d / "ok").write_text("")
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    job = wait_status(base, jid, ("done", "error"), 60)
    assert job["status"] == "done" and job["results"]["p1/d1"]["outputs"]["json"] == "p1-d1.json"
    assert (d / "out" / "p1-d1.json").exists() and any("stub stage" in line for line in job["log"])
    assert call(base, "/api/status")[1]["workers"] == {"light": 1, "heavy": 1, "processes": True, "unavailable": {}}
    (d / "die").write_text("")                             # the process ends mid-job: the job fails, the next one gets a new process
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    job = wait_status(base, jid, ("done", "error"), 60)
    assert job["status"] == "error" and "worker process stopped" in job["error"]
    os.remove(d / "die")
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    job = wait_status(base, jid, ("done", "error"), 90)
    assert job["status"] == "done"


def test_a_stuck_worker_process_is_stopped_after_the_job_timeout(served_processes, monkeypatch):
    base, server = served_processes
    monkeypatch.setattr(server, "JOB_TIMEOUT", 2)
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()
    d = server.job_dir(jid)
    (d / "ok").write_text("")
    (d / "hang").write_text("")
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    t0 = time.time()
    job = wait_status(base, jid, ("done", "error"), 60)
    assert job["status"] == "error" and "longer than" in job["error"] and time.time() - t0 < 30
    os.remove(d / "hang")
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    assert wait_status(base, jid, ("done", "error"), 90)["status"] == "done"   # a fresh process


def test_two_worker_processes_share_the_heavy_lane(served_two_processes):
    """Two read-aheads run at once, one per process; an extraction whose sheet is still being read ahead waits for
    it; a job running beside another gets a share of the threads (bridge.THREADS = 8 -> 4)."""
    base, server = served_two_processes
    a, b = upload(base), upload(base)
    for jid in (a, b):
        (server.job_dir(jid) / "hold").write_text("")   # the read-aheads wait in their processes
    wait_status(base, a, ("ready",))
    wait_status(base, b, ("ready",))
    assert wait_until(lambda: sorted(c["jid"] for c in server.CURRENT.values() if c["kind"] == "prefetch") == sorted([a, b]))
    status = call(base, "/api/status")[1]
    assert len(status["working"]) == 2 and status["workers"]["heavy"] == 2
    (server.job_dir(a) / "ok").write_text("")
    assert call(base, f"/api/sheets/{a}/run", "POST", {})[0] == 200
    assert wait_until(lambda: call(base, f"/api/sheets/{a}")[1]["progress"]["stage"] == "reading the text ahead")
    (server.job_dir(a) / "hold").unlink()                  # a's read-ahead ends: its extraction follows on a free process
    job = wait_status(base, a, ("done", "error"), 60)
    assert job["status"] == "done"
    assert any("4 threads" in line for line in job["log"]), [l for l in job["log"] if "threads" in l]   # b's read-ahead ran beside it
    (server.job_dir(b) / "hold").unlink()
    server.wait_idle()


def test_the_public_worker_reads_the_session_from_disk_and_the_pickle_goes_with_the_extraction(served_public_processes, monkeypatch):
    base, server = served_public_processes
    from pathlib import Path as P
    monkeypatch.setattr(server.bridge, "save_session", lambda d, s: (P(d) / "session.pkl").write_text("stub") or True)
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()                                     # the read-ahead, in the process, found the pickle
    d = server.job_dir(jid)
    assert (d / "session.pkl").exists() and any("loaded from disk" in line for line in server.JOBS[jid]["log"])
    (d / "ok").write_text("")
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    assert wait_status(base, jid, ("done", "error"), 60)["status"] == "done"
    assert not (d / "session.pkl").exists()                # not kept on the public server once extracted


def test_delete_during_a_read_ahead_leaves_no_folder(served):
    """A sheet deleted while its text is read ahead (a status the page sees as ready) is discarded by the worker when
    it is done, not resurrected by the cache the read-ahead writes."""
    base, server, _ = served
    server.bridge.PREFETCH_GATE.clear()
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    assert wait_until(lambda: any(c["kind"] == "prefetch" and c["jid"] == jid for c in server.CURRENT.values()))
    status, body = call(base, f"/api/sheets/{jid}", "DELETE")
    assert status == 200 and body.get("pending") is True
    server.bridge.PREFETCH_GATE.set()
    server.wait_idle()
    assert wait_until(lambda: not server.job_dir(jid).exists()) and jid not in server.JOBS


def test_delete_during_a_read_ahead_in_a_worker_process(served_processes):
    base, server = served_processes
    jid = upload(base)
    (server.job_dir(jid) / "hold").write_text("")
    wait_status(base, jid, ("ready",))
    assert wait_until(lambda: any(c["kind"] == "prefetch" and c["jid"] == jid for c in server.CURRENT.values()))
    assert call(base, f"/api/sheets/{jid}", "DELETE")[1].get("pending") is True
    (server.job_dir(jid) / "hold").unlink()
    server.wait_idle()
    assert wait_until(lambda: not server.job_dir(jid).exists()) and jid not in server.JOBS


def test_a_record_that_cannot_be_written_queues_nothing(served, monkeypatch):
    """A job record that cannot be written (disk full) leaves the sheet as it was: not busy, nothing queued."""
    base, server, _ = served
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()

    def broken(data, path):
        raise OSError(28, "No space left on device")
    dump = server.dump
    server.dump = broken                                   # not through monkeypatch: its undo would take the fixture's patches with it
    try:
        status, body = call(base, f"/api/sheets/{jid}/run", "POST", {})
        assert status == 400 and body["code"] == "disk_full"
        assert server.JOBS[jid]["status"] == "ready" and not server.QUEUED["heavy"]
        status, body = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", PNG)
        assert status == 400 and body["code"] == "disk_full" and len(server.JOBS) == 1 and len(list(server.SHEETS.iterdir())) == 1
    finally:
        server.dump = dump
    assert call(base, f"/api/sheets/{jid}/run", "POST", {})[0] == 200
    wait_status(base, jid, ("done", "error"))


def test_a_stuck_busy_job_can_be_deleted_and_is_swept(served, monkeypatch):
    """A job left busy with nothing queued or running (a crash between the record and the queue) is not stuck forever."""
    base, server, _ = served
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()
    with server.LOCK:
        server.JOBS[jid]["status"] = "queued"
    assert call(base, f"/api/sheets/{jid}", "DELETE")[1] == {"deleted": jid}
    assert jid not in server.JOBS and wait_until(lambda: not server.job_dir(jid).exists())
