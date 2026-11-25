"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false, selectedSegment: 1};
const settings = [...document.querySelectorAll("[data-setting]")];

async function api(path, options) {
  const response = await fetch("/ribbontrace" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
}

function traceMap() {
  return state.result?.traces || state.card?.settings?.traces || {};
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  if (state.draft) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#faf1c8";
    ctx.beginPath();
    state.draft.forEach(([x,y],i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
    ctx.stroke();
  } else {
    const points = state.result.points;
    const traces = traceMap();
    // The hand-entered chords remain visible as the straight fallback.
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(250,241,200,.58)";
    ctx.beginPath();
    points.forEach(([x,y],i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
    ctx.stroke();

    const selected = Number(get("segment-select")?.value || state.selectedSegment || 1);
    points.slice(0, -1).forEach((point, i) => {
      const segment = i + 1;
      const trace = traces[String(segment)] || traces[segment];
      if (segment === selected) {
        ctx.lineWidth = 9;
        ctx.strokeStyle = "rgba(255,255,255,.22)";
        ctx.beginPath();
        ctx.moveTo(point[0], point[1]);
        ctx.lineTo(points[i + 1][0], points[i + 1][1]);
        ctx.stroke();
      }
      if (!trace?.path) return;
      ctx.beginPath();
      trace.path.forEach(([x,y],j) => j ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
      ctx.lineWidth = 5;
      ctx.strokeStyle = "rgba(5,35,43,.9)";
      ctx.stroke();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = "#83e7f2";
      ctx.stroke();

      ctx.setLineDash([7, 5]);
      ctx.lineWidth = 3;
      ctx.strokeStyle = "#ff2fb3";
      for (const gap of trace.occlusions || []) {
        ctx.beginPath();
        ctx.moveTo(gap.start[0], gap.start[1]);
        ctx.lineTo(gap.end[0], gap.end[1]);
        ctx.stroke();
      }
      ctx.setLineDash([]);
    });
  }

  const pending = state.draft || state.result.points;
  ctx.font = "bold 13px system-ui";
  pending.forEach(([x,y],i) => {
    ctx.fillStyle = "#f8d98b";
    ctx.beginPath();
    ctx.arc(x,y,5,0,2*Math.PI);
    ctx.fill();
    ctx.strokeStyle = "#25393a";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "#fff9e6";
    ctx.fillText(String(i+1),x+9,y-7);
  });
  if (probe) {
    ctx.strokeStyle = "#30393b";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
  }
}


function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width,height} = canvas;
  const {result} = state;
  const xAt = (x) => 40 + (width-78)*x/result.length_px;
  const yAt = (y) => height-27 - (height-45)*y/255;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#fcfaf4";
  ctx.fillRect(0,0,width,height);

  const hiddenRows = result.profile.filter((p) => p.brightness === null);
  if (hiddenRows.length) {
    const x0 = xAt(hiddenRows[0].distance);
    const x1 = xAt(hiddenRows[hiddenRows.length - 1].distance);
    ctx.fillStyle = "rgba(255,47,179,.10)";
    ctx.fillRect(x0, 18, Math.max(2, x1-x0), height-45);
    ctx.fillStyle = "#9b3776";
    ctx.font = "11px system-ui";
    ctx.fillText("遮挡估段 · 不取色", x0 + 5, 31);
  }

  ctx.font = "12px monospace";
  ctx.fillStyle = "#646c63";
  ctx.strokeStyle = "#dddcd1";
  ctx.lineWidth = 1;
  for (const level of [0,128,255]) {
    ctx.fillText(level,5,yAt(level)+3);
    ctx.beginPath();
    ctx.moveTo(xAt(0),yAt(level));
    ctx.lineTo(xAt(result.length_px),yAt(level));
    ctx.stroke();
  }
  ctx.fillText("0",xAt(0),height-7);
  ctx.fillText(result.length_px.toFixed(1),xAt(result.length_px)-16,height-7);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#367675";
  ctx.beginPath();
  let pen = false;
  result.profile.forEach((p) => {
    if (p.brightness === null || p.brightness === undefined) {
      pen = false;
      return;
    }
    if (!pen) ctx.moveTo(xAt(p.distance),yAt(p.brightness));
    else ctx.lineTo(xAt(p.distance),yAt(p.brightness));
    pen = true;
  });
  ctx.stroke();
}


function pointLabel(point) {
  return "(" + point.x + ", " + point.y + ") · RGB " + point.rgb.join("/") +
    " · 亮度 " + point.brightness.toFixed(1);
}

function segmentLabel(i, points, traces) {
  const trace = traces[String(i)] || traces[i];
  const gap = trace?.occluded_length_px || 0;
  return `第${i}对（点${i}→点${i+1}） · ${points[i-1].join(",")}→${points[i].join(",")}` +
    (trace ? ` · 已重走${gap ? ` / 估段${gap.toFixed(1)}px` : " / 全实见"}` : " · 直线");
}

function updateSegmentOptions() {
  const select = get("segment-select");
  const points = state.result?.points || state.card?.settings?.points || [];
  const traces = traceMap();
  const previous = Number(select.value || state.selectedSegment || 1);
  select.replaceChildren();
  for (let i = 1; i < points.length; i += 1) {
    select.append(new Option(segmentLabel(i, points, traces), String(i)));
  }
  const next = Math.min(Math.max(1, previous), Math.max(1, points.length - 1));
  select.value = String(next);
  state.selectedSegment = next;
  updateSegmentLength();
}

function updateSegmentLength() {
  const element = get("segment-length");
  if (!element || !state.result) return;
  const segment = Number(get("segment-select")?.value || state.selectedSegment || 1);
  const summary = (state.result.segment_lengths || []).find((item) => item.segment === segment);
  const mmPerPx = state.card?.settings?.mm_per_px || 1;
  if (!summary) {
    element.textContent = "";
    return;
  }
  if (summary.retraced) {
    element.textContent = `本对：实见 ${summary.visible_length_px.toFixed(1)}px` +
      `（${(summary.visible_length_px * mmPerPx).toFixed(2)}mm） · ` +
      `遮挡估段 ${summary.occluded_length_px.toFixed(1)}px` +
      `（${(summary.occluded_length_px * mmPerPx).toFixed(2)}mm）`;
  } else {
    element.textContent = `本对：手填直线 ${summary.length_px.toFixed(1)}px` +
      `（${(summary.length_px * mmPerPx).toFixed(2)}mm），暂无估段`;
  }
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? result.points.at(-1)[0];
  get("probe-y").value = state.probe?.y ?? result.points.at(-1)[1];
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  get("path-points").value = result.points.map((p) => p.join(",")).join("\n");
  get("length-value").textContent = result.length_mm.toFixed(2);
  get("length-unit").textContent = "mm";
  const traceCount = Object.keys(result.traces || {}).length;
  get("measurement-state").textContent = result.segments + " 段" + (traceCount ? ` · ${traceCount}段重走` : "");
  const occMm = result.occluded_length_mm || 0;
  const visibleMm = result.visible_length_mm ?? result.length_mm;
  const occPx = result.occluded_length_px || 0;
  const visiblePx = result.visible_length_px ?? result.length_px;
  get("length-breakdown").textContent =
    `实见 ${visibleMm.toFixed(2)} mm / ${visiblePx.toFixed(1)} px` +
    ` · 遮挡估段 ${occMm.toFixed(2)} mm / ${occPx.toFixed(1)} px`;
  get("selected-level").textContent = result.length_px.toFixed(2) + " px · " + result.profile.length + " 个沿线读数";
  updateSegmentOptions();
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/ribbontrace" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/ribbontrace" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  const selectedSegment = Math.min(2, Math.max(1, card.settings.points.length - 1));
  Object.assign(state, {card, result, photo, probe: null, draft: null, selectedSegment});
  get("library").value = card.id;
  if (window.history?.replaceState) window.history.replaceState(null, "", "#card=" + card.id);
  show();
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
function pointSegmentDistance(px, py, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const length2 = dx * dx + dy * dy;
  if (!length2) return Math.hypot(px - a[0], py - a[1]);
  const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / length2));
  return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
}

function nearestSegment(x, y) {
  const points = state.result?.points || [];
  let best = 1;
  let bestDistance = Infinity;
  for (let i = 0; i < points.length - 1; i += 1) {
    const distance = pointSegmentDistance(x, y, points[i], points[i + 1]);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = i + 1;
    }
  }
  return best;
}

function selectSegmentAt(x, y) {
  const best = nearestSegment(x, y);
  state.selectedSegment = best;
  get("segment-select").value = String(best);
  paintSource();
  const trace = traceMap()[String(best)];
  notice(trace ? `已选第${best}对；当前是重走估段，可切回手填直线。` : `已选第${best}对相邻点，点“重走这一段”。`, "retrace-notice");
}

get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  if (state.mode === "segment") {
    selectSegmentAt(x, y);
    return;
  }
  const nearest = nearestSegment(x, y);
  state.selectedSegment = nearest;
  get("segment-select").value = String(nearest);
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      const points = pointsFromForm();
      if (points.length === 32) throw new Error("路径最多32点");
      if (points.at(-1)?.[0] === x && points.at(-1)?.[1] === y) throw new Error("连续路径点不能重复");
      points.push([x,y]);
      get("path-points").value = points.map((p) => p.join(",")).join("\n");
      state.draft = points;
      paintSource();
      notice("已加入待保存的路径点。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
function pointsFromForm() {
  return get("path-points").value.trim().split(/\n/).filter((line) => line.trim()).map((line) => {
    const match = line.match(/^\s*(\d+)\s*[,，]\s*(\d+)\s*$/);
    if (!match) throw new Error("路径点每行按 X,Y 填写");
    return [Number(match[1]), Number(match[2])];
  });
}

function pointsFromFormOrNull() {
  try {
    return pointsFromForm();
  } catch {
    return null;
  }
}

get("path-points").addEventListener("input", () => {
  state.draft = pointsFromFormOrNull();
  if (state.draft) {
    updateSegmentOptions();
    paintSource();
  }
});

get("segment-select").addEventListener("change", (event) => {
  state.selectedSegment = Number(event.target.value);
  notice("", "retrace-notice");
  updateSegmentLength();
  paintSource();
});

get("retrace-apply").addEventListener("click", () => action(async () => {
  const segment = Number(get("segment-select").value || state.selectedSegment || 1);
  state.selectedSegment = segment;
  const card = await api(route("/retrace"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, segment, apply: true}),
  });
  await open(card);
  state.selectedSegment = segment;
  updateSegmentOptions();
  paintSource();
  await library();
  const gap = card.retrace?.occluded_length_px || 0;
  notice(`第${segment}对已重走并保存；遮挡估段 ${gap.toFixed(1)} px，校样和 CSV 可复核。`, "retrace-notice");
}, "retrace-notice"));

get("retrace-reset").addEventListener("click", () => action(async () => {
  const segment = Number(get("segment-select").value || state.selectedSegment || 1);
  state.selectedSegment = segment;
  const card = await api(route("/retrace"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, segment, reset: true}),
  });
  await open(card);
  state.selectedSegment = segment;
  updateSegmentOptions();
  paintSource();
  await library();
  notice(`第${segment}对已切回手填直线。`, "retrace-notice");
}, "retrace-notice"));

get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    fields.points = pointsFromForm();
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(async () => {
  await library();
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("card");
  const cardId = hash || (window.location.hash.startsWith("#") && /^[0-9a-f]{32}$/.test(window.location.hash.slice(1))
    ? window.location.hash.slice(1) : "");
  if (cardId) {
    const card = await api("/cards/" + cardId);
    get("library").value = card.id;
    await open(card);
  }
});
