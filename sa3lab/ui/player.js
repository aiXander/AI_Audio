// SA3 UI kit: one-at-a-time playback (the studio's _sa3All rule: starting one source stops the
// rest). One shared <audio> element; a range [t0, t1] stops (or loops) on a rAF check, ~16 ms.
//
//   play({key, url, t0, t1, loop, onTime})   start `url` at t0; onTime(t) every frame while playing
//   stop()  ·  toggle(opts)  ·  isPlaying(key)  ·  subscribe(cb)  (cb({key, playing}) on changes)

const audio = new Audio();
audio.preload = "auto";
let cur = null;
const subs = new Set();
const emit = () => subs.forEach((cb) => cb({ key: cur && cur.key, playing: !!cur }));

function tick() {
  if (!cur) return;
  const t = audio.currentTime;
  if (cur.t1 != null && t >= cur.t1) {
    if (cur.loop) audio.currentTime = cur.t0;
    else return stop();
  }
  if (cur.onTime) cur.onTime(audio.currentTime);
  requestAnimationFrame(tick);
}

export async function play({ key, url, t0 = 0, t1 = null, loop = false, onTime = null }) {
  stop();
  cur = { key, url, t0, t1, loop, onTime };
  if (audio.dataset.url !== url) { audio.src = url; audio.dataset.url = url; }
  try {
    if (audio.readyState < 1) await new Promise((res, rej) => { audio.onloadedmetadata = res; audio.onerror = rej; });
    audio.currentTime = t0;
    await audio.play();
  } catch (e) {
    cur = null; emit();
    throw e;
  }
  emit();
  requestAnimationFrame(tick);
}

export function stop() {
  if (!cur) return;
  const c = cur;
  cur = null;
  audio.pause();
  if (c.onTime) c.onTime(null);
  emit();
}

export function toggle(opts) {
  if (cur && cur.key === opts.key) return stop();
  return play(opts);
}

export const isPlaying = (key) => !!cur && (key == null || cur.key === key);
export const now = () => (cur ? audio.currentTime : null);
export function subscribe(cb) { subs.add(cb); return () => subs.delete(cb); }
audio.addEventListener("ended", () => { if (cur && cur.loop) play(cur); else stop(); });
