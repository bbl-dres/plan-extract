"""Stage 8b, the wall graph as a planar partition of the floor: the wall centre lines, the bridged gaps, the open-plan
separators and the floor outline joined into one graph whose faces are the rooms.

A room then has three polygons: the accurate one traced from the pixels (stage 6), the gross one bounded by the wall
centre lines (its face), and the simplified net one bounded by the wall finish faces (the face minus the wall bands,
straight edges, a handful of vertices). Gross areas sum to the floor area GF apart from the faces no room owns.

Everything here is geometry in one unit (working pixels in the pipeline; the tests and tools may pass metres): edges
are {"id", "line" (LineString), "thickness", "kind"} with kind "wall", "bridge", "separation" or "outline" (thickness 0
for the last two); rooms are {"id", "poly"}; the floor outline is a Polygon. build() returns the faces and writes the
results on the rooms and the edges (see the function).
"""
import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

WALL_KINDS = ("wall", "bridge")


def _ends(line):
    c = np.asarray(line.coords)
    return c[0], c[-1]


def _node_point(members, lines, ends, reach):
    """Where the ends of a cluster meet: the point nearest to all their axes (the intersection of two, the
    least-squares point of three or more), so that every axis keeps its direction; the mean of the ends when the
    axes are parallel or the point lands farther than `reach` from the mean."""
    pts = np.array([lines[ends[a][0]][ends[a][1]] for a in members])
    mean = pts.mean(axis=0)
    A, b = np.zeros((2, 2)), np.zeros(2)
    for a in members:
        i, k = ends[a]
        c = lines[i]
        p, q = (c[0], c[1]) if k == 0 else (c[-1], c[-2])
        d = q - p
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            continue
        d = d / L
        P = np.eye(2) - np.outer(d, d)
        A += P
        b += P @ p
    try:
        if np.linalg.cond(A) > 1e4:
            return mean
        x = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return mean
    return x if float(np.linalg.norm(x - mean)) <= reach else mean


def snap_ends(edges, reach, extend, rooms=()):
    """Join the graph where the segments stop short. Wall ends within `reach` of each other form one node (all of
    them move to the point where their axes meet, so a three-way junction stays one point and a wall keeps its
    direction); a lone end within `reach` of another edge
    moves onto it; a dangling end is extended along its own direction to the first edge within `extend` unless that
    extension cuts through a room (it stays a stub then). Separation and outline edges are targets, not movers.
    Returns the edges with their lines changed and "snapped", "extended" or "open end" notes."""
    lines = [np.asarray(e["line"].coords, float) for e in edges]
    ends = [(i, k) for i, e in enumerate(edges) if e["kind"] in WALL_KINDS for k in (0, -1)]
    pts = [Point(lines[i][k]) for i, k in ends]
    # 1. clusters of wall ends within reach: one node each
    parent = list(range(len(ends)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    ptree = shapely.STRtree(pts)
    edges_of = {a: {ends[a][0]} for a in range(len(ends))}   # the edges whose ends a cluster holds: never both ends of one edge
    for a, p in enumerate(pts):
        for b in ptree.query(p.buffer(reach)):
            b = int(b)
            if b == a or p.distance(pts[b]) > reach:
                continue
            ra, rb = find(a), find(b)
            if ra == rb or edges_of[ra] & edges_of[rb]:   # a short edge between two walls would collapse to a point
                continue
            parent[ra] = rb
            edges_of[rb] |= edges_of.pop(ra)
    groups = {}
    for a in range(len(ends)):
        groups.setdefault(find(a), []).append(a)
    lone = []
    for members in groups.values():
        if len(members) == 1:
            lone.append(members[0])
            continue
        c = _node_point(members, lines, ends, reach)
        for a in members:
            i, k = ends[a]
            lines[i][k] = c
            edges[i].setdefault("notes", []).append("snapped")
    tree = shapely.STRtree([LineString(l) for l in lines])
    interiors = unary_union([r["poly"].buffer(-reach) for r in rooms if r["poly"].area > 0]) if rooms else None
    for a in lone:
        i, k = ends[a]
        p = lines[i][k]
        # 2. the interior of another edge within reach: the foot of the perpendicular
        best, bd = None, reach
        for j in tree.query(Point(p).buffer(reach)):
            j = int(j)
            if j == i:
                continue
            d = LineString(lines[j]).distance(Point(p))
            if d < bd:
                best, bd = j, d
        if best is not None:
            other = LineString(lines[best])
            foot = np.asarray(other.interpolate(other.project(Point(p))).coords[0])
            own = lines[i][-1] if k == 0 else lines[i][0]
            if np.linalg.norm(foot - own) >= max(2.0, 0.25 * float(np.linalg.norm(p - own))):   # never onto its own other end
                lines[i][k] = foot
                edges[i].setdefault("notes", []).append("snapped")
                continue
        # 3. a dangling end: extend along the segment to the first edge within `extend`
        a_, b_ = (lines[i][-1], lines[i][0]) if k == 0 else (lines[i][0], lines[i][-1])
        d = b_ - a_
        n = float(np.linalg.norm(d))
        if n < 1e-6:
            continue
        d /= n
        ray = LineString([b_, b_ + d * extend])
        hit, hd = None, extend
        for j in tree.query(ray):
            j = int(j)
            if j == i:
                continue
            x = ray.intersection(LineString(lines[j]))
            for g in getattr(x, "geoms", [x]):
                if g.geom_type != "Point":
                    continue
                dd = float(np.linalg.norm(np.asarray(g.coords[0]) - b_))
                if 1e-6 < dd < hd:
                    hit, hd = np.asarray(g.coords[0]), dd
        if hit is None:
            edges[i].setdefault("notes", []).append("open end")
            continue
        piece = LineString([b_, hit])
        if interiors is not None and piece.intersection(interiors).length > reach:   # it would cut a room
            edges[i].setdefault("notes", []).append("open end")
            continue
        lines[i][k] = hit
        edges[i].setdefault("notes", []).append("extended")
    for e, c in zip(edges, lines):
        e["line"] = LineString(c)
    return edges


def faces_of(edges, grid):
    """The faces of the graph: every line snapped to a grid of `grid` (so that an end moved onto another line lies on
    it exactly), the linework noded at every crossing, then polygonized."""
    lines = [shapely.set_precision(e["line"], grid) for e in edges if e["line"].length > 0]
    lines = [l for l in lines if not l.is_empty and l.length > 0]
    noded = shapely.node(shapely.MultiLineString(lines))
    return [f for f in polygonize(noded) if f.area > 0]


def build(edges, rooms, outline, *, reach, extend, small, simplify=0.0, columns=(), voids=()):
    """The planar partition: snap the graph, cut its faces, give every room its face. Writes on each room
    "face_gross" (Polygon or None), "face_net" (Polygon or None), "face_flags" (list), and on each edge "rooms"
    (the room ids on its two sides, "outside" or None) and "notes". `reach`, `extend`, `small` (the largest ownerless
    face that joins a neighbour) are lengths and an area in the edges' unit. Returns {"faces", "unowned" (faces no room owns), "band" (the facade's outer
    half, between the exterior axes and the outline), "edges"}."""
    edges = [dict(e) for e in edges]
    if outline is not None and not outline.is_empty:
        edges.append({"id": "outline", "line": LineString(outline.exterior.coords), "thickness": 0.0, "kind": "outline"})
    edges = snap_ends(edges, reach, extend, rooms)
    faces = faces_of(edges, reach / 30)
    tree = shapely.STRtree(faces)
    # which room sits in which face: by the room's representative point, then by the largest overlap
    owner = {}                                              # face index -> [room]
    for r in rooms:
        r["face_gross"], r["face_net"], r["face_flags"] = None, None, []
        if r["poly"].is_empty:
            continue
        hits = [int(j) for j in tree.query(r["poly"].representative_point(), predicate="within")]
        if not hits:
            hits = [int(j) for j in tree.query(r["poly"], predicate="intersects")]
            hits = sorted(hits, key=lambda j: -faces[j].intersection(r["poly"]).area)[:1]
        if hits:
            owner.setdefault(hits[0], []).append(r)
    # faces nobody owns: the band between the exterior wall axes and the outline (the facade's outer half) stays out
    # of every room, since gross areas stop at the axes; a small interior face (a column cell, a sliver) joins the
    # neighbour it shares the longest edge with; a large one (a shaft without a stamp) stays apart and is reported
    gross = {}                                              # room id -> polygon
    for j, rs in owner.items():
        f = faces[j]
        if len(rs) == 1:
            gross[rs[0]["id"]] = f
        else:                                               # a face with several rooms: the drawing shows no divider
            for r in rs:
                part = f.intersection(r.get("poly_gross", r["poly"]))
                part = max(getattr(part, "geoms", [part]), key=lambda g: g.area) if not part.is_empty else part
                gross[r["id"]] = part if part.geom_type == "Polygon" else f.intersection(r["poly"].buffer(reach))
                r["face_flags"].append("face shared: divided along the drawing")
    unowned, band = [], []
    for j, f in enumerate(faces):
        if j in owner:
            continue
        if outline is not None and not outline.is_empty and f.exterior.intersection(outline.exterior).length > 0:
            band.append(f)
            continue
        if f.area > small:
            unowned.append(f)
            continue
        best, bl = None, 0.0
        for rid, g in gross.items():
            L = f.boundary.intersection(g.boundary).length
            if L > bl:
                best, bl = rid, L
        if best is not None:
            gross[best] = unary_union([gross[best], f])
    # net: the face minus the wall bands (and columns and voids inside it), the largest piece, straightened
    bands = unary_union([e["line"].buffer(e["thickness"] / 2, cap_style="flat", join_style="mitre")
                         for e in edges if e["kind"] in WALL_KINDS and e["thickness"] > 0] + list(columns))
    for r in rooms:
        g = gross.get(r["id"])
        if g is None or g.is_empty:
            r["face_flags"].append("no closed face: pixel polygons only")
            continue
        g = g.buffer(0)
        g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
        r["face_gross"] = g.simplify(simplify) if simplify else g
        n = g.difference(bands)
        for v in voids:
            n = n.difference(v)
        n = n.buffer(0)
        if n.is_empty:
            r["face_flags"].append("no net face")
            continue
        n = max(getattr(n, "geoms", [n]), key=lambda q: q.area)
        r["face_net"] = n.simplify(simplify) if simplify else n
    # the rooms on either side of every edge
    by_id = {r["id"]: r["face_gross"] for r in rooms if r.get("face_gross") is not None}
    gtree = shapely.STRtree(list(by_id.values())) if by_id else None
    ids = list(by_id)
    for e in edges:
        if e["kind"] == "outline":
            continue
        line = e["line"]
        if line.length < 1e-6 or gtree is None:
            e["rooms"] = [None, None]
            continue
        mid = line.interpolate(0.5, normalized=True)
        q = line.interpolate(min(0.5 + 2.0 / max(line.length, 4.0), 1.0), normalized=True)
        t = np.asarray(q.coords[0]) - np.asarray(mid.coords[0])
        t /= max(np.linalg.norm(t), 1e-9)
        nrm = np.array([-t[1], t[0]])
        off = e["thickness"] / 2 + reach / 2
        sides = []
        for s in (1, -1):
            p = Point(np.asarray(mid.coords[0]) + s * nrm * off)
            hit = [ids[int(j)] for j in gtree.query(p, predicate="within")]
            sides.append(hit[0] if hit else ("outside" if outline is not None and not outline.contains(p) else None))
        e["rooms"] = sides
    return {"faces": faces, "unowned": unowned, "band": band, "edges": edges}


def vertex_count(poly):
    return 0 if poly is None or poly.is_empty else len(poly.exterior.coords) - 1
