from types import SimpleNamespace

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

    monkeypatch.setattr("mandown.sources.source_webtoons.requests.get", fake_get)

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
        "mandown.sources.source_naver.requests.get",
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
    source_classes = {
        "naver": SimpleNamespace(search=lambda title: []),
        "webtoons": SimpleNamespace(
            search=lambda title: [SourceSearchResult("Found", "https://example.com/found")]
        ),
        "mangadex": SimpleNamespace(search=lambda title: []),
    }
    comic = object()
    monkeypatch.setattr(api, "SEARCH_SOURCES", source_classes)
    monkeypatch.setattr(api, "query", lambda url: comic)

    results = mandown.search(" Found ")

    assert isinstance(results, SearchResults)
    assert results["naver"] is None
    assert results["mangadex"] is None
    assert isinstance(results["webtoons"][0], SearchItem)
    assert results["webtoons"][0].comic is comic
    assert results["webtoons"][0].comic is comic
    assert results.asdict()["webtoons"][0]["title"] == "Found"


def test_search_rejects_empty_title() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        mandown.search("  ")
