from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import threading
import time
import unicodedata
import zipfile
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


SUPPORTED_EXTENSIONS = {".epub", ".txt", ".md"}
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_EPUB_MEMBER_BYTES = 25 * 1024 * 1024
MAX_EPUB_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_CHAPTERS = 1000
MAX_TOTAL_CHARACTERS = 30_000_000


def _clean_text(value: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _safe_slug(value: object, fallback: str = "novel") -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64] or fallback


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class _HTMLTextParser(HTMLParser):
    BLOCK_TAGS = {"article", "blockquote", "div", "h1", "h2", "h3", "h4", "li", "p", "section"}
    SKIP_TAGS = {"script", "style", "svg", "nav"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.heading_depth = 0
        self.heading_parts: list[str] = []
        self.first_heading = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3"}:
            self.heading_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")
        if tag in {"h1", "h2", "h3"} and self.heading_depth:
            self.heading_depth -= 1
            if not self.first_heading:
                self.first_heading = _clean_text(" ".join(self.heading_parts), 180)
            self.heading_parts.clear()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.parts.append(data)
        if self.heading_depth:
            self.heading_parts.append(data)

    def result(self) -> tuple[str, str]:
        text = unescape("".join(self.parts)).replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return self.first_heading, text


def _html_to_text(raw: bytes) -> tuple[str, str]:
    parser = _HTMLTextParser()
    parser.feed(_decode_text(raw))
    return parser.result()


def _split_plain_chapters(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    heading_pattern = re.compile(
        r"(?im)^(?:#{1,3}\s+.+|(?:cap[ií]tulo|chapter|pr[oó]logo|prologue|ep[ií]logo|epilogue|volume|interl[uú]dio|interlude)\b[^\n]*)$"
    )
    matches = list(heading_pattern.finditer(text))
    if not matches:
        return [{"title": "Capitulo 1", "content": text}]

    chapters: list[dict] = []
    preface = text[: matches[0].start()].strip()
    if len(preface) >= 80:
        chapters.append({"title": "Introducao", "content": preface})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = re.sub(r"^#{1,3}\s+", "", match.group(0)).strip()
        content = text[match.end() : end].strip()
        if content:
            chapters.append({"title": _clean_text(heading, 180), "content": content})
    return chapters or [{"title": "Capitulo 1", "content": text}]


class LightNovelLocalPlugin:
    provider = "light_novel_local"
    source_label = "Light Novel Local"

    def __init__(self, root: Path | str | None = None) -> None:
        data_root = os.environ.get("KARI_DATA_DIR")
        default_root = (
            Path(data_root) / "light_novel_library"
            if data_root
            else Path(__file__).resolve().parent.parent / "backend" / ".cache" / "light_novel_library"
        )
        self.root = Path(root or default_root).resolve()
        self.novels_root = self.root / "novels"
        self.import_root = self.root / ".imports"
        self.store_path = self.root / "library.json"
        self.lock = threading.RLock()
        self.novels_root.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_source(source_url: str) -> bool:
        return urlparse(str(source_url or "")).scheme.lower() == "light-novel"

    @staticmethod
    def novel_url(novel_id: str) -> str:
        return f"light-novel://novel/{novel_id}"

    @staticmethod
    def chapter_url(novel_id: str, chapter_id: str) -> str:
        return f"light-novel://chapter/{novel_id}/{chapter_id}"

    @staticmethod
    def parse_source(source_url: str) -> tuple[str, str, str | None]:
        parsed = urlparse(str(source_url or ""))
        if parsed.scheme.lower() != "light-novel":
            raise ValueError("Fonte Light Novel Local invalida.")
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc == "novel" and len(parts) == 1:
            return "novel", parts[0], None
        if parsed.netloc == "chapter" and len(parts) == 2:
            return "chapter", parts[0], parts[1]
        raise ValueError("Endereco Light Novel Local invalido.")

    def _empty_library(self) -> dict:
        return {"version": 1, "novels": {}}

    def _load(self) -> dict:
        if not self.store_path.exists():
            return self._empty_library()
        try:
            value = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_library()
        if not isinstance(value, dict) or not isinstance(value.get("novels"), dict):
            return self._empty_library()
        return value

    def _save(self, library: dict) -> None:
        temporary = self.store_path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.store_path)

    def _novel_id(self, title: str) -> str:
        digest = hashlib.sha1(_identity(title).encode("utf-8")).hexdigest()[:10]
        return f"{_safe_slug(title)}-{digest}"

    def _novel_dir(self, novel_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", novel_id):
            raise ValueError("Identificador de novel invalido.")
        return self.novels_root / novel_id

    def catalog_items(self, query: str = "") -> list[dict]:
        wanted = _identity(query)
        with self.lock:
            novels = list(self._load()["novels"].values())
        items = []
        for novel in novels:
            if wanted and wanted not in _identity(novel.get("title")):
                continue
            chapters = novel.get("chapters") or []
            if not chapters:
                continue
            novel_id = str(novel["id"])
            items.append(
                {
                    "id": f"light-novel-local:{novel_id}",
                    "title": novel.get("title") or "Light novel sem titulo",
                    "slug": novel_id,
                    "url": self.novel_url(novel_id),
                    "source_url": self.novel_url(novel_id),
                    "source": self.provider,
                    "provider": self.provider,
                    "section": "Minha biblioteca de Light Novels",
                    "poster": f"/api/light-novels/assets/{novel_id}/cover.webp",
                    "description": novel.get("description") or "",
                    "genres": novel.get("genres") or ["Light Novel"],
                    "authors": [novel["author"]] if novel.get("author") else [],
                    "latest_chapter": str(len(chapters)),
                    "chapter_count": len(chapters),
                    "chapter_preview": [str(chapter.get("number")) for chapter in reversed(chapters[-3:])],
                    "chapter_languages": [novel.get("language") or "pt-br"],
                    "language": novel.get("language") or "pt-br",
                    "updated_at": str(novel.get("updated_at") or ""),
                    "status": "local",
                }
            )
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def get_novel(self, novel_id: str) -> dict:
        with self.lock:
            novel = self._load()["novels"].get(novel_id)
        if not isinstance(novel, dict):
            raise FileNotFoundError("Light novel nao encontrada.")
        return novel

    def manga_metadata(self, source_url: str) -> dict:
        kind, novel_id, _ = self.parse_source(source_url)
        if kind != "novel":
            raise ValueError("Informe endereco da light novel.")
        novel = self.get_novel(novel_id)
        chapters = novel.get("chapters") or []
        return {
            "slug": novel_id,
            "url": self.novel_url(novel_id),
            "title": novel.get("title"),
            "type": "Light Novel",
            "poster": f"/api/light-novels/assets/{novel_id}/cover.webp",
            "description": novel.get("description") or "",
            "latest_chapter": str(len(chapters)),
            "authors": [novel["author"]] if novel.get("author") else [],
            "genres": novel.get("genres") or ["Light Novel"],
            "status": "local",
            "rating": {},
            "languages": [{
                "code": novel.get("language") or "pt-br",
                "title": novel.get("language") or "pt-br",
                "chapter_count": len(chapters),
            }],
        }

    def list_chapters(self, source_url: str) -> list[dict]:
        kind, novel_id, _ = self.parse_source(source_url)
        if kind != "novel":
            raise ValueError("Informe endereco da light novel.")
        return [dict(chapter) for chapter in self.get_novel(novel_id).get("chapters") or []]

    def get_chapter(self, source_url: str) -> tuple[dict, dict, str]:
        kind, novel_id, chapter_id = self.parse_source(source_url)
        if kind != "chapter" or not chapter_id:
            raise ValueError("Informe endereco do capitulo textual.")
        novel = self.get_novel(novel_id)
        chapter = next(
            (item for item in novel.get("chapters") or [] if str(item.get("id")) == chapter_id),
            None,
        )
        if not chapter:
            raise FileNotFoundError("Capitulo da light novel nao encontrado.")
        path = (self._novel_dir(novel_id) / "chapters" / str(chapter["filename"])).resolve()
        try:
            path.relative_to(self.novels_root.resolve())
        except ValueError as exc:
            raise FileNotFoundError("Capitulo fora da biblioteca.") from exc
        if not path.is_file():
            raise FileNotFoundError("Texto do capitulo nao encontrado.")
        return novel, chapter, path.read_text(encoding="utf-8")

    def chapter_neighbors(self, source_url: str) -> tuple[str | None, str | None]:
        _, novel_id, chapter_id = self.parse_source(source_url)
        chapters = self.get_novel(novel_id).get("chapters") or []
        index = next((i for i, chapter in enumerate(chapters) if chapter.get("id") == chapter_id), -1)
        if index < 0:
            return None, None
        previous_url = self.chapter_url(novel_id, chapters[index - 1]["id"]) if index > 0 else None
        next_url = self.chapter_url(novel_id, chapters[index + 1]["id"]) if index + 1 < len(chapters) else None
        return previous_url, next_url

    def resolve_cover(self, novel_id: str) -> Path:
        path = (self._novel_dir(novel_id) / "cover.webp").resolve()
        if not path.is_file():
            raise FileNotFoundError("Capa da light novel nao encontrada.")
        return path

    def import_file(
        self,
        source_path: Path | str,
        original_filename: str,
        title: str = "",
        author: str = "",
        description: str = "",
        language: str = "pt-br",
    ) -> dict:
        source = Path(source_path).resolve()
        extension = Path(original_filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Formato nao suportado. Use EPUB, TXT ou MD.")
        if not source.is_file() or source.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Arquivo ausente ou maior que 60 MB.")

        metadata: dict = {}
        cover_bytes: bytes | None = None
        if extension == ".epub":
            metadata, chapters, cover_bytes = self._read_epub(source)
        else:
            text = _decode_text(source.read_bytes())
            chapters = _split_plain_chapters(text)

        novel_title = _clean_text(title or metadata.get("title") or Path(original_filename).stem, 180)
        if not novel_title:
            raise ValueError("Titulo da light novel ficou vazio.")
        if not chapters:
            raise ValueError("Arquivo nao possui capitulos textuais.")
        if len(chapters) > MAX_CHAPTERS:
            raise ValueError(f"Light novel excede limite de {MAX_CHAPTERS} capitulos.")
        total_characters = sum(len(str(chapter.get("content") or "")) for chapter in chapters)
        if total_characters > MAX_TOTAL_CHARACTERS:
            raise ValueError("Light novel excede limite de 30 milhoes de caracteres.")

        novel_id = self._novel_id(novel_title)
        stage = self.import_root / uuid4().hex
        chapters_dir = stage / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=False)
        chapter_rows = []
        try:
            for index, chapter in enumerate(chapters, start=1):
                chapter_id = f"chapter-{index:04d}"
                filename = f"{chapter_id}.txt"
                content = str(chapter.get("content") or "").strip()
                if not content:
                    continue
                (chapters_dir / filename).write_text(content, encoding="utf-8")
                chapter_rows.append({
                    "id": chapter_id,
                    "number": index,
                    "title": _clean_text(chapter.get("title") or f"Capitulo {index}", 180),
                    "filename": filename,
                    "characters": len(content),
                })
            if not chapter_rows:
                raise ValueError("Arquivo nao possui texto legivel.")

            if cover_bytes:
                self._write_cover(cover_bytes, stage / "cover.webp")
            else:
                self._generate_cover(novel_title, stage / "cover.webp")

            final_dir = self._novel_dir(novel_id)
            if final_dir.exists():
                shutil.rmtree(final_dir)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            stage.replace(final_dir)

            now = time.time()
            novel = {
                "id": novel_id,
                "title": novel_title,
                "author": _clean_text(author or metadata.get("author"), 180),
                "description": _clean_text(description or metadata.get("description"), 4000),
                "genres": metadata.get("genres") or ["Light Novel"],
                "language": _clean_text(language or metadata.get("language") or "pt-br", 20).lower(),
                "chapters": chapter_rows,
                "filename": Path(original_filename).name,
                "created_at": now,
                "updated_at": now,
            }
            with self.lock:
                library = self._load()
                library["novels"][novel_id] = novel
                self._save(library)
            return self.catalog_items(query=novel_title)[0]
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def delete_novel(self, novel_id: str) -> None:
        with self.lock:
            library = self._load()
            if novel_id not in library["novels"]:
                raise FileNotFoundError("Light novel nao encontrada.")
            del library["novels"][novel_id]
            self._save(library)
        shutil.rmtree(self._novel_dir(novel_id), ignore_errors=True)

    def _read_epub(self, source: Path) -> tuple[dict, list[dict], bytes | None]:
        try:
            archive = zipfile.ZipFile(source)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("EPUB invalido.") from exc
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if sum(max(0, info.file_size) for info in infos) > MAX_EPUB_UNCOMPRESSED_BYTES:
                raise ValueError("EPUB excede limite descompactado de 500 MB.")
            if any(info.file_size > MAX_EPUB_MEMBER_BYTES for info in infos):
                raise ValueError("EPUB contem arquivo interno maior que 25 MB.")
            names = {info.filename for info in infos}
            try:
                container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
                rootfile = next(
                    element.attrib.get("full-path", "")
                    for element in container.iter()
                    if element.tag.rsplit("}", 1)[-1] == "rootfile"
                )
            except Exception as exc:
                raise ValueError("EPUB sem container/OPF valido.") from exc
            if rootfile not in names:
                raise ValueError("Arquivo OPF do EPUB nao encontrado.")
            opf = ElementTree.fromstring(archive.read(rootfile))
            metadata_node = next((node for node in opf.iter() if node.tag.rsplit("}", 1)[-1] == "metadata"), None)
            metadata = self._epub_metadata(metadata_node)
            manifest: dict[str, dict] = {}
            spine_ids: list[str] = []
            cover_id = ""
            for node in opf.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "item" and node.attrib.get("id"):
                    manifest[node.attrib["id"]] = dict(node.attrib)
                    if "cover-image" in node.attrib.get("properties", "").split():
                        cover_id = node.attrib["id"]
                elif tag == "itemref" and node.attrib.get("idref"):
                    spine_ids.append(node.attrib["idref"])
                elif tag == "meta" and node.attrib.get("name") == "cover":
                    cover_id = node.attrib.get("content", cover_id)

            base = posixpath.dirname(rootfile)
            chapters = []
            for item_id in spine_ids:
                item = manifest.get(item_id) or {}
                if "nav" in item.get("properties", "").split():
                    continue
                member = self._epub_member(base, item.get("href", ""), names)
                if not member or item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                    continue
                heading, content = _html_to_text(archive.read(member))
                if len(content) < 80:
                    continue
                chapters.append({"title": heading or f"Capitulo {len(chapters) + 1}", "content": content})

            cover_bytes = None
            cover_item = manifest.get(cover_id) if cover_id else None
            if cover_item:
                cover_member = self._epub_member(base, cover_item.get("href", ""), names)
                if cover_member:
                    cover_bytes = archive.read(cover_member)
            return metadata, chapters, cover_bytes

    @staticmethod
    def _epub_member(base: str, href: str, names: set[str]) -> str:
        href = unquote(str(href or "").split("#", 1)[0])
        member = posixpath.normpath(posixpath.join(base, href)).lstrip("/")
        if not href or member.startswith("../") or member not in names:
            return ""
        return member

    @staticmethod
    def _epub_metadata(metadata_node) -> dict:
        values: dict[str, list[str]] = {}
        if metadata_node is not None:
            for child in metadata_node:
                tag = child.tag.rsplit("}", 1)[-1].lower()
                text = _clean_text(child.text, 4000)
                if text:
                    values.setdefault(tag, []).append(text)
        return {
            "title": (values.get("title") or [""])[0],
            "author": (values.get("creator") or [""])[0],
            "description": (values.get("description") or [""])[0],
            "language": (values.get("language") or [""])[0],
            "genres": values.get("subject") or ["Light Novel"],
        }

    @staticmethod
    def _write_cover(raw: bytes, target: Path) -> None:
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                opened.load()
                cover = opened.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError("Capa interna do EPUB e invalida.") from exc
        try:
            cover.thumbnail((900, 1350), Image.Resampling.LANCZOS)
            cover.save(target, format="WEBP", quality=92, method=5)
        finally:
            cover.close()

    @staticmethod
    def _generate_cover(title: str, target: Path) -> None:
        cover = Image.new("RGB", (720, 1080), "#101516")
        draw = ImageDraw.Draw(cover)
        draw.rectangle((0, 0, 18, 1080), fill="#6ee7b7")
        draw.rectangle((62, 74, 658, 78), fill="#33413e")
        font_paths = (
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
        )
        font_path = next((path for path in font_paths if path.exists()), None)
        font_size = 58
        if font_path:
            while font_size > 30:
                candidate_font = ImageFont.truetype(str(font_path), font_size)
                if all(draw.textlength(word, font=candidate_font) <= 570 for word in title.split()):
                    break
                font_size -= 4
            font = ImageFont.truetype(str(font_path), font_size)
        else:
            font = ImageFont.load_default()
        small = ImageFont.truetype(str(font_path), 28) if font_path else ImageFont.load_default()
        words = title.split()
        lines: list[str] = []
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= 570:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        y = 155
        for line in lines[:8]:
            draw.text((72, y), line, font=font, fill="#f4f4f5")
            y += int(font_size * 1.32)
        draw.text((72, 955), "LIGHT NOVEL", font=small, fill="#6ee7b7")
        cover.save(target, format="WEBP", quality=92, method=5)
        cover.close()
