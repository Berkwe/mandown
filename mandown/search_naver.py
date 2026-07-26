"""Naver Webtoon title search provider."""

import requests

from .request_utils import USER_AGENT
from .sources.base_source import SourceSearchResult

SEARCH_URL = "https://comic.naver.com/api/search/all"
HEADERS = {
    "Referer": "https://m.comic.naver.com/",
    "User-Agent": USER_AGENT,
}


def search(query: str) -> list[SourceSearchResult]:
    """Search Naver Webtoon and return lightweight series results."""
    response = requests.get(
        SEARCH_URL,
        params={"keyword": query},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json().get("searchWebtoonResult", {}).get("searchViewList", [])

    results: list[SourceSearchResult] = []
    for row in rows:
        title_id = row.get("titleId")
        if not title_id:
            continue
        artists = row.get("communityArtists") or []
        authors = tuple(
            artist["name"]
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        )
        results.append(
            SourceSearchResult(
                title=row.get("titleName") or str(title_id),
                url=(
                    "https://m.comic.naver.com/webtoon/list?"
                    f"titleId={title_id}&sortOrder=ASC"
                ),
                authors=authors,
                cover_art=row.get("thumbnailUrl") or "",
            )
        )
    return results
