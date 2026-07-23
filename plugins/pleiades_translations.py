from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BASE_URL = "https://pleiadestranslations.wordpress.com"
SERIES_URL = f"{BASE_URL}/"
WP_API = "https://public-api.wordpress.com/wp/v2/sites/pleiadestranslations.wordpress.com"
TITLE = "Re:Zero kara Hajimeru Isekai Seikatsu"


def _clean(value: object, limit: int = 20000) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()[:limit]


def _roman_number(value: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50}
    total = 0
    previous = 0
    for character in reversed(value.casefold()):
        current = values.get(character, 0)
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


class PleiadesTranslationsPlugin:
    provider = "pleiades_translations"
    source_label = "Pleiades Translations"

    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=2, pool_connections=16, pool_maxsize=16)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Referer": SERIES_URL,
                "User-Agent": "Mozilla/5.0 Kari/1.0",
            }
        )
        self._chapter_cache: list[dict] | None = None
        self._cache_lock = threading.Lock()

    @staticmethod
    def is_source(source_url: str) -> bool:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname != "pleiadestranslations.wordpress.com":
            return False
        path = parsed.path.rstrip("/")
        return path == "" or bool(
            re.fullmatch(r"/arco-[a-z0-9-]+", path, re.I)
            or re.fullmatch(
                r"/\d{4}/\d{2}/\d{2}/arco-\d+-(?:capitulo|interludio|adendo)[^/]*",
                path,
                re.I,
            )
        )

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str]:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname != "pleiadestranslations.wordpress.com":
            raise ValueError("Endereco Pleiades Translations invalido.")
        path = parsed.path.rstrip("/")
        if not path:
            return "series", ""
        arc_match = re.fullmatch(r"/(arco-[a-z0-9-]+)", path, re.I)
        if arc_match:
            return "arc", arc_match.group(1).lower()
        chapter_match = re.fullmatch(
            r"/\d{4}/\d{2}/\d{2}/(arco-\d+-(?:capitulo|interludio|adendo)[^/]*)",
            path,
            re.I,
        )
        if chapter_match:
            return "chapter", unquote(chapter_match.group(1)).lower()
        raise ValueError("Endereco Pleiades Translations invalido.")

    def _get(self, url: str, *, params: dict | None = None) -> requests.Response:
        response = self.session.get(url, params=params, timeout=(5, 35))
        response.raise_for_status()
        return response

    @staticmethod
    def _chapter_parts(url: str) -> tuple[int, str, float] | None:
        slug = unquote(urlparse(str(url or "")).path.rstrip("/").rsplit("/", 1)[-1])
        match = re.match(
            r"arco-(\d+)-(capitulo|interludio|adendo)(?:-([0-9]+|[ivxl]+))?",
            slug,
            re.I,
        )
        if not match:
            return None
        arc = int(match.group(1))
        kind = match.group(2).casefold()
        raw_number = str(match.group(3) or "")
        if kind == "capitulo":
            local_number = float(raw_number) if raw_number.isdigit() else 0.0
        elif kind == "adendo":
            local_number = 900.0
        else:
            local_number = 1000.0 + float(
                int(raw_number) if raw_number.isdigit() else _roman_number(raw_number)
            )
        return arc, kind, local_number

    @staticmethod
    def _arc_sections(soup: BeautifulSoup) -> list[tuple[str, set[int]]]:
        sections: list[tuple[str, set[int]]] = []
        seen: set[str] = set()
        for link in soup.select("nav a[href], .menu a[href]"):
            text = _clean(link.get_text(" ", strip=True), 120)
            href = str(link.get("href") or "").split("?", 1)[0].rstrip("/") + "/"
            parsed = urlparse(href)
            if parsed.hostname != "pleiadestranslations.wordpress.com":
                continue
            if not re.fullmatch(r"/arco-[a-z0-9-]+/", parsed.path, re.I):
                continue
            arcs = {int(value) for value in re.findall(r"\d+", f"{text} {parsed.path}")}
            if href in seen or not arcs:
                continue
            seen.add(href)
            sections.append((href, arcs))
        return sections

    def _section_rows(self, section: tuple[str, set[int]]) -> list[dict]:
        section_url, allowed_arcs = section
        soup = BeautifulSoup(self._get(section_url).text, "html.parser")
        root = soup.select_one("article.page .entry-content") or soup.select_one(".entry-content")
        if not root:
            return []
        rows: list[dict] = []
        seen: set[str] = set()
        for link in root.select("a[href]"):
            url = str(link.get("href") or "").split("?", 1)[0]
            parts = self._chapter_parts(url)
            if not parts or parts[0] not in allowed_arcs or url in seen:
                continue
            seen.add(url)
            label = _clean(link.get_text(" ", strip=True), 400)
            rows.append(self._chapter_row(url, label))
        return rows

    def _category_rows(self) -> list[dict]:
        categories = self._get(
            f"{WP_API}/categories",
            params={"per_page": 100, "_fields": "id,slug,name,count"},
        ).json()
        category_rows = [
            category
            for category in categories
            if isinstance(category, dict)
            and re.fullmatch(r"arco-\d+", str(category.get("slug") or ""), re.I)
            and int(category.get("count") or 0) > 0
        ]

        def load(category: dict) -> list[dict]:
            posts = self._get(
                f"{WP_API}/posts",
                params={
                    "categories": int(category["id"]),
                    "per_page": 100,
                    "_fields": "id,slug,link,title",
                },
            ).json()
            rows: list[dict] = []
            for post in posts if isinstance(posts, list) else []:
                link = str(post.get("link") or "")
                if not self._chapter_parts(link):
                    continue
                rendered = str((post.get("title") or {}).get("rendered") or "")
                title = _clean(BeautifulSoup(rendered, "html.parser").get_text(" ", strip=True), 400)
                rows.append(self._chapter_row(link, title))
            return rows

        with ThreadPoolExecutor(max_workers=min(8, len(category_rows) or 1)) as executor:
            grouped = list(executor.map(load, category_rows))
        return [row for group in grouped for row in group]

    def _chapter_row(self, url: str, title: str) -> dict:
        parts = self._chapter_parts(url)
        if not parts:
            raise ValueError("Link de capitulo Pleiades invalido.")
        arc, kind, local_number = parts
        slug = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
        clean_title = re.sub(r"^Arco\s*\d+\s*[—–-]\s*", "", _clean(title, 400), flags=re.I)
        if kind == "capitulo" and local_number == 0 and clean_title:
            title_number = re.search(r"Cap[ií]tulo\s*(\d+)", clean_title, re.I)
            if title_number:
                local_number = float(title_number.group(1))
        label = f"Arco {arc} · {clean_title or slug}"
        return {
            "id": slug,
            "arc": arc,
            "kind": kind,
            "local_number": local_number,
            "number": float(arc * 10000) + local_number,
            "number_text": f"{arc}.{str(local_number).rstrip('0').rstrip('.')}",
            "label": label,
            "title": label,
            "url": url,
        }

    def _chapters(self) -> list[dict]:
        with self._cache_lock:
            if self._chapter_cache is not None:
                return [dict(chapter) for chapter in self._chapter_cache]

        home = BeautifulSoup(self._get(SERIES_URL).text, "html.parser")
        sections = self._arc_sections(home)
        with ThreadPoolExecutor(max_workers=min(8, len(sections) or 1)) as executor:
            section_groups = list(executor.map(self._section_rows, sections))

        merged: dict[tuple[int, str, float], dict] = {}
        for chapter in [row for group in section_groups for row in group]:
            identity = (
                int(chapter["arc"]),
                str(chapter["kind"]),
                float(chapter["local_number"]),
            )
            merged[identity] = chapter
        for chapter in self._category_rows():
            identity = (
                int(chapter["arc"]),
                str(chapter["kind"]),
                float(chapter["local_number"]),
            )
            # A API fornece o link canonico quando uma secao ainda aponta para
            # um slug antigo do mesmo capitulo.
            merged[identity] = chapter

        chapters = sorted(
            merged.values(),
            key=lambda chapter: (
                int(chapter["arc"]),
                float(chapter["local_number"]),
                str(chapter["id"]),
            ),
        )
        if not chapters:
            raise RuntimeError("Pleiades Translations nao retornou capitulos.")
        with self._cache_lock:
            self._chapter_cache = chapters
        return [dict(chapter) for chapter in chapters]

    def catalog_items(self, query: str = "", limit: int = 24) -> list[dict]:
        wanted = _clean(query, 180).casefold()
        aliases = f"{TITLE} re zero rezero starting life in another world"
        if wanted and wanted not in aliases.casefold():
            return []
        chapters = self._chapters()
        latest = chapters[-1]
        return [
            {
                "id": "pleiades-translations:rezero",
                "url": SERIES_URL,
                "source_url": SERIES_URL,
                "title": TITLE,
                "source": self.provider,
                "provider": self.provider,
                "language": "pt-br",
                "available_translated_languages": ["pt-br"],
                "poster": (
                    "https://pleiadestranslations.wordpress.com/"
                    "wp-content/uploads/2021/01/arco1cap0.png"
                ),
                "description": (
                    "Traducao em portugues brasileiro da web novel Re:Zero, "
                    "organizada pelas secoes de cada arco."
                ),
                "genres": ["Web Novel", "Fantasy", "Isekai", "Drama"],
                "authors": ["Tappei Nagatsuki"],
                "status": "Em andamento",
                "latest_chapter": latest["label"],
                "chapter_count": len(chapters),
                "chapter_preview": [
                    chapter["label"] for chapter in reversed(chapters[-3:])
                ],
            }
        ][: max(1, min(int(limit), 24))]

    def manga_metadata(self, source_url: str) -> dict:
        if not self.is_source(source_url):
            raise ValueError("Endereco Pleiades Translations invalido.")
        chapters = self._chapters()
        arcs = sorted({int(chapter["arc"]) for chapter in chapters})
        return {
            "slug": "rezero",
            "url": SERIES_URL,
            "title": TITLE,
            "type": "Web Novel",
            "poster": (
                "https://pleiadestranslations.wordpress.com/"
                "wp-content/uploads/2021/01/arco1cap0.png"
            ),
            "description": (
                "Traducao em portugues brasileiro da web novel Re:Zero. "
                f"Capitulos disponiveis nos arcos {', '.join(map(str, arcs))}."
            ),
            "latest_chapter": chapters[-1]["label"],
            "chapter_count": len(chapters),
            "authors": ["Tappei Nagatsuki"],
            "genres": ["Web Novel", "Fantasy", "Isekai", "Drama"],
            "status": "Em andamento",
            "rating": {},
            "alternative_titles": [
                "Re:Zero − Starting Life in Another World",
                "Re:Zero",
            ],
            "languages": [
                {
                    "code": "pt-br",
                    "title": "Portugues (Brasil)",
                    "chapter_count": len(chapters),
                }
            ],
        }

    def list_chapters(self, source_url: str) -> list[dict]:
        if not self.is_source(source_url):
            raise ValueError("Endereco Pleiades Translations invalido.")
        return self._chapters()

    @staticmethod
    def _content_blocks(rendered: str) -> tuple[str, list[dict]]:
        soup = BeautifulSoup(rendered, "html.parser")
        blocks: list[dict] = []
        text_parts: list[str] = []
        block_tags = {"p", "h1", "h2", "h3", "h4", "blockquote", "li"}
        for node in soup.find_all([*block_tags, "img"]):
            if node.name == "img":
                source = _clean(
                    node.get("data-orig-file")
                    or node.get("data-large-file")
                    or node.get("src"),
                    1200,
                )
                if source.startswith(("http://", "https://")):
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
            text = _clean(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "text", "text": text})
                text_parts.append(text)
        return "\n\n".join(text_parts), blocks

    def get_chapter(self, source_url: str) -> dict:
        kind, chapter_slug = self.parse_source(source_url)
        if kind != "chapter":
            raise ValueError("Informe a URL de um capitulo Pleiades Translations.")
        posts = self._get(
            f"{WP_API}/posts",
            params={
                "slug": chapter_slug,
                "per_page": 1,
                "_fields": "id,slug,link,title,content",
            },
        ).json()
        if not isinstance(posts, list) or not posts:
            raise FileNotFoundError("Capitulo nao encontrado no Pleiades Translations.")
        post = posts[0]
        rendered_title = str((post.get("title") or {}).get("rendered") or "")
        chapter_title = _clean(
            BeautifulSoup(rendered_title, "html.parser").get_text(" ", strip=True),
            400,
        )
        content, blocks = self._content_blocks(
            str((post.get("content") or {}).get("rendered") or "")
        )
        if not content:
            raise RuntimeError("Capitulo Pleiades Translations sem texto publico.")
        chapter_row = self._chapter_row(
            str(post.get("link") or source_url),
            chapter_title,
        )
        return {
            "url": str(post.get("link") or source_url),
            "source_url": SERIES_URL,
            "novel_title": TITLE,
            "chapter_title": chapter_title,
            "number_text": chapter_row["number_text"],
            "content": content,
            "blocks": blocks,
            "previous": None,
            "next": None,
        }
