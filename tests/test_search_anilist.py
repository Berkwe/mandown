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
                ],
                "naver_url": "https://comic.naver.com/webtoon/list?titleId=123",
            },
        ),
        SourceSearchResult(
            title="한국 만화",
            url="https://anilist.co/manga/456",
            extra={
                "anilist_id": 456,
                "title": {
                    "native": "한국 만화",
                    "english": None,
                },
                "externalLinks": [],
                "naver_url": None,
            },
        ),
    ]
