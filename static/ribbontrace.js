"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false, baseRgb: null};
const settings = [...document.querySelectorAll("[data-setting]")];
const RGB_LIMIT = 18;
const CUT_WINDOW = 2;

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
  paintCutOverlay(ctx);
  if (probe) {
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(probe.x - 6, probe.y - 6, 12, 12);
    ctx.strokeStyle = "#ffffff";
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
  }
}

function currentSearch() {
  const draft = cutDraft();
  if (draft.target == null || !Number.isFinite(draft.target) || draft.target <= 0) return null;
  if (draft.tolerance == null || !Number.isFinite(draft.tolerance) || draft.tolerance < 0) return null;
  if (!draft.base || draft.base.length !== 3 || !state.result) return null;
  return {draft, candidates: computeCandidates(state.result.profile, state.card.settings.mm_per_px,
                                         draft.target, draft.tolerance, draft.base)};
}

function paintCutOverlay(ctx) {
  const {result} = state;
  if (!result) return;
  const live = currentSearch();
  if (live) {
    live.candidates.forEach((candidate, i) => {
      ctx.beginPath();
      ctx.arc(candidate.x, candidate.y, i === 0 ? 8 : 5, 0, 2 * Math.PI);
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = i === 0 ? "#15803d" : "#86efac";
      ctx.stroke();
    });
  }
  const cut = result.cut;
  if (result.has_cut && cut && cut.valid !== false) {
    for (const p of result.profile) {
      if (p.at_cut) ctx.fillStyle = "#e11d48";
      else if (p.disposition === "keep") ctx.fillStyle = "rgba(22,163,74,.85)";
      else if (p.disposition === "cut") ctx.fillStyle = "rgba(220,38,38,.85)";
      else continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.6, 0, 2 * Math.PI);
      ctx.fill();
    }
    const {x, y} = cut;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffffff";
    ctx.beginPath(); ctx.arc(x, y, 13, 0, 2 * Math.PI); ctx.stroke();
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = "#e11d48";
    ctx.beginPath(); ctx.arc(x, y, 11, 0, 2 * Math.PI); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - 17, y); ctx.lineTo(x - 7, y);
    ctx.moveTo(x + 7, y); ctx.lineTo(x + 17, y);
    ctx.moveTo(x, y - 17); ctx.lineTo(x, y - 7);
    ctx.moveTo(x, y + 7); ctx.lineTo(x, y + 17);
    ctx.stroke();
    ctx.font = "bold 14px system-ui";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 3;
    ctx.strokeText("切位", x + 15, y - 12);
    ctx.fillStyle = "#e11d48";
    ctx.fillText("切位", x + 15, y - 12);
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
  if (result.has_cut && result.cut) {
    const cx = xAt(result.cut.distance_px);
    ctx.strokeStyle = "#e11d48";
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(cx, 18); ctx.lineTo(cx, height - 24); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#be123c";
    ctx.fillText("切位 " + result.cut.distance_mm.toFixed(1) + " mm", Math.min(cx + 5, width - 110), 14);
  }
  ctx.fillStyle = "#646c63";
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

/* ---------- cut planning (mirrors ribbontrace.route.cut_search) ---------- */

function cutDraft() {
  const targetRaw = get("cut-target-mm").value.trim();
  const toleranceRaw = get("cut-tolerance-mm").value.trim();
  return {
    target: targetRaw === "" ? null : Number(targetRaw),
    tolerance: toleranceRaw === "" ? null : Number(toleranceRaw),
    base: state.baseRgb ? state.baseRgb.slice() : null,
  };
}

function validDraftOrThrow() {
  const draft = cutDraft();
  if (draft.target == null || !Number.isFinite(draft.target) || draft.target <= 0)
    throw new Error("请填写大于 0 的想要的毫米数。");
  if (draft.tolerance == null || !Number.isFinite(draft.tolerance) || draft.tolerance < 0)
    throw new Error("请填写不小于 0 的允许差（毫米）。");
  if (!draft.base || draft.base.length !== 3)
    throw new Error("请先用原片点读取一个无花纹底色。");
  return draft;
}

function computeCandidates(profile, mmPerPx, target, tolerance, base) {
  const n = profile.length;
  const close = (rgb) => rgb.every((channel, k) => Math.abs(channel - base[k]) <= RGB_LIMIT);
  // Nearest pattern index strictly before/after each sample, computed once.
  const patternAt = profile.map((s) => !close(s.rgb));
  const leftIndex = new Array(n).fill(null);
  const rightIndex = new Array(n).fill(null);
  let last = null;
  for (let i = 0; i < n; i++) { leftIndex[i] = last; if (patternAt[i]) last = i; }
  let next = null;
  for (let i = n - 1; i >= 0; i--) { rightIndex[i] = next; if (patternAt[i]) next = i; }
  const candidates = [];
  profile.forEach((sample, i) => {
    const distanceMm = sample.distance * mmPerPx;
    const errorMm = Math.abs(distanceMm - target);
    const lengthOk = errorMm <= tolerance + 1e-9;
    const bothSides = i - CUT_WINDOW >= 0 && i + CUT_WINDOW < n;
    const colourOk = bothSides &&
      profile.slice(i - CUT_WINDOW, i + CUT_WINDOW + 1).every((q) => close(q.rgb));
    if (!lengthOk || !bothSides || !colourOk) return;
    const lj = leftIndex[i];
    const rj = rightIndex[i];
    const left = lj == null ? null : (sample.distance - profile[lj].distance) * mmPerPx;
    const right = rj == null ? null : (profile[rj].distance - sample.distance) * mmPerPx;
    const known = [left, right].filter((v) => v != null);
    const clearance = known.length ? Math.min(...known) : null;
    candidates.push({index: i, sample: i + 1, x: sample.x, y: sample.y,
      segment: sample.segment, distance_mm: distanceMm, error_mm: errorMm,
      left_mm: left, right_mm: right, clearance_mm: clearance});
  });
  const widest = Math.max(0, ...candidates.map((c) => c.clearance_mm).filter((v) => v != null));
  candidates.forEach((c) => {
    const lengthScore = tolerance > 0 ? c.error_mm / tolerance : 0;
    const safetyScore = widest > 0 && c.clearance_mm != null ? 1 - c.clearance_mm / widest : 0;
    c.score = 0.5 * lengthScore + 0.5 * safetyScore;
  });
  candidates.sort((a, b) =>
    (Math.round(a.score * 1e12) - Math.round(b.score * 1e12)) ||
    (a.error_mm - b.error_mm) || (a.index - b.index));
  candidates.forEach((c, rank) => { c.rank = rank + 1; });
  return candidates;
}

function diagnoseSearch(draft) {
  const profile = state.result.profile;
  const mmPerPx = state.card.settings.mm_per_px;
  const n = profile.length;
  const close = (rgb) => rgb.every((channel, k) => Math.abs(channel - draft.base[k]) <= RGB_LIMIT);
  const inLength = [];
  profile.forEach((sample, i) => {
    const distanceMm = sample.distance * mmPerPx;
    if (Math.abs(distanceMm - draft.target) <= draft.tolerance + 1e-9) inLength.push(i);
  });
  if (!inLength.length)
    return "允许差内没有实际采样点：请放宽允许差或调整想要的毫米数。";
  const sided = inLength.filter((i) => i - CUT_WINDOW >= 0 && i + CUT_WINDOW < n);
  if (!sided.length)
    return "长度允许差内的采样点都太靠近路径起点或终点，切位前后不足两个取样点，先不给切位。";
  const colourFails = sided.filter((i) =>
    !profile.slice(i - CUT_WINDOW, i + CUT_WINDOW + 1).every((q) => close(q.rgb)));
  if (colourFails.length === sided.length)
    return "允许差内候选的五点窗口都有取样点 RGB 离所取底色超过 18，可能压到花纹，先不给切位。";
  return "没有同时满足长度与底色条件的切位。";
}

function clearText(value) {
  return value == null ? "两侧采样未遇到花纹" : value.toFixed(2) + " mm";
}

function candidateLine(c, recommended) {
  const adoptedIndex = state.result.has_cut ? state.result.cut.index : null;
  const adopted = adoptedIndex === c.index;
  return `<div class="candidate${recommended ? " recommended" : ""}${adopted ? " adopted" : ""}" data-index="${c.index}">
    <div class="candidate-title">
      <b>${recommended ? "推荐 · " : ""}取样点 #${c.sample}</b>
      <span class="candidate-badges">
        <span class="rank">综合第 ${c.rank} 位</span>
        ${adopted ? '<span class="adopted-badge">已采用</span>' : ""}
      </span>
    </div>
    <p>距起点实际 <strong>${c.distance_mm.toFixed(2)} mm</strong> · 长度偏差 ${c.error_mm.toFixed(2)} mm · 第 ${c.segment} 段（实际段号 ${c.segment}）· 原片 (${c.x}, ${c.y})</p>
    <p>最近花纹净空：左 ${clearText(c.left_mm)} / 右 ${clearText(c.right_mm)} · 取较近一侧 ${c.clearance_mm == null ? "未遇到花纹" : c.clearance_mm.toFixed(2) + " mm"}</p>
  </div>`;
}

function renderCutPanel() {
  if (!state.card || !state.result) return;
  const draft = cutDraft();
  const message = get("cut-message");
  const options = get("cut-options");
  const adoptButton = get("adopt-cut");
  const clearButton = get("clear-cut");
  const adoptedBox = get("cut-adopted");
  const cut = state.result.cut;

  clearButton.hidden = !state.result.has_cut;
  if (state.result.has_cut && cut) {
    adoptedBox.hidden = false;
    adoptedBox.innerHTML = `已采用切位：取样点 <b>#${cut.sample}</b> · 第 ${cut.segment} 段（实际段号 ${cut.segment}）· 距起点实际 <b>${cut.distance_mm.toFixed(2)} mm</b> · 原片 (${cut.x}, ${cut.y})。原整条路径仍保留。`;
  } else {
    adoptedBox.hidden = true;
    adoptedBox.replaceChildren();
  }

  const complete = draft.target != null && Number.isFinite(draft.target) && draft.target > 0 &&
    draft.tolerance != null && Number.isFinite(draft.tolerance) && draft.tolerance >= 0 &&
    draft.base;
  if (!complete) {
    adoptButton.hidden = true;
    message.hidden = true;
    options.innerHTML = `<p class="cut-hint">填好想要的毫米数、允许差，并用原片点读选取无花纹底色后，这里会按“长度贴目标 + 离两边花纹更远”列出实际采样切位。</p>`;
    return;
  }
  const candidates = computeCandidates(state.result.profile, state.card.settings.mm_per_px,
                                       draft.target, draft.tolerance, draft.base);
  if (!candidates.length) {
    adoptButton.hidden = true;
    message.hidden = false;
    message.textContent = diagnoseSearch(draft);
    options.innerHTML = "";
    return;
  }
  message.hidden = false;
  message.textContent = `找到 ${candidates.length} 个合格切位（均通过 ±2 取样点底色检查），首位为兼顾长度与净空的推荐切位。`;
  adoptButton.hidden = false;
  adoptButton.dataset.index = candidates[0].index;
  const [best, ...rest] = candidates;
  let html = candidateLine(best, true);
  if (rest.length) {
    html += `<details class="other-cuts"><summary>其他可选切位（${rest.length}）</summary><div class="other-list">`;
    for (const c of rest) {
      html += `<div class="other-row">${candidateLine(c, false)}
        <button type="button" class="adopt-option" data-index="${c.index}" id="adopt-option-${c.index}">采用 #${c.sample}</button></div>`;
    }
    html += `</div></details>`;
  }
  options.innerHTML = html;
}

function renderCutLength() {
  const cut = state.result.cut;
  const box = get("cut-length");
  const empty = get("cut-length-empty");
  if (!state.result.has_cut || !cut) {
    box.hidden = true;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  box.hidden = false;
  const value = get("cut-length-value");
  value.textContent = cut.distance_mm.toFixed(2);
  value.dataset.mm = String(cut.distance_mm);
  get("cut-length-meta").textContent =
    `取样点 #${cut.sample} · 第 ${cut.segment} 段 · 原片 (${cut.x}, ${cut.y})；整条路径 ${state.result.length_mm.toFixed(2)} mm 与全部路径点仍保留。`;
}

function renderBase() {
  const base = state.baseRgb;
  const current = get("base-current");
  const summary = get("cut-base-summary");
  const picker = get("base-color");
  if (!base) {
    current.hidden = true;
    summary.textContent = "先用左侧「原片点读」读一个看得见的无花纹底色，再点「取为底色」。";
    return;
  }
  const hex = "#" + base.map((c) => Number(c).toString(16).padStart(2, "0")).join("");
  if (picker) picker.value = hex;
  current.hidden = false;
  get("base-swatch").style.background = `rgb(${base[0]}, ${base[1]}, ${base[2]})`;
  get("base-rgb-text").textContent = `底色 RGB ${base.join(" / ")} · 原片点读实值`;
  summary.innerHTML = `已取无花纹底色 <b>RGB ${base.join(" / ")}</b>（${hex}）；切位及前后各两个取样点每通道差须 ≤ ${RGB_LIMIT}。`;
}

function renderProbePasses() {
  const box = get("probe-passes");
  const probe = state.probe;
  if (!probe) {
    box.replaceChildren();
    return;
  }
  if (!probe.passes || !probe.passes.length) {
    box.innerHTML = `<p class="no-pass">${probe.message || "该像素不是当前路径的实际采样点；RGB 仍是照片实值。"}</p>`;
    return;
  }
  const rows = probe.passes.map((p) => {
    const verdict = p.verdict_cn
      ? `<span class="verdict verdict-${p.disposition === "cut_here" ? "here" : p.status}" data-verdict="${p.status || ""}">${p.verdict_cn}</span>`
      : `<span class="verdict verdict-pending" data-verdict="pending">采用切位后区分剪下 / 留下</span>`;
    return `<div class="pass-row${p.status ? " is-" + p.status : ""}${p.at_cut ? " is-here" : ""}" data-pass="${p.pass}" data-segment="${p.segment}" data-index="${p.index}" data-status="${p.status || "pending"}">
      <span class="pass-no">第 ${p.pass} 次经过</span>
      <span class="pass-seg">第 <b>${p.segment}</b> 段<span class="seg-alias">（实际段号 ${p.segment}）</span></span>
      <span class="pass-sample">取样点 #${p.sample}</span>
      <span class="pass-mm">${p.distance_mm.toFixed(2)} mm</span>
      ${verdict}
    </div>`;
  }).join("");
  box.innerHTML = `<p class="pass-head">该原片像素被路径经过 ${probe.pass_count} 次（按经过顺序列出，不去重）：</p>` + rows;
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
  renderProbePasses();
  get("path-points").value = result.points.map((p) => p.join(",")).join("\n");
  get("length-value").textContent = result.length_mm.toFixed(2);
  get("length-unit").textContent = "mm";
  get("measurement-state").textContent = result.segments + " 段";
  get("selected-level").textContent = result.length_px.toFixed(2) + " px · " + result.profile.length + " 个原片取样";
  get("cut-target-mm").value = card.settings.cut_target_mm ?? "";
  get("cut-tolerance-mm").value = card.settings.cut_tolerance_mm ?? "";
  state.baseRgb = Array.isArray(card.settings.base_rgb) ? card.settings.base_rgb.slice() : null;
  renderBase();
  renderCutLength();
  renderCutPanel();
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/ribbontrace" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
}

async function open(card, options = {}) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/ribbontrace" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, draft: null});
  get("library").value = card.id;
  show();
  if (options.probeXY) await probe(options.probeXY.x, options.probeXY.y);
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  renderProbePasses();
  paintSource();
}

async function persistSettings(patch, probeXY) {
  const card = await api(route(""), {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: patch}),
  });
  await open(card, {probeXY: probeXY || null});
  await library();
}

function currentProbeXY() {
  return state.probe ? {x: state.probe.x, y: state.probe.y} : null;
}

function adoptCut(index) {
  action(async () => {
    const draft = validDraftOrThrow();
    await persistSettings({
      cut_target_mm: draft.target,
      cut_tolerance_mm: draft.tolerance,
      base_rgb: draft.base,
      cut_index: index,
    }, currentProbeXY());
  }, "cut-message");
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

get("use-base").addEventListener("click", () => {
  if (!state.probe) {
    notice("请先用「原片点读」在照片上点一个看得见的无花纹底色像素。", "cut-message");
    return;
  }
  state.baseRgb = state.probe.rgb.slice();
  renderBase();
  renderCutPanel();
  paintSource();
  notice("已把当前点读的照片实值 RGB 取为无花纹底色。");
});

get("cut-target-mm").addEventListener("input", renderCutPanel);
get("cut-tolerance-mm").addEventListener("input", renderCutPanel);
get("base-color").addEventListener("input", (event) => {
  const hex = event.target.value;
  if (/^#[0-9a-f]{6}$/i.test(hex)) {
    state.baseRgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    renderBase();
    renderCutPanel();
    paintSource();
  }
});

get("cut-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const draft = validDraftOrThrow();
    const probeXY = currentProbeXY();
    await persistSettings({
      cut_target_mm: draft.target,
      cut_tolerance_mm: draft.tolerance,
      base_rgb: draft.base,
    }, probeXY);
    const count = state.result.cut_search?.count ?? 0;
    notice(count ? `找到 ${count} 个合格切位，已在下方列出并给出推荐。`
                 : "允许差内没有前后各两个底色干净采样点的切位，暂不提供切位。", "cut-message");
  }, "cut-message");
});

get("adopt-cut").addEventListener("click", () => {
  const index = Number(get("adopt-cut").dataset.index);
  if (Number.isInteger(index)) adoptCut(index);
});

get("cut-options").addEventListener("click", (event) => {
  const button = event.target.closest(".adopt-option");
  if (!button) return;
  adoptCut(Number(button.dataset.index));
});

get("clear-cut").addEventListener("click", () => {
  action(async () => {
    await persistSettings({cut_index: null}, currentProbeXY());
    notice("已取消采用切位；原整条路径保持不变。", "cut-message");
  }, "cut-message");
});

get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));

window.ribbonTrace = {state, computeCandidates, adoptCut, probe};
action(library);
