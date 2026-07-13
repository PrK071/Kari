from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as curl_requests

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    PlaywrightError = RuntimeError
    sync_playwright = None


REAL_HOST = "yomumangas.com"
TYPED_HOST = "yumomangas.com"
IMAGE_PATH_MARKER = "/chapters/"
DEFAULT_OUTPUT = Path("downloads/yomu")
BRAVE_PATHS = (
    Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
    Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe",
)


@dataclass(frozen=True)
class DownloadedImage:
    source_index: int
    url: str
    content: bytes
    content_type: str
    digest: str


def normalize_chapter_url(raw_url: str) -> str:
    url = unescape(raw_url.strip()).replace(TYPED_HOST, REAL_HOST)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != REAL_HOST:
        raise ValueError(f"URL deve pertencer a {REAL_HOST}.")
    if not re.match(r"^/mangas/\d+/[^/]+/[^/]+/?$", parsed.path):
        raise ValueError("URL nao parece um capitulo: /mangas/ID/slug/capitulo")
    return parsed._replace(fragment="").geturl()


def normalize_image_url(raw_url: str, chapter_url: str) -> str:
    url = urljoin(chapter_url, unescape(str(raw_url or "")).strip())
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not (host == REAL_HOST or host.endswith(f".{REAL_HOST}")):
        return ""
    if IMAGE_PATH_MARKER not in parsed.path:
        return ""
    return parsed._replace(fragment="").geturl()


def dedupe_urls(urls: list[str], chapter_url: str) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = normalize_image_url(raw_url, chapter_url)
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def is_cloudflare_challenge(page) -> bool:
    title = (page.title() or "").casefold()
    return "just a moment" in title or "um momento" in title


def wait_for_access(page, timeout_seconds: int, headless: bool) -> None:
    if not is_cloudflare_challenge(page):
        return
    if headless:
        raise RuntimeError(
            "Cloudflare pediu verificacao. Rode sem --headless e conclua no navegador."
        )

    print("Cloudflare ativo. Conclua a verificacao na janela do navegador.")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        if not is_cloudflare_challenge(page):
            return
    raise TimeoutError("Cloudflare nao foi concluido dentro do tempo limite.")


def launch_browser_context(playwright, profile: Path, headless: bool):
    common = {
        "user_data_dir": str(profile),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
    }
    candidates: list[dict] = []
    for path in BRAVE_PATHS:
        if path.exists():
            candidates.append({"executable_path": str(path)})
    candidates.extend(({"channel": "chrome"}, {"channel": "msedge"}, {}))

    errors: list[str] = []
    for browser_options in candidates:
        try:
            return playwright.chromium.launch_persistent_context(
                **browser_options,
                **common,
            )
        except PlaywrightError as exc:
            errors.append(str(exc).splitlines()[0])
    raise RuntimeError("Nenhum Chromium compativel abriu: " + " | ".join(errors))


def dom_image_urls(page) -> list[str]:
    return page.evaluate(
        r"""() => {
            const result = [];
            for (const image of document.querySelectorAll("img")) {
                const values = [
                    image.currentSrc,
                    image.src,
                    image.getAttribute("data-src"),
                    image.getAttribute("data-lazy-src"),
                    image.getAttribute("data-original"),
                    image.getAttribute("srcset"),
                    image.getAttribute("data-srcset"),
                ];
                for (const value of values) {
                    if (!value) continue;
                    for (const part of String(value).split(",")) {
                        const candidate = part.trim().split(/\s+/)[0];
                        if (candidate.includes("/chapters/")) {
                            result.push(new URL(candidate, location.href).href);
                            break;
                        }
                    }
                }
            }
            return result;
        }"""
    )


def hydrate_and_collect(page, network_urls: list[str], max_passes: int = 5) -> list[str]:
    previous_count = -1
    stable_passes = 0

    for _ in range(max_passes):
        metrics = page.evaluate(
            """() => ({
                height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
                viewport: window.innerHeight || 720,
            })"""
        )
        step = max(500, int(metrics["viewport"] * 0.8))
        for y in range(0, int(metrics["height"]) + step, step):
            page.evaluate("y => window.scrollTo(0, y)", y)
            page.wait_for_timeout(70)
        page.wait_for_timeout(500)

        current_urls = dom_image_urls(page)
        current_count = len(current_urls)
        stable_passes = stable_passes + 1 if current_count == previous_count else 0
        previous_count = current_count
        if current_count > 0 and stable_passes >= 1:
            page.evaluate("window.scrollTo(0, 0)")
            return [*current_urls, *network_urls]

    page.evaluate("window.scrollTo(0, 0)")
    return [*dom_image_urls(page), *network_urls]


def collect_image_urls(
    chapter_url: str,
    profile: Path,
    headless: bool,
    challenge_timeout: int,
) -> list[str]:
    if sync_playwright is None:
        raise RuntimeError("Playwright ausente. Instale: pip install playwright")

    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, profile, headless)
        page = context.pages[0] if context.pages else context.new_page()
        network_urls: list[str] = []

        def record_response(response) -> None:
            if IMAGE_PATH_MARKER in response.url:
                network_urls.append(response.url)

        page.on("response", record_response)
        page.goto(chapter_url, wait_until="domcontentloaded", timeout=60_000)
        wait_for_access(page, challenge_timeout, headless)
        page.wait_for_timeout(800)
        urls = hydrate_and_collect(page, network_urls)
        context.close()
    return dedupe_urls(urls, chapter_url)


def image_extension(url: str, content_type: str, content: bytes) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".avif", ".gif", ".jpg", ".jpeg", ".png", ".webp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    mime_extensions = {
        "image/avif": ".avif",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    mime = content_type.lower().split(";", 1)[0]
    if mime in mime_extensions:
        return mime_extensions[mime]
    if content.startswith(b"\x00\x00\x00") and b"ftypavif" in content[:32]:
        return ".avif"
    return ".bin"


async def fetch_image(
    session: curl_requests.AsyncSession,
    semaphore: asyncio.Semaphore,
    source_index: int,
    url: str,
    referer: str,
) -> DownloadedImage:
    async with semaphore:
        response = await session.get(
            url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8",
                "Referer": referer,
            },
            timeout=30,
        )
        response.raise_for_status()
        content = bytes(response.content)
        content_type = response.headers.get("content-type", "")
        if not content:
            raise RuntimeError(f"Resposta vazia para {url}")
        if (
            not content_type.lower().startswith("image/")
            and image_extension(url, content_type, content) == ".bin"
        ):
            raise RuntimeError(f"Resposta invalida para {url}")
        return DownloadedImage(
            source_index=source_index,
            url=url,
            content=content,
            content_type=content_type,
            digest=hashlib.sha256(content).hexdigest(),
        )


async def download_images(
    urls: list[str],
    chapter_url: str,
    concurrency: int,
) -> list[DownloadedImage]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    indexed_urls = list(enumerate(urls))
    images_by_index: list[DownloadedImage | None] = [None] * len(indexed_urls)

    async def fetch_indexed(source_index: int, url: str) -> None:
        images_by_index[source_index] = await fetch_image(
            session,
            semaphore,
            source_index,
            url,
            chapter_url,
        )

    async with curl_requests.AsyncSession(impersonate="firefox") as session:
        tasks = [
            fetch_indexed(index, url)
            for index, url in indexed_urls
        ]
        await asyncio.gather(*tasks)

    missing = [index for index, image in enumerate(images_by_index) if image is None]
    if missing:
        raise RuntimeError(f"Downloads incompletos nos indices: {missing}")
    return [image for image in images_by_index if image is not None]


def chapter_output(root: Path, chapter_url: str) -> Path:
    parts = [part for part in urlparse(chapter_url).path.split("/") if part]
    name = "-".join(parts[-3:])
    target = root / re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-._")
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_unique_images(
    images: list[DownloadedImage],
    chapter_url: str,
    output_root: Path,
) -> list[dict]:
    target = chapter_output(output_root, chapter_url)
    seen_hashes: set[str] = set()
    manifest: list[dict] = []

    for image in images:
        if image.digest in seen_hashes:
            continue
        seen_hashes.add(image.digest)
        page_number = len(manifest) + 1
        extension = image_extension(image.url, image.content_type, image.content)
        filename = f"pagina_{page_number:03d}{extension}"
        (target / filename).write_bytes(image.content)
        manifest.append(
            {
                "page": page_number,
                "source_index": image.source_index,
                "filename": filename,
                "url": image.url,
                "sha256": image.digest,
                "bytes": len(image.content),
                "content_type": image.content_type,
            }
        )
        print(f"[{page_number:03d}] {filename} ({len(image.content):,} bytes)")

    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK: {len(manifest)} paginas unicas em {target}")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extrai e baixa paginas de capitulos do Yomu Mangas."
    )
    parser.add_argument("chapter_url", help="URL /mangas/ID/slug/capitulo")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--profile", default=".yomu-browser-profile")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--challenge-timeout", type=int, default=180)
    parser.add_argument("--headless", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        chapter_url = normalize_chapter_url(args.chapter_url)
        urls = collect_image_urls(
            chapter_url,
            Path(args.profile).resolve(),
            args.headless,
            args.challenge_timeout,
        )
        if not urls:
            raise RuntimeError("Nenhuma pagina encontrada no DOM ou Network.")
        print(f"Encontradas: {len(urls)} URLs unicas")
        images = asyncio.run(download_images(urls, chapter_url, args.concurrency))
        manifest = save_unique_images(images, chapter_url, Path(args.output))
        return 0 if manifest else 1
    except KeyboardInterrupt:
        print("Interrompido.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
