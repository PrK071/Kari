from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BASE_URL = "https://fliptru.com.br"
MEDIA_HOST = "media.fliptru.com.br"


def _clean(value: object, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _background_url(style: object) -> str:
    match = re.search(r"background(?:-image)?\s*:\s*(?:[^;]*?)url\((['\"]?)(.*?)\1\)", str(style or ""), re.I)
    return match.group(2).strip() if match else ""


def _kind_and_genres(value: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in value.split("|") if part.strip()]
    if not parts:
        return "Quadrinho", []
    if len(parts) == 1:
        return "Quadrinho", parts
    return parts[0], parts[1:]


class FliptruPlugin:
    provider = "fliptru"
    source_label = "Fliptru"

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
        return parsed.hostname in {"fliptru.com.br", "www.fliptru.com.br"} and bool(
            re.match(r"^/comic/[^/]+(?:/[^/]+)?/?$", parsed.path, re.I)
        )

    @staticmethod
    def slug_from_source(source_url: str) -> str | None:
        parsed = urlparse(str(source_url or ""))
        match = re.match(r"^/comic/([^/]+)", parsed.path, re.I)
        return unquote(match.group(1)) if match else None

    @staticmethod
    def chapter_parts(source_url: str) -> tuple[str, str] | None:
        parsed = urlparse(str(source_url or ""))
        match = re.match(r"^/comic/([^/]+)/([^/]+)/?$", parsed.path, re.I)
        return (unquote(match.group(1)), unquote(match.group(2))) if match else None

    @staticmethod
    def comic_url(slug: str) -> str:
        return f"{BASE_URL}/comic/{slug}"

    @staticmethod
    def chapter_url(slug: str, chapter: str) -> str:
        return f"{BASE_URL}/comic/{slug}/{chapter}"

    def _get(self, url: str, *, params: dict | None = None) -> requests.Response:
        response = self.session.get(url, params=params, timeout=(5, 22))
        response.raise_for_status()
        return response

    @staticmethod
    def profile_url(handle: str) -> str:
        clean_handle = str(handle or "").strip().lstrip("@")
        return f"{BASE_URL}/@{quote(clean_handle, safe='_-.')}"

    def _resolve_profile_link(self, href: str) -> str:
        intermediate = urljoin(BASE_URL, href)
        try:
            soup = BeautifulSoup(self._get(intermediate).text, "html.parser")
            for link in soup.select("a[href^='http']"):
                label = _clean(link.get_text(" ", strip=True), 120).casefold()
                target = str(link.get("href") or "").strip()
                if "link externo" in label and urlparse(target).hostname:
                    return target
        except Exception:
            pass
        return intermediate

    def author_profile(self, name: str) -> dict:
        handle = str(name or "").strip().lstrip("@")
        if not handle:
            raise ValueError("Informe o nome do autor Fliptru.")
        profile_url = self.profile_url(handle)
        soup = BeautifulSoup(self._get(profile_url).text, "html.parser")
        image_node = soup.select_one("img[alt^='Foto de @']")
        profile_card = image_node.find_parent(class_="card-body") if image_node else None
        if not image_node or not profile_card:
            raise RuntimeError(f"Fliptru nao encontrou autor: {name}")

        heading = profile_card.select_one("h4")
        display_name = _clean(heading.get_text(" ", strip=True) if heading else handle, 180)
        image_url = str(image_node.get("src") or "").strip()
        description_node = profile_card.select_one(".d-none.d-md-block ul.list-group p")
        if not description_node:
            description_node = profile_card.select_one("#more-info p")
        description = _clean(
            description_node.get_text(" ", strip=True) if description_node else "",
            4000,
        )

        social_links: list[dict] = []
        seen: set[str] = set()
        for link in profile_card.select(".d-none.d-md-block a[href*='/leaving/ProfileLink/']"):
            label = _clean(link.get_text(" ", strip=True), 80) or "Rede"
            target = self._resolve_profile_link(str(link.get("href") or ""))
            if not target or target in seen:
                continue
            seen.add(target)
            social_links.append({"label": label, "url": target})

        return {
            "id": handle,
            "name": display_name or handle,
            "native_name": f"@{handle}",
            "alternative_names": [],
            "role": "Autor / Quadrinista",
            "image_url": image_url,
            "image_fallbacks": [],
            "description": description,
            "gender": "",
            "birth_date": "",
            "death_date": "",
            "age": None,
            "years_active": [],
            "home_town": "",
            "occupations": ["Quadrinista"],
            "language": "pt-br",
            "favourites": None,
            "status": "",
            "genres": [],
            "total_series": None,
            "blood_type": "",
            "official_site": "",
            "twitter": "",
            "facebook": "",
            "social_links": social_links,
            "site_url": profile_url,
            "source_links": [{"label": "Fliptru", "url": profile_url}],
            "source": "Fliptru",
        }

    def catalog_items(self, limit: int = 24) -> list[dict]:
        soup = BeautifulSoup(self._get(f"{BASE_URL}/").text, "html.parser")
        items: list[dict] = []
        seen: set[str] = set()
        for card in soup.select("a.comic-card[data-url]"):
            data_url = str(card.get("data-url") or "")
            match = re.search(r"/comic/([^/?#]+)/info", data_url, re.I)
            if not match:
                continue
            slug = unquote(match.group(1))
            if slug in seen:
                continue
            title = _clean(card.get("data-title"), 240)
            if not title:
                heading = card.select_one("h1")
                title = _clean(heading.get_text(" ", strip=True) if heading else "", 240)
            cover = _background_url(card.get("style"))
            genre_heading = card.select_one("h2")
            kind_and_genre = _clean(genre_heading.get_text(" ", strip=True) if genre_heading else "", 120)
            _, genres = _kind_and_genres(kind_and_genre)
            if not title or not cover:
                continue
            seen.add(slug)
            items.append(
                {
                    "id": f"fliptru:{slug}",
                    "url": self.comic_url(slug),
                    "title": title,
                    "source": self.provider,
                    "provider": self.provider,
                    "language": "pt-br",
                    "available_translated_languages": ["pt-br"],
                    "poster": cover,
                    "description": "",
                    "genres": genres or ["Quadrinho"],
                    "authors": [],
                    "status": "",
                    "latest_chapter": "",
                }
            )
            if len(items) >= max(1, limit):
                break
        return items

    def search_items(self, query: str, limit: int = 12) -> list[dict]:
        wanted = _clean(query, 180)
        if not wanted:
            raise ValueError("Digite o nome da obra para buscar.")
        rows = self._get(f"{BASE_URL}/search/", params={"term": wanted, "page": "home"}).json()
        sources: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            path = str((row or {}).get("url") or "")
            match = re.match(r"/comic/([^/?#]+)/info", path, re.I)
            if match:
                sources.append(self.comic_url(unquote(match.group(1))))
            if len(sources) >= max(1, limit):
                break

        def load(source: str) -> dict | None:
            try:
                return self.catalog_item_from_metadata(self.manga_metadata(source))
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(6, len(sources) or 1)) as executor:
            return [item for item in executor.map(load, sources) if item]

    def catalog_item_from_metadata(self, manga: dict) -> dict:
        return {
            "id": f"fliptru:{manga['slug']}",
            "url": manga["url"],
            "title": manga["title"],
            "source": self.provider,
            "provider": self.provider,
            "language": "pt-br",
            "available_translated_languages": ["pt-br"],
            "poster": manga.get("poster"),
            "description": manga.get("description") or "",
            "genres": manga.get("genres") or ["Quadrinho"],
            "authors": manga.get("authors") or [],
            "status": manga.get("status") or "",
            "latest_chapter": manga.get("latest_chapter") or "",
            "chapter_count": manga.get("chapter_count"),
        }

    def manga_metadata(self, source_url: str) -> dict:
        slug = self.slug_from_source(source_url)
        if not slug:
            raise ValueError("Informe uma URL de obra Fliptru.")
        soup = BeautifulSoup(self._get(self.comic_url(slug)).text, "html.parser")
        title_node = soup.select_one("h1")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "", 240)
        if not title:
            og_title = soup.select_one('meta[property="og:title"]')
            title = _clean((og_title or {}).get("content") if og_title else "", 240)
            title = re.sub(r"^Leia o quadrinho\s+", "", title, flags=re.I)
        if not title:
            raise RuntimeError("Fliptru nao retornou titulo da obra.")
        description_node = soup.select_one(".comic-description-large p") or soup.select_one(".comic-description-short p")
        description = _clean(description_node.get_text(" ", strip=True) if description_node else "")
        cover_meta = soup.select_one('meta[property="og:image"]')
        cover = str(cover_meta.get("content") or "").strip() if cover_meta else ""
        heading = title_node.find_previous("h2") if title_node else None
        kind_and_genre = _clean(heading.get_text(" ", strip=True) if heading else "", 120)
        kind, genres = _kind_and_genres(kind_and_genre)
        for tag in soup.select(".comic-description-large a[href^='/tag/']"):
            genre = _clean(tag.get_text(" ", strip=True).lstrip("#"), 80)
            if genre and genre.casefold() not in {item.casefold() for item in genres}:
                genres.append(genre)
        author_link = soup.select_one('a[href^="/@"]')
        author = _clean(author_link.get_text(" ", strip=True).lstrip("@") if author_link else "", 120)
        page_text = soup.get_text(" ", strip=True)
        count_match = re.search(r"(\d+)\s+cap[ií]tulos?", page_text, re.I)
        chapter_count = int(count_match.group(1)) if count_match else 0
        return {
            "slug": slug,
            "url": self.comic_url(slug),
            "title": title,
            "type": kind,
            "poster": cover,
            "description": description,
            "latest_chapter": str(chapter_count) if chapter_count else None,
            "chapter_count": chapter_count,
            "authors": [author] if author else [],
            "genres": genres or ["Quadrinho"],
            "status": "",
            "rating": {},
            "languages": [{"code": "pt-br", "title": "Portugues (Brasil)", "chapter_count": chapter_count}],
        }

    def list_chapters(self, source_url: str) -> list[dict]:
        slug = self.slug_from_source(source_url)
        if not slug:
            raise ValueError("Informe uma URL de obra Fliptru.")
        chapters: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page <= 100:
            params = {"order": "asc"}
            if page > 1:
                params["page"] = str(page)
            soup = BeautifulSoup(self._get(f"{self.comic_url(slug)}/chapters", params=params).text, "html.parser")
            added = 0
            for link in soup.select(f'a[href*="/comic/{slug}/"]'):
                href = str(link.get("href") or "")
                match = re.search(rf"/comic/{re.escape(slug)}/([^/?#]+)", href, re.I)
                if not match:
                    continue
                chapter_key = unquote(match.group(1))
                if chapter_key in seen:
                    continue
                label = _clean(link.get_text(" ", strip=True), 300)
                number_match = re.match(r"\s*([\d.,]+)", label)
                number_text = number_match.group(1).replace(",", ".") if number_match else chapter_key
                title = re.sub(r"^\s*[\d.,]+\s*[-–—]?\s*", "", label).strip()
                chapters.append(
                    {
                        "id": chapter_key,
                        "number": number_text,
                        "title": title,
                        "url": self.chapter_url(slug, chapter_key),
                    }
                )
                seen.add(chapter_key)
                added += 1
            load_more = soup.select_one(
                "#chapters-load-more[data-url], #chapters-load-more [data-url], "
                "[data-url*='/chapters?']"
            )
            if not load_more or added == 0:
                break
            page += 1
        return chapters

    def get_chapter(self, source_url: str) -> dict:
        parts = self.chapter_parts(source_url)
        if not parts:
            raise ValueError("Informe uma URL de capitulo Fliptru.")
        slug, chapter_key = parts
        url = self.chapter_url(slug, chapter_key)
        soup = BeautifulSoup(self._get(url).text, "html.parser")
        images: list[str] = []
        for image in soup.select(".comic-page-image img, img[alt^='Página'], img[alt^='Pagina']"):
            source = str(image.get("src") or image.get("data-src") or "").strip()
            if source and urlparse(source).hostname == MEDIA_HOST and source not in images:
                images.append(source)
        # Leitor horizontal usa cada pagina como background-image de um slide,
        # sem elemento <img>. Limita ao container de pagina para ignorar anuncios.
        for page in soup.select(".comic-page-image[style]"):
            source = _background_url(page.get("style"))
            if source and urlparse(source).hostname == MEDIA_HOST and source not in images:
                images.append(source)
        if not images:
            raise RuntimeError("Capitulo Fliptru sem paginas publicas.")
        previous_url = None
        next_url = None
        for link in soup.select("a[href*='chapter-nav-button']"):
            href = str(link.get("href") or "")
            target = urljoin(BASE_URL, href.split("?", 1)[0])
            if "extra=prev" in href:
                previous_url = target
            elif "extra=next" in href:
                next_url = target
        page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "", 300)
        chapter_title = re.sub(r"^.*?\s+-\s+", "", page_title, count=1).replace(" - Fliptru", "").strip()
        return {
            "slug": slug,
            "chapter_key": chapter_key,
            "title": chapter_title,
            "pages": images,
            "previous": previous_url,
            "next": next_url,
        }
