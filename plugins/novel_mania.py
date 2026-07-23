from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BASE_URL = "https://novelmania.com.br"
ASSET_HOST = "assets.novelmania.com.br"


def _clean(value: object, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()[:limit]


def _decode_js_string(value: str) -> str:
    # TanStack serializa o HTML usando escapes JS (\x3C), que JSON nao aceita.
    normalized = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: f"\\u00{match.group(1)}",
        value,
    )
    return json.loads(normalized)


def _script_string(value: str) -> str:
    return _decode_js_string(value) if value else ""


class NovelManiaPlugin:
    provider = "novel_mania"
    source_label = "Novel Mania"

    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=2, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Referer": f"{BASE_URL}/",
                "User-Agent": "Mozilla/5.0 Kari/1.0",
            }
        )

    @staticmethod
    def is_source(source_url: str) -> bool:
        parsed = urlparse(str(source_url or ""))
        return parsed.hostname in {"novelmania.com.br", "www.novelmania.com.br"} and bool(
            re.fullmatch(r"/novels/[^/]+(?:/capitulos/[^/]+)?/?", parsed.path, re.I)
        )

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str, str | None]:
        parsed = urlparse(str(source_url or ""))
        match = re.fullmatch(
            r"/novels/([^/]+)(?:/capitulos/([^/]+))?/?",
            parsed.path,
            re.I,
        )
        if parsed.hostname not in {"novelmania.com.br", "www.novelmania.com.br"} or not match:
            raise ValueError("Endereco Novel Mania invalido.")
        novel_slug = unquote(match.group(1))
        chapter_slug = unquote(match.group(2)) if match.group(2) else None
        return ("chapter" if chapter_slug else "novel", novel_slug, chapter_slug)

    @staticmethod
    def novel_url(slug: str) -> str:
        return f"{BASE_URL}/novels/{slug}"

    @staticmethod
    def chapter_url(novel_slug: str, chapter_slug: str) -> str:
        return f"{BASE_URL}/novels/{novel_slug}/capitulos/{chapter_slug}"

    def _get(self, url: str, *, params: dict | None = None) -> requests.Response:
        response = self.session.get(url, params=params, timeout=(5, 25))
        response.raise_for_status()
        return response

    @staticmethod
    def _json_ld(soup: BeautifulSoup, wanted_type: str) -> dict:
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.get_text() or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            values = payload if isinstance(payload, list) else [payload]
            for value in values:
                if isinstance(value, dict) and value.get("@type") == wanted_type:
                    return value
        return {}

    @staticmethod
    def _chapter_rows(html: str, novel_slug: str) -> list[dict]:
        pattern = re.compile(
            r"longTitle:(\"(?:\\.|[^\"\\])*\"),position:([0-9.]+),"
            r"publishedAt:(?:null|\"(?:\\.|[^\"\\])*\"),slug:"
            r"(\"(?:\\.|[^\"\\])*\"),title:(\"(?:\\.|[^\"\\])*\")",
            re.I,
        )
        chapters: list[dict] = []
        seen: set[str] = set()
        for match in pattern.finditer(html):
            chapter_slug = _script_string(match.group(3))
            if not chapter_slug or chapter_slug in seen:
                continue
            seen.add(chapter_slug)
            position = float(match.group(2))
            number_text = str(int(position)) if position.is_integer() else str(position)
            long_title = _clean(_script_string(match.group(1)), 240)
            title = _clean(_script_string(match.group(4)), 300)
            display_title = re.sub(
                r"^(?:cap[ií]tulo|interl[uú]dio|pr[oó]logo|ep[ií]logo)\s*[\d.]*\s*[:\-–—]?\s*",
                "",
                title,
                flags=re.I,
            ).strip() or title
            chapters.append(
                {
                    "id": chapter_slug,
                    "number": position,
                    "number_text": number_text,
                    "title": display_title,
                    "label": long_title or title or f"Capitulo {number_text}",
                    "url": NovelManiaPlugin.chapter_url(novel_slug, chapter_slug),
                }
            )
        return sorted(chapters, key=lambda chapter: float(chapter["number"]))

    def catalog_items(self, query: str = "", limit: int = 24) -> list[dict]:
        params = {"q": _clean(query, 180)} if _clean(query, 180) else None
        soup = BeautifulSoup(self._get(f"{BASE_URL}/novels", params=params).text, "html.parser")
        items: list[dict] = []
        seen: set[str] = set()
        for link in soup.select("a[href^='/novels/']"):
            href = str(link.get("href") or "").split("?", 1)[0]
            match = re.fullmatch(r"/novels/([^/]+)/?", href, re.I)
            image = link.select_one("img[src]")
            if not match or not image:
                continue
            slug = unquote(match.group(1))
            if slug in seen:
                continue
            title = _clean(image.get("alt"), 240)
            cover = urljoin(BASE_URL, str(image.get("src") or ""))
            if not title or urlparse(cover).hostname != ASSET_HOST:
                continue
            seen.add(slug)
            type_label = _clean(link.get_text(" ", strip=True).replace(title, "", 1), 80)
            items.append(
                {
                    "id": f"novel-mania:{slug}",
                    "url": self.novel_url(slug),
                    "source_url": self.novel_url(slug),
                    "title": title,
                    "source": self.provider,
                    "provider": self.provider,
                    "language": "pt-br",
                    "available_translated_languages": ["pt-br"],
                    "poster": cover,
                    "description": "",
                    "genres": [type_label] if type_label else ["Web Novel"],
                    "authors": [],
                    "status": "",
                    "chapter_preview": ["Abrir lista de capitulos"],
                }
            )
            if len(items) >= max(1, min(limit, 24)):
                break
        return items

    def manga_metadata(self, source_url: str) -> dict:
        kind, slug, _ = self.parse_source(source_url)
        if kind != "novel":
            raise ValueError("Informe a URL da novel no Novel Mania.")
        html = self._get(self.novel_url(slug)).text
        soup = BeautifulSoup(html, "html.parser")
        book = self._json_ld(soup, "Book")
        title = _clean(book.get("name"), 240)
        if not title:
            title = _clean((soup.select_one('meta[property="og:title"]') or {}).get("content"), 240)
        if not title:
            raise RuntimeError("Novel Mania nao retornou o titulo da obra.")
        description = _clean(BeautifulSoup(str(book.get("description") or ""), "html.parser").get_text(" "))
        poster = str(book.get("image") or "").strip()
        if not poster:
            poster = str((soup.select_one('meta[property="og:image"]') or {}).get("content") or "").strip()
        authors_value = book.get("author") or []
        if isinstance(authors_value, dict):
            authors_value = [authors_value]
        authors = [
            _clean(author.get("name") if isinstance(author, dict) else author, 160)
            for author in authors_value
        ]
        authors = [author for author in authors if author]
        genres_value = book.get("genre") or []
        genres = [genres_value] if isinstance(genres_value, str) else list(genres_value)
        genres = [_clean(genre, 80) for genre in genres if _clean(genre, 80)]
        chapters = self._chapter_rows(html, slug)
        return {
            "slug": slug,
            "url": self.novel_url(slug),
            "title": title,
            "type": "Web Novel",
            "poster": poster,
            "description": description,
            "latest_chapter": chapters[-1]["number_text"] if chapters else None,
            "chapter_count": len(chapters),
            "authors": authors,
            "genres": genres or ["Web Novel"],
            "status": "",
            "rating": {},
            "languages": [
                {
                    "code": "pt-br",
                    "title": "Portugues (Brasil)",
                    "chapter_count": len(chapters),
                }
            ],
        }

    def list_chapters(self, source_url: str) -> list[dict]:
        kind, slug, _ = self.parse_source(source_url)
        if kind != "novel":
            raise ValueError("Informe a URL da novel no Novel Mania.")
        return self._chapter_rows(self._get(self.novel_url(slug)).text, slug)

    @staticmethod
    def _content_value(html: str) -> str:
        match = re.search(r"\bcontent:(\"(?:\\.|[^\"\\])*\")", html)
        if not match:
            raise RuntimeError("Novel Mania nao retornou o texto do capitulo.")
        return _script_string(match.group(1))

    def get_chapter(self, source_url: str) -> dict:
        kind, novel_slug, chapter_slug = self.parse_source(source_url)
        if kind != "chapter" or not chapter_slug:
            raise ValueError("Informe a URL de um capitulo Novel Mania.")
        url = self.chapter_url(novel_slug, chapter_slug)
        html = self._get(url).text
        soup = BeautifulSoup(html, "html.parser")
        creative_work = self._json_ld(soup, "CreativeWork")
        novel = creative_work.get("isPartOf") if isinstance(creative_work.get("isPartOf"), dict) else {}
        novel_title = _clean(novel.get("name"), 240) or novel_slug.replace("-", " ").title()
        raw_content = self._content_value(html)
        content_soup = BeautifulSoup(raw_content, "html.parser")
        blocks: list[dict] = []
        text_parts: list[str] = []
        block_tags = {"p", "h1", "h2", "h3", "blockquote", "li"}
        for node in content_soup.find_all([*block_tags, "img"]):
            if node.name == "img":
                source = str(node.get("src") or "").strip()
                if urlparse(source).hostname == ASSET_HOST:
                    blocks.append(
                        {
                            "type": "image",
                            "src": source,
                            "alt": _clean(node.get("alt"), 180) or "Ilustracao do capitulo",
                        }
                    )
                continue
            if node.find_parent(block_tags):
                continue
            text = _clean(node.get_text(" ", strip=True), 20000).replace("\xa0", " ")
            if text:
                blocks.append({"type": "text", "text": text})
                text_parts.append(text)
        if not text_parts:
            raise RuntimeError("Capitulo Novel Mania sem texto publico.")

        chapters = self._chapter_rows(html, novel_slug)
        current_index = next(
            (index for index, chapter in enumerate(chapters) if chapter["id"] == chapter_slug),
            -1,
        )
        current = chapters[current_index] if current_index >= 0 else {
            "id": chapter_slug,
            "number": 0,
            "number_text": "",
            "title": _clean(creative_work.get("name"), 300),
            "label": _clean(creative_work.get("name"), 300),
            "url": url,
        }
        previous_url = chapters[current_index - 1]["url"] if current_index > 0 else None
        next_url = chapters[current_index + 1]["url"] if 0 <= current_index < len(chapters) - 1 else None
        return {
            "url": url,
            "novel_slug": novel_slug,
            "chapter_slug": chapter_slug,
            "novel_title": novel_title,
            "chapter": current,
            "content": "\n\n".join(text_parts),
            "blocks": blocks,
            "previous": previous_url,
            "next": next_url,
        }
