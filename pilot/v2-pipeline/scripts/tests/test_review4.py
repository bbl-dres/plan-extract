"""The fourth code review (docs/reviews/2026-10-15-app-and-pipeline-code-review-4.md): a closed wall ring gets its
segments, one thickness per wall run with the straight face kept, junctions of the wall graph at the intersection
of the axes, a short wall piece that is not collapsed, a fraction with a zero denominator, distinct OCR cache keys,
an Excel export that survives control characters and never writes a formula."""
import types

import numpy as np
import pytest
from conftest import plan_image as plan, px
from shapely.geometry import LineString

from fpx import DEFAULT, graph, pipeline
from fpx.model import WALL
from fpx.scale import parse_dimension
from fpx.walls import consolidate_runs


def run(sheet):
    pipeline.run(sheet, stages=pipeline.AFTER_SEGMENTATION)
    return sheet


def test_a_closed_wall_ring_gets_its_segments():
    """A ring without a junction or an end (a one-room plan without an opening, a shaft, a chimney) was no wall at all:
    the skeleton graph had nothing to seed from."""
    s = run(plan())                                        # the 10 x 6 m building alone: one closed ring of 0.3 m wall
    assert len(s.wall_segments) >= 4 and len(s.rooms) == 1
    assert all(0.2 <= w["thickness"] <= 0.4 for w in s.wall_segments)
    assert sum(w["line"].length for w in s.wall_segments) / DEFAULT.px(1.0) > 28   # the ring's perimeter at the axis, about 31 m


def segs_of(pieces):
    """Collinear horizontal pieces [(x0, x1, centre y, thickness)] in working px, chained by shared nodes."""
    out = []
    for k, (x0, x1, y, t) in enumerate(pieces):
        out.append({"id": f"w{k}", "line": LineString([(x0, y), (x1, y)]), "thickness": t / DEFAULT.px(1.0), "angle": 0.0, "nodes": (k, k + 1)})
    return out


def test_one_thickness_per_wall_run_keeps_the_straight_face():
    """Three pieces of one wall, the middle one measured thicker (a sill, furniture against the wall): all three take
    the run's thickness, and the middle one moves so that the straight (lower) face stays where it is."""
    segs = segs_of([(0, 100, 20.0, 10), (100, 200, 22.0, 14), (200, 300, 20.0, 10)])   # the lower face at y = 15 on all three
    consolidate_runs(segs, DEFAULT)
    assert [round(s["thickness"] * DEFAULT.px(1.0)) for s in segs] == [10, 10, 10]
    assert segs[1].get("thickness_measured") == pytest.approx(14 / DEFAULT.px(1.0))
    assert segs[1]["line"].coords[0][1] == pytest.approx(20.0, abs=0.01)   # centre back on the run: the lower face stays at 15
    assert "thickness_measured" not in segs[0]


def test_a_real_width_change_keeps_its_own_thickness():
    segs = segs_of([(0, 100, 20.0, 10), (100, 140, 27.0, 24), (140, 300, 20.0, 10)])   # 2.4 x: a chimney breast
    consolidate_runs(segs, DEFAULT)
    assert round(segs[1]["thickness"] * DEFAULT.px(1.0)) == 24 and segs[1]["line"].coords[0][1] == 27.0


def test_junctions_meet_where_the_axes_cross():
    """Two walls that stop short of their corner: the node is the intersection of the axes, so the horizontal wall
    stays horizontal (the mean of the ends would tilt it)."""
    edges = [{"id": "h", "line": LineString([(0, 50), (97, 50)]), "thickness": 10, "kind": "wall"},
             {"id": "v", "line": LineString([(100, 0), (100, 47)]), "thickness": 10, "kind": "wall"}]
    graph.snap_ends(edges, reach=10, extend=30)
    assert list(edges[0]["line"].coords[-1]) == pytest.approx([100, 50]) and list(edges[1]["line"].coords[-1]) == pytest.approx([100, 50])


def test_a_short_wall_piece_between_two_walls_is_not_collapsed():
    edges = [{"id": "s", "line": LineString([(100, 0), (112, 0)]), "thickness": 10, "kind": "wall"},
             {"id": "a", "line": LineString([(100, -50), (100, -3)]), "thickness": 10, "kind": "wall"},
             {"id": "b", "line": LineString([(112, -50), (112, -3)]), "thickness": 10, "kind": "wall"}]
    graph.snap_ends(edges, reach=15, extend=30)
    assert edges[0]["line"].length > 5                     # its two ends were one cluster before: a zero-length wall


def test_a_fraction_with_a_zero_denominator_is_no_dimension():
    assert parse_dimension("6'-6 1/0\"") == []
    assert parse_dimension("6'-6 1/8\"")[0][0] == pytest.approx(6.5104 * 0.3048, rel=0.01)


def test_cache_keys_tell_boxes_apart(tmp_path):
    pkg = types.SimpleNamespace(id="p", img=np.zeros((4, 4, 3), np.uint8))
    a = pipeline._cache_path(tmp_path, pkg, "box100-2000-900-3000", "t")
    b = pipeline._cache_path(tmp_path, pkg, "box100-2000-900-3100", "t")
    assert a != b


def test_excel_takes_control_characters_and_formulas_as_text(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from fpx.xlsx import to_xlsx
    data = {"schema_version": "x", "generator": {"name": "t"}, "sheet": {"scale": {}}, "floor": {}, "voids": [], "walls": [], "qa": [],
            "openings": [], "rooms": [{"id": "r1", "name": "B\x01ro", "area_net": 1.0}, {"id": "r2", "name": '=HYPERLINK("http://x","y")', "area_net": 2.0}]}
    path = tmp_path / "t.xlsx"
    to_xlsx(data, path, DEFAULT)
    ws = openpyxl.load_workbook(path)["Räume"]
    names = {ws.cell(row=r, column=4).value: ws.cell(row=r, column=4).data_type for r in (2, 3)}
    assert "Bro" in names and names['=HYPERLINK("http://x","y")'] == "s"
