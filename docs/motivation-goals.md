# Motivation and Goals

*BBL · Why BBL explores computer-vision extraction of floor plan data, what it wants to achieve, what has been learned so far, and the recommended route. Bracketed numbers refer to the shared [source list](papers/sources.md).*

## 1. Background

The Federal Office for Buildings and Logistics (BBL) manages the real estate of the Swiss civil federal administration: roughly 3,000 buildings and some 6 million m², from administrative and court buildings to customs facilities, embassies, research institutes and cultural buildings.\[94\] Its area management, run in the ERP, needs for every floor the room outlines with a unique room ID (AOID), usage and area, and the gross floor area (GF). These figures feed net-zero reporting, portfolio management, maintenance, transactions and workspace planning.

For many buildings this data is missing, outdated or of unknown quality, for two reasons:

- **The archive is large and unstructured.** BBL surveys buildings regularly, but cannot survey all of them. At the same time it holds, and keeps receiving, a great deal of documentation: plans as scans, PDFs and DWGs, construction-project deliveries, building documentation. Most of it follows no common format or drawing style and is hard to process by hand.
- **New deliveries are hard to get right.** Delivery teams must supply DWGs that follow BBL's CAD guideline, with fixed layers for room polygons and AOIDs. Compliance is checked automatically, but many teams find the requirements hard to meet, which slows the whole data flow.

The market offers no suitable solution (§4). This motivates an in-house approach: extract structured floor data directly from plans as they are archived or delivered, using computer vision (CV) where no clean CAD data exists.

## 2. Goals

1. **Unlock the archive.** Turn existing documentation into structured floor data for buildings that are not surveyed: rooms, areas, GF, room connections. Use it to fill gaps in the ERP and to decide where a survey is most needed.
2. **Relieve delivery teams.** Accept plans in the form they are produced (construction drawings, PDFs, DWGs drawn for print) and generate the CAD-guideline layers automatically, so that people only correct what the automated check flags.
3. **Keep the data trustworthy.** Every extracted value carries its source and a confidence; nothing is filled in silently. Plans are not the building, and recorded areas are often entered by hand, so results are checked against the ERP, other documents or the site, and a person confirms what is uncertain.

**What to extract**, in order of priority. Every element maps to a class of the Industry Foundation Classes (IFC) so that it can be exported; the mapping is part of the [pipeline design](pipeline.md).

- **Rooms (core):** the outline and the tag content (name or usage, AOID or room number, category, height, materials); net and gross area from the geometry next to the tagged area.
- **Annotations:** the title block (building, storey, plan type, scale, date, revision) and other information drawn on the sheet, such as legends, the north arrow, dimensions and notes.
- **Doors and windows:** all types, including empty openings, and the rooms each connects or serves.
- **Walls:** exterior or interior; load-bearing where evident.
- **Structure and circulation:** columns, beams where drawn, stairs, ramps.
- **Voids in the floor:** shafts, staircase openings, air spaces, light wells, for the GF deduction.
- **Building systems and furniture:** elevators, escalators and other large components; furniture where drawn.
- **Derived per floor:** GF (required), room connections, and proposals for the energy reference area (EBF) and zones.

**Outputs.** Structured data following the [data model](pipeline.md#4-data-model), CAD files on the guideline layers, IFC, and reports (PDF and spreadsheet: room list, floor figures, openings, QA).

**Use cases.**
- Area quantities for reporting, e.g. net zero.
- Cost estimation for facility management: cleaning, maintenance.
- Visualisation and workspace management.
- Simulations of health in the workspace: visibility, acoustics, accessibility, daylight.

**Inputs.** Whatever users upload: raster images (also scans and photos), PDFs (vector, raster or both), and DWGs. Construction drawings and facility-management plans dominate; historical plans follow no standard at all.

**Constraints.**
- Mostly office, administrative and historical buildings; few apartments.
- Internal plans are processed in Switzerland only; self-hosted, open-source components are preferred, and BBL publishes its own code as open source (Art. 9 EMBAG).
- Results feed the existing ERP-based area management; no second area-management system.
- Procurement follows public procurement law.

**Out of scope.** BIM authoring and digital-twin products; IFC is an export next to CAD, not an authoring target. Complete furniture inventories and building-condition assessment.

## 3. What We Have Learned

- **The in-house route works.** Pilots show that a segmentation model trained only on public data, rendered in varied drawing styles, reads plans it has never seen: it finds walls, doors and rooms with areas close to the reference, and the room names are read locally. Training such a model is cheap and needs no proprietary data.
- **Uploads can be handled as they come.** One normalisation step turns any image, PDF or CAD file into a page image plus the vector data where it exists. A layout step finds each drawing on the sheet, its title block and its scale ([pipeline design](pipeline.md)).
- **Construction drawings are the main gap.** Published models lose much of their accuracy when moved from simple to dense, construction-style drawings, because hatches, dimension lines, axes and text look like walls. Facility-management plans are a third style that no study covers. The cure is training data in these styles.
- **No public training data fits.** The large research datasets are non-commercial, and none contains Swiss construction or FM plans. BBL's own compliant DWGs are the best training source: rendered in many styles they give exact labels for rooms, AOIDs, GF and walls.
- **Some outputs are derived, not drawn.** GF and room connections follow from walls, rooms and doors. EBF and zones depend on information plans rarely show, so the pipeline proposes them and a person confirms.
- **Language models read but do not measure.** Vision-language models read text well but count elements poorly and estimate areas badly.\[55\] They suit reading room stamps and title blocks, not geometry.
- **Recorded figures need care.** Areas in documentation and registers are often entered by hand and not derived from the geometry. A deviation between an extracted and a recorded area is a finding in its own right, not automatically an extraction error.

## 4. Market

- **No product covers the chain** from archive plans to the ERP. AI converters are mostly cloud services hosted abroad, and vendor accuracy figures are unverified.
- **Full products** would create a second area-management system without ERP integration; conversion-only services are rare.
- **On-premise options** are few and can be tested on non-sensitive plans.
- **Digitising services** remain the pragmatic choice for the worst scans, and laser scanning on site where no usable plan exists.

The individual products and services are catalogued in [closed solutions](solutions-closed.md), [open solutions](solutions-open.md) and BBL's market screening.\[1\]

## 5. Recommended Route and Next Steps

1. **Inventory the archive.** Scan it automatically per building and sheet: format, check result, drawing type (construction, FM, historical), quality, drawings per sheet, scale cues. This sizes the work, shows which buildings have usable documentation, and points surveys to where documentation is missing.
2. **Use compliant DWGs twice.** Read them directly (exact), and render them in many styles as training data.
3. **Extend the training data to construction and FM styles.** Add dimension chains, axes, tags, material hatches, usage colours and full sheets to the renderer, draw training plans from BIM models, and label a sample of BBL sheets for evaluation and fine-tuning.
4. **Run everything else through the pipeline** ([pipeline design](pipeline.md)): normalise, find the drawings and their scale, extract walls, doors, windows and rooms, read the title block and the room stamps, derive GF and connections, check plausibility.
5. **Write the guideline layers and validate them automatically**; people correct only what is flagged, and their corrections become training data.
6. **Compare with the ERP and prioritise.** Flag rooms and floors where extracted and recorded data disagree, and feed the list into survey planning.
7. **Measure what matters:** human correction time per sheet, per drawing type and style, on a stratified sample of BBL sheets ([evaluation plan](pipeline.md#8-evaluation)).
8. **Decide the open points:** a DWG converter with a licence that fits federal use; a legal review of training data and pretrained weights; the budget for a correction tool and self-hosted GPUs.

**Open challenges** beyond these steps: plans that differ from the built state; old scans, hand annotations and overlays; columns, stairs and voids; linking storeys and sheets of one building; licence-clean pretrained weights. The [literature review](literature-review.md) tracks them.

## 6. Documents

- [Pipeline design](pipeline.md): the canonical target, stage by stage, with data model and evaluation plan
- [Literature review and state of the art](literature-review.md): methods, models, benchmarks and datasets
- [Open solutions](solutions-open.md) and [closed solutions](solutions-closed.md): what can be used or bought
- Datasets: [open](datasets-open.md), [open building documentation](datasets-open-docu.md) and [closed](datasets-closed.md)
- [Research reports](reports/README.md): findings, recommendations and their evidence
- [Pilots](../pilot/): the extraction pipeline tried on real plans
