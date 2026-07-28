"""Reusable async AniList client backed by one requests session."""

import asyncio
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from ..errors import (
    AniListGraphQLResponseError,
    AniListHTTPError,
    AniListNetworkError,
    AniListRateLimitError,
    AniListResponseError,
    AniListTimeoutError,
)
from ..request_utils import USER_AGENT
from ..sources import get_class_for
from .fields import AniListField, AniListFieldSet
from .models import (
    AniListCoverImage,
    AniListExternalLink,
    AniListGraphQLError,
    AniListManga,
    AniListMangaSummary,
    AniListPageInfo,
    AniListSearchResponse,
    AniListSupportedSource,
    AniListTitle,
)

ANILIST_ENDPOINT = "https://graphql.anilist.co"
ANILIST_MAX_PER_PAGE = 50
DEFAULT_PER_PAGE = 10
MANDOWN_PER_PAGE_LIMIT = 25

_SEARCH_QUERY = """\
query SearchManga($search: String!, $page: Int!, $perPage: Int!) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { currentPage lastPage hasNextPage perPage total }
    media(search: $search, type: MANGA) {
%s
    }
  }
}
"""

_DETAIL_QUERY = """\
query GetManga($id: Int!) {
  Media(id: $id, type: MANGA) {
%s
  }
}
"""

_PROVIDER_DOMAINS = {
    "mangadex": ("mangadex.org",),
    "webtoons": ("webtoons.com",),
    "naver": ("comic.naver.com",),
    "naver_series": ("series.naver.com",),
}
_PROVIDER_CLASSES = {
    "mangadex": "MangaDexSource",
    "webtoons": "WebtoonsSource",
    "naver": "NaverWebtoonSource",
    "naver_series": "NaverSeriesSource",
}
_LINK_TYPE_ORDER = {"STREAMING": 0, "INFO": 1}


class AniListClient:
    """Search AniList's general manga index and load selected manga metadata.

    AniList exposes one general ``search`` argument. It does not provide a
    separate English-title substring search, so short English fragments may
    not return an otherwise expected title.
    """

    def __init__(
        self,
        *,
        endpoint: str = ANILIST_ENDPOINT,
        timeout: float | tuple[float, float] = (5, 20),
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._session_lock = threading.Lock()
        self._closed = False

    async def __aenter__(self) -> "AniListClient":
        if self._closed:
            raise RuntimeError("AniListClient is closed.")
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_session:
            await asyncio.to_thread(self._session.close)

    async def search_manga(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
        fields: AniListFieldSet | None = None,
        include_details: bool = False,
        include_external_links: bool | None = None,
        include_description: bool | None = None,
        include_cover: bool | None = None,
    ) -> AniListSearchResponse:
        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_query:
            raise ValueError("AniList search query cannot be empty.")
        self._validate_page(page, per_page)
        selected = fields or AniListFieldSet.CARD
        if include_details:
            selected = AniListFieldSet(selected.values.union(AniListFieldSet.DETAIL.values))
        selected = self._apply_field_overrides(
            selected,
            include_external_links=include_external_links,
            include_description=include_description,
            include_cover=include_cover,
        )
        payload = await self._post(
            _SEARCH_QUERY % self._indent_fields(selected.graphql(), 6),
            {"search": normalized_query, "page": page, "perPage": per_page},
        )
        page_payload = self._mapping(payload.get("Page"), "data.Page")
        media = page_payload.get("media")
        if not isinstance(media, list):
            raise AniListResponseError("AniList response is missing data.Page.media array.")
        page_info = self._parse_page_info(
            self._mapping(page_payload.get("pageInfo"), "data.Page.pageInfo")
        )
        detailed = bool(
            selected.values
            & {
                AniListField.DESCRIPTION,
                AniListField.CHAPTERS,
                AniListField.VOLUMES,
                AniListField.GENRES,
                AniListField.EXTERNAL_LINKS,
            }
        )
        items = tuple(
            self._parse_manga(self._mapping(row, "data.Page.media item"), detailed=detailed)
            for row in media
        )
        return AniListSearchResponse(items=items, page_info=page_info)

    async def get_manga(
        self,
        anilist_id: int,
        *,
        fields: AniListFieldSet | None = None,
        include_external_links: bool | None = None,
        include_description: bool | None = None,
        include_cover: bool | None = None,
    ) -> AniListManga:
        if isinstance(anilist_id, bool) or not isinstance(anilist_id, int) or anilist_id <= 0:
            raise ValueError("AniList manga ID must be a positive integer.")
        selected = self._apply_field_overrides(
            fields or AniListFieldSet.DETAIL,
            include_external_links=include_external_links,
            include_description=include_description,
            include_cover=include_cover,
        )
        payload = await self._post(
            _DETAIL_QUERY % self._indent_fields(selected.graphql(), 4),
            {"id": anilist_id},
        )
        media = self._mapping(payload.get("Media"), "data.Media")
        parsed = self._parse_manga(media, detailed=True)
        if not isinstance(parsed, AniListManga):
            raise AssertionError("Detailed AniList parser returned a summary.")
        return parsed

    def extract_supported_sources(
        self,
        external_links: Sequence[AniListExternalLink],
        *,
        include_disabled: bool = False,
    ) -> tuple[AniListSupportedSource, ...]:
        return extract_supported_sources(
            external_links,
            include_disabled=include_disabled,
        )

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("AniListClient is closed.")
        try:
            response = await asyncio.to_thread(self._post_sync, query, variables)
        except requests.Timeout as error:
            raise AniListTimeoutError(f"AniList request timed out: {error}") from error
        except requests.ConnectionError as error:
            raise AniListNetworkError(f"AniList network error: {error}") from error
        except requests.RequestException as error:
            raise AniListNetworkError(f"AniList request failed: {error}") from error

        if response.status_code == 429:
            raise AniListRateLimitError(
                429,
                "AniList rate limit exceeded.",
                retry_after=_optional_int(response.headers.get("Retry-After")),
                reset_at=_optional_int(response.headers.get("X-RateLimit-Reset")),
            )
        if response.status_code >= 400:
            raise AniListHTTPError(
                response.status_code,
                f"AniList returned HTTP {response.status_code}.",
            )
        try:
            body = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as error:
            raise AniListResponseError(f"AniList returned invalid JSON: {error}") from error
        if not isinstance(body, dict):
            raise AniListResponseError("AniList response body is not an object.")
        raw_errors = body.get("errors")
        if raw_errors:
            graphql_errors = self._parse_graphql_errors(raw_errors)
            message = "; ".join(error.message for error in graphql_errors)
            raise AniListGraphQLResponseError(
                f"AniList GraphQL error: {message}",
                graphql_errors,
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise AniListResponseError("AniList response is missing a data object.")
        return data

    def _post_sync(self, query: str, variables: dict[str, Any]) -> requests.Response:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"Mandown AniList Client ({USER_AGENT})",
        }
        with self._session_lock:
            return self._session.post(
                self.endpoint,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=self.timeout,
            )

    @staticmethod
    def _validate_page(page: int, per_page: int) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("AniList page must be an integer greater than or equal to 1.")
        if (
            isinstance(per_page, bool)
            or not isinstance(per_page, int)
            or not 1 <= per_page <= ANILIST_MAX_PER_PAGE
        ):
            raise ValueError(
                f"AniList per_page must be between 1 and {ANILIST_MAX_PER_PAGE}."
            )

    @staticmethod
    def _apply_field_overrides(
        selected: AniListFieldSet,
        *,
        include_external_links: bool | None,
        include_description: bool | None,
        include_cover: bool | None,
    ) -> AniListFieldSet:
        overrides = (
            (include_external_links, AniListField.EXTERNAL_LINKS),
            (include_description, AniListField.DESCRIPTION),
            (include_cover, AniListField.COVER_IMAGE),
        )
        for enabled, field in overrides:
            if enabled is True:
                selected = selected.with_fields(field)
            elif enabled is False:
                selected = selected.without_fields(field)
        return selected

    @staticmethod
    def _indent_fields(fields: str, spaces: int) -> str:
        prefix = " " * spaces
        return "\n".join(prefix + line for line in fields.splitlines())

    @staticmethod
    def _mapping(value: Any, location: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise AniListResponseError(f"AniList response is missing {location} object.")
        return value

    @staticmethod
    def _parse_page_info(value: Mapping[str, Any]) -> AniListPageInfo:
        return AniListPageInfo(
            current_page=_optional_int(value.get("currentPage")),
            last_page=_optional_int(value.get("lastPage")),
            has_next_page=value.get("hasNextPage") is True,
            per_page=_optional_int(value.get("perPage")),
            total=_optional_int(value.get("total")),
        )

    def _parse_manga(
        self,
        value: Mapping[str, Any],
        *,
        detailed: bool,
    ) -> AniListMangaSummary | AniListManga:
        anilist_id = _optional_int(value.get("id"))
        if anilist_id is None or anilist_id <= 0:
            raise AniListResponseError("AniList manga is missing a valid id.")
        title_value = value.get("title")
        title_mapping = title_value if isinstance(title_value, Mapping) else {}
        cover_value = value.get("coverImage")
        cover_mapping = cover_value if isinstance(cover_value, Mapping) else None
        common = {
            "id": anilist_id,
            "id_mal": _optional_int(value.get("idMal")),
            "title": AniListTitle(
                romaji=_optional_string(title_mapping.get("romaji")),
                english=_optional_string(title_mapping.get("english")),
                native=_optional_string(title_mapping.get("native")),
            ),
            "synonyms": _string_tuple(value.get("synonyms")),
            "format": _optional_string(value.get("format")),
            "status": _optional_string(value.get("status")),
            "country_of_origin": _optional_string(value.get("countryOfOrigin")),
            "popularity": _optional_int(value.get("popularity")),
            "average_score": _optional_int(value.get("averageScore")),
            "cover_image": (
                AniListCoverImage(
                    extra_large=_optional_string(cover_mapping.get("extraLarge")),
                    large=_optional_string(cover_mapping.get("large")),
                    medium=_optional_string(cover_mapping.get("medium")),
                    color=_optional_string(cover_mapping.get("color")),
                )
                if cover_mapping is not None
                else None
            ),
            "site_url": _optional_string(value.get("siteUrl")),
        }
        if not detailed:
            return AniListMangaSummary(**common)
        return AniListManga(
            **common,
            description=_optional_string(value.get("description")),
            chapters=_optional_int(value.get("chapters")),
            volumes=_optional_int(value.get("volumes")),
            genres=_string_tuple(value.get("genres")),
            external_links=self._parse_external_links(value.get("externalLinks")),
        )

    @staticmethod
    def _parse_external_links(value: Any) -> tuple[AniListExternalLink, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise AniListResponseError("AniList externalLinks is not an array.")
        links: list[AniListExternalLink] = []
        for raw_link in value:
            if not isinstance(raw_link, Mapping):
                continue
            site = _optional_string(raw_link.get("site"))
            url = _optional_string(raw_link.get("url"))
            if site is None or url is None:
                continue
            disabled = raw_link.get("isDisabled")
            links.append(
                AniListExternalLink(
                    site=site,
                    url=url,
                    type=_optional_string(raw_link.get("type")),
                    language=_optional_string(raw_link.get("language")),
                    is_disabled=disabled if isinstance(disabled, bool) else None,
                    notes=_optional_string(raw_link.get("notes")),
                )
            )
        return tuple(links)

    @staticmethod
    def _parse_graphql_errors(value: Any) -> tuple[AniListGraphQLError, ...]:
        if not isinstance(value, list):
            return (AniListGraphQLError(message=str(value)),)
        errors = []
        for raw_error in value:
            if not isinstance(raw_error, Mapping):
                errors.append(AniListGraphQLError(message=str(raw_error)))
                continue
            raw_locations = raw_error.get("locations")
            raw_path = raw_error.get("path")
            errors.append(
                AniListGraphQLError(
                    message=_optional_string(raw_error.get("message")) or "Unknown error",
                    status=_optional_int(raw_error.get("status")),
                    locations=(
                        tuple(item for item in raw_locations if isinstance(item, dict))
                        if isinstance(raw_locations, list)
                        else ()
                    ),
                    path=(
                        tuple(item for item in raw_path if isinstance(item, (str, int)))
                        if isinstance(raw_path, list)
                        else ()
                    ),
                )
            )
        return tuple(errors)


def extract_supported_sources(
    external_links: Sequence[AniListExternalLink],
    *,
    include_disabled: bool = False,
) -> tuple[AniListSupportedSource, ...]:
    """Return resolver-ready MangaDex, WEBTOON, and Naver links."""

    candidates: list[tuple[int, int, AniListSupportedSource]] = []
    seen: set[tuple[str, str | None, str]] = set()
    for index, link in enumerate(external_links):
        if link.is_disabled is True and not include_disabled:
            continue
        provider = _provider_for_url(link.url)
        if provider is None:
            continue
        try:
            resolver = get_class_for(link.url)
        except ValueError:
            continue
        if resolver.__name__ != _PROVIDER_CLASSES[provider]:
            continue
        normalized_url = _normalized_url(link.url)
        dedupe_key = (provider, link.language, normalized_url)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        source = AniListSupportedSource(
            provider=provider,
            url=link.url,
            language=link.language,
            site=link.site,
            type=link.type,
            is_disabled=link.is_disabled is True,
        )
        candidates.append((_LINK_TYPE_ORDER.get((link.type or "").upper(), 2), index, source))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in candidates)


def _provider_for_url(url: str) -> str | None:
    try:
        hostname = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return None
    for provider, domains in _PROVIDER_DOMAINS.items():
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
            return provider
    return None


def _normalized_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold()
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"{hostname}{port}"
        return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path, parsed.query, ""))
    except ValueError:
        return url.strip()


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        normalized
        for item in value
        if (normalized := _optional_string(item)) is not None
    )
