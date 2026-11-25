"""Connect the ribbon path desk to the photo preparation workspace."""

from io import BytesIO
import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import exports, route
from .store import Cards


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


def query_number(*names):
    for name in names:
        raw = request.args.get(name)
        if raw is not None and raw != "":
            return raw
    return None


def seek_from_args():
    """Read optional stateless cut-search overrides from the query string."""
    target = query_number("target_mm", "cut_target_mm", "cut_mm", "want_mm",
                          "target", "length_mm", "cut_length_mm", "mm")
    tolerance = query_number("tolerance_mm", "cut_tolerance_mm", "tolerance",
                            "allowance_mm", "allowance", "allow", "delta_mm")
    if target is not None:
        target = float(target)
    if tolerance is not None:
        tolerance = float(tolerance)
    base = None
    for name in ("base_rgb", "base_color", "base_colour", "base"):
        raw = request.args.get(name)
        if raw:
            try:
                raw = json.loads(raw) if raw.lstrip().startswith("[") else raw
            except json.JSONDecodeError:
                pass
            base = route.parse_rgb(raw)
            break
    if base is None and request.args.get("base_r") is not None:
        base = route.parse_rgb([request.args.get("base_r"),
                                request.args.get("base_g"),
                                request.args.get("base_b")])
    return target, tolerance, base


def create_app(directory=None):
    root = Path(directory or os.environ.get("RIBBONTRACE_DATA", ".ribbontrace-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("ribbontrace", __name__, url_prefix="/ribbontrace")

    @desk.get("/")
    def home():
        return render_template("ribbontrace.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="ribbontrace")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 20 * 1024 * 1024
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            raise Invalid("请选择缎带原照")
        size, image = route.decode(upload.stream.read(route.MAX_IMAGE_BYTES + 1))
        card = cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                            size, image, route.defaults(size))
        return jsonify(route.card_view(card)), 201

    @api.post("/practice")
    def practice():
        return jsonify(route.card_view(cards.demo())), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        if request.method == "PATCH":
            updated = cards.update(key, object_value(request.get_json(silent=True), "缎带卡设置"))
            return jsonify(route.card_view(updated))
        return jsonify(route.card_view(cards.read(key)))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = route.pixels(cards.photo(key))
        target, tolerance, base = seek_from_args()
        return card, image, route.report(image, card["settings"], target, tolerance, base)

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        return jsonify(revision=card["revision"], **result)

    @api.get("/cards/<key>/probe")
    @api.get("/cards/<key>/sample")
    @api.get("/cards/<key>/samples/<int:number>")
    def probe(key, number=None):
        card = cards.read(key, request.args.get("revision"))
        image = route.pixels(cards.photo(key))
        settings = card["settings"]
        # Allow lookup by actual sample number (#N, 1-based) or index (0-based)
        # in addition to raw X/Y point reading.
        x_arg, y_arg = request.args.get("x"), request.args.get("y")
        if x_arg is None and y_arg is None:
            values, _ = route.profile(image, settings)
            one_based_number = number
            if one_based_number is None and request.args.get("sample") is not None:
                one_based_number = integer(request.args.get("sample"), "取样点#", 1, len(values))
            if one_based_number is not None:
                idx = one_based_number - 1
            else:
                index_arg = request.args.get("index", request.args.get("sample_index"))
                if index_arg is None:
                    raise Invalid("请提供 X/Y 或取样点编号")
                idx = integer(index_arg, "取样点序号", 0, len(values) - 1)
            chosen = values[idx]
            x, y = chosen["x"], chosen["y"]
        else:
            x = integer(x_arg, "X", 0, card["width"] - 1)
            y = integer(y_arg, "Y", 0, card["height"] - 1)
        return jsonify(route.probe(image, x, y, settings))

    def cut_search_response(key):
        card = cards.read(key, request.args.get("revision"))
        image = route.pixels(cards.photo(key))
        target, tolerance, base = seek_from_args()
        search = route.cut_search(image, card["settings"], target, tolerance, base, strict=True)
        return jsonify(revision=card["revision"], **search)

    @api.get("/cards/<key>/cut")
    @api.get("/cards/<key>/cuts")
    @api.get("/cards/<key>/cut/search")
    @api.get("/cards/<key>/cut/options")
    @api.get("/cards/<key>/cut-position")
    @api.get("/cards/<key>/suggest-cut")
    @api.get("/cards/<key>/suggest")
    def cut_options(key):
        return cut_search_response(key)

    def adopt_cut(key, index_override=None):
        card = cards.read(key)
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        incoming = {}
        for source in (body.get("settings"), body.get("cut"), body):
            if isinstance(source, dict):
                incoming.update(route.normalize_cut_keys(source))
        index = index_override
        if index is None:
            index = route._pick(incoming, route.CUT_INDEX_KEYS)
        if index is None and incoming.get("index") is not None:
            index = incoming.get("index")
        if index is None and ("sample" in incoming or "number" in incoming):
            one_based = integer(incoming.get("sample", incoming.get("number")), "切位取样点", 1)
            index = one_based - 1
        if index is None and incoming.get("x") is not None and incoming.get("y") is not None:
            image = route.pixels(cards.photo(key))
            values, _ = route.profile(image, card["settings"])
            x = integer(incoming.get("x"), "切位X", 0, card["width"] - 1)
            y = integer(incoming.get("y"), "切位Y", 0, card["height"] - 1)
            hits = [item["index"] for item in values if item["x"] == x and item["y"] == y]
            if not hits:
                raise Invalid("该坐标不是实际采样点，无法采用为切位")
            index = hits[0]
        if index is None:
            if body.get("clear") or incoming.get("clear"):
                index = None
            else:
                raise Invalid("请选择一个切位")
        if index is not None:
            index = integer(index, "采用切位", 0)
            image = route.pixels(cards.photo(key))
            sample_count = len(route.profile(image, card["settings"])[0])
            if index >= sample_count:
                raise Invalid("切位不在当前实际采样点中，请重新查找")
        settings_patch = {k: v for k, v in incoming.items()
                          if k in ("cut_target_mm", "cut_tolerance_mm", "base_rgb")}
        settings_patch["cut_index"] = index
        revision = body.get("revision", card["revision"])
        updated = cards.update(key, {"revision": revision, "settings": settings_patch})
        image = route.pixels(cards.photo(key))
        result = route.report(image, updated["settings"])
        return jsonify({"card": route.card_view(updated), "revision": updated["revision"], **result}), 200

    @api.post("/cards/<key>/cut")
    @api.post("/cards/<key>/cut/adopt")
    @api.post("/cards/<key>/cuts/adopt")
    @api.post("/cards/<key>/adopt-cut")
    @api.post("/cards/<key>/adopt")
    @api.post("/cards/<key>/cut-position")
    def cut_adopt(key):
        return adopt_cut(key)

    @api.post("/cards/<key>/cuts/<int:index>/adopt")
    @api.post("/cards/<key>/cut/<int:index>/adopt")
    def cut_adopt_index(key, index):
        return adopt_cut(key, index)

    @api.delete("/cards/<key>/cut")
    @api.post("/cards/<key>/cut/clear")
    def cut_clear(key):
        body = request.get_json(silent=True) or {}
        updated = cards.update(key, {"revision": body.get("revision", cards.read(key)["revision"]),
                                     "settings": {"cut_index": None}})
        return jsonify(route.card_view(updated))

    @api.get("/cards/<key>/export/profile.csv")
    def profile(key):
        _, _, result = measurement(key)
        return download(exports.trace_table(result), "text/csv", "path-profile.csv")

    @api.get("/cards/<key>/export/proof.png")
    def proof(key):
        card, image, result = measurement(key)
        return download(exports.proof(image, card, result), "image/png", "ribbon-proof.png")

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card, image, result = measurement(key)
        package = exports.package(card, cards.photo(key).read_bytes(), image, result)
        return download(package, "application/zip", f"ribbontrace-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        card = cards.read(key, request.args.get("revision"))
        image = route.pixels(cards.photo(key)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["photo_name"])
        project = services["store"].create(f'{card["name"]} · 原片准备')
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk


# Convenient WSGI entry point (gunicorn/waitress `ribbontrace.server:app`).
app = create_app()
application = app
