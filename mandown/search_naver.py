"""Naver Webtoon title search provider."""

import requests

from .errors import SourceResponseError
from .request_utils import USER_AGENT
from .search_common import parse_naver_id
from .sources.base_source import SourceSearchResult

SEARCH_URL = "https://comic.naver.com/api/search/all"
HEADERS = {
    "Referer": "https://m.comic.naver.com/",
    "User-Agent": USER_AGENT,
}


def search(query: str) -> list[SourceSearchResult]:
    """Search Naver Webtoon and return lightweight series results."""
    try:
        response = requests.get(
            SEARCH_URL,
            params={"keyword": query},
            headers=HEADERS,
            timeout=20,
        )
        if response is None:
            raise _response_error("request returned no response")
        response.raise_for_status()
    except requests.RequestException as error:
        raise _response_error(f"HTTP request failed: {error}") from error
    try:
        payload = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError) as error:
        raise _response_error(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise _response_error("response body is not an object")
    search_result = payload.get("searchWebtoonResult")
    if not isinstance(search_result, dict):
        raise _response_error("missing searchWebtoonResult object")
    rows = search_result.get("searchViewList")
    if not isinstance(rows, list):
        raise _response_error("missing searchViewList array")

    results: list[SourceSearchResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title_id = row.get("titleId")
        if not title_id:
            continue
        artists = row.get("communityArtists") or []
        if not isinstance(artists, list):
            artists = []
        authors = tuple(
            artist["name"]
            for artist in artists
            if isinstance(artist, dict) and isinstance(artist.get("name"), str)
        )
        result_url = (
            "https://m.comic.naver.com/webtoon/list?"
            f"titleId={title_id}&sortOrder=ASC"
        )
        results.append(
            SourceSearchResult(
                title=(
                    row["titleName"]
                    if isinstance(row.get("titleName"), str) and row["titleName"]
                    else str(title_id)
                ),
                url=result_url,
                authors=authors,
                cover_art=(
                    row["thumbnailUrl"]
                    if isinstance(row.get("thumbnailUrl"), str)
                    else ""
                ),
                identifiers={
                    "anilist_id": None,
                    "mal_id": None,
                    "naver_id": parse_naver_id(result_url),
                    "webtoons_id": None,
                    "mangadex_id": None,
                },
            )
        )
    return results


def _response_error(reason: str) -> SourceResponseError:
    return SourceResponseError(f"Naver response error: url={SEARCH_URL}, reason={reason}")
