# Pilot v2: Graph pipeline with a trained segmenter

*October 2026 · Landgut Lohn, 1. OG, same floor as [pilot v1](../landgut-lohn-og1/README.md) · stages 0–10 of the [pipeline design](../../docs/pipeline.md) · full measurements in the internal second review*

## Question

Can a segmenter trained only on public data, rendered in random drawing styles, drive the structural pipeline on real BBL sheets it has never seen?

## Data

| Sheet | Input | Style |
|---|---|---|
| S1 | 2005 vector PDF, 1:100 printed reduced | Outlined walls, door and window swings, room stamps with names and areas |
| S2 | Historical scan 0056, 1:50, 400 dpi, 1-bit | Solid black (poché) walls, windows as gaps, no door swings, calligraphic labels, no areas |
| S3 | Later survey scan 0066, 1:50, 400 dpi, 1-bit | Outlined walls with reveals and pilasters, ceiling stucco drawn inside rooms, almost no labels |

**Reference:** the curated 2005 topology of the floor: 15 rooms (name, stamp area, polygon), 25 walls and 37 openings (24 exterior, 13 interior), in plan metres. S2 and S3 show earlier states of the building, so only unchanged elements (outline, exterior openings, some rooms) are comparable.

BBL plans are non-public: they and everything derived from them stay in `data/` (gitignored) and were processed locally only; training used public data only. This README and `evaluate.py` contain room names and areas from those plans; check them before publishing the repository.

## Method

- **Training data:** [Swiss Dwellings v3.0.0](../../data/README.md) (CC BY 4.0), one floor per plan, split by site, plus storeys of public IFC models.
- **Renderer:** every training sample is drawn on the fly in a random style: walls, doors, windows, stairs, fixtures, room stamps in several lettering styles, dimension chains, scan defects and construction-drawing clutter.
- **Pipeline:** stages 0–10: triage, scale, layout masking, text (PDF text and RapidOCR), segmentation, wall graph, openings, stairs and voids, rooms, stamps, areas and connections, QA, and export to JSON, DXF, Excel and IFC 4.3. One module per stage in [`fpx/`](scripts/fpx/__init__.py), every threshold with its reason in [`fpx/config.py`](scripts/fpx/config.py). Local CPU, 40–75 s per sheet.
- **Evaluation:** against the 2005 reference, scans registered by translation only; one scorer (`fpeval/`) for every benchmark.

### Model

- **Architecture:** U-Net with a ResNet-34 encoder (ImageNet initialisation), 24.4 M parameters. Six classes (background, wall, door, window, column, stairs) plus heads for room interior, room boundaries, voids, text and door swings. RGB input at 50 px/m, inferred on 1,024 px tiles.
- **Training:** 30,000 iterations × 16 crops of 512 px, 52 minutes on an RTX 4090 (RunPod, about USD 0.65). No BBL plan and no benchmark image was used for training or model selection.
- **Weights** (gitignored): v3, the default (`common.MODEL`): `data/model-v3/segmenter.pt`, SHA-256 `11fbeb97de291ffc…`; v2: `data/model-v2/`, `ac962255c8597731…`; v1: `data/model/`, `0d14e5a5e8d710a6…`.
- **Intended use:** research on BBL archive plans, every result checked by QA and a person. Not for sections, elevations or site plans.
- **Open licence question:** ImageNet-pretrained encoder weights in production; the fallback is a licence-clean encoder or training from scratch.

## Results

Model v3:

| | S1 (CAD print) | S2 (poché scan) | S3 (survey scan) |
|---|---|---|---|
| Walls IoU vs 2005 | 0.86 | 0.61 | 0.59 |
| Rooms matched (IoU ≥ 0.5) | 15/15 | 12/15 | 13/15 |
| Mean room IoU | 0.92 | 0.74 | 0.78 |
| Median area error vs stamp | 2.8% | 6.7% | 4.4% |
| Room names (local OCR) | 15/15 | 9/11 with a name vocabulary (raw 0/11) | no names on the sheet |
| Exterior / interior openings found | 24/24, 10/13 | 24/24, 10/13 | 24/24, 12/13 |
| Room connections found | 10/12 | 7/12 | 8/12 |
| GF outline IoU | 0.91 | 0.91 | 0.91 |

For comparison on S1: pilot v1 reached walls IoU 0.61, mean room IoU 0.76 and a median area error of 14.7%; model v2 reached walls IoU 0.87, mean room IoU 0.89, 2.7% and 12/12 connections. Model v3 misses four doors on S1 that v2 found.

### Public benchmarks

All model rows are zero-shot (trained on renders only); published rows are in-domain unless marked. Benchmark data is used for evaluation only and its outputs stay local. Protocols and full tables: internal second review §4.4 and [published baselines](../../docs/reports/2026-10-07-published-baselines.md).

| Benchmark | Metric | Published | Model v3 |
|---|---|---|---|
| CubiCasa5K, 400 test plans | pixel IoU wall / door / window | 0.730 / 0.536 / 0.668 | 0.706 / 0.551 / 0.649 |
| FloorPlanCAD, Nov 2021 test split | length-weighted F1 (door, window, stair, wall) | 0.73–0.81 | 0.321 |
| WAFFLE, 110 annotated plans | wall precision / recall / IoU | 0.746 / 0.805 / 0.632 (zero-shot) | 0.823 / 0.761 / 0.654 (first 40 plans) |
| CVC-FP, 122 scans | wall IoU | 89.2% (5-fold), 64.7% cross-dataset | 78.1% |
| Swiss Dwellings renders, 117 held-out floors | rooms recall / precision, wall IoU, door / window F1 | – | 0.884 / 0.952, 0.90, 0.93 / 0.93 |

## Findings

1. **Style randomisation transfers.** Trained only on rendered Swiss Dwellings floors, the model segments three unseen drawing styles: walls IoU 0.86 on the CAD print and 0.59–0.61 on the scans (pilot v1: 0.61 and 0.36).
2. **The graph pipeline fixes v1's main failures.** Doors and windows close rooms, so rooms no longer merge through doorways. Rooms are bounded by the wall faces, so areas are within 3% (median) of the stamps on S1 without a snapping step. Room connections come as a by-product.
3. **Local OCR reads printed stamps, not calligraphy.** It read all 15 printed names and 12 of 15 areas on S1; on S2 no name is exact, and a name vocabulary repairs 9 of 11. A self-hosted VLM is the candidate for hand lettering.
4. **Layout masking before segmentation pays off.** Masking title blocks, notes and scan borders halved S3's false wall area.
5. **Synthetic validation does not pick the best checkpoint for real sheets.** A small real validation set is the missing piece.
6. **Weak spots:** construction drawings (FloorPlanCAD 0.32: dimension chains, hatches and furniture read as walls), columns (CubiCasa IoU 0.10) and open-plan areas without a drawn dividing line.

## Caveats

- One building, one floor, three sheets. The model never saw them, but several post-processing rules were written after inspecting failures on these sheets, so the results are optimistic until repeated on held-out BBL sheets. The public benchmarks and the curated set are the check.
- The name vocabulary was written after seeing S2; the raw OCR score (0/11) is the honest one.

## Next steps

1. **Real validation set:** label 10–20 BBL sheets from other buildings and styles for checkpoint and threshold selection, then rerun this pilot unchanged as a held-out test.
2. **Doors on CAD prints:** recover the four S1 doors v3 lost; check the casement/door renderer change against real casement windows before the next training run.
3. **Open plan without a drawn line:** split areas by their stamps, or train the boundary head on more open-plan renders.
4. **Stamps on historical plans:** compare a self-hosted VLM with OCR plus vocabulary, entirely locally.
5. **DWG export:** write DWG (e.g. through ODA) and validate it with plan-check.

## Run

Everything runs from `scripts/` (`cd pilot/v2-pipeline/scripts`). Models and outputs are written to `data/`. Environment: [requirements.txt](scripts/requirements.txt).

    python sd_prepare.py              # Swiss Dwellings -> training floors
    python train.py --iters 30000 --batch 16 --workers 14 --require-cuda --negatives --val-cache ../data/val-v3neg.pt --out ../data/model-v3   # GPU
    python run_pipeline.py            # stages 0-10 on S1-S3
    python evaluate.py                # score against the 2005 reference
    python curated.py run core        # the curated viewer set
    python export_viewer.py           # -> viewer-data/
    python -m pytest -q tests

Scripts share `--model`, `--out`, `--config` and `--threads`. Training ablations: [experiments.md](scripts/experiments.md).

**Viewer:** [`viewer.html`](viewer.html) shows the 24 core plans of the [curated set](../../data/curated/README.md) and the three Landgut Lohn sheets in 2D/3D, each with its pipeline versions. Serve it over HTTP (`python -m http.server` in this folder) or open it on GitHub Pages; `export_viewer.py --local` adds the benchmark-only plans, which `viewer.html#data=local` reads.
