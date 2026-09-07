from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import main as kari


@dataclass
class CatalogMigrationReport:
    curated_found: int = 0
    snapshot_found: int = 0
    dataset_groups_found: int = 0
    dataset_groups_skipped: int = 0
    valid_items: int = 0
    upserted: int = 0
    dry_run: bool = False


def _snapshot_items(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog snapshot deve conter um objeto JSON")
    pools = [payload.get("items") or []]
    pools.extend(
        section.get("items") or []
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    )
    return [dict(item) for pool in pools for item in pool if isinstance(item, dict)]


def _dataset_items(path: Path) -> tuple[list[dict], int]:
    """Importa apenas grupos com URL de obra utilizavel; o SQLite nao e promovido."""
    if not path.is_file():
        return [], 0
    with closing(sqlite3.connect(path)) as database:
        rows = database.execute(
            """
            SELECT manga_name, manga_slug, source,
                   COUNT(DISTINCT chapter) AS chapter_count,
                   MAX(scraped_at) AS last_scraped_at
            FROM pages
            GROUP BY manga_name, manga_slug, source
            """
        ).fetchall()
    items: list[dict] = []
    skipped = 0
    for title, slug, source, chapter_count, scraped_at in rows:
        source_url = str(source or "").strip()
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            skipped += 1
            continue
        provider = kari._guess_provider({"url": source_url})
        items.append({
            "title": str(title or slug),
            "provider": provider,
            "source": kari._source_label(provider),
            "source_url": source_url,
            "chapter_count": int(chapter_count or 0),
            "chapter_count_verified": True,
            "dataset_last_scraped_at": str(scraped_at or ""),
        })
    return items, skipped


def collect_catalog_items(
    snapshot_path: Path | None,
    manga_db_path: Path | None,
) -> tuple[list[dict], CatalogMigrationReport]:
    report = CatalogMigrationReport()
    curated = kari._fast_curated_catalog_items()
    report.curated_found = len(curated)
    snapshot = _snapshot_items(snapshot_path) if snapshot_path else []
    report.snapshot_found = len(snapshot)
    dataset, skipped = _dataset_items(manga_db_path) if manga_db_path else ([], 0)
    report.dataset_groups_found = len(dataset) + skipped
    report.dataset_groups_skipped = skipped

    unique: dict[tuple[str, str], dict] = {}
    for raw in [*curated, *snapshot, *dataset]:
        item = kari._normalize_manga_item(raw)
        if not item:
            continue
        provider = str(item.get("provider") or kari._guess_provider(item)).casefold()
        source_url = str(item.get("source_url") or "").strip()
        if not provider or not source_url:
            continue
        unique[(provider, source_url)] = kari._catalog_persistence_item(item)
    report.valid_items = len(unique)
    return list(unique.values()), report


def run(
    *,
    snapshot_path: Path | None,
    manga_db_path: Path | None,
    dry_run: bool,
) -> CatalogMigrationReport:
    items, report = collect_catalog_items(snapshot_path, manga_db_path)
    report.dry_run = dry_run
    if dry_run:
        return report
    if kari.catalog_repository is None:
        raise RuntimeError("KARI_PERSISTENCE_BACKEND=postgres e DATABASE_URL sao obrigatorios")
    report.upserted = kari.catalog_repository.upsert_many(items)
    return report


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="Popula catalog_items de forma explicita e idempotente.",
    )
    parser.add_argument(
        "--catalog-json",
        type=Path,
        default=kari.CATALOG_SNAPSHOT_PATH,
        help="Snapshot legado opcional; usado somente como fonte de ingestao.",
    )
    parser.add_argument(
        "--manga-db",
        type=Path,
        default=Path(__file__).parents[1] / "manga_dataset" / "mangas.db",
        help="SQLite offline opcional; grupos sem URL de obra valida sao ignorados.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            snapshot_path=args.catalog_json,
            manga_db_path=args.manga_db,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps({"ok": True, **asdict(report)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(cli())
