import asyncio
import importlib
import sys

import pytest

import mandown
from mandown.anilist import (
    AniListClient,
    AniListExternalLink,
    AniListManga,
    AniListTitle,
)
from mandown.search import SearchItem, SearchResults
from mandown.sources.source_mangadex import MangaDexSource
from mandown.sources.source_naver import NaverWebtoonSource
from mandown.sources.source_webtoons import WebtoonsSource


def downloadable_manga() -> AniListManga:
    return AniListManga(
        id=105398,
        id_mal=147863,
        title=AniListTitle(
            romaji="Na Honjaman Level Up",
            english="Solo Leveling",
            native="나 혼자만 레벨업",
        ),
        site_url="https://anilist.co/manga/105398",
        external_links=(
            AniListExternalLink(
                site="WEBTOON",
                url="https://www.webtoons.com/en/action/example/list?title_no=10",
                type="STREAMING",
                language="English",
            ),
        ),
    )


def test_legacy_provider_search_modules_are_not_active() -> None:
    search_module = importlib.import_module("mandown.search")

    assert not hasattr(search_module, "SEARCH_PROVIDERS")
    assert "mandown.legacy.search.naver" not in sys.modules
    assert "mandown.legacy.search.webtoons" not in sys.modules
    assert "mandown.legacy.search.mangadex" not in sys.modules
    assert not hasattr(NaverWebtoonSource, "search")
    assert not hasattr(WebtoonsSource, "search")
    assert not hasattr(MangaDexSource, "search")


def test_deprecated_search_keeps_mapping_and_routes_only_to_anilist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    client = AniListClient(session=object())
    item = search_module._to_search_item(client, downloadable_manga())

    async def fake_search_items(query: str) -> list[SearchItem]:
        assert query == "Solo Leveling"
        return [item]

    monkeypatch.setattr(search_module, "_search_items", fake_search_items)

    with pytest.warns(DeprecationWarning):
        results = mandown.search(" Solo Leveling ")

    assert isinstance(results, SearchResults)
    assert results["naver"] is None
    assert results["webtoons"] is None
    assert results["mangadex"] is None
    assert results["anilist"][0].url.endswith("title_no=10")


@pytest.mark.parametrize("source", ["naver", "webtoons", "mangadex"])
def test_deprecated_search_rejects_retired_source_filters(source: str) -> None:
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="no longer active"):
        mandown.search("Title", source=source)


def test_deprecated_search_rejects_running_event_loop() -> None:
    async def exercise() -> None:
        with pytest.warns(DeprecationWarning), pytest.raises(
            RuntimeError, match="active event loop"
        ):
            mandown.search("Title")

    asyncio.run(exercise())


def test_search_all_yields_one_anilist_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    search_module = importlib.import_module("mandown.search")

    async def fake_search_items(query: str) -> list[SearchItem]:
        assert query == "Title"
        return []

    monkeypatch.setattr(search_module, "_search_items", fake_search_items)
    async def collect() -> list:
        batches = []
        with pytest.warns(DeprecationWarning):
            async for batch in mandown.search_all("Title"):
                batches.append(batch)
        return batches

    assert asyncio.run(collect()) == [("anilist", [])]


def test_search_all_rejects_retired_options() -> None:
    async def collect() -> None:
        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="retired"):
            async for _ in mandown.search_all("Title", deduplicate=True):
                pass

    asyncio.run(collect())


def test_anilist_only_item_without_supported_url_has_controlled_comic_error() -> None:
    search_module = importlib.import_module("mandown.search")
    client = AniListClient(session=object())
    item = search_module._to_search_item(
        client,
        AniListManga(
            id=1,
            title=AniListTitle(english="Catalog only"),
            site_url="https://anilist.co/manga/1",
        ),
    )

    with pytest.raises(ValueError, match="no Mandown-supported external source"):
        _ = item.comic


def test_search_item_serialization_remains_available() -> None:
    search_module = importlib.import_module("mandown.search")
    item = search_module._to_search_item(
        AniListClient(session=object()),
        downloadable_manga(),
    )

    serialized = item.asdict()

    assert serialized["source"] == "anilist"
    assert serialized["identifiers"]["anilist_id"] == 105398
    assert serialized["urls"]["webtoons"].endswith("title_no=10")
