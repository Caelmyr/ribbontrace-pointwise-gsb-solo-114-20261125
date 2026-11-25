"""Image-assisted rerouting of one adjacent-point ribbon segment.

The desk's saved points remain the fixed ends of every segment.  This module
finds a centre line in a 56 px-wide corridor around one hand-marked pair and,
when justified, bridges exactly one short occlusion.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from scipy.stats import theilslopes

from image_lab.validation import Invalid


CORRIDOR_HALF_WIDTH = 28
MAX_BRIDGE_PX = 24
API_VERSION = 1


def unavailable(reason: str) -> Invalid:
    """All image decisions which cannot safely continue share one cue."""
    return Invalid(f"还不能续：{reason}")


def _frame(start, end):
    a = np.asarray(start, dtype=float)
    b = np.asarray(end, dtype=float)
    delta = b - a
    length = float(np.hypot(*delta))
    if length < 1:
        raise unavailable("这一对相邻点距离太短")
    along = delta / length
    normal = np.array([-along[1], along[0]])
    return a, b, length, along, normal


def blue_ribbon_mask(image: np.ndarray) -> np.ndarray:
    """Return the cool-colour ribbon mask used by the reroute assistant."""
    rgb = np.asarray(image, dtype=np.uint8)
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)

    # The photographed practice ribbon is teal/blue.  The RGB rule also keeps
    # dark anti-aliased ribbon pixels that a saturation threshold can miss.
    cool = ((b - r) > 16) & ((g - r) > 5) & (b >= g - 34)
    colourful = (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)) > 22
    hue = cv2.inRange(hsv, np.array([82, 28, 28]), np.array([132, 255, 238])) > 0
    mask = ((cool & colourful) | hue).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


def _corridor_data(image, a, b, length, along, normal):
    h, w = image.shape[:2]
    rows = max(4, int(math.ceil(length)) + 1)
    ss = np.linspace(0.0, length, rows)
    offsets = np.arange(-CORRIDOR_HALF_WIDTH, CORRIDOR_HALF_WIDTH + 1, dtype=float)
    grid_s, grid_t = np.meshgrid(ss, offsets, indexing="ij")
    grid_x = (a[0] + along[0] * grid_s + normal[0] * grid_t).astype(np.float32)
    grid_y = (a[1] + along[1] * grid_s + normal[1] * grid_t).astype(np.float32)

    raw_mask = blue_ribbon_mask(image)
    corridor = np.zeros_like(raw_mask)
    plus = np.column_stack((
        a[0] + along[0] * ss + normal[0] * CORRIDOR_HALF_WIDTH,
        a[1] + along[1] * ss + normal[1] * CORRIDOR_HALF_WIDTH,
    )).astype(np.int32)
    minus = np.column_stack((
        a[0] + along[0] * ss - normal[0] * CORRIDOR_HALF_WIDTH,
        a[1] + along[1] * ss - normal[1] * CORRIDOR_HALF_WIDTH,
    )).astype(np.int32)
    boundary = np.concatenate((plus, minus[::-1]))
    cv2.fillPoly(corridor, [boundary], 1)
    mask = (raw_mask & corridor).astype(np.uint8)
    labels_count, labels = cv2.connectedComponents(mask, connectivity=8)

    map_x = grid_x
    map_y = grid_y
    mask_score = cv2.remap(mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    label_grid = cv2.remap(labels.astype(np.float32), map_x, map_y, cv2.INTER_NEAREST,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.int32)
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    distance_grid = cv2.remap(distance, map_x, map_y, cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    rgb_grid = np.stack([
        cv2.remap(image[:, :, c].astype(np.float32), map_x, map_y, cv2.INTER_LINEAR,
                  borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        for c in range(3)
    ], axis=-1)

    endpoint_j = CORRIDOR_HALF_WIDTH
    start_label = int(label_grid[0, endpoint_j])
    end_label = int(label_grid[-1, endpoint_j])
    if start_label == 0:
        start_label = _nearby_label(label_grid[0], endpoint_j)
    if end_label == 0:
        end_label = _nearby_label(label_grid[-1], endpoint_j)

    return {
        "ss": ss, "tt": offsets, "x": grid_x, "y": grid_y,
        "mask_score": mask_score, "labels": label_grid,
        "distance": distance_grid, "rgb": rgb_grid,
        "start_label": start_label, "end_label": end_label,
        "image_mask": mask,
    }


def _nearby_label(row, centre_j, radius=5):
    best = 0
    for d in range(1, radius + 1):
        for j in (centre_j - d, centre_j + d):
            if 0 <= j < len(row) and row[j] > 0:
                return int(row[j])
    return best


def _runs_for_label(label_row, label):
    chosen = label_row == label
    runs = []
    j = 0
    while j < len(chosen):
        if not chosen[j]:
            j += 1
            continue
        lo = j
        while j < len(chosen) and chosen[j]:
            j += 1
        hi = j - 1
        if hi - lo + 1 >= 2:
            runs.append((lo, hi, lo - CORRIDOR_HALF_WIDTH, hi - CORRIDOR_HALF_WIDTH,
                         hi - lo + 1))
    return runs


def _choose_run(runs, predicted_t=0.0):
    if not runs:
        return None
    return min(runs, key=lambda run: abs(((run[2] + run[3]) / 2.0) - predicted_t))


def _median_width(data, label, at_end=False):
    labels = data["labels"]
    count = labels.shape[0]
    if at_end:
        indices = range(max(0, int(count * 0.72)), count)
    else:
        indices = range(0, min(count, max(4, int(count * 0.28))))
    widths = []
    for i in indices:
        run = _choose_run(_runs_for_label(labels[i], label), 0.0)
        if run:
            widths.append(run[4])
    if not widths:
        return None
    return float(np.median(widths))


class _Side:
    """One visible ribbon end, scanned from its fixed point toward the cover."""

    def __init__(self, data, label, width, start=True):
        self.data = data
        self.label = label
        self.width = width
        self.start = start
        self.n = data["ss"].shape[0]
        self.scan_indices = (list(range(self.n)) if start
                             else list(range(self.n - 1, -1, -1)))

        self.observations = []  # scan order: fixed point -> occlusion edge
        for i in self.scan_indices:
            run = _choose_run(_runs_for_label(data["labels"][i], label), 0.0)
            if run is not None:
                self.observations.append((i, run))
        if len(self.observations) < 5:
            raise unavailable("端点附近露出的同带不够，无法确认两边")

        full = [(i, run) for i, run in self.observations
                if 0.82 * width <= run[4] <= 1.18 * width]
        if len(full) < 5:
            # A narrowly anti-aliased mask can be one or two pixels short; keep
            # enough rows but rely on robust line fits below.
            full = [(i, run) for i, run in self.observations
                    if 0.70 * width <= run[4] <= 1.30 * width]
        if len(full) < 4:
            raise unavailable("看不到同一缎带的两条连续边")

        gap_window = full[-26:]
        gap_s = np.array([data["ss"][i] for i, _ in gap_window], dtype=float)
        centre = np.array([(run[2] + run[3]) / 2.0 for _, run in gap_window])
        lo = np.array([run[2] for _, run in gap_window])
        hi = np.array([run[3] for _, run in gap_window])
        self.anchor_s = float(np.median(gap_s))
        self.center_fit = self._line_fit(gap_s, centre)
        self.lo_fit = self._line_fit(gap_s, lo)
        self.hi_fit = self._line_fit(gap_s, hi)
        self.last_full_i = gap_window[-1][0] if start else gap_window[-1][0]
        # For the reversed scan the final gap_window item has the smallest s.
        self.gap_i = (max(i for i, _ in gap_window) if start
                      else min(i for i, _ in gap_window))

        self.t = np.full(self.n, np.nan)
        for i, run in self.observations:
            self.t[i] = (run[2] + run[3]) / 2.0
        self.boundary_candidates = self._find_boundary()
        if not self.boundary_candidates:
            raise unavailable("露带在遮挡边前对不上中心线")

    @staticmethod
    def _line_fit(x, y):
        if len(x) >= 6:
            slope, intercept, _, _ = theilslopes(y, x)
            return np.array([float(slope), float(intercept)])
        return np.polyfit(x, y, 1)

    def center_at(self, s):
        return float(np.polyval(self.center_fit, s))

    def edge_at(self, s):
        return float(np.polyval(self.lo_fit, s)), float(np.polyval(self.hi_fit, s))

    def _find_boundary(self):
        """Find plausible final centre-inside-ribbon positions."""
        candidates = []
        direction = 1 if self.start else -1
        limit = min(self.n, self.gap_i + 11) if self.start else max(-1, self.gap_i - 11)
        previous_t = self.center_at(float(self.data["ss"][self.gap_i]))
        i = self.gap_i
        while True:
            s = float(self.data["ss"][i])
            predicted = self.center_at(s)
            runs = _runs_for_label(self.data["labels"][i], self.label)
            run = _choose_run(runs, predicted)
            if run is not None:
                lo_t, hi_t = float(run[2]), float(run[3])
                # Prefer the fitted centre when this cross-section is complete;
                # in a cut run, take the nearest actually visible ribbon pixel.
                fitted_lo, fitted_hi = self.edge_at(s)
                complete = abs(lo_t - fitted_lo) <= 2.2 and abs(hi_t - fitted_hi) <= 2.2
                target = predicted if complete else min(max(predicted, lo_t), hi_t)
                t = min(max(target, lo_t), hi_t)
                if abs(t - predicted) <= 2.2 and abs(t - previous_t) <= 1.8:
                    self.t[i] = t
                    previous_t = t
                    candidates.append((i, float(t), run))
            next_i = i + direction
            if (self.start and (i >= limit - 1 or next_i >= self.n)) or \
               (not self.start and (i <= limit + 1 or next_i < 0)):
                break
            i = next_i

        if not candidates:
            return []
        # Retain the positions nearest the cover as alternative route ends.
        return candidates[-6:]

    def visible_values(self, boundary_i):
        """Return smoothed t values from fixed endpoint to boundary."""
        values = self.t.copy()
        if self.start:
            idx = np.arange(0, boundary_i + 1)
        else:
            idx = np.arange(boundary_i, self.n)

        known = idx[np.isfinite(values[idx])]
        if len(known) >= 2:
            values[idx] = np.interp(idx, known, values[known])
        else:
            for i in idx:
                values[i] = self.center_at(float(self.data["ss"][i]))

        # The hand-marked end is fixed before smoothing as well as after.
        values[0 if self.start else self.n - 1] = 0.0
        # Smooth observed cross-section centres without blurring horizontally
        # across the whole one-row image.
        row = values[idx].reshape(1, -1).astype(np.float32)
        if row.shape[1] >= 5:
            values[idx] = cv2.GaussianBlur(row, (5, 1), 1.1).reshape(-1)
        values[0 if self.start else self.n - 1] = 0.0

        for i in idx:
            runs = _runs_for_label(self.data["labels"][i], self.label)
            run = _choose_run(runs, values[i])
            if run is not None:
                lo, hi = run[2] - 0.35, run[3] + 0.35
                if not lo <= values[i] <= hi:
                    values[i] = min(max(values[i], lo), hi)
        return values


def visible_only_trace(data, config, a, b, length, along, normal):
    """Trace an unobscured segment between its fixed endpoints."""
    labels = data["labels"]
    preferred = data["start_label"] or data["end_label"]
    ts = np.zeros(labels.shape[0], dtype=float)
    last = 0.0
    for i in range(labels.shape[0]):
        runs = _runs_for_label(labels[i], preferred)
        if not runs and data["end_label"]:
            runs = _runs_for_label(labels[i], data["end_label"])
        run = _choose_run(runs, last)
        if run is None:
            return None
        t = (run[2] + run[3]) / 2.0
        if abs(t - last) <= 4.0 or i == 0 or i == labels.shape[0] - 1:
            ts[i] = t
            last = t
        else:
            return None
    if ts.shape[0] >= 5:
        ts = cv2.GaussianBlur(ts.reshape(1, -1).astype(np.float32), (5, 1), 1.0).reshape(-1)
    ts[0] = 0.0
    ts[-1] = 0.0
    coords = []
    for i, t in enumerate(ts):
        s = float(data["ss"][i])
        coords.append([round(float(a[0] + along[0] * s + normal[0] * t), 3),
                       round(float(a[1] + along[1] * s + normal[1] * t), 3)])
    coords[0] = [int(a[0]), int(a[1])]
    coords[-1] = [int(b[0]), int(b[1])]
    distances = [0.0]
    for p, q in zip(coords, coords[1:]):
        distances.append(distances[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
    return {
        "kind": "visible_ribbon",
        "mode": "ribbon_edges_visible",
        "path": coords,
        "distance": [round(v, 4) for v in distances],
        "occluded_ranges": [],
        "occlusions": [],
        "length_px": round(distances[-1], 4),
        "visible_length_px": round(distances[-1], 4),
        "occluded_length_px": 0.0,
        "estimated_length_px": 0.0,
        "corridor_half_width_px": CORRIDOR_HALF_WIDTH,
        "bridge_limit_px": MAX_BRIDGE_PX,
    }


def _background_colour(data):
    rgb = data["rgb"]
    edge_pixels = np.concatenate((rgb[:, 0, :], rgb[:, -1, :]), axis=0)
    return np.median(edge_pixels, axis=0)


def _cover_confirmed(data, i1, t1, i2, t2, background):
    count = max(5, int(math.hypot(data["ss"][i2] - data["ss"][i1], t2 - t1) * 2))
    fractions = np.linspace(0.14, 0.86, count)
    s1, s2 = data["ss"][i1], data["ss"][i2]
    ss = s1 + (s2 - s1) * fractions
    tt = t1 + (t2 - t1) * fractions
    si = np.clip(np.rint(ss).astype(int), 0, data["ss"].shape[0] - 1)
    # Rows are approximately 1 px apart; map the exact s to row fraction.
    row_f = np.interp(ss, data["ss"], np.arange(data["ss"].shape[0]))
    i0 = np.floor(row_f).astype(int)
    i0 = np.clip(i0, 0, data["ss"].shape[0] - 2)
    f = (row_f - i0)[:, None]
    j = np.clip(np.rint(tt + CORRIDOR_HALF_WIDTH).astype(int), 0,
                2 * CORRIDOR_HALF_WIDTH)
    mask_score = data["mask_score"][i0, j] * (1 - f[:, 0]) + \
        data["mask_score"][i0 + 1, j] * f[:, 0]
    colours = data["rgb"][i0, j, :] * (1 - f) + data["rgb"][i0 + 1, j, :] * f
    bg_distance = np.linalg.norm(colours - background, axis=1)
    hsv = cv2.cvtColor(np.clip(colours, 0, 255).astype(np.uint8).reshape(-1, 1, 3),
                       cv2.COLOR_RGB2HSV).reshape(-1, 3)
    saturation = hsv[:, 1]
    blue_fraction = float(np.median(mask_score > 0.45))
    foreign = float(np.median(bg_distance) > 30 or np.median(saturation) > 42)
    return blue_fraction <= 0.30 and foreign > 0, {
        "blue_fraction": blue_fraction,
        "background_distance": float(np.median(bg_distance)),
        "saturation": float(np.median(saturation)),
    }


def _analyze_segment(image: np.ndarray, config: dict[str, Any], segment: int | str | None = None,
                    segment_index: int | None = None, **selector) -> dict[str, Any]:
    """Find and validate a reroute for one 1-based adjacent-point segment."""
    points = config.get("points") if isinstance(config, dict) else None
    if not isinstance(points, list) or not 2 <= len(points) <= 32:
        raise Invalid("需要2到32个原片路径点")

    seg = _segment_number(segment, segment_index, selector, len(points) - 1)
    a_xy = points[seg - 1]
    b_xy = points[seg]
    a, b, length, along, normal = _frame(a_xy, b_xy)
    data = _corridor_data(image, a, b, length, along, normal)

    if data["start_label"] == 0 or data["end_label"] == 0:
        raise unavailable("这一对点没有都落在走廊内的同色缎带上")

    if data["start_label"] == data["end_label"]:
        visible = visible_only_trace(data, config, a, b, length, along, normal)
        if visible is None:
            raise unavailable("可见缎带在走廊内中断或走向跳动过大")
        visible.update({
            "api": API_VERSION,
            "segment": seg,
            "segment_index": seg - 1,
            "start": [int(a[0]), int(a[1])],
            "end": [int(b[0]), int(b[1])],
            "candidate_count": 1,
        })
        return visible

    width_start = _median_width(data, data["start_label"], at_end=False)
    width_end = _median_width(data, data["end_label"], at_end=True)
    if width_start is None or width_end is None:
        raise unavailable("找不到连续的缎带两边")
    if abs(width_start - width_end) > max(5.0, 0.28 * max(width_start, width_end)):
        raise unavailable("遮挡两侧缎带宽窄对不上")
    width = (width_start + width_end) / 2.0

    start_side = _Side(data, data["start_label"], width_start, start=True)
    end_side = _Side(data, data["end_label"], width_end, start=False)
    background = _background_colour(data)

    candidates = []
    too_wide = False
    cover_failed = False
    for i1, t1, run1 in start_side.boundary_candidates:
        for i2, t2, run2 in end_side.boundary_candidates:
            if i2 <= i1 + 1:
                continue
            s1, s2 = float(data["ss"][i1]), float(data["ss"][i2])
            bridge = math.hypot(s2 - s1, t2 - t1)
            if bridge > MAX_BRIDGE_PX + 1e-8:
                too_wide = True
                continue

            sm = (s1 + s2) / 2.0
            cs1 = start_side.center_at(sm)
            cs2 = end_side.center_at(sm)
            lo1, hi1 = start_side.edge_at(sm)
            lo2, hi2 = end_side.edge_at(sm)
            edge_residual = max(abs(lo1 - lo2), abs(hi1 - hi2))
            center_residual = abs(cs1 - cs2)
            width_mismatch = abs(width_start - width_end)

            derivative1 = start_side.center_fit[0]
            derivative2 = end_side.center_fit[0]
            v1 = along + normal * derivative1
            v2 = along + normal * derivative2
            v1 /= np.linalg.norm(v1)
            v2 /= np.linalg.norm(v2)
            angle = math.acos(max(-1.0, min(1.0, float(np.dot(v1, v2)))))

            edges_match = edge_residual <= max(4.5, 0.22 * width)
            centres_match = center_residual <= max(3.5, 0.18 * width)
            angles_match = angle <= math.radians(42)
            if not (edges_match and centres_match and angles_match):
                continue

            covered, cover_detail = _cover_confirmed(data, i1, t1, i2, t2, background)
            if not covered:
                cover_failed = True
                continue

            endpoint_fit = abs(t1 - start_side.center_at(s1)) + abs(t2 - end_side.center_at(s2))
            score = (edge_residual * 3.0 + center_residual * 2.0 +
                     endpoint_fit * 1.1 +
                     math.degrees(angle) * 0.18 + bridge * 0.12 +
                     abs(t1) * 0.02 + abs(t2) * 0.02)
            candidates.append({
                "score": score, "i1": i1, "i2": i2, "t1": t1, "t2": t2,
                "bridge": bridge, "edge_residual": edge_residual,
                "center_residual": center_residual, "angle": angle,
                "cover": cover_detail,
            })

    if not candidates:
        if too_wide:
            raise unavailable(f"遮挡沿缎带超过{MAX_BRIDGE_PX}原片像素，或两侧露带间隔太宽")
        if cover_failed:
            raise unavailable("中间不像一处连续上层遮挡，未予补线")
        raise unavailable("遮挡两边的缎带边缘或走向对不上")

    best = min(candidates, key=lambda item: item["score"])
    i1, i2 = best["i1"], best["i2"]
    t1, t2 = best["t1"], best["t2"]
    t_values = start_side.visible_values(i1)
    end_values = end_side.visible_values(i2)
    t_values[i2:] = end_values[i2:]
    # Linear forward bridge in chord-normal coordinates.
    for i in range(i1, i2 + 1):
        f = (data["ss"][i] - data["ss"][i1]) / max(1e-9, data["ss"][i2] - data["ss"][i1])
        t_values[i] = t1 + (t2 - t1) * f

    coords = []
    for i in range(len(data["ss"])):
        s = float(data["ss"][i])
        t = float(t_values[i])
        x = a[0] + along[0] * s + normal[0] * t
        y = a[1] + along[1] * s + normal[1] * t
        coords.append([round(float(x), 3), round(float(y), 3)])
    coords[0] = [int(a[0]), int(a[1])]
    coords[-1] = [int(b[0]), int(b[1])]

    local_dist = [0.0]
    for p, q in zip(coords, coords[1:]):
        local_dist.append(local_dist[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
    d1 = local_dist[i1]
    d2 = local_dist[i2]
    bridge_length = d2 - d1
    if bridge_length > MAX_BRIDGE_PX + 0.01:  # allow 3-decimal coordinate rounding
        raise unavailable(f"补过的路径为{bridge_length:.1f}px，超过{MAX_BRIDGE_PX}px")

    result = {
        "api": API_VERSION,
        "segment": seg,
        "segment_index": seg - 1,
        "kind": "retraced_ribbon",
        "mode": "ribbon_edges_with_occlusion_bridge",
        "start": [int(a[0]), int(a[1])],
        "end": [int(b[0]), int(b[1])],
        "path": coords,
        "distance": [round(v, 4) for v in local_dist],
        "occluded_ranges": [[round(d1, 4), round(d2, 4)]],
        "occlusions": [{
            "start_distance": round(d1, 4),
            "end_distance": round(d2, 4),
            "start": coords[i1],
            "end": coords[i2],
            "length_px": round(bridge_length, 3),
            "max_allowed_px": MAX_BRIDGE_PX,
            "edge_residual_px": round(best["edge_residual"], 3),
            "center_residual_px": round(best["center_residual"], 3),
            "direction_difference_deg": round(math.degrees(best["angle"]), 2),
        }],
        "length_px": round(local_dist[-1], 4),
        "visible_length_px": round(local_dist[-1] - bridge_length, 4),
        "occluded_length_px": round(bridge_length, 4),
        "estimated_length_px": round(bridge_length, 4),
        "corridor_half_width_px": CORRIDOR_HALF_WIDTH,
        "bridge_limit_px": MAX_BRIDGE_PX,
        "width_px": round(width, 2),
        "candidate_count": len(candidates),
    }
    return result


def _segment_number(segment, segment_index, selector, last):
    # Accept several natural request/keyword spellings at this boundary.
    if segment is None and segment_index is not None:
        segment = int(segment_index) + 1
    if segment is None and selector:
        for key in ("segment_number", "pair", "pair_index", "adjacent_pair"):
            if key in selector:
                value = selector[key]
                if key == "pair_index":
                    segment = int(value) + 1
                else:
                    segment = value
                break
        if segment is None and "start" in selector and "end" in selector:
            # Coordinate pair selector; exact matching is handled by caller if
            # needed.  Here only dictionary integer shortcuts are supported.
            raise Invalid("请用相邻点对编号选择要重走的段")
    try:
        seg = int(segment)
    except (TypeError, ValueError):
        raise Invalid("请选择要重走的相邻点对") from None
    if not 1 <= seg <= last:
        raise Invalid(f"相邻点对编号须在1到{last}之间")
    return seg


def retrace_segment(image, config, segment=None, segment_index=None, **selector):
    """Public entry point: convert any unstable image case to a user cue."""
    try:
        return _analyze_segment(image, config, segment=segment,
                                segment_index=segment_index, **selector)
    except Invalid:
        raise
    except Exception as exc:  # image arrays are untrusted and varied
        raise unavailable("走廊内缎带信息不足、遮挡太宽或两侧对不上") from exc


reroute_segment = retrace_segment
suggest_retrace = retrace_segment
