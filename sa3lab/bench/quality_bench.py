"""SA3 quality-trick benchmark — generation half (runs in the MLX venv).

Renders a fixed prompt × config matrix on Medium + SAME-L with one fixed seed
per prompt, so every config of a prompt starts from the same initial noise and
differs only in the trick under test. Every latent is decoded twice: stock
SAME-L decoder and with the `squeakfix_v3` decoder LoRA merged in, so the
decoder trick is a pure A/B on identical latents.

Tricks under test (from the SA3 research doc's idea backlog + ecosystem doc):
  merge33   medium-base : medium (ARC) at 1:2 weight average (worstplayer recipe)
  cfg3      CFG 3 + APG 1.0 at 16 steps (the merge author's sampler settings)
  squeakfix thepatch/same-l-decoder-lora squeakfix_v3, decoder-only LoRA
  long120   render 120 s instead of 30 s (paper: quality peaks at 120-190 s)
  tagprompt AudioSparx tag-style prompt instead of a descriptive sentence
Plus a re-encode chain (decode → encode → decode, 3 round trips) on the
baseline latents with each decoder, where squeakfix claims its biggest win.

Run:  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/bench/quality_bench.py
Scoring is the second half: sa3lab/bench/score_bench.py (own uv env, torch).
"""
from __future__ import annotations

import gc, json, math, os, sys, time
from pathlib import Path

import numpy as np
import mlx.core as mx

AI_AUDIO = Path(__file__).resolve().parents[2]
# All SA3-related downloads live in AI_Audio/models/ (one folder to delete to reclaim disk).
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
MLX_ROOT = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
sys.path.insert(0, str(MLX_ROOT)); sys.path.insert(0, str(MLX_ROOT / "scripts"))
os.chdir(MLX_ROOT)  # weight paths in the upstream helpers are relative to here

from models.defs.sa3_pipeline import (apply_prompt_padding, build_pingpong_schedule,
                                      load_conditioner_from_npz, patched_decode,
                                      sample_flow_pingpong)
from models.defs.t5gemma_mlx import T5Gemma
from models.defs import dit_mlx_medium, same_l_decoder, same_l_encoder
from sa3_mlx import patch_audio, save_wav

SR, SPL = 44100, 4096
OUT = AI_AUDIO / "outputs" / "sa3_bench_quality"
ARC = MLX_ROOT / "models/mlx/dit_medium_f16.npz"
BASE = MLX_ROOT / "models/mlx/dit_medium-base_f16.npz"
MERGE = AI_AUDIO / "models/derived/dit_medium-merge33_f16.npz"
DEC_W = MLX_ROOT / "models/mlx/same_l_decoder_f32.npz"
ENC_W = MLX_ROOT / "models/mlx/same_l_encoder_f32.npz"

PROMPTS = [
    dict(id="jungle", seed=1234,
         text="Energetic jungle drum and bass track with fast chopped amen breaks, crisp hi-hats, "
              "deep rolling sub bass and atmospheric pads, 170 BPM",
         tags="TrackType: Music, Genre: Drum & Bass, Jungle, Instruments: Drums, Bass, Synth Pad, "
              "Moods: Energetic, BPM: 170"),
    dict(id="jazztrio", seed=2024,
         text="Intimate acoustic jazz piano trio recorded live: warm grand piano, upright bass and "
              "brushed drums with ride cymbal, relaxed swing, 120 BPM",
         tags="TrackType: Music, Genre: Jazz, Instruments: Piano, Upright Bass, Drums, "
              "Moods: Relaxed, Warm, BPM: 120"),
    dict(id="cinematic", seed=777,
         text="Epic cinematic orchestral score with soaring strings, French horns, timpani rolls "
              "and shimmering choir, building tension, 90 BPM",
         tags="TrackType: Music, Genre: Cinematic, Orchestral, Instruments: Strings, French Horn, "
              "Timpani, Choir, Moods: Epic, BPM: 90"),
]

# name: (dit, seconds, steps, cfg, prompt_field)
CONFIGS = {
    "A_baseline":      ("arc",     30,  8, 1.0, "text"),
    "B_cfg3":          ("arc",     30, 16, 3.0, "text"),
    "C_merge33":       ("merge33", 30,  8, 1.0, "text"),
    "D_merge33_cfg3":  ("merge33", 30, 16, 3.0, "text"),
    "E_long120":       ("arc",    120,  8, 1.0, "text"),
    "F_tagprompt":     ("arc",     30,  8, 1.0, "tags"),
}
APG = 1.0
ROUNDTRIPS = 3


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def build_merge():
    if MERGE.exists():
        return
    log("building merge: 1/3 medium-base + 2/3 medium (ARC)")
    a, b = dict(mx.load(str(ARC))), dict(mx.load(str(BASE)))
    only_a, only_b = set(a) - set(b), set(b) - set(a)
    log(f"  keys arc={len(a)} base={len(b)} arc-only={len(only_a)} base-only={len(only_b)}")
    out = {}
    for k, v in a.items():
        if k in b and b[k].shape == v.shape:
            out[k] = (v.astype(mx.float32) * (2 / 3) + b[k].astype(mx.float32) * (1 / 3)).astype(v.dtype)
        else:
            out[k] = v
    MERGE.parent.mkdir(parents=True, exist_ok=True)
    mx.savez(str(MERGE), **out)
    del a, b, out; gc.collect()


def squeakfix_deltas():
    from huggingface_hub import hf_hub_download
    path = hf_hub_download("thepatch/same-l-decoder-lora", "squeakfix_v3.safetensors")
    w, meta = mx.load(path, return_metadata=True)
    cfg = json.loads(meta["lora_config"])
    scale = cfg["alpha"] / cfg["rank"]
    deltas = {}
    for k in w:
        if not k.endswith("lora_A"):
            continue
        mod = k[: -len(".parametrizations.weight.0.lora_A")]
        A, B = w[k].astype(mx.float32), w[k[:-1] + "B"].astype(mx.float32)
        if mod == "layers.1":
            tgt = "project_in.weight"
        else:
            i, rest = mod.split(".")[3], ".".join(mod.split(".")[4:])
            tgt = {"self_attn.to_qkv": "attn.to_qkv", "self_attn.to_out": "attn.to_out",
                   "ff.ff.0.proj": "ff.glu_proj", "ff.ff.2": "ff.proj_out"}[rest]
            tgt = f"blocks.{i}.{tgt}.weight"
        deltas[tgt] = scale * (B @ A)
    return deltas


def load_decoders():
    stock = same_l_decoder.load_model(weights_path=str(DEC_W), dtype=mx.float32)
    fixed = same_l_decoder.load_model(weights_path=str(DEC_W), dtype=mx.float32)
    params = dict(_flatten(fixed.parameters()))
    deltas = squeakfix_deltas()
    upd = []
    for k, d in deltas.items():
        assert params[k].shape == d.shape, (k, params[k].shape, d.shape)
        upd.append((k, params[k] + d))
    fixed.load_weights(upd, strict=False); mx.eval(fixed.parameters())
    log(f"squeakfix merged into {len(upd)} decoder weights")
    return {"stock": stock, "squeakfix": fixed}


def _flatten(tree, prefix=""):
    if isinstance(tree, dict):
        for k, v in tree.items():
            yield from _flatten(v, f"{prefix}{k}.")
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            yield from _flatten(v, f"{prefix}{i}.")
    else:
        yield prefix[:-1], tree


def decode(dec, lat):
    T = lat.shape[-1]
    patches = same_l_decoder.decode_chunked(dec, lat.astype(mx.float32), 128, 8) if T > 144 else dec(lat.astype(mx.float32))
    audio = patched_decode(patches, 256, 2); mx.eval(audio)
    return np.array(audio)[0]  # (2, T*4096)


def encode(enc, audio):
    lat = enc(mx.array(patch_audio(audio[None].astype(np.float32), 256))); mx.eval(lat)
    return lat


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    have_base = BASE.exists() or MERGE.exists()
    if have_base:
        build_merge()
    else:
        log("medium-base not on disk yet: skipping merge configs this run (re-run later; finished latents are reused)")
    dtype = mx.float16

    log("T5Gemma: encoding prompts")
    t5 = T5Gemma.from_npz(str(MLX_ROOT / "models/mlx/t5gemma_f16.npz"))
    emb = {}
    for p in PROMPTS:
        for f in ("text", "tags"):
            e, m = t5.encode([p[f]], max_len=256); mx.eval(e, m)
            emb[(p["id"], f)] = (e.astype(dtype), m)
    del t5; gc.collect(); mx.clear_cache()

    latents = {}
    dit_paths = {"arc": ARC, "merge33": MERGE} if have_base else {"arc": ARC}
    for cname, c in CONFIGS.items():  # reuse latents from earlier runs
        for p in PROMPTS:
            f = OUT / f"{p['id']}__{cname}.latent.npy"
            if f.exists():
                latents[(p["id"], cname)] = mx.array(np.load(f))
    for dit_name, path in dit_paths.items():
        todo = [(c, p) for c, v in CONFIGS.items() if v[0] == dit_name for p in PROMPTS
                if (p["id"], c) not in latents]
        if not todo:
            continue
        pad_emb, secs = load_conditioner_from_npz(str(path), prefix="cond.")
        for secs_len in sorted({c[1] for c in CONFIGS.values() if c[0] == dit_name}):
            T_lat = math.ceil(secs_len * SR / SPL)
            log(f"loading DiT {dit_name} (T_lat={T_lat})")
            model = dit_mlx_medium.load_dit(str(path), T_lat=T_lat, dtype=dtype)
            for cname, (dn, s, steps, cfg, field) in CONFIGS.items():
                if dn != dit_name or s != secs_len:
                    continue
                for p in PROMPTS:
                    if (p["id"], cname) in latents:
                        continue
                    e, m = emb[(p["id"], field)]
                    sec_e = secs(float(s)).astype(dtype)
                    cross = mx.concatenate([apply_prompt_padding(e, m, pad_emb.astype(dtype)), sec_e], axis=1)
                    gcond = sec_e[:, 0, :]
                    null_cross = mx.zeros_like(cross)

                    def model_fn(x, t, cross=cross, gcond=gcond, null_cross=null_cross, cfg=cfg):
                        if cfg == 1.0:
                            return model(x, t, cross, gcond)
                        v = model(mx.concatenate([x, x]), mx.concatenate([t, t]),
                                  mx.concatenate([cross, null_cross]), mx.concatenate([gcond, gcond]))
                        cv, uv = mx.split(v, 2, axis=0)
                        sig = t.reshape(-1, 1, 1).astype(mx.float32)
                        xf = x.astype(mx.float32)
                        cd, ud = xf - cv.astype(mx.float32) * sig, xf - uv.astype(mx.float32) * sig
                        diff = cd - ud
                        unit = cd / mx.maximum(mx.sqrt((cd * cd).sum(axis=(-2, -1), keepdims=True)), 1e-8)
                        orth = diff - (diff * unit).sum(axis=(-2, -1), keepdims=True) * unit
                        diff = APG * orth + (1 - APG) * diff
                        return ((xf - (cd + (cfg - 1) * diff)) / sig).astype(x.dtype)

                    noise = mx.random.normal((1, 256, T_lat), dtype=dtype, key=mx.random.key(p["seed"]))
                    sigmas = build_pingpong_schedule(steps, sigma_max=1.0, use_logsnr_shift=True)
                    t0 = time.time()
                    lat = sample_flow_pingpong(model_fn, noise, sigmas, seed=p["seed"] + 1)
                    mx.eval(lat)
                    latents[(p["id"], cname)] = lat
                    np.save(OUT / f"{p['id']}__{cname}.latent.npy", np.array(lat.astype(mx.float32)))
                    log(f"  {p['id']:<10} {cname:<16} {time.time()-t0:5.1f}s")
            del model; gc.collect(); mx.clear_cache()

    log("decoding (stock + squeakfix)")
    decs = load_decoders()
    files = []
    for (pid, cname), lat in latents.items():
        secs_len = CONFIGS[cname][1]
        for dname, dec in decs.items():
            audio = decode(dec, lat)[:, : secs_len * SR]
            fn = OUT / f"sa3_bench_{pid}__{cname}__{dname}.wav"
            save_wav(str(fn), audio)
            files.append(dict(file=fn.name, prompt=pid, config=cname, decoder=dname, roundtrips=0))

    log(f"re-encode chains ({ROUNDTRIPS} round trips) on baseline latents")
    enc = same_l_encoder.load_model(weights_path=str(ENC_W), dtype=mx.float32)
    for p in PROMPTS:
        for dname, dec in decs.items():
            audio = decode(dec, latents[(p["id"], "A_baseline")])
            for d in range(1, ROUNDTRIPS + 1):
                audio = decode(dec, encode(enc, audio))
            fn = OUT / f"sa3_bench_{p['id']}__A_baseline_rt{ROUNDTRIPS}__{dname}.wav"
            save_wav(str(fn), audio[:, : 30 * SR])
            files.append(dict(file=fn.name, prompt=p["id"], config=f"A_baseline_rt{ROUNDTRIPS}",
                              decoder=dname, roundtrips=ROUNDTRIPS))

    manifest = dict(prompts=PROMPTS, configs={k: dict(zip(["dit", "seconds", "steps", "cfg", "prompt_field"], v))
                                              for k, v in CONFIGS.items()}, apg=APG, files=files)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    log(f"done: {len(files)} wavs in {OUT}")


if __name__ == "__main__":
    main()
