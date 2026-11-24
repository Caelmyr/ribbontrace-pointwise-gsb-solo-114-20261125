"""Measure a hand-marked ribbon path and keep its actual source pixels."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from scipy.interpolate import PchipInterpolator

from image_lab.validation import Invalid, integer, number


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
    return {"points": points,
            "mm_per_px": number(value.get("mm_per_px"), "标尺", 0.001, 500),
            "step": integer(value.get("step"), "采样间隔", 1, 64)}


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


def profile(image, config):
    path, cumulative = geometry(config)
    end = float(cumulative[-1])
    distances = np.r_[np.arange(0,end,config["step"]), end]
    xs = np.rint(np.interp(distances,cumulative,path[:,0])).astype(int)
    ys = np.rint(np.interp(distances,cumulative,path[:,1])).astype(int)
    segments = np.minimum(np.searchsorted(cumulative,distances,side="right")-1,len(path)-2)
    levels = image[ys,xs] @ LUMA
    return [{"distance":float(d),"x":int(x),"y":int(y),"brightness":float(level),"segment":int(segment+1)}
            for d,x,y,level,segment in zip(distances,xs,ys,levels,segments)], end


def report(image, config):
    values, end = profile(image, config)
    return {"profile":values, "length_px":end, "length_mm":end*config["mm_per_px"],
            "points":config["points"], "segments":len(config["points"])-1}


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
