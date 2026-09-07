from __future__ import annotations

from backend.title_normalization import normalize_match_text, source_identifier


def test_hunter_title_variants_share_one_normalized_value() -> None:
    expected = "hunter x hunter"
    assert normalize_match_text("Hunter x Hunter") == expected
    assert normalize_match_text("HUNTER × HUNTER") == expected
    assert normalize_match_text("  hunter---x  hunter ") == expected


def test_normalization_removes_accents_but_preserves_unicode_letters() -> None:
    assert normalize_match_text("Pokémon") == "pokemon"
    assert normalize_match_text("進撃の巨人") == "進撃の巨人"


def test_source_identifier_is_stable_across_cosmetic_url_differences() -> None:
    assert source_identifier("HTTPS://Example.COM:443/manga/work/?b=2&a=1#top") == (
        "https://example.com/manga/work?a=1&b=2"
    )
