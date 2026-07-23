from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BASE_URL = "https://tensurafan.github.io"
SERIES_URL = f"{BASE_URL}/"
VOLUMES_URL = f"{BASE_URL}/ln/volumes.json"
TITLE = "That Time I Got Reincarnated as a Slime"


def _clean(value: object, limit: int = 20000) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()[:limit]


class TensuraFanPlugin:
    provider = "tensura_fan"
    source_label = "Tensura Fan"

    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=2, pool_connections=6, pool_maxsize=6)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": SERIES_URL,
                "User-Agent": "Mozilla/5.0 Kari/1.0",
            }
        )
        self._volumes: list[dict] | None = None

    @staticmethod
    def is_source(source_url: str) -> bool:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname != "tensurafan.github.io":
            return False
        return parsed.path.rstrip("/") in {"", "/read"} or bool(
            re.fullmatch(r"/(?:read|ln)/[a-z0-9._-]+(?:\.html)?/?", parsed.path, re.I)
        )

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str]:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname != "tensurafan.github.io":
            raise ValueError("Endereco Tensura Fan invalido.")
        path = parsed.path.rstrip("/")
        if path in {"", "/read"}:
            return "series", ""
        match = re.fullmatch(r"/(?:read|ln)/([a-z0-9._-]+?)(?:\.html)?", path, re.I)
        if not match:
            raise ValueError("Endereco Tensura Fan invalido.")
        return "chapter", match.group(1).lower()

    @staticmethod
    def chapter_url(volume_id: str) -> str:
        return f"{BASE_URL}/read/{volume_id}"

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=(5, 35))
        response.raise_for_status()
        return response

    def _available_volumes(self) -> list[dict]:
        if self._volumes is not None:
            return [dict(volume) for volume in self._volumes]

        payload = self._get(VOLUMES_URL).json()
        if not isinstance(payload, list):
            raise RuntimeError("Tensura Fan retornou um indice de volumes invalido.")

        volumes: list[dict] = []
        seen_paths: set[str] = set()
        for ordinal, raw in enumerate(payload, start=1):
            if not isinstance(raw, dict) or int(raw.get("progress") or 0) < 100:
                continue
            volume_id = _clean(raw.get("id"), 40).lower()
            path = _clean(raw.get("path"), 300)
            name = _clean(raw.get("name"), 200)
            if not volume_id or not path or not name or path in seen_paths:
                continue
            seen_paths.add(path)
            volumes.append(
                {
                    "id": volume_id,
                    "name": name,
                    "path": path,
                    "ordinal": ordinal,
                    "url": self.chapter_url(volume_id),
                }
            )
        if not volumes:
            raise RuntimeError("Tensura Fan nao publicou volumes completos.")
        volumes.sort(
            key=lambda volume: (
                0 if volume["id"].startswith("b") else 1,
                self._volume_number(volume["id"], int(volume["ordinal"])),
            )
        )
        for ordinal, volume in enumerate(volumes, start=1):
            volume["ordinal"] = ordinal
        self._volumes = volumes
        return [dict(volume) for volume in volumes]

    @staticmethod
    def _volume_number(volume_id: str, fallback: int) -> float:
        match = re.fullmatch(r"v(\d+)(?:[_\.](\d+))?", volume_id, re.I)
        if not match:
            return float(fallback)
        whole = float(match.group(1))
        decimal = match.group(2)
        return whole + (float(f"0.{decimal}") if decimal else 0.0)

    def catalog_items(self, query: str = "", limit: int = 24) -> list[dict]:
        wanted = _clean(query, 180).casefold()
        aliases = f"{TITLE} tensei shitara slime datta ken tensura slime"
        if wanted and wanted not in aliases.casefold():
            return []
        volumes = self._available_volumes()
        latest = next(
            (volume for volume in reversed(volumes) if volume["id"].startswith("v")),
            volumes[-1],
        )
        cover = f"{BASE_URL}/ln/sources/Volume 20/illustrations/cover.jpg"
        return [
            {
                "id": "tensura-fan:slime",
                "url": SERIES_URL,
                "source_url": SERIES_URL,
                "title": TITLE,
                "source": self.provider,
                "provider": self.provider,
                "language": "en",
                "available_translated_languages": ["en"],
                "poster": cover,
                "description": (
                    "Fan translation em ingles da light novel Tensei Shitara Slime Datta Ken, "
                    "com volumes, historias extras e ilustracoes."
                ),
                "genres": ["Light Novel", "Fantasy", "Isekai", "Adventure"],
                "authors": ["Fuse"],
                "status": "Em andamento",
                "latest_chapter": latest["name"],
                "chapter_count": len(volumes),
                "chapter_preview": [
                    volume["name"] for volume in reversed(volumes[-3:])
                ],
            }
        ][: max(1, min(int(limit), 24))]

    def manga_metadata(self, source_url: str) -> dict:
        if not self.is_source(source_url):
            raise ValueError("Endereco Tensura Fan invalido.")
        volumes = self._available_volumes()
        latest = next(
            (volume for volume in reversed(volumes) if volume["id"].startswith("v")),
            volumes[-1],
        )
        return {
            "slug": "slime",
            "url": SERIES_URL,
            "title": TITLE,
            "type": "Light Novel",
            "poster": f"{BASE_URL}/ln/sources/Volume 20/illustrations/cover.jpg",
            "description": (
                "Fan translation em ingles de That Time I Got Reincarnated as a Slime. "
                "A fonte inclui os volumes publicados, historias extras e ilustracoes."
            ),
            "latest_chapter": latest["name"],
            "chapter_count": len(volumes),
            "authors": ["Fuse"],
            "artists": ["Mitz Vah"],
            "genres": ["Light Novel", "Fantasy", "Isekai", "Adventure"],
            "status": "Em andamento",
            "rating": {},
            "alternative_titles": [
                "Tensei Shitara Slime Datta Ken",
                "Tensura",
            ],
            "languages": [
                {
                    "code": "en",
                    "title": "English",
                    "chapter_count": len(volumes),
                }
            ],
        }

    def list_chapters(self, source_url: str) -> list[dict]:
        if not self.is_source(source_url):
            raise ValueError("Endereco Tensura Fan invalido.")
        rows: list[dict] = []
        for fallback, volume in enumerate(self._available_volumes(), start=1):
            number = self._volume_number(volume["id"], fallback)
            rows.append(
                {
                    "id": volume["id"],
                    "number": number,
                    "number_text": str(number).rstrip("0").rstrip("."),
                    "label": volume["name"],
                    "title": volume["name"],
                    "url": volume["url"],
                }
            )
        return rows

    @staticmethod
    def _prepare_dynamic_names(soup: BeautifulSoup) -> None:
        for node in soup.select("[data-term]"):
            term = _clean(node.get("data-term"), 300)
            if term:
                node.clear()
                node.append(term)

    @staticmethod
    def _content_blocks(rendered: str) -> tuple[str, list[dict]]:
        soup = BeautifulSoup(rendered, "html.parser")
        TensuraFanPlugin._prepare_dynamic_names(soup)
        blocks: list[dict] = []
        text_parts: list[str] = []
        block_tags = {"p", "h1", "h2", "h3", "h4", "blockquote", "li"}
        for node in soup.find_all([*block_tags, "img"]):
            if node.name == "img":
                source = _clean(node.get("src"), 1000)
                if source:
                    blocks.append(
                        {
                            "type": "image",
                            "src": urljoin(BASE_URL, source),
                            "alt": _clean(node.get("alt"), 180) or "Ilustracao do volume",
                        }
                    )
                continue
            if node.find_parent(block_tags):
                continue
            text = _clean(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "text", "text": text})
                text_parts.append(text)
        return "\n\n".join(text_parts), blocks

    def get_chapter(self, source_url: str) -> dict:
        kind, volume_id = self.parse_source(source_url)
        if kind != "chapter":
            raise ValueError("Informe a URL de um volume Tensura Fan.")
        volume = next(
            (item for item in self._available_volumes() if item["id"] == volume_id),
            None,
        )
        if not volume:
            raise FileNotFoundError("Volume nao encontrado no Tensura Fan.")
        rendered = self._get(urljoin(BASE_URL, volume["path"])).text
        content, blocks = self._content_blocks(rendered)
        if not content:
            raise RuntimeError("Volume Tensura Fan sem texto publico.")
        return {
            "url": volume["url"],
            "source_url": SERIES_URL,
            "novel_title": TITLE,
            "chapter_title": volume["name"],
            "number_text": str(self._volume_number(volume_id, volume["ordinal"])).rstrip("0").rstrip("."),
            "content": content,
            "blocks": blocks,
            "previous": None,
            "next": None,
        }
