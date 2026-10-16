"""The page in a real browser against the stub bridge (no pipeline): it boots without a script error, the landing page
and its demo link render, and the Download menu stays hidden until there are results. Skipped when Playwright or its
Chromium is not installed (pip install playwright; python -m playwright install chromium)."""
import json
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def browser_page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as ex:                            # noqa: BLE001 - no browser binary: the test does not apply
            pytest.skip(f"Chromium for Playwright is not installed: {ex}")
        page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
        yield page
        browser.close()


def test_finished_results_render(served, monkeypatch, browser_page):
    """The results screen of a finished drawing (the stub's RESULT) renders its figures and room list, and a notice
    (the error path of every POST) shows: both broke once without a test noticing. The Download menu lists only the
    exports the pipeline wrote (no Excel without openpyxl)."""
    import conftest
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    monkeypatch.setitem(conftest.RESULT, "exports", {"json": "p1-d1.json", "xlsx": None, "ifc": "p1-d1.ifc"})
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    wait_status(base, jid, ("done",))
    errors = []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=results")
    browser_page.wait_for_selector("text=Net room area", timeout=15000)
    assert browser_page.locator(".room-row", has_text="Office").count() == 1
    menu = browser_page.evaluate("[...document.querySelectorAll('#download-menu a')].map((a) => a.getAttribute('download'))")
    assert "p1-d1.ifc" in menu and not any(n.endswith(".xlsx") for n in menu)
    assert not any(n.endswith(".png") for n in menu)       # the overlay image is a layer, not an export
    assert browser_page.locator(".stage-bottom button.btn-fit[aria-label='Fit'] svg").count() == 1   # fit: a home icon with a name
    soon = browser_page.locator("#download-menu .soon")      # the PDF report is announced, not a link yet
    assert soon.count() == 1 and soon.inner_text().startswith("PDF") and "coming soon" in soon.inner_text()
    browser_page.evaluate("import('/js/ui.js').then((m) => m.notify('A test notice'))")
    browser_page.wait_for_selector("text=A test notice", timeout=5000)
    assert errors == [] and browser_page.locator("#fatal").is_hidden()


def test_measured_scale_with_a_reduced_preview(served, monkeypatch, browser_page):
    """Measuring a known distance on a sheet whose preview is half size: the distance typed keeps its characters, and
    the confirmed scale counts the measured pixels once in sheet px (it was once divided by the preview scale twice)."""
    import conftest
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setitem(conftest.ANALYSIS["packages"][0]["preview"], "scale", 0.5)
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    errors = []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=scale")
    browser_page.click("text=Correct by measuring", timeout=15000)
    box = browser_page.locator(".stage canvas").bounding_box()
    browser_page.mouse.click(box["x"] + 200, box["y"] + 300)          # no preview image: the view is 1:1 with the preview
    browser_page.mouse.click(box["x"] + 400, box["y"] + 300)          # 200 preview px = 400 sheet px
    field = browser_page.locator("input[aria-label='Real distance']")
    field.press_sequentially("4.00", delay=30)
    assert field.input_value() == "4.00"
    browser_page.click("text=Confirm scale")
    t0 = time.time()
    while not server.JOBS[jid]["confirm"]["p1/d1"].get("scale_confirmed") and time.time() - t0 < 10:
        time.sleep(0.05)
    confirmed = server.JOBS[jid]["confirm"]["p1/d1"]
    assert confirmed["px_per_m"] == 100 and confirmed["measured"]["px"] == 400 and confirmed["scale_source"] == "measured"
    assert errors == []


def test_a_changed_area_reads_the_scale_again(served, monkeypatch, browser_page):
    """Step 1 with a half-size preview: dragging a corner and confirming posts the area in sheet px, the scale cues are
    read again for it, and step 2 opens once they are."""
    import conftest
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setitem(conftest.ANALYSIS["packages"][0]["preview"], "scale", 0.5)
    monkeypatch.setitem(conftest.ANALYSIS["packages"][0]["drawings"][0], "polygon_px", [[0, 0], [400, 0], [400, 300], [0, 300]])
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    errors = []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=area")
    browser_page.wait_for_selector("text=Confirm the building area", timeout=15000)
    box = browser_page.locator(".stage canvas").bounding_box()
    browser_page.mouse.move(box["x"] + 200, box["y"] + 150)        # the south-east corner: (400, 300) sheet px at half size
    browser_page.mouse.down()
    browser_page.mouse.move(box["x"] + 300, box["y"] + 250, steps=5)
    browser_page.mouse.up()
    browser_page.locator(".panel .foot .btn-primary").click()
    browser_page.wait_for_selector("text=Check the detected scale", timeout=15000)
    c = server.JOBS[jid]["confirm"]["p1/d1"]
    assert c["polygon_px"] == [[0, 0], [600, 0], [600, 500], [0, 500]] and c["scale_area"] == c["polygon_px"] and c["scale_pending"] is False
    assert errors == []


def test_an_expired_sheet_leaves_the_project(served, browser_page):
    """A sheet the server no longer has (deleted after KEEP_MINUTES on the public server) leaves the project quietly,
    instead of staying as a broken entry."""
    base, _, _ = served
    browser_page.goto(f"{base}/?lang=en&sheets=20260101-000000-gone&screen=results")
    browser_page.wait_for_selector("text=From a scanned plan to rooms, areas and walls", timeout=15000)   # the landing page
    assert browser_page.locator("text=no longer on the server").count() == 0
    assert browser_page.evaluate("JSON.parse(localStorage.getItem('fpx-workflow-project')).sheets") == []


def test_number_formats(served, browser_page):
    base, _, _ = served
    browser_page.goto(f"{base}/?lang=fr")
    browser_page.wait_for_selector("text=Glisser des plans ici ou cliquer", timeout=15000)
    got = browser_page.evaluate("import('/js/core.js').then(({ fmt }) => [fmt.secs(119.6), fmt.pct(0.2), fmt.pct(-3.4), fmt.m2(1234.56), fmt.scaleN(1234)])")
    assert got[:3] == ["2 min 0 s", "0 %", "−3 %"]
    assert got[3].replace(" ", " ") == "1 234,6 m²" and got[4] == "1 : 1234"


@pytest.fixture
def phone_page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as ex:                            # noqa: BLE001
            pytest.skip(f"Chromium for Playwright is not installed: {ex}")
        page = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True).new_page()
        yield page
        browser.close()


def test_phone_layout(served, monkeypatch, phone_page):
    """On a phone the drawing sits above the panel, the confirm button is on screen without scrolling, nothing
    overflows sideways, and the layers of the results are a drawer, closed at first."""
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    errors = []
    phone_page.on("pageerror", lambda e: errors.append(str(e)))
    phone_page.goto(f"{base}/?lang=en&sheets={jid}&screen=area")
    phone_page.wait_for_selector("text=Confirm the building area", timeout=15000)
    assert phone_page.evaluate("document.documentElement.scrollWidth") <= 390
    box = phone_page.locator(".panel .foot .btn-primary").bounding_box()
    assert box["y"] + box["height"] <= 844 and phone_page.locator(".stage").bounding_box()["height"] >= 300
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    wait_status(base, jid, ("done",))
    phone_page.goto(f"{base}/?lang=en&sheets={jid}&screen=results")
    phone_page.wait_for_selector(".figures", timeout=15000)
    assert phone_page.locator(".side.left").is_hidden() and phone_page.evaluate("document.documentElement.scrollWidth") <= 390
    phone_page.tap(".edge-left")
    assert phone_page.locator(".side.left").is_visible()
    phone_page.tap(".edge-left")                           # the open drawer must not cover its own button
    assert phone_page.locator(".side.left").is_hidden()
    assert errors == []


def test_progress_bar_while_analysing(served, browser_page):
    """The busy screen names the stage in plain words and says how long it takes; no pipeline line."""
    from test_server import call
    base, server, gate = served
    gate.clear()
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    try:
        browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=upload")
        browser_page.wait_for_selector(".progress-box", timeout=15000)
        text = browser_page.locator(".progress-box").inner_text()
        assert "Finding the drawings and the title block" in text and ("left" in text or "almost" in text)
        assert "no OCR" not in browser_page.locator("#screen").inner_text()
    finally:
        gate.set()


def test_without_app_server_the_page_says_so(browser_page):
    """Served by a plain file server (python -m http.server, an editor's preview), the page has no API behind it: it
    says how to start the app server instead of showing an upload page that cannot work."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(Path(__file__).resolve().parents[1])))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        browser_page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html?lang=en")
        browser_page.wait_for_selector("text=The app server is not reachable", timeout=15000)
        assert browser_page.locator("#app").is_hidden()
    finally:
        srv.shutdown()
        srv.server_close()


def test_landing_boots_without_errors(served, tmp_path, monkeypatch, browser_page):
    base, server, _ = served
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "house.png").write_bytes(b"\x89PNG fake")
    (demo / "demo.json").write_text(json.dumps({"file": "house.png", "title": "A house", "source": "test", "licence": "CC0"}), encoding="utf-8")
    monkeypatch.setattr(server, "DEMO", demo)
    errors, console = [], []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    browser_page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    # German unless the visitor chose another language
    browser_page.goto(base + "/")
    browser_page.wait_for_selector("text=Grundrisse hierher ziehen oder klicken", timeout=15000)
    assert browser_page.evaluate("document.documentElement.lang") == "de"
    assert browser_page.locator("text=Demo-Datei laden").count() == 1
    # ?lang= wins and is remembered; the banner, the footer and the login demo are there
    browser_page.goto(base + "/?lang=en")
    browser_page.wait_for_selector("text=Drop floor plans here or click", timeout=15000)
    assert browser_page.locator("text=Load demo file").count() == 1
    # the explainer film in the page's language, in a player: nothing plays by itself, a click on the stage plays, the
    # timeline seeks, replay starts over
    assert browser_page.locator("video.how-film").get_attribute("src") == "/media/how-it-works-en.webm"
    assert browser_page.evaluate("document.querySelector('video.how-film').paused") and browser_page.locator(".how-play").is_visible()
    browser_page.wait_for_function("document.querySelector('video.how-film').readyState >= 1", timeout=15000)
    browser_page.click(".how-stage")
    browser_page.wait_for_function("!document.querySelector('video.how-film').paused", timeout=10000)
    assert browser_page.locator(".how-btn[aria-label='Pause']").count() == 1
    browser_page.evaluate("const r = document.querySelector('.how-time'); r.value = 20; r.dispatchEvent(new Event('input'))")
    browser_page.wait_for_function("document.querySelector('video.how-film').currentTime >= 20", timeout=5000)
    browser_page.click(".how-btn[aria-label='Replay']")
    browser_page.wait_for_function("document.querySelector('video.how-film').currentTime < 3", timeout=5000)
    browser_page.click(".how-btn[aria-label='Pause']")
    browser_page.wait_for_function("document.querySelector('video.how-film').paused", timeout=5000)
    assert browser_page.locator(".prototype-banner").inner_text().startswith("PROTOTYPE")
    assert browser_page.locator(".footer a[href='/api/docs']").count() == 1
    assert browser_page.locator("#download-menu").is_hidden()
    assert browser_page.locator("#fatal").is_hidden()
    browser_page.click("#btn-login")
    assert browser_page.locator("#login").is_visible()
    browser_page.click("#login-close")
    unnamed = browser_page.evaluate("""() => [...document.querySelectorAll('button, a[href], input, select')].filter((el) => el.getClientRects().length
      && !(el.getAttribute('aria-label') || el.textContent.trim() || el.getAttribute('title') || (el.labels && el.labels[0]))).length""")
    assert unnamed == 0
    assert errors == [] and console == []                   # no script error, no failed request on the landing page
    # the language menu switches the page (it reloads)
    browser_page.select_option("#lang-select", "fr")
    browser_page.wait_for_selector("text=Glisser des plans ici ou cliquer", timeout=15000)
    browser_page.goto(base + "/?lang=en")
    browser_page.wait_for_selector("text=Drop floor plans here or click", timeout=15000)
    # the demo starts a job through the stub bridge and the page moves on to step 1 by itself (the stub has no preview
    # image, so a failed image request is expected there; a script error is not)
    browser_page.click("text=Load demo file")
    browser_page.wait_for_selector("text=Confirm the building area", timeout=15000)
    assert errors == []


def test_walls_and_openings_are_clickable(served, monkeypatch, browser_page):
    """A click on a wall or an opening of the finished drawing opens the same card as a room, with the figures of
    the element (the stub's wall w000 runs along y = -0.1 m, its door o1 sits at x 3.9..4.1, y 1..1.9); Escape
    closes it."""
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    wait_status(base, jid, ("done",))
    errors = []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=results")
    browser_page.wait_for_selector("text=Net room area", timeout=15000)
    time.sleep(0.5)
    box = browser_page.locator(".stage canvas").bounding_box()
    card = browser_page.locator(".popup")

    def click_plan(x_m, y_m):                               # plan metres (y up, extent 10 x 8 m) -> working px -> screen
        sx, sy = browser_page.evaluate("([x, y]) => document.querySelector('.stage').fpxViewer.toScreen(x, y)", [x_m * 50, (8 - y_m) * 50])
        browser_page.mouse.click(box["x"] + sx, box["y"] + sy)
        time.sleep(0.3)

    click_plan(3.0, -0.1)
    text = card.inner_text()
    assert "Wall w000" in text and "Thickness" in text and "0.20 m" in text and "Office" in text and "Outside" in text, text
    browser_page.keyboard.press("Escape")                   # the card sits over the door in this small plan
    click_plan(4.0, 1.45)
    text = card.inner_text()
    assert "Door o1" in text and "Width" in text and "0.90 m" in text and "w000" in text, text
    browser_page.locator(".room-row", has_text="Office").click()
    time.sleep(0.3)
    text = card.inner_text()
    assert "Gross (wall axes)" in text and "13.4" in text, text
    browser_page.keyboard.press("Escape")
    time.sleep(0.2)
    assert card.is_hidden()
    # the layer tree: a row is a switch with the eye on the right, a group row folds its group, the axes are off at first
    walls = browser_page.locator(".layer-row[data-layer=walls]")
    assert walls.get_attribute("aria-checked") == "true" and "Walls (1)" in walls.inner_text()
    walls.click()
    assert browser_page.locator(".layer-row[data-layer=walls]").get_attribute("aria-checked") == "false"
    assert browser_page.locator(".layer-row[data-layer=wallAxes]").get_attribute("aria-checked") == "false"
    assert browser_page.locator(".layer-row[data-layer=graph]").get_attribute("aria-checked") == "false"
    group = browser_page.locator(".layer-group", has_text="Rooms").first
    assert group.get_attribute("open") is not None
    group.locator("summary .eye").click()                     # the group's eye: all its layers off, the group stays open
    time.sleep(0.2)
    assert browser_page.locator(".layer-row[data-layer=rooms]").get_attribute("aria-checked") == "false"
    assert browser_page.locator(".layer-row[data-layer=labels]").get_attribute("aria-checked") == "false"
    group = browser_page.locator(".layer-group", has_text="Rooms").first
    assert group.get_attribute("open") is not None and group.locator("summary .eye").get_attribute("aria-checked") == "false"
    group.locator("summary .eye").click()                     # and on again, every one of them
    time.sleep(0.2)
    assert browser_page.locator(".layer-row[data-layer=gross]").get_attribute("aria-checked") == "true"
    group = browser_page.locator(".layer-group", has_text="Rooms").first
    group.locator("summary").click()
    time.sleep(0.2)
    assert browser_page.locator(".layer-group", has_text="Rooms").first.get_attribute("open") is None
    assert errors == []


def test_a_queued_job_shows_its_place_in_the_queue(served, browser_page):
    """The results screen of a job waiting behind another says how many are ahead and how long it will take, and
    the wait keeps moving while the record itself does not change (the unchanged poll carries it)."""
    from test_server import call, wait_status
    base, server, _ = served
    server.bridge.PREFETCH_GATE.clear()                    # the heavy lane is held by the first sheet's read-ahead
    a = call(base, "/api/upload?name=a.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    b = call(base, "/api/upload?name=b.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, a, ("ready",))
    wait_status(base, b, ("ready",))
    from conftest import wait_until
    assert wait_until(lambda: [c["jid"] for c in server.CURRENT.values() if c["kind"] == "prefetch"] == [a])
    call(base, f"/api/sheets/{a}/run", "POST", {})
    call(base, f"/api/sheets/{b}/run", "POST", {})
    errors = []
    browser_page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        browser_page.goto(f"{base}/?lang=en&sheets={b}&screen=results")
        browser_page.wait_for_selector("text=1 sheet ahead of yours in the queue", timeout=15000)
        text = browser_page.locator(".progress-box").inner_text()
        assert "about" in text and ("s left" in text or "min left" in text), text
    finally:
        server.bridge.PREFETCH_GATE.set()
    wait_status(base, b, ("done", "error"))
    assert errors == []


def test_the_card_never_covers_the_selected_element(served, monkeypatch, browser_page):
    """The card is docked top right (top left only when the element lies there), joined to the element by a line."""
    from test_server import call, wait_status
    base, server, _ = served
    monkeypatch.setattr(server.bridge, "EXTRACT_OK", True)
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    call(base, f"/api/sheets/{jid}/run", "POST", {})
    wait_status(base, jid, ("done",))
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=results")
    browser_page.wait_for_selector("text=Net room area", timeout=15000)
    time.sleep(0.5)
    browser_page.locator(".room-row", has_text="Office").click()
    time.sleep(0.4)
    boxes = browser_page.evaluate("""() => {
      const stage = document.querySelector('.stage').getBoundingClientRect(), card = document.querySelector('.popup').getBoundingClientRect();
      const v = document.querySelector('.stage').fpxViewer, ring = [[0, 0], [4, 0], [4, 3], [0, 3]].map(([x, y]) => v.toScreen(x * 50, (8 - y) * 50));   // the stub's Office, 4 x 3 m
      const xs = ring.map((p) => p[0]), ys = ring.map((p) => p[1]);
      return { item: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], card: [card.left - stage.left, card.top - stage.top, card.right - stage.left, card.bottom - stage.top],
               leader: !document.querySelector('.callout').hidden, stage: [stage.width, stage.height] };
    }""")
    i, c = boxes["item"], boxes["card"]
    overlap = max(0, min(i[2], c[2]) - max(i[0], c[0])) * max(0, min(i[3], c[3]) - max(i[1], c[1]))
    assert overlap == 0, boxes
    assert abs(c[2] - (boxes["stage"][0] - 12)) < 2 and abs(c[1] - 12) < 2, boxes   # docked top right
    assert boxes["leader"], boxes                                                  # joined to the room by a line


def test_the_resolution_is_changed_with_an_apply_button(served, monkeypatch, browser_page):
    """The resolution row: the value on one line with its button to the left; a change needs Enter or Apply."""
    from test_server import call, wait_status
    base, server, _ = served
    jid = call(base, "/api/upload?name=plan.png", "POST", b"\x89PNG fake", {"Content-Type": "application/octet-stream"})[1]["id"]
    wait_status(base, jid, ("ready",))
    call(base, f"/api/sheets/{jid}/confirm", "POST", {"confirm": {"p1/d1": {"area_confirmed": True}}})
    browser_page.goto(f"{base}/?lang=en&sheets={jid}&screen=scale")
    browser_page.wait_for_selector("text=Resolution", timeout=15000)
    row = browser_page.locator(".kv", has_text="Resolution")
    assert row.locator(".big").bounding_box()["height"] < 40          # one line
    row.locator("button", has_text="Change").click()
    browser_page.locator(".input-dpi").fill("300")
    browser_page.locator("body").click()                                # a click elsewhere does not apply it
    assert browser_page.locator(".input-dpi").count() == 1
    browser_page.locator("button", has_text="Apply").click()
    browser_page.wait_for_selector("text=300 dpi", timeout=5000)
    assert browser_page.locator(".kv", has_text="Resolution").locator("text=set by hand").count() == 1
