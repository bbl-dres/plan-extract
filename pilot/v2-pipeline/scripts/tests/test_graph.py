"""Stage 8b, the wall graph as a planar partition (fpx.graph): every room gets a closed face, the gross faces add up to
the floor, the simplified net polygons are few-cornered and agree with the pixel ones, the walls know their rooms."""
import numpy as np
from conftest import plan_image as plan, px, two_rooms
from shapely.geometry import LineString, Polygon

from fpx import DEFAULT, graph, pipeline
from fpx.model import DOOR, WALL


def run(sheet):
    pipeline.run(sheet, stages=pipeline.AFTER_SEGMENTATION)
    return sheet


def test_two_rooms_get_their_faces():
    s = run(two_rooms())
    rooms = {r["id"]: r for r in s.rooms}
    assert len(rooms) == 2 and all(r["gross_basis"] == "wall axes" for r in rooms.values())
    # the gross faces stop at the wall axes: with the facade's outer half (the band to the outline) they fill the GF
    band = sum(DEFAULT.m2(f.area) for f in s.wall_faces["band"])
    assert 4.0 < band < 5.5 and s.wall_faces["unowned"] == []
    assert abs(sum(r["area_gross"] for r in rooms.values()) + band - s.gf_area) < 0.02 * s.gf_area
    for r in rooms.values():
        assert graph.vertex_count(r["poly_net_simple"]) <= 6                        # a rectangle, not a traced outline
        assert abs(r["area_net_simple"] - r["area_net"]) < 0.03 * r["area_net"]   # the finish faces agree with the pixels
        assert r["face_flags"] == []
    # the interior wall knows the rooms on its two sides, the facades know the outside
    kinds = {tuple(sorted(str(x) for x in w["rooms"])) for w in s.wall_segments}
    assert tuple(sorted(rooms)) in kinds and any("outside" in k for k in kinds)
    assert all(w.get("length") for w in s.wall_segments)


def test_a_wall_that_stops_short_is_joined():
    # the interior wall ends 0.2 m before the north facade: a skeleton stops half a thickness early anyway
    s = run(plan(lambda r: (r(5.85, 1.5, 6.15, 6.7, WALL), r(5.85, 3.5, 6.15, 4.4, DOOR))))
    assert len(s.rooms) == 2
    assert all(r["gross_basis"] == "wall axes" for r in s.rooms)
    assert any("snapped" in w.get("notes", []) or "extended" in w.get("notes", []) for w in s.wall_segments)


def test_build_on_plain_geometry():
    """The module on its own, in metres: a 4 x 3 m room inside a 0.2 m wall ring and a partition that stops short."""
    ring = [LineString([(0, 0), (4, 0)]), LineString([(4, 0), (4, 3)]), LineString([(4, 3), (0, 3)]), LineString([(0, 3), (0, 0)])]
    edges = [{"id": f"w{i}", "line": l, "thickness": 0.2, "kind": "wall"} for i, l in enumerate(ring)]
    edges.append({"id": "p", "line": LineString([(2, 0.15), (2, 2.7)]), "thickness": 0.1, "kind": "wall"})   # 0.3 m short of the far wall
    rooms = [{"id": "a", "poly": Polygon([(0.1, 0.1), (1.95, 0.1), (1.95, 2.9), (0.1, 2.9)])},
             {"id": "b", "poly": Polygon([(2.05, 0.1), (3.9, 0.1), (3.9, 2.9), (2.05, 2.9)])}]
    outline = Polygon([(-0.1, -0.1), (4.1, -0.1), (4.1, 3.1), (-0.1, 3.1)])
    res = graph.build(edges, rooms, outline, reach=0.25, extend=0.9, small=0.5)
    assert res["unowned"] == [] and all(r["face_gross"] is not None for r in rooms)
    assert len(res["band"]) == 1 and abs(sum(r["face_gross"].area for r in rooms) - 4 * 3) < 0.01
    assert graph.vertex_count(rooms[0]["face_net"]) == 4 and abs(rooms[0]["face_net"].area - 1.85 * 2.8) < 0.02
    p = next(e for e in res["edges"] if e["id"] == "p")
    assert "extended" in p["notes"] and sorted(p["rooms"]) == ["a", "b"]
    assert np.allclose(np.asarray(p["line"].coords)[-1], (2, 3), atol=0.01)
