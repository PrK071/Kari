"""Gera alguns backgrounds preset em backend/static/backgrounds/."""
import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "backend" / "static" / "backgrounds"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def diagonal_gradient(c1, c2, w=W, h=H):
    ys, xs = np.mgrid[0:h, 0:w]
    t = (xs / w + ys / h) / 2.0
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(3):
        img[..., i] = (c1[i] + (c2[i] - c1[i]) * t).astype(np.uint8)
    return Image.fromarray(img, "RGB")


def radial_gradient(inner, outer, w=W, h=H):
    ys, xs = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    t = np.clip(d / d.max(), 0, 1)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(3):
        img[..., i] = (inner[i] + (outer[i] - inner[i]) * t).astype(np.uint8)
    return Image.fromarray(img, "RGB")


# --- estaticos ---
diagonal_gradient(np.array([12, 30, 24]), np.array([98, 214, 154])).save(OUT / "aurora.jpg", quality=92)
diagonal_gradient(np.array([20, 12, 40]), np.array([150, 110, 240])).save(OUT / "nebula.jpg", quality=92)
radial_gradient(np.array([26, 34, 40]), np.array([6, 9, 12])).save(OUT / "spotlight.png")
print("static ok")

# --- gif animado (gradiente que desliza) ---
frames = []
gw, gh = 640, 360
for k in range(24):
    phase = k / 24.0
    ys, xs = np.mgrid[0:gh, 0:gw]
    t = (np.sin((xs / gw + phase) * 2 * math.pi) + 1) / 2
    img = np.zeros((gh, gw, 3), dtype=np.uint8)
    c1 = np.array([10, 26, 22]); c2 = np.array([98, 214, 154])
    for i in range(3):
        img[..., i] = (c1[i] + (c2[i] - c1[i]) * t).astype(np.uint8)
    frames.append(Image.fromarray(img, "RGB"))
frames[0].save(OUT / "wave.gif", save_all=True, append_images=frames[1:], duration=80, loop=0, optimize=True)
print("gif ok")

# --- mp4 (tenta H.264, cai pra mp4v) ---
try:
    import cv2
    mw, mh = 1280, 720
    for fourcc_name in ("avc1", "H264", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
        path = OUT / "flow.mp4"
        vw = cv2.VideoWriter(str(path), fourcc, 24.0, (mw, mh))
        if not vw.isOpened():
            vw.release(); continue
        for k in range(72):
            phase = k / 72.0
            ys, xs = np.mgrid[0:mh, 0:mw]
            t = (np.sin((xs / mw * 2 + ys / mh + phase * 2) * math.pi) + 1) / 2
            img = np.zeros((mh, mw, 3), dtype=np.uint8)
            c1 = np.array([40, 12, 60]); c2 = np.array([150, 110, 240])
            for i in range(3):
                img[..., i] = (c1[i] + (c2[i] - c1[i]) * t).astype(np.uint8)
            vw.write(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        vw.release()
        size = path.stat().st_size if path.exists() else 0
        print(f"mp4 fourcc={fourcc_name} size={size}")
        if size > 1000:
            break
except Exception as exc:
    print("mp4 falhou:", exc)

print("FILES:", sorted(p.name + f"({p.stat().st_size})" for p in OUT.iterdir()))
