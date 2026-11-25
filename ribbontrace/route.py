"""Measure a hand-marked ribbon path and keep its actual source pixels."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from scipy.interpolate import PchipInterpolator

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024

# A sample counts as plain base colour when every RGB channel is within this
# distance of the photographed base reading.
BASE_RGB_LIMIT = 18
# The cut itself plus this many actual samples on each side must be base-clean.
CUT_WINDOW_SIDE = 2

# Accepted spellings for the cut settings (the canonical names are first).
TARGET_KEYS = ("cut_target_mm", "target_mm", "cut_mm", "want_mm", "wanted_mm",
               "target_length_mm", "cut_length_mm", "length_target_mm", "desired_mm")
TOLERANCE_KEYS = ("cut_tolerance_mm", "tolerance_mm", "tolerance", "cut_tolerance",
                  "allowance_mm", "allowed_deviation_mm", "deviation_mm", "allow_mm")
BASE_KEYS = ("base_rgb", "base_colour_rgb", "base_color_rgb", "base_colour",
             "base_color", "background_rgb", "base", "ground_rgb")
CUT_INDEX_KEYS = ("cut_index", "cut_sample_index", "adopted_cut_index",
                  "adopt_index", "cut_sample", "index")


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


def _pick(value, keys):
    """Return the first present (non-None) value among alias keys."""
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def optional_number(value, label, minimum=None, maximum=None):
    if value is None or value == "":
        return None
    return number(value, label, minimum, maximum)


def parse_rgb(value):
    """Accept [r,g,b], {'r','g','b'} or a #RRGGBB string; return plain ints."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.count(",") == 2:
            return parse_rgb([part.strip() for part in text.split(",")])
        if not text.startswith("#"):
            text = "#" + text
        if len(text) != 7:
            raise Invalid("底色须为 #RRGGBB 或 R,G,B")
        try:
            return [int(text[i:i + 2], 16) for i in (1, 3, 5)]
        except ValueError as error:
            raise Invalid("底色须为 #RRGGBB 或 R,G,B") from error
    if isinstance(value, dict):
        return parse_rgb([value.get("r", value.get("R")), value.get("g", value.get("G")),
                          value.get("b", value.get("B"))])
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [integer(channel, "底色RGB通道", 0, 255) for channel in value]
    raise Invalid("底色须为三个0–255的RGB通道")


def normalize_cut_keys(incoming):
    """Map every accepted alias onto the canonical settings keys before merge."""
    if not isinstance(incoming, dict):
        return incoming
    result = dict(incoming)
    # Accept cut/adoption expressed as a bare index or as a nested object.
    nested_sources = []
    cut_value = incoming.get("cut")
    if isinstance(cut_value, (int, float)) and not isinstance(cut_value, bool):
        result.setdefault("cut_index", cut_value)
    elif isinstance(cut_value, dict):
        nested_sources.append(cut_value)
    for key in ("adopted_cut", "cut_position", "adopted", "selection", "chosen"):
        value = incoming.get(key)
        if isinstance(value, dict):
            nested_sources.append(value)
    for nested in nested_sources:
        if isinstance(nested, dict):
            for keys in (TARGET_KEYS, TOLERANCE_KEYS, BASE_KEYS, CUT_INDEX_KEYS):
                value = _pick(nested, keys)
                if value is not None and not any(k in incoming for k in keys):
                    result[keys[0]] = value
            if nested.get("index") is not None and "cut_index" not in result:
                result["cut_index"] = nested["index"]
    target = _pick(incoming, TARGET_KEYS)
    if target is not None:
        result["cut_target_mm"] = target
    tolerance = _pick(incoming, TOLERANCE_KEYS)
    if tolerance is not None:
        result["cut_tolerance_mm"] = tolerance
    base = _pick(incoming, BASE_KEYS)
    if base is not None:
        result["base_rgb"] = base
    cut_index = _pick(incoming, CUT_INDEX_KEYS)
    if cut_index is not None:
        result["cut_index"] = cut_index
    return result


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
    config = {"points": points,
              "mm_per_px": number(value.get("mm_per_px"), "标尺", 0.001, 500),
              "step": integer(value.get("step"), "采样间隔", 1, 64)}
    target = optional_number(_pick(value, TARGET_KEYS), "想要的毫米数", 0.001, 1_000_000)
    tolerance = optional_number(_pick(value, TOLERANCE_KEYS), "允许差", 0, 1_000_000)
    config["cut_target_mm"] = target
    config["cut_tolerance_mm"] = tolerance
    config["base_rgb"] = parse_rgb(_pick(value, BASE_KEYS))
    cut_index = optional_number(_pick(value, CUT_INDEX_KEYS), "采用切位", 0)
    config["cut_index"] = int(cut_index) if cut_index is not None else None
    return config


SETTING_ALIASES = {
    "target_mm": "cut_target_mm",
    "cut_mm": "cut_target_mm",
    "want_mm": "cut_target_mm",
    "target_length_mm": "cut_target_mm",
    "cut_length_mm": "cut_target_mm",
    "tolerance_mm": "cut_tolerance_mm",
    "tolerance": "cut_tolerance_mm",
    "allowance_mm": "cut_tolerance_mm",
    "base_color": "base_rgb",
    "base_colour": "base_rgb",
    "background_rgb": "base_rgb",
    "cut_sample_index": "cut_index",
    "adopted_cut_index": "cut_index",
}


def settings_view(config):
    """Expose likely alias spellings in API responses without persisting them."""
    view = dict(config)
    for alias, canonical in SETTING_ALIASES.items():
        view.setdefault(alias, config.get(canonical))
    base = config.get("base_rgb")
    if base:
        view["base_hex"] = "#" + "".join(f"{int(c):02x}" for c in base)
        view["base_color_hex"] = view["base_hex"]
    else:
        view["base_hex"] = None
        view["base_color_hex"] = None
    return view


def card_view(card):
    return {**card, "settings": settings_view(card["settings"])}


def defaults(size):
    w, h = size
    return settings({"points": [[w//4,h//2], [w*3//4,h//2]], "mm_per_px": 1, "step": 5}, size)


def point(image, x, y, config=None):
    rgb = image[y, x]
    return {"x":x, "y":y, "rgb":[int(value) for value in rgb], "brightness":float(rgb @ LUMA)}


def geometry(config):
    path = np.array(config["points"], dtype=float)
    edge_lengths = np.linalg.norm(path[1:]-path[:-1],axis=1)
    cumulative = np.r_[0,np.cumsum(edge_lengths)]
    return path, cumulative


def profile(image, config):
    path, cumulative = geometry(config)
    end = float(cumulative[-1])
    distances = np.r_[np.arange(0,end,config["step"]), end]
    xs = np.rint(np.interp(distances,cumulative,path[:,0])).astype(int)
    ys = np.rint(np.interp(distances,cumulative,path[:,1])).astype(int)
    segments = np.minimum(np.searchsorted(cumulative,distances,side="right")-1,len(path)-2)
    colours = image[ys,xs]
    levels = colours @ LUMA
    rows = []
    for i,(d,x,y,rgb,level,segment) in enumerate(zip(distances,xs,ys,colours,levels,segments)):
        rows.append({"index":i,
                     "distance":float(d),
                     "distance_px":float(d),
                     "x":int(x),
                     "y":int(y),
                     "rgb":[int(channel) for channel in rgb],
                     "brightness":float(level),
                     "segment":int(segment+1)})
    return rows, end


def rgb_close(rgb, base_rgb, limit=BASE_RGB_LIMIT):
    """Every channel must be no more than `limit` away from the base reading."""
    return all(abs(int(a) - int(b)) <= limit for a, b in zip(rgb, base_rgb))


def resolve_cut_request(config, target_mm=None, tolerance_mm=None, base_rgb=None):
    target_mm = config.get("cut_target_mm") if target_mm is None else target_mm
    tolerance_mm = config.get("cut_tolerance_mm") if tolerance_mm is None else tolerance_mm
    base_rgb = config.get("base_rgb") if base_rgb is None else base_rgb
    return target_mm, tolerance_mm, base_rgb


def cut_requirements(config, target_mm=None, tolerance_mm=None, base_rgb=None):
    """Validate an explicit cut search request and return plain values."""
    target, tolerance, base = resolve_cut_request(config, target_mm, tolerance_mm, base_rgb)
    if target is None:
        raise Invalid("请填写想要剪下的毫米数")
    if tolerance is None:
        raise Invalid("请填写长度允许差（毫米）")
    if base is None:
        raise Invalid("请先用原片点读选取一个无花纹底色")
    target = number(target, "想要的毫米数", 0.001, 1_000_000)
    tolerance = number(tolerance, "允许差", 0, 1_000_000)
    base = parse_rgb(base)
    if base is None:
        raise Invalid("请先用原片点读选取一个无花纹底色")
    return float(target), float(tolerance), base


def pattern_clearance(values, i, base_rgb, limit=BASE_RGB_LIMIT):
    """Actual arc distance (px) from sample i to the nearest pattern on each side.

    A sample is pattern when at least one RGB channel differs from the base by
    more than the channel limit. ``None`` on a side means no pattern was met.
    """
    left_px = None
    for j in range(i - 1, -1, -1):
        if not rgb_close(values[j]["rgb"], base_rgb, limit):
            left_px = values[i]["distance"] - values[j]["distance"]
            break
    right_px = None
    for j in range(i + 1, len(values)):
        if not rgb_close(values[j]["rgb"], base_rgb, limit):
            right_px = values[j]["distance"] - values[i]["distance"]
            break
    return left_px, right_px


def cut_search(image, config, target_mm=None, tolerance_mm=None, base_rgb=None,
               values=None, strict=False):
    """Rank actual path samples that can serve as a pattern-safe cut.

    Candidates must sit at least two samples away from both profile ends, land
    within the length tolerance and have the cut plus two samples on each side
    all within 18 RGB channel units of the chosen base colour.
    """
    if strict:
        target, tolerance, base = cut_requirements(config, target_mm, tolerance_mm, base_rgb)
    else:
        target, tolerance, base = resolve_cut_request(config, target_mm, tolerance_mm, base_rgb)
        if target is None or tolerance is None or not base:
            return None
        target, tolerance, base = float(target), float(tolerance), list(base)
    if values is None:
        values, _ = profile(image, config)
    mm_per_px = config["mm_per_px"]
    total = len(values)
    # Linear-time nearest-pattern indices on each side (strictly before/after).
    pattern_at = [not rgb_close(sample["rgb"], base) for sample in values]
    nearest_left, nearest_right = [None] * total, [None] * total
    last = None
    for i in range(total):
        nearest_left[i] = last
        if pattern_at[i]:
            last = i
    nxt = None
    for i in range(total - 1, -1, -1):
        nearest_right[i] = nxt
        if pattern_at[i]:
            nxt = i
    candidates = []
    for i, sample in enumerate(values):
        distance_mm = sample["distance"] * mm_per_px
        error_mm = abs(distance_mm - target)
        length_ok = error_mm <= tolerance + 1e-9
        has_both_sides = i - CUT_WINDOW_SIDE >= 0 and i + CUT_WINDOW_SIDE < total
        window = values[i - CUT_WINDOW_SIDE:i + CUT_WINDOW_SIDE + 1] if has_both_sides else []
        colour_ok = has_both_sides and all(rgb_close(item["rgb"], base) for item in window)
        if not (has_both_sides and length_ok and colour_ok):
            continue
        j = nearest_left[i]
        left_px = None if j is None else sample["distance"] - values[j]["distance"]
        j = nearest_right[i]
        right_px = None if j is None else values[j]["distance"] - sample["distance"]
        left_mm = None if left_px is None else left_px * mm_per_px
        right_mm = None if right_px is None else right_px * mm_per_px
        known = [side for side in (left_mm, right_mm) if side is not None]
        clearance_mm = min(known) if known else None
        span_mm = sum(known) if known else None
        candidates.append({
            "index": i,
            "sample_index": i,
            "sample": i + 1,
            "number": i + 1,
            "x": sample["x"],
            "y": sample["y"],
            "segment": sample["segment"],
            "distance_px": sample["distance"],
            "distance_mm": distance_mm,
            "length_mm": distance_mm,
            "actual_mm": distance_mm,
            "target_mm": target,
            "tolerance_mm": tolerance,
            "length_error_mm": error_mm,
            "error_mm": error_mm,
            "left_pattern_mm": left_mm,
            "right_pattern_mm": right_mm,
            "left_clearance_mm": left_mm,
            "right_clearance_mm": right_mm,
            "clearance_mm": clearance_mm,
            "nearest_pattern_mm": clearance_mm,
            "span_mm": span_mm,
            "clear_span_mm": span_mm,
            "window_samples": [item["index"] + 1 for item in window],
            "eligible": True,
        })
    widest = max((item["clearance_mm"] for item in candidates
                  if item["clearance_mm"] is not None), default=None)
    for item in candidates:
        length_term = item["length_error_mm"] / tolerance if tolerance > 0 else 0.0
        if widest:
            safety_term = 0.0 if item["clearance_mm"] is None else 1.0 - item["clearance_mm"] / widest
        else:
            safety_term = 0.0
        item["length_score"] = length_term
        item["safety_score"] = safety_term
        item["score"] = 0.5 * length_term + 0.5 * safety_term
    candidates.sort(key=lambda item: (round(item["score"], 12), item["length_error_mm"], item["index"]))
    for rank, item in enumerate(candidates, 1):
        item["rank"] = rank
        item["recommended"] = rank == 1
    recommended = candidates[0] if candidates else None
    nearest_length = min(candidates, key=lambda item: (item["length_error_mm"], item["index"])) \
        if candidates else None
    safest = max((item for item in candidates if item["clearance_mm"] is not None),
                 key=lambda item: (item["clearance_mm"], -item["index"]), default=None)
    return {
        "target_mm": target,
        "tolerance_mm": tolerance,
        "base_rgb": list(base),
        "channel_limit": BASE_RGB_LIMIT,
        "window_side_samples": CUT_WINDOW_SIDE,
        "candidates": candidates,
        "options": candidates,
        "cuts": candidates,
        "count": len(candidates),
        "recommended": recommended,
        "best": recommended,
        "recommended_index": recommended["index"] if recommended else None,
        "best_index": recommended["index"] if recommended else None,
        "nearest_length_index": nearest_length["index"] if nearest_length else None,
        "safest_index": safest["index"] if safest else None,
        "message": "" if candidates else "允许差内没有前后各两个采样点均为无花纹底色的切位，暂不提供切位。",
    }


def adopted_cut(config, values):
    index = config.get("cut_index")
    if index is None:
        return None
    index = int(index)
    if not 0 <= index < len(values):
        return {"index": index, "valid": False,
                "message": "采用的切位已不在当前采样点中，请重新查找。"}
    sample = values[index]
    distance_mm = sample["distance"] * config["mm_per_px"]
    cut = {
        "index": index,
        "sample_index": index,
        "sample": index + 1,
        "number": index + 1,
        "x": sample["x"],
        "y": sample["y"],
        "segment": sample["segment"],
        "distance_px": sample["distance"],
        "distance_mm": distance_mm,
        "length_mm": distance_mm,
        "actual_mm": distance_mm,
        "mm": distance_mm,
        "valid": True,
    }
    for key in ("cut_target_mm", "cut_tolerance_mm", "base_rgb"):
        cut[key] = config.get(key)
    cut["target_mm"] = config.get("cut_target_mm")
    cut["tolerance_mm"] = config.get("cut_tolerance_mm")
    cut["base_rgb"] = config.get("base_rgb")
    return cut


def decorate_disposition(values, cut):
    """Attach cut-off/kept status to every actual sample in path order."""
    cut_index = cut["index"] if cut and cut.get("valid") else None
    for sample in values:
        if cut_index is None:
            sample["disposition"] = None
            sample["status"] = None
            sample["state"] = None
            sample["decision"] = None
            sample["kept"] = None
            sample["cut"] = None
            sample["is_cut"] = None
            sample["offcut"] = None
            sample["cut_piece"] = None
            sample["side"] = None
            sample["verdict"] = None
            sample["verdict_cn"] = None
            sample["label"] = None
            continue
        if sample["index"] < cut_index:
            side, disposition, verdict = "before", "cut", "剪下"
            kept = False
        elif sample["index"] > cut_index:
            side, disposition, verdict = "after", "keep", "留下"
            kept = True
        else:
            side, disposition, verdict = "at", "cut_here", "切位"
            kept = False
        binary = "keep" if kept else "cut"
        sample["side"] = side
        sample["disposition"] = disposition
        # "status"/"state"/"decision" stay binary for callers distinguishing cut vs keep.
        sample["status"] = binary
        sample["state"] = "kept" if kept else "cut"
        sample["decision"] = binary
        sample["kept"] = kept
        sample["cut"] = not kept
        sample["is_cut"] = not kept
        sample["offcut"] = not kept
        sample["cut_piece"] = not kept
        sample["verdict"] = verdict
        sample["verdict_cn"] = verdict
        sample["label"] = verdict
        sample["label_cn"] = verdict
        sample["at_cut"] = side == "at"
    return values


def passes_for(values, x, y, mm_per_px, cut):
    """Every pass of the path through one original pixel, never de-duplicated."""
    passes = []
    occurrence = 0
    for sample in values:
        if sample["x"] != x or sample["y"] != y:
            continue
        occurrence += 1
        item = {
            "pass": occurrence,
            "visit": occurrence,
            "index": sample["index"],
            "sample_index": sample["index"],
            "sample": sample["index"] + 1,
            "number": sample["index"] + 1,
            "x": x,
            "y": y,
            "segment": sample["segment"],
            "actual_segment": sample["segment"],
            "distance": sample["distance"],
            "distance_px": sample["distance"],
            "distance_mm": sample["distance"] * mm_per_px,
            "rgb": sample["rgb"],
            "brightness": sample["brightness"],
        }
        for key in ("side", "disposition", "status", "state", "decision", "kept",
                    "cut", "is_cut", "offcut", "cut_piece", "verdict", "verdict_cn",
                    "label", "label_cn", "at_cut"):
            item[key] = sample.get(key)
        passes.append(item)
    return passes


def probe(image, x, y, config):
    row = point(image, x, y, config)
    values, _ = profile(image, config)
    cut = adopted_cut(config, values)
    decorate_disposition(values, cut)
    passes = passes_for(values, x, y, config["mm_per_px"], cut)
    row.update({
        "passes": passes,
        "samples": passes,
        "occurrences": passes,
        "visits": passes,
        "matches": passes,
        "sample_passes": passes,
        "sample_points": passes,
        "pass_count": len(passes),
        "sample_count": len(passes),
        "occurrence_count": len(passes),
        "match_count": len(passes),
        "on_path": bool(passes),
        "is_sample": bool(passes),
        "cut": cut,
        "adopted_cut": cut,
        "cut_position": cut,
    })
    if len(passes) >= 2:
        row["message"] = f"该原片像素被路径经过{len(passes)}次，已分别列出实际段号与去留。"
    elif len(passes) == 1:
        row["message"] = "该像素是一个实际采样点。"
    else:
        row["message"] = "该像素不是当前路径的实际采样点；RGB 为原片实值。"
    row["segments"] = [item["segment"] for item in passes]
    row["statuses"] = [item["status"] for item in passes]
    row["verdicts"] = [item["verdict_cn"] for item in passes]
    if len(passes) == 1:
        only = passes[0]
        for key in ("segment", "distance", "distance_px", "distance_mm", "index",
                    "sample", "side", "disposition", "status", "state", "kept",
                    "is_cut", "offcut", "cut_piece", "verdict", "verdict_cn",
                    "label", "at_cut"):
            row[key] = only[key]
    return row


def report(image, config, target_mm=None, tolerance_mm=None, base_rgb=None):
    values, end = profile(image, config)
    cut = adopted_cut(config, values)
    decorate_disposition(values, cut)
    search = cut_search(image, config, target_mm, tolerance_mm, base_rgb, values=values)
    recommended = search["recommended"] if search else None
    cut_mm = cut["distance_mm"] if cut and cut.get("valid") else None
    cut_index = cut["index"] if cut and cut.get("valid") else None
    return {"profile":values, "length_px":end, "length_mm":end*config["mm_per_px"],
            "points":config["points"], "segments":len(config["points"])-1,
            "cut":cut, "adopted_cut":cut, "cut_position":cut,
            "cut_index":cut_index, "adopted_cut_index":cut_index,
            "cut_mm":cut_mm, "cut_length_mm":cut_mm, "adopted_cut_mm":cut_mm,
            "has_cut": bool(cut and cut.get("valid")),
            "cut_search":search,
            "suggested_cut": recommended,
            "recommended_cut": recommended,
            "best_cut": recommended,
            "cut_options": search["candidates"] if search else [],
            "cut_candidates": search["candidates"] if search else [],
            "candidates": search["candidates"] if search else [],
            "cuts": search["candidates"] if search else []}


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
