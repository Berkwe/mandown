"""WEBTOON title search provider."""

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .errors import SourceResponseError
from .request_utils import USER_AGENT
from .search_common import parse_webtoons_id
from .sources.base_source import SourceSearchResult

SEARCH_URL = "https://www.webtoons.com/en/search"


def search(query: str) -> list[SourceSearchResult]:
    """Search WEBTOON and return lightweight series results."""
    try:
        response = requests.get(
            SEARCH_URL,
            params={"keyword": query},
            headers={
                "Referer": "https://webtoons.com/",
                "User-Agent": USER_AGENT,
            },
            timeout=20,
        )
        if response is None:
            raise _response_error("request returned no response")
        response.raise_for_status()
    except requests.RequestException as error:
        raise _response_error(f"HTTP request failed: {error}") from error
    if not isinstance(response.text, str):
        raise _response_error("response body is not text")
    soup = BeautifulSoup(response.text, "lxml")
    response_url = response.url if isinstance(response.url, str) else SEARCH_URL

    results: list[SourceSearchResult] = []
    seen: set[str] = set()
    for card in soup.select("a._card_item[href*='title_no=']"):
        url = urljoin(response_url, str(card.get("href", "")))
        if not url or url in seen:
            continue
        seen.add(url)

        title_element = card.select_one(".title")
        if not title_element:
            continue
        author_element = card.select_one(".author")
        image = card.select_one("img")
        authors = (
            tuple(part.strip() for part in author_element.get_text().split("/") if part.strip())
            if author_element
            else ()
        )
        results.append(
            SourceSearchResult(
                title=title_element.get_text(strip=True),
                url=url,
                authors=authors,
                cover_art=str(image.get("src", "")) if image else "",
                identifiers={
                    "anilist_id": None,
                    "mal_id": None,
                    "naver_id": None,
                    "webtoons_id": parse_webtoons_id(url),
                    "mangadex_id": None,
                },
            )
        )
    return results


def _response_error(reason: str) -> SourceResponseError:
    return SourceResponseError(f"WEBTOON response error: url={SEARCH_URL}, reason={reason}")
