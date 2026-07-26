import pytest
import requests

from mandown import search_anilist
from mandown.sources.base_source import SourceSearchResult


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} response")


def test_anilist_search_returns_titles_links_and_marked_naver_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "Page": {
                "media": [
                    {
                        "id": 123,
                        "idMal": 321,
                        "title": {
                            "native": "신의 탑",
                            "english": "Tower of God",
                        },
                        "externalLinks": [
                            {
                                "site": "Official Site",
                                "url": "https://example.com/tower",
                            },
                            {
                                "site": "Naver Webtoon",
                                "url": "https://comic.naver.com/webtoon/list?titleId=123",
                            },
                            {
                                "site": "WEBTOON",
                                "url": (
                                    "https://www.webtoons.com/id/fantasy/example/"
                                    "list?title_no=94"
                                ),
                            },
                            {
                                "site": "WEBTOON",
                                "url": (
                                    "https://www.webtoons.com/en/fantasy/example/"
                                    "list?title_no=95"
                                ),
                            },
                        ],
                    },
                    {
                        "id": 456,
                        "title": {
                            "native": "한국 만화",
                            "english": None,
                        },
                        "externalLinks": [],
                    },
                ]
            }
        }
    }

    def fake_post(url: str, **kwargs) -> FakeResponse:
        assert url == "https://graphql.anilist.co"
        assert kwargs["json"]["variables"] == {"search": "Tower"}
        query = kwargs["json"]["query"]
        assert "search: $search" in query
        assert "type: MANGA" in query
        assert 'countryOfOrigin: "KR"' in query
        assert "externalLinks" in query
        return FakeResponse(payload)

    monkeypatch.setattr(search_anilist.requests, "post", fake_post)

    assert search_anilist.search("Tower") == [
        SourceSearchResult(
            title="Tower of God",
            url="https://comic.naver.com/webtoon/list?titleId=123",
            extra={
                "anilist_id": 123,
                "mal_id": 321,
                "title": {
                    "native": "신의 탑",
                    "english": "Tower of God",
                },
                "externalLinks": [
                    {
                        "url": "https://example.com/tower",
                        "site": "Official Site",
                    },
                    {
                        "url": "https://comic.naver.com/webtoon/list?titleId=123",
                        "site": "Naver Webtoon",
                    },
                    {
                        "url": (
                            "https://www.webtoons.com/id/fantasy/example/"
                            "list?title_no=94"
                        ),
                        "site": "WEBTOON",
                    },
                    {
                        "url": (
                            "https://www.webtoons.com/en/fantasy/example/"
                            "list?title_no=95"
                        ),
                        "site": "WEBTOON",
                    },
                ],
                "naver_url": "https://comic.naver.com/webtoon/list?titleId=123",
                "webtoons_url": (
                    "https://www.webtoons.com/en/fantasy/example/list?title_no=95"
                ),
            },
            identifiers={
                "anilist_id": 123,
                "mal_id": 321,
                "naver_id": "123",
                "webtoons_id": "95",
                "mangadex_id": None,
            },
        ),
        SourceSearchResult(
            title="한국 만화",
            url="https://anilist.co/manga/456",
            extra={
                "anilist_id": 456,
                "mal_id": None,
                "title": {
                    "native": "한국 만화",
                    "english": None,
                },
                "externalLinks": [],
                "naver_url": None,
                "webtoons_url": None,
            },
            identifiers={
                "anilist_id": 456,
                "mal_id": None,
                "naver_id": None,
                "webtoons_id": None,
                "mangadex_id": None,
            },
        ),
    ]


def test_anilist_webtoons_url_falls_back_when_english_link_is_missing() -> None:
    external_links = [
        {
            "site": "Official Site",
            "url": "https://www.webtoons.com/en/example/list?title_no=1",
        },
        {
            "site": "WEBTOON",
            "url": "https://www.webtoons.com/id/example/list?title_no=2",
        },
        {
            "site": "WEBTOON",
            "url": "https://www.webtoons.com/th/example/list?title_no=3",
        },
    ]

    assert search_anilist._preferred_webtoons_url(external_links) == (
        "https://www.webtoons.com/id/example/list?title_no=2"
    )


def test_anilist_search_rejects_missing_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_anilist.requests, "post", lambda *args, **kwargs: None)

    with pytest.raises(search_anilist.SourceResponseError, match="no response"):
        search_anilist.search("broken")
