"""Metre-based planar geometry shared by validation and the radio simulator."""

Point = tuple[float, float]


def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Closed segment intersection, including collinear overlap; gaps are absent segments."""

    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return all(min(p[i], r[i]) - 1e-9 <= q[i] <= max(p[i], r[i]) + 1e-9 for i in (0, 1))

    v = [cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)]
    if v[0] * v[1] < -1e-12 and v[2] * v[3] < -1e-12:
        return True
    return any(
        abs(o) < 1e-9 and on_segment(p, q, r)
        for o, p, q, r in [(v[0], a, c, b), (v[1], a, d, b), (v[2], c, a, d), (v[3], c, b, d)]
    )


def rectangle_bounds(center: Point, size: Point) -> tuple[float, float, float, float]:
    return (
        center[0] - size[0] / 2,
        center[1] - size[1] / 2,
        center[0] + size[0] / 2,
        center[1] + size[1] / 2,
    )


def segment_in_regions(a: Point, b: Point, regions) -> bool:
    """Cover the *entire* segment with a union of rectangles, without sampled shortcuts."""
    intervals = []
    for region in regions:
        bounds = rectangle_bounds(region.center, region.size)
        lower, upper = 0.0, 1.0
        for axis in (0, 1):
            delta = b[axis] - a[axis]
            if abs(delta) < 1e-12:
                if not bounds[axis] - 1e-9 <= a[axis] <= bounds[axis + 2] + 1e-9:
                    upper = -1.0
                    break
            else:
                ends = sorted(
                    ((bounds[axis] - a[axis]) / delta, (bounds[axis + 2] - a[axis]) / delta)
                )
                lower, upper = max(lower, ends[0]), min(upper, ends[1])
        if lower <= upper + 1e-9:
            intervals.append((lower, upper))
    covered = 0.0
    for lower, upper in sorted(intervals):
        if lower > covered + 1e-9:
            return False
        covered = max(covered, upper)
        if covered >= 1 - 1e-9:
            return True
    return False
