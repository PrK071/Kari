from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BASE_URL = "https://centralnovel.com"
REST_POSTS_URL = f"{BASE_URL}/wp-json/wp/v2/posts"


def _clean(value: object, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()[:limit]


def _identity(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class CentralNovelPlugin:
    provider = "central_novel"
    source_label = "Central Novel"

    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=2, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Referer": f"{BASE_URL}/",
                "User-Agent": "Mozilla/5.0 Kari/1.0",
            }
        )

    @staticmethod
    def is_source(source_url: str) -> bool:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname not in {"centralnovel.com", "www.centralnovel.com"}:
            return False
        return bool(
            re.fullmatch(r"/series/[^/]+/?", parsed.path, re.I)
            or re.fullmatch(r"/(?!series/?$)[^/]+/?", parsed.path, re.I)
        )

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str]:
        parsed = urlparse(str(source_url or ""))
        if parsed.hostname not in {"centralnovel.com", "www.centralnovel.com"}:
            raise ValueError("Endereco Central Novel invalido.")
        series_match = re.fullmatch(r"/series/([^/]+)/?", parsed.path, re.I)
        if series_match:
            return "series", unquote(series_match.group(1))
        chapter_match = re.fullmatch(r"/(?!series/?$)([^/]+)/?", parsed.path, re.I)
        if chapter_match:
            return "chapter", unquote(chapter_match.group(1))
        raise ValueError("Endereco Central Novel invalido.")

    @staticmethod
    def series_url(slug: str) -> str:
        return f"{BASE_URL}/series/{slug}/"

    @staticmethod
    def chapter_url(slug: str) -> str:
        return f"{BASE_URL}/{slug}/"

    def _get(self, url: str, *, params: dict | None = None) -> requests.Response:
        response = self.session.get(url, params=params, timeout=(5, 25))
        response.raise_for_status()
        return response

    @staticmethod
    def _cover_from_soup(soup: BeautifulSoup) -> str:
        meta = soup.select_one('meta[property="og:image"]')
        return str(meta.get("content") or "").strip() if meta else ""

    @staticmethod
    def _catalog_item_from_article(article) -> dict | None:
        title_link = article.select_one("h2 a[href*='/series/']")
        image = article.select_one("img[src]")
        if not title_link or not image:
            return None
        source_url = str(title_link.get("href") or "").split("?", 1)[0]
        parsed = urlparse(source_url)
        match = re.fullmatch(r"/series/([^/]+)/?", parsed.path, re.I)
        title = _clean(title_link.get_text(" ", strip=True), 240)
        if not match or not title:
            return None
        latest = _clean(
            (article.select_one(".nchapter") or {}).get_text(" ", strip=True)
            if article.select_one(".nchapter")
            else "",
            100,
        )
        return {
            "id": f"central-novel:{unquote(match.group(1))}",
            "url": source_url,
            "source_url": source_url,
            "title": title,
            "source": "central_novel",
            "provider": "central_novel",
            "language": "pt-br",
            "available_translated_languages": ["pt-br"],
            "poster": urljoin(BASE_URL, str(image.get("src") or image.get("data-src") or "")),
            "description": _clean(
                (article.select_one(".contexcerpt") or {}).get_text(" ", strip=True)
                if article.select_one(".contexcerpt")
                else ""
            ),
            "genres": [
                _clean(link.get_text(" ", strip=True).lstrip("#"), 80)
                for link in article.select(".mdgenre a")
                if _clean(link.get_text(" ", strip=True).lstrip("#"), 80)
            ] or ["Web Novel"],
            "authors": [],
            "status": "",
            "latest_chapter": latest,
            "chapter_preview": [latest] if latest else ["Abrir lista de capitulos"],
        }

    def _search_index(self, query: str, limit: int) -> list[tuple[str, str]]:
        wanted = _identity(query)
        soup = BeautifulSoup(self._get(f"{BASE_URL}/series/list-mode/").text, "html.parser")
        results: list[tuple[str, str]] = []
        for link in soup.select(".soralist a.series[href*='/series/']"):
            title = _clean(link.get_text(" ", strip=True), 240)
            source_url = str(link.get("href") or "").split("?", 1)[0]
            if title and wanted in _identity(title):
                results.append((title, source_url))
            if len(results) >= limit:
                break
        return results

    def _catalog_item_from_metadata(self, source_url: str) -> dict:
        manga = self.manga_metadata(source_url)
        return {
            "id": f"central-novel:{manga['slug']}",
            "url": manga["url"],
            "source_url": manga["url"],
            "title": manga["title"],
            "source": self.provider,
            "provider": self.provider,
            "language": "pt-br",
            "available_translated_languages": ["pt-br"],
            "poster": manga.get("poster") or "",
            "description": manga.get("description") or "",
            "genres": manga.get("genres") or ["Web Novel"],
            "authors": manga.get("authors") or [],
            "status": manga.get("status") or "",
            "latest_chapter": manga.get("latest_chapter") or "",
            "chapter_preview": ["Abrir lista de capitulos"],
        }

    def catalog_items(self, query: str = "", limit: int = 24) -> list[dict]:
        safe_limit = max(1, min(limit, 24))
        wanted = _clean(query, 180)
        if wanted:
            matches = self._search_index(wanted, safe_limit)

            def load(row: tuple[str, str]) -> dict | None:
                try:
                    return self._catalog_item_from_metadata(row[1])
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=min(6, len(matches) or 1)) as executor:
                return [item for item in executor.map(load, matches) if item]

        soup = BeautifulSoup(self._get(f"{BASE_URL}/series/").text, "html.parser")
        items: list[dict] = []
        seen: set[str] = set()
        for article in soup.select(".listupd article"):
            item = self._catalog_item_from_article(article)
            if not item or item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
            if len(items) >= safe_limit:
                break
        return items

    @staticmethod
    def _series_description(soup: BeautifulSoup) -> str:
        parts: list[str] = []
        for paragraph in soup.select(".bixbox.synp .entry-content > p"):
            text = _clean(paragraph.get_text(" ", strip=True), 2000)
            if not text:
                continue
            lowered = _identity(text)
            if lowered.startswith("esta novel foi traduzida") or lowered.startswith("se voce possui os direitos"):
                break
            parts.append(text)
        return "\n\n".join(parts)[:4000]

    @staticmethod
    def _series_field(soup: BeautifulSoup, label: str) -> list[str]:
        wanted = _identity(label)
        for span in soup.select(".spe > span"):
            strong = span.select_one("b")
            if strong and _identity(strong.get_text(" ", strip=True).rstrip(":")) == wanted:
                links = [_clean(link.get_text(" ", strip=True), 160) for link in span.select("a")]
                if links:
                    return [value for value in links if value]
                text = span.get_text(" ", strip=True)
                text = re.sub(rf"^{re.escape(strong.get_text(' ', strip=True))}", "", text).strip()
                return [_clean(text, 240)] if text else []
        return []

    @staticmethod
    def _chapter_rows(soup: BeautifulSoup) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        dom_rows = list(soup.select(".eplister li[data-id]"))
        for ordinal, item in enumerate(reversed(dom_rows), start=1):
            link = item.select_one("a[href]")
            if not link:
                continue
            url = str(link.get("href") or "").split("?", 1)[0]
            parsed = urlparse(url)
            match = re.fullmatch(r"/(?!series/?$)([^/]+)/?", parsed.path, re.I)
            if not match or url in seen or "/pdf/" in parsed.path:
                continue
            seen.add(url)
            number_label = _clean(
                (item.select_one(".epl-num") or {}).get_text(" ", strip=True)
                if item.select_one(".epl-num")
                else "",
                100,
            )
            number_match = re.search(r"Cap\.?\s*([0-9]+(?:\.[0-9]+)?)", number_label, re.I)
            number_text = number_match.group(1) if number_match else str(ordinal)
            try:
                number = float(number_text)
            except ValueError:
                number = float(ordinal)
            title = _clean(
                (item.select_one(".epl-title") or {}).get_text(" ", strip=True)
                if item.select_one(".epl-title")
                else "",
                300,
            )
            rows.append(
                {
                    "id": unquote(match.group(1)),
                    "post_id": str(item.get("data-id") or ""),
                    "number": number,
                    "number_text": number_text,
                    "label": number_label or f"Capitulo {number_text}",
                    "title": title,
                    "url": url,
                }
            )
        return sorted(rows, key=lambda chapter: float(chapter["number"]))

    def manga_metadata(self, source_url: str) -> dict:
        kind, slug = self.parse_source(source_url)
        if kind != "series":
            raise ValueError("Informe a URL da serie no Central Novel.")
        soup = BeautifulSoup(self._get(self.series_url(slug)).text, "html.parser")
        title_meta = soup.select_one('meta[property="og:title"]')
        title = _clean(title_meta.get("content") if title_meta else "", 240)
        title = re.sub(r"\s*\|\s*Central Novel\s*$", "", title, flags=re.I).strip()
        if not title:
            heading = soup.select_one("h1")
            title = _clean(heading.get_text(" ", strip=True) if heading else "", 240)
        if not title:
            raise RuntimeError("Central Novel nao retornou titulo da obra.")
        chapters = self._chapter_rows(soup)
        types = self._series_field(soup, "Tipo")
        status = (self._series_field(soup, "Status") or [""])[0]
        return {
            "slug": slug,
            "url": self.series_url(slug),
            "title": title,
            "type": types[0] if types else "Web Novel",
            "poster": self._cover_from_soup(soup),
            "description": self._series_description(soup),
            "latest_chapter": chapters[-1]["number_text"] if chapters else None,
            "chapter_count": len(chapters),
            "authors": self._series_field(soup, "Autor"),
            "genres": [
                _clean(link.get_text(" ", strip=True), 80)
                for link in soup.select(".genxed a[href*='/genre/']")
                if _clean(link.get_text(" ", strip=True), 80)
            ] or types or ["Web Novel"],
            "status": status,
            "rating": {},
            "alternative_titles": [
                _clean(value, 240)
                for value in _clean(
                    (soup.select_one(".alter") or {}).get_text(" ", strip=True)
                    if soup.select_one(".alter")
                    else "",
                    1200,
                ).split(",")
                if _clean(value, 240)
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
        kind, slug = self.parse_source(source_url)
        if kind != "series":
            raise ValueError("Informe a URL da serie no Central Novel.")
        soup = BeautifulSoup(self._get(self.series_url(slug)).text, "html.parser")
        return self._chapter_rows(soup)

    @staticmethod
    def _content_blocks(rendered: str) -> tuple[str, list[dict]]:
        soup = BeautifulSoup(rendered, "html.parser")
        blocks: list[dict] = []
        text_parts: list[str] = []
        block_tags = {"p", "h1", "h2", "h3", "blockquote", "li"}
        for node in soup.find_all([*block_tags, "img"]):
            if node.name == "img":
                source = str(node.get("src") or node.get("data-src") or "").strip()
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
            text = _clean(node.get_text(" ", strip=True), 20000).replace("\xa0", " ")
            if text:
                blocks.append({"type": "text", "text": text})
                text_parts.append(text)
        return "\n\n".join(text_parts), blocks

    def get_chapter(self, source_url: str) -> dict:
        kind, chapter_slug = self.parse_source(source_url)
        if kind != "chapter":
            raise ValueError("Informe a URL de um capitulo Central Novel.")
        response = self._get(
            REST_POSTS_URL,
            params={
                "slug": chapter_slug,
                "_fields": "id,slug,link,title,content,date,modified",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise FileNotFoundError("Capitulo nao encontrado no Central Novel.")
        post = rows[0]
        rendered_title = _clean((post.get("title") or {}).get("rendered"), 300)
        content, blocks = self._content_blocks(str((post.get("content") or {}).get("rendered") or ""))
        if not content:
            raise RuntimeError("Capitulo Central Novel sem texto publico.")
        number_match = re.search(r"Cap[ií]tulo\s*([\d.]+)", rendered_title, re.I)
        if not number_match:
            number_match = re.search(r"-capitulo-([\d.]+)", chapter_slug, re.I)
        number_text = number_match.group(1) if number_match else ""
        novel_title = re.sub(
            r"\s*[-–—]\s*(?:Cap[ií]tulo|Vol(?:ume)?\.?|Pr[oó]logo|Posf[aá]cio|Ep[ií]logo|Interl[uú]dio)\b.*$",
            "",
            rendered_title,
            flags=re.I,
        ).strip() or rendered_title
        chapter_title = rendered_title[len(novel_title):].strip(" -–—")
        return {
            "url": str(post.get("link") or self.chapter_url(chapter_slug)),
            "novel_title": novel_title,
            "chapter_title": chapter_title or f"Capitulo {number_text}",
            "number_text": number_text,
            "content": content,
            "blocks": blocks,
            "previous": None,
            "next": None,
        }
