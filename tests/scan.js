// Scan-to-PDF in a real browser: add pictures, rotate, clean up, save, then check the PDF that was uploaded.
//   node tests/scan.js    # PORT=8790, CHROMIUM=/path/to/chrome
//
//   npm install            # once, in the repository root: Playwright
//   node tests/smoke.js    # PORT=8790, CHROMIUM=/path/to/chrome
//
// Starts server.py on a throwaway data directory and checks: sign-in, folders, uploads (with a 9 MB file in
// chunks, and one whose connection is cut half way), thumbnails and the viewer, rename and move, sharing as
// viewer and as editor, a download, the storage limit, trash and restore, search. Exit 0 = pass.
const { chromium } = require("playwright");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PORT = Number(process.env.PORT || 8790);
const SERVER = path.join(__dirname, "..", "server.py");
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), "family-cloud-"));
const base = `http://127.0.0.1:${PORT}/`;
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log((ok ? "ok   " : "FAIL ") + what); };

function makePng(w, h, rgb) {
  const zlib = require("zlib");
  const table = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf) => { let c = 0xFFFFFFFF; for (const byte of buf) c = table[(c ^ byte) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const sum = Buffer.alloc(4); sum.writeUInt32BE(crc(body));
    return Buffer.concat([len, body, sum]);
  };
  const head = Buffer.alloc(13); head.writeUInt32BE(w, 0); head.writeUInt32BE(h, 4); head[8] = 8; head[9] = 2;
  const rows = [];
  for (let y = 0; y < h; y++) { const row = Buffer.alloc(1 + w * 3); for (let x = 0; x < w; x++) { if (rgb) rgb.copy(row, 1 + x * 3, (y * w + x) * 3, (y * w + x) * 3 + 3); else { row[1 + x * 3] = x % 256; row[2 + x * 3] = y % 256; row[3 + x * 3] = 120; } } rows.push(row); }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
}
const PNG = makePng(240, 160);
const { paperPhoto, QUAD } = require("./paper.js");
const PHOTO = makePng(600, 800, paperPhoto(600, 800, QUAD));
const pattern = (n) => Buffer.from(Uint8Array.from({ length: n }, (_, i) => (i * 31 + (i >> 8)) % 251));    // position-dependent, so a lost or repeated chunk shows up

function addUser(name, display, extra = []) {
  const r = spawnSync("python3", [SERVER, "--data-dir", DATA, "add-user", name, "--name", display, "--password-stdin", ...extra], { input: "correct horse\n" });
  if (r.status !== 0) throw new Error("add-user failed: " + r.stderr);
}

(async () => {
  addUser("anna", "Anna");
  const proc = spawn("python3", [SERVER, "--data-dir", DATA, "serve", "--port", String(PORT)], { stdio: ["ignore", "ignore", "ignore"] });
  for (let i = 0; i < 80; i++) { try { await fetch(base); break; } catch (e) { await new Promise((r) => setTimeout(r, 100)); } }
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  try {
    const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, acceptDownloads: true })).newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (d) => d.accept());
    await page.goto(base);
    await page.waitForSelector("#login-form");
    await page.fill("[name=username]", "anna");
    await page.fill("[name=password]", "correct horse");
    await page.click("#login-form button");
    await page.waitForSelector("#scan-open:not([hidden])");

    await page.click("#scan-open");
    check(await page.isDisabled("#scan-save"), "Save is off until there is a page");
    await page.setInputFiles("#scan-pick", [
      { name: "one.png", mimeType: "image/png", buffer: PNG },
      { name: "two.png", mimeType: "image/png", buffer: makePng(160, 240) },
    ]);
    await page.waitForFunction(() => document.querySelectorAll("#scan-pages li").length === 2);
    await page.locator("#scan-pages li").nth(1).locator("button", { hasText: "Rotate" }).click();
    await page.locator("#scan-pages li").nth(0).locator("button", { hasText: "Remove" }).click();
    await page.setInputFiles("#scan-photo", { name: "three.png", mimeType: "image/png", buffer: PNG });
    await page.waitForFunction(() => document.querySelectorAll("#scan-pages li").length === 2);
    check((await page.textContent("#scan-save")).includes("(2)"), "two pages are kept after one is removed");
    await page.fill("#scan-name", "Tax letter");
    await page.click("#scan-save");
    await page.waitForFunction(() => document.getElementById("uploads-title").textContent === "Uploads finished" || document.querySelector("#upload-list .failed"), null, { timeout: 30000 });
    check(!(await page.$("#upload-list .failed")), "the PDF uploads");
    check(await page.locator("#list li", { hasText: "Tax letter.pdf" }).count() === 1, "it is listed as Tax letter.pdf");

    await page.locator("#list li", { hasText: "Tax letter.pdf" }).locator("button.more").click();
    const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#sheet-form .actions button", { hasText: /^Download$/ }).click()]);
    const file = path.join(DATA, "out.pdf");
    await download.saveAs(file);
    const info = spawnSync("pdfinfo", [file], { encoding: "utf8" });
    check(info.status === 0 && /Pages:\s+2/.test(info.stdout), "pdfinfo reads a 2-page PDF: " + (info.stdout || info.stderr).split("\n").filter((l) => /Pages|Page size|Producer|Error/.test(l)).join(" | "));
    const ppm = spawnSync("pdftoppm", ["-r", "20", "-png", file, path.join(DATA, "pg")]);
    check(ppm.status === 0 && fs.existsSync(path.join(DATA, "pg-2.png")), "both pages render");
    // A photo of paper on a table: the scanner should cut the table away and straighten the page.
    await page.click("#scan-open");
    await page.setInputFiles("#scan-pick", { name: "paper.png", mimeType: "image/png", buffer: PHOTO });
    await page.waitForFunction(() => document.querySelectorAll("#scan-pages li").length === 1);
    await page.locator("#scan-pages li button", { hasText: "Original colours" }).click();      // colours back on, so the table would show
    await page.locator("#scan-pages li button", { hasText: "Frame" }).click();
    check(await page.isVisible("#frame-canvas"), "the frame editor opens");
    await page.click("#frame-ok");
    await page.fill("#scan-name", "Paper");
    await page.click("#scan-save");
    await page.waitForFunction(() => [...document.querySelectorAll("#list li")].some((li) => li.textContent.includes("Paper.pdf")));
    await page.locator("#list li", { hasText: "Paper.pdf" }).locator("button.more").click();
    const [dl2] = await Promise.all([page.waitForEvent("download"), page.locator("#sheet-form .actions button", { hasText: /^Download$/ }).click()]);
    const file2 = path.join(DATA, "paper.pdf");
    await dl2.saveAs(file2);
    spawnSync("pdftoppm", ["-r", "30", file2, path.join(DATA, "paper")]);       // a PPM picture
    const ppm2 = fs.readFileSync(path.join(DATA, "paper-1.ppm"));
    const [pw, ph] = ppm2.toString("latin1", 0, 30).split(/\s+/).slice(1, 3).map(Number);
    const pix = ppm2.subarray(ppm2.length - pw * ph * 3);
    const lum = (x, y) => pix[(y * pw + x) * 3];
    const edge = [[2, 2], [pw - 3, 2], [pw - 3, ph - 3], [2, ph - 3]].map(([x, y]) => lum(x, y));
    check(edge.every((v) => v > 180), "the saved page has paper in all four corners, not the table (" + edge.join(",") + ")");
    check(Math.abs(pw / ph - 0.72) < 0.15, "the page has a paper-like shape, " + pw + " x " + ph);

    check(errors.length === 0, "no page errors" + (errors.length ? ": " + errors.join("; ") : ""));
  } catch (e) {
    failures.push("crashed: " + e.message);
    console.log("FAIL crashed:", e.stack || e);
  } finally {
    await browser.close();
    proc.kill();
    fs.rmSync(DATA, { recursive: true, force: true });
  }
  console.log(failures.length ? failures.length + " failed" : "all passed");
  process.exit(failures.length ? 1 : 0);
})();
