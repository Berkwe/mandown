import asyncio
import importlib
import threading

import pytest
import requests

import mandown
from mandown import api
from mandown.errors import SourceResponseError
from mandown.search import SearchItem, SearchResults
from mandown.search_common import (
    parse_int_identifier,
    parse_mangadex_id,
    parse_naver_id,
    parse_webtoons_id,
)
from mandown.sources.base_source import SourceSearchResult
from mandown.sources.source_mangadex import MangaDexSource
from mandown.sources.source_naver import NaverWebtoonSource
from mandown.sources.source_webtoons import WebtoonsSource

MISSING = object()


class FakeResponse:
    def __init__(
        self,
        *,
        data: object = MISSING,
        text: object = "",
        url: str = "",
    ):
        self._data = {} if data is MISSING else data
        self.text = text
        self.url = url

    def json(self) -> object:
        return self._data

    def raise_for_status(self) -> None:
        return None


class Unparseable:
    def __str__(self) -> str:
        raise RuntimeError("cannot stringify")


@pytest.mark.parametrize(
    "parser",
    [
        parse_int_identifier,
        parse_naver_id,
        parse_webtoons_id,
        parse_mangadex_id,
    ],
)
def test_identifier_parsers_never_raise(parser) -> None:
    assert parser(Unparseable()) is None


def test_webtoons_search_parses_main_search_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <a class="_card_item" href="/en/action/example/list?title_no=42">
      <img src="https://example.com/cover.jpg">
      <strong class="title">Example Toon</strong>
      <div class="author">One / Two</div>
    </a>
    """

    def fake_get(url: str, **kwargs) -> FakeResponse:
        assert url == "https://www.webtoons.com/en/search"
        assert kwargs["params"] == {"keyword": "example"}
        return FakeResponse(text=html, url=f"{url}?keyword=example")

    monkeypatch.setattr("mandown.search_webtoons.requests.get", fake_get)

    assert WebtoonsSource.search("example") == [
        SourceSearchResult(
            "Example Toon",
            "https://www.webtoons.com/en/action/example/list?title_no=42",
            ("One", "Two"),
            "https://example.com/cover.jpg",
            identifiers={
                "anilist_id": None,
                "mal_id": None,
                "naver_id": None,
                "webtoons_id": "42",
                "mangadex_id": None,
            },
        )
    ]


def test_naver_search_parses_webtoon_results(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "searchWebtoonResult": {
            "searchViewList": [
                {
                    "titleId": 123,
                    "titleName": "검색 결과",
                    "thumbnailUrl": "https://example.com/naver.jpg",
                    "communityArtists": [{"name": "작가"}],
                }
            ]
        }
    }
    monkeypatch.setattr(
        "mandown.search_naver.requests.get",
        lambda *args, **kwargs: FakeResponse(data=data),
    )

    assert NaverWebtoonSource.search("검색") == [
        SourceSearchResult(
            "검색 결과",
            "https://m.comic.naver.com/webtoon/list?titleId=123&sortOrder=ASC",
            ("작가",),
            "https://example.com/naver.jpg",
            identifiers={
                "anilist_id": None,
                "mal_id": None,
                "naver_id": "123",
                "webtoons_id": None,
                "mangadex_id": None,
            },
        )
    ]


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"searchWebtoonResult": None},
        {"searchWebtoonResult": {"searchViewList": None}},
    ],
)
def test_naver_search_rejects_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
    data: object,
) -> None:
    monkeypatch.setattr(
        "mandown.search_naver.requests.get",
        lambda *args, **kwargs: FakeResponse(data=data),
    )

    with pytest.raises(SourceResponseError, match="Naver response error"):
        NaverWebtoonSource.search("broken")


def test_webtoons_search_rejects_non_text_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mandown.search_webtoons.requests.get",
        lambda *args, **kwargs: FakeResponse(text=None),
    )

    with pytest.raises(SourceResponseError, match="WEBTOON response error"):
        WebtoonsSource.search("broken")


@pytest.mark.parametrize(
    ("target", "search"),
    [
        ("mandown.search_naver.requests.get", NaverWebtoonSource.search),
        ("mandown.search_webtoons.requests.get", WebtoonsSource.search),
    ],
)
def test_html_and_json_searches_wrap_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    search,
) -> None:
    def fail(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(target, fail)

    with pytest.raises(SourceResponseError, match="HTTP request failed"):
        search("broken")


@pytest.mark.parametrize(
    ("target", "search"),
    [
        ("mandown.search_naver.requests.get", NaverWebtoonSource.search),
        ("mandown.search_webtoons.requests.get", WebtoonsSource.search),
    ],
)
def test_html_and_json_searches_reject_missing_response(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    search,
) -> None:
    monkeypatch.setattr(target, lambda *args, **kwargs: None)

    with pytest.raises(SourceResponseError, match="request returned no response"):
        search("broken")


def test_mangadex_search_parses_api_results(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "data": [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "attributes": {
                    "title": {"en": "Example Manga"},
                    "links": {
                        "al": "123",
                        "mal": "456",
                        "raw": (
                            "https://series.naver.com/comic/detail.series?"
                            "productNo=789"
                        ),
                    },
                },
                "relationships": [
                    {"type": "author", "attributes": {"name": "Author"}},
                    {"type": "artist", "attributes": {"name": "Author"}},
                    {"type": "cover_art", "attributes": {"fileName": "cover.jpg"}},
                ],
            }
        ]
    }
    monkeypatch.setattr(MangaDexSource, "_get", lambda url: FakeResponse(data=data))

    assert MangaDexSource.search("Example Manga") == [
        SourceSearchResult(
            "Example Manga",
            (
                "https://mangadex.org/title/"
                "11111111-1111-4111-8111-111111111111"
            ),
            ("Author",),
            (
                "https://uploads.mangadex.org/covers/"
                "11111111-1111-4111-8111-111111111111/cover.jpg"
            ),
            identifiers={
                "anilist_id": 123,
                "mal_id": 456,
                "naver_id": "789",
                "webtoons_id": None,
                "mangadex_id": "11111111-1111-4111-8111-111111111111",
            },
        )
    ]


def test_mangadex_search_skips_malformed_api_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {
        "data": [
            None,
            [],
            {"attributes": {"title": {"en": "Missing ID"}}},
            {"id": 123, "attributes": {"title": {"en": "Wrong ID"}}},
            {
                "id": "fallback-id",
                "attributes": {"title": []},
                "relationships": {},
            },
            {
                "id": "partial-id",
                "attributes": [],
                "relationships": [
                    None,
                    {"type": "author", "attributes": []},
                    {"type": "artist", "attributes": {"name": 42}},
                    {"type": "cover_art", "attributes": {"fileName": []}},
                ],
            },
        ]
    }
    monkeypatch.setattr(MangaDexSource, "_get", lambda url: FakeResponse(data=data))

    assert MangaDexSource.search("Example Manga") == [
        SourceSearchResult(
            "fallback-id",
            "https://mangadex.org/title/fallback-id",
            identifiers={
                "anilist_id": None,
                "mal_id": None,
                "naver_id": None,
                "webtoons_id": None,
                "mangadex_id": None,
            },
        ),
        SourceSearchResult(
            "partial-id",
            "https://mangadex.org/title/partial-id",
            identifiers={
                "anilist_id": None,
                "mal_id": None,
                "naver_id": None,
                "webtoons_id": None,
                "mangadex_id": None,
            },
        ),
    ]


def test_mangadex_search_wraps_request_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(url: str):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(MangaDexSource, "_get", fail)

    with pytest.raises(SourceResponseError, match="request failed"):
        MangaDexSource.search("broken")


def test_mangadex_search_rejects_missing_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MangaDexSource, "_get", lambda url: None)

    with pytest.raises(SourceResponseError, match="request returned no response"):
        MangaDexSource.search("broken")


@pytest.mark.parametrize("data", [{"data": []}, {"data": {}}, {"data": None}])
def test_mangadex_search_treats_non_array_data_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    data: dict,
) -> None:
    monkeypatch.setattr(MangaDexSource, "_get", lambda url: FakeResponse(data=data))

    assert MangaDexSource.search("Example Manga") == []


def test_search_returns_none_for_empty_catalog_and_lazy_comic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = {
        "naver": lambda title: [],
        "webtoons": lambda title: [
            SourceSearchResult("Found", "https://example.com/found")
        ],
        "mangadex": lambda title: [],
        "anilist": lambda title: pytest.fail(
            "AniList should not run in synchronous default search"
        ),
    }
    comic = object()
    monkeypatch.setattr(api, "SEARCH_PROVIDERS", providers)
    monkeypatch.setattr(api, "query", lambda url: comic)

    results = mandown.search(" Found ")

    assert isinstance(results, SearchResults)
    assert results["naver"] is None
    assert results["mangadex"] is None
    assert results["anilist"] is None
    assert isinstance(results["webtoons"][0], SearchItem)
    assert results["webtoons"][0].comic is comic
    assert results["webtoons"][0].comic is comic
    item = results.asdict()["webtoons"][0]
    assert item["title"] == "Found"
    assert item["identifiers"] == {
        "anilist_id": None,
        "mal_id": None,
        "naver_id": None,
        "webtoons_id": None,
        "mangadex_id": None,
    }
    assert item["urls"] == {
        "naver": None,
        "webtoons": "https://example.com/found",
        "mangadex": None,
        "anilist": None,
    }
    assert item["titles"] == ["Found"]
    assert item["sources"] == ["webtoons"]


def test_search_treats_provider_none_as_no_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        "SEARCH_PROVIDERS",
        {"naver": lambda title: None},
    )

    assert mandown.search("Missing", source="naver")["naver"] is None


def test_search_rejects_invalid_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        "SEARCH_PROVIDERS",
        {"naver": lambda title: {"unexpected": "mapping"}},
    )

    with pytest.raises(SourceResponseError, match="expected a list or None"):
        mandown.search("Broken", source="naver")


def test_search_rejects_empty_title() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        mandown.search("  ")


def test_search_can_query_only_one_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def provider(name: str, results: list[SourceSearchResult]):
        return lambda title: calls.append((name, title)) or results

    monkeypatch.setattr(
        api,
        "SEARCH_PROVIDERS",
        {
            "naver": provider(
                "naver",
                [SourceSearchResult("Naver match", "https://example.com/naver")],
            ),
            "webtoons": provider("webtoons", []),
            "mangadex": provider("mangadex", []),
            "anilist": provider("anilist", []),
        },
    )

    results = mandown.search(" Series ", source=" NAVER ")

    assert calls == [("naver", "Series")]
    assert results["naver"][0].title == "Naver match"
    assert results["webtoons"] is None
    assert results["mangadex"] is None
    assert results["anilist"] is None


def test_search_rejects_unsupported_source() -> None:
    with pytest.raises(ValueError, match="Unsupported search source.*naver"):
        mandown.search("Series", source="unknown")


def test_search_can_query_only_anilist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api,
        "SEARCH_PROVIDERS",
        {
            "naver": lambda title: pytest.fail("Naver should be skipped"),
            "webtoons": lambda title: pytest.fail("WEBTOON should be skipped"),
            "mangadex": lambda title: pytest.fail("MangaDex should be skipped"),
            "anilist": lambda title: [
                SourceSearchResult(
                    "AniList match",
                    "https://comic.naver.com/webtoon/list?titleId=1",
                )
            ],
        },
    )

    results = mandown.search("Series", source="anilist")

    assert results["anilist"][0].title == "AniList match"
    assert results["naver"] is None
    assert results["webtoons"] is None
    assert results["mangadex"] is None


def test_search_all_yields_results_in_completion_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    slow_started = threading.Event()
    release_slow = threading.Event()

    def slow_provider(query: str) -> list[SourceSearchResult]:
        slow_started.set()
        assert release_slow.wait(timeout=5)
        return [SourceSearchResult("Slow", "https://example.com/slow")]

    def fast_provider(query: str) -> list[SourceSearchResult]:
        assert slow_started.wait(timeout=5)
        return [SourceSearchResult("Fast", "https://example.com/fast")]

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {
            "slow": slow_provider,
            "fast": fast_provider,
        },
    )

    async def collect_results() -> list[tuple[str, list[SearchItem]]]:
        batches = []
        async for source, matches in search_module.search_all(" Series "):
            batches.append((source, matches))
            if source == "fast":
                release_slow.set()
        return batches

    batches = asyncio.run(collect_results())

    assert [source for source, _ in batches] == ["fast", "slow"]
    assert batches[0][1][0].title == "Fast"
    assert batches[1][1][0].title == "Slow"


def test_search_all_treats_provider_none_as_empty_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {"empty": lambda query: None},
    )

    async def collect_results() -> list[tuple[str, list[SearchItem]]]:
        return [batch async for batch in search_module.search_all("Missing")]

    assert asyncio.run(collect_results()) == [("empty", [])]


def test_search_all_rejects_invalid_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {"invalid": lambda query: {"unexpected": "mapping"}},
    )

    async def consume() -> None:
        async for _ in search_module.search_all("Broken", retries=0):
            pass

    with pytest.raises(SourceResponseError, match="expected a list or None"):
        asyncio.run(consume())


def test_search_all_deduplicates_into_one_merged_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    mangadex_id = "11111111-1111-4111-8111-111111111111"
    naver_url = "https://comic.naver.com/webtoon/list?titleId=7"
    mangadex_url = f"https://mangadex.org/title/{mangadex_id}"

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {
            "naver": lambda query: [
                SourceSearchResult(
                    "Naver Title",
                    naver_url,
                    identifiers={"naver_id": "7"},
                )
            ],
            "mangadex": lambda query: [
                SourceSearchResult(
                    "MangaDex Title",
                    mangadex_url,
                    identifiers={
                        "anilist_id": 10,
                        "mal_id": 20,
                        "naver_id": "7",
                        "mangadex_id": mangadex_id,
                    },
                ),
                SourceSearchResult(
                    "Unrelated MangaDex Without IDs",
                    (
                        "https://mangadex.org/title/"
                        "22222222-2222-4222-8222-222222222222"
                    ),
                ),
                SourceSearchResult(
                    "Unrelated MangaDex With Nonmatching IDs",
                    (
                        "https://mangadex.org/title/"
                        "33333333-3333-4333-8333-333333333333"
                    ),
                    identifiers={
                        "anilist_id": 999,
                        "mal_id": 998,
                        "naver_id": "997",
                    },
                ),
            ],
            "webtoons": lambda query: [
                SourceSearchResult(
                    "Unlinked One",
                    "https://www.webtoons.com/en/a/list?title_no=1",
                    identifiers={"webtoons_id": "1"},
                ),
                SourceSearchResult(
                    "Unlinked Two",
                    "https://www.webtoons.com/en/b/list?title_no=2",
                    identifiers={"webtoons_id": "2"},
                ),
            ],
        },
    )

    async def collect_results() -> list[tuple[str, list[SearchItem]]]:
        return [
            batch
            async for batch in search_module.search_all(
                "Series",
                deduplicate=True,
            )
        ]

    batches = asyncio.run(collect_results())

    assert len(batches) == 1
    assert batches[0][0] == "merged"
    groups = batches[0][1]
    assert len(groups) == 5

    merged = next(group for group in groups if group.identifiers["naver_id"] == "7")
    assert merged.identifiers == {
        "anilist_id": 10,
        "mal_id": 20,
        "naver_id": "7",
        "webtoons_id": None,
        "mangadex_id": mangadex_id,
    }
    assert merged.urls == {
        "naver": naver_url,
        "webtoons": None,
        "mangadex": mangadex_url,
        "anilist": None,
    }
    assert set(merged.titles) == {"Naver Title", "MangaDex Title"}
    assert set(merged.sources) == {"naver", "mangadex"}
    assert "Unrelated MangaDex Without IDs" not in merged.titles
    assert "Unrelated MangaDex With Nonmatching IDs" not in merged.titles
    assert {group.title for group in groups if group is not merged} == {
        "Unrelated MangaDex Without IDs",
        "Unrelated MangaDex With Nonmatching IDs",
        "Unlinked One",
        "Unlinked Two",
    }


def test_merge_priority_is_anilist_then_mal_then_naver() -> None:
    search_module = importlib.import_module("mandown.search")
    anilist_group = SearchItem(
        "anilist",
        SourceSearchResult(
            "AniList group",
            "https://anilist.co/manga/1",
            identifiers={"anilist_id": 1, "mal_id": 11, "naver_id": "111"},
        ),
    )
    mal_group = SearchItem(
        "mangadex",
        SourceSearchResult(
            "MAL group",
            "https://mangadex.org/title/11111111-1111-4111-8111-111111111111",
            identifiers={"anilist_id": 2, "mal_id": 22, "naver_id": "222"},
        ),
    )
    bridge = SearchItem(
        "naver",
        SourceSearchResult(
            "Bridge",
            "https://comic.naver.com/webtoon/list?titleId=222",
            identifiers={"anilist_id": 1, "mal_id": 22, "naver_id": "222"},
        ),
    )
    groups = [anilist_group, mal_group]

    matched = search_module._merge_item(groups, bridge)

    assert matched is anilist_group
    assert "naver" in anilist_group.sources
    assert mal_group.sources == ["mangadex"]


def test_merge_never_matches_none_identifiers() -> None:
    search_module = importlib.import_module("mandown.search")
    groups: list[SearchItem] = []

    search_module._merge_item(
        groups,
        SearchItem("naver", SourceSearchResult("One", "https://example.com/one")),
    )
    search_module._merge_item(
        groups,
        SearchItem("mangadex", SourceSearchResult("Two", "https://example.com/two")),
    )

    assert len(groups) == 2
    assert all(group.sources for group in groups)


@pytest.mark.parametrize("identifier", ["anilist_id", "mal_id", "naver_id"])
def test_merge_normalizes_identifier_types_before_comparison(identifier: str) -> None:
    search_module = importlib.import_module("mandown.search")
    string_group = SearchItem(
        "mangadex",
        SourceSearchResult(
            "MangaDex title",
            "https://mangadex.org/title/11111111-1111-4111-8111-111111111111",
        ),
    )
    integer_item = SearchItem(
        "anilist",
        SourceSearchResult("AniList title", "https://anilist.co/manga/122082"),
    )
    string_group.identifiers[identifier] = "122082"
    integer_item.identifiers[identifier] = 122082
    groups = [string_group]

    matched = search_module._merge_item(groups, integer_item)

    assert matched is string_group
    assert len(groups) == 1
    assert set(string_group.titles) == {"MangaDex title", "AniList title"}


def test_search_all_uses_anilist_webtoon_external_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")

    def fail_webtoons_search(query: str) -> list[SourceSearchResult]:
        pytest.fail("WEBTOON fallback should not run when AniList has a link")

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {
            "webtoons": fail_webtoons_search,
            "anilist": lambda query: [
                SourceSearchResult(
                    "Tower of God",
                    "https://anilist.co/manga/85143",
                    extra={
                        "anilist_id": 85143,
                        "externalLinks": [
                            {
                                "site": "WEBTOON",
                                "url": (
                                    "https://www.webtoons.com/en/fantasy/"
                                    "tower-of-god/list?title_no=95"
                                ),
                            }
                        ],
                    },
                )
            ],
        },
    )

    async def collect_results() -> list[tuple[str, list[SearchItem]]]:
        return [
            batch
            async for batch in search_module.search_all(
                "Tower of God",
                webtoons_fallback=True,
            )
        ]

    batches = asyncio.run(collect_results())

    assert [source for source, _ in batches] == ["anilist", "webtoons"]
    webtoon = batches[1][1][0]
    assert webtoon.source == "webtoons"
    assert webtoon.title == "Tower of God"
    assert webtoon.url == (
        "https://www.webtoons.com/en/fantasy/tower-of-god/list?title_no=95"
    )
    assert webtoon.extra["anilist_id"] == 85143


def test_search_all_adds_anilist_urls_to_supported_source_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {
            "naver": lambda query: [],
            "mangadex": lambda query: [
                SourceSearchResult(
                    "Existing MangaDex match",
                    "https://mangadex.org/title/existing",
                )
            ],
            "webtoons": lambda query: pytest.fail(
                "WEBTOON fallback should not run"
            ),
            "anilist": lambda query: [
                SourceSearchResult(
                    "무사만리행",
                    "https://series.naver.com/comic/detail.nhn?productNo=5173965",
                    extra={
                        "anilist_id": 123,
                        "externalLinks": [
                            {
                                "site": "MangaDex",
                                "url": "https://mangadex.org/title/existing",
                            },
                            {
                                "site": "MangaDex",
                                "url": "https://mangadex.org/title/from-anilist",
                            },
                            {
                                "site": "WEBTOON",
                                "url": (
                                    "https://www.webtoons.com/en/action/example/"
                                    "list?title_no=42"
                                ),
                            },
                        ],
                    },
                )
            ],
        },
    )

    async def collect_results() -> dict[str, list[SearchItem]]:
        return {
            source: matches
            async for source, matches in search_module.search_all("the long way")
        }

    results = asyncio.run(collect_results())

    assert [match.url for match in results["naver"]] == [
        "https://series.naver.com/comic/detail.nhn?productNo=5173965"
    ]
    assert [match.url for match in results["mangadex"]] == [
        "https://mangadex.org/title/existing",
        "https://mangadex.org/title/from-anilist",
    ]
    assert [match.url for match in results["webtoons"]] == [
        "https://www.webtoons.com/en/action/example/list?title_no=42"
    ]
    assert results["naver"][0].source == "naver"
    assert results["naver"][0].extra["anilist_id"] == 123


def test_search_all_webtoons_fallback_is_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    fallback_queries: list[str] = []

    def webtoons_search(query: str) -> list[SourceSearchResult]:
        fallback_queries.append(query)
        return [
            SourceSearchResult(
                "Fallback match",
                "https://www.webtoons.com/en/list?title_no=1",
            )
        ]

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {
            "webtoons": webtoons_search,
            "anilist": lambda query: [],
        },
    )

    async def collect_results(*, fallback: bool) -> list[tuple[str, list[SearchItem]]]:
        return [
            batch
            async for batch in search_module.search_all(
                " Missing ",
                webtoons_fallback=fallback,
            )
        ]

    default_batches = asyncio.run(collect_results(fallback=False))
    fallback_batches = asyncio.run(collect_results(fallback=True))

    assert default_batches == [("anilist", []), ("webtoons", [])]
    assert fallback_queries == ["Missing"]
    assert fallback_batches[1][0] == "webtoons"
    assert fallback_batches[1][1][0].title == "Fallback match"


def test_search_all_rejects_empty_query() -> None:
    search_module = importlib.import_module("mandown.search")

    async def consume() -> None:
        async for _ in search_module.search_all("  "):
            pass

    with pytest.raises(ValueError, match="cannot be empty"):
        asyncio.run(consume())


def test_search_all_retries_source_response_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    calls = 0

    def flaky_provider(query: str) -> list[SourceSearchResult]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise SourceResponseError(f"temporary failure {calls}")
        return [SourceSearchResult("Recovered", "https://example.com/recovered")]

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {"flaky": flaky_provider},
    )

    async def collect_results() -> list[tuple[str, list[SearchItem]]]:
        return [
            batch
            async for batch in search_module.search_all("Series", retries=2)
        ]

    batches = asyncio.run(collect_results())

    assert calls == 3
    assert batches[0][0] == "flaky"
    assert batches[0][1][0].title == "Recovered"


def test_search_all_raises_last_error_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_module = importlib.import_module("mandown.search")
    errors = [
        SourceResponseError("failure 1"),
        SourceResponseError("failure 2"),
        SourceResponseError("failure 3"),
    ]
    calls = 0

    def failing_provider(query: str) -> list[SourceSearchResult]:
        nonlocal calls
        error = errors[calls]
        calls += 1
        raise error

    monkeypatch.setattr(
        search_module,
        "SEARCH_PROVIDERS",
        {"failing": failing_provider},
    )

    async def consume() -> None:
        async for _ in search_module.search_all("Series", retries=2):
            pass

    with pytest.raises(SourceResponseError) as raised:
        asyncio.run(consume())

    assert calls == 3
    assert raised.value is errors[-1]


@pytest.mark.parametrize("retries", [-1, 1.5, True])
def test_search_all_rejects_invalid_retries(retries: object) -> None:
    search_module = importlib.import_module("mandown.search")

    async def consume() -> None:
        async for _ in search_module.search_all("Series", retries=retries):
            pass

    with pytest.raises(ValueError, match="non-negative integer"):
        asyncio.run(consume())


@pytest.mark.parametrize("deduplicate", [None, 1, "yes"])
def test_search_all_rejects_invalid_deduplicate(deduplicate: object) -> None:
    search_module = importlib.import_module("mandown.search")

    async def consume() -> None:
        async for _ in search_module.search_all(
            "Series",
            deduplicate=deduplicate,
        ):
            pass

    with pytest.raises(ValueError, match="must be a boolean"):
        asyncio.run(consume())
