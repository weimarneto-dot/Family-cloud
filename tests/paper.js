// A made-up photo of a sheet of paper lying slanted on a dark table, for the scanner tests.
// The paper is white with dark "text" lines; table is brown-grey. Returns RGB bytes (w*h*3).
function inQuad(q, x, y) {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4];
    const cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
    if (cross === 0) continue;
    if (sign === 0) sign = Math.sign(cross); else if (Math.sign(cross) !== sign) return false;
  }
  return true;
}
function paperPhoto(w, h, quad) {
  const rgb = Buffer.alloc(w * h * 3);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let c = [70, 58, 50];
    if (inQuad(quad, x, y)) c = (y % 40 < 6 && x % 90 > 12) ? [40, 40, 50] : [235, 232, 225];
    rgb.set(c, (y * w + x) * 3);
  }
  return rgb;
}
module.exports = { paperPhoto, QUAD: [[110, 90], [520, 130], [490, 700], [70, 640]] };     // TL, TR, BR, BL in a 600x800 photo
