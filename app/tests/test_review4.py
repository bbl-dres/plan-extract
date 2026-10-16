"""The fourth code review (docs/reviews/2026-10-15-app-and-pipeline-code-review-4.md): a confirmed scale whose working
image would be too large is refused, a Unicode digit in ?rev= is not a 500, a status with the worker details."""
from test_server import call, wait_status

PNG = {"Content-Type": "application/octet-stream"}


def ready_sheet(base):
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", PNG)[1]["id"]
    wait_status(base, jid, ("ready",))
    return jid


def test_a_scale_that_needs_a_huge_working_image_is_refused(served):
    base, server, _ = served
    jid = ready_sheet(base)
    # the stub's drawing spans 10 x 10 sheet px: at 0.0667 px/m it is 150 x 150 m = 22 500 m2, within the extent and
    # area limits, but 56 megapixels at the pipeline's 50 px/m
    status, body = call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": {"scale_confirmed": True, "px_per_m": 0.0667, "scale_source": "measured"}}})
    assert status == 400 and body["code"] == "implausible_scale" and "megapixels" in body["error"], body
    status, body = call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": {"scale_confirmed": True, "px_per_m": 50.0, "scale_source": "proposal"}}})
    assert status == 200


def test_a_unicode_digit_in_the_revision_is_ignored(served):
    base, server, _ = served
    jid = ready_sheet(base)
    status, body = call(base, f"/api/sheets/{jid}?rev=%C2%B2")
    assert status == 200 and body["id"] == jid and "unchanged" not in body


def test_status_names_the_workers(served):
    base, server, _ = served
    body = call(base, "/api/status")[1]
    assert body["workers"] == {"light": 1, "heavy": 1, "processes": False, "unavailable": {}} and body["queued"] == {"light": 0, "heavy": 0}
