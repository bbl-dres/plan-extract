"""Routes and job model of app/server.py with the stub bridge of conftest.py: static files and the traversal guard,
upload validation, the analysis through the worker, confirm validation, the deferred delete of a busy job, the demo
sheet, 404s and malformed JSON.

    python -m pytest app/tests -q      (from the repository root)
"""
import importlib.util
import sys
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def call(base, path, method="GET", body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or ({"Content-Type": "application/json"} if body is not None and not isinstance(body, bytes) else {}))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("content-type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if e.headers.get("content-type", "").startswith("application/json") else raw)


def wait_status(base, jid, wanted, timeout=10):
    t0 = time.time()
    status, job = None, None
    while time.time() - t0 < timeout:
        status, job = call(base, f"/api/sheets/{jid}")
        assert status == 200, (status, job)                 # a 404 or 500 says so, not KeyError: 'status'
        if job["status"] in wanted:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not reach {wanted}: {job and job.get('status')} {job and job.get('error')}")


def test_static_files_and_traversal_guard(served):
    base, server, _ = served
    status, body = call(base, "/css/tokens.css")
    assert status == 200 and b"--accent" in body
    status, body = call(base, "/js/app.js")
    assert status == 200 and b"import" in body
    assert call(base, "/js/../server.py")[0] == 404
    assert call(base, "/css/../tests/conftest.py")[0] == 404
    assert call(base, "/css/nothing.css")[0] == 404


def test_upload_rejects_unknown_type_and_empty_body(served):
    base, server, _ = served
    status, body = call(base, "/api/upload?name=notes.txt", "POST", b"hello", {"Content-Type": "application/octet-stream"})
    assert status == 400 and "Unsupported file type" in body["error"]
    status, body = call(base, "/api/upload?name=plan.png", "POST", b"", {"Content-Type": "application/octet-stream"})
    assert status == 400 and "empty" in body["error"]


def test_upload_analysis_confirm_run_and_delete(served):
    base, server, _ = served
    status, body = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})
    assert status == 200 and body["status"] == "analysing"
    jid = body["id"]
    job = wait_status(base, jid, ("ready", "error"))
    assert job["status"] == "ready", job.get("error")
    assert job["analysis"]["packages"][0]["drawings"][0]["id"] == "d1"
    # the analysis reads the scale cues of the floor plans too, inside their detected area
    c = job["confirm"]["p1/d1"]
    assert c["extract"] is True and c["proposal_px_per_m"] == 50.0 and c["scale_pending"] is False and c["scale_area"] is None
    assert job["analysis"]["packages"][0]["drawings"][0]["scale"]["px_per_m"] == 50.0
    # confirm: merged per key; a non-object entry is refused
    area = [[1, 1], [9, 1], [9, 9], [1, 9]]
    status, body = call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": {"area_confirmed": True, "px_per_m": 48.5, "polygon_px": area}}})
    assert status == 200 and body["confirm"]["p1/d1"]["px_per_m"] == 48.5 and body["confirm"]["p1/d1"]["extract"] is True
    # a changed area: the cues are read again and the area they were read in is recorded; then the door search
    assert call(base, f"/api/sheets/{jid}/scale", "POST", {"keys": ["p9/d9"]})[0] == 400
    status, body = call(base, f"/api/sheets/{jid}/scale", "POST", {"keys": ["p1/d1"]})
    assert status == 200 and body["status"] == "scaling"
    job = wait_status(base, jid, ("ready", "error"))
    assert job["analysis"]["packages"][0]["drawings"][0]["scale"]["px_per_m"] == 50.0
    assert job["confirm"]["p1/d1"]["proposal_px_per_m"] == 50.0 and job["confirm"]["p1/d1"]["scale_pending"] is False
    assert job["confirm"]["p1/d1"]["scale_area"] == area
    status, body = call(base, f"/api/sheets/{jid}/scale", "POST", {"keys": ["p1/d1"], "doors": True})
    assert status == 200
    job = wait_status(base, jid, ("ready", "error"))
    assert job["analysis"]["packages"][0]["drawings"][0]["scale"]["px_per_m"] == 48.0
    assert call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": 5}})[0] == 400
    assert call(base, f"/api/sheets/{jid}/confirm", "POST", b"{not json", {"Content-Type": "application/json"})[0] == 400
    # run: the stub extraction fails per drawing, which the job reports as an error with the message
    status, body = call(base, f"/api/sheets/{jid}/run", "POST", {})
    assert status == 200 and body["status"] == "queued"
    job = wait_status(base, jid, ("done", "error"))
    assert job["status"] == "error" and "stub: no pipeline" in job["error"]
    assert call(base, f"/api/sheets/{jid}/results?key=p1/d1")[1]["error"] == "stub: no pipeline"
    assert call(base, f"/api/sheets/{jid}/results?key=p1/d9")[0] == 404
    # the live view: nothing until the bridge writes it, then the file as is
    assert call(base, f"/api/sheets/{jid}/live?key=p1/d1")[1] == {"stage": None}
    assert call(base, f"/api/sheets/{jid}/live?key=../x")[0] == 400
    (server.SHEETS / jid / "out").mkdir(exist_ok=True)
    (server.SHEETS / jid / "out" / "p1-d1_live.json").write_text(json.dumps({"stage": "walls", "walls": [[[0, 0], [1, 0], [1, 1]]]}), encoding="utf-8")
    assert call(base, f"/api/sheets/{jid}/live?key=p1/d1")[1]["stage"] == "walls"
    assert call(base, f"/api/sheets/{jid}/log")[0] == 200
    # the sheet list, then delete
    status, body = call(base, "/api/sheets")
    assert status == 200 and [s["id"] for s in body["sheets"]] == [jid]
    assert call(base, f"/api/sheets/{jid}", "DELETE")[0] == 200
    assert call(base, f"/api/sheets/{jid}")[0] == 404
    assert not (server.SHEETS / jid).exists()


def test_scale_cues_with_the_analysis(served, monkeypatch):
    """A re-read marks the drawing pending until it is done (the scale step waits instead of showing the old record);
    when the cues fail at the analysis, the layout stands and the drawing stays pending for the scale step."""
    base, server, gate = served
    png = {"Content-Type": "application/octet-stream"}
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", png)[1]["id"]
    wait_status(base, jid, ("ready",))
    gate.clear()                                           # the stub scale step waits
    call(base, f"/api/sheets/{jid}/scale", "POST", {"keys": ["p1/d1"]})
    assert call(base, f"/api/sheets/{jid}")[1]["confirm"]["p1/d1"]["scale_pending"] is True
    gate.set()
    assert wait_status(base, jid, ("ready",))["confirm"]["p1/d1"]["scale_pending"] is False

    def broken(*a, **k):
        raise RuntimeError("no OCR engine")
    monkeypatch.setattr(server.bridge, "scale_step", broken)
    jid = call(base, "/api/upload?name=plan2.png", "POST", b"\x89PNG fake", png)[1]["id"]
    job = wait_status(base, jid, ("ready", "error"))
    assert job["status"] == "ready" and job["confirm"]["p1/d1"]["scale_pending"] is True
    assert any("scale cues not read" in line for line in job["log"])


def test_busy_job_delete_is_deferred_and_worker_survives(served):
    base, server, gate = served
    gate.clear()                                           # the stub analysis waits: the job stays 'analysing'
    status, body = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})
    jid = body["id"]
    assert call(base, f"/api/sheets/{jid}")[1]["status"] == "analysing"
    assert call(base, f"/api/sheets/{jid}/run", "POST", {})[0] == 400
    # the abort: the job is gone for the client at once, its folder once the worker is done with it
    status, body = call(base, f"/api/sheets/{jid}", "DELETE")
    assert status == 200 and body["pending"] is True
    assert call(base, f"/api/sheets/{jid}")[0] == 404
    assert jid not in [s["id"] for s in call(base, "/api/sheets")[1]["sheets"]]
    gate.set()
    t0 = time.time()
    while (server.SHEETS / jid).exists() and time.time() - t0 < 10:
        time.sleep(0.05)
    assert not (server.SHEETS / jid).exists() and jid not in server.JOBS
    # a second job after the first: the single worker is still alive
    status, body = call(base, "/api/upload?name=plan2.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})
    assert wait_status(base, body["id"], ("ready",))["status"] == "ready"


def test_demo_sheet(served, tmp_path, monkeypatch):
    base, server, _ = served
    demo = tmp_path / "demo"
    demo.mkdir()
    monkeypatch.setattr(server, "DEMO", demo)              # an empty demo folder: no demo
    assert call(base, "/api/demo")[1]["demo"] is None
    assert call(base, "/api/demo", "POST", {})[0] == 400
    (demo / "house.png").write_bytes(b"\x89PNG fake")
    (demo / "demo.json").write_text(json.dumps({"file": "house.png", "title": "A house", "source": "test", "licence": "CC0"}), encoding="utf-8")
    monkeypatch.setattr(server, "DEMO", demo)
    status, body = call(base, "/api/demo")
    assert status == 200 and body["demo"]["title"] == "A house" and body["demo"]["size"] == 9
    status, body = call(base, "/api/demo", "POST", {})
    assert status == 200 and body["name"] == "house.png"
    assert wait_status(base, body["id"], ("ready",))["status"] == "ready"


def test_cross_site_writes_refused(served):
    """Another site's page cannot make a logged-in browser change anything (CSRF): POST and DELETE marked cross-site by
    Sec-Fetch-Site or by a foreign Origin are refused; the app's own page, scripts without those headers and GETs pass."""
    base, server, _ = served
    png = {"Content-Type": "application/octet-stream"}
    for foreign in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Sec-Fetch-Site": "same-site"}):
        assert call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {**png, **foreign})[0] == 403, foreign
    assert not server.JOBS
    status, body = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {**png, "Origin": base, "Sec-Fetch-Site": "same-origin"})
    assert status == 200
    jid = body["id"]
    wait_status(base, jid, ("ready",))
    assert call(base, f"/api/sheets/{jid}", "DELETE", None, {"Origin": "https://evil.example"})[0] == 403
    assert call(base, "/api/sheets", headers={"Sec-Fetch-Site": "cross-site"})[0] == 200
    assert call(base, f"/api/sheets/{jid}", "DELETE", None, {"Origin": base})[0] == 200


def test_public_server_keeps_nothing(served, monkeypatch):
    """--public: no sheet list, unguessable ids, a sheet deleted KEEP_MINUTES after its last request (never while the
    worker has it), uploads refused while MAX_BUSY jobs wait, and the mode reported to the page."""
    base, server, gate = served
    monkeypatch.setattr(server, "PUBLIC", True)
    monkeypatch.setattr(server, "KEEP_MINUTES", 1)
    png = {"Content-Type": "application/octet-stream"}
    health = call(base, "/api/health")[1]
    assert health["public"] is True and health["keep_minutes"] == 1
    assert call(base, "/api/sheets")[0] == 404
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", png)[1]["id"]
    assert len(jid.rsplit("-", 1)[1]) == 32                  # 128 random bits after the time stamp
    wait_status(base, jid, ("ready",))
    server.wait_idle()                                     # a sheet whose text is being read ahead is not swept
    t = time.time()
    assert server.sweep(now=t + 30) == [] and (server.SHEETS / jid).exists()     # asked for just now: kept
    assert server.sweep(now=t + 120) == [jid]
    assert not (server.SHEETS / jid).exists() and call(base, f"/api/sheets/{jid}")[0] == 404
    gate.clear()                                           # a job the worker has is never swept
    busy = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", png)[1]["id"]
    assert server.sweep(now=time.time() + 600) == [] and (server.SHEETS / busy).exists()
    monkeypatch.setattr(server, "MAX_BUSY", 1)
    status, body = call(base, "/api/upload?name=plan2.png", "POST", b"\x89PNG fake", png)
    assert status == 400 and "busy" in body["error"]
    gate.set()
    wait_status(base, busy, ("ready",))


def test_public_restart_keeps_only_recent_sheets(served, monkeypatch):
    """A restart (a deploy) keeps the sheets asked for within KEEP_MINUTES, as the folder's time records; older ones,
    broken ones and one that was busy are handled as the sweeper would."""
    import os
    base, server, _ = served

    def leftover(name, status, age_min):
        d = server.SHEETS / name
        d.mkdir(parents=True)
        (d / "job.json").write_text(json.dumps({"id": name, "status": status, "log": [], "analysis": {"packages": []}}), encoding="utf-8")
        t = time.time() - age_min * 60
        os.utime(d, (t, t))
        return d

    old = leftover("20260101-000000-old", "done", server.KEEP_MINUTES + 5)
    recent = leftover("20260101-000000-recent", "done", 5)
    scaling = leftover("20260101-000000-scaling", "scaling", 1)
    broken = server.SHEETS / "20260101-000000-broken"
    broken.mkdir()
    monkeypatch.setattr(server, "PUBLIC", True)
    server.JOBS.clear()
    server.load_jobs()
    assert not old.exists() and not broken.exists() and recent.exists()
    assert set(server.JOBS) == {recent.name, scaling.name}
    assert server.JOBS[scaling.name]["status"] == "ready"           # the scale step just runs again
    assert time.time() - server.JOBS[recent.name]["seen"] > 4 * 60    # its clock goes on from the last request


def test_ocr_cache_inside_the_job_on_the_public_server(tmp_path):
    """The real bridge (no pipeline import needed): the shared cache locally, the job's own folder with --public."""
    spec = importlib.util.spec_from_file_location("bridge_real", Path(__file__).resolve().parents[1] / "bridge.py")
    b = importlib.util.module_from_spec(spec)
    saved = list(sys.path)                                 # the real bridge puts the pipeline on the path: not for the other tests
    try:
        spec.loader.exec_module(b)
    finally:
        sys.path[:] = saved
    assert b.ocr_cache(tmp_path) == b.CACHE_DIR
    b.JOB_CACHE = True
    assert b.ocr_cache(tmp_path) == tmp_path / "ocr_cache"


def test_unsupported_method_and_bad_request_get_an_answer(served):
    """The error path logs the status code first: an unsupported method or a malformed request line (which scanners
    send) is answered, not dropped with an empty reply. HEAD (which monitors send) is a GET without the body."""
    base, server, _ = served
    import http.client
    import socket
    host, port = base.rsplit("/", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request("PUT", "/")
    assert conn.getresponse().status == 501
    conn.close()
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request("HEAD", "/api/health")
    r = conn.getresponse()
    assert r.status == 200 and int(r.headers["Content-Length"]) > 0 and r.read() == b""
    conn.close()
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(b"GARBAGE\r\n\r\n")                       # answered HTTP/0.9 style (an error page without headers)
        assert s.recv(64)


def test_finished_extraction_results_review_and_csv(served, monkeypatch):
    """A finished drawing (the stub's RESULT): the results payload, the review merged into it, the room list as CSV."""
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    assert wait_status(base, jid, ("done", "error"))["status"] == "done"
    res = call(base, f"/api/sheets/{jid}/results?key=p1/d1")[1]
    assert [r["name"] for r in res["rooms"]] == ["Office", None] and res["floor"]["gf_area"] == 20.0
    call(base, f"/api/sheets/{jid}/review", "POST", {"key": "p1/d1", "rooms": {"r01": {"checked": True}}})
    assert call(base, f"/api/sheets/{jid}/results?key=p1/d1")[1]["review"]["rooms"]["r01"]["checked"] is True
    status, csv = call(base, f"/api/sheets/{jid}/rooms.csv?key=p1/d1")
    assert status == 200 and b"r01,Office" in csv and b",yes," in csv


def test_unknown_routes(served):
    base, server, _ = served
    assert call(base, "/api/nothing")[0] == 404
    assert call(base, "/api/sheets/no-such-id")[0] == 404
    assert call(base, "/api/sheets/no-such-id/results?key=x")[0] == 404
