"""Visible pixels, not projected bounding boxes. Thresholds are diagnostic only."""
from PIL import Image, ImageChops, ImageDraw


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _edges(points):
    return list(zip(points, points[1:]+points[:1]))


def _intersects(a, b, c, d):
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]): return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]): return False
    return _cross(a,b,c)*_cross(a,b,d) <= 0 and _cross(c,d,a)*_cross(c,d,b) <= 0


def validate_polygon(points, box):
    x, y, w, h = box
    if any(not (x <= a <= x+w and y <= b <= y+h) for a, b in points):
        raise ValueError("visible polygon must be inside its native reference region")
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError("polygon vertices must be distinct; do not repeat the closing vertex")
    edges = _edges(points)
    area = abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in edges))/2
    if area < 1:
        raise ValueError("visible polygon must have at least one square pixel of area")
    for i, (a,b) in enumerate(edges):
        for j in range(i+1, len(edges)):
            if j == i+1 or (i == 0 and j == len(edges)-1): continue
            if _intersects(a,b,*edges[j]):
                raise ValueError("visible polygon must be simple, without self intersections")


def _point_inside_polygon(point, polygon):
    x, y = point
    inside = False
    for (x1,y1),(x2,y2) in _edges(polygon):
        # Holes touching an outer edge are ambiguous notches, not holes.
        if abs(_cross((x1,y1),(x2,y2),(x,y))) < 1e-9 and min(x1,x2) <= x <= max(x1,x2) and min(y1,y2) <= y <= max(y1,y2):
            return False
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y-y1)*(x2-x1)/(y2-y1)
            if crossing_x > x:
                inside = not inside
    return inside


def validate_hole(points, outer_polygons, box):
    """Require each subtraction polygon to be strictly contained in one outer polygon."""
    validate_polygon(points, box)
    if not outer_polygons:
        raise ValueError("visible hole requires an outer visible polygon")
    hole_edges = _edges(points)
    for outer in outer_polygons:
        outer_edges = _edges(outer)
        if (all(_point_inside_polygon(point, outer) for point in points)
                and not any(_intersects(a,b,c,d) for a,b in hole_edges for c,d in outer_edges)):
            return
    raise ValueError("visible hole must be strictly contained in one outer visible polygon")


def visible_metrics(plan, analysis, id_path, encoding):
    if encoding != "rgb-index-v2-no-msaa":
        return {"available": False, "reason": "Recapture using the exact non-antialiased ID pass", "requires_manual_review": True}
    sx, sy, sw, sh = analysis["scene_box"]
    size = (int(sw+.5), int(sh+.5))
    with Image.open(id_path) as opened:
        if opened.size != size:
            raise ValueError("ID evidence dimensions differ from native scene crop")
        rgb = opened.convert("RGB")
    r, g, labels = rgb.split()
    histogram = labels.histogram()
    if r.getextrema()[1] or g.getextrema()[1] or any(histogram[len(plan["objects"])+1:]):
        raise ValueError("ID evidence contains unknown object colors")
    results = []
    for region in analysis["regions"]:
        indices = {i+1 for i,n in enumerate(plan["objects"]) if region["id"] in n["region_ids"]}
        actual = labels.point([255 if i in indices else 0 for i in range(256)])
        bounds = actual.getbbox()
        count = actual.histogram()[255]
        item = {"region_id": region["id"], "critical": region["critical"],
                "visible_pixels": count, "visible_box": list(bounds) if bounds else None,
                "box_format": "left,top,right,bottom in captured scene pixels",
                "silhouette_iou": None, "requires_manual_review": True,
                "source_hole_count": len(region.get("visible_holes", []))}
        polygons = region.get("visible_polygons", [])
        holes = region.get("visible_holes", [])
        if not polygons:
            item["reason"] = "missing_original_visible_silhouette"
        elif region["confidence"] < .8:
            item["reason"] = "low_confidence_original_annotation"
        else:
            expected = Image.new("L", size)
            draw = ImageDraw.Draw(expected)
            transform = lambda p: ((p[0]-sx)*size[0]/sw, (p[1]-sy)*size[1]/sh)
            for polygon in polygons:
                draw.polygon([transform(point) for point in polygon], fill=255)
            for hole in holes:
                draw.polygon([transform(point) for point in hole], fill=0)
            intersection = ImageChops.darker(actual, expected).histogram()[255]
            union = ImageChops.lighter(actual, expected).histogram()[255]
            item.update(silhouette_iou=intersection/union if union else 0,
                        target_pixels=expected.histogram()[255],
                        reason="diagnostic_threshold_not_calibrated",
                        below_provisional_target=(intersection/union if union else 0) < .85)
        if not count and region["critical"]:
            item["reason"] = "critical_region_not_visible"
        results.append(item)
    return {"available": True, "measurement": "visible ID pixel union vs original annotated positive polygons minus validated visible holes",
            "regions": results, "requires_manual_review": True, "acceptance_supported": False}
