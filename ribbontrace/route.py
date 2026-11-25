"""Measure a hand-marked ribbon path and keep its actual source pixels."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from scipy.interpolate import PchipInterpolator

from image_lab.validation import Invalid, integer, number

from .retrace import (
    CORRIDOR_HALF_WIDTH,
    MAX_BRIDGE_PX,
    retrace_segment,
    unavailable as retrace_unavailable,
)


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(photo.size) < 32 or max(photo.size) > 2600 or math.prod(photo.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normal = ImageOps.exif_transpose(photo).convert("RGB")
            return normal.size, png(normal)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("原片无法解码") from error


def pixels(path):
    with Image.open(path) as photo:
        return np.asarray(photo.convert("RGB"))


def normalize_traces(value, points, size=None):
    """Validate saved segment reroutes while leaving straight mode as absence."""
    width, height = size or (100000, 100000)
    if value in (None, {}):
        return {}
    if isinstance(value, dict) and isinstance(value.get("path", value.get("points")), list) and \
            ("segment" in value or "segment_index" in value):
        value = [value]
    raw_items = []
    if isinstance(value, dict):
        raw_items = list(value.items())
    elif isinstance(value, list):
        raw_items = [(item.get("segment", item.get("segment_index", 0)) if isinstance(item, dict) else None, item)
                     for item in value]
    else:
        raise Invalid("重走段数据格式不正确")

    traces = {}
    last_segment = len(points) - 1
    for raw_key, raw in raw_items:
        if raw is None:
            continue
        if not isinstance(raw, dict):
            if isinstance(raw, list):
                raw = {"path": raw}
            else:
                raise Invalid("重走段数据格式不正确")
        segment_value = raw.get("segment", raw_key)
        if isinstance(segment_value, str) and segment_value.isdigit():
            seg = int(segment_value)
        else:
            try:
                seg = int(segment_value)
            except (TypeError, ValueError):
                raise Invalid("重走段编号不正确") from None
        # Lists may use zero-based segment_index.
        if isinstance(raw.get("segment_index"), int) and "segment" not in raw:
            seg = raw["segment_index"] + 1
        if not 1 <= seg <= last_segment:
            raise Invalid("重走段编号超出相邻点对范围")

        path_raw = raw.get("path", raw.get("points"))
        if not isinstance(path_raw, list) or not 2 <= len(path_raw) <= 5000:
            raise Invalid("重走段路径需要2到5000个坐标点")
        path = []
        point_estimated = []
        for mark in path_raw:
            estimated_flag = False
            if isinstance(mark, dict):
                estimated_flag = bool(mark.get("estimated", mark.get("occluded", mark.get("hidden")))) or \
                    str(mark.get("kind", "")).lower() in {"occluded", "estimated", "hidden"}
                x_value, y_value = mark.get("x", mark.get("X")), mark.get("y", mark.get("Y"))
            elif isinstance(mark, (list, tuple)) and len(mark) >= 3:
                x_value, y_value = mark[0], mark[1]
                third = mark[2]
                if isinstance(third, str):
                    estimated_flag = third in {"1", "true", "True", "estimated", "occluded",
                                              "hidden", "遮挡估段"}
                else:
                    estimated_flag = bool(third)
            elif isinstance(mark, (list, tuple)):
                x_value, y_value = mark[0], mark[1]
            else:
                raise Invalid("重走段路径点需要X和Y")
            x = number(x_value, "重走段X", -20, width + 20)
            y = number(y_value, "重走段Y", -20, height + 20)
            if not math.isfinite(x) or not math.isfinite(y):
                raise Invalid("重走段坐标必须有限")
            path.append([float(x), float(y)])
            point_estimated.append(estimated_flag)

        expected_start = [float(points[seg - 1][0]), float(points[seg - 1][1])]
        expected_end = [float(points[seg][0]), float(points[seg][1])]
        if math.hypot(path[0][0] - expected_start[0], path[0][1] - expected_start[1]) > 2 or \
           math.hypot(path[-1][0] - expected_end[0], path[-1][1] - expected_end[1]) > 2:
            # A moved hand point makes the old reroute stale; straight fallback
            # is always valid, so drop rather than blocking ordinary editing.
            continue
        path[0] = expected_start
        path[-1] = expected_end

        delta = np.array(expected_end) - np.array(expected_start)
        chord_length = float(np.hypot(*delta))
        if chord_length >= 1:
            unit = delta / chord_length
            normal = np.array([-unit[1], unit[0]])
            start_array = np.array(expected_start)
            for x, y in path:
                rel = np.array([x, y]) - start_array
                along_position = float(rel @ unit)
                side_position = abs(float(rel @ normal))
                if along_position < -1.0 or along_position > chord_length + 1.0 or \
                        side_position > CORRIDOR_HALF_WIDTH + 0.5:
                    raise Invalid(f"重走段只能在两点连线两侧各{CORRIDOR_HALF_WIDTH}原片像素内")

        distances = [0.0]
        for p, q in zip(path, path[1:]):
            step_length = math.hypot(q[0] - p[0], q[1] - p[1])
            if step_length < 1e-9:
                q[0] += 1e-6
            distances.append(distances[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
        total = distances[-1]
        if total > 10000:
            raise Invalid("重走段长度异常")

        ranges_raw = raw.get("occluded_ranges",
                       raw.get("estimated_ranges",
                       raw.get("hidden_ranges", raw.get("cover_ranges", []))))
        if ranges_raw is None:
            ranges_raw = []
        if not isinstance(ranges_raw, list):
            raise Invalid("遮挡估段区间格式不正确")
        if not ranges_raw and any(point_estimated):
            start_index = None
            for i, flagged in enumerate(point_estimated + [False]):
                if flagged and start_index is None:
                    start_index = i
                elif not flagged and start_index is not None:
                    ranges_raw.append([distances[start_index], distances[i - 1]])
                    start_index = None
        ranges = []
        for interval in ranges_raw:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise Invalid("遮挡估段区间需要起止距离")
            lo = number(interval[0], "估段起点", 0, total + 0.001)
            hi = number(interval[1], "估段终点", 0, total + 0.001)
            if hi <= lo:
                raise Invalid("遮挡估段区间方向不正确")
            if hi - lo > MAX_BRIDGE_PX + 0.01:
                raise Invalid(f"遮挡估段不能超过{MAX_BRIDGE_PX}原片像素")
            ranges.append([round(lo, 4), round(hi, 4)])
        if len(ranges) > 1:
            raise Invalid("同一相邻点对只允许补一处遮挡")

        trace = {
            "segment": seg,
            "segment_index": seg - 1,
            "kind": str(raw.get("kind", "retraced_ribbon"))[:40],
            "path": [[round(x, 3), round(y, 3)] for x, y in path],
            "distance": [round(v, 4) for v in distances],
            "occluded_ranges": ranges,
            "occlusions": raw.get("occlusions") if isinstance(raw.get("occlusions"), list) else [],
            "length_px": round(total, 4),
            "visible_length_px": round(total - sum(hi - lo for lo, hi in ranges), 4),
            "occluded_length_px": round(sum(hi - lo for lo, hi in ranges), 4),
            "estimated_length_px": round(sum(hi - lo for lo, hi in ranges), 4),
            "corridor_half_width_px": CORRIDOR_HALF_WIDTH,
            "bridge_limit_px": MAX_BRIDGE_PX,
        }
        traces[str(seg)] = trace
    return traces


def settings(value, size):
    w, h = size
    marks = value.get("points")
    if not isinstance(marks, list) or not 2 <= len(marks) <= 32:
        raise Invalid("需要2到32个原片路径点")
    points = []
    for i, mark in enumerate(marks):
        if not isinstance(mark, list) or len(mark) != 2:
            raise Invalid("每个路径点需要X和Y")
        point = [integer(mark[0], f"点{i+1}的X", 0, w-1), integer(mark[1], f"点{i+1}的Y", 0, h-1)]
        if points and point == points[-1]:
            raise Invalid("连续路径点不能相同")
        points.append(point)
    raw_traces = None
    for key in ("traces", "retraces", "reroutes", "segment_traces", "trace_segments"):
        if value.get(key) is not None:
            raw_traces = value.get(key)
            break
    if raw_traces is None:
        for key in ("retrace", "reroute", "segment_trace"):
            if isinstance(value.get(key), dict):
                raw_traces = [value[key]]
                break
    traces = normalize_traces(raw_traces, points, (w, h))
    normalized = {"points": points,
                  "mm_per_px": number(value.get("mm_per_px"), "标尺", 0.001, 500),
                  "step": integer(value.get("step"), "采样间隔", 1, 64)}
    if traces:
        normalized["traces"] = traces
    return normalized


def defaults(size):
    w, h = size
    return settings({"points": [[w//4,h//2], [w*3//4,h//2]], "mm_per_px": 1, "step": 5}, size)


def point(image, x, y, config):
    rgb = image[y,x]
    return {"x":x, "y":y, "rgb":[int(value) for value in rgb], "brightness":float(rgb @ LUMA)}


def geometry(config):
    path = np.array(config["points"], dtype=float)
    edge_lengths = np.linalg.norm(path[1:]-path[:-1],axis=1)
    cumulative = np.r_[0,np.cumsum(edge_lengths)]
    return path, cumulative


def effective_geometry(config):
    """Build the measured polyline, including saved rerouted segment shapes."""
    points = config["points"]
    traces = config.get("traces") or {}
    coords = []
    distances = []
    segment_ends = []
    intervals = []
    summaries = []
    offset = 0.0

    for i in range(len(points) - 1):
        seg = i + 1
        trace = traces.get(str(seg)) or traces.get(seg)
        start = np.array(points[i], dtype=float)
        end = np.array(points[i + 1], dtype=float)
        straight_length = float(np.linalg.norm(end - start))
        if trace and trace.get("path"):
            part = np.array(trace["path"], dtype=float)
            if math.hypot(*(part[0] - start)) > 2 or math.hypot(*(part[-1] - end)) > 2:
                part = np.vstack((start, end))
                trace = None
        else:
            part = np.vstack((start, end))

        local = np.r_[0.0, np.cumsum(np.linalg.norm(part[1:] - part[:-1], axis=1))]
        if coords:
            # Avoid a duplicate zero-length joint in the global interpolation.
            if np.linalg.norm(part[0] - coords[-1]) < 1e-8:
                part = part[1:]
                local = local[1:]
            global_part = local + offset
            coords.extend(part.tolist())
            distances.extend(global_part.tolist())
        else:
            coords = part.tolist()
            distances = (local + offset).tolist()

        segment_length = float(local[-1])
        for lo, hi in (trace.get("occluded_ranges", []) if trace else []):
            intervals.append((offset + float(lo), offset + float(hi), seg))
        occ = float(trace.get("occluded_length_px",
                              sum(hi - lo for lo, hi in trace.get("occluded_ranges", [])))
                    if trace else 0.0)
        summaries.append({
            "segment": seg,
            "segment_index": i,
            "length_px": round(segment_length, 4),
            "straight_length_px": round(straight_length, 4),
            "visible_length_px": round(segment_length - occ, 4),
            "occluded_length_px": round(occ, 4),
            "estimated_length_px": round(occ, 4),
            "retraced": bool(trace),
            "occlusions": trace.get("occlusions", []) if trace else [],
        })
        offset += segment_length
        segment_ends.append(offset)

    path = np.array(coords, dtype=float)
    cumulative = np.array(distances, dtype=float)
    # Remove accidental non-increasing duplicates for interpolation.
    keep = np.ones(len(cumulative), dtype=bool)
    for i in range(1, len(cumulative)):
        if cumulative[i] <= cumulative[i - 1]:
            cumulative[i] = cumulative[i - 1] + 1e-6
    return path, cumulative, np.array(segment_ends, dtype=float), intervals, summaries


def with_normalized_traces(config):
    if config.get("traces"):
        return config
    raw_traces = None
    for key in ("retraces", "reroutes", "segment_traces", "trace_segments"):
        if config.get(key) is not None:
            raw_traces = config.get(key)
            break
    if raw_traces is None:
        for key in ("retrace", "reroute", "segment_trace"):
            if isinstance(config.get(key), dict):
                raw_traces = [config[key]]
                break
    if raw_traces is None:
        return config
    return {**config, "traces": normalize_traces(raw_traces, config["points"])}


def profile_detailed(image, config):
    config = with_normalized_traces(config)
    path, cumulative, segment_ends, intervals, summaries = effective_geometry(config)
    end = float(cumulative[-1])
    distances = np.r_[np.arange(0,end,config["step"]), end]
    xs = np.rint(np.interp(distances,cumulative,path[:,0])).astype(int)
    ys = np.rint(np.interp(distances,cumulative,path[:,1])).astype(int)
    h, w = image.shape[:2]
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)

    segments = np.searchsorted(segment_ends, distances, side="right") + 1
    segments = np.clip(segments, 1, len(config["points"]) - 1)
    occluded = np.zeros(len(distances), dtype=bool)
    for lo, hi, seg in intervals:
        occluded |= (distances >= lo - 1e-7) & (distances <= hi + 1e-7) & (segments == seg)

    levels = image[ys,xs] @ LUMA
    rows = []
    for d, x, y, level, segment, hidden in zip(distances, xs, ys, levels, segments, occluded):
        hidden = bool(hidden)
        category = "estimated" if hidden else "observed"
        measurement_class = "estimated" if hidden else "measured"
        rows.append({
            "distance": float(d),
            "x": int(x),
            "y": int(y),
            "brightness": None if hidden else float(level),
            "luma": None if hidden else float(level),
            "segment": int(segment),
            "kind": "occluded" if hidden else "visible",
            "part": "estimated" if hidden else "seen",
            "type": measurement_class,
            "category": category,
            "class": measurement_class,
            "state": "occluded_estimate" if hidden else "visible_measurement",
            "measured": not hidden,
            "estimated": hidden,
            "evidence": "遮挡估段" if hidden else "实见",
        })
    return rows, end, summaries, intervals


def profile(image, config):
    """Backward-compatible profile API: rows and total length only."""
    values, end, _, _ = profile_detailed(image, config)
    return values, end


def report(image, config):
    config = with_normalized_traces(config)
    values, end, summaries, intervals = profile_detailed(image, config)
    _, straight_cumulative = geometry(config)
    straight_length = float(straight_cumulative[-1])
    occluded_length = float(sum(item["occluded_length_px"] for item in summaries))
    visible_length = end - occluded_length
    traces = config.get("traces") or {}
    occluded_segments = []
    for summary in summaries:
        for occlusion in summary.get("occlusions", []):
            occluded_segments.append({"segment": summary["segment"], **occlusion})
    return {"profile":values, "length_px":end, "length_mm":end*config["mm_per_px"],
            "manual_length_px":straight_length,
            "manual_length_mm":straight_length*config["mm_per_px"],
            "original_length_px":straight_length,
            "original_length_mm":straight_length*config["mm_per_px"],
            "straight_length_px":straight_length,
            "straight_length_mm":straight_length*config["mm_per_px"],
            "visible_length_px":visible_length,
            "visible_length_mm":visible_length*config["mm_per_px"],
            "seen_length_px":visible_length,
            "seen_length_mm":visible_length*config["mm_per_px"],
            "occluded_length_px":occluded_length,
            "occluded_length_mm":occluded_length*config["mm_per_px"],
            "hidden_length_px":occluded_length,
            "hidden_length_mm":occluded_length*config["mm_per_px"],
            "estimated_length_px":occluded_length,
            "estimated_length_mm":occluded_length*config["mm_per_px"],
            "measured_length_px":visible_length,
            "measured_length_mm":visible_length*config["mm_per_px"],
            "segment_lengths":summaries,
            "occluded_segments":occluded_segments,
            "estimated_segments":occluded_segments,
            "segments":len(config["points"])-1,
            "points":config["points"],
            "traces":traces,
            "corridor_half_width_px":CORRIDOR_HALF_WIDTH,
            "bridge_limit_px":MAX_BRIDGE_PX}



def retrace(image, config, segment=None, segment_index=None, **selector):
    """Convenience alias for image-assisted one-segment rerouting."""
    return retrace_segment(image, config, segment=segment,
                           segment_index=segment_index, **selector)


reroute_segment = retrace_segment
reroute = retrace


def practice():
    w,h = 756,454
    cfg = settings({"points":[[159,281],[289,222],[422,234]],"mm_per_px":.45,"step":5},(w,h))
    yy,xx = np.indices((h,w))
    base = 212+13*np.cos((xx-100)/260)-yy*.035+np.sin(xx*.51+yy*.63)*1.8
    image = Image.fromarray(np.stack([base+7,base+4,base-8],axis=-1).clip(0,255).astype("uint8"),"RGB")
    draw = ImageDraw.Draw(image)
    along = np.arange(94,656)
    blue_y = PchipInterpolator([94,159,289,422,542,655],[307,281,222,234,171,159])(along)
    blue = [(int(x),int(y)) for x,y in zip(along,blue_y)]
    draw.line([(x+2,y+3) for x,y in blue],fill="#8b9491",width=29,joint="curve")
    draw.line(blue,fill="#337e92",width=25,joint="curve")
    draw.line([(x,y-6) for x,y in blue],fill="#559eb0",width=7,joint="curve")
    draw.line([(x,y+7) for x,y in blue],fill="#285d76",width=4,joint="curve")
    y_values = np.arange(73,365)
    gold_x = PchipInterpolator([73,156,225,285,364],[371,333,344,385,469])(y_values)
    gold = [(int(x),int(y)) for x,y in zip(gold_x,y_values)]
    draw.line([(x+3,y+2) for x,y in gold],fill="#a19a80",width=19,joint="curve")
    draw.line(gold,fill="#ce8949",width=16,joint="curve")
    draw.line([(x-4,y) for x,y in gold],fill="#f6bf7b",width=5,joint="curve")
    draw.text((30,48),"RIBBON STUDY / A",fill="#554e43",font=ImageFont.load_default(size=17))
    draw.text((30,386),"SIMULATED LAYOUT",fill="#696051",font=ImageFont.load_default(size=11))
    draw.line((40,414,140,414),fill="#494339",width=2)
    for x in range(40,141,10):
        draw.line((x,414,x,406 if x%50 else 399),fill="#494339",width=2)
    draw.text((153,404),"45 mm",fill="#494339",font=ImageFont.load_default(size=12))
    return image.size,png(image),cfg
