from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import unquote, urlparse
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

try:
    import fitz
except Exception:  # PyMuPDF e opcional ate o usuario importar um PDF.
    fitz = None

try:
    import rarfile
except Exception:  # rarfile e opcional ate o usuario importar um CBR.
    rarfile = None


IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
ARCHIVE_EXTENSIONS = {".cbz", ".zip", ".cbr"}
SUPPORTED_EXTENSIONS = ARCHIVE_EXTENSIONS | {".pdf"}
MAX_UPLOAD_BYTES = 350 * 1024 * 1024
MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1536 * 1024 * 1024
MAX_PAGES = 800
MAX_PAGE_DIMENSION = 5000
MAX_PAGE_PIXELS = 100_000_000
PDF_DPI = 170


def _clean_text(value: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _safe_slug(value: object, fallback: str = "hq") -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64] or fallback


def _natural_key(value: object) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]


def _number_value(value: object) -> float | None:
    match = re.search(r"\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _display_number(value: object) -> str:
    text = _clean_text(value, 40)
    number = _number_value(text)
    if number is None:
        return text or "1"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _infer_title_and_issue(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem.replace("_", " ").strip()
    match = re.match(
        r"^(.*?)(?:\s*[-–—]?\s*(?:#|issue\s*|ed(?:icao|ição)\s*|vol(?:ume)?\.?\s*|cap(?:itulo|ítulo)?\.?\s*))"
        r"(\d+(?:[.,]\d+)?)\s*$",
        stem,
        flags=re.IGNORECASE,
    )
    if not match:
        return stem or "HQ sem titulo", "1"
    title = match.group(1).strip(" -–—") or stem
    return title, _display_number(match.group(2))


class HQLocalPlugin:
    """Biblioteca local de HQs importadas pelo leitor."""

    provider = "hq_local"
    source_label = "HQ Local"

    def __init__(self, root: Path | str | None = None) -> None:
        data_root = os.environ.get("KARI_DATA_DIR")
        default_root = (
            Path(data_root) / "hq_library"
            if data_root
            else Path(__file__).resolve().parent.parent / "backend" / ".cache" / "hq_library"
        )
        self.root = Path(root or default_root).resolve()
        self.comics_root = self.root / "comics"
        self.import_root = self.root / ".imports"
        self.store_path = self.root / "library.json"
        self.lock = threading.RLock()
        self.comics_root.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)
        self._configure_rar_tool()

    @staticmethod
    def is_source(source_url: str) -> bool:
        return urlparse(str(source_url or "")).scheme.lower() == "hq-local"

    @staticmethod
    def comic_url(comic_id: str) -> str:
        return f"hq-local://comic/{comic_id}"

    @staticmethod
    def issue_url(comic_id: str, issue_id: str) -> str:
        return f"hq-local://issue/{comic_id}/{issue_id}"

    @staticmethod
    def page_url(path: Path) -> str:
        return path.resolve().as_uri().replace("file:", "hqfile:", 1)

    def resolve_page_url(self, page_url: str) -> Path:
        parsed = urlparse(str(page_url or ""))
        if parsed.scheme.lower() != "hqfile":
            raise FileNotFoundError("Pagina local invalida.")
        raw_path = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        path = Path(raw_path).resolve()
        try:
            path.relative_to(self.comics_root.resolve())
        except ValueError as exc:
            raise FileNotFoundError("Pagina fora da biblioteca de HQs.") from exc
        if not path.is_file() or not re.fullmatch(r"page-\d{4}\.webp", path.name):
            raise FileNotFoundError("Pagina local nao encontrada.")
        return path

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str, str | None]:
        parsed = urlparse(str(source_url or ""))
        if parsed.scheme.lower() != "hq-local":
            raise ValueError("Fonte HQ Local invalida.")
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc == "comic" and len(parts) == 1:
            return "comic", parts[0], None
        if parsed.netloc == "issue" and len(parts) == 2:
            return "issue", parts[0], parts[1]
        raise ValueError("Endereco HQ Local invalido.")

    def _configure_rar_tool(self) -> None:
        if rarfile is None:
            return
        candidates = (
            Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
            Path(r"C:\Program Files\WinRAR\WinRAR.exe"),
        )
        for candidate in candidates:
            if candidate.exists():
                rarfile.UNRAR_TOOL = str(candidate)
                return

    def _empty_library(self) -> dict:
        return {"version": 1, "comics": {}}

    def _load(self) -> dict:
        if not self.store_path.exists():
            return self._empty_library()
        try:
            value = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_library()
        if not isinstance(value, dict) or not isinstance(value.get("comics"), dict):
            return self._empty_library()
        return value

    def _save(self, library: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.store_path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(library, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.store_path)

    def _comic_id(self, title: str) -> str:
        digest = hashlib.sha1(_identity(title).encode("utf-8")).hexdigest()[:10]
        return f"{_safe_slug(title)}-{digest}"

    def catalog_items(self, query: str = "") -> list[dict]:
        wanted = _identity(query)
        with self.lock:
            comics = list(self._load()["comics"].values())
        items: list[dict] = []
        for comic in comics:
            if wanted and wanted not in _identity(comic.get("title")):
                continue
            issues = self._sorted_issues(comic.get("issues") or [])
            if not issues:
                continue
            latest = issues[-1]
            newest = max(issues, key=lambda item: float(item.get("created_at") or 0))
            comic_id = str(comic["id"])
            issue_id = str(newest["id"])
            items.append(
                {
                    "id": f"hq-local:{comic_id}",
                    "title": comic.get("title") or "HQ sem titulo",
                    "slug": comic_id,
                    "url": self.comic_url(comic_id),
                    "source_url": self.comic_url(comic_id),
                    "source": self.provider,
                    "provider": self.provider,
                    "section": "Minha biblioteca de HQs",
                    "poster": f"/api/hq/assets/{comic_id}/{issue_id}/page-0001.webp",
                    "description": comic.get("description") or "",
                    "genres": comic.get("genres") or ["HQ"],
                    "authors": comic.get("authors") or [],
                    "latest_chapter": _display_number(latest.get("number")),
                    "chapter_count": len(issues),
                    "chapter_preview": [
                        _display_number(issue.get("number")) for issue in reversed(issues[-3:])
                    ],
                    "chapter_languages": ["pt-br"],
                    "language": "pt-br",
                    "updated_at": str(comic.get("updated_at") or ""),
                    "status": "local",
                }
            )
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def manga_metadata(self, source_url: str) -> dict:
        kind, comic_id, _ = self.parse_source(source_url)
        if kind != "comic":
            raise ValueError("Informe endereco da HQ, nao da edicao.")
        comic = self.get_comic(comic_id)
        issues = self._sorted_issues(comic.get("issues") or [])
        poster = ""
        if issues:
            newest = max(issues, key=lambda item: float(item.get("created_at") or 0))
            poster = f"/api/hq/assets/{comic_id}/{newest['id']}/page-0001.webp"
        return {
            "slug": comic_id,
            "url": self.comic_url(comic_id),
            "title": comic.get("title") or "HQ sem titulo",
            "type": "HQ",
            "poster": poster,
            "description": comic.get("description") or "",
            "latest_chapter": _display_number(issues[-1].get("number")) if issues else None,
            "authors": comic.get("authors") or [],
            "genres": comic.get("genres") or ["HQ"],
            "status": "local",
            "rating": {},
            "languages": [
                {"code": "pt-br", "title": "Portugues (Brasil)", "chapter_count": len(issues)}
            ],
        }

    def get_comic(self, comic_id: str) -> dict:
        with self.lock:
            comic = self._load()["comics"].get(comic_id)
        if not isinstance(comic, dict):
            raise FileNotFoundError("HQ nao encontrada na biblioteca local.")
        return comic

    def list_issues(self, source_url: str) -> list[dict]:
        kind, comic_id, _ = self.parse_source(source_url)
        if kind != "comic":
            raise ValueError("Informe endereco da HQ.")
        comic = self.get_comic(comic_id)
        return self._sorted_issues(comic.get("issues") or [])

    def get_issue(self, source_url: str) -> tuple[dict, dict, list[Path]]:
        kind, comic_id, issue_id = self.parse_source(source_url)
        if kind != "issue" or not issue_id:
            raise ValueError("Informe endereco da edicao da HQ.")
        comic = self.get_comic(comic_id)
        issue = next(
            (item for item in comic.get("issues") or [] if str(item.get("id")) == issue_id),
            None,
        )
        if not issue:
            raise FileNotFoundError("Edicao nao encontrada na biblioteca local.")
        issue_dir = self._issue_dir(comic_id, issue_id)
        pages = sorted(issue_dir.glob("page-*.webp"), key=lambda path: _natural_key(path.name))
        if not pages:
            raise FileNotFoundError("Edicao local nao possui paginas.")
        return comic, issue, pages

    def issue_neighbors(self, source_url: str) -> tuple[str | None, str | None]:
        _, comic_id, issue_id = self.parse_source(source_url)
        issues = self._sorted_issues(self.get_comic(comic_id).get("issues") or [])
        index = next((i for i, issue in enumerate(issues) if issue.get("id") == issue_id), -1)
        if index < 0:
            return None, None
        previous_url = self.issue_url(comic_id, issues[index - 1]["id"]) if index > 0 else None
        next_url = self.issue_url(comic_id, issues[index + 1]["id"]) if index + 1 < len(issues) else None
        return previous_url, next_url

    def resolve_asset(self, comic_id: str, issue_id: str, filename: str) -> Path:
        if not re.fullmatch(r"page-\d{4}\.webp", filename):
            raise FileNotFoundError("Arquivo de HQ invalido.")
        path = (self._issue_dir(comic_id, issue_id) / filename).resolve()
        root = self.comics_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise FileNotFoundError("Arquivo fora da biblioteca de HQs.") from exc
        if not path.is_file():
            raise FileNotFoundError("Pagina da HQ nao encontrada.")
        return path

    def import_file(
        self,
        source_path: Path | str,
        original_filename: str,
        title: str = "",
        issue_number: str = "",
        description: str = "",
    ) -> dict:
        source = Path(source_path).resolve()
        extension = Path(original_filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Formato nao suportado. Use CBZ, ZIP, CBR ou PDF.")
        if not source.is_file():
            raise FileNotFoundError("Arquivo enviado nao encontrado.")
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("HQ excede limite de 350 MB.")

        inferred_title, inferred_issue = _infer_title_and_issue(original_filename)
        comic_title = _clean_text(title or inferred_title, 180) or "HQ sem titulo"
        number = _display_number(issue_number or inferred_issue)
        comic_id = self._comic_id(comic_title)
        issue_seed = f"{_identity(number)}:{source.stat().st_size}:{original_filename}"
        issue_id = f"ed-{_safe_slug(number, '1')}-{hashlib.sha1(issue_seed.encode('utf-8')).hexdigest()[:8]}"
        stage = self.import_root / uuid4().hex
        pages_stage = stage / "pages"
        pages_stage.mkdir(parents=True, exist_ok=False)

        try:
            if extension == ".pdf":
                page_count = self._import_pdf(source, pages_stage)
            elif extension in {".cbz", ".zip"}:
                page_count = self._import_zip(source, pages_stage)
            else:
                page_count = self._import_rar(source, pages_stage)
            if page_count <= 0:
                raise ValueError("Arquivo nao contem paginas de imagem.")

            final_dir = self._issue_dir(comic_id, issue_id)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_dir.exists():
                shutil.rmtree(final_dir)
            pages_stage.replace(final_dir)

            now = time.time()
            with self.lock:
                library = self._load()
                comic = library["comics"].get(comic_id) or {
                    "id": comic_id,
                    "title": comic_title,
                    "description": "",
                    "authors": [],
                    "genres": ["HQ"],
                    "created_at": now,
                    "issues": [],
                }
                if description.strip():
                    comic["description"] = _clean_text(description, 4000)
                comic["title"] = comic_title
                comic["updated_at"] = now
                replaced_issue_ids = [
                    str(existing.get("id"))
                    for existing in (comic.get("issues") or [])
                    if _identity(existing.get("number")) == _identity(number)
                    and str(existing.get("id")) != issue_id
                ]
                comic["issues"] = [
                    existing for existing in (comic.get("issues") or [])
                    if _identity(existing.get("number")) != _identity(number)
                ]
                comic["issues"].append(
                    {
                        "id": issue_id,
                        "title": f"Edicao {number}",
                        "number": number,
                        "filename": Path(original_filename).name,
                        "page_count": page_count,
                        "created_at": now,
                    }
                )
                library["comics"][comic_id] = comic
                self._save(library)
            for replaced_id in replaced_issue_ids:
                shutil.rmtree(self._issue_dir(comic_id, replaced_id), ignore_errors=True)
            return self.catalog_items(query=comic_title)[0]
        except Exception:
            issue_dir = self._issue_dir(comic_id, issue_id)
            if issue_dir.exists() and not self._issue_is_registered(comic_id, issue_id):
                shutil.rmtree(issue_dir, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def delete_comic(self, comic_id: str) -> None:
        with self.lock:
            library = self._load()
            if comic_id not in library["comics"]:
                raise FileNotFoundError("HQ nao encontrada.")
            del library["comics"][comic_id]
            self._save(library)
        shutil.rmtree(self.comics_root / comic_id, ignore_errors=True)

    def _issue_is_registered(self, comic_id: str, issue_id: str) -> bool:
        try:
            comic = self.get_comic(comic_id)
        except FileNotFoundError:
            return False
        return any(str(issue.get("id")) == issue_id for issue in comic.get("issues") or [])

    def _issue_dir(self, comic_id: str, issue_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", comic_id) or not re.fullmatch(r"[a-z0-9-]+", issue_id):
            raise ValueError("Identificador de HQ invalido.")
        return self.comics_root / comic_id / "issues" / issue_id

    def _sorted_issues(self, issues: list[dict]) -> list[dict]:
        return sorted(
            [dict(issue) for issue in issues if isinstance(issue, dict)],
            key=lambda issue: (
                _number_value(issue.get("number")) is None,
                _number_value(issue.get("number")) or 0,
                _natural_key(issue.get("number") or issue.get("title") or ""),
            ),
        )

    def _write_page(self, raw: bytes, target: Path) -> None:
        if not raw or len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Pagina vazia ou maior que 40 MB.")
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                if opened.width * opened.height > MAX_PAGE_PIXELS:
                    raise ValueError("Pagina excede limite de 100 megapixels.")
                opened.load()
                page = opened.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError("Pagina de imagem invalida.") from exc
        try:
            if max(page.size) > MAX_PAGE_DIMENSION:
                page.thumbnail((MAX_PAGE_DIMENSION, MAX_PAGE_DIMENSION), Image.Resampling.LANCZOS)
            page.save(target, format="WEBP", quality=93, method=5)
        finally:
            page.close()

    def _import_streams(
        self,
        entries: list[tuple[str, int, Callable[[], BinaryIO]]],
        target_dir: Path,
    ) -> int:
        image_entries = [entry for entry in entries if Path(entry[0]).suffix.lower() in IMAGE_EXTENSIONS]
        image_entries.sort(key=lambda entry: _natural_key(entry[0]))
        if len(image_entries) > MAX_PAGES:
            raise ValueError(f"HQ excede limite de {MAX_PAGES} paginas.")
        total = sum(max(0, int(entry[1] or 0)) for entry in image_entries)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("HQ excede limite descompactado de 1,5 GB.")
        for index, (_, declared_size, opener) in enumerate(image_entries, start=1):
            if declared_size > MAX_PAGE_BYTES:
                raise ValueError("HQ contem pagina maior que 40 MB.")
            with opener() as stream:
                raw = stream.read(MAX_PAGE_BYTES + 1)
            self._write_page(raw, target_dir / f"page-{index:04d}.webp")
        return len(image_entries)

    def _import_zip(self, source: Path, target_dir: Path) -> int:
        try:
            archive = zipfile.ZipFile(source)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("CBZ/ZIP invalido.") from exc
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if any(info.flag_bits & 0x1 for info in infos):
                raise ValueError("CBZ/ZIP protegido por senha nao e suportado.")
            entries = [
                (info.filename, info.file_size, lambda info=info: archive.open(info, "r"))
                for info in infos
            ]
            return self._import_streams(entries, target_dir)

    def _import_rar(self, source: Path, target_dir: Path) -> int:
        if rarfile is None:
            raise RuntimeError("Suporte CBR ausente. Instale dependencia rarfile.")
        try:
            archive = rarfile.RarFile(source)
            infos = [info for info in archive.infolist() if not info.isdir()]
            entries = [
                (info.filename, info.file_size, lambda info=info: archive.open(info))
                for info in infos
            ]
            with archive:
                return self._import_streams(entries, target_dir)
        except rarfile.Error as exc:
            raise ValueError(f"CBR invalido ou indisponivel: {exc}") from exc

    def _import_pdf(self, source: Path, target_dir: Path) -> int:
        if fitz is None:
            raise RuntimeError("Suporte PDF ausente. Instale dependencia PyMuPDF.")
        try:
            document = fitz.open(source)
        except Exception as exc:
            raise ValueError("PDF invalido.") from exc
        with document:
            if document.needs_pass:
                raise ValueError("PDF protegido por senha nao e suportado.")
            if document.page_count > MAX_PAGES:
                raise ValueError(f"HQ excede limite de {MAX_PAGES} paginas.")
            for index, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(dpi=PDF_DPI, alpha=False)
                raw = pixmap.tobytes("png")
                self._write_page(raw, target_dir / f"page-{index:04d}.webp")
            return document.page_count
