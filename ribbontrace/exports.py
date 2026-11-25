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
    cut = result.get("cut") if result.get("has_cut") else None
    output = Image.new("RGB",(max(420,w),h+88),"#2c3435")
    output.paste(Image.fromarray(image),(0,0))
    draw = ImageDraw.Draw(output)
    points = [tuple(p) for p in result["points"]]
    draw.line(points,fill="#fbe8b6",width=2,joint="curve")
    for i,(x,y) in enumerate(points,1):
        draw.ellipse((x-4,y-4,x+4,y+4),fill="#f6db83")
        draw.text((x+6,y-12),str(i),fill="#f9f3de",stroke_width=1,stroke_fill="#263836")
    # Colour the actual sampled path by cut-off / kept side once a cut is adopted.
    if cut:
        for p in result["profile"]:
            if p.get("disposition") == "cut":
                colour = "#ff8a7a"
            elif p.get("disposition") == "keep":
                colour = "#8be0a4"
            else:
                colour = "#ff4d5e"
            draw.ellipse((p["x"]-2,p["y"]-2,p["x"]+2,p["y"]+2),fill=colour)
    if cut:
        cx,cy = cut["x"],cut["y"]
        for radius,colour,width in ((13,"#ffffff",5),(13,"#e11d48",2),(7,"#e11d48",2)):
            draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),outline=colour,width=width)
        draw.line((cx-18,cy,cx-8,cy),fill="#e11d48",width=2)
        draw.line((cx+8,cy,cx+18,cy),fill="#e11d48",width=2)
        draw.line((cx,cy-18,cx,cy-8),fill="#e11d48",width=2)
        draw.line((cx,cy+8,cx,cy+18),fill="#e11d48",width=2)
        label = f'CUT #{cut["sample"]} {cut["distance_mm"]:.2f} mm'
        tx,ty = cx+15,cy-26
        draw.text((tx+1,ty+1),label,fill="#2b1418")
        draw.text((tx,ty),label,fill="#ffd9de")
    draw.text((14,h+12),f'Marked path {result["length_mm"]:.2f} mm / {result["length_px"]:.2f} px',fill="#e2eeed")
    draw.text((14,h+31),f'{result["segments"]} segments / sample step {card["settings"]["step"]} px',fill="#f2d491")
    if cut:
        draw.text((14,h+50),
                  f'Cut from start {cut["distance_mm"]:.2f} mm actual at original pixel ({cut["x"]},{cut["y"]}), segment {cut["segment"]}',
                  fill="#ffb3c0")
        draw.text((14,h+68),"red samples = cut off / green samples = kept",fill="#9fe6b5")
    else:
        draw.text((14,h+50),"No adopted cut; whole path retained.",fill="#9fb3ae")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as stream:
        stream.writestr("ribbon.png",original)
        stream.writestr("reading.json",json.dumps({"card":card,"length_mm":result["length_mm"],
                                                    "length_px":result["length_px"],
                                                    "cut":result.get("cut"),
                                                    "cut_search":result.get("cut_search")},
                                                   ensure_ascii=False,indent=2))
        stream.writestr("path-profile.csv",trace_table(result))
        stream.writestr("ribbon-proof.png",proof(image,card,result))
    return buffer.getvalue()
