"use strict";
// Document scanner: take or pick photos of pages, find the paper in each and straighten it, tidy it up, and save them as one PDF in the current folder.
// Everything happens in the browser; the PDF goes up through the normal upload queue (enqueue in app.js).
// buildPdf has no browser dependencies so the server-side test can run it in Node.

const SCAN_MAX_SIDE = 2000;          // longest side of a saved page, in pixels
const SCAN_DPI = 150;                // a page is this many pixels per inch, so 2000 px is about A4
const SCAN_QUALITY = 0.82;

// pages: [{ jpeg: Uint8Array, width, height }] (JPEG data and its pixel size) -> Uint8Array holding a PDF
function buildPdf(pages) {
  const enc = new TextEncoder();
  const chunks = [];
  const offsets = [];
  let length = 0;
  const put = (part) => { const b = typeof part === "string" ? enc.encode(part) : part; chunks.push(b); length += b.length; };
  const begin = (n) => { offsets[n] = length; put(n + " 0 obj\n"); };
  const n = pages.length;
  // 1 = catalog, 2 = page tree, then three objects per page: page, content stream, image.
  put("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n");
  begin(1); put("<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
  begin(2); put("<< /Type /Pages /Count " + n + " /Kids [" + pages.map((_, i) => (3 + i * 3) + " 0 R").join(" ") + "] >>\nendobj\n");
  pages.forEach((p, i) => {
    const page = 3 + i * 3, content = page + 1, image = page + 2;
    const w = +(p.width * 72 / SCAN_DPI).toFixed(2), h = +(p.height * 72 / SCAN_DPI).toFixed(2);
    begin(page);
    put("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + w + " " + h + "] /Resources << /XObject << /Im0 " + image + " 0 R >> >> /Contents " + content + " 0 R >>\nendobj\n");
    const draw = "q " + w + " 0 0 " + h + " 0 0 cm /Im0 Do Q";
    begin(content); put("<< /Length " + draw.length + " >>\nstream\n" + draw + "\nendstream\nendobj\n");
    begin(image);
    put("<< /Type /XObject /Subtype /Image /Width " + p.width + " /Height " + p.height + " /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length " + p.jpeg.length + " >>\nstream\n");
    put(p.jpeg); put("\nendstream\nendobj\n");
  });
  const total = 3 + n * 3;
  const xref = length;
  let table = "xref\n0 " + total + "\n0000000000 65535 f \n";
  for (let i = 1; i < total; i++) table += String(offsets[i]).padStart(10, "0") + " 00000 n \n";
  put(table + "trailer\n<< /Size " + total + " /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF\n");
  const out = new Uint8Array(length);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}


// ---- finding the paper: pure functions on pixel arrays, so the test can run them in Node ----------------

// gray: Uint8Array w*h. Returns the paper's corners [TL, TR, BR, BL] as {x,y} in 0..1, or null if no page stands out.
function detectQuad(gray, w, h) {
  const blur = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let sum = 0, n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < w && yy < h) { sum += gray[yy * w + xx]; n++; }
    }
    blur[y * w + x] = sum / n;
  }
  const hist = new Uint32Array(256);
  for (let i = 0; i < blur.length; i++) hist[blur[i]]++;
  let total = w * h, sumAll = 0;
  for (let v = 0; v < 256; v++) sumAll += v * hist[v];
  let wB = 0, sumB = 0, best = -1, threshold = 128;       // Otsu: the split that separates paper from table best
  for (let v = 0; v < 256; v++) {
    wB += hist[v]; if (!wB) continue;
    const wF = total - wB; if (!wF) break;
    sumB += v * hist[v];
    const mB = sumB / wB, mF = (sumAll - sumB) / wF, between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) { best = between; threshold = v; }
  }
  for (const bright of [true, false]) {       // normally the paper is lighter than what it lies on
    const quad = largestBlobCorners(blur, w, h, threshold, bright);
    if (quad) return quad;
  }
  return null;
}

function largestBlobCorners(blur, w, h, threshold, bright) {
  const seen = new Uint8Array(w * h), stack = new Int32Array(w * h);
  let bestSize = 0, bestC = null;
  for (let start = 0; start < w * h; start++) {
    if (seen[start] || (blur[start] > threshold) !== bright) continue;
    let top = 0, size = 0;
    const c = [Infinity, -Infinity, Infinity, -Infinity, 0, 0, 0, 0];       // min x+y, max x+y, min x-y, max x-y, then the points
    const pt = [[0, 0], [0, 0], [0, 0], [0, 0]];
    stack[top++] = start; seen[start] = 1;
    while (top) {
      const i = stack[--top], x = i % w, y = (i - x) / w;
      size++;
      const a = x + y, b = x - y;
      if (a < c[0]) { c[0] = a; pt[0] = [x, y]; }        // top-left
      if (a > c[1]) { c[1] = a; pt[2] = [x, y]; }        // bottom-right
      if (b > c[3]) { c[3] = b; pt[1] = [x, y]; }        // top-right
      if (b < c[2]) { c[2] = b; pt[3] = [x, y]; }        // bottom-left
      if (x > 0 && !seen[i - 1] && (blur[i - 1] > threshold) === bright) { seen[i - 1] = 1; stack[top++] = i - 1; }
      if (x < w - 1 && !seen[i + 1] && (blur[i + 1] > threshold) === bright) { seen[i + 1] = 1; stack[top++] = i + 1; }
      if (y > 0 && !seen[i - w] && (blur[i - w] > threshold) === bright) { seen[i - w] = 1; stack[top++] = i - w; }
      if (y < h - 1 && !seen[i + w] && (blur[i + w] > threshold) === bright) { seen[i + w] = 1; stack[top++] = i + w; }
    }
    if (size > bestSize) { bestSize = size; bestC = pt; }
  }
  if (!bestC || bestSize < 0.1 * w * h || bestSize > 0.97 * w * h) return null;
  let q = bestC.map(([x, y]) => ({ x: (x + 0.5) / w, y: (y + 0.5) / h }));
  const mx = q.reduce((a, p) => a + p.x, 0) / 4, my = q.reduce((a, p) => a + p.y, 0) / 4;
  q = q.map((p) => ({ x: p.x + (mx - p.x) * 0.02, y: p.y + (my - p.y) * 0.02 }));      // a hair inside, so no table edge ends up in the page
  return quadArea(q) < 0.12 ? null : q;
}

function quadArea(q) {
  let a = 0;
  for (let i = 0; i < 4; i++) { const p = q[i], n = q[(i + 1) % 4]; a += p.x * n.y - n.x * p.y; }
  return Math.abs(a) / 2;
}

const FULL_QUAD = () => [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }];
function isFullQuad(q) { return q.every((p, i) => Math.abs(p.x - (i === 1 || i === 2 ? 1 : 0)) < 0.002 && Math.abs(p.y - (i >= 2 ? 1 : 0)) < 0.002); }
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

// A convex quad with corners in order and no sliver: what the corner handles must keep to.
function quadOk(q) {
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4], c = q[(i + 2) % 4];
    if ((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x) <= 1e-4) return false;
  }
  return quadArea(q) > 0.02;
}

// Straighten the quad (corners in source pixels) into an ow x oh picture: for every output pixel, look up
// the matching source spot through a perspective transform and blend the four pixels around it.
function warp(src, sw, sh, quad, ow, oh) {
  // Solve for the 8 numbers of the transform output -> source.
  const from = [[0, 0], [ow, 0], [ow, oh], [0, oh]];
  const m = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = from[i], X = quad[i].x, Y = quad[i].y;
    m.push([x, y, 1, 0, 0, 0, -x * X, -y * X, X]);
    m.push([0, 0, 0, x, y, 1, -x * Y, -y * Y, Y]);
  }
  for (let c = 0; c < 8; c++) {
    let piv = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(m[r][c]) > Math.abs(m[piv][c])) piv = r;
    [m[c], m[piv]] = [m[piv], m[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = m[r][c] / m[c][c];
      for (let k = c; k < 9; k++) m[r][k] -= f * m[c][k];
    }
  }
  const [a, b, c0, d, e, f0, g, hh] = m.map((row, i) => row[8] / row[i]);
  const out = new Uint8ClampedArray(ow * oh * 4);
  for (let y = 0; y < oh; y++) for (let x = 0; x < ow; x++) {
    const px = x + 0.5, py = y + 0.5, den = g * px + hh * py + 1;
    let sx = (a * px + b * py + c0) / den - 0.5, sy = (d * px + e * py + f0) / den - 0.5;
    sx = Math.min(sw - 1, Math.max(0, sx)); sy = Math.min(sh - 1, Math.max(0, sy));
    const x0 = Math.floor(sx), y0 = Math.floor(sy), x1 = Math.min(sw - 1, x0 + 1), y1 = Math.min(sh - 1, y0 + 1);
    const fx = sx - x0, fy = sy - y0, o = (y * ow + x) * 4;
    const i00 = (y0 * sw + x0) * 4, i10 = (y0 * sw + x1) * 4, i01 = (y1 * sw + x0) * 4, i11 = (y1 * sw + x1) * 4;
    for (let k = 0; k < 3; k++) {
      out[o + k] = (src[i00 + k] * (1 - fx) + src[i10 + k] * fx) * (1 - fy) + (src[i01 + k] * (1 - fx) + src[i11 + k] * fx) * fy;
    }
    out[o + 3] = 255;
  }
  return out;
}

if (typeof module !== "undefined") module.exports = { buildPdf, detectQuad, warp, quadOk };

if (typeof document !== "undefined") (function () {
  const scan = { pages: [], busy: false };
  const WORK_SIDE = 2400, SMALL_SIDE = 600, DETECT_SIDE = 320;

  // ---- one page: the photo (kept at two sizes), where the paper is in it, how it is turned, the clean-up --

  function shrunk(src, side) {
    const k = Math.min(1, side / Math.max(src.width, src.height));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(src.width * k)); c.height = Math.max(1, Math.round(src.height * k));
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    return c;
  }

  async function decode(file) {
    let src;
    try { src = await createImageBitmap(file, { imageOrientation: "from-image" }); }
    catch (e) {
      const url = URL.createObjectURL(file);
      try { src = new Image(); src.src = url; await src.decode(); } finally { URL.revokeObjectURL(url); }
    }
    const work = shrunk(src, WORK_SIDE);
    if (src.close) src.close();
    return work;
  }

  function newPage(work) {
    const page = { work, small: shrunk(work, SMALL_SIDE), smallData: null, quad: FULL_QUAD(), turns: 0, clean: $("scan-clean").checked };
    autoFrame(page);
    return page;
  }

  function autoFrame(page) {
    const c = shrunk(page.work, DETECT_SIDE), d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    const gray = new Uint8Array(c.width * c.height);
    for (let i = 0; i < gray.length; i++) gray[i] = (d[i * 4] * 77 + d[i * 4 + 1] * 150 + d[i * 4 + 2] * 29) >> 8;
    const q = detectQuad(gray, c.width, c.height);
    page.quad = q && quadOk(q) ? q : FULL_QUAD();
    return !!q;
  }

  // The straightened page, at most `side` pixels on its longest edge, before turning and clean-up.
  function straighten(page, side) {
    const useSmall = side <= SMALL_SIDE;
    const from = useSmall ? page.small : page.work;
    const full = isFullQuad(page.quad);
    const px = page.quad.map((p) => ({ x: p.x * from.width, y: p.y * from.height }));
    const tw = full ? from.width : (dist(px[0], px[1]) + dist(px[3], px[2])) / 2;
    const th = full ? from.height : (dist(px[0], px[3]) + dist(px[1], px[2])) / 2;
    const k = Math.min(1, side / Math.max(tw, th));
    const ow = Math.max(1, Math.round(tw * k)), oh = Math.max(1, Math.round(th * k));
    const canvas = document.createElement("canvas");
    canvas.width = ow; canvas.height = oh;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (full) { ctx.drawImage(from, 0, 0, ow, oh); return canvas; }
    let data;
    if (useSmall) { if (!page.smallData) page.smallData = page.small.getContext("2d").getImageData(0, 0, from.width, from.height); data = page.smallData; }
    else data = from.getContext("2d").getImageData(0, 0, from.width, from.height);
    ctx.putImageData(new ImageData(warp(data.data, from.width, from.height, px, ow, oh), ow, oh), 0, 0);
    return canvas;
  }

  function render(page, side) {
    const base = straighten(page, side);
    if (page.turns === 0 && !page.clean) return base;
    const turned = page.turns % 2 === 1;
    const w = turned ? base.height : base.width, h = turned ? base.width : base.height;
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.translate(w / 2, h / 2);
    ctx.rotate(page.turns * Math.PI / 2);
    ctx.drawImage(base, -base.width / 2, -base.height / 2);
    if (page.clean) cleanUp(ctx, w, h);
    return canvas;
  }

  // "Document" look: grey, with the paper stretched to white and the ink to black.
  function cleanUp(ctx, w, h) {
    const img = ctx.getImageData(0, 0, w, h), d = img.data;
    const hist = new Uint32Array(256);
    for (let i = 0; i < d.length; i += 4) { const g = (d[i] * 77 + d[i + 1] * 150 + d[i + 2] * 29) >> 8; d[i] = g; hist[g]++; }
    const cut = (share) => { let sum = 0; for (let v = 0; v < 256; v++) { sum += hist[v]; if (sum >= share * w * h) return v; } return 255; };
    const lo = cut(0.02), hi = Math.max(lo + 24, cut(0.55));      // the paper is the brightest bulk of the picture
    const lut = new Uint8ClampedArray(256);
    for (let v = 0; v < 256; v++) lut[v] = Math.max(0, Math.min(255, ((v - lo) * 255) / (hi - lo)));
    for (let i = 0; i < d.length; i += 4) { const g = lut[d[i]]; d[i] = d[i + 1] = d[i + 2] = g; }
    ctx.putImageData(img, 0, 0);
  }

  function toJpeg(canvas) {
    return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not make the picture"))), "image/jpeg", SCAN_QUALITY));
  }

  // ---- the scanner window ---------------------------------------------------------------------------------

  const $ = (id) => document.getElementById(id);
  const tag = (name, props, ...kids) => el(name, props, ...kids);

  function stamp() {
    const d = new Date(), p = (v) => String(v).padStart(2, "0");
    return "Scan " + d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + "." + p(d.getMinutes());
  }

  function redraw() {
    const list = $("scan-pages");
    list.replaceChildren(...scan.pages.map((page, i) => {
      const thumb = render(page, 240);
      thumb.className = "thumb";
      return tag("li", {},
        thumb,
        tag("div", { class: "page-tools" },
          tag("span", { class: "meta" }, "Page " + (i + 1)),
          tag("button", { type: "button", class: "icon", onclick: () => edit(page) }, "Frame"),
          tag("button", { type: "button", class: "icon", onclick: () => { page.turns = (page.turns + 1) % 4; redraw(); } }, "Rotate"),
          tag("button", { type: "button", class: "icon", onclick: () => { page.clean = !page.clean; redraw(); } }, page.clean ? "Original colours" : "Clean up"),
          tag("button", { type: "button", class: "icon", disabled: i === 0, onclick: () => { scan.pages.splice(i - 1, 0, scan.pages.splice(i, 1)[0]); redraw(); } }, "Up"),
          tag("button", { type: "button", class: "icon", onclick: () => { scan.pages.splice(i, 1); redraw(); } }, "Remove")));
    }));
    $("scan-empty").hidden = scan.pages.length > 0;
    $("scan-save").disabled = scan.busy || scan.pages.length === 0;
    $("scan-save").textContent = scan.busy ? "Making PDF…" : "Save as PDF" + (scan.pages.length ? " (" + scan.pages.length + ")" : "");
  }

  async function addFiles(files) {
    for (const file of files) {
      if (!/^image\//.test(file.type)) { toast(file.name + " is not a picture"); continue; }
      try { scan.pages.push(newPage(await decode(file))); }
      catch (e) { toast("Could not open " + file.name); }
    }
    redraw();
  }

  // ---- the frame editor: drag the four corners onto the corners of the paper ------------------------------

  const frame = { page: null, quad: null, drag: -1, scale: 1, canvas: $("frame-canvas") };

  function edit(page) {
    frame.page = page;
    frame.quad = page.quad.map((p) => ({ x: p.x, y: p.y }));
    $("frame").hidden = false;
    paint();
  }

  function paint() {
    const { canvas, page, quad } = frame;
    const room = Math.min(window.innerWidth - 28, 900), tall = window.innerHeight - 190;
    frame.scale = Math.min(room / page.small.width, tall / page.small.height, 3);
    canvas.width = Math.round(page.small.width * frame.scale); canvas.height = Math.round(page.small.height * frame.scale);
    const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
    ctx.drawImage(page.small, 0, 0, W, H);
    ctx.fillStyle = "rgba(0,0,0,.45)";
    ctx.beginPath(); ctx.rect(0, 0, W, H);
    quad.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H)));
    ctx.closePath(); ctx.fill("evenodd");
    ctx.strokeStyle = "#2fb48c"; ctx.lineWidth = 3;
    ctx.beginPath(); quad.forEach((p, i) => (i ? ctx.lineTo(p.x * W, p.y * H) : ctx.moveTo(p.x * W, p.y * H))); ctx.closePath(); ctx.stroke();
    ctx.fillStyle = "#fff";
    for (const p of quad) { ctx.beginPath(); ctx.arc(p.x * W, p.y * H, 11, 0, 7); ctx.fill(); ctx.stroke(); }
    $("frame-ok").disabled = !quadOk(quad);
  }

  function pointer(e) {
    const r = frame.canvas.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  }
  frame.canvas.addEventListener("pointerdown", (e) => {
    const p = pointer(e), r = frame.canvas.getBoundingClientRect();
    let best = -1, bestD = 36 / Math.max(r.width, r.height);          // grab a corner within about 36 px
    frame.quad.forEach((q, i) => { const d = dist(p, q) * (r.width / Math.max(r.width, r.height)); if (d < bestD) { bestD = d; best = i; } });
    if (best < 0) return;
    frame.drag = best;
    frame.canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  frame.canvas.addEventListener("pointermove", (e) => { if (frame.drag >= 0) { frame.quad[frame.drag] = pointer(e); paint(); } });
  const drop = () => { frame.drag = -1; };
  frame.canvas.addEventListener("pointerup", drop);
  frame.canvas.addEventListener("pointercancel", drop);
  $("frame-auto").addEventListener("click", () => {
    const keep = frame.page.quad;
    if (!autoFrame(frame.page)) toast("Could not find the paper: drag the corners yourself");
    frame.quad = frame.page.quad.map((p) => ({ x: p.x, y: p.y }));
    frame.page.quad = keep;
    paint();
  });
  $("frame-full").addEventListener("click", () => { frame.quad = FULL_QUAD(); paint(); });
  $("frame-cancel").addEventListener("click", () => { $("frame").hidden = true; });
  $("frame-ok").addEventListener("click", () => {
    frame.page.quad = frame.quad;
    frame.page.smallData = null;
    $("frame").hidden = true;
    redraw();
  });

  // ---- opening, closing, saving --------------------------------------------------------------------------

  function open() {
    scan.pages = [];
    scan.busy = false;
    $("scan-name").value = stamp();
    $("scan-error").textContent = "";
    $("scan").hidden = false;
    redraw();
  }

  function close() {
    if (scan.pages.length && !confirm("Throw away these " + scan.pages.length + " scanned page(s)?")) return;
    scan.pages = [];
    $("scan").hidden = true;
  }

  async function save() {
    scan.busy = true;
    $("scan-error").textContent = "";
    redraw();
    try {
      const out = [];
      for (const page of scan.pages) {
        const canvas = render(page, SCAN_MAX_SIDE);
        out.push({ jpeg: new Uint8Array(await (await toJpeg(canvas)).arrayBuffer()), width: canvas.width, height: canvas.height });
      }
      const name = ($("scan-name").value.trim().replace(/[\/\\:*?"<>|]+/g, " ").replace(/\.pdf$/i, "").trim() || stamp()) + ".pdf";
      const file = new File([buildPdf(out)], name, { type: "application/pdf" });
      scan.pages = [];
      $("scan").hidden = true;
      enqueue([file]);
    } catch (e) {
      $("scan-error").textContent = e.message;
    }
    scan.busy = false;
    redraw();
  }

  $("scan-open").addEventListener("click", open);
  $("scan-close").addEventListener("click", close);
  $("scan-camera").addEventListener("click", () => $("scan-photo").click());
  $("scan-library").addEventListener("click", () => $("scan-pick").click());
  for (const id of ["scan-photo", "scan-pick"]) {
    $(id).addEventListener("change", () => { const files = [...$(id).files]; $(id).value = ""; addFiles(files); });
  }
  $("scan-save").addEventListener("click", save);
})();
