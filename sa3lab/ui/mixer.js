// SA3 UI kit: live mix monitoring in WebAudio, matched to the server's mix (sa3lab/mixdsp.py).
//
// The composer's reference WAV is mixed on the server; while mixing, the page plays the same mix
// live: each piece's cached audio (aligned + fixed + faded: /sa3/composer/piece) through its mix
// strip (BiquadFilterNodes → GainNode → StereoPannerNode → volume-automation GainNode), summed into
// the master (gain → limiter → meter). The AudioContext is pinned to 44.1 kHz and stripChain() is a
// port of mixdsp.strip_chain, so the biquad coefficients are the server's exactly.
//
// Limiter: an AudioWorklet (LIMITER below, loaded from a Blob, no file to serve) standing in for
// ffmpeg's `alimiter=attack=5:release=50:level=0`: stereo-linked, 5 ms lookahead (sliding-min of the
// gain each sample needs, box-smoothed over the lookahead, so the output never passes the ceiling),
// linear release back to unity in 50 ms like alimiter's. No makeup gain (a DynamicsCompressorNode,
// the old stand-in, adds +0.57 dB per dB of ceiling and is not brick-wall), so below the ceiling the
// monitor is bit-for-bit the reference's sum; on overs it is close, not sample-identical.
//
//   const mx = new Mixer()
//   mx.setPieces([{key, url, at, dur, mix, env, envSig, envBreaks}])  mx.setMaster({gain, limit, ceiling})
// env (optional): (t) => linear gain at timeline time t, a volume automation (the Composer's track
// envelope) on the voice's own GainNode; envBreaks: the curve's key times (jumps land on the exact
// sample, constant stretches cost one event, the rest is sampled every 1 ms); envSig: a string that
// changes when the curve does (a change while playing is rescheduled in place, with a 10 ms glide).
//   await mx.play(t0)  mx.restart()  mx.stop()  mx.playing  mx.peak()  (dBFS of the last ~50 ms)
//   mx.time(): the timeline time you HEAR (output latency + lookahead taken off: Bluetooth headphones
//   lag 150-300 ms, so the playhead would run ahead); mx.pos(): the time being scheduled (restarts).
//   mx.response(mix, freqs) → Float32Array dB (the strip's curve, from the browser's own nodes)
// Pieces' audio is fetched + decoded as soon as setPieces names it (▶ starts at once; a small LRU
// keeps the last few replaced versions for undo). Changing a piece's mix while playing glides the
// running nodes (10 ms, no zipper noise) when the filter list keeps its shape, else restarts from the
// current position: every start / stop / restart is an 8 ms crossfade (no clicks), and a restart
// continues sample-exactly from where the old graph is, so it is seamless when only automation moved.
//
// Rewrite preview (mx.setPreview({keys, length}) | null): fakes what a render will do, so you hear
// it before rendering. keys = the render's strength curve [[t, u]] (0 keep … 1 regenerate; gaps,
// seams and drawn regions). While playing, the pieces' sum is crossfaded equal-power into soft pink
// noise by u (dry cos(uπ/2), noise sin(uπ/2)): kept audio is untouched, a soft region gets a veil of
// hiss, generated time is hiss only. It mirrors diffusion (the reference plus noise in proportion to
// strength); it does not predict what SA3 will put there. Automation is scheduled once per play().

const SR = 44100;
const BUTTER4 = [0.5411961, 1.306563];
const BUTTER2 = 0.7071068;
const qdb = (q) => 20 * Math.log10(q);
const LOOK = 220 / SR;     // limiter lookahead (alimiter attack=5 ms), also its constant latency
const LEAD = 0.03;         // scheduling headroom: a start lands on the audio thread's next quanta
const XF = 0.008;          // declick crossfade on every start / stop / restart
const GLIDE = 0.01;        // in-place parameter changes (strip, master, automation) glide this long
const PRE = 0.05;          // a restart's new graph runs this long silent first, so its filters have settled at the crossfade
const KEEP = 6;            // decoded buffers kept beyond the current pieces (undo, A/B)

const LIMITER = `
const N0 = Math.round(${LOOK} * sampleRate);
registerProcessor("sa3-limiter", class extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [{ name: "ceiling", defaultValue: 0.8913, minValue: 1e-4, maxValue: 8, automationRate: "k-rate" },
            { name: "on", defaultValue: 1, minValue: 0, maxValue: 1, automationRate: "k-rate" }];
  }
  constructor() {
    super();
    const L = (this.L = N0);
    this.N = L + 1;
    this.d0 = new Float32Array(L); this.d1 = new Float32Array(L); this.di = 0;   // the lookahead delay
    this.a = new Float64Array(this.N).fill(1); this.ai = 0; this.mn = 1; this.age = 0;   // sliding min of the needed gain
    this.box = new Float64Array(L).fill(1); this.bi = 0;                         // box smoothing = the attack ramp
    this.r = 1; this.rs = 0;                                                     // released gain, its slope
    this.rel = 0.05 * sampleRate;
  }
  process(ins, outs, prm) {
    const inp = ins[0] || [], out = outs[0], n = out[0].length;
    const i0 = inp.length ? inp[0] : null, i1 = inp.length > 1 ? inp[1] : i0;
    const o0 = out[0], o1 = out[1] || out[0];
    const c = prm.ceiling[0], on = prm.on[0] >= 0.5;
    const { L, N, d0, d1, a, box } = this;
    let { di, ai, mn, age, bi, r, rs } = this;
    let sum = 0;
    for (let k = 0; k < L; k++) sum += box[k];                                   // re-summed per block: no drift
    for (let i = 0; i < n; i++) {
      const xl = i0 ? i0[i] : 0, xr = i1 ? i1[i] : 0;
      const pk = Math.max(Math.abs(xl), Math.abs(xr));
      const tg = on && pk > c ? c / pk : 1;
      ai = ai + 1 === N ? 0 : ai + 1; a[ai] = tg;
      if (tg <= mn) { mn = tg; age = 0; }
      else if (++age > L) {                                                      // the min left the window: rescan
        mn = 1; age = 0;
        for (let k = 0; k < N; k++) { const j = (ai - k + N) % N; if (a[j] < mn) { mn = a[j]; age = k; } }
      }
      if (mn < r) { r = mn; rs = (1 - mn) / this.rel; }                         // alimiter: unity again in 50 ms
      else if (r < mn) r = Math.min(mn, r + rs);
      sum += r - box[bi]; box[bi] = r; bi = bi + 1 === L ? 0 : bi + 1;
      const g = Math.min(1, sum / L);
      let yl = d0[di] * g, yr = d1[di] * g;
      d0[di] = xl; d1[di] = xr; di = di + 1 === L ? 0 : di + 1;
      if (on) { yl = yl > c ? c : yl < -c ? -c : yl; yr = yr > c ? c : yr < -c ? -c : yr; }
      o0[i] = yl; o1[i] = yr;
    }
    Object.assign(this, { di, ai, mn, age, bi, r, rs });
    return true;
  }
});`;

export function stripChain(mix) {
  const m = mix || {}, out = [];
  for (const [key, type] of [["hp", "highpass"], ["lp", "lowpass"]]) {
    const x = m[key];
    if (!x || !x.f) continue;
    for (const q of (+x.slope === 24 ? BUTTER4 : [BUTTER2])) out.push({ type, f: +x.f, q: qdb(q), g: 0 });
  }
  for (const [key, type] of [["low", "lowshelf"], ["mid", "peaking"], ["high", "highshelf"]]) {
    const x = m[key];
    if (!x || !x.f || Math.abs(+x.g || 0) < 1e-3) continue;
    out.push({ type, f: +x.f, q: type === "peaking" ? +(x.q || 1) : 1, g: +x.g });
  }
  return out;
}

// A volume automation over timeline [a, b] as events in timeline time: one setValue per constant
// stretch, a 1 kHz curve per moving one, a jump exactly at its key time.
function envPlan(p, a, b) {
  if (!p.env) return [{ t: a, v: 1 }];
  const env = (t) => Math.max(0, p.env(t));
  const xs = [a, ...(p.envBreaks || []).filter((x) => x > a + 1e-4 && x < b - 1e-4), b].sort((x, y) => x - y);
  const out = [];
  for (let i = 0; i + 1 < xs.length; i++) {
    const x = xs[i], y = xs[i + 1], d = y - x;
    if (d < 1e-6) continue;
    const v0 = env(x), v1 = env(y - 1e-6), vm = env(x + d / 2);
    if (d < 4 / SR || (Math.abs(v0 - v1) < 1e-7 && Math.abs(v0 - vm) < 1e-7)) { out.push({ t: x, v: v0 }); continue; }
    const n = Math.max(2, Math.ceil(d * 1000) + 1), c = new Float32Array(n), span = d - 1e-6;
    for (let k = 0; k < n; k++) c[k] = env(x + (k / (n - 1)) * span);
    out.push({ t: x, c, d: d - 2 / SR });       // ends just before the next event: no overlap
  }
  return out;
}
function applyPlan(prm, plan, ct) {
  for (const e of plan) {
    if (e.c) prm.setValueCurveAtTime(e.c, ct(e.t), e.d);
    else prm.setValueAtTime(e.v, ct(e.t));
  }
}

export class Mixer {
  constructor() {
    this.ctx = null;
    this.bufs = new Map();     // url → Promise<AudioBuffer>, LRU order
    this.pieces = [];
    this.masterCfg = {};
    this.sess = null;          // the graph now playing: {t0, base, fade, dry, nz, voices, end, cont}
    this.playing = false;
    this.tok = 0;
    this.look = 0;
    this._lat = { at: -1e9, v: 0 };
    this._pk = null;
  }
  ensure() {
    if (this.ctx) return this.ctx;
    const ctx = (this.ctx = new AudioContext({ sampleRate: SR, latencyHint: "interactive" }));
    this.mGain = ctx.createGain();
    this.an = ctx.createAnalyser();
    this.an.fftSize = 2048;
    this.mGain.connect(this.an); this.an.connect(ctx.destination);
    const url = URL.createObjectURL(new Blob([LIMITER], { type: "application/javascript" }));
    this.ready = ctx.audioWorklet.addModule(url).then(() => {
      this.lim = new AudioWorkletNode(ctx, "sa3-limiter", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2],
        channelCount: 2, channelCountMode: "explicit", channelInterpretation: "speakers" });
      this.mGain.disconnect(); this.mGain.connect(this.lim); this.lim.connect(this.an);
      this.look = LOOK;
      this.applyMaster(false);
    }).catch((e) => console.warn("mixer: limiter worklet unavailable, monitoring unlimited", e))
      .finally(() => URL.revokeObjectURL(url));
    this.applyMaster(false);
    return ctx;
  }
  async load(url) {
    let p = this.bufs.get(url);
    if (p) { this.bufs.delete(url); this.bufs.set(url, p); return p; }   // LRU touch
    p = fetch(url).then((r) => { if (!r.ok) throw new Error("piece audio " + r.status); return r.arrayBuffer(); })
      .then((b) => this.ensure().decodeAudioData(b));
    p.catch(() => this.bufs.delete(url));
    this.bufs.set(url, p);
    return p;
  }
  prefetch() {                               // decode what the pieces name now; drop all but KEEP others
    const want = new Set(this.pieces.map((p) => p.url));
    for (const u of want) this.load(u).catch(() => {});
    let extra = this.bufs.size - want.size - KEEP;
    for (const u of this.bufs.keys()) { if (extra <= 0) break; if (!want.has(u)) { this.bufs.delete(u); extra--; } }
  }
  setPieces(pieces) {
    const shape = (ps) => ps.map((p) => p.key + "|" + p.url + "|" + p.at + "|" + stripChain(p.mix).map((b) => b.type).join(",")).join(";");
    const restart = this.playing && shape(pieces) !== shape(this.pieces);
    this.pieces = pieces;
    this.prefetch();
    if (!this.playing || !this.sess) return;
    if (restart) return this.restart();
    for (const v of this.sess.voices) {
      const p = pieces.find((x) => x.key === v.key);
      if (!p) continue;
      this.applyStrip(v, p.mix, true);
      if ((p.envSig || "") !== (v.p.envSig || "") && !this.reEnv(v, p)) return this.restart();
      v.p = p;
    }
  }
  reEnv(v, p) {                              // a new volume curve on a running voice: glide into it from now
    const ctx = this.ctx, s = this.sess, g = v.eg.gain;
    if (!g.cancelAndHoldAtTime) return false;
    const T = ctx.currentTime + 0.01, tl = s.t0 + (T - s.base);
    if (tl >= v.ve) return true;
    const a = Math.max(tl + GLIDE, v.vs), ct = (t) => s.base + (t - s.t0);
    try {
      g.cancelAndHoldAtTime(T);
      g.setValueAtTime(v.p.env ? Math.max(0, v.p.env(Math.max(tl, v.vs))) : 1, T);   // pin it: a hold alone inserts nothing on an unautomated param
      g.linearRampToValueAtTime(p.env ? Math.max(0, p.env(a)) : 1, ct(a));
      applyPlan(g, envPlan(p, a + 2 / SR, v.ve), ct);
      return true;
    } catch (e) { return false; }
  }
  setPreview(p) { this.preview = p && p.keys && p.keys.length ? p : null; }
  noiseBuf() {                               // 4 s of pink noise (Kellet's filter) through two one-pole lowpasses at ~1.8 kHz (soft, no hiss), RMS ≈ 0.1, looped
    if (this._noise) return this._noise;
    const ctx = this.ensure(), n = 4 * SR, b = ctx.createBuffer(2, n, SR);
    for (let c = 0; c < 2; c++) {
      const d = b.getChannelData(c);
      let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, ss = 0, l1 = 0, l2 = 0;
      const lp = Math.exp(-2 * Math.PI * 1800 / SR);
      for (let i = 0; i < n; i++) {
        const w = Math.random() * 2 - 1;
        b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759; b2 = 0.969 * b2 + w * 0.153852;
        b3 = 0.8665 * b3 + w * 0.3104856; b4 = 0.55 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.016898;
        const pk = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362; b6 = w * 0.115926;
        l1 = lp * l1 + (1 - lp) * pk; l2 = lp * l2 + (1 - lp) * l1;
        d[i] = l2; ss += d[i] * d[i];
      }
      const k = 0.1 / Math.sqrt(ss / n);
      for (let i = 0; i < n; i++) d[i] *= k;
    }
    return (this._noise = b);
  }
  previewCurves(t0, t1) {                    // the rewrite preview's dry / noise gains at 50 Hz, or null
    const pv = this.preview;
    if (!pv || t1 <= t0) return null;
    const at = (t) => {                       // compose._sample's rule: linear between keys, held at the ends
      const k = pv.keys;
      let i = 0;
      while (i < k.length && k[i][0] <= t) i++;
      if (i === 0) return k[0][1];
      if (i >= k.length) return k[k.length - 1][1];
      const a = k[i - 1], b = k[i];
      return b[0] > a[0] ? a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]) : b[1];
    };
    const n = Math.max(2, Math.ceil((t1 - t0) * 50)), dry = new Float32Array(n), nz = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const u = Math.max(0, Math.min(1, at(t0 + (i / (n - 1)) * (t1 - t0))));
      dry[i] = Math.cos(u * Math.PI / 2); nz[i] = 0.3 * Math.sin(u * Math.PI / 2);   // noise bed peaks ~6 dB under the old 0.6
    }
    return { dry, nz };
  }
  setMaster(m) { this.masterCfg = m || {}; if (this.ctx) this.applyMaster(true); }
  applyMaster(smooth) {
    const m = this.masterCfg, ctx = this.ctx;
    const set = (prm, x) => (smooth ? prm.setTargetAtTime(x, ctx.currentTime, GLIDE / 3) : (prm.value = x));
    set(this.mGain.gain, Math.pow(10, (+m.gain || 0) / 20));
    if (this.lim) {
      this.lim.parameters.get("ceiling").value = Math.pow(10, +(m.ceiling ?? -1) / 20);
      this.lim.parameters.get("on").value = m.limit !== false ? 1 : 0;
    }
  }
  applyStrip(v, mix, smooth = false) {
    const sig = JSON.stringify(mix || {});
    if (smooth && sig === v.mixSig) return;
    v.mixSig = sig;
    const now = this.ctx.currentTime;
    const set = (prm, x) => (smooth ? prm.setTargetAtTime(x, now, GLIDE / 3) : (prm.value = x));
    stripChain(mix).forEach((b, i) => { const n = v.bq[i]; if (!n) return; set(n.frequency, b.f); set(n.Q, b.q); set(n.gain, b.g); });
    set(v.gain.gain, Math.pow(10, (+(mix || {}).gain || 0) / 20));
    set(v.pan.pan, Math.max(-1, Math.min(1, +(mix || {}).pan || 0)));
  }
  restart() { return this.playing ? this.play(null, { onEnd: this._onEnd }) : Promise.resolve(); }
  // t0 = null: continue from wherever the running graph is at the moment the new one takes over
  async play(t0 = 0, { onEnd = null } = {}) {
    const tok = ++this.tok;
    const ctx = this.ensure();
    if (ctx.state === "suspended") await ctx.resume();
    await this.ready;
    const cont = t0 == null && !!this.sess;
    if (t0 == null) t0 = this.pos() ?? 0;    // refined below once the buffers are in
    const ps = this.pieces.filter((p) => p.at + p.dur > t0 - 0.5);
    const bufs = await Promise.all(ps.map((p) => this.load(p.url).catch(() => null)));
    if (tok !== this.tok) return;            // a newer play() / stop() took over meanwhile
    let start = null;
    if (cont && this.sess) {                 // where the old graph will be when the new one takes over
      start = ctx.currentTime + LEAD + 0.02 + PRE;
      t0 = this.sess.t0 + (start - this.sess.base);
    }

    // build every node + automation plan first, then fix the start time: no voice can start late
    const fade = ctx.createGain(), dry = ctx.createGain();
    dry.connect(fade); fade.connect(this.mGain);
    const voices = [];
    let last = t0;
    ps.forEach((p, i) => {
      const b = bufs[i];
      if (!b || p.at + b.duration <= t0) return;
      const src = ctx.createBufferSource();
      src.buffer = b;
      const bq = stripChain(p.mix).map((x) => { const n = ctx.createBiquadFilter(); n.type = x.type; return n; });
      const gain = ctx.createGain(), pan = ctx.createStereoPanner(), eg = ctx.createGain();
      let node = src;
      for (const n of bq) { node.connect(n); node = n; }
      node.connect(gain); gain.connect(pan); pan.connect(eg); eg.connect(dry);
      const vs = Math.max(t0, p.at), ve = p.at + b.duration;
      const v = { key: p.key, p, src, bq, gain, pan, eg, vs, ve, plan: p.env ? envPlan(p, vs, ve) : null };
      this.applyStrip(v, p.mix);
      last = Math.max(last, ve);
      voices.push(v);
    });
    if (this.preview) last = Math.max(last, this.preview.length || 0);
    const pc = this.previewCurves(t0, last);
    const noise = pc ? this.noiseBuf() : null;

    if (start == null || ctx.currentTime > start - PRE - 0.005) start = ctx.currentTime + LEAD + (cont ? PRE : 0);   // (a slow build: a few ms repeat, never misaligned voices)
    const ct = (t) => start + (t - t0);
    const mid = cont || ps.some((p) => p.at < t0 - 1e-4);   // starting inside audio: fade in, else the clip's own fade stands
    const s = { t0, base: start, fade, dry, voices, end: last, cont: !!cont, mid };
    for (const v of voices) {
      if (v.plan) applyPlan(v.eg.gain, v.plan, ct);
      const pre = cont && v.vs <= t0 ? Math.min(PRE, t0 - v.p.at) : 0;   // pre-roll (silent: the fade is 0 until start)
      v.src.start(ct(v.vs) - pre, Math.max(0, t0 - v.p.at - pre));
    }
    if (pc) {
      const nz = ctx.createGain(), src = ctx.createBufferSource();
      nz.connect(fade);
      dry.gain.setValueAtTime(pc.dry[0], ctx.currentTime); dry.gain.setValueCurveAtTime(pc.dry, start, last - t0);
      nz.gain.setValueAtTime(pc.nz[0], ctx.currentTime); nz.gain.setValueCurveAtTime(pc.nz, start, last - t0);
      src.buffer = noise; src.loop = true; src.connect(nz);
      src.start(start, (t0 * 1.37) % 4);
      voices.push({ key: "_noise", src, bq: [] });
    }
    if (mid) { fade.gain.value = 0; fade.gain.setValueAtTime(0, start); fade.gain.linearRampToValueAtTime(1, start + XF); }
    if (this.sess) this.retire(this.sess, start);           // the old graph crossfades out as this one comes in
    this.sess = s;
    this.playing = true;
    this._onEnd = onEnd;
    this.armEnd(s);
  }
  armEnd(s) {
    clearTimeout(this._endT);
    const left = s.end - s.t0 + (s.base - this.ctx.currentTime) + this.latency();
    this._endT = setTimeout(() => {
      if (this.sess !== s || !this.playing) return;
      if (this.pos() < s.end - 0.02) return this.armEnd(s);
      this.stop();
      if (this._onEnd) this._onEnd();
    }, Math.max(0, left) * 1000 + 60);
  }
  retire(s, at) {                            // fade a graph out from `at` over XF, then free it
    const g = s.fade.gain, v = s.mid ? Math.max(0, Math.min(1, (at - s.base) / XF)) : 1;   // the fade's value at `at`
    if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(at); else g.cancelScheduledValues(at);
    g.setValueAtTime(v, at);                 // pinned: the ramp below starts at `at`, not at the last event
    g.linearRampToValueAtTime(0, at + XF);
    for (const v of s.voices) { try { v.src.stop(at + XF + 0.002); } catch (e) { /* never started */ } }
    setTimeout(() => { for (const v of s.voices) v.src.disconnect(); s.fade.disconnect(); }, (at - this.ctx.currentTime + XF) * 1000 + 100);
  }
  stop() {
    this.tok++;
    clearTimeout(this._endT);
    if (this.sess && this.ctx) this.retire(this.sess, this.ctx.currentTime + 0.002);
    this.sess = null;
    this.playing = false;
  }
  pos() {                                    // the timeline time being scheduled now
    const s = this.sess;
    return this.playing && s && this.ctx ? s.t0 + Math.max(0, this.ctx.currentTime - s.base) : null;
  }
  latency() {                                // seconds from scheduled to heard (output device + lookahead), refreshed each second
    const ctx = this.ctx;
    if (!ctx) return 0;
    const now = performance.now();
    if (now - this._lat.at > 1000) {
      let l = (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
      if (ctx.getOutputTimestamp) {
        const ts = ctx.getOutputTimestamp();
        if (ts.contextTime > 0 && ts.performanceTime > 0) {
          const d = ctx.currentTime - (ts.contextTime + (now - ts.performanceTime) / 1000);
          if (d > 0 && d < 1) l = Math.max(l, d);
        }
      }
      this._lat = { at: now, v: l };
    }
    return this._lat.v + this.look;
  }
  time() {                                   // the timeline time you hear now
    const s = this.sess;
    if (!this.playing || !s || !this.ctx) return null;
    const t = s.t0 + (this.ctx.currentTime - s.base) - this.latency();
    return s.cont ? t : Math.max(s.t0, t);   // a continuation: the old graph is still what you hear
  }
  peak() {
    if (!this.an) return -Infinity;
    const a = this._pk || (this._pk = new Float32Array(this.an.fftSize));
    this.an.getFloatTimeDomainData(a);
    let m = 0;
    for (let i = 0; i < a.length; i++) { const x = Math.abs(a[i]); if (x > m) m = x; }
    return 20 * Math.log10(m + 1e-9);
  }
  response(mix, freqs) {
    const ctx = this.ensure();
    const f = Float32Array.from(freqs), out = new Float32Array(freqs.length).fill(0);
    const mag = new Float32Array(freqs.length), ph = new Float32Array(freqs.length);
    for (const b of stripChain(mix)) {
      const n = ctx.createBiquadFilter();
      n.type = b.type; n.frequency.value = b.f; n.Q.value = b.q; n.gain.value = b.g;
      n.getFrequencyResponse(f, mag, ph);
      for (let i = 0; i < out.length; i++) out[i] += 20 * Math.log10(mag[i] + 1e-12);
    }
    const g = +(mix || {}).gain || 0;
    for (let i = 0; i < out.length; i++) out[i] += g;
    return out;
  }
}
