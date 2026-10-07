// Family Cloud in a real browser: two people on two "phones" use it the way a family would.
//
//   npm install            # once, in the repository root: Playwright
//   node tests/smoke.js    # PORT=8789, CHROMIUM=/path/to/chrome
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

const PORT = Number(process.env.PORT || 8789);
const SERVER = path.join(__dirname, "..", "server.py");
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), "family-cloud-"));
const base = `http://127.0.0.1:${PORT}/`;
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log((ok ? "ok   " : "FAIL ") + what); };

function makePng(w, h) {
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
  for (let y = 0; y < h; y++) { const row = Buffer.alloc(1 + w * 3); for (let x = 0; x < w; x++) { row[1 + x * 3] = x % 256; row[2 + x * 3] = y % 256; row[3 + x * 3] = 120; } rows.push(row); }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
}
const PNG = makePng(240, 160);
const pattern = (n) => Buffer.from(Uint8Array.from({ length: n }, (_, i) => (i * 31 + (i >> 8)) % 251));    // position-dependent, so a lost or repeated chunk shows up

function addUser(name, display, extra = []) {
  const r = spawnSync("python3", [SERVER, "--data-dir", DATA, "add-user", name, "--name", display, "--password-stdin", ...extra], { input: "correct horse\n" });
  if (r.status !== 0) throw new Error("add-user failed: " + r.stderr);
}

(async () => {
  addUser("anna", "Anna");
  addUser("ben", "Ben", ["--quota-gb", "0.00001"]);          // about 10 KB of his own, to try the limit
  let proc = spawn("python3", [SERVER, "--data-dir", DATA, "serve", "--port", String(PORT)], { stdio: ["ignore", "ignore", "ignore"] });
  for (let i = 0; i < 80; i++) { try { await fetch(base); break; } catch (e) { await new Promise((r) => setTimeout(r, 100)); } }
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  try {
    const open = async () => {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.errors = [];
      page.on("pageerror", (e) => page.errors.push(String(e)));
      page.on("dialog", (d) => d.accept());
      await page.goto(base);
      return page;
    };
    const login = async (page, user, password = "correct horse") => {
      await page.waitForSelector("#login-form");
      await page.fill("[name=username]", user);
      await page.fill("[name=password]", password);
      await page.click("#login-form button");
    };
    const row = (page, name) => page.locator("#list li", { hasText: name });
    const action = async (page, name, label) => {
      await row(page, name).locator("button.more").click();
      await page.locator("#sheet-form .actions button", { hasText: new RegExp("^" + label + "$") }).click();
    };
    const uploaded = async (page, files) => {
      await page.setInputFiles("#file", files);
      await page.waitForFunction(() => document.getElementById("uploads-title").textContent === "Uploads finished" || document.querySelector("#upload-list .failed"), null, { timeout: 60000 });
    };
    const a = await open(), b = await open();

    // sign in, an empty cloud, a folder
    await login(a, "anna", "wrong password");
    await a.waitForFunction(() => document.getElementById("login-error").textContent.length > 0);
    check(/wrong username or password/i.test(await a.textContent("#login-error")), "wrong password shows an error");
    await login(a, "anna");
    await a.waitForSelector("#app:not([hidden])");
    check((await a.textContent("#me-name")) === "Anna" && /used/.test(await a.textContent("#storage")), "signed in, storage line shown");
    check((await a.textContent("#empty")).includes("empty"), "an empty cloud says so");
    await a.click("#new-folder");
    await a.fill("#sheet-form input", "Holiday");
    await a.click("#sheet-form button[type=submit]");
    await row(a, "Holiday").waitFor();
    check(true, "a folder is created");
    await row(a, "Holiday").click();
    await a.waitForFunction(() => document.getElementById("crumbs").textContent.includes("Holiday"));
    check((await a.textContent("#crumbs")).includes("My files"), "the path shows where you are");

    // uploads: a picture (thumbnail), a text file, then a 9 MB file in three chunks
    await uploaded(a, [{ name: "beach.png", mimeType: "image/png", buffer: PNG }, { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("remember the sunscreen") }]);
    await row(a, "beach.png").waitFor();
    check((await row(a, "notes.txt").count()) === 1, "two files uploaded into the folder");
    const hasFfmpeg = spawnSync("ffmpeg", ["-version"]).status === 0;
    if (hasFfmpeg) {
      await a.waitForFunction(() => { const i = document.querySelector("#list li img"); return i && i.complete && i.naturalWidth > 0; });
      check(true, "the picture has a thumbnail");
    } else console.log("skip  no ffmpeg: thumbnails not checked");
    const big = pattern(9 * 1024 * 1024);
    await uploaded(a, [{ name: "movie.bin", mimeType: "application/octet-stream", buffer: big }]);
    await row(a, "movie.bin").waitFor();
    const want = crypto.createHash("sha256").update(big).digest("hex");
    const got = await a.evaluate(async () => {
      const r = await fetch([...document.querySelectorAll("#list li")].find((li) => li.textContent.includes("movie.bin")) && "/api/folder?id=" + state.folder);
      const items = (await r.json()).items;
      const id = items.find((i) => i.name === "movie.bin").id;
      const buf = await (await fetch("/api/file/" + id)).arrayBuffer();
      return { size: buf.byteLength, sha: [...new Uint8Array(await crypto.subtle.digest("SHA-256", buf))].map((x) => x.toString(16).padStart(2, "0")).join("") };
    });
    check(got.size === big.length && got.sha === want, "a 9 MB file arrives byte for byte after being sent in chunks");

    // a connection that drops in the middle of an upload: the second chunk's first try is cut
    let cut = false;
    await a.route((url) => url.pathname.startsWith("/api/upload/") && url.search.includes("offset=4194304"), (route) => {
      if (!cut) { cut = true; route.abort(); } else route.continue();
    });
    const resume = pattern(9 * 1024 * 1024 + 123);
    await a.setInputFiles("#file", [{ name: "resumed.bin", mimeType: "application/octet-stream", buffer: resume }]);
    await a.waitForFunction(() => /retrying/i.test(document.getElementById("upload-list").textContent), null, { timeout: 30000 });
    check(true, "a cut connection is noticed ('retrying')");
    await a.waitForFunction(() => document.getElementById("uploads-title").textContent === "Uploads finished", null, { timeout: 60000 });
    await a.unroute("**/*");
    await row(a, "resumed.bin").waitFor();
    const sha2 = await a.evaluate(async () => {
      const items = (await (await fetch("/api/folder?id=" + state.folder)).json()).items;
      const buf = await (await fetch("/api/file/" + items.find((i) => i.name === "resumed.bin").id)).arrayBuffer();
      return { size: buf.byteLength, sha: [...new Uint8Array(await crypto.subtle.digest("SHA-256", buf))].map((x) => x.toString(16).padStart(2, "0")).join("") };
    });
    check(cut && sha2.size === resume.length && sha2.sha === crypto.createHash("sha256").update(resume).digest("hex"), "the interrupted upload resumed and is intact");
    await a.click("#uploads-close");

    // the viewer, rename, move
    await row(a, "beach.png").click();
    await a.waitForSelector("#viewer:not([hidden]) img");
    check(await a.evaluate(() => document.querySelector("#viewer-body img").naturalWidth) === 240, "a picture opens full size in the viewer");
    await a.click("#viewer-close");
    check(await a.isHidden("#viewer"), "the viewer closes");
    await action(a, "notes.txt", "Rename");
    await a.fill("#sheet-form input", "trip-notes.txt");
    await a.click("#sheet-form button[type=submit]");
    await row(a, "trip-notes.txt").waitFor();
    check(true, "a file is renamed");
    await action(a, "trip-notes.txt", "Move");
    await a.locator("#sheet-form .picker button", { hasText: "Up" }).click();
    await a.locator("#sheet-form button", { hasText: "Move here" }).click();
    await a.waitForFunction(() => !document.querySelector("#list").textContent.includes("trip-notes.txt"));
    check((await row(a, "trip-notes.txt").count()) === 0, "moving it takes it out of this folder");
    await a.click("#crumbs button");
    await row(a, "trip-notes.txt").waitFor();
    check(true, "and it is in My files");

    // sharing: Ben first as viewer
    await action(a, "Holiday", "Share with family");
    await a.selectOption("#sheet-form select[aria-label='Access for Ben']", "view");
    await a.waitForSelector("#toast:not([hidden])");
    await a.click("#sheet-form button", { hasText: "Done" });
    await row(a, "Holiday").locator(".tag").waitFor();
    check(true, "the shared folder is tagged");
    await login(b, "ben");
    await b.waitForSelector("#app:not([hidden])");
    check((await row(b, "Holiday").count()) === 0, "Ben's own files are separate from Anna's");
    await b.click("#tabs button[data-tab=shared]");
    await row(b, "Holiday").waitFor();
    await row(b, "Holiday").click();
    await row(b, "beach.png").waitFor();
    check(await b.isHidden("#upload") && await b.isHidden("#new-folder"), "a viewer cannot upload or make folders");
    await row(b, "beach.png").locator("button.more").click();
    const labels = await b.locator("#sheet-form .actions button").allTextContents();
    check(labels.includes("Download") && !labels.some((l) => /Rename|trash|Move|Share/.test(l)), "a viewer can only open and download: " + labels.join(", "));
    const [dl] = await Promise.all([b.waitForEvent("download"), b.locator("#sheet-form .actions button", { hasText: "Download" }).click()]);
    check(dl.suggestedFilename() === "beach.png" && fs.statSync(await dl.path()).size === PNG.length, "Ben can download Anna's shared picture");

    // Ben as editor
    await a.click("#crumbs button", { hasText: "My files" }).catch(() => {});
    await action(a, "Holiday", "Share with family");
    await a.selectOption("#sheet-form select[aria-label='Access for Ben']", "edit");
    await a.waitForSelector("#toast:not([hidden])");
    await a.click("#sheet-form button", { hasText: "Done" });
    await b.click("#tabs button[data-tab=shared]");
    await row(b, "Holiday").click();
    await b.waitForSelector("#upload:not([hidden])");
    check(true, "an editor can upload");
    await uploaded(b, [{ name: "from-ben.txt", mimeType: "text/plain", buffer: Buffer.from("hello from Ben") }]);
    await row(b, "from-ben.txt").waitFor();
    await b.click("#uploads-close");
    await row(a, "Holiday").click();
    await row(a, "from-ben.txt").waitFor();
    check(true, "Anna sees what Ben added");
    // the share root itself is not Ben's to change
    await b.click("#crumbs button", { hasText: "Shared with me" });
    await row(b, "Holiday").locator("button.more").click();
    const rootLabels = await b.locator("#sheet-form .actions button").allTextContents();
    check(!rootLabels.some((l) => /Rename|trash|Move/.test(l)), "an editor cannot rename or delete the shared folder itself: " + rootLabels.join(", "));
    await b.keyboard.press("Escape");

    // the storage limit on Ben's own space
    await b.click("#tabs button[data-tab=files]");
    await uploaded(b, [{ name: "too-big.bin", mimeType: "application/octet-stream", buffer: Buffer.alloc(50_000, 1) }]);
    check(/storage limit/i.test(await b.textContent("#upload-list")), "going over the storage limit is refused with a clear message");

    // trash and restore, then search
    await row(a, "beach.png").waitFor();
    await action(a, "beach.png", "Move to trash");
    await a.waitForFunction(() => !document.querySelector("#list").textContent.includes("beach.png"));
    await a.click("#tabs button[data-tab=trash]");
    await row(a, "beach.png").waitFor();
    check(await a.isVisible("#empty-trash"), "the trash shows what was deleted");
    await action(a, "beach.png", "Restore");
    await a.click("#tabs button[data-tab=files]");
    await row(a, "Holiday").click();
    await row(a, "beach.png").waitFor();
    check(true, "a restored file is back in its folder");
    await a.fill("#search", "notes");
    await row(a, "trip-notes.txt").waitFor();
    check((await a.locator("#list li").count()) === 1, "search finds a file by name");
    await a.fill("#search", "");
    await a.click("#sign-out");
    await a.waitForSelector("#login:not([hidden])");
    check(await a.isHidden("#app"), "signing out shows the sign-in page");

    await b.screenshot({ path: path.join(os.tmpdir(), "family-cloud-ben.png") });
    check(a.errors.length === 0 && b.errors.length === 0, "no page errors: " + JSON.stringify([a.errors, b.errors]));
  } finally {
    await browser.close();
    proc.kill();
  }
  console.log(failures.length ? "FAIL\n  " + failures.join("\n  ") : "OK");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
