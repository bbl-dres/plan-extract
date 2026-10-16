"""The API documentation and the batch preview: the docs page and the OpenAPI description are served, every workflow
operation the description names is one the server routes, and the batch API (/api/v1) refuses every request with
401 and a Bearer challenge, with a token or without.

    python -m pytest app/tests -q      (from the repository root)
"""
import json
import re
import time
import urllib.request
from pathlib import Path

from test_server import call, wait_status

APP = Path(__file__).resolve().parents[1]
SPEC = json.loads((APP / "openapi.json").read_text(encoding="utf-8"))


def test_docs_and_description_served(served):
    base, _, _ = served
    status, page = call(base, "/api/docs")
    assert status == 200 and b"swagger-ui-bundle.js" in page and b"/api/openapi.json" in page
    status, spec = call(base, "/api/openapi.json")
    assert status == 200 and spec["openapi"].startswith("3.1") and "/api/v1/batches" in spec["paths"]
    assert call(base, "/js/vendor/swagger-ui/swagger-ui-bundle.js")[0] == 200
    assert call(base, "/js/api-docs.js")[0] == 200
    # the explainer film of the landing page, one file per language, and nothing beside the media folder
    assert all(call(base, f"/media/how-it-works-{lang}.webm")[0] == 200 for lang in ("de", "fr", "it", "en"))
    assert call(base, "/media/how-it-works-de.jpg")[0] == 200 and call(base, "/media/../server.py")[0] == 404
    # a byte range (the video seeks with them): the slice as 206
    with urllib.request.urlopen(urllib.request.Request(base + "/media/how-it-works-de.webm", headers={"Range": "bytes=10-109"})) as r:
        assert r.status == 206 and r.headers["Content-Range"].startswith("bytes 10-109/") and len(r.read()) == 100
    with urllib.request.urlopen(urllib.request.Request(base + "/media/how-it-works-de.webm", headers={"Range": "bytes=-50"})) as r:
        assert r.status == 206 and len(r.read()) == 50 and r.headers["Accept-Ranges"] == "bytes"
    status, page = call(base, "/api/docs")
    page = page.decode("utf-8") if isinstance(page, bytes) else page
    assert status == 200 and all(s in page for s in ('id="btn-help"', 'id="lang-select"', 'id="btn-login"', 'data-i18n="top.back"', 'id="help"'))


def test_batch_api_needs_a_token(served):
    base, server, _ = served
    for method, path in (("POST", "/api/v1/batches"), ("GET", "/api/v1/batches/b1"), ("DELETE", "/api/v1/batches/b1"),
                         ("POST", "/api/v1/batches/b1/sheets?name=plan.pdf"), ("POST", "/api/v1/batches/b1/start")):
        status, body = call(base, path, method, {} if method == "POST" else None)
        assert status == 401 and body["error"] == "token_required", (method, path)
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + "/api/v1/batches/b1", headers={"Authorization": "Bearer abc"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401 and json.loads(e.read())["error"] == "invalid_token"
        assert e.headers["WWW-Authenticate"] == 'Bearer realm="plan-extract", error="invalid_token"'


def test_every_documented_workflow_operation_is_routed(served):
    """Each operation of the description is called on a real (stub) job: whatever it answers, it is never the router's
    'no route' 404, so the description cannot name an endpoint the server does not have."""
    base, server, _ = served
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    ops = [(m.upper(), p) for p, item in SPEC["paths"].items() for m in item if m in ("get", "post", "delete") and not p.startswith("/api/v1/")]
    ops.sort(key=lambda op: op[0] == "DELETE")                # the delete of the job last
    for method, path in ops:
        url = path.replace("{id}", jid).replace("{name}", "p1_preview.jpg")
        query = {"/api/upload": "name=plan.png"}.get(path) or ("key=p1/d1" if "{id}/" in path and method == "GET" and path.split("/")[-1] in ("live", "results", "rooms.csv") else "")
        body = b"\x89PNG fake" if path == "/api/upload" else ({"keys": ["p1/d1"]} if path.endswith("/scale") else {"key": "p1/d1", "rooms": {}} if path.endswith("/review")
                                                              else {"confirm": {}} if path.endswith("/confirm") else {}) if method == "POST" else None
        headers = {"Content-Type": "application/octet-stream"} if path == "/api/upload" else None
        status, answer = call(base, url + (f"?{query}" if query else ""), method, body, headers)
        assert not (status == 404 and isinstance(answer, dict) and str(answer.get("error", "")).startswith("no route")), (method, path)
        if path.endswith("/scale") or path.endswith("/run"):
            wait_status(base, jid, ("ready", "done", "error"))
    assert re.match(r"^\d", SPEC["info"]["version"])
