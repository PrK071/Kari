"""Baixa alguns backgrounds gratuitos (Pexels, licenca livre) pra static/backgrounds/."""
import re
import sys
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parents[1] / "backend" / "static" / "backgrounds"
OUT.mkdir(parents=True, exist_ok=True)

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# Paginas Pexels (licenca Pexels: uso livre). Extraimos o CDN mp4.
VIDEO_PAGES = [
    ("gradient", "https://www.pexels.com/video/purple-and-blue-abstract-4990247/"),
    ("particles", "https://www.pexels.com/video/an-abstract-background-of-glowing-particles-3129671/"),
    ("waves", "https://www.pexels.com/video/digital-animation-of-a-blue-wave-3163534/"),
]

PHOTO_CDN = [
    ("mountains", "https://images.pexels.com/photos/1287145/pexels-photo-1287145.jpeg?auto=compress&cs=tinysrgb&w=1920"),
    ("sky", "https://images.pexels.com/photos/531756/pexels-photo-531756.jpeg?auto=compress&cs=tinysrgb&w=1920"),
]


def pick_smallest_mp4(html: str) -> str:
    links = sorted(set(re.findall(r"https://videos\.pexels\.com/video-files/[^\"'\\ ]+\.mp4", html)))
    # prefere resolucoes menores (hd/sd) pra nao pesar
    def score(u):
        m = re.search(r"_(\d+)_(\d+)_", u)
        return int(m.group(1)) * int(m.group(2)) if m else 10 ** 12
    links.sort(key=score)
    # pega o menor >= 1280 de largura se possivel, senao o menor
    for u in links:
        m = re.search(r"_(\d+)_(\d+)_", u)
        if m and int(m.group(1)) >= 1280:
            return u
    return links[0] if links else ""


def download(url: str, dest: Path, referer: str = "") -> int:
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=40, stream=True)
    if r.status_code != 200:
        print(f"  ! {dest.name}: HTTP {r.status_code}")
        return 0
    size = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
            size += len(chunk)
    print(f"  + {dest.name}: {size} bytes")
    return size


print("== videos ==")
for name, page in VIDEO_PAGES:
    try:
        r = requests.get(page, headers=UA, timeout=25)
        if r.status_code != 200:
            print(f"  page {name}: HTTP {r.status_code}")
            continue
        mp4 = pick_smallest_mp4(r.text)
        if not mp4:
            print(f"  page {name}: sem mp4 encontrado")
            continue
        download(mp4, OUT / f"web_{name}.mp4", referer=page)
    except Exception as exc:
        print(f"  page {name}: ERRO {exc}")

print("== imagens ==")
for name, url in PHOTO_CDN:
    try:
        download(url, OUT / f"web_{name}.jpg")
    except Exception as exc:
        print(f"  {name}: ERRO {exc}")

print("FILES:", sorted(p.name for p in OUT.iterdir()))
