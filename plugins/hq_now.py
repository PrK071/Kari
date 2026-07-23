from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter


GRAPHQL_URL = "https://admin.hq-now.com/graphql"
STATIC_HTTP_PREFIX = "http://static.hq-now.com/"
STATIC_HTTPS_PREFIX = "https://static.hq-now.com/"


def _clean(value: object, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _number(value: object) -> float | None:
    try:
        return float(str(value or "").replace(",", "."))
    except ValueError:
        return None


def _https_image(value: object) -> str:
    url = str(value or "").strip()
    return url.replace(STATIC_HTTP_PREFIX, STATIC_HTTPS_PREFIX, 1)


class HQNowPlugin:
    provider = "hq_now"
    source_label = "HQ Now"

    _DETAIL_FIELDS = """
        id name synopsis editoraId status publisherName hqCover impressionsCount updatedAt
        capitulos { name id number }
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=2, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.hq-now.com",
                "Referer": "https://www.hq-now.com/",
                "User-Agent": "Mozilla/5.0 Kari/1.0",
            }
        )

    @staticmethod
    def is_source(source_url: str) -> bool:
        return urlparse(str(source_url or "")).scheme.lower() == "hq-now"

    @staticmethod
    def comic_url(comic_id: int | str) -> str:
        return f"hq-now://comic/{int(comic_id)}"

    @staticmethod
    def issue_url(comic_id: int | str, issue_id: int | str) -> str:
        return f"hq-now://issue/{int(comic_id)}/{int(issue_id)}"

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, int, int | None]:
        parsed = urlparse(str(source_url or ""))
        if parsed.scheme.lower() != "hq-now":
            raise ValueError("Fonte HQ Now invalida.")
        parts = [part for part in parsed.path.split("/") if part]
        try:
            if parsed.netloc == "comic" and len(parts) == 1:
                return "comic", int(parts[0]), None
            if parsed.netloc == "issue" and len(parts) == 2:
                return "issue", int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("Identificador HQ Now invalido.") from exc
        raise ValueError("Endereco HQ Now invalido.")

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        response = self.session.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            timeout=(5, 20),
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            raise RuntimeError(_clean(errors[0].get("message") or "HQ Now indisponivel."))
        return payload.get("data") or {}

    def _details(self, ids: list[int]) -> list[dict]:
        unique_ids = list(dict.fromkeys(int(value) for value in ids if int(value) > 0))[:60]
        if not unique_ids:
            return []
        fields = self._DETAIL_FIELDS
        aliases = "\n".join(
            f"hq{index}: getHqsById(id: {comic_id}) {{ {fields} }}"
            for index, comic_id in enumerate(unique_ids)
        )
        data = self._graphql(f"query BatchHqs {{ {aliases} }}")
        result: list[dict] = []
        for index in range(len(unique_ids)):
            rows = data.get(f"hq{index}") or []
            if rows and isinstance(rows[0], dict):
                result.append(rows[0])
        return result

    def _item(self, hq: dict) -> dict:
        comic_id = int(hq.get("id") or 0)
        chapters = self._sorted_issues(hq.get("capitulos") or [])
        latest = chapters[-1] if chapters else {}
        return {
            "id": f"hq-now:{comic_id}",
            "title": _clean(hq.get("name"), 240) or "HQ sem titulo",
            "slug": str(comic_id),
            "url": self.comic_url(comic_id),
            "source_url": self.comic_url(comic_id),
            "source": self.source_label,
            "provider": self.provider,
            "section": "HQ Now",
            "poster": _https_image(hq.get("hqCover")),
            "cover_url": _https_image(hq.get("hqCover")),
            "description": _clean(hq.get("synopsis")),
            "genres": ["HQ", _clean(hq.get("publisherName"), 80)],
            "authors": [],
            "status": _clean(hq.get("status"), 60),
            "latest_chapter": str(latest.get("number") or ""),
            "chapter_count": len(chapters),
            "chapter_preview": [str(row.get("number") or "") for row in reversed(chapters[-3:])],
            "chapter_languages": ["pt-br"],
            "language": "pt-br",
            "updated_at": str(hq.get("updatedAt") or ""),
            "views": int(hq.get("impressionsCount") or 0),
        }

    def catalog_items(self, query: str = "", limit: int = 32) -> list[dict]:
        wanted = _clean(query, 180)
        if wanted:
            data = self._graphql(
                """
                query SearchHqs($name: String!) {
                  getHqsByName(name: $name) { id name }
                }
                """,
                {"name": wanted},
            )
            rows = data.get("getHqsByName") or []
        else:
            data = self._graphql(
                """
                query RecentHqs {
                  getRecentlyUpdatedHqs { id name }
                }
                """
            )
            rows = data.get("getRecentlyUpdatedHqs") or []
        ids = [int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) > 0]
        return [self._item(row) for row in self._details(ids[: max(1, min(limit, 60))])]

    def manga_metadata(self, source_url: str) -> dict:
        kind, comic_id, _ = self.parse_source(source_url)
        if kind != "comic":
            raise ValueError("Informe endereco da HQ, nao da edicao.")
        details = self._details([comic_id])
        if not details:
            raise FileNotFoundError("HQ nao encontrada no HQ Now.")
        item = self._item(details[0])
        return {
            "slug": str(comic_id),
            "url": self.comic_url(comic_id),
            "title": item["title"],
            "type": "HQ",
            "poster": item["poster"],
            "description": item["description"],
            "latest_chapter": item["latest_chapter"] or None,
            "authors": [],
            "genres": item["genres"],
            "status": item["status"],
            "rating": {},
            "languages": [
                {"code": "pt-br", "title": "Portugues (Brasil)", "chapter_count": item["chapter_count"]}
            ],
        }

    def list_issues(self, source_url: str) -> list[dict]:
        _, comic_id, _ = self.parse_source(source_url)
        details = self._details([comic_id])
        if not details:
            raise FileNotFoundError("HQ nao encontrada no HQ Now.")
        return self._sorted_issues(details[0].get("capitulos") or [])

    def get_issue(self, source_url: str) -> tuple[dict, dict, list[str]]:
        kind, comic_id, issue_id = self.parse_source(source_url)
        if kind != "issue" or issue_id is None:
            raise ValueError("Informe endereco da edicao HQ Now.")
        data = self._graphql(
            """
            query Chapter($chapterId: Int!) {
              getChapterById(chapterId: $chapterId) {
                name number oneshot pictures { pictureUrl }
                hq { id name capitulos { id number } }
              }
            }
            """,
            {"chapterId": issue_id},
        )
        chapter = data.get("getChapterById") or {}
        hq = chapter.get("hq") or {}
        if int(hq.get("id") or 0) != comic_id:
            raise FileNotFoundError("Edicao nao pertence a HQ informada.")
        pages = [_https_image(row.get("pictureUrl")) for row in chapter.get("pictures") or []]
        pages = [url for url in pages if url]
        if not pages:
            raise FileNotFoundError("Edicao HQ Now sem paginas.")
        issue = {
            "id": issue_id,
            "number": str(chapter.get("number") or ""),
            "title": _clean(chapter.get("name"), 240),
        }
        comic = {"id": comic_id, "title": _clean(hq.get("name"), 240), "issues": hq.get("capitulos") or []}
        return comic, issue, pages

    def issue_neighbors(self, source_url: str) -> tuple[str | None, str | None]:
        _, comic_id, issue_id = self.parse_source(source_url)
        issues = self.list_issues(self.comic_url(comic_id))
        index = next((i for i, issue in enumerate(issues) if int(issue.get("id") or 0) == issue_id), -1)
        if index < 0:
            return None, None
        previous_url = self.issue_url(comic_id, issues[index - 1]["id"]) if index > 0 else None
        next_url = self.issue_url(comic_id, issues[index + 1]["id"]) if index + 1 < len(issues) else None
        return previous_url, next_url

    @staticmethod
    def _sorted_issues(issues: list[dict]) -> list[dict]:
        return sorted(
            [dict(issue) for issue in issues if isinstance(issue, dict)],
            key=lambda issue: (_number(issue.get("number")) is None, _number(issue.get("number")) or 0, int(issue.get("id") or 0)),
        )
