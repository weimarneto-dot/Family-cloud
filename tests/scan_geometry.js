// Paper detection and straightening, without a browser: node tests/scan_geometry.js
const { detectQuad, warp, quadOk } = require("../static/scan.js");
const { paperPhoto, QUAD } = require("./paper.js");
let bad = 0;
const check = (ok, what) => { if (!ok) bad++; console.log((ok ? "ok   " : "FAIL ") + what); };

const W = 300, H = 400, k = W / 600;                    // the detector works on a small picture
const quad = QUAD.map(([x, y]) => [x * k, y * k]);
const rgb = paperPhoto(W, H, quad);
const gray = new Uint8Array(W * H);
for (let i = 0; i < gray.length; i++) gray[i] = (rgb[i * 3] * 77 + rgb[i * 3 + 1] * 150 + rgb[i * 3 + 2] * 29) >> 8;

const found = detectQuad(gray, W, H);
check(!!found, "finds the paper");
if (found) {
  const err = Math.max(...found.map((p, i) => Math.hypot(p.x * W - quad[i][0], p.y * H - quad[i][1])));
  check(err <= 8, "corners are close to the real ones (a hair inside on purpose) (worst " + err.toFixed(1) + ")");
  check(quadOk(found), "the corners make a proper four-sided shape");

  const ow = 200, oh = 280;
  const rgba = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < W * H; i++) { rgba.set([rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2], 255], i * 4); }
  const out = warp(rgba, W, H, found.map((p) => ({ x: p.x * W, y: p.y * H })), ow, oh);
  let table = 0;
  for (let i = 0; i < ow * oh; i++) if (out[i * 4] < 100 && out[i * 4 + 1] < 90 && out[i * 4 + 2] < 85 && Math.abs(out[i * 4] - 70) < 12) table++;
  check(table < ow * oh * 0.01, "straightened page has almost no table in it (" + table + " of " + ow * oh + " px)");
  const corner = (x, y) => out[(y * ow + x) * 4];
  check([[3, 3], [ow - 4, 3], [ow - 4, oh - 4], [3, oh - 4]].every(([x, y]) => corner(x, y) > 200), "all four corners of the result are paper");
}
check(detectQuad(new Uint8Array(W * H).fill(120), W, H) === null, "a plain grey picture gives no page");
check(!quadOk([{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 1, y: 0 }, { x: 0, y: 1 }]), "a bow-tie shape is refused");
console.log(bad ? bad + " failed" : "all passed");
process.exit(bad ? 1 : 0);
