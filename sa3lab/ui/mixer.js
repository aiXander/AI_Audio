// SA3 UI kit: live mix monitoring in WebAudio, matched to the server's mix (sa3lab/mixdsp.py).
//
// The composer's reference WAV is mixed on the server; while mixing, the page plays the same mix
// live: each piece's cached audio (aligned + fixed + faded: /sa3/composer/piece) through its mix
// strip (BiquadFilterNodes → GainNode → StereoPannerNode), summed into the master (gain →
// limiter → meter). The AudioContext is pinned to 44.1 kHz and stripChain() is a port of
// mixdsp.strip_chain, so the biquad coefficients are the server's exactly. The limiter is a
// DynamicsCompressorNode stand-in for ffmpeg's alimiter: overs sound slightly different (the
// page says so); below the ceiling the monitor matches the reference.
//
//   const mx = new Mixer()
//   mx.setPieces([{key, url, at, dur, mix}])  mx.setMaster({gain, limit, ceiling})
//   await mx.play(t0)  mx.stop()  mx.time()  mx.playing  mx.peak()  (dBFS of the last ~50 ms)
//   mx.response(mix, freqs) → Float32Array dB (the strip's curve, from the browser's own nodes)
// Changing a piece's mix while playing updates the running nodes in place when the filter list
// keeps its shape, else restarts from the current position.
//
// Rewrite preview (mx.setPreview({keys, length}) | null): fakes what a render will do, so you hear
// it before rendering. keys = the render's strength curve [[t, u]] (0 keep … 1 regenerate; gaps,
// seams and drawn regions). While playing, the pieces' sum is crossfaded equal-power into soft pink
// noise by u (dry cos(uπ/2), noise sin(uπ/2)): kept audio is untouched, a soft region gets a veil of
// hiss, generated time is hiss only. It mirrors diffusion (the reference plus noise in proportion to
// strength); it does not predict what SA3 will put there. Automation is scheduled once per play().

const BUTTER4 = [0.5411961, 1.306563];
const BUTTER2 = 0.7071068;
const qdb = (q) => 20 * Math.log10(q);

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

export class Mixer {
  constructor() {
    this.ctx = null;
    this.bufs = new Map();
    this.pieces = [];
    this.masterCfg = {};
    this.voices = [];
    this.playing = false;
    this.t0 = 0;
    this.base = 0;
  }
  ensure() {
    if (this.ctx) return this.ctx;
    const ctx = (this.ctx = new AudioContext({ sampleRate: 44100 }));
    this.mGain = ctx.createGain();
    this.lim = ctx.createDynamicsCompressor();
    this.lim.knee.value = 0; this.lim.ratio.value = 20; this.lim.attack.value = 0.003; this.lim.release.value = 0.05;
    this.an = ctx.createAnalyser();
    this.an.fftSize = 2048;
    this.mGain.connect(this.lim); this.lim.connect(this.an); this.an.connect(ctx.destination);
    this.dry = ctx.createGain();             // the pieces' sum; ducked by the rewrite preview
    this.dry.connect(this.mGain);
    this.nGain = ctx.createGain();
    this.nGain.gain.value = 0;
    this.nGain.connect(this.mGain);
    this.applyMaster();
    return ctx;
  }
  async load(url) {
    if (!this.bufs.has(url)) {
      const p = fetch(url).then((r) => { if (!r.ok) throw new Error("piece audio " + r.status); return r.arrayBuffer(); })
        .then((b) => this.ensure().decodeAudioData(b));
      p.catch(() => this.bufs.delete(url));
      this.bufs.set(url, p);
    }
    return this.bufs.get(url);
  }
  setPieces(pieces) {
    const shape = (ps) => ps.map((p) => p.key + "|" + p.url + "|" + p.at + "|" + stripChain(p.mix).map((b) => b.type).join(",")).join(";");
    const restart = this.playing && shape(pieces) !== shape(this.pieces);
    this.pieces = pieces;
    if (restart) return this.play(this.time());
    if (this.playing) for (const v of this.voices) { const p = pieces.find((x) => x.key === v.key); if (p) this.applyStrip(v, p.mix); }
  }
  setPreview(p) { this.preview = p && p.keys && p.keys.length ? p : null; }
  noiseBuf() {                               // 4 s of pink noise (Kellet's filter) through two one-pole lowpasses at ~1.8 kHz (soft, no hiss), RMS ≈ 0.1, looped
    if (this._noise) return this._noise;
    const ctx = this.ensure(), n = 4 * 44100, b = ctx.createBuffer(2, n, 44100);
    for (let c = 0; c < 2; c++) {
      const d = b.getChannelData(c);
      let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, ss = 0, l1 = 0, l2 = 0;
      const lp = Math.exp(-2 * Math.PI * 1800 / 44100);
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
  schedulePreview(start, t0, t1) {
    const pv = this.preview, ctx = this.ctx;
    this.dry.gain.cancelScheduledValues(0); this.nGain.gain.cancelScheduledValues(0);
    if (!pv) { this.dry.gain.value = 1; this.nGain.gain.value = 0; return; }
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
    this.dry.gain.setValueAtTime(dry[0], ctx.currentTime);
    this.nGain.gain.setValueAtTime(nz[0], ctx.currentTime);
    this.dry.gain.setValueCurveAtTime(dry, start, t1 - t0);
    this.nGain.gain.setValueCurveAtTime(nz, start, t1 - t0);
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuf(); src.loop = true;
    src.connect(this.nGain);
    src.start(start, (t0 * 1.37) % 4);
    this.voices.push({ key: "_noise", src, bq: [] });
  }
  setMaster(m) { this.masterCfg = m || {}; if (this.ctx) this.applyMaster(); }
  applyMaster() {
    const m = this.masterCfg;
    this.mGain.gain.value = Math.pow(10, (+m.gain || 0) / 20);
    const lim = m.limit !== false;
    this.lim.threshold.value = lim ? +(m.ceiling ?? -1) : 0;
    this.lim.ratio.value = lim ? 20 : 1;
  }
  applyStrip(v, mix) {
    const ch = stripChain(mix);
    ch.forEach((b, i) => { const n = v.bq[i]; if (!n) return; n.frequency.value = b.f; n.Q.value = b.q; n.gain.value = b.g; });
    v.gain.gain.value = Math.pow(10, (+(mix || {}).gain || 0) / 20);
    v.pan.pan.value = Math.max(-1, Math.min(1, +(mix || {}).pan || 0));
  }
  async play(t0 = 0, { onEnd = null } = {}) {
    this.stop();
    const ctx = this.ensure();
    if (ctx.state === "suspended") await ctx.resume();
    const ps = this.pieces.filter((p) => p.at + p.dur > t0);
    const bufs = await Promise.all(ps.map((p) => this.load(p.url).catch(() => null)));
    this.stop();
    const start = ctx.currentTime + 0.06;
    this.t0 = t0; this.base = start; this.playing = true;
    let last = t0;
    ps.forEach((p, i) => {
      const b = bufs[i];
      if (!b) return;
      const src = ctx.createBufferSource();
      src.buffer = b;
      const bq = stripChain(p.mix).map((x) => { const n = ctx.createBiquadFilter(); n.type = x.type; return n; });
      const gain = ctx.createGain(), pan = ctx.createStereoPanner();
      let node = src;
      for (const n of bq) { node.connect(n); node = n; }
      node.connect(gain); gain.connect(pan); pan.connect(this.dry);
      const v = { key: p.key, src, bq, gain, pan };
      this.applyStrip(v, p.mix);
      const off = Math.max(0, t0 - p.at);
      src.start(start + Math.max(0, p.at - t0), off);
      last = Math.max(last, p.at + b.duration);
      this.voices.push(v);
    });
    if (this.preview) last = Math.max(last, this.preview.length || 0);
    if (last > t0) this.schedulePreview(start, t0, last);
    this.end = last;
    this._onEnd = onEnd;
    clearTimeout(this._endT);
    this._endT = setTimeout(() => { if (this.playing && this.time() >= this.end - 0.02) { this.stop(); if (onEnd) onEnd(); } }, (last - t0) * 1000 + 150);
  }
  stop() {
    for (const v of this.voices) { try { v.src.stop(); } catch (e) { /* not started */ } v.src.disconnect(); }
    this.voices = [];
    this.playing = false;
    clearTimeout(this._endT);
    if (this.ctx) { this.dry.gain.cancelScheduledValues(0); this.nGain.gain.cancelScheduledValues(0); this.dry.gain.value = 1; this.nGain.gain.value = 0; }
  }
  time() { return this.playing && this.ctx ? this.t0 + Math.max(0, this.ctx.currentTime - this.base) : null; }
  peak() {
    if (!this.an) return -Infinity;
    const a = new Float32Array(this.an.fftSize);
    this.an.getFloatTimeDomainData(a);
    let m = 0;
    for (const x of a) m = Math.max(m, Math.abs(x));
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
