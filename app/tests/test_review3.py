"""Code review 3 (docs/reviews/2026-10-14-app-code-review-3.md): what the page sends is checked (confirmations, JSON
bodies, Content-Length), error answers carry a code, the public server shows and logs less and limits queued work,
uploads with any name, downloads with the real name, CSV cells that cannot run, weak ETags, the status route."""
import json
import socket
import time
import types

import numpy as np

from test_review2 import head
from test_server import call, wait_status

PNG = {"Content-Type": "application/octet-stream"}


def ready_sheet(base, name="plan.png"):
    jid = call(base, f"/api/upload?name={name}", "POST", b"\x89PNG fake", PNG)[1]["id"]
    wait_status(base, jid, ("ready",))
    return jid


def raw(base, request):
    """A raw HTTP request; returns the status line."""
    host, port = base.rsplit("/", 1)[1].split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(request)
        return s.recv(256).split(b"\r\n")[0].decode()


def test_confirm_is_validated(served):
    base, server, _ = served
    jid = ready_sheet(base)
    url = f"/api/sheets/{jid}/confirm"
    for bad in ({"p9/d9": {"extract": True}},                               # not a drawing of this sheet
                {"p1/d1": {"results": {}}},                                # not a field the page may set
                {"p1/d1": {"extract": "yes"}},
                {"p1/d1": {"polygon_px": [[0, 0], [1, 1]]}},               # two corners
                {"p1/d1": {"polygon_px": [[0, 0]] * (server.MAX_POLYGON + 1)}},
                {"p1/d1": {"px_per_m": -5}},
                {"p1/d1": {"measured": {"px": 10, "distance": None}}},
                {}):
        status, body = call(base, url, "POST", {"confirm": bad})
        assert status == 400 and body["code"] == "bad_request", bad
    status, body = call(base, url, "POST", b'{"confirm": {"p1/d1": {"px_per_m": NaN}}}', {"Content-Type": "application/json"})
    assert status == 400 and body["code"] == "bad_request"
    # 10 x 10 px at 0.01 px/m: a 1 km drawing, a distance typed in the wrong unit
    status, body = call(base, url, "POST", {"confirm": {"p1/d1": {"px_per_m": 0.01, "scale_confirmed": True}}})
    assert status == 400 and body["code"] == "implausible_scale"
    good = {"scale_confirmed": True, "px_per_m": 50.0, "dpi": None, "scale_source": "measured",
            "measured": {"px": 100.0, "distance": 2, "unit": "m", "p1": [0, 0], "p2": [100.0, 0]}}
    status, body = call(base, url, "POST", {"confirm": {"p1/d1": good}})
    assert status == 200 and body["confirm"]["p1/d1"]["px_per_m"] == 50.0
    assert server.JOBS[jid]["confirm"]["p1/d1"]["extract"] is True   # merged into the defaults


def test_json_body_and_content_length_limits(served):
    base, server, _ = served
    jid = ready_sheet(base)
    big = b'{"confirm": {}, "pad": "' + b"x" * (server.MAX_JSON + 10) + b'"}'
    status, body = call(base, f"/api/sheets/{jid}/confirm", "POST", big, {"Content-Type": "application/json"})
    assert status == 400 and body["code"] == "too_large"
    assert raw(base, b"POST /api/upload?name=a.png HTTP/1.1\r\nHost: x\r\nContent-Length: ten\r\n\r\n").endswith("400 Bad Request")
    assert raw(base, b"GET http://x/api/health HTTP/1.1\r\nHost: x\r\n\r\n").endswith("400 Bad Request")


def test_refused_uploads_are_answered(served):
    """A refused upload is read to its end before the answer goes: closing a socket with unread data resets the
    connection on some systems, and the page would see a network error instead of the reason."""
    base, server, _ = served
    for _ in range(5):
        status, body = call(base, "/api/upload?name=plan.exe", "POST", b"x" * 3_000_000, PNG)
        assert status == 400 and body["code"] == "unsupported_type"
    status, body = call(base, "/api/upload?name=plan.png", "POST", b"", PNG)
    assert status == 400 and body["code"] == "empty"
    assert call(base, "/api/sheets/nope")[1]["code"] == "not_found"


def test_any_upload_name_and_the_real_name_on_downloads(served, monkeypatch):
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    jid = ready_sheet(base, "Grundriss%20Z%C3%BCrich%20%E2%80%93%201.OG.png")
    job = server.JOBS[jid]
    assert job["name"] == "Grundriss Zürich – 1.OG.png" and job["file"] == "Grundriss_Zurich_1.OG.png"
    assert (server.SHEETS / jid / job["file"]).is_file()
    header = server.attachment("Zürich 1.OG.csv")["Content-Disposition"]
    assert header == "attachment; filename=\"Zurich_1.OG.csv\"; filename*=UTF-8''Z%C3%BCrich%201.OG.csv"
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    wait_status(base, jid, ("done",))
    status, headers, _ = head(base, f"/api/sheets/{jid}/file/p1-d1.json?download")
    assert status == 200 and 'filename="p1-d1.json"' in headers["Content-Disposition"]


def test_csv_cells_do_not_run_and_a_failed_drawing_has_no_rooms(served, monkeypatch):
    base, server, _ = served
    assert server._cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")" and server._cell("@SUM(A1)") == "'@SUM(A1)"
    assert server._cell(-2.4) == -2.4 and server._cell("Büro") == "Büro" and server._cell(None) is None
    jid = ready_sheet(base)
    call(base, f"/api/sheets/{jid}/run", "POST", {})                # the stub's extraction fails
    wait_status(base, jid, ("error",))
    status, body = call(base, f"/api/sheets/{jid}/rooms.csv?key=p1/d1")
    assert status == 404 and body["code"] == "not_found"


def test_weak_etags_and_the_live_record(served):
    base, server, _ = served
    jid = ready_sheet(base)
    out = server.SHEETS / jid / "out"
    out.mkdir(exist_ok=True)
    assert call(base, f"/api/sheets/{jid}/live?key=p1/d1")[1] == {"stage": None}
    (out / "p1-d1_live.json").write_text('{"stage": "rooms"}', encoding="utf-8")
    status, headers, body = head(base, f"/api/sheets/{jid}/live?key=p1/d1")
    assert status == 200 and json.loads(body) == {"stage": "rooms"}
    assert head(base, f"/api/sheets/{jid}/live?key=p1/d1", {"If-None-Match": "W/" + headers["ETag"]})[0] == 304
    assert call(base, f"/api/sheets/{jid}/live?key=../../x")[0] == 400


def test_cross_site_compares_scheme_host_and_port(served):
    """Behind nginx the Host is the domain and X-Forwarded-Proto the scheme; an Origin differing in any of the three
    is another site."""
    base, server, _ = served
    port = int(base.rsplit(":", 1)[1])
    proxied = {**PNG, "Host": "plan-extract.ch", "X-Forwarded-Proto": "https"}
    upload = lambda headers: call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", headers)[0]   # noqa: E731
    assert upload({**proxied, "Origin": "https://plan-extract.ch"}) == 200
    assert upload({**proxied, "Origin": "https://plan-extract.ch:443"}) == 200
    assert upload({**proxied, "Origin": "http://plan-extract.ch"}) == 403
    assert upload({**proxied, "Origin": "https://plan-extract.ch:8443"}) == 403
    assert upload({**proxied, "Origin": "https://evil.plan-extract.ch"}) == 403
    assert upload({**PNG, "Origin": f"http://127.0.0.1:{port + 1}"}) == 403
    assert upload({**PNG, "Origin": "http://[::1"}) == 403              # malformed


def test_public_server_shows_and_logs_less(served, monkeypatch, capsys):
    base, server, _ = served
    monkeypatch.setattr(server, "PUBLIC", True)
    health = call(base, "/api/health", headers={"X-Forwarded-For": "203.0.113.9"})[1]          # a visitor, through nginx
    assert "pipeline_dir" not in health and "python" not in health and health["ok"] is True and health["public"] is True
    assert "pipeline_dir" in call(base, "/api/health")[1]                                      # on the server itself (setup.sh)
    jid = ready_sheet(base)
    d = server.SHEETS / jid
    (d / "p1_preview.jpg").write_bytes(b"\xff\xd8 fake")
    assert head(base, f"/api/sheets/{jid}/file/p1_preview.jpg")[0] == 200
    for private in ("job.json", "log.txt", server.JOBS[jid]["file"], "..", "%2E%2E%2Fjob.json"):
        assert call(base, f"/api/sheets/{jid}/file/{private}")[0] == 404, private
    h = object.__new__(server.Handler)
    h.client_address = ("203.0.113.9", 0)
    capsys.readouterr()
    h.log_message('"%s" %s %s', f"GET /api/sheets/{jid}/file/Grundriss%20Meier.png?download HTTP/1.1", "404", "-")
    err = capsys.readouterr().err
    assert "/api/sheets/<id>/file/<name>" in err and "Meier" not in err and jid not in err


def test_public_limits_on_queued_work(served, monkeypatch):
    """--public: run and scale are refused while MAX_BUSY jobs wait, and work nobody has asked about for
    ABANDON_SECONDS (the visitor closed the page) is not run when its turn comes."""
    base, server, gate = served
    monkeypatch.setattr(server, "PUBLIC", True)
    server.bridge.PREFETCH_GATE.clear()                    # the heavy lane stays busy with the sheet's read-ahead
    ready = ready_sheet(base)
    gate.clear()                                           # the light lane holds the next analysis
    held = call(base, "/api/upload?name=plan2.png", "POST", b"\x89PNG fake", PNG)[1]["id"]
    monkeypatch.setattr(server, "MAX_BUSY", 1)
    for path, body in (("run", {}), ("scale", {"keys": ["p1/d1"]})):
        status, answer = call(base, f"/api/sheets/{ready}/{path}", "POST", body)
        assert status == 400 and answer["code"] == "server_busy", path
    monkeypatch.setattr(server, "MAX_BUSY", 10)
    assert call(base, f"/api/sheets/{ready}/run", "POST", {})[0] == 200
    server.JOBS[ready]["seen"] = time.time() - server.ABANDON_SECONDS - 1
    gate.set()
    server.bridge.PREFETCH_GATE.set()
    wait_status(base, held, ("ready",))
    t0 = time.time()
    while server.JOBS[ready]["status"] == "queued" and time.time() - t0 < 10:   # not polled: that would count as asked
        time.sleep(0.05)
    job = server.JOBS[ready]
    assert job["status"] == "ready" and "nobody" in job["error"] and job["results"] is None


def test_status_route(served):
    base, server, _ = served
    status, body = call(base, "/api/status")
    assert status == 200 and body["worker_alive"] is True and body["queue"] == 0 and body["working"] is None
    assert set(body) >= {"busy", "jobs", "free_disk_gb", "rss_mb"}


def test_session_cache_bounded_by_bytes(served, monkeypatch):
    base, server, _ = served
    monkeypatch.setattr(server, "SESSION_BYTES", 1500)
    session = lambda: {"packages": [{"pkg": types.SimpleNamespace(img=np.zeros(1000, np.uint8))}]}   # noqa: E731
    server.SESSIONS.clear()
    for jid in ("a", "b", "c"):
        server.remember_session(jid, session())
    assert list(server.SESSIONS) == ["c"]                  # the latest always stays, even alone over the bound


def test_busy_states_are_the_same_on_server_and_page(served):
    import re
    from pathlib import Path
    base, server, _ = served
    js = (Path(__file__).resolve().parents[1] / "js" / "state.js").read_text(encoding="utf-8")
    page = re.search(r"const BUSY = \[([^\]]*)\]", js).group(1)
    assert tuple(re.findall(r"'(\w+)'", page)) == server.BUSY


def test_stub_bridge_matches_the_real_one():
    """The stub of conftest.py stands in for bridge.py: the functions the server calls take the same parameters."""
    import importlib.util
    import inspect
    import sys
    from pathlib import Path

    import conftest
    spec = importlib.util.spec_from_file_location("bridge_real", Path(__file__).resolve().parents[1] / "bridge.py")
    real = importlib.util.module_from_spec(spec)
    saved = list(sys.path)                                 # the real bridge puts the pipeline on the path: not for the other tests
    try:
        spec.loader.exec_module(real)
    finally:
        sys.path[:] = saved
    stub = conftest.make_bridge()
    for name in ("analyse", "scale_step", "prefetch_text", "extract", "save_session", "load_session", "live_name", "check"):
        params = lambda f: [p.name for p in inspect.signature(f).parameters.values()]   # noqa: E731
        assert params(getattr(stub, name)) == params(getattr(real, name)), name
