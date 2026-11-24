"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false};
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

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  ctx.lineWidth = 2;
  const pending = state.draft || state.result.points;
  ctx.strokeStyle = "#faf1c8";
  ctx.beginPath();
  pending.forEach(([x,y],i) => {
    if (i === 0) ctx.moveTo(x,y);
    else ctx.lineTo(x,y);
  });
  ctx.stroke();
  ctx.font = "bold 13px system-ui";
  pending.forEach(([x,y],i) => {
    ctx.fillStyle = "#f8d98b";
    ctx.beginPath();
    ctx.arc(x,y,5,0,2*Math.PI);
    ctx.fill();
    ctx.strokeStyle = "#25393a";
    ctx.strokeText(String(i+1),x+9,y-7);
    ctx.fillStyle = "#fff9e6";
    ctx.fillText(String(i+1),x+9,y-7);
    ctx.strokeStyle = "#faf1c8";
  });
  if (probe) {
    ctx.strokeStyle = "#30393b";
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
  ctx.font = "12px monospace";
  ctx.fillStyle = "#646c63";
  ctx.strokeStyle = "#dddcd1";
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
  result.profile.forEach((p,i) => {
    if (!i) ctx.moveTo(xAt(p.distance),yAt(p.brightness));
    else ctx.lineTo(xAt(p.distance),yAt(p.brightness));
  });
  ctx.stroke();
}

function pointLabel(point) {
  return "(" + point.x + ", " + point.y + ") · RGB " + point.rgb.join("/") +
    " · 亮度 " + point.brightness.toFixed(1);
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
  get("measurement-state").textContent = result.segments + " 段";
  get("selected-level").textContent = result.length_px.toFixed(2) + " px · " + result.profile.length + " 个原片取样";
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
  Object.assign(state, {card, result, photo, probe: null, draft: null});
  get("library").value = card.id;
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
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
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
action(library);
