"""The text inside the drawings is read ahead of the extraction while the user confirms: after the analysis and after
a scale step for a changed area, behind any work a user waits for, never for a sheet that is busy, gone or abandoned."""
import time

from conftest import wait_until
from test_server import call, wait_status

PNG = {"Content-Type": "application/octet-stream"}


def upload(base):
    return call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", PNG)[1]["id"]


def test_read_ahead_after_the_analysis_and_after_an_area_change(served):
    base, server, _ = served
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()
    assert server.bridge.PREFETCHED == [(jid, ["p1/d1"], None)]     # the selected floor plan, in its detected area
    assert server.JOBS[jid]["status"] == "ready" and any("read ahead" in line for line in server.JOBS[jid]["log"])
    poly = [[1, 1], [9, 1], [9, 9], [1, 9]]
    call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": {"area_confirmed": True, "polygon_px": poly}}})
    call(base, f"/api/sheets/{jid}/scale", "POST", {"keys": ["p1/d1"]})
    wait_status(base, jid, ("ready",))
    server.wait_idle()
    assert server.bridge.PREFETCHED[-1] == (jid, ["p1/d1"], poly)   # read again for the new area


def test_work_a_user_waits_for_goes_first(served):
    """The heavy lane: a run queued for a user goes before a read-ahead queued earlier; the light lane takes the
    analysis and the scale step, so neither waits for the heavy lane."""
    base, server, _ = served
    server.stop_workers()
    time.sleep(0.2)
    server.put_work("prefetch", "a", {"keys": ["p1/d1"]}, low=True)
    server.put_work("analyse", "b")
    server.put_work("extract", "d")
    server.put_work("prefetch", "c", {"keys": ["p1/d1"]}, low=True)
    assert [e[2:] for e in server.QUEUED["heavy"]] == [("extract", "d"), ("prefetch", "a"), ("prefetch", "c")]
    assert [e[2:] for e in server.QUEUED["light"]] == [("analyse", "b")]
    assert [server.LANES["heavy"].get()[2][0:2] for _ in range(3)] == [("extract", "d"), ("prefetch", "a"), ("prefetch", "c")]


def test_a_run_queued_during_a_read_ahead_waits_for_it(served):
    base, server, _ = served
    server.bridge.PREFETCH_GATE.clear()
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    assert wait_until(lambda: [c["kind"] for c in server.CURRENT.values()] == ["prefetch"])   # the read-ahead waits at its gate
    assert call(base, f"/api/sheets/{jid}/run", "POST", {})[0] == 200
    assert wait_until(lambda: len(server.QUEUED["heavy"]) == 1) and server.JOBS[jid]["status"] == "queued"   # behind the read-ahead
    server.bridge.PREFETCH_GATE.set()
    assert wait_status(base, jid, ("done", "error"))["status"] == "error"   # the stub's extraction fails: it ran


def test_no_read_ahead_for_a_sheet_that_is_busy_gone_or_abandoned(served, monkeypatch):
    """The worker's rule when a read-ahead comes up: the sheet must be at rest, still there, and (on the public
    server) someone must have asked about it lately; else the extraction reads the text itself."""
    base, server, _ = served
    jid = upload(base)
    wait_status(base, jid, ("ready",))
    server.wait_idle()
    n = len(server.bridge.PREFETCHED)

    def comes_up():
        server.put_work("prefetch", jid, {"keys": ["p1/d1"]}, low=True)
        server.wait_idle()
        return len(server.bridge.PREFETCHED) - n

    with server.LOCK:
        server.JOBS[jid]["status"] = "queued"              # as if a run were queued meanwhile
    assert comes_up() == 0
    with server.LOCK:
        server.JOBS[jid]["status"] = "ready"
    monkeypatch.setattr(server, "PUBLIC", True)
    server.JOBS[jid]["seen"] = time.time() - server.ABANDON_SECONDS - 1
    assert comes_up() == 0 and server.JOBS[jid]["status"] == "ready" and not server.JOBS[jid]["error"]
    server.JOBS[jid]["seen"] = time.time()
    assert comes_up() == 1
    server.JOBS.pop(jid)
    assert comes_up() == 1
