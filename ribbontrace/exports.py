"""Export actual path sampling and its photographed layout."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .route import png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("distance_px","x_px","y_px","luma","segment"))
    for p in result["profile"]:
        writer.writerow((f'{p["distance"]:.4f}',p["x"],p["y"],f'{p["brightness"]:.3f}',p["segment"]))
    return buffer.getvalue().encode()


def proof(image, card, result):
    h,w = image.shape[:2]
    output = Image.new("RGB",(max(420,w),h+65),"#2c3435")
    output.paste(Image.fromarray(image),(0,0))
    draw = ImageDraw.Draw(output)
    points = [tuple(p) for p in result["points"]]
    draw.line(points,fill="#fbe8b6",width=2,joint="curve")
    for i,(x,y) in enumerate(points,1):
        draw.ellipse((x-4,y-4,x+4,y+4),fill="#f6db83")
        draw.text((x+6,y-12),str(i),fill="#f9f3de",stroke_width=1,stroke_fill="#263836")
    draw.text((14,h+14),f'Marked path {result["length_mm"]:.2f} mm / {result["length_px"]:.2f} px',fill="#e2eeed")
    draw.text((14,h+35),f'{result["segments"]} segments / sample step {card["settings"]["step"]} px',fill="#f2d491")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as stream:
        stream.writestr("ribbon.png",original)
        stream.writestr("reading.json",json.dumps({"card":card,"length_mm":result["length_mm"],
                                                    "length_px":result["length_px"]},ensure_ascii=False,indent=2))
        stream.writestr("path-profile.csv",trace_table(result))
        stream.writestr("ribbon-proof.png",proof(image,card,result))
    return buffer.getvalue()
