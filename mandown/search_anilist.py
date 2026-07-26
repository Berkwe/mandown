"""AniList GraphQL search provider for Korean manga."""

import time
from typing import Any

import requests

from .errors import SourceResponseError
from .request_utils import USER_AGENT
from .search_common import parse_int_identifier, parse_naver_id, parse_webtoons_id
from .sources.base_source import SourceSearchResult

API_URL = "https://graphql.anilist.co"
REQUEST_TIMEOUT = 20
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5

SEARCH_QUERY = """
query SearchKoreanManga($search: String!) {
  Page(page: 1, perPage: 25) {
    media(search: $search, type: MANGA, countryOfOrigin: "KR") {
      id
      idMal
      title {
        native
        english
      }
      externalLinks {
        url
        site
      }
    }
  }
}
"""


def search(query: str) -> list[SourceSearchResult]:
    """
    Search AniList for Korean manga.

    Native and English titles, all external links, and the separately marked
    Naver Webtoon URL are returned in ``SourceSearchResult.extra``.
    """
    data = _post_graphql({"search": query})
    page = data.get("Page")
    if not isinstance(page, dict):
        raise _response_error("missing data.Page object")
    media_rows = page.get("media")
    if not isinstance(media_rows, list):
        raise _response_error("missing data.Page.media array")

    results: list[SourceSearchResult] = []
    for media in media_rows:
        if not isinstance(media, dict):
            continue
        anilist_id = media.get("id")
        if not isinstance(anilist_id, int):
            continue

        titles = media.get("title") or {}
        native_title = _optional_string(titles.get("native")) if isinstance(titles, dict) else None
        english_title = (
            _optional_string(titles.get("english")) if isinstance(titles, dict) else None
        )
        external_links = _external_links(media.get("externalLinks"))
        mal_id = parse_int_identifier(media.get("idMal"))
        naver_url = next(
            (
                link["url"]
                for link in external_links
                if parse_naver_id(link["url"]) is not None
            ),
            None,
        )
        webtoons_url = _preferred_webtoons_url(external_links)
        result_url = naver_url or f"https://anilist.co/manga/{anilist_id}"
        display_title = english_title or native_title or str(anilist_id)

        results.append(
            SourceSearchResult(
                title=display_title,
                url=result_url,
                extra={
                    "anilist_id": anilist_id,
                    "mal_id": mal_id,
                    "title": {
                        "native": native_title,
                        "english": english_title,
                    },
                    "externalLinks": external_links,
                    "naver_url": naver_url,
                    "webtoons_url": webtoons_url,
                },
                identifiers={
                    "anilist_id": anilist_id,
                    "mal_id": mal_id,
                    "naver_id": parse_naver_id(naver_url),
                    "webtoons_id": parse_webtoons_id(webtoons_url),
                    "mangadex_id": None,
                },
            )
        )
    return results


def _post_graphql(variables: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            response = requests.post(
                API_URL,
                json={"query": SEARCH_QUERY, "variables": variables},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as error:
            if attempt + 1 < MAX_REQUEST_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue
            raise _response_error(f"network error: {error}") from error
        except requests.RequestException as error:
            raise _response_error(f"request failed: {error}") from error

        if response is None:
            raise _response_error("request returned no response")
        if (response.status_code == 429 or 500 <= response.status_code <= 599) and (
            attempt + 1 < MAX_REQUEST_ATTEMPTS
        ):
            time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            raise _response_error(
                f"status={response.status_code}, HTTP request failed: {error}"
            ) from error
        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as error:
            raise _response_error(
                f"status={response.status_code}, invalid JSON: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise _response_error(f"status={response.status_code}, body is not an object")
        graphql_errors = payload.get("errors")
        if graphql_errors:
            raise _response_error(
                f"status={response.status_code}, GraphQL errors={graphql_errors!r}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise _response_error(f"status={response.status_code}, missing data object")
        return data

    raise AssertionError("AniList request retry loop ended unexpectedly")


def _external_links(value: Any) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if not isinstance(value, list):
        return links
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _optional_string(item.get("url"))
        site = _optional_string(item.get("site"))
        if url is not None and site is not None:
            links.append({"url": url, "site": site})
    return links


def _preferred_webtoons_url(external_links: list[dict[str, str]]) -> str | None:
    candidates = [
        link["url"]
        for link in external_links
        if link["site"].casefold() == "webtoon"
        and parse_webtoons_id(link["url"]) is not None
    ]
    return next(
        (url for url in candidates if "/en/" in url.casefold()),
        candidates[0] if candidates else None,
    )


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _response_error(reason: str) -> SourceResponseError:
    return SourceResponseError(f"AniList response error: url={API_URL}, reason={reason}")
