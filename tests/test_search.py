import asyncio
import importlib
import threading

import pytest

import mandown
from mandown import api
from mandown.search import SearchItem, SearchResults
from mandown.sources.base_source import SourceSearchResult
from mandown.sources.source_mangadex import MangaDexSource
from mandown.sources.source_naver import NaverWebtoonSource
from mandown.sources.source_webtoons import WebtoonsSource


class FakeResponse:
    def __init__(self, *, data: dict | None = None, text: str = "", url: str = ""):
        self._data = data or {}
        self.text = text
        self.url = url

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        return None


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
        )
    ]


def test_mangadex_search_parses_api_results(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "data": [
            {
                "id": "manga-id",
                "attributes": {"title": {"en": "Example Manga"}},
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
            "https://mangadex.org/title/manga-id",
            ("Author",),
            "https://uploads.mangadex.org/covers/manga-id/cover.jpg",
        )
    ]


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
    assert results.asdict()["webtoons"][0]["title"] == "Found"


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
