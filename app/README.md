# Floor plan workflow app

A web app on the pilot v2 pipeline (`pilot/v2-pipeline`, package `fpx`): upload a floor plan sheet, confirm the
building area and the scale, and get rooms, walls, openings, stairs and the gross floor area (GF) as JSON, Excel,
DXF, IFC or CSV (a PDF report is planned). Public instance (prototype): [plan-extract.ch](https://plan-extract.ch).

## Run locally

From the repository root:

    python app/server.py          # http://127.0.0.1:8765/   (--port N; --warm loads OCR and model at start)

Needs the pipeline's [requirements](../pilot/v2-pipeline/scripts/requirements.txt) and the segmenter
`pilot/v2-pipeline/data/model-v3/segmenter.pt` (gitignored, or set `V2_MODEL`); the upload page shows a pipeline
check. Locally everything stays on this machine, in `app/data/` (gitignored). For a public server (`--public`: no
sheet list, sheets deleted an hour after their last request) see [deploy/README.md](deploy/README.md).

## Workflow

1. **Upload**: the sheet is analysed in one go, its layout and the scale cues of its floor plans (10 to 30 s).
2. **Building area**: confirm or adjust the detected floor plan.
3. **Scale**: check the proposal, or measure a known distance.
4. **Results**: the extraction runs (about a minute per drawing); review the rooms, download the exports.

## Code

    server.py      HTTP server (standard library): the page, the JSON API, two lanes of pipeline work (worker.py: the processes of the heavy lane)
    bridge.py      the only module that calls the pipeline
    index.html     the page; js/ (ES modules, no build step), css/
    i18n.json      interface texts in German (default), French, Italian, English; ?lang= or the language menu
    openapi.json   the API, shown at /api/docs; /api/v1 is a batch preview that needs a token (401 for now)
    demo/          the demo sheet
    media/         the explainer film of the landing page: how-it-works.html (the film as a page), render.py (-> how-it-works-<lang>.webm and a poster)
    deploy/        setup for a public server (nginx, systemd, HTTPS)
    tests/         python -m pytest app/tests -q   (stub pipeline; the browser test needs Playwright)

## Limits

- Two lanes: the analysis of an upload and the scale step on a thread of the server; extractions and the read-ahead of the drawing text on FPX_APP_WORKERS worker processes (0: a thread of the server), so a newcomer's sheet is analysed while others are extracted and two sheets are extracted at once. A waiting job is told its place and the expected wait.
- No accounts or login yet; on the public server a visitor reaches only the sheets of their own browser.
- The building area is a rectangle; rooms can be marked as checked, not edited. DWG needs a converter (DXF works).
