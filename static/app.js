"use strict";
// Cloud web app. File and folder names from other people are put on the page with textContent or
// append(string), never as HTML.
const $ = (id) => document.getElementById(id);
const state = { me: null, tab: "files", folder: null, path: [], role: "owner", items: [], rootView: true };
const VIEWABLE = /^(image\/(jpeg|png|gif|webp)|video\/(mp4|webm|quicktime)|audio\/(mpeg|mp4|ogg|webm|wav|x-wav)|application\/pdf)$/;

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
  return node;
}

async function api(path, body) {
  const opts = { credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers = { "Content-Type": "application/json", "X-Requested-With": "family-cloud" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Signed out"); }
  if (!res.ok) { const err = new Error((data && data.error) || "Request failed (" + res.status + ")"); err.status = res.status; err.data = data; throw err; }
  return data;
}

function fmtSize(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return (n >= 100 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
let toastTimer = 0;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- sign in / out -------------------------------------------------------------------------

function showLogin() {
  state.me = null;
  $("app").hidden = true;
  $("login").hidden = false;
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  $("login-error").textContent = "";
  try {
    await api("/api/login", { username: form.get("username"), password: form.get("password") });
    e.target.reset();
    await start();
  } catch (err) { $("login-error").textContent = err.message; }
});
$("sign-out").addEventListener("click", async () => {
  try { await api("/api/logout", {}); } catch (e) { /* already out */ }
  showLogin();
});

async function start() {
  state.me = await api("/api/me");
  $("login").hidden = true;
  $("app").hidden = false;
  $("me-name").textContent = state.me.display_name;
  Object.assign(state, { tab: "files", folder: null });
  $("search").value = "";
  paintMe();
  await load();
}

function paintMe() {
  const m = state.me;
  $("storage").textContent = fmtSize(m.used) + " used" + (m.quota ? " of " + fmtSize(m.quota) : " · " + fmtSize(m.disk_free) + " free on the drive");
  $("drive-warning").hidden = m.drive_ok;
}
async function refreshMe() { try { state.me = await api("/api/me"); paintMe(); } catch (e) { /* shown next time */ } }

// ---- browsing ------------------------------------------------------------------------------

async function load() {
  const q = $("search").value.trim();
  try {
    if (q) {
      Object.assign(state, { items: await api("/api/search?q=" + encodeURIComponent(q)), path: [], role: "view", rootView: false });
    } else if (state.tab === "trash") {
      Object.assign(state, { items: await api("/api/trash"), path: [], role: "none", rootView: true });
    } else if (state.tab === "shared" && state.folder === null) {
      Object.assign(state, { items: await api("/api/shared"), path: [], role: "none", rootView: true });
    } else {
      const r = await api("/api/folder" + (state.folder === null ? "" : "?id=" + state.folder));
      Object.assign(state, { items: r.items, path: r.path, role: r.role, rootView: state.folder === null });
    }
  } catch (err) {
    if (err.status === 404 && state.folder !== null) { state.folder = null; return load(); }
    toast(err.message);
    return;
  }
  render();
}

function crumb(label, folderId) {
  return el("button", { type: "button", onclick: () => { state.folder = folderId; $("search").value = ""; load(); } }, label);
}

function render() {
  const q = $("search").value.trim();
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", !q && b.dataset.tab === state.tab));
  const trail = [];
  if (q) trail.push(el("span", { class: "now" }, "Results for “" + q + "”"));
  else if (state.tab === "trash") trail.push(el("span", { class: "now" }, "Trash: kept for 30 days"));
  else {
    const home = state.tab === "shared" ? "Shared with me" : "My files";
    trail.push(state.folder === null ? el("span", { class: "now" }, home) : crumb(home, null));
    state.path.forEach((p, i) => { trail.push(" / "); trail.push(i === state.path.length - 1 ? el("span", { class: "now" }, p.name) : crumb(p.name, p.id)); });
  }
  $("crumbs").replaceChildren(...trail);
  const canWrite = !q && state.tab !== "trash" && (state.role === "owner" || state.role === "edit") && !(state.tab === "shared" && state.folder === null);
  $("new-folder").hidden = $("upload").hidden = !canWrite;
  $("empty-trash").hidden = !(state.tab === "trash" && !q && state.items.length);
  const rows = state.items.map(row);
  $("list").replaceChildren(...rows);
  const empty = $("empty");
  empty.hidden = rows.length > 0;
  empty.textContent = q ? "Nothing found." : state.tab === "trash" ? "The trash is empty." : state.tab === "shared" && state.folder === null
    ? "Nothing has been shared with you yet." : "This folder is empty. Tap Upload to add files.";
}

function row(item) {
  const folder = item.kind === "folder";
  const thumb = el("div", { class: "thumb" + (folder ? " folder" : "") },
    folder ? "▸" : item.thumb ? el("img", { src: "/api/thumb/" + item.id, alt: "", loading: "lazy" }) : ((item.name.split(".").pop() || "").slice(0, 4).toUpperCase() || "FILE"));
  const meta = (folder ? "" : fmtSize(item.size) + " · ") + fmtDate(item.modified) +
    (item.role !== "owner" && item.role !== "none" && item.role ? " · " + item.role : "");
  const li = el("li", { class: state.tab === "trash" ? "" : "open" },
    thumb,
    el("div", { class: "body" }, el("div", { class: "name" }, item.name, item.shared ? el("span", { class: "tag" }, "shared") : null), el("div", { class: "meta" }, meta)),
    el("button", { class: "more", type: "button", "aria-label": "Actions for " + item.name, onclick: (e) => { e.stopPropagation(); actions(item); } }, "···"));
  if (state.tab !== "trash") li.addEventListener("click", () => openItem(item));
  return li;
}

function openItem(item) {
  if (item.kind === "folder") { state.folder = item.id; $("search").value = ""; load(); return; }
  if (VIEWABLE.test(item.mime || "")) viewer(item);
  else download(item);
}
function download(item) {
  const a = el("a", { href: "/api/file/" + item.id + "?download=1", download: item.name });
  document.body.append(a);
  a.click();
  a.remove();
}

for (const b of document.querySelectorAll("#tabs button")) {
  b.addEventListener("click", () => { state.tab = b.dataset.tab; state.folder = null; $("search").value = ""; load(); });
}
let searchTimer = 0;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(load, 300); });

// ---- viewer --------------------------------------------------------------------------------

function viewer(item) {
  const src = "/api/file/" + item.id;
  let body;
  if (item.mime.startsWith("image/")) body = el("img", { src, alt: item.name });
  else if (item.mime.startsWith("video/")) body = el("video", { src, controls: true, autoplay: true, playsinline: true });
  else if (item.mime.startsWith("audio/")) body = el("audio", { src, controls: true, autoplay: true });
  else body = el("iframe", { src, title: item.name });
  $("viewer-name").textContent = item.name;
  $("viewer-download").setAttribute("href", src + "?download=1");
  $("viewer-download").setAttribute("download", item.name);
  $("viewer-body").replaceChildren(body);
  $("viewer").hidden = false;
}
function closeViewer() { $("viewer").hidden = true; $("viewer-body").replaceChildren(); }   // removing the element stops playback
$("viewer-close").addEventListener("click", closeViewer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeViewer(); closeSheet(); } });

// ---- sheets (rename, move, share, actions) -------------------------------------------------

function closeSheet() { $("sheet").hidden = true; $("sheet-form").replaceChildren(); $("sheet-form").onsubmit = null; }
function openSheet(title, ...nodes) {
  $("sheet-form").replaceChildren(el("h2", {}, title), ...nodes.flat().filter((n) => n));
  $("sheet-form").onsubmit = (e) => e.preventDefault();
  $("sheet").hidden = false;
}
$("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
const cancelBtn = () => el("button", { type: "button", class: "icon", onclick: closeSheet }, "Cancel");

function nameSheet(title, initial, label, action) {
  const input = el("input", { value: initial, "aria-label": "Name", maxlength: "255", autocomplete: "off" });
  const error = el("p", { id: "sheet-error", role: "alert" });
  openSheet(title, input, error, el("div", { class: "row" }, cancelBtn(), el("button", { type: "submit" }, label)));
  $("sheet-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await action(input.value); closeSheet(); await load(); } catch (err) { error.textContent = err.message; }
  };
  input.focus();
  input.select();
}

function actions(item) {
  const mine = item.role === "owner";
  const shareRoot = !mine && state.tab === "shared" && state.folder === null;
  const canChange = (item.role === "owner" || item.role === "edit") && !shareRoot;
  const buttons = [];
  const b = (label, fn, cls) => buttons.push(el("button", { type: "button", class: cls || "", onclick: () => { closeSheet(); fn(); } }, label));
  if (state.tab === "trash") {
    b("Restore", async () => { try { await api("/api/restore", { id: item.id }); toast("Restored."); await load(); } catch (e) { toast(e.message); } });
    b("Delete forever", async () => {
      if (!confirm("Delete “" + item.name + "” forever? This cannot be undone.")) return;
      try { await api("/api/delete", { id: item.id }); await load(); refreshMe(); } catch (e) { toast(e.message); }
    }, "danger");
  } else {
    if (item.kind === "folder") b("Open", () => openItem(item));
    else { if (VIEWABLE.test(item.mime || "")) b("Open", () => openItem(item)); b("Download", () => download(item)); }
    if (canChange) b("Rename", () => nameSheet("Rename", item.name, "Save", (name) => api("/api/rename", { id: item.id, name })));
    if (canChange && mine) b("Move", () => movePicker(item));
    if (mine) b("Share with family", () => shareSheet(item));
    if (canChange) b("Move to trash", async () => { try { await api("/api/trash", { id: item.id }); toast("Moved to the trash."); await load(); } catch (e) { toast(e.message); } }, "danger");
  }
  openSheet(item.name, el("div", { class: "actions" }, buttons), el("div", { class: "row" }, cancelBtn()));
}

async function movePicker(item) {
  async function show(dest) {
    let listing;
    try { listing = await api("/api/folder" + (dest === null ? "" : "?id=" + dest)); } catch (e) { toast(e.message); closeSheet(); return; }
    const folders = listing.items.filter((i) => i.kind === "folder" && i.id !== item.id);
    const where = dest === null ? "My files" : listing.folder.name;
    const picker = el("div", { class: "picker" },
      dest !== null ? el("button", { type: "button", onclick: () => show(listing.path.length > 1 ? listing.path[listing.path.length - 2].id : null) }, "‹ Up") : null,
      folders.map((f) => el("button", { type: "button", onclick: () => show(f.id) }, "▸ " + f.name)));
    const error = el("p", { id: "sheet-error", role: "alert" });
    openSheet("Move “" + item.name + "” to", el("p", {}, "Now in: " + where), picker, error, el("div", { class: "row" }, cancelBtn(),
      el("button", { type: "button", onclick: async () => {
        try { await api("/api/move", { id: item.id, parent: dest }); closeSheet(); await load(); } catch (err) { error.textContent = err.message; }
      } }, "Move here")));
  }
  await show(state.tab === "files" ? state.folder : null);
}

async function shareSheet(item) {
  let people, current;
  try { [people, current] = await Promise.all([api("/api/users"), api("/api/shares?id=" + item.id)]); } catch (e) { toast(e.message); return; }
  const roles = new Map(current.map((s) => [s.user_id, s.role]));
  const rows = people.map((p) => {
    const select = el("select", { "aria-label": "Access for " + p.display_name },
      el("option", { value: "" }, "No access"), el("option", { value: "view" }, "Can view"), el("option", { value: "edit" }, "Can edit"));
    select.value = roles.get(p.id) || "";
    select.addEventListener("change", async () => {
      try { await api("/api/share", { id: item.id, user: p.id, role: select.value || null }); toast("Saved."); load(); } catch (e) { toast(e.message); }
    });
    return el("div", { class: "person" }, el("span", { class: "spacer" }, p.display_name), select);
  });
  openSheet("Share “" + item.name + "”", el("p", {}, item.kind === "folder" ? "Everything inside is shared too. Editors can add, rename and delete files; viewers can only look and download." : "The person can open and download this file."),
    rows.length ? rows : el("p", {}, "There is nobody else on this server yet."), el("div", { class: "row" }, el("button", { type: "button", onclick: closeSheet }, "Done")));
}

$("new-folder").addEventListener("click", () => nameSheet("New folder", "", "Create", (name) => api("/api/folder", { parent: state.folder, name })));
$("empty-trash").addEventListener("click", async () => {
  if (!confirm("Delete everything in the trash forever?")) return;
  try { await api("/api/trash/empty", {}); await load(); refreshMe(); } catch (e) { toast(e.message); }
});

// ---- uploads: in chunks, one file at a time, retried and resumed if the connection drops ---------

const jobs = [];
let pumping = false;

function showUploads() {
  $("uploads").hidden = false;
  const done = jobs.filter((j) => j.state === "done").length;
  $("uploads-title").textContent = done === jobs.length ? "Uploads finished" : "Uploading " + (done + 1) + " of " + jobs.length;
}
$("uploads-close").addEventListener("click", () => {
  if (!jobs.some((j) => j.state === "waiting" || j.state === "running")) { jobs.length = 0; $("upload-list").replaceChildren(); }
  $("uploads").hidden = true;
});

function enqueue(files) {
  if (!files.length) return;
  for (const file of files) {
    const bar = el("progress", { max: String(Math.max(1, file.size)), value: "0" });
    const status = el("span", { class: "meta" }, "Waiting");
    const cancel = el("button", { type: "button", class: "icon", onclick: () => { job.cancelled = true; if (job.state === "waiting") finishJob(job, "cancelled", "Cancelled"); } }, "Cancel");
    const li = el("li", {}, el("div", { class: "row" }, el("span", { class: "name" }, file.name), status, cancel), bar);
    const job = { file, parent: state.folder, state: "waiting", cancelled: false, bar, status, li, cancel, id: null };
    jobs.push(job);
    $("upload-list").append(li);
  }
  showUploads();
  pump();
}

function finishJob(job, st, text) {
  job.state = st;
  job.status.textContent = text;
  job.status.classList.toggle("failed", st === "failed");
  job.cancel.hidden = true;
  showUploads();
}

async function pump() {
  if (pumping) return;
  pumping = true;
  let uploaded = false;
  for (const job of jobs) {
    if (job.state !== "waiting") continue;
    job.state = "running";
    job.status.textContent = "Starting…";
    try {
      await uploadOne(job);
      if (job.cancelled) throw Object.assign(new Error("Cancelled"), { cancelled: true });
      finishJob(job, "done", "Done");
      uploaded = true;
    } catch (err) {
      if (job.id) { try { await api("/api/upload/" + job.id + "/cancel", {}); } catch (e) { /* cleaned up later */ } }
      finishJob(job, err.cancelled ? "cancelled" : "failed", err.cancelled ? "Cancelled" : err.message);
    }
  }
  pumping = false;
  if (uploaded) { await load(); refreshMe(); }
}

async function uploadOne(job) {
  const file = job.file;
  const start = await api("/api/upload/start", { parent: job.parent, name: file.name, size: file.size, mime: file.type });
  job.id = start.id;
  let offset = 0, failures = 0;
  while (offset < file.size) {
    if (job.cancelled) return;
    const end = Math.min(offset + start.chunk, file.size);
    try {
      const res = await fetch("/api/upload/" + start.id + "?offset=" + offset, { method: "PUT", credentials: "same-origin", body: file.slice(offset, end),
        headers: { "X-Requested-With": "family-cloud", "Content-Type": "application/octet-stream" } });
      if (res.status === 401) { showLogin(); throw new Error("Signed out"); }
      if (res.status === 409) { offset = (await res.json()).received; continue; }       // the server knows where it is: carry on from there
      if (!res.ok) { const data = await res.json().catch(() => ({})); throw Object.assign(new Error(data.error || "Upload failed (" + res.status + ")"), { fatal: true }); }
      offset = end;
      failures = 0;
      job.bar.value = offset;
      job.status.textContent = Math.floor((offset / file.size) * 100) + "%";
    } catch (err) {
      if (err.fatal || err.message === "Signed out" || ++failures > 6) throw err;
      job.status.textContent = "Connection lost, retrying…";
      await sleep(Math.min(8000, 800 * 2 ** failures));
      try { offset = (await api("/api/upload/" + start.id)).received; } catch (e) { /* try again next round */ }   // where did it get to?
    }
  }
  if (job.cancelled) return;
  job.status.textContent = "Finishing…";
  await api("/api/upload/" + start.id + "/finish", {});
}

$("upload").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => { const files = [...$("file").files]; $("file").value = ""; enqueue(files); });
document.addEventListener("dragover", (e) => { if (!$("new-folder").hidden) e.preventDefault(); });
document.addEventListener("drop", (e) => {
  if ($("new-folder").hidden || !e.dataTransfer || !e.dataTransfer.files.length) return;
  e.preventDefault();
  enqueue([...e.dataTransfer.files]);
});
window.addEventListener("beforeunload", (e) => { if (jobs.some((j) => j.state === "running")) { e.preventDefault(); e.returnValue = ""; } });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { /* needs HTTPS or localhost */ });
api("/api/me").then(start).catch(() => { if (!state.me) showLogin(); });
