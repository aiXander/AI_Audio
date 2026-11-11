"""Stack spectrogram PNGs vertically with labels (for an agent to Read).
    uv run python montage.py out.png a.png b.png ...   (label = file stem)"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out, files = sys.argv[1], sys.argv[2:]
W = 1400
ims = [Image.open(f).convert("RGB") for f in files]
ims = [im.resize((W, int(im.height * W / im.width))) for im in ims]
H = sum(im.height + 22 for im in ims)
M = Image.new("RGB", (W, H), (40, 40, 40))
d = ImageDraw.Draw(M)
y = 0
for f, im in zip(files, ims):
    d.text((6, y + 5), Path(f).name, fill=(255, 255, 255))
    M.paste(im, (0, y + 22))
    y += im.height + 22
M.save(out)
