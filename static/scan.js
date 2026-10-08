"use strict";
// Document scanner: take or pick photos of pages, tidy them up, and save them as one PDF in the current folder.
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

if (typeof module !== "undefined") module.exports = { buildPdf };

if (typeof document !== "undefined") (function () {
  const scan = { pages: [], busy: false };

  // ---- one page: the original photo, how it is turned, and whether it gets the "document" clean-up -------

  async function decode(file) {
    try { return await createImageBitmap(file, { imageOrientation: "from-image" }); }
    catch (e) {
      const url = URL.createObjectURL(file);
      try {
        const img = new Image();
        img.src = url;
        await img.decode();
        return img;
      } finally { URL.revokeObjectURL(url); }
    }
  }

  // Draw a page at most `side` pixels on its longest edge, turned and cleaned up as chosen.
  function render(page, side) {
    const turned = page.turns % 2 === 1;
    const sw = page.src.width, sh = page.src.height;
    const scale = Math.min(1, side / Math.max(sw, sh));
    const w = Math.max(1, Math.round((turned ? sh : sw) * scale)), h = Math.max(1, Math.round((turned ? sw : sh) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, w, h);
    ctx.translate(w / 2, h / 2);
    ctx.rotate(page.turns * Math.PI / 2);
    const dw = Math.round(sw * scale), dh = Math.round(sh * scale);
    ctx.drawImage(page.src, -dw / 2, -dh / 2, dw, dh);
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
      try { scan.pages.push({ src: await decode(file), turns: 0, clean: $("scan-clean").checked }); }
      catch (e) { toast("Could not open " + file.name); }
    }
    redraw();
  }

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
