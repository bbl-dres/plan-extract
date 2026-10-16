# Pilot v1: Classical CV and a VLM

*October 2026 · Landgut Lohn, 1. OG · tests the extraction modes from [motivation and goals](../../docs/motivation-goals.md) · continued in [pilot v2](../v2-pipeline/README.md)*

## Question

Can a VLM, classical CV, or both together extract room outlines, room attributes and openings from a clean CAD print and from a historical scan in a different drawing style?

## Data

| Sheet | Input | Style |
|---|---|---|
| S1 | 2005 vector PDF, 1:100 printed reduced; rendered at 400 dpi, text layer ignored | Outlined walls, door and window swings, room stamps with names and areas |
| S2 | Historical scan 0056, 1:50, 400 dpi, 1-bit | Solid black (poché) walls, windows as gaps, no door swings, calligraphic labels, no areas |
| S3 | Later survey scan 0066, 1:50, 400 dpi, 1-bit | Not processed in this pilot |

**Reference:** the curated 2005 topology of the floor: 15 rooms (name, stamp area, polygon), 25 walls and 37 openings (24 exterior, 13 interior), in plan metres.

BBL plans are non-public: they and everything derived from them stay in `data/` (gitignored). This README contains room names and areas from those plans; check it before publishing the repository.

## Method

- **Mode A (whole page):** the VLM (Claude, cloud) reads a 1500 px image of the whole sheet.
- **Mode B (native resolution):** the VLM reads a 400 dpi crop of the main building.
- **Mode C (hybrid):** classical CV proposes room polygons (wall-thick strokes only, door gaps closed by erosion, regions regrown by watershed). The VLM names, merges or rejects the numbered candidates, and each stamp area is checked against its polygon area.

## Results

Rooms and walls from mode C; names, stamp areas and openings from mode B.

| | S1 (CAD print) | S2 (poché scan) |
|---|---|---|
| Walls IoU vs 2005 | 0.61 | 0.36 |
| Rooms matched (IoU ≥ 0.5) | 14/15 | 6 unchanged rooms, IoU 0.82–0.90 |
| Mean room IoU | 0.76 | – |
| Median area error vs stamp | 14.7% (3.8% with a 0.10 m snap to the wall face) | – |
| Room names (VLM) | 15/15 | 11/11 |
| Stamp areas read (VLM) | 14/15 | no areas on the sheet |
| Exterior / interior openings found | ~22/24, 12/13 | – |

## Findings

1. **Semantics work.** At native resolution the VLM read every room name, calligraphy included, and 14 of 15 stamp areas (the miss was overprinted by a symbol). This is the part the commercial tool in the BBL test (AmpliFY) failed.
2. **Geometry needs pixel-precise CV plus snapping.** Large rooms reach IoU 0.85–0.94 but stop at the inner edge of the wall pen and come out 5–14% too small; areas are reporting-grade only after snapping to the wall face (median error 14.7% → 3.8%).
3. **The stamp-area check works as QA, and labelling a numbered overlay is cheap.** One VLM pass fixed the merges, rejected 14 fragments and named every room.
4. **Openings without door leaves break room separation.** On S2, stair, vestibule and corridors merge into one region; stairs also fall apart into flights, landings and void.
5. **Older plans show older buildings.** S2's title-block scale was within 1.6% of the reference, but its room layout predates 2005, so historical sheets need plan-vs-reality checks.

## Caveats

- One building, one floor, two sheets. Parameters were tuned on the test sheets themselves, so the results are optimistic.
- The VLM had seen the 2005 room names beforehand, so the S1 readings may be optimistic; the historical names were not known.
- The VLM was a cloud model: the plans were sent to Anthropic's API at the user's request. A production pipeline would use a self-hosted model.
- The reference itself deviates from its stamp areas (up to +29% for the stair with its void).

## Next steps

1. **Graph pipeline:** done in [pilot v2](../v2-pipeline/README.md), with a segmenter trained on style-randomised public data, also on S3.
2. **Self-hosted VLM:** repeat the labelling with a local model (e.g. Qwen3-VL) and compare it with the cloud VLM.
3. **Vector reference:** use the 2005 DXF (in `data/inputs`) as an exact reference (needs ezdxf).

## Run

Everything runs from `scripts/` (`cd pilot/landgut-lohn-og1/scripts`). Plans go in `data/inputs/`, the reference in `data/reference/`; outputs are written to `data/out/`. Python 3.14 with pymupdf, opencv-python, numpy, shapely and pillow.

    python 00_reference_summary.py    # reference rooms, stamp areas, openings
    python 01_render.py               # render S1 at 400 dpi, crop, previews of all sheets
    python 02_segment_2005.py         # mode C geometry on S1
    python 03_score_hybrid_2005.py    # mode C on S1 with the VLM labels
    python 04_segment_scan_0056.py    # mode C on S2
    python 05_export_viewer.py        # -> data/out/viewer-data.js

The VLM readings (labels, merges, rejects, stamp areas) are recorded as data in scripts 03 and 04.

**Viewer:** [`viewer.html`](viewer.html) overlays S1 and S2 with the extracted rooms and walls in 2D/3D, coloured by room, confidence or IoU against the reference. Open it directly from disk (the data file embeds the images); three.js loads from a CDN, so it needs internet.
