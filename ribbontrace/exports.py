"""Export actual path sampling and its photographed layout."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json
import math

from PIL import Image, ImageDraw

from .route import png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow((
        "distance_px", "x_px", "y_px", "luma", "segment",
        "kind", "part", "type", "category", "state", "class", "source", "visibility",
        "evidence", "measured", "estimated",
    ))
    for p in result["profile"]:
        hidden = bool(p.get("estimated", p.get("kind") == "occluded"))
        brightness = p.get("brightness", p.get("luma"))
        writer.writerow((
            f'{p["distance"]:.4f}', p["x"], p["y"],
            "" if brightness is None else f'{brightness:.3f}',
            p["segment"],
            p.get("kind", "occluded" if hidden else "visible"),
            p.get("part", "estimated" if hidden else "seen"),
            p.get("type", "estimated" if hidden else "measured"),
            p.get("category", "estimated" if hidden else "observed"),
            p.get("state", "occluded_estimate" if hidden else "visible_measurement"),
            "estimated" if hidden else "measured",
            "occlusion_bridge" if hidden else "source_photo",
            "hidden_estimate" if hidden else "visible",
            p.get("evidence", "遮挡估段" if hidden else "实见"),
            "0" if hidden else "1",
            "1" if hidden else "0",
        ))
    return buffer.getvalue().encode()


def _dashed_line(draw, points, fill, width=4, dash=10, gap=7):
    """Draw pixel-space dashed polyline segments along cumulative length."""
    if len(points) < 2:
        return
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        travelled = 0.0
        while travelled < length:
            a = travelled
            b = min(length, travelled + dash)
            draw.line((x1 + ux * a, y1 + uy * a, x1 + ux * b, y1 + uy * b),
                      fill=fill, width=width)
            travelled += dash + gap


def proof(image, card, result):
    h,w = image.shape[:2]
    output = Image.new("RGB",(max(420,w),h+65),"#2c3435")
    output.paste(Image.fromarray(image),(0,0))
    draw = ImageDraw.Draw(output)

    # The hand-entered straight fallback goes underneath the adopted route.
    points = [tuple(p) for p in result["points"]]
    draw.line(points,fill="#fbe8b6",width=2,joint="curve")

    # Saved reroutes are drawn above it; the fixed hand points stay on top.
    traces = result.get("traces") or card.get("settings", {}).get("traces", {})
    for key, trace in traces.items():
        if not trace.get("path"):
            continue
        path = [(float(x), float(y)) for x, y in trace["path"]]
        draw.line(path, fill="#062b35", width=6, joint="curve")
        draw.line(path, fill="#83e7f2", width=3, joint="curve")
        for occlusion in trace.get("occlusions", []):
            a = tuple(occlusion["start"])
            b = tuple(occlusion["end"])
            # Pale halo plus magenta dashes makes the estimated bridge visible
            # over both the gold band and the dark/light ribbon edges.
            draw.line((a, b), fill="#fff4c2", width=7)
            _dashed_line(draw, [a, b], fill="#ff2fb3", width=4, dash=9, gap=6)
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            label = f'Est. {occlusion.get("length_px", 0):.1f}px'
            draw.rectangle((mx + 7, my - 25, mx + 118, my - 7), fill="#3a2334")
            draw.text((mx + 12, my - 21), label, fill="#ffd7f1")

    for i,(x,y) in enumerate(points,1):
        draw.ellipse((x-4,y-4,x+4,y+4),fill="#f6db83",outline="#263836",width=1)
        draw.text((x+6,y-12),str(i),fill="#f9f3de",stroke_width=1,stroke_fill="#263836")
    draw.text((14,h+9),f'Marked path {result["length_mm"]:.2f} mm / {result["length_px"]:.2f} px',fill="#e2eeed")
    draw.text((14,h+27),f'{result["segments"]} segments / sample step {card["settings"]["step"]} px',fill="#f2d491")
    visible_px = result.get("visible_length_px", result["length_px"])
    estimated_px = result.get("estimated_length_px", result.get("occluded_length_px", 0.0))
    draw.text((14,h+46),
              f'Seen {visible_px:.1f}px + est. cover {estimated_px:.1f}px '
              f'(<={result.get("bridge_limit_px", 24)}); cover unsampled',
              fill="#8fe8f2")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as stream:
        stream.writestr("ribbon.png",original)
        stream.writestr("reading.json",json.dumps({"card":card,"length_mm":result["length_mm"],
                                                    "length_px":result["length_px"],
                                                    "visible_length_px":result.get("visible_length_px"),
                                                    "occluded_length_px":result.get("occluded_length_px"),
                                                    "segment_lengths":result.get("segment_lengths"),
                                                    "profile_kinds":{
                                                        "visible":"实见原片取样",
                                                        "occluded":"遮挡估段；luma为空，遮挡上方颜色不作为该带实测"
                                                    }},ensure_ascii=False,indent=2))
        stream.writestr("path-profile.csv",trace_table(result))
        stream.writestr("ribbon-proof.png",proof(image,card,result))
    return buffer.getvalue()
