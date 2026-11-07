// SA3 UI kit: the strength-curve rules (segments.py parse_plan / frame_strength / plan_end), shared by
// every editor of a strength curve: keys [[t, u], …] sorted by t, linear between, flat outside, two
// keys at one t = a hard edge (list order = the value before the jump first). Pure functions,
// ported unchanged from segments.js (the studio's original editor) so both editors write identical keys.
export const SPL = 4096 / 44100;                     // one latent frame, seconds
export const r3 = (v) => Math.round(v * 1000) / 1000;
export const r4 = (v) => Math.round(v * 10000) / 10000;
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const ease = (f) => (1 - Math.cos(Math.PI * f)) / 2;   // smooth interpolation: cosine, never past either key
export function at(K, t, smooth = false) {          // value at t, from the right (post-jump); smooth: eased between keys
  if (!K.length) return 0;
  const j = K.findIndex((k) => k[0] > t);
  if (j < 0) return K[K.length - 1][1];
  if (j === 0) return K[0][1];
  const a = K[j - 1], b = K[j], f = (t - a[0]) / (b[0] - a[0]);
  return a[1] + (smooth ? ease(f) : f) * (b[1] - a[1]);
}
export function before(L, t) {                      // value just before t (pre-jump)
  if (!L.length) return 0;
  let i = -1;
  for (let n = 0; n < L.length; n++) if (L[n][0] < t) i = n;
  if (i < 0) return L[0][1];
  const a = L[i], b = L[i + 1];
  if (!b) return a[1];
  return a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
}
export const after = at;
export function setRange(L, a, b, u) {              // [a, b] flat at u, hard edges, outside unchanged
  const fl = before(L, a), fr = after(L, b);
  return [...L.filter((k) => k[0] < a), [a, fl], [a, u], [b, u], [b, fr], ...L.filter((k) => k[0] > b)];
}
export function heal(L, a, b) {                     // drop [a, b]; the curve rejoins linearly
  const fl = before(L, a), fr = after(L, b);
  return [...L.filter((k) => k[0] < a), [a, fl], [b, fr], ...L.filter((k) => k[0] > b)];
}
// drop redundant keys; all-zero = no curve. keepZero: keep an all-zero curve (a volume at silence);
// prune: false keeps keys on a flat stretch (a volume curve: two points at one level start a ramp)
export function norm(L, { keepZero = false, prune = true } = {}) {
  L = L.map((k) => [r3(k[0]), r4(clamp(k[1], 0, 1))]);
  let out = [];
  for (const k of L) {
    const n = out.length;
    if (n >= 2 && out[n - 1][0] === k[0] && out[n - 2][0] === k[0]) out[n - 1] = k;
    else out.push(k);
  }
  out = out.filter((k, i) => !(i > 0 && k[0] === out[i - 1][0] && k[1] === out[i - 1][1]));
  let changed = prune;
  while (changed) {
    changed = false;
    for (let i = 0; i < out.length; i++) {
      const p = out[i - 1], k = out[i], n = out[i + 1];
      const flatL = !p || (p[1] === k[1] && p[0] !== k[0]), flatR = !n || (n[1] === k[1] && n[0] !== k[0]);
      if ((p || n) && flatL && flatR && !(p && p[0] === k[0]) && !(n && n[0] === k[0])) { out.splice(i, 1); changed = true; break; }
    }
  }
  return !keepZero && out.every((k) => k[1] <= 0) ? [] : out;
}
export function end(K) {                            // segments.py plan_end
  let e = 0, prev = 0;
  for (const [t, u] of K) { if (u > 0 || prev > 0) e = Math.max(e, t); prev = u; }
  return e;
}
export function boxes(K) {                          // flat stretches above 0: the draggable plateaus
  const out = [];
  for (let i = 0; i + 1 < K.length; i++) {
    const a = K[i], b = K[i + 1];
    if (a[1] === b[1] && a[1] > 0 && b[0] - a[0] > 1e-4) out.push({ i, a: a[0], b: b[0], u: a[1] });
  }
  return out;
}
export function spansToKeys(spans) {
  let L = [];
  for (const s of spans.slice().sort((x, y) => x[0] - y[0])) L = setRange(L, +s[0], +s[1], 1);
  return norm(L);
}
export function ucol(u) {                           // teal (soft) → violet → pink (regenerate)
  const st = [[62, 230, 196], [155, 140, 255], [255, 111, 181]];
  const x = clamp(u, 0, 1) * 2, i = Math.min(1, Math.floor(x)), f = x - i;
  return `rgb(${st[i].map((v, j) => Math.round(v + (st[i + 1][j] - v) * f)).join(",")})`;
}
