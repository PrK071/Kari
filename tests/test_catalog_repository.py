from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.persistence.models import Base
from backend.persistence.postgres import PostgresCatalogRepository


def _repository(database_path: Path) -> PostgresCatalogRepository:
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    return PostgresCatalogRepository(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    )


def _hunter(**overrides) -> dict:
    item = {
        "title": "Hunter x Hunter",
        "alternative_titles": ["HUNTER × HUNTER", "HxH"],
        "provider": "mangalivre",
        "source": "MangaLivre",
        "source_url": "https://mangalivre.blog/manga/hunter-x-hunter/",
        "cover_url": "https://example.test/hunter.jpg",
        "genres": ["Aventura"],
        "chapter_count": 420,
        "chapter_count_verified": True,
        "catalog_home_ready": True,
    }
    item.update(overrides)
    return item


def test_catalog_search_matches_exact_alias_and_unicode(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "catalog.sqlite")
    assert repository.upsert_item(_hunter())

    for query in ("hunter x hunter", "HUNTER × HUNTER", "hxh"):
        results = repository.search(query, 10)
        assert len(results) == 1
        assert results[0]["title"] == "Hunter x Hunter"


def test_catalog_upsert_identity_uses_provider_and_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "catalog.sqlite")
    assert repository.upsert_many([
        _hunter(chapter_count=419),
        _hunter(chapter_count=420, source_url="https://mangalivre.blog/manga/hunter-x-hunter"),
        _hunter(
            provider="mangadex",
            source="MangaDex",
            source_url="https://mangadex.org/title/hunter",
        ),
    ]) == 3

    # URL com ou sem slash e a mesma fonte; outro provider continua distinto.
    source = repository.get_by_source(
        "mangalivre", "https://mangalivre.blog/manga/hunter-x-hunter/"
    )
    assert source is not None
    assert source["chapter_count"] == 420
    assert len(repository.search("hunter x hunter", 10)) == 1


def test_catalog_survives_repository_restart_and_drives_home(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite"
    first = _repository(database_path)
    first.upsert_item(_hunter())

    restarted = _repository(database_path)
    assert restarted.search("hunter x hunter", 10)
    assert restarted.list_home(10)[0]["title"] == "Hunter x Hunter"


def test_catalog_index_snapshot_excludes_complementary_payload(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "catalog.sqlite")
    repository.upsert_item(_hunter(description="large complementary value"))

    snapshot = repository.list_index_items()

    assert len(snapshot) == 1
    assert snapshot[0]["canonical_title"] == "Hunter x Hunter"
    assert snapshot[0]["chapter_count"] == 420
    assert "description" not in snapshot[0]
    assert "payload" not in snapshot[0]
