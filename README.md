# AI_Audio

Creative hacking on open AI audio models: style transfer, latent morphing, codec surgery, LoRA finetunes. Current focus is **Stable Audio 3** and its SAME-L codec.

| Folder | What |
|---|---|
| `stable-audio-3/` | submodule: fork of [Stability-AI/stable-audio-3](https://github.com/Stability-AI/stable-audio-3) |
| `underfit/` | submodule: fork of [dada-bots/underfit](https://github.com/dada-bots/underfit), SA3 LoRA training dashboard |
| `sa3lab/` | our own SA3 tools: a re-skinned studio web UI and quality benchmarks |

Weights, datasets and renders are not in git (`models/`, `audio-data/`, `outputs/`).

## Clone

```bash
git clone --recurse-submodules https://github.com/aiXander/AI_Audio.git
cd AI_Audio
git submodule foreach 'git checkout main'   # submodules clone detached; put them on a branch before committing
```

## Sync between machines

```bash
# after editing code inside a submodule
cd stable-audio-3 && git commit -am "..." && git push && cd ..
git commit -am "Bump stable-audio-3" && git push

# on the other machine (keeps each submodule on its main branch, ready to edit)
git pull && git submodule foreach 'git checkout main && git pull'
```

Pull upstream updates into a fork: `cd stable-audio-3 && git fetch upstream && git merge upstream/main`.
