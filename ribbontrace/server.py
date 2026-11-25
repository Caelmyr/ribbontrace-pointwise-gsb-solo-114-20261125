"""Connect the ribbon path desk to the photo preparation workspace."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import exports, route
from .store import Cards


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


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
        return jsonify(cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                                    size, image, route.defaults(size))), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, object_value(request.get_json(silent=True), "缎带卡设置"))
                       if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = route.pixels(cards.photo(key))
        return card, image, route.report(image, card["settings"])

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        return jsonify(revision=card["revision"], **result)

    @api.get("/cards/<key>/probe")
    def probe(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "Y", 0, card["height"] - 1)
        return jsonify(route.point(route.pixels(cards.photo(key)), x, y, card["settings"]))

    def segment_from_request(payload, card):
        args = request.args
        points = card["settings"]["points"]
        last = len(points) - 1
        zero_based = payload.get("segment_index", args.get("segment_index",
                      payload.get("pair_index", args.get("pair_index"))))
        if zero_based is not None:
            return integer(zero_based, "相邻点对", 0, last - 1) + 1

        segment = None
        for source in (payload, args):
            for key in ("segment", "segment_number", "pair", "adjacent_pair"):
                if source.get(key) is not None:
                    segment = source.get(key)
                    break
            if segment is not None:
                break
        if segment is None and args.get("index") is not None:
            return integer(args.get("index"), "相邻点对", 0, last - 1) + 1

        if segment is None:
            pair = payload.get("pair_points") or payload.get("endpoints")
            start = payload.get("start", payload.get("a", payload.get("point_a")))
            end = payload.get("end", payload.get("b", payload.get("point_b")))
            if isinstance(pair, list) and len(pair) == 2:
                start, end = pair
            if isinstance(start, (list, tuple)) and isinstance(end, (list, tuple)):
                def close(point, expected):
                    return abs(float(point[0]) - expected[0]) <= 2 and \
                           abs(float(point[1]) - expected[1]) <= 2
                for i in range(last):
                    if close(start, points[i]) and close(end, points[i + 1]):
                        return i + 1
                    if close(start, points[i + 1]) and close(end, points[i]):
                        return i + 1
        return integer(segment, "相邻点对", 1, last)

    @api.get("/cards/<key>/retrace")
    @api.get("/cards/<key>/reroute")
    @api.get("/cards/<key>/retrace/<int:segment>")
    @api.get("/cards/<key>/reroute/<int:segment>")
    def retrace_preview(key, segment=None):
        card = cards.read(key, request.args.get("revision"))
        payload = {"segment": segment} if segment is not None else {}
        segment_number = segment_from_request(payload, card)
        image = route.pixels(cards.photo(key))
        candidate = route.retrace_segment(image, card["settings"], segment_number)
        return jsonify(retrace=candidate, reroute=candidate, **candidate)

    @api.post("/cards/<key>/retrace")
    @api.post("/cards/<key>/reroute")
    @api.post("/cards/<key>/retrace/<int:segment>")
    @api.post("/cards/<key>/reroute/<int:segment>")
    def retrace_apply(key, segment=None):
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        payload = object_value(body, "重走段请求")
        if segment is not None:
            payload = {**payload, "segment": payload.get("segment", segment)}
        query_revision = request.args.get("revision")
        card = cards.read(key, payload.get("revision", query_revision))
        segment = segment_from_request(payload, card)
        image = route.pixels(cards.photo(key))

        if payload.get("reset") or payload.get("action") == "reset" or payload.get("mode") == "straight":
            traces = dict(card["settings"].get("traces", {}))
            traces.pop(str(segment), None)
            updated = cards.update(key, {
                "revision": payload.get("revision", card["revision"]),
                "name": card["name"],
                "settings": {**card["settings"], "traces": traces},
            })
            return jsonify({**updated, "reset_segment": segment,
                            "result": route.report(image, updated["settings"])})

        candidate = None
        manual = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else payload
        if isinstance(manual, dict) and isinstance(manual.get("path", manual.get("points")), list):
            candidate = {**manual, "segment": segment}
        else:
            candidate = route.retrace_segment(image, card["settings"], segment)
        if payload.get("preview") is True or payload.get("apply") is False or \
                payload.get("action") == "preview":
            return jsonify(retrace=candidate, reroute=candidate, **candidate)

        traces = dict(card["settings"].get("traces", {}))
        traces[str(segment)] = candidate
        updated = cards.update(key, {
            "revision": payload.get("revision", card["revision"]),
            "name": payload.get("name", card["name"]),
            "settings": {**card["settings"], "traces": traces},
        })
        saved_candidate = updated["settings"].get("traces", {}).get(str(segment), candidate)
        result = route.report(image, updated["settings"])
        return jsonify({**updated, **saved_candidate, "retrace": saved_candidate,
                        "reroute": saved_candidate, "result": result})

    @api.delete("/cards/<key>/retrace")
    @api.delete("/cards/<key>/reroute")
    @api.delete("/cards/<key>/retrace/<int:segment>")
    @api.delete("/cards/<key>/reroute/<int:segment>")
    def retrace_reset(key, segment=None):
        card = cards.read(key, request.args.get("revision"))
        payload = {"segment": segment} if segment is not None else {}
        segment_number = segment_from_request(payload, card)
        image = route.pixels(cards.photo(key))
        traces = dict(card["settings"].get("traces", {}))
        traces.pop(str(segment_number), None)
        updated = cards.update(key, {
            "revision": request.args.get("revision", card["revision"]),
            "name": card["name"],
            "settings": {**card["settings"], "traces": traces},
        })
        return jsonify({**updated, "reset_segment": segment_number,
                        "result": route.report(image, updated["settings"])})

    @api.post("/cards/<key>/segments/<int:segment>/retrace")
    @api.post("/cards/<key>/segments/<int:segment>/reroute")
    def retrace_segment_rest(key, segment):
        body = request.get_json(silent=True) or {}
        payload = object_value(body, "重走段请求") if body else {}
        card = cards.read(key, payload.get("revision", request.args.get("revision")))
        integer(segment, "相邻点对", 1, len(card["settings"]["points"]) - 1)
        image = route.pixels(cards.photo(key))
        candidate = route.retrace_segment(image, card["settings"], segment)
        traces = dict(card["settings"].get("traces", {}))
        traces[str(segment)] = candidate
        updated = cards.update(key, {
            "revision": payload.get("revision", card["revision"]),
            "name": card["name"],
            "settings": {**card["settings"], "traces": traces},
        })
        return jsonify({**updated, **candidate, "retrace": candidate,
                        "result": route.report(image, updated["settings"])})

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
