"use strict";
/* Wi-Fi Site Survey — หน้าเว็บ (vanilla JS)
   พิกัดทั้งหมดเก็บเป็นพิกเซลของรูปแปลน; สเกล pxPerM แปลงเป็นเมตร */

const $ = (id) => document.getElementById(id);
const cv = $("cv"), ctx = cv.getContext("2d");

let state = newState();
let view = { s: 1, ox: 0, oy: 0 };
let tool = "select";
let sel = null;               // {type:'point'|'ap', id}
let scaleClicks = [];         // ระหว่างตั้งสเกล
let planImg = null;           // HTMLImageElement
let hm = null;                // {canvas, coverage, cells}
let hmDirty = true;
let busy = false;
let drag = null;
let info = { scanner_ok: true };

function newState() {
  return { name: "โปรเจกต์ใหม่", plan: null, pxPerM: null, points: [], aps: [], nextId: 1 };
}

/* ------------------------------------------------------------ สี / โมเดลสัญญาณ */
const STOPS = [[-90, [215, 48, 39]], [-80, [252, 141, 89]], [-70, [254, 224, 139]], [-60, [145, 207, 96]], [-50, [26, 152, 80]]];
function rssiColor(v) {
  if (v <= STOPS[0][0]) return STOPS[0][1];
  if (v >= STOPS[STOPS.length - 1][0]) return STOPS[STOPS.length - 1][1];
  for (let i = 1; i < STOPS.length; i++) {
    if (v <= STOPS[i][0]) {
      const [v0, c0] = STOPS[i - 1], [v1, c1] = STOPS[i], t = (v - v0) / (v1 - v0);
      return c0.map((c, k) => Math.round(c + (c1[k] - c) * t));
    }
  }
}
const rgb = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const PL0 = { "2.4": 40, "5": 47, "6": 49 };

function mPerPx() { return state.pxPerM ? 1 / state.pxPerM : 0.05; }
function predictAt(ap, x, y) {
  const d = Math.max(1, Math.hypot(x - ap.x, y - ap.y) * mPerPx());
  const n = parseFloat($("plExp").value) || 3;
  return ap.txPower - (PL0[ap.band] ?? 47) - 10 * n * Math.log10(d);
}

/* ------------------------------------------------------------ ค่าที่ใช้วาด heatmap */
function filterVal() { return $("hmFilter").value; }
function pointValue(p) {
  const f = filterVal();
  let aps = p.aps || [];
  if (f.startsWith("ssid:")) aps = aps.filter((a) => a.ssid === f.slice(5));
  else if (f.startsWith("bssid:")) aps = aps.filter((a) => a.bssid === f.slice(6));
  return aps.length ? Math.max(...aps.map((a) => a.rssi)) : null;
}
function predictedValue(x, y) {
  const f = filterVal();
  let list = state.aps;
  if (f.startsWith("ap:")) list = list.filter((a) => String(a.id) === f.slice(3));
  return list.length ? Math.max(...list.map((a) => predictAt(a, x, y))) : null;
}
function itemValue(p) {  // ค่าสำหรับสีของหมุดจุดวัด
  return $("hmSource").value === "predicted" ? null : pointValue(p);
}

function rebuildFilter() {
  const sel_ = $("hmFilter"), prev = sel_.value, src = $("hmSource").value;
  const opts = [["all", "ทุก AP (ตัวที่แรงที่สุด)"]];
  if (src === "measured") {
    const ssids = new Set(), bssids = new Map();
    state.points.forEach((p) => (p.aps || []).forEach((a) => {
      if (a.ssid) ssids.add(a.ssid);
      bssids.set(a.bssid, a.ssid || "(hidden)");
    }));
    [...ssids].sort().forEach((s) => opts.push(["ssid:" + s, "SSID: " + s]));
    [...bssids].sort((a, b) => a[1].localeCompare(b[1])).forEach(([b, s]) => opts.push(["bssid:" + b, `${s} · ${b}`]));
  } else {
    state.aps.forEach((a) => opts.push(["ap:" + a.id, "AP: " + a.name]));
  }
  sel_.innerHTML = "";
  opts.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel_.appendChild(o); });
  sel_.value = opts.some((o) => o[0] === prev) ? prev : "all";
}

/* ------------------------------------------------------------ คำนวณ heatmap */
function computeHeatmap() {
  if (!state.plan) return null;
  const { w, h } = state.plan;
  const step = Math.max(4, Math.round(Math.max(w, h) / 180));
  const gw = Math.ceil(w / step), gh = Math.ceil(h / step);
  const src = $("hmSource").value, target = parseFloat($("target").value);
  const c = document.createElement("canvas"); c.width = gw; c.height = gh;
  const g = c.getContext("2d"), img = g.createImageData(gw, gh);
  const pts = [];
  if (src === "measured") state.points.forEach((p) => { const v = pointValue(p); pts.push({ x: p.x, y: p.y, v: v ?? -100 }); });
  if ((src === "measured" && !pts.length) || (src === "predicted" && !state.aps.length)) return { canvas: c, coverage: null, cells: 0 };
  const maxD = state.pxPerM ? 12 * state.pxPerM : Math.max(w, h) * 0.2;   // ไกลจากจุดวัดเกิน ~12 ม. ไม่ระบายสี
  let ok = 0, tot = 0;
  for (let j = 0; j < gh; j++) for (let i = 0; i < gw; i++) {
    const x = (i + 0.5) * step, y = (j + 0.5) * step;
    let v, alpha = 1;
    if (src === "predicted") v = predictedValue(x, y);
    else {
      let wsum = 0, vsum = 0, dmin = Infinity;
      for (const p of pts) {
        const d2 = (p.x - x) ** 2 + (p.y - y) ** 2;
        const d = Math.sqrt(d2);
        if (d < dmin) dmin = d;
        const wt = 1 / (d2 + (step * 0.5) ** 2) ** 1.0;   // IDW กำลัง 2
        wsum += wt; vsum += wt * p.v;
      }
      v = vsum / wsum;
      alpha = dmin > maxD ? 0 : dmin > maxD * 0.6 ? 1 - (dmin - maxD * 0.6) / (maxD * 0.4) : 1;
    }
    if (v == null || alpha <= 0) continue;
    const col = rssiColor(v), k = (j * gw + i) * 4;
    img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2]; img.data[k + 3] = Math.round(255 * alpha);
    if (alpha > 0.5) { tot++; if (v >= target) ok++; }
  }
  g.putImageData(img, 0, 0);
  return { canvas: c, coverage: tot ? (100 * ok) / tot : null, cells: tot };
}

/* ------------------------------------------------------------ วาด */
function renderScene(g, u, showHm = true) {
  // u = ขนาด 1 "พิกเซลหน้าจอ" ในหน่วยพิกเซลแปลน
  const { w, h } = state.plan;
  g.drawImage(planImg, 0, 0, w, h);
  if (showHm && $("hmShow").checked && hm) {
    g.save(); g.globalAlpha = $("opacity").value / 100; g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    g.drawImage(hm.canvas, 0, 0, w, h); g.restore();
  }
  // เส้นสเกล
  if (scaleClicks.length) {
    g.strokeStyle = "#ff3df2"; g.lineWidth = 2 * u; g.fillStyle = "#ff3df2";
    scaleClicks.forEach((p) => { g.beginPath(); g.arc(p.x, p.y, 4 * u, 0, 7); g.fill(); });
    if (scaleClicks.length === 2) { g.beginPath(); g.moveTo(scaleClicks[0].x, scaleClicks[0].y); g.lineTo(scaleClicks[1].x, scaleClicks[1].y); g.stroke(); }
  }
  g.textAlign = "center"; g.textBaseline = "middle";
  // จุดวัด
  state.points.forEach((p, i) => {
    const v = itemValue(p), r = 9 * u;
    g.beginPath(); g.arc(p.x, p.y, r, 0, 7);
    g.fillStyle = v == null ? "#888" : rgb(rssiColor(v)); g.fill();
    g.lineWidth = (sel && sel.type === "point" && sel.id === p.id ? 3 : 1.5) * u;
    g.strokeStyle = sel && sel.type === "point" && sel.id === p.id ? "#1f6feb" : "#222"; g.stroke();
    g.fillStyle = "#000"; g.font = `bold ${9 * u}px sans-serif`; g.fillText(String(i + 1), p.x, p.y + 0.5 * u);
    if (v != null) { g.font = `${10 * u}px sans-serif`; g.lineWidth = 3 * u; g.strokeStyle = "rgba(255,255,255,.9)"; g.strokeText(v.toFixed(0), p.x, p.y + 17 * u); g.fillStyle = "#111"; g.fillText(v.toFixed(0), p.x, p.y + 17 * u); }
  });
  // AP ที่วางแผนไว้
  state.aps.forEach((a) => {
    const r = 11 * u, on = sel && sel.type === "ap" && sel.id === a.id;
    g.fillStyle = "#1f6feb"; g.strokeStyle = on ? "#ffd400" : "#fff"; g.lineWidth = (on ? 3 : 2) * u;
    g.beginPath(); g.moveTo(a.x, a.y - r); g.lineTo(a.x + r, a.y); g.lineTo(a.x, a.y + r); g.lineTo(a.x - r, a.y); g.closePath(); g.fill(); g.stroke();
    g.fillStyle = "#fff"; g.font = `bold ${9 * u}px sans-serif`; g.fillText("AP", a.x, a.y + 0.5 * u);
    g.font = `${10 * u}px sans-serif`; g.lineWidth = 3 * u; g.strokeStyle = "rgba(255,255,255,.95)"; g.strokeText(a.name, a.x, a.y + r + 9 * u); g.fillStyle = "#0b3d91"; g.fillText(a.name, a.x, a.y + r + 9 * u);
  });
}

function draw() {
  const dpr = window.devicePixelRatio || 1, r = cv.getBoundingClientRect();
  if (cv.width !== Math.round(r.width * dpr) || cv.height !== Math.round(r.height * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, r.width, r.height);
  $("empty").hidden = !!state.plan;
  if (!state.plan || !planImg) return;
  if (hmDirty) { hm = computeHeatmap(); hmDirty = false; updateStats(); }
  ctx.save(); ctx.translate(view.ox, view.oy); ctx.scale(view.s, view.s);
  renderScene(ctx, 1 / view.s);
  ctx.restore();
}
let rafPending = false;
function redraw(recompute = false) {
  if (recompute) hmDirty = true;
  if (rafPending) return; rafPending = true;
  requestAnimationFrame(() => { rafPending = false; draw(); });
}

function updateStats() {
  const best = state.points.map((p) => { const v = pointValue(p); return v; }).filter((v) => v != null);
  const target = parseFloat($("target").value), parts = [];
  parts.push(`จุดวัด: <b>${state.points.length}</b> · AP ที่วางแผน: <b>${state.aps.length}</b>`);
  if (best.length) parts.push(`RSSI (ต่ำสุด/เฉลี่ย/สูงสุด): <b>${Math.min(...best).toFixed(0)} / ${(best.reduce((a, b) => a + b, 0) / best.length).toFixed(0)} / ${Math.max(...best).toFixed(0)}</b> dBm`,
    `จุดที่ผ่านเกณฑ์: <b>${best.filter((v) => v >= target).length}/${best.length}</b>`);
  if (hm && hm.coverage != null) parts.push(`พื้นที่ครอบคลุม ≥ ${target} dBm: <b>${hm.coverage.toFixed(0)}%</b>`);
  $("stats").innerHTML = parts.join("<br>");
}

/* ------------------------------------------------------------ มุมมอง */
function fitView() {
  if (!state.plan) return;
  const r = cv.getBoundingClientRect(), s = Math.min(r.width / state.plan.w, r.height / state.plan.h) * 0.95;
  view = { s, ox: (r.width - state.plan.w * s) / 2, oy: (r.height - state.plan.h * s) / 2 };
}
const toImg = (e) => { const r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left - view.ox) / view.s, y: (e.clientY - r.top - view.oy) / view.s }; };

/* ------------------------------------------------------------ ข้อความ/สถานะ */
let toastT;
function toast(msg, err = false) {
  const t = $("toast"); t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), err ? 7000 : 2800);
}
function setBusy(on, text = "กำลังสแกน…") { busy = on; $("busy").hidden = !on; $("busyText").textContent = text; }
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).error || m; } catch (_) {} throw new Error(m); }
  return r;
}

/* ------------------------------------------------------------ สแกน */
function simulateScan(x, y) {
  if (!state.aps.length) throw new Error("โหมดจำลอง: วาง AP อย่างน้อย 1 ตัวก่อน");
  const noise = () => (Math.random() + Math.random() + Math.random() - 1.5) * 3;
  return state.aps.map((a) => ({
    bssid: "02:00:00:00:" + (a.id >> 8 & 255).toString(16).padStart(2, "0") + ":" + (a.id & 255).toString(16).padStart(2, "0"),
    ssid: "Sim-" + a.name, rssi: Math.round((predictAt(a, x, y) + noise()) * 10) / 10,
    channel: a.channel || null, freq: null, band: a.band, security: "",
  })).filter((a) => a.rssi > -95).sort((a, b) => b.rssi - a.rssi);
}
async function doScan(x, y) {
  if ($("scanMode").value === "sim") return simulateScan(x, y);
  const n = Math.max(1, Math.min(10, parseInt($("samples").value) || 1));
  const r = await api("/api/scan?samples=" + n, { method: "POST" });
  return (await r.json()).aps;
}
async function addPoint(x, y) {
  setBusy(true);
  try {
    const aps = await doScan(x, y);
    const p = { id: state.nextId++, x, y, label: "จุด " + (state.points.length + 1), aps, time: new Date().toISOString() };
    state.points.push(p); sel = { type: "point", id: p.id };
    toast(`วัดสำเร็จ: พบ ${aps.length} AP` + (aps[0] ? ` · แรงสุด ${aps[0].rssi.toFixed(0)} dBm` : ""));
  } catch (e) { toast("สแกนไม่สำเร็จ: " + e.message, true); }
  finally { setBusy(false); rebuildFilter(); renderSel(); redraw(true); }
}
async function rescan(p) {
  setBusy(true);
  try { p.aps = await doScan(p.x, p.y); p.time = new Date().toISOString(); toast("สแกนใหม่แล้ว"); }
  catch (e) { toast("สแกนไม่สำเร็จ: " + e.message, true); }
  finally { setBusy(false); rebuildFilter(); renderSel(); redraw(true); }
}

/* ------------------------------------------------------------ รายการที่เลือก */
const getSel = () => !sel ? null : sel.type === "point" ? state.points.find((p) => p.id === sel.id) : state.aps.find((a) => a.id === sel.id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function renderSel() {
  const o = getSel(), panel = $("selPanel"), body = $("selBody");
  if (!o) { panel.hidden = true; return; }
  panel.hidden = false;
  if (sel.type === "point") {
    const rows = [...(o.aps || [])].sort((a, b) => b.rssi - a.rssi).slice(0, 25).map((a) =>
      `<tr><td>${esc(a.ssid || "(hidden)")}</td><td>${a.rssi.toFixed(0)}</td><td>${a.channel ?? "-"}</td><td>${a.band}</td></tr>`).join("");
    body.innerHTML = `<label>ชื่อจุด<input id="selLabel" type="text" value="${esc(o.label)}"></label>
      <div class="hint">ตำแหน่ง: ${state.pxPerM ? (o.x / state.pxPerM).toFixed(1) + ", " + (o.y / state.pxPerM).toFixed(1) + " ม." : "ยังไม่ตั้งสเกล"}</div>
      <table class="aps"><tr><th>SSID</th><th>dBm</th><th>Ch</th><th>GHz</th></tr>${rows || '<tr><td colspan=4>ไม่พบ AP</td></tr>'}</table>
      <div class="row"><button id="selRescan">สแกนจุดนี้ใหม่</button><button id="selDel" class="danger">ลบจุด</button></div>`;
    $("selLabel").oninput = (e) => { o.label = e.target.value; };
    $("selRescan").onclick = () => rescan(o);
  } else {
    body.innerHTML = `<label>ชื่อ AP<input id="apName" type="text" value="${esc(o.name)}"></label>
      <div class="row"><label>ย่าน<select id="apBand"><option>2.4</option><option>5</option><option>6</option></select></label>
      <label>ช่อง<input id="apCh" type="number" min="1" max="233" value="${o.channel ?? ""}"></label></div>
      <label>กำลังส่ง EIRP (dBm)<input id="apPwr" type="number" min="0" max="36" value="${o.txPower}"></label>
      <button id="selDel" class="danger block">ลบ AP</button>`;
    $("apBand").value = o.band;
    const upd = () => { o.name = $("apName").value; o.band = $("apBand").value; o.channel = parseInt($("apCh").value) || null; o.txPower = parseFloat($("apPwr").value) || 18; rebuildFilter(); redraw(true); };
    ["apName", "apBand", "apCh", "apPwr"].forEach((id) => ($(id).onchange = upd));
  }
  $("selDel").onclick = deleteSel;
}
function deleteSel() {
  if (!sel) return;
  if (sel.type === "point") state.points = state.points.filter((p) => p.id !== sel.id);
  else state.aps = state.aps.filter((a) => a.id !== sel.id);
  sel = null; rebuildFilter(); renderSel(); redraw(true);
}

/* ------------------------------------------------------------ เครื่องมือ */
function setTool(t) {
  tool = t; scaleClicks = []; $("btnScale").textContent = "ตั้งสเกล (คลิก 2 จุดบนแปลน)";
  document.querySelectorAll("#tools button").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
  cv.style.cursor = t === "select" ? "default" : "crosshair"; redraw();
}
document.querySelectorAll("#tools button").forEach((b) => (b.onclick = () => setTool(b.dataset.tool)));
$("btnScale").onclick = () => { tool = "scale"; scaleClicks = []; document.querySelectorAll("#tools button").forEach((b) => b.classList.remove("active")); cv.style.cursor = "crosshair"; $("btnScale").textContent = "คลิกจุดที่ 1 …"; };
function updateScaleInfo() {
  $("scaleInfo").textContent = state.pxPerM ? `สเกล: ${state.pxPerM.toFixed(1)} px = 1 เมตร (รูปกว้าง ${(state.plan.w / state.pxPerM).toFixed(1)} ม.)` : "ยังไม่ได้ตั้งสเกล (ผลจำลองและพื้นที่ครอบคลุมจะใช้ค่าประมาณ)";
  $("btnScale").disabled = !state.plan;
}

function hit(p) {
  const rr = 12 / view.s;
  for (const a of state.aps) if (Math.hypot(a.x - p.x, a.y - p.y) < rr) return { type: "ap", id: a.id };
  for (const q of state.points) if (Math.hypot(q.x - p.x, q.y - p.y) < rr) return { type: "point", id: q.id };
  return null;
}

cv.addEventListener("pointerdown", (e) => {
  if (!state.plan || busy) return;
  cv.setPointerCapture(e.pointerId);
  const p = toImg(e);
  if (e.button === 1 || e.button === 2) { drag = { kind: "pan", sx: e.clientX, sy: e.clientY, ox: view.ox, oy: view.oy }; return; }
  if (tool === "select") {
    const h = hit(p);
    sel = h; renderSel();
    drag = h ? { kind: "move", obj: getSel() } : { kind: "pan", sx: e.clientX, sy: e.clientY, ox: view.ox, oy: view.oy, moved: false };
    redraw();
  } else drag = { kind: "click", sx: e.clientX, sy: e.clientY, p };
});
cv.addEventListener("pointermove", (e) => {
  if (!drag) return;
  if (drag.kind === "pan") { view.ox = drag.ox + e.clientX - drag.sx; view.oy = drag.oy + e.clientY - drag.sy; redraw(); }
  else if (drag.kind === "move") { const p = toImg(e); drag.obj.x = p.x; drag.obj.y = p.y; drag.dirty = true; redraw(true); }
});
cv.addEventListener("pointerup", async (e) => {
  const d = drag; drag = null; if (!d) return;
  if (d.kind === "move" && d.dirty) { redraw(true); return; }
  if (d.kind !== "click" || Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 5) return;
  const p = d.p;
  if (p.x < 0 || p.y < 0 || p.x > state.plan.w || p.y > state.plan.h) return;
  if (tool === "point") await addPoint(p.x, p.y);
  else if (tool === "ap") {
    const a = { id: state.nextId++, x: p.x, y: p.y, name: "AP-" + (state.aps.length + 1), band: "5", channel: 36, txPower: 18 };
    state.aps.push(a); sel = { type: "ap", id: a.id }; rebuildFilter(); renderSel(); redraw(true);
  } else if (tool === "scale") {
    scaleClicks.push(p); $("btnScale").textContent = scaleClicks.length === 1 ? "คลิกจุดที่ 2 …" : "…"; redraw();
    if (scaleClicks.length === 2) {
      const px = Math.hypot(scaleClicks[0].x - scaleClicks[1].x, scaleClicks[0].y - scaleClicks[1].y);
      setTimeout(() => {
        const m = parseFloat(prompt("ระยะระหว่างสองจุดนี้ยาวกี่เมตร?", "10"));
        if (m > 0 && px > 0) { state.pxPerM = px / m; toast("ตั้งสเกลเรียบร้อย"); }
        updateScaleInfo(); setTool("select"); redraw(true);
      }, 30);
    }
  }
});
cv.addEventListener("contextmenu", (e) => e.preventDefault());
cv.addEventListener("wheel", (e) => {
  if (!state.plan) return; e.preventDefault();
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, k = Math.exp(-e.deltaY * 0.0015);
  const ns = Math.max(0.05, Math.min(20, view.s * k));
  view.ox = mx - (mx - view.ox) * (ns / view.s); view.oy = my - (my - view.oy) * (ns / view.s); view.s = ns; redraw();
}, { passive: false });
window.addEventListener("resize", () => redraw());
window.addEventListener("keydown", (e) => {
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === "Delete" || e.key === "Backspace") deleteSel();
  else if (e.key === "Escape") setTool("select");
  else if (e.key === "v") setTool("select"); else if (e.key === "p") setTool("point"); else if (e.key === "a") setTool("ap");
});

/* ------------------------------------------------------------ แปลน */
$("filePlan").onchange = (e) => {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    const im = new Image();
    im.onload = () => {
      const k = Math.min(1, 2400 / Math.max(im.width, im.height)), w = Math.round(im.width * k), h = Math.round(im.height * k);
      const c = document.createElement("canvas"); c.width = w; c.height = h;
      const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, w, h); g.drawImage(im, 0, 0, w, h);
      state.plan = { src: c.toDataURL("image/jpeg", 0.9), w, h }; state.pxPerM = null; state.points = []; state.aps = []; sel = null;
      loadPlanImage(() => { updateScaleInfo(); rebuildFilter(); renderSel(); fitView(); redraw(true); toast("โหลดแปลนแล้ว — ตั้งสเกลก่อนเพื่อให้ได้ระยะที่ถูกต้อง"); });
    };
    im.onerror = () => toast("เปิดรูปไม่ได้ (ถ้าเป็น PDF ให้แปลงเป็น PNG/JPG ก่อน)", true);
    im.src = rd.result;
  };
  rd.readAsDataURL(f); e.target.value = "";
};
function loadPlanImage(cb) {
  if (!state.plan) { planImg = null; cb && cb(); return; }
  planImg = new Image(); planImg.onload = () => cb && cb(); planImg.src = state.plan.src;
}

/* ------------------------------------------------------------ ตัวควบคุม heatmap */
["hmShow", "opacity"].forEach((id) => ($(id).oninput = () => redraw()));
["hmFilter", "target", "plExp"].forEach((id) => ($(id).oninput = () => redraw(true)));
$("hmSource").onchange = () => { rebuildFilter(); redraw(true); };
$("scanMode").onchange = () => {
  $("scanNote").textContent = $("scanMode").value === "sim" ? "โหมดจำลองสร้างค่า RSSI จากโมเดลระยะทาง (ใช้ฝึก/ทดลองใช้งาน)" : "";
};

/* ------------------------------------------------------------ บันทึก / เปิด */
function serialize() { return { app: "wifi-survey", version: 1, ...state, name: $("projName").value.trim() || "โปรเจกต์ใหม่", settings: { target: $("target").value, plExp: $("plExp").value } }; }
function loadProject(d) {
  state = { name: d.name || "โปรเจกต์", plan: d.plan || null, pxPerM: d.pxPerM || null, points: d.points || [], aps: d.aps || [], nextId: d.nextId || 1 };
  const ids = [...state.points, ...state.aps].map((o) => o.id); state.nextId = Math.max(state.nextId, ...ids, 0) + 1;
  if (d.settings) { $("target").value = d.settings.target ?? -67; $("plExp").value = d.settings.plExp ?? 3; }
  $("projName").value = state.name; sel = null; scaleClicks = [];
  loadPlanImage(() => { updateScaleInfo(); rebuildFilter(); renderSel(); fitView(); redraw(true); });
}
async function refreshList() {
  try {
    const { projects } = await (await api("/api/projects")).json();
    $("projList").innerHTML = '<option value="">— เปิดโปรเจกต์ที่บันทึกไว้ —</option>' + projects.map((n) => `<option>${esc(n)}</option>`).join("");
  } catch (_) {}
}
$("btnSave").onclick = async () => {
  const d = serialize();
  try { await api("/api/projects/" + encodeURIComponent(d.name), { method: "POST", body: JSON.stringify(d) }); toast("บันทึกแล้ว: " + d.name); refreshList(); }
  catch (e) { toast("บันทึกไม่สำเร็จ: " + e.message, true); }
};
$("btnOpen").onclick = async () => {
  const n = $("projList").value; if (!n) return;
  try { loadProject(await (await api("/api/projects/" + encodeURIComponent(n))).json()); toast("เปิดแล้ว: " + n); }
  catch (e) { toast("เปิดไม่สำเร็จ: " + e.message, true); }
};
$("btnNew").onclick = () => { if (state.points.length && !confirm("เริ่มโปรเจกต์ใหม่? ข้อมูลที่ยังไม่บันทึกจะหายไป")) return; loadProject(newState()); };
function download(name, blobOrText, type) {
  const b = blobOrText instanceof Blob ? blobOrText : new Blob([blobOrText], { type });
  const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
$("btnDlJson").onclick = () => download((serialize().name) + ".json", JSON.stringify(serialize()), "application/json");
$("fileJson").onchange = async (e) => { const f = e.target.files[0]; if (!f) return; try { loadProject(JSON.parse(await f.text())); } catch (_) { toast("ไฟล์ JSON ไม่ถูกต้อง", true); } e.target.value = ""; };

/* ------------------------------------------------------------ ส่งออก */
function compositeImage() {
  if (!state.plan) throw new Error("ยังไม่มีแปลน");
  if (hmDirty) { hm = computeHeatmap(); hmDirty = false; }
  const { w, h } = state.plan, bar = Math.round(Math.max(40, h * 0.06)), c = document.createElement("canvas");
  c.width = w; c.height = h + bar; const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
  renderScene(g, Math.max(1, Math.max(w, h) / 1100));
  // แถบสีอธิบาย
  const bw = Math.min(w * 0.6, 700), bx = (w - bw) / 2, by = h + bar * 0.3, bh = bar * 0.3;
  const gr = g.createLinearGradient(bx, 0, bx + bw, 0);
  STOPS.forEach(([v, col]) => gr.addColorStop((v + 90) / 40, rgb(col)));
  g.fillStyle = gr; g.fillRect(bx, by, bw, bh);
  g.fillStyle = "#222"; g.font = `${bar * 0.22}px sans-serif`; g.textBaseline = "top"; g.textAlign = "left"; g.fillText("-90 dBm", bx, by + bh + 3);
  g.textAlign = "right"; g.fillText("-50 dBm", bx + bw, by + bh + 3);
  return c;
}
$("btnPng").onclick = () => { try { compositeImage().toBlob((b) => download("heatmap.jpg", b), "image/jpeg", 0.92); } catch (e) { toast(e.message, true); } };
$("btnPdf").onclick = async () => {
  if (!state.plan) return toast("ยังไม่มีแปลน", true);
  setBusy(true, "กำลังสร้างรายงาน…");
  try {
    const d = serialize(), img = compositeImage().toDataURL("image/jpeg", 0.9);
    d.aps = state.aps.map((a) => ({ ...a, pos_m: state.pxPerM ? `${(a.x / state.pxPerM).toFixed(1)}, ${(a.y / state.pxPerM).toFixed(1)}` : "-" }));
    delete d.plan;
    const r = await api("/api/report", { method: "POST", body: JSON.stringify({ project: d, image: img, lang: $("lang").value, target: parseFloat($("target").value), coverage: hm ? hm.coverage : null, surveyor: $("surveyor").value }) });
    download((d.name || "report") + ".pdf", await r.blob()); toast("สร้างรายงานแล้ว");
  } catch (e) { toast("สร้างรายงานไม่สำเร็จ: " + e.message, true); }
  finally { setBusy(false); }
};
$("btnCsv").onclick = () => {
  const q = (s) => '"' + String(s ?? "").replace(/"/g, '""') + '"', m = (v) => (state.pxPerM ? (v / state.pxPerM).toFixed(2) : "");
  const rows = [["point", "label", "x_m", "y_m", "ssid", "bssid", "rssi_dbm", "channel", "band_ghz", "time"]];
  state.points.forEach((p, i) => (p.aps || []).forEach((a) => rows.push([i + 1, p.label, m(p.x), m(p.y), a.ssid, a.bssid, a.rssi, a.channel ?? "", a.band, p.time || ""])));
  download("survey.csv", "﻿" + rows.map((r) => r.map(q).join(",")).join("\n"), "text/csv");
};

/* ------------------------------------------------------------ เริ่มต้น */
(async function init() {
  try {
    info = await (await api("/api/info")).json();
    if (!info.scanner_ok) { $("scanMode").value = "sim"; $("scanNote").textContent = `เครื่องนี้สแกน Wi-Fi ไม่ได้ (${info.scanner}) — ใช้โหมดจำลองแทน`; }
    if (!info.thai_font) $("lang").querySelector('[value="th"]').textContent += " — ไม่พบฟอนต์ ใช้อังกฤษแทน";
  } catch (_) {}
  rebuildFilter(); updateScaleInfo(); refreshList(); redraw();
})();
window.__survey = { get state() { return state; } };   // สำหรับทดสอบ
