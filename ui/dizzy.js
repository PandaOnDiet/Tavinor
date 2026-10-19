// Spin the pointer in circles and Tavinor gets dizzy (like an Xbox 360
// avatar shaken too hard). Feed it pointer samples; it returns true once
// the pointer has turned `turns` full circles within `windowMs`.
//
// How: the angle of each point around the centre of the recent path,
// summed step by step. Circles add up; lines and back-and-forth wiggles
// cancel out or jump across the centre (steps too big to be a circle are
// ignored), so ordinary use never trips it.
function makeDizzy({ windowMs = 1600, turns = 2.5, minRadius = 35, maxStep = 1.5 } = {}) {
  const pts = [];
  return (x, y, t) => {
    pts.push({ x, y, t });
    while (pts.length && t - pts[0].t > windowMs) pts.shift();
    if (pts.length < 12) return false;
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p.x; cy += p.y; }
    cx /= pts.length; cy /= pts.length;
    let sum = 0, r = 0, prev = null;
    for (const p of pts) {
      const dist = Math.hypot(p.x - cx, p.y - cy);
      r += dist;
      if (dist < minRadius / 2) { prev = null; continue; }     // too near the centre to have an angle
      const a = Math.atan2(p.y - cy, p.x - cx);
      if (prev !== null) {
        let d = a - prev;
        if (d > Math.PI) d -= 2 * Math.PI;
        if (d < -Math.PI) d += 2 * Math.PI;
        if (Math.abs(d) <= maxStep) sum += d;
      }
      prev = a;
    }
    if (r / pts.length >= minRadius && Math.abs(sum) >= turns * 2 * Math.PI) {
      pts.length = 0;
      return true;
    }
    return false;
  };
}

module.exports = { makeDizzy };

// node dizzy.js: the self-check.
if (require.main === module) {
  const run = (path, ms = 1600, every = 33) => {
    const d = makeDizzy();
    let hit = false;
    for (let t = 0; t <= ms; t += every) { const [x, y] = path(t); hit = d(x, y, t) || hit; }
    return hit;
  };
  const circle = (r, perSec, dir = 1) => (t) => [500 + r * Math.cos(dir * 2 * Math.PI * perSec * t / 1000),
                                                 400 + r * Math.sin(dir * 2 * Math.PI * perSec * t / 1000)];
  const assert = require('assert');
  assert(run(circle(80, 2)), 'fast circles: dizzy');
  assert(run(circle(80, 2, -1)), 'fast circles the other way: dizzy');
  assert(!run(circle(80, 1)), 'slow circles (1.6 turns): not dizzy');
  assert(!run(circle(15, 3)), 'tiny jitter circles: not dizzy');
  assert(!run((t) => [100 + t, 300]), 'a straight drag: not dizzy');
  assert(!run((t) => [500 + 200 * Math.sin(t / 60), 400]), 'fast left-right shaking: not dizzy');
  assert(!run((t) => [500 + 150 * Math.sin(t / 70), 400 + 150 * Math.sin(t / 70)]), 'diagonal shaking: not dizzy');
  assert(!run((t) => [400 + (t % 400), 300 + 40 * ((t / 400) | 0)], 3000), 'reading-style sweeps: not dizzy');
  console.log('dizzy: 8 checks passed');
}
