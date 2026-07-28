import asyncio

import pytest
import requests

from mandown.anilist import (
    ANILIST_MAX_PER_PAGE,
    DEFAULT_PER_PAGE,
    AniListClient,
    AniListExternalLink,
    AniListFieldSet,
    AniListManga,
    extract_supported_sources,
)
from mandown.errors import (
    AniListGraphQLResponseError,
    AniListHTTPError,
    AniListNetworkError,
    AniListRateLimitError,
    AniListResponseError,
    AniListTimeoutError,
)


class FakeResponse:
    def __init__(
        self,
        payload: object = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


def page_payload(media: list[dict] | None = None) -> dict:
    return {
        "data": {
            "Page": {
                "pageInfo": {
                    "currentPage": 1,
                    "lastPage": 3,
                    "hasNextPage": True,
                    "perPage": 10,
                    "total": 27,
                },
                "media": media
                if media is not None
                else [
                    {
                        "id": 105398,
                        "idMal": 147863,
                        "title": {
                            "romaji": "Na Honjaman Level Up",
                            "english": "Solo Leveling",
                            "native": "나 혼자만 레벨업",
                        },
                        "synonyms": ["I Level Up Alone"],
                        "format": "MANGA",
                        "status": "FINISHED",
                        "countryOfOrigin": "KR",
                        "popularity": 100,
                        "averageScore": 84,
                        "coverImage": {
                            "extraLarge": "https://img/extra.jpg",
                            "large": "https://img/large.jpg",
                            "medium": "https://img/medium.jpg",
                            "color": "#123456",
                        },
                        "siteUrl": "https://anilist.co/manga/105398",
                    }
                ],
            }
        }
    }


def test_lightweight_search_uses_page_media_variables_and_card_fields() -> None:
    session = FakeSession([FakeResponse(page_payload())])
    client = AniListClient(session=session)

    response = asyncio.run(client.search_manga(" Solo Leveling "))

    _, kwargs = session.calls[0]
    query = kwargs["json"]["query"]
    assert "Page(page: $page, perPage: $perPage)" in query
    assert "media(search: $search, type: MANGA)" in query
    assert kwargs["json"]["variables"] == {
        "search": "Solo Leveling",
        "page": 1,
        "perPage": DEFAULT_PER_PAGE,
    }
    assert "coverImage" in query
    assert "description" not in query
    assert "externalLinks" not in query
    assert response.items[0].title.english == "Solo Leveling"
    assert response.page_info.current_page == 1
    assert response.page_info.has_next_page is True


def test_detail_preset_and_overrides_control_expensive_fields() -> None:
    detailed_media = {
        **page_payload()["data"]["Page"]["media"][0],
        "description": None,
        "chapters": None,
        "volumes": None,
        "genres": ["Action"],
        "externalLinks": [],
    }
    session = FakeSession([FakeResponse(page_payload([detailed_media]))])
    client = AniListClient(session=session)

    response = asyncio.run(
        client.search_manga(
            "Solo",
            include_details=True,
            include_cover=False,
        )
    )

    query = session.calls[0][1]["json"]["query"]
    assert "description" in query
    assert "externalLinks" in query
    assert "coverImage" not in query
    item = response.items[0]
    assert isinstance(item, AniListManga)
    assert item.description is None
    assert item.chapters is None
    assert item.volumes is None
    assert item.external_links == ()


def test_get_manga_uses_id_variable_and_parses_nullable_values() -> None:
    media = {
        "id": 105398,
        "idMal": None,
        "title": {"romaji": "Title", "english": None, "native": None},
        "synonyms": [],
        "format": "FUTURE_UNKNOWN_FORMAT",
        "status": "FUTURE_UNKNOWN_STATUS",
        "countryOfOrigin": "KR",
        "popularity": None,
        "averageScore": None,
        "coverImage": None,
        "siteUrl": "https://anilist.co/manga/105398",
        "description": None,
        "chapters": None,
        "volumes": None,
        "genres": [],
        "externalLinks": [],
    }
    session = FakeSession([FakeResponse({"data": {"Media": media}})])
    client = AniListClient(session=session)

    result = asyncio.run(client.get_manga(105398))

    query = session.calls[0][1]["json"]["query"]
    assert "Media(id: $id, type: MANGA)" in query
    assert session.calls[0][1]["json"]["variables"] == {"id": 105398}
    assert result.title.english is None
    assert result.format == "FUTURE_UNKNOWN_FORMAT"
    assert result.external_links == ()


@pytest.mark.parametrize("per_page", [1, ANILIST_MAX_PER_PAGE])
def test_python_client_accepts_api_page_boundaries(per_page: int) -> None:
    client = AniListClient(session=FakeSession([FakeResponse(page_payload([]))]))
    asyncio.run(client.search_manga("x", per_page=per_page))


@pytest.mark.parametrize("per_page", [0, ANILIST_MAX_PER_PAGE + 1])
def test_python_client_rejects_out_of_range_page_sizes(per_page: int) -> None:
    client = AniListClient(session=FakeSession([]))
    with pytest.raises(ValueError, match="between 1 and 50"):
        asyncio.run(client.search_manga("x", per_page=per_page))


def test_field_sets_accept_only_supported_fields() -> None:
    assert "id\n" in f"{AniListFieldSet.LIGHT.graphql()}\n"
    with pytest.raises(ValueError):
        AniListFieldSet(["unsafeField { injected }"])


def test_supported_source_extraction_validates_domain_deduplicates_and_sorts() -> None:
    webtoon = "https://www.webtoons.com/en/action/example/list?title_no=10"
    links = (
        AniListExternalLink(
            site="Naver Webtoon",
            url="https://evil.example/titleId=1",
            type="STREAMING",
        ),
        AniListExternalLink(
            site="MangaDex",
            url="https://mangadex.org/title/37f5cce0-8070-4ada-96e5-fa24b1bd4ff9",
            type="INFO",
            language="English",
        ),
        AniListExternalLink(
            site="WEBTOON",
            url=webtoon,
            type="STREAMING",
            language="English",
        ),
        AniListExternalLink(
            site="WEBTOON duplicate",
            url=webtoon,
            type="STREAMING",
            language="English",
        ),
        AniListExternalLink(
            site="Naver",
            url="https://comic.naver.com/webtoon/list?titleId=1",
            type="STREAMING",
            is_disabled=True,
        ),
    )

    sources = extract_supported_sources(links)

    assert [source.provider for source in sources] == ["webtoons", "mangadex"]
    assert len(sources) == 2
    assert all(source.is_disabled is False for source in sources)
    assert len(extract_supported_sources(links, include_disabled=True)) == 3


def test_graphql_errors_are_typed() -> None:
    session = FakeSession(
        [FakeResponse({"errors": [{"message": "Bad query", "status": 400}], "data": None})]
    )
    client = AniListClient(session=session)

    with pytest.raises(AniListGraphQLResponseError) as captured:
        asyncio.run(client.search_manga("x"))

    assert captured.value.graphql_errors[0].message == "Bad query"
    assert captured.value.graphql_errors[0].status == 400


def test_rate_limit_error_retains_retry_headers() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status_code=429,
                headers={"Retry-After": "30", "X-RateLimit-Reset": "1000"},
            )
        ]
    )
    client = AniListClient(session=session)

    with pytest.raises(AniListRateLimitError) as captured:
        asyncio.run(client.search_manga("x"))

    assert captured.value.retry_after == 30
    assert captured.value.reset_at == 1000


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (requests.Timeout("slow"), AniListTimeoutError),
        (requests.ConnectionError("offline"), AniListNetworkError),
        (FakeResponse(status_code=503), AniListHTTPError),
        (FakeResponse(json_error=ValueError("bad json")), AniListResponseError),
        (FakeResponse([]), AniListResponseError),
    ],
)
def test_transport_failures_have_distinct_errors(response, error_type) -> None:
    client = AniListClient(session=FakeSession([response]))
    with pytest.raises(error_type):
        asyncio.run(client.search_manga("x"))


def test_owned_session_is_reused_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([FakeResponse(page_payload([])), FakeResponse(page_payload([]))])
    monkeypatch.setattr("mandown.anilist.client.requests.Session", lambda: session)

    async def exercise() -> None:
        async with AniListClient() as client:
            await client.search_manga("one")
            await client.search_manga("two")

    asyncio.run(exercise())

    assert len(session.calls) == 2
    assert session.closed is True
